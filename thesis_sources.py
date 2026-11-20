from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from .core import Job
from .sources import CollectionResult, SourceError, SourceReport, html_to_text


@dataclass(frozen=True, slots=True)
class ThesisPortal:
    name: str
    url: str
    country: str
    automatic: bool
    description: str


THESIS_PORTALS = (
    ThesisPortal(
        "Doctorat.gouv.fr",
        "https://app.doctorat.gouv.fr/",
        "France",
        True,
        "Plateforme nationale des sujets de thèse",
    ),
    ThesisPortal(
        "ABG",
        "https://www.abg.asso.fr/fr/candidatOffres",
        "France / international",
        True,
        "Sujets déposés par laboratoires et entreprises",
    ),
    ThesisPortal(
        "myScience",
        "https://www.myscience.ch/jobs",
        "Suisse",
        True,
        "Postes doctoraux des universités suisses",
    ),
    ThesisPortal(
        "Campus France",
        "https://doctorat.campusfrance.org/phd/offers/search/my/reset",
        "France",
        False,
        "Doctorats, stages de master et postdoctorats",
    ),
    ThesisPortal(
        "EURAXESS",
        "https://euraxess.ec.europa.eu/jobs/search",
        "France / Suisse / Europe",
        False,
        "Offres de recherche européennes, filtre R1",
    ),
    ThesisPortal(
        "Academic Positions · France",
        "https://academicpositions.fr/jobs/position/phd/country/france",
        "France",
        False,
        "Agrégateur international d'offres de PhD",
    ),
    ThesisPortal(
        "Academic Positions · Suisse",
        "https://academicpositions.fr/jobs/position/phd/country/switzerland",
        "Suisse",
        False,
        "Agrégateur international d'offres de PhD",
    ),
    ThesisPortal(
        "ANRT Cifre",
        "https://offres-et-candidatures-cifre.anrt.asso.fr/",
        "France",
        False,
        "Thèses en partenariat entreprise-laboratoire, compte requis",
    ),
)

_USER_AGENT = "ThesisRadar/1.0 (personal thesis-search tool)"


class _MatchingLinkParser(HTMLParser):
    def __init__(self, pattern: str) -> None:
        super().__init__(convert_charrefs=True)
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.links: list[tuple[str, str, int]] = []
        self._href = ""
        self._text: list[str] = []
        self._start = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a" or self._href:
            return
        href = dict(attrs).get("href") or ""
        if self.pattern.search(href):
            self._href = href
            self._text = []
            self._start = self.getpos()[1]

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._href:
            return
        title = re.sub(r"\s+", " ", " ".join(self._text)).strip()
        if title:
            self.links.append((self._href, title, self._start))
        self._href = ""
        self._text = []


def _matching_links(html: str, pattern: str) -> list[tuple[str, str]]:
    parser = _MatchingLinkParser(pattern)
    parser.feed(html)
    return [(href, title) for href, title, _ in parser.links]


def _context_after_link(html: str, href: str, *, limit: int = 4500) -> str:
    match = re.search(rf"href=[\"']{re.escape(href)}[\"']", html, re.IGNORECASE)
    if not match:
        return ""
    return html_to_text(html[match.start() : match.start() + limit])


def _context_before_link(html: str, href: str, *, limit: int = 3200) -> str:
    match = re.search(rf"href=[\"']{re.escape(href)}[\"']", html, re.IGNORECASE)
    if not match:
        return ""
    return html_to_text(html[max(0, match.start() - limit) : match.end()])


def _date_from_context(context: str) -> str:
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", context)
    if not match:
        return ""
    day, month, year = match.groups()
    return f"{('20' + year) if len(year) == 2 else year}-{int(month):02d}-{int(day):02d}"


def parse_doctorat_gouv(payload: object) -> list[Job]:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise SourceError("réponse Doctorat.gouv.fr invalide")
    jobs: list[Job] = []
    for item in payload["content"]:
        if not isinstance(item, dict) or item.get("typeProposition") != "proposition":
            continue
        source_id = str(item.get("id") or "")
        if not source_id:
            continue
        description = html_to_text(
            " ".join(
                str(item.get(field) or "")
                for field in ("resume", "resumeAnglais", "objectif", "contexte")
            )
        )
        location = str(item.get("uniteRechercheVille") or item.get("etablissementVille") or "France")
        jobs.append(
            Job(
                source="Doctorat.gouv.fr",
                source_id=source_id,
                title=str(item.get("theseTitre") or item.get("theseTitreAnglais") or "Sujet de thèse"),
                company=str(item.get("etablissementLibelle") or item.get("ecoleDoctoraleLibelle") or "École doctorale / laboratoire"),
                location=location,
                country="France",
                url=f"https://app.doctorat.gouv.fr/proposition?id={source_id}",
                description=description,
                published_at=str(item.get("dateMiseEnLigne") or ""),
                employment_type="Sujet de thèse",
                tags=tuple(str(value) for value in (item.get("domainesImpactListe") or []) if value),
            )
        )
    return jobs


def parse_abg_theses(html: str) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    links = _matching_links(html, r"/candidatOffres/show/id_offre/\d+")
    for index, (href, title) in enumerate(links):
        url = urljoin("https://www.abg.asso.fr/", href)
        if url in seen:
            continue
        seen.add(url)
        start_match = re.search(rf"href=[\"']{re.escape(href)}[\"']", html, re.IGNORECASE)
        if not start_match:
            continue
        end = len(html)
        if index + 1 < len(links):
            next_match = re.search(
                rf"href=[\"']{re.escape(links[index + 1][0])}[\"']",
                html[start_match.end() :],
                re.IGNORECASE,
            )
            if next_match:
                end = start_match.end() + next_match.start()
        card_html = html[start_match.start() : end]
        type_match = re.search(r"<span class=['\"]type['\"]>(.*?)</span>", card_html, re.IGNORECASE | re.DOTALL)
        if not type_match or html_to_text(type_match.group(1)).casefold() not in {"these", "thèse"}:
            continue
        context = html_to_text(card_html)
        source_id_match = re.search(r"id_offre/(\d+)", href)
        country = "Switzerland" if re.search(r"\b(?:Suisse|Switzerland)\b", context, re.IGNORECASE) else "France"
        if re.search(r"\bCanada\b", context, re.IGNORECASE):
            country = "Canada"
        company_match = re.search(r"<div class=['\"]societe['\"]>(.*?)<span", card_html, re.IGNORECASE | re.DOTALL)
        location_match = re.search(r"<div class=['\"]adresse['\"]>(.*?)</div>", card_html, re.IGNORECASE | re.DOTALL)
        location = html_to_text(location_match.group(1)) if location_match else country
        jobs.append(
            Job(
                source="ABG",
                source_id=source_id_match.group(1) if source_id_match else url,
                title=title,
                company=html_to_text(company_match.group(1)) if company_match else "Laboratoire / entreprise",
                location=location,
                country=country,
                url=url,
                description=context,
                published_at=_date_from_context(context),
                employment_type="Sujet de thèse",
            )
        )
    return jobs


def parse_myscience_theses(html: str) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for href, title in _matching_links(html, r"/jobs/id\d+"):
        url = urljoin("https://www.myscience.ch/", href)
        if url in seen:
            continue
        seen.add(url)
        context = _context_after_link(html, href, limit=1800)
        thesis_text = f"{title} {context}"
        if not re.search(
            r"\b(?:PhD|doctoral|doctorant|doctorante|Doktorand|Doktorandin)\b",
            thesis_text,
            re.IGNORECASE,
        ):
            continue
        source_id_match = re.search(r"/jobs/id(\d+)", href)
        jobs.append(
            Job(
                source="myScience",
                source_id=source_id_match.group(1) if source_id_match else url,
                title=title,
                company="Université / institut suisse",
                location="Switzerland",
                country="Switzerland",
                url=url,
                description=context,
                employment_type="Doctorat / PhD",
            )
        )
    return jobs


def _request_text(url: str, timeout: float) -> str:
    request = Request(url, headers={"Accept": "text/html", "User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise SourceError(f"HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise SourceError(f"connexion impossible: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SourceError(str(exc)) from exc


def _request_doctorat_page(page: int, timeout: float) -> object:
    url = "https://app.doctorat.gouv.fr/api/propositions-these?" + urlencode(
        {
            "page": page,
            "size": 200,
            "typeProposition": "proposition",
            "sortField": "dateMiseEnLigne",
            "sortDirection": "DESC",
        }
    )
    request = Request(url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SourceError(str(exc)) from exc


def fetch_doctorat_gouv(*, timeout: float = 25, max_pages: int = 4) -> list[Job]:
    jobs: list[Job] = []
    for page in range(max_pages):
        payload = _request_doctorat_page(page, timeout)
        jobs.extend(parse_doctorat_gouv(payload))
        if not isinstance(payload, dict) or payload.get("last") is True:
            break
    return jobs


def fetch_abg_theses(*, queries: tuple[str, ...], timeout: float = 25) -> list[Job]:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    try:
        opener.open(
            Request(
                "https://www.abg.asso.fr/fr/candidatOffres",
                headers={"User-Agent": _USER_AGENT},
            ),
            timeout=timeout,
        ).read()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise SourceError(str(exc)) from exc

    jobs: list[Job] = []
    search_queries = queries or ("",)
    for query in search_queries:
        body = urlencode(
            {
                "page": "1",
                "orderBy": "updated_at_desc",
                "autoload": "0",
                "criteria[mot_cle]": query,
                "criteria[id_type_offre]": "2",
                "criteria[id_type_contrat]": "",
                "criteria[id_champs_scientifiques]": "",
                "criteria[id_niveau_etude]": "",
                "criteria[id_metier]": "",
                "criteria[id_off_liste_salaire]": "",
                "criteria[international]": "",
                "criteria[geo_liste_region_id_region]": "",
                "criteria[geo_liste_region_id_region_france]": "",
                "criteria[nb_offres_list]": "50",
                "reset_all": "0",
            }
        ).encode("utf-8")
        request = Request(
            "https://www.abg.asso.fr/fr/candidatOffres/recherche",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.abg.asso.fr/fr/candidatOffres",
                "User-Agent": _USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
            },
            method="POST",
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                jobs.extend(parse_abg_theses(response.read().decode("utf-8", errors="replace")))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise SourceError(str(exc)) from exc
    return jobs


def collect_thesis_sources(
    *, queries: tuple[str, ...] = (), timeout: float = 25
) -> CollectionResult:
    source_calls = (
        ("Doctorat.gouv.fr", "https://app.doctorat.gouv.fr/", lambda: fetch_doctorat_gouv(timeout=timeout)),
        ("ABG", "https://www.abg.asso.fr/fr/candidatOffres", lambda: fetch_abg_theses(queries=queries, timeout=timeout)),
        ("myScience", "https://www.myscience.ch/jobs", lambda: parse_myscience_theses(_request_text("https://www.myscience.ch/jobs", timeout))),
    )
    jobs: list[Job] = []
    reports: list[SourceReport] = []
    for source_name, url, fetcher in source_calls:
        try:
            source_jobs = fetcher()
        except (SourceError, OSError, ValueError) as exc:
            reports.append(SourceReport(source_name, "error", 0, str(exc), url))
            continue
        jobs.extend(source_jobs)
        reports.append(SourceReport(source_name, "ok", len(source_jobs), "", url))
    return CollectionResult(tuple(jobs), tuple(reports))