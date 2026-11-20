from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .core import Job, canonical_country


_USER_AGENT = "JobRadar/1.0 (personal job-search tool)"


class CareerSiteError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CareerCompany:
    name: str
    careers_url: str
    priority: str
    locations: tuple[str, ...]
    sector: str
    connector: Mapping[str, object]

    @property
    def automatic(self) -> bool:
        return str(self.connector.get("type") or "link") != "link"

    def search_url(self, query: str) -> str:
        template = str(self.connector.get("search_url_template") or "")
        if not template or not query:
            return ""
        return template.replace("{query}", quote(query))


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_to_text(value: object) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(str(value or ""))
        parser.close()
    except (AssertionError, ValueError):
        return re.sub(r"<[^>]*>", " ", str(value or ""))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _as_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item)
    if value:
        return (str(value),)
    return ()


def _request(
    url: str,
    *,
    method: str = "GET",
    accept: str = "application/json",
    body: bytes | None = None,
    timeout: float = 25,
) -> bytes:
    headers = {"Accept": accept, "User-Agent": _USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise CareerSiteError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise CareerSiteError(f"connexion impossible: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CareerSiteError(str(exc)) from exc


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    timeout: float = 25,
) -> object:
    try:
        return json.loads(_request(url, method=method, body=body, timeout=timeout))
    except json.JSONDecodeError as exc:
        raise CareerSiteError(f"reponse JSON invalide: {exc}") from exc


def load_career_companies(path: str | Path | None = None) -> tuple[CareerCompany, ...]:
    registry_path = Path(path) if path else Path(__file__).with_name("companies.json")
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CareerSiteError(f"registre entreprises illisible: {exc}") from exc
    raw_companies = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(raw_companies, list):
        raise CareerSiteError("registre entreprises invalide")

    companies: list[CareerCompany] = []
    for item in raw_companies:
        if not isinstance(item, dict) or not item.get("name") or not item.get("careers_url"):
            continue
        connector = item.get("connector")
        companies.append(
            CareerCompany(
                name=str(item["name"]),
                careers_url=str(item["careers_url"]),
                priority=str(item.get("priority") or "standard"),
                locations=_as_tuple(item.get("locations")),
                sector=str(item.get("sector") or "life sciences"),
                connector=connector if isinstance(connector, dict) else {"type": "link"},
            )
        )
    return tuple(companies)


def trusted_company_names(companies: Iterable[CareerCompany]) -> tuple[str, ...]:
    return tuple(company.name for company in companies)


def priority_company_names(companies: Iterable[CareerCompany]) -> tuple[str, ...]:
    return tuple(company.name for company in companies if company.priority == "romandie")


def _source_name(company: CareerCompany) -> str:
    return f"Carriere · {company.name}"


def _relative_posted_at(value: object, *, now: datetime | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    anchor = (now or datetime.now(UTC)).astimezone(UTC)
    normalized = text.casefold()
    if "today" in normalized or "aujourd" in normalized:
        return anchor.isoformat()
    if "yesterday" in normalized or "hier" in normalized:
        return (anchor - timedelta(days=1)).isoformat()
    match = re.search(r"(\d+)\s+(?:day|jour)", normalized)
    if match:
        return (anchor - timedelta(days=int(match.group(1)))).isoformat()
    return text


def parse_workday(
    payload: object,
    company: CareerCompany,
    *,
    now: datetime | None = None,
) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
        raise CareerSiteError(f"reponse Workday invalide pour {company.name}")
    board_url = str(company.connector.get("board_url") or company.careers_url).rstrip("/")
    jobs: list[Job] = []
    for item in payload["jobPostings"]:
        if not isinstance(item, dict):
            continue
        external_path = str(item.get("externalPath") or "")
        location = str(item.get("locationsText") or "")
        bullet_fields = _as_tuple(item.get("bulletFields"))
        jobs.append(
            Job(
                source=_source_name(company),
                source_id=external_path or str(item.get("title") or ""),
                title=str(item.get("title") or ""),
                company=company.name,
                location=location,
                country=canonical_country("", location),
                url=f"{board_url}{external_path}" if external_path else company.careers_url,
                description=" ".join(bullet_fields),
                published_at=_relative_posted_at(item.get("postedOn"), now=now),
                tags=(company.sector, *bullet_fields),
                direct_employer=True,
            )
        )
    return jobs


def workday_country_filter(
    payload: object,
    countries: Iterable[str] = ("France", "Switzerland"),
) -> tuple[str, tuple[str, ...]] | None:
    country_list = tuple(countries)
    if not country_list:
        return None
    targets = {
        (canonical_country(country) or country).casefold()
        for country in country_list
    }

    def matches_country(label: object) -> bool:
        text = str(label or "")
        canonical = canonical_country("", text)
        return canonical.casefold() in targets if canonical else False

    def visit(
        node: object,
        *,
        allow_locations: bool,
        allow_country: bool,
    ) -> tuple[str, tuple[str, ...]] | None:
        if isinstance(node, list):
            for item in node:
                found = visit(
                    item,
                    allow_locations=allow_locations,
                    allow_country=allow_country,
                )
                if found is not None:
                    return found
            return None
        if not isinstance(node, dict):
            return None
        parameter = str(node.get("facetParameter") or "")
        descriptor = str(node.get("descriptor") or "")
        values = node.get("values")
        is_country = allow_country and descriptor.casefold() == "country"
        is_locations = allow_locations and (
            descriptor.casefold() == "locations" or parameter.casefold() == "locations"
        )
        if parameter and (is_country or is_locations) and isinstance(values, list):
            identifiers = tuple(
                str(value.get("id"))
                for value in values
                if isinstance(value, dict)
                and matches_country(value.get("descriptor"))
                and value.get("id")
            )
            return parameter, identifiers
        for key in ("facets", "values"):
            found = visit(
                node.get(key),
                allow_locations=allow_locations,
                allow_country=allow_country,
            )
            if found is not None:
                return found
        return None

    country_filter = visit(payload, allow_locations=False, allow_country=True)
    if country_filter is not None and country_filter[1]:
        return country_filter
    location_filter = visit(payload, allow_locations=True, allow_country=False)
    return location_filter if location_filter is not None else country_filter


def enrich_workday_job(
    job: Job,
    payload: object,
    *,
    use_hiring_organization: bool = False,
) -> Job:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobPostingInfo"), dict):
        raise CareerSiteError("reponse de detail Workday invalide")
    info = payload["jobPostingInfo"]
    location = str(info.get("location") or job.location)
    country_value = info.get("country") or job.country
    raw_country = str(
        country_value.get("descriptor")
        if isinstance(country_value, dict)
        else country_value
    )
    country = canonical_country(raw_country, location) or raw_country
    organization = payload.get("hiringOrganization")
    company_name = job.company
    if use_hiring_organization and isinstance(organization, dict):
        company_name = str(organization.get("name") or job.company)
    return replace(
        job,
        title=str(info.get("title") or job.title),
        company=company_name,
        location=location,
        country=country,
        url=str(info.get("externalUrl") or job.url),
        description=_html_to_text(info.get("jobDescription") or job.description),
        published_at=_relative_posted_at(
            info.get("startDate") or info.get("postedOn") or job.published_at
        ),
        employment_type=str(info.get("timeType") or job.employment_type),
        tags=job.tags + _as_tuple(info.get("jobReqId")),
    )


def parse_workable(payload: object, company: CareerCompany) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise CareerSiteError(f"reponse Workable invalide pour {company.name}")
    account = str(company.connector.get("account") or "")
    jobs: list[Job] = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        raw_location = item.get("location")
        location_data: dict[object, object] = (
            raw_location if isinstance(raw_location, dict) else {}
        )
        location = ", ".join(
            str(value)
            for value in (
                location_data.get("city"),
                location_data.get("region"),
                location_data.get("country"),
            )
            if value
        )
        shortcode = str(item.get("shortcode") or item.get("id") or "")
        departments = _as_tuple(item.get("department"))
        jobs.append(
            Job(
                source=_source_name(company),
                source_id=shortcode,
                title=str(item.get("title") or ""),
                company=company.name,
                location=location,
                country=canonical_country(str(location_data.get("countryCode") or ""), location),
                url=f"https://apply.workable.com/{account}/j/{shortcode}/",
                published_at=str(item.get("published") or ""),
                employment_type=str(item.get("type") or ""),
                remote=bool(item.get("remote")) or item.get("workplace") == "remote",
                tags=(company.sector, *departments),
                direct_employer=True,
            )
        )
    return jobs


def parse_rss(payload: str, company: CareerCompany) -> list[Job]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise CareerSiteError(f"flux RSS invalide pour {company.name}: {exc}") from exc

    jobs: list[Job] = []
    for item in root.findall(".//item"):
        fields: dict[str, str] = {}
        for child in item:
            key = child.tag.rsplit("}", 1)[-1].casefold()
            fields[key] = " ".join("".join(child.itertext()).split())
        title = fields.get("title", "")
        link = fields.get("link", "") or fields.get("guid", "")
        if fields.get("guid") == "0" or "no jobs currently available" in title.casefold():
            continue
        location = fields.get("location", "") or fields.get("city", "")
        published = fields.get("pubdate", "") or fields.get("date", "")
        try:
            published = parsedate_to_datetime(published).isoformat() if published else ""
        except (TypeError, ValueError):
            pass
        jobs.append(
            Job(
                source=_source_name(company),
                source_id=fields.get("guid", "") or link or title,
                title=title,
                company=company.name,
                location=location,
                country=canonical_country("", location),
                url=link or company.careers_url,
                description=_html_to_text(fields.get("description", "")),
                published_at=published,
                tags=(company.sector,),
                direct_employer=True,
            )
        )
    return jobs


def parse_smartrecruiters(payload: object, company: CareerCompany) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise CareerSiteError(f"reponse SmartRecruiters invalide pour {company.name}")
    account = quote(str(company.connector.get("account") or ""), safe="")
    jobs: list[Job] = []
    for item in payload["content"]:
        if not isinstance(item, dict) or item.get("active") is False:
            continue
        identifier = str(item.get("id") or "")
        if not identifier or not item.get("name"):
            continue
        location_data = item.get("location") or {}
        location = str(location_data.get("fullLocation") or ", ".join(
            str(location_data[key]) for key in ("city", "region", "country")
            if location_data.get(key)
        ))
        sections = (item.get("jobAd") or {}).get("sections") or {}
        description = " ".join(
            _html_to_text(section.get("text"))
            for section in sections.values() if isinstance(section, dict)
        )
        jobs.append(Job(
            source=_source_name(company), source_id=identifier,
            title=str(item["name"]), company=company.name,
            location=location,
            country=canonical_country(str(location_data.get("country") or ""), location),
            url=str(item.get("postingUrl") or
                    f"https://jobs.smartrecruiters.com/{account}/{quote(identifier, safe='')}"),
            description=description, published_at=str(item.get("releasedDate") or ""),
            employment_type=str((item.get("typeOfEmployment") or {}).get("label") or ""),
            remote=bool(location_data.get("remote")),
            tags=(company.sector, str((item.get("department") or {}).get("label") or "")),
            direct_employer=True,
        ))
    return jobs


def parse_ashby(payload: object, company: CareerCompany) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise CareerSiteError(f"reponse Ashby invalide pour {company.name}")
    jobs: list[Job] = []
    for item in payload["jobs"]:
        if not isinstance(item, dict) or item.get("isListed") is False:
            continue
        url = str(item.get("jobUrl") or "")
        if not url or not item.get("title"):
            continue
        address = (item.get("address") or {}).get("postalAddress") or {}
        location = str(item.get("location") or "")
        # Use only the posting's location; company headquarters are not job locations.
        jobs.append(Job(
            source=_source_name(company), source_id=url,
            title=str(item["title"]), company=company.name, location=location,
            country=canonical_country(str(address.get("addressCountry") or ""), location),
            url=url, description=str(item.get("descriptionPlain") or
                                      _html_to_text(item.get("descriptionHtml"))),
            published_at=str(item.get("publishedAt") or ""),
            employment_type=str(item.get("employmentType") or ""),
            remote=bool(item.get("isRemote")) or item.get("workplaceType") == "Remote",
            tags=(company.sector, *_as_tuple(item.get("department"))),
            direct_employer=True,
        ))
    return jobs


def parse_greenhouse(payload: object, company: CareerCompany) -> list[Job]:
    """Public Job Board API. updated_at is NOT the publication date."""
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise CareerSiteError(f"reponse Greenhouse invalide pour {company.name}")
    jobs = []
    for item in payload["jobs"]:
        if not isinstance(item, dict) or not item.get("id") or not item.get("title"):
            continue
        # Prospect/talent-pool posts are not actual vacancies.
        if "internal_job_id" in item and item["internal_job_id"] is None:
            continue
        url = str(item.get("absolute_url") or "")
        if not url.startswith(("https://", "http://")):
            continue
        location_data = item.get("location")
        location = str(location_data.get("name") or "") if isinstance(location_data, dict) else ""
        jobs.append(Job(
            source=_source_name(company), source_id=str(item["id"]),
            title=str(item["title"]), company=company.name, location=location,
            country=canonical_country("", location), url=url,
            description=_html_to_text(unescape(str(item.get("content") or ""))),
            published_at=str(item.get("first_published") or ""),
            remote=bool(re.search(r"\bremote\b", location, re.IGNORECASE)),
            tags=(company.sector,), direct_employer=True,
        ))
    return jobs


def _unique_jobs(jobs: Iterable[Job]) -> list[Job]:
    unique: dict[tuple[str, str], Job] = {}
    for job in jobs:
        unique[(job.company.casefold(), job.source_id or job.url)] = job
    return list(unique.values())


def _fetch_workday(
    company: CareerCompany,
    *,
    queries: Iterable[str],
    timeout: float,
    max_pages: int,
    countries: Iterable[str],
) -> list[Job]:
    api_url = str(company.connector.get("api_url") or "")
    if not api_url:
        raise CareerSiteError(f"URL Workday absente pour {company.name}")
    jobs: list[Job] = []
    resolved_jobs: dict[str, Job] = {}
    for query in queries:
        initial_body = json.dumps(
            {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": query},
            separators=(",", ":"),
        ).encode("utf-8")
        initial_payload = _request_json(
            api_url,
            method="POST",
            body=initial_body,
            timeout=timeout,
        )
        country_filter = workday_country_filter(initial_payload, countries)
        if country_filter is not None and not country_filter[1]:
            continue
        applied_facets = (
            {country_filter[0]: list(country_filter[1])} if country_filter is not None else {}
        )
        offset = 0
        for _ in range(max_pages):
            body = json.dumps(
                {
                    "appliedFacets": applied_facets,
                    "limit": 20,
                    "offset": offset,
                    "searchText": query,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            payload = _request_json(api_url, method="POST", body=body, timeout=timeout)
            page_jobs = parse_workday(payload, company)
            detail_base = api_url.removesuffix("/jobs")
            for job in page_jobs:
                if job.source_id in resolved_jobs:
                    continue
                if not job.source_id.startswith("/"):
                    jobs.append(job)
                    resolved_jobs[job.source_id] = job
                    continue
                try:
                    detail_payload = _request_json(
                        f"{detail_base}{job.source_id}",
                        timeout=timeout,
                    )
                    enriched_job = enrich_workday_job(
                        job,
                        detail_payload,
                        use_hiring_organization=bool(
                            company.connector.get("use_hiring_organization")
                        ),
                    )
                    jobs.append(enriched_job)
                    resolved_jobs[job.source_id] = enriched_job
                except CareerSiteError:
                    jobs.append(job)
                    resolved_jobs[job.source_id] = job
            total = int(payload.get("total") or 0) if isinstance(payload, dict) else 0
            offset += len(page_jobs)
            if not page_jobs or offset >= total:
                break
    unique_jobs = _unique_jobs(jobs)
    target_countries = {canonical_country(country) or country for country in countries}
    if not target_countries:
        return unique_jobs
    return [
        job
        for job in unique_jobs
        if canonical_country(job.country, job.location) in target_countries
    ]


def _fetch_workable(
    company: CareerCompany,
    *,
    queries: Iterable[str],
    timeout: float,
) -> list[Job]:
    account = str(company.connector.get("account") or "")
    if not account:
        raise CareerSiteError(f"compte Workable absent pour {company.name}")
    url = f"https://apply.workable.com/api/v3/accounts/{account}/jobs"
    jobs: list[Job] = []
    for query in queries:
        body = json.dumps(
            {"query": query, "location": [], "department": [], "worktype": [], "remote": []},
            separators=(",", ":"),
        ).encode("utf-8")
        payload = _request_json(url, method="POST", body=body, timeout=timeout)
        jobs.extend(parse_workable(payload, company))
    return _unique_jobs(jobs)


def _fetch_rss(
    company: CareerCompany,
    *,
    queries: Iterable[str],
    timeout: float,
) -> list[Job]:
    url_template = str(company.connector.get("url_template") or "")
    if not url_template:
        raise CareerSiteError(f"URL RSS absente pour {company.name}")
    jobs: list[Job] = []
    for query in queries:
        url = url_template.replace("{query}", quote(query, safe=""))
        payload = _request(url, accept="*/*", timeout=timeout).decode(
            "utf-8", errors="replace"
        )
        jobs.extend(parse_rss(payload, company))
    return _unique_jobs(jobs)


def fetch_career_company(
    company: CareerCompany,
    *,
    queries: Iterable[str],
    timeout: float = 25,
    max_pages: int = 20,
    countries: Iterable[str] = ("France", "Switzerland"),
) -> list[Job]:
    connector_type = str(company.connector.get("type") or "link")
    if connector_type == "greenhouse":
        board = str(company.connector.get("board") or "")
        if not board:
            raise CareerSiteError(f"tableau Greenhouse absent pour {company.name}")
        jobs = parse_greenhouse(_request_json(
            f"https://boards-api.greenhouse.io/v1/boards/{quote(board, safe='')}/jobs?content=true",
            timeout=timeout,
        ), company)
        targets = {canonical_country(country) or country for country in countries}
        return [job for job in jobs if not targets or job.country in targets]
    if connector_type == "workday":
        return _fetch_workday(
            company,
            queries=queries,
            timeout=timeout,
            max_pages=max_pages,
            countries=countries,
        )
    if connector_type == "workable":
        return _fetch_workable(company, queries=queries, timeout=timeout)
    if connector_type == "rss":
        return _fetch_rss(company, queries=queries, timeout=timeout)
    if connector_type in {"smartrecruiters", "ashby"}:
        targets = {canonical_country(country) or country for country in countries}
        if connector_type == "smartrecruiters":
            jobs = _fetch_smartrecruiters(
                company, queries=queries, timeout=timeout,
                max_pages=max_pages, countries=targets,
            )
        else:
            board = str(company.connector.get("board") or "")
            if not board:
                raise CareerSiteError(f"tableau Ashby absent pour {company.name}")
            # The public feed returns every listed posting, including full descriptions.
            # Keyword matching is performed by the common ranking/filtering pipeline.
            jobs = parse_ashby(_request_json(
                f"https://api.ashbyhq.com/posting-api/job-board/{quote(board, safe='')}",
                timeout=timeout,
            ), company)
        return [job for job in jobs if not targets or
                canonical_country(job.country, job.location) in targets]
    raise CareerSiteError("portail non automatise; utiliser le lien carriere")


def _fetch_smartrecruiters(
    company: CareerCompany,
    *,
    queries: Iterable[str],
    timeout: float,
    max_pages: int,
    countries: Iterable[str],
) -> list[Job]:
    account = str(company.connector.get("account") or "")
    if not account:
        raise CareerSiteError(f"compte SmartRecruiters absent pour {company.name}")
    base = f"https://api.smartrecruiters.com/v1/companies/{quote(account, safe='')}/postings"
    country_codes = {"France": "fr", "Switzerland": "ch"}
    targets = tuple(countries)
    filters = tuple(country_codes[c] for c in sorted(targets)) if (
        targets and all(c in country_codes for c in targets)
    ) else ("",)
    jobs: dict[str, Job] = {}
    for query in queries:
        for country in filters:
            offset = 0
            for _ in range(max_pages):
                params = {"q": query, "limit": "100", "offset": str(offset)}
                if country:
                    params["country"] = country
                payload = _request_json(f"{base}?{urlencode(params)}", timeout=timeout)
                page = parse_smartrecruiters(payload, company)
                for job in page:
                    if job.source_id in jobs:
                        continue
                    try:
                        detail = _request_json(
                            f"{base}/{quote(job.source_id, safe='')}", timeout=timeout,
                        )
                        if not isinstance(detail, dict) or not detail.get("id"):
                            raise CareerSiteError("reponse de detail SmartRecruiters invalide")
                        enriched = parse_smartrecruiters({"content": [detail]}, company)
                        if not enriched:  # A posting can be withdrawn between requests.
                            continue
                        job = enriched[0]
                    except CareerSiteError:
                        pass
                    jobs[job.source_id] = job
                # Offset counts raw entries, including inactive or incomplete postings.
                offset += len(payload["content"])
                if not payload["content"] or offset >= int(payload.get("totalFound") or 0):
                    break
    return list(jobs.values())
