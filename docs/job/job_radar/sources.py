from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import os
import re
from typing import Callable, Iterable, Mapping, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .career_sites import (
    CareerCompany,
    CareerSiteError,
    fetch_career_company,
    load_career_companies,
)
from .core import Job, canonical_country


DEFAULT_QUERIES = (
    "laboratory automation",
    "robotics biotech",
    "automation pharma",
    "liquid handling",
)

THESIS_QUERIES = (
    "automatisation",
    "robotique",
    "laboratory automation",
    "biotechnologie",
    "pharmaceutique",
)

PUBLIC_SOURCES = ("Arbeitnow", "Remotive", "Jobicy")
STAFFING_SOURCES = ("Randstad", "Michael Page")
OPTIONAL_SOURCES = STAFFING_SOURCES + ("France Travail", "Google Jobs (SerpAPI)")
CAREER_SITES_SOURCE = "Sites carriere officiels"

_USER_AGENT = "JobRadar/1.0 (personal job-search tool)"


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceReport:
    source: str
    status: str
    fetched_jobs: int
    message: str = ""
    url: str = ""


@dataclass(frozen=True, slots=True)
class CollectionResult:
    jobs: tuple[Job, ...]
    reports: tuple[SourceReport, ...]


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: object) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(str(value or ""))
        parser.close()
    except (AssertionError, ValueError):
        return re.sub(r"<[^>]*>", " ", str(value or ""))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


class _JobLinkParser(HTMLParser):
    def __init__(self, link_pattern: str) -> None:
        super().__init__(convert_charrefs=True)
        self.link_pattern = re.compile(link_pattern, re.IGNORECASE)
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._href:
            return
        href = dict(attrs).get("href") or ""
        if self.link_pattern.search(href):
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            title = re.sub(r"\s+", " ", " ".join(self._text)).strip()
            if title and title.casefold() not in {"voir le detail", "voir l'offre"}:
                self.links.append((self._href, title))
            self._href = ""
            self._text = []


def parse_job_listing_html(
    html: str,
    *,
    source: str,
    base_url: str,
    link_pattern: str,
    country: str,
) -> list[Job]:
    parser = _JobLinkParser(link_pattern)
    parser.feed(html)
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    for href, title in parser.links:
        url = href if href.startswith("http") else f"{base_url.rstrip('/')}/{href.lstrip('/')}"
        if url in seen_urls:
            continue
        seen_urls.add(url)
        link_match = re.search(
            rf"href=[\"']{re.escape(href)}[\"']",
            html,
            re.IGNORECASE,
        )
        card_html = ""
        if link_match:
            marker = '<li class="cards__item' if source == "Randstad" else '<li class="views-row"'
            card_start = html.rfind(marker, 0, link_match.start())
            card_end = html.find(marker, link_match.end())
            if card_start >= 0:
                card_html = html[card_start : card_end if card_end >= 0 else link_match.end() + 3000]
        if not card_html:
            match = re.search(re.escape(href) + r".{0,3000}", html, re.IGNORECASE | re.DOTALL)
            card_html = match.group(0) if match else ""
        context = html_to_text(card_html)
        location = country
        if source == "Randstad":
            location_match = re.search(
                r'<li class="cards__meta-item">.*?</svg></span>(.*?)</li>',
                card_html,
                re.IGNORECASE | re.DOTALL,
            )
            if location_match:
                location = html_to_text(location_match.group(1)).title()
        else:
            location_match = re.search(
                r'<div class="job-location"[^>]*>(.*?)</div>',
                card_html,
                re.IGNORECASE | re.DOTALL,
            )
            if location_match:
                location = html_to_text(location_match.group(1))
        jobs.append(
            Job(
                source=source,
                source_id=url.rstrip("/").rsplit("/", 1)[-1],
                title=title,
                company=source,
                location=location,
                country=country,
                url=url,
                description=context,
            )
        )
    return jobs


def _as_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item)
    if value:
        return (str(value),)
    return ()


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 25,
) -> object:
    request_headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    request_headers.update(headers or {})
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise SourceError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise SourceError(f"connexion impossible: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise SourceError(str(exc)) from exc


def _request_text(url: str, *, timeout: float = 25) -> str:
    request = Request(
        url,
        headers={"Accept": "text/html", "User-Agent": _USER_AGENT},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise SourceError(f"HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise SourceError(f"connexion impossible: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SourceError(str(exc)) from exc


def parse_arbeitnow(payload: object) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SourceError("reponse Arbeitnow invalide")
    jobs: list[Job] = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        location = str(item.get("location") or "")
        jobs.append(
            Job(
                source="Arbeitnow",
                source_id=str(item.get("slug") or item.get("url") or ""),
                title=str(item.get("title") or ""),
                company=str(item.get("company_name") or "Non renseignee"),
                location=location,
                country=canonical_country("", location),
                url=str(item.get("url") or ""),
                description=html_to_text(item.get("description")),
                published_at=str(item.get("created_at") or ""),
                employment_type=", ".join(_as_tuple(item.get("job_types"))),
                remote=bool(item.get("remote")),
                tags=_as_tuple(item.get("tags")),
            )
        )
    return jobs


def parse_remotive(payload: object) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SourceError("reponse Remotive invalide")
    jobs: list[Job] = []
    for item in payload["jobs"]:
        if not isinstance(item, dict):
            continue
        location = str(item.get("candidate_required_location") or "Remote")
        jobs.append(
            Job(
                source="Remotive",
                source_id=str(item.get("id") or item.get("url") or ""),
                title=str(item.get("title") or ""),
                company=str(item.get("company_name") or "Non renseignee"),
                location=location,
                country=canonical_country("", location),
                url=str(item.get("url") or ""),
                description=html_to_text(item.get("description")),
                published_at=str(item.get("publication_date") or ""),
                employment_type=str(item.get("job_type") or ""),
                remote=True,
                tags=_as_tuple(item.get("tags")) + _as_tuple(item.get("category")),
            )
        )
    return jobs


def parse_jobicy(payload: object) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise SourceError("reponse Jobicy invalide")
    jobs: list[Job] = []
    for item in payload["jobs"]:
        if not isinstance(item, dict):
            continue
        location = str(item.get("jobGeo") or "Remote")
        jobs.append(
            Job(
                source="Jobicy",
                source_id=str(item.get("id") or item.get("jobSlug") or ""),
                title=str(item.get("jobTitle") or ""),
                company=str(item.get("companyName") or "Non renseignee"),
                location=location,
                country=canonical_country("", location),
                url=str(item.get("url") or ""),
                description=html_to_text(
                    item.get("jobDescription") or item.get("jobExcerpt")
                ),
                published_at=str(item.get("pubDate") or ""),
                employment_type=", ".join(_as_tuple(item.get("jobType"))),
                remote=True,
                tags=_as_tuple(item.get("jobIndustry")) + _as_tuple(item.get("jobLevel")),
            )
        )
    return jobs


def parse_france_travail(payload: object) -> list[Job]:
    if not isinstance(payload, dict):
        raise SourceError("reponse France Travail invalide")
    raw_results = payload.get("resultats")
    if not isinstance(raw_results, list):
        raise SourceError("reponse France Travail invalide")
    jobs: list[Job] = []
    for raw_item in raw_results:
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)
        raw_company = item.get("entreprise")
        raw_location = item.get("lieuTravail")
        raw_origin = item.get("origineOffre")
        raw_contact = item.get("contact")
        company_data: dict[object, object] = raw_company if isinstance(raw_company, dict) else {}
        location_data: dict[object, object] = (
            raw_location if isinstance(raw_location, dict) else {}
        )
        origin_data: dict[object, object] = raw_origin if isinstance(raw_origin, dict) else {}
        contact_data: dict[object, object] = raw_contact if isinstance(raw_contact, dict) else {}
        offer_id = str(item.get("id") or "")
        url = str(
            origin_data.get("urlOrigine")
            or contact_data.get("urlPostulation")
            or f"https://candidat.francetravail.fr/offres/recherche/detail/{offer_id}"
        )
        jobs.append(
            Job(
                source="France Travail",
                source_id=offer_id,
                title=str(item.get("intitule") or ""),
                company=str(company_data.get("nom") or "Non renseignee"),
                location=str(location_data.get("libelle") or "France"),
                country="France",
                url=url,
                description=html_to_text(item.get("description")),
                published_at=str(item.get("dateCreation") or item.get("dateActualisation") or ""),
                employment_type=str(
                    item.get("typeContratLibelle") or item.get("typeContrat") or ""
                ),
                tags=_as_tuple(item.get("romeLibelle")) + _as_tuple(item.get("secteurActiviteLibelle")),
            )
        )
    return jobs


def parse_serpapi(payload: object, fallback_country: str = "") -> list[Job]:
    if not isinstance(payload, dict):
        raise SourceError("reponse SerpAPI invalide")
    if payload.get("error"):
        raise SourceError(str(payload["error"]))
    raw_jobs = payload.get("jobs_results", [])
    if not isinstance(raw_jobs, list):
        raise SourceError("reponse Google Jobs invalide")
    jobs: list[Job] = []
    for raw_item in raw_jobs:
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, object], raw_item)
        raw_detected = item.get("detected_extensions")
        detected: dict[object, object] = (
            raw_detected if isinstance(raw_detected, dict) else {}
        )
        apply_options = item.get("apply_options")
        first_apply = apply_options[0] if isinstance(apply_options, list) and apply_options else {}
        location = str(item.get("location") or fallback_country)
        jobs.append(
            Job(
                source=f"Google Jobs / {item.get('via') or 'source inconnue'}",
                source_id=str(item.get("job_id") or item.get("share_link") or ""),
                title=str(item.get("title") or ""),
                company=str(item.get("company_name") or "Non renseignee"),
                location=location,
                country=canonical_country(fallback_country, location),
                url=str(
                    first_apply.get("link")
                    if isinstance(first_apply, dict) and first_apply.get("link")
                    else item.get("share_link") or ""
                ),
                description=html_to_text(item.get("description")),
                published_at=str(detected.get("posted_at") or ""),
                employment_type=str(detected.get("schedule_type") or ""),
                remote=bool(detected.get("work_from_home")),
                tags=_as_tuple(item.get("extensions")),
            )
        )
    return jobs


def fetch_arbeitnow(*, max_pages: int = 3, timeout: float = 25) -> list[Job]:
    jobs: list[Job] = []
    next_url: str | None = "https://www.arbeitnow.com/api/job-board-api"
    for _ in range(max(1, max_pages)):
        if not next_url:
            break
        payload = _request_json(next_url, timeout=timeout)
        jobs.extend(parse_arbeitnow(payload))
        if not isinstance(payload, dict):
            break
        links = payload.get("links")
        next_url = str(links.get("next")) if isinstance(links, dict) and links.get("next") else None
    return jobs


def _fetch_query_source(
    *,
    queries: Iterable[str],
    url_factory: Callable[[str], str],
    parser: Callable[[object], list[Job]],
    timeout: float,
) -> list[Job]:
    jobs: list[Job] = []
    for query in queries:
        jobs.extend(parser(_request_json(url_factory(query), timeout=timeout)))
    return jobs


def fetch_remotive(
    *, queries: Iterable[str] = DEFAULT_QUERIES[:2], timeout: float = 25
) -> list[Job]:
    return _fetch_query_source(
        queries=queries,
        url_factory=lambda query: "https://remotive.com/api/remote-jobs?"
        + urlencode({"search": query, "limit": 100}),
        parser=parse_remotive,
        timeout=timeout,
    )


def fetch_jobicy(
    *, queries: Iterable[str] = DEFAULT_QUERIES[:2], timeout: float = 25
) -> list[Job]:
    return _fetch_query_source(
        queries=queries,
        url_factory=lambda query: "https://jobicy.com/api/v2/remote-jobs?"
        + urlencode({"count": 50, "tag": query}),
        parser=parse_jobicy,
        timeout=timeout,
    )


def fetch_randstad(*, timeout: float = 25) -> list[Job]:
    url = "https://www.randstad.fr/emploi/q-automatisme/"
    return parse_job_listing_html(
        _request_text(url, timeout=timeout),
        source="Randstad",
        base_url="https://www.randstad.fr",
        link_pattern=r"^/emploi/[^/]+_[^/]+/$",
        country="France",
    )


def fetch_michael_page(*, timeout: float = 25) -> list[Job]:
    url = "https://www.michaelpage.fr/jobs/automatisme"
    return parse_job_listing_html(
        _request_text(url, timeout=timeout),
        source="Michael Page",
        base_url="https://www.michaelpage.fr",
        link_pattern=r"^/job-detail/.+/ref/[^/]+$",
        country="France",
    )


def _france_travail_token(client_id: str, client_secret: str, timeout: float) -> str:
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "api_offresdemploiv2 o2dsoffre",
        }
    ).encode("ascii")
    payload = _request_json(
        "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=body,
        timeout=timeout,
    )
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise SourceError("jeton France Travail absent de la reponse")
    return str(payload["access_token"])


def fetch_france_travail(
    *,
    client_id: str,
    client_secret: str,
    queries: Iterable[str] = DEFAULT_QUERIES,
    timeout: float = 25,
) -> list[Job]:
    token = _france_travail_token(client_id, client_secret, timeout)
    jobs: list[Job] = []
    for query in queries:
        url = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search?" + urlencode(
            {"motsCles": query, "range": "0-149"}
        )
        payload = _request_json(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        jobs.extend(parse_france_travail(payload))
    return jobs


def fetch_serpapi(
    *,
    api_key: str,
    locations: Iterable[str] = ("Geneva, Switzerland", "Vaud, Switzerland", "France"),
    query: str = "(automation OR robotics) (pharma OR biotech OR laboratory)",
    timeout: float = 25,
) -> list[Job]:
    jobs: list[Job] = []
    for location in locations:
        country = "Switzerland" if "switzerland" in location.casefold() else "France"
        url = "https://serpapi.com/search.json?" + urlencode(
            {
                "engine": "google_jobs",
                "q": query,
                "location": location,
                "hl": "fr",
                "api_key": api_key,
            }
        )
        jobs.extend(parse_serpapi(_request_json(url, timeout=timeout), country))
    return jobs


def collect_career_sites(
    *,
    companies: Iterable[CareerCompany],
    queries: Iterable[str] = DEFAULT_QUERIES,
    countries: Iterable[str] = ("France", "Switzerland"),
    enabled_companies: Iterable[str] | None = None,
    timeout: float = 25,
    max_pages: int = 20,
    max_workers: int = 6,
) -> CollectionResult:
    company_list = tuple(companies)
    selected = (
        {name.casefold() for name in enabled_companies} if enabled_companies is not None else None
    )
    query_list = tuple(query.strip() for query in queries if query.strip())
    country_list = tuple(countries)
    jobs: list[Job] = []
    reports_by_name: dict[str, SourceReport] = {}
    automatic: list[CareerCompany] = []

    for company in company_list:
        if not company.automatic:
            reports_by_name[company.name] = SourceReport(
                company.name,
                "manual_only",
                0,
                "Controle manuel sur le portail officiel.",
                company.careers_url,
            )
        elif selected is not None and company.name.casefold() not in selected:
            reports_by_name[company.name] = SourceReport(
                company.name,
                "disabled",
                0,
                "Employeur non selectionne.",
                company.careers_url,
            )
        else:
            automatic.append(company)

    def fetch(company: CareerCompany) -> list[Job]:
        return fetch_career_company(
            company,
            queries=query_list,
            countries=country_list,
            timeout=timeout,
            max_pages=max_pages,
        )

    if automatic:
        worker_count = max(1, min(max_workers, len(automatic)))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(fetch, company): company for company in automatic}
            for future in as_completed(futures):
                company = futures[future]
                try:
                    company_jobs = future.result()
                except (CareerSiteError, OSError, ValueError) as exc:
                    reports_by_name[company.name] = SourceReport(
                        company.name,
                        "error",
                        0,
                        str(exc),
                        company.careers_url,
                    )
                    continue
                jobs.extend(company_jobs)
                reports_by_name[company.name] = SourceReport(
                    company.name,
                    "ok",
                    len(company_jobs),
                    "",
                    company.careers_url,
                )

    reports = tuple(reports_by_name[company.name] for company in company_list)
    return CollectionResult(tuple(jobs), reports)


def collect_jobs(
    *,
    enabled_sources: Iterable[str] = (CAREER_SITES_SOURCE,),
    queries: Iterable[str] = DEFAULT_QUERIES,
    countries: Iterable[str] = ("France", "Switzerland"),
    career_companies: Iterable[CareerCompany] | None = None,
    enabled_companies: Iterable[str] | None = None,
    career_max_pages: int = 20,
    career_workers: int = 6,
    arbeitnow_pages: int = 3,
    timeout: float = 25,
    france_travail_client_id: str | None = None,
    france_travail_client_secret: str | None = None,
    serpapi_api_key: str | None = None,
) -> CollectionResult:
    enabled = set(enabled_sources)
    queries_tuple = tuple(query.strip() for query in queries if query.strip())
    jobs: list[Job] = []
    reports: list[SourceReport] = []
    france_travail_client_id = france_travail_client_id or os.getenv("FRANCE_TRAVAIL_CLIENT_ID")
    france_travail_client_secret = france_travail_client_secret or os.getenv(
        "FRANCE_TRAVAIL_CLIENT_SECRET"
    )
    serpapi_api_key = serpapi_api_key or os.getenv("SERPAPI_API_KEY")
    serpapi_call: Callable[[], list[Job]] | None = None
    if serpapi_api_key:
        configured_serpapi_key = str(serpapi_api_key)
        serpapi_call = lambda: fetch_serpapi(
            api_key=configured_serpapi_key,
            timeout=timeout,
        )

    if CAREER_SITES_SOURCE in enabled:
        try:
            companies = (
                tuple(career_companies)
                if career_companies is not None
                else load_career_companies()
            )
            career_result = collect_career_sites(
                companies=companies,
                queries=queries_tuple,
                countries=countries,
                enabled_companies=enabled_companies,
                timeout=timeout,
                max_pages=career_max_pages,
                max_workers=career_workers,
            )
        except CareerSiteError as exc:
            reports.append(SourceReport(CAREER_SITES_SOURCE, "error", 0, str(exc)))
        else:
            jobs.extend(career_result.jobs)
            reports.extend(career_result.reports)

    source_calls: list[tuple[str, Callable[[], list[Job]] | None, str]] = [
        (
            "Arbeitnow",
            lambda: fetch_arbeitnow(max_pages=arbeitnow_pages, timeout=timeout),
            "",
        ),
        (
            "Remotive",
            lambda: fetch_remotive(queries=queries_tuple[:2], timeout=timeout),
            "",
        ),
        (
            "Jobicy",
            lambda: fetch_jobicy(queries=queries_tuple[:2], timeout=timeout),
            "",
        ),
        ("Randstad", lambda: fetch_randstad(timeout=timeout), ""),
        ("Michael Page", lambda: fetch_michael_page(timeout=timeout), ""),
        (
            "France Travail",
            (
                lambda: fetch_france_travail(
                    client_id=str(france_travail_client_id),
                    client_secret=str(france_travail_client_secret),
                    queries=queries_tuple,
                    timeout=timeout,
                )
            )
            if france_travail_client_id and france_travail_client_secret
            else None,
            "Definir FRANCE_TRAVAIL_CLIENT_ID et FRANCE_TRAVAIL_CLIENT_SECRET.",
        ),
        (
            "Google Jobs (SerpAPI)",
            serpapi_call,
            "Definir SERPAPI_API_KEY.",
        ),
    ]

    for source_name, source_call, missing_configuration in source_calls:
        if source_name not in enabled:
            continue
        if source_call is None:
            reports.append(SourceReport(source_name, "not_configured", 0, missing_configuration))
            continue
        try:
            source_jobs = source_call()
        except (SourceError, OSError, ValueError) as exc:
            reports.append(SourceReport(source_name, "error", 0, str(exc)))
            continue
        jobs.extend(source_jobs)
        reports.append(SourceReport(source_name, "ok", len(source_jobs)))

    return CollectionResult(tuple(jobs), tuple(reports))