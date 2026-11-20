from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
import re
from typing import TYPE_CHECKING, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import unicodedata


if TYPE_CHECKING:
    from .matching import ProfileFit


DEFAULT_AUTOMATION_TERMS = (
    "automation",
    "automatisation",
    "automatisme",
    "robotics",
    "robotique",
    "robotic",
    "laboratory automation",
    "lab automation",
    "liquid handling",
    "liquid handler",
    "high throughput",
    "high-throughput",
    "process control",
    "process automation",
    "industrial automation",
    "controle commande",
    "controle-commande",
    "plc",
    "scada",
    "dcs",
    "mes",
    "tecan",
    "hamilton",
    "beckman coulter",
    "siLA",
)

DEFAULT_LIFE_SCIENCE_TERMS = (
    "pharma",
    "pharmaceutical",
    "pharmaceutique",
    "biotech",
    "biotechnology",
    "biotechnologie",
    "biopharma",
    "biologics",
    "life science",
    "sciences de la vie",
    "drug discovery",
    "drug development",
    "developpement de medicaments",
    "gmp",
    "glp",
    "gxp",
    "clinical",
    "clinique",
    "medical diagnostic",
    "diagnostic medical",
    "in vitro diagnostic",
    "ivd",
    "molecular biology",
    "biologie moleculaire",
    "cell therapy",
    "gene therapy",
    "therapie cellulaire",
    "therapie genique",
    "vaccine",
    "vaccin",
    "antibody",
    "anticorps",
    "assay development",
    "bioanalytical",
    "bioanalyse",
)

DEFAULT_THESIS_TERMS = (
    "phd",
    "ph.d",
    "doctoral",
    "doctorat",
    "doctorant",
    "doctorante",
    "doctoral researcher",
    "doctoral candidate",
    "thesis",
    "master thesis",
    "master's thesis",
    "memoire de master",
    "these",
    "cifre",
    "doktorand",
    "doktorandin",
)

DEFAULT_PRIORITY_LOCATIONS = (
    "geneve",
    "geneva",
    "genf",
    "canton de geneve",
    "vaud",
    "canton de vaud",
    "lausanne",
    "nyon",
    "morges",
    "aubonne",
    "saint prex",
    "saint-prex",
    "tolochenaz",
    "corsier sur vevey",
    "corsier-sur-vevey",
    "rolle",
    "gland",
    "prangins",
    "eysins",
    "ecublens",
    "epalinges",
    "vevey",
    "montreux",
    "yverdon",
)

DEFAULT_ROMANDIE_LOCATIONS = DEFAULT_PRIORITY_LOCATIONS + (
    "fribourg",
    "freiburg",
    "bulle",
    "villars sur glane",
    "neuchatel",
    "boudry",
    "couvet",
    "le locle",
    "la chaux de fonds",
    "la chaux-de-fonds",
    "marin epagnier",
    "val de travers",
    "valais",
    "visp",
    "martigny",
    "monthey",
    "sion",
    "sierre",
    "jura",
    "delemont",
)

COUNTRY_ALIASES = {
    "ch": "Switzerland",
    "che": "Switzerland",
    "schweiz": "Switzerland",
    "suisse": "Switzerland",
    "svizzera": "Switzerland",
    "switzerland": "Switzerland",
    "fr": "France",
    "fra": "France",
    "france": "France",
}

# Normalize common ATS ISO codes for Worldwide facets and profile preferences.
for _canonical, _aliases in {
    "United States": ("us", "usa", "united states", "united states of america", "etats-unis"),
    "United Kingdom": ("gb", "gbr", "uk", "united kingdom", "royaume-uni", "england", "scotland"),
    "Germany": ("de", "deu", "germany", "deutschland", "allemagne"),
    "Belgium": ("be", "bel", "belgium", "belgique"),
    "Netherlands": ("nl", "nld", "netherlands", "pays-bas"),
    "Ireland": ("ie", "irl", "ireland", "irlande"),
    "Denmark": ("dk", "dnk", "denmark", "danemark"),
    "Sweden": ("se", "swe", "sweden", "suede"),
    "Austria": ("at", "aut", "austria", "autriche"),
    "Italy": ("it", "ita", "italy", "italie"),
    "Spain": ("es", "esp", "spain", "espagne"),
    "Canada": ("ca", "can", "canada"),
    "Australia": ("au", "aus", "australia", "australie"),
    "Singapore": ("sg", "sgp", "singapore", "singapour"),
}.items():
    COUNTRY_ALIASES.update(dict.fromkeys(_aliases, _canonical))

LOCATION_COUNTRY_HINTS = {
    "Switzerland": (
        "ch",
        "che",
        "switzerland",
        "suisse",
        "schweiz",
        "geneve",
        "geneva",
        "genf",
        "vaud",
        "lausanne",
        "basel",
        "basle",
        "zurich",
        "zug",
        "bern",
        "fribourg",
        "neuchatel",
        "valais",
        *DEFAULT_ROMANDIE_LOCATIONS,
    ),
    "France": (
        "fr",
        "fra",
        "france",
        "paris",
        "lyon",
        "grenoble",
        "annecy",
        "bordeaux",
        "toulouse",
        "strasbourg",
        "lille",
        "nantes",
        "montpellier",
        "marseille",
        "nice",
        "ile de france",
        "auvergne rhone alpes",
        "haute savoie",
    ),
}

_TRACKING_QUERY_KEYS = {
    "gh_src",
    "ref",
    "referrer",
    "source",
    "trk",
}


@dataclass(frozen=True, slots=True)
class Job:
    source: str
    source_id: str
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    country: str = ""
    published_at: str = ""
    employment_type: str = ""
    remote: bool = False
    tags: tuple[str, ...] = ()
    direct_employer: bool = False


@dataclass(frozen=True, slots=True)
class SearchCriteria:
    countries: tuple[str, ...] = ("France", "Switzerland")
    automation_terms: tuple[str, ...] = DEFAULT_AUTOMATION_TERMS
    life_science_terms: tuple[str, ...] = DEFAULT_LIFE_SCIENCE_TERMS
    priority_locations: tuple[str, ...] = DEFAULT_PRIORITY_LOCATIONS
    romandie_locations: tuple[str, ...] = DEFAULT_ROMANDIE_LOCATIONS
    trusted_life_science_companies: tuple[str, ...] = ()
    priority_companies: tuple[str, ...] = ()
    max_age_days: int | None = 60
    include_remote_without_target_country: bool = False
    require_life_science_context: bool = True
    required_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MatchResult:
    job: Job
    score: int
    eligible: bool
    country: str
    priority_location: bool
    romandie_location: bool
    automation_hits: tuple[str, ...]
    life_science_hits: tuple[str, ...]
    required_hits: tuple[str, ...]
    reasons: tuple[str, ...]
    profile_fit: ProfileFit | None = None


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def is_excluded_employer(value: str) -> bool:
    return re.search(r"\badaptyv(?:bio)?\b", normalize_text(value)) is not None


def is_excluded_job(job: Job) -> bool:
    return any(is_excluded_employer(value) for value in (job.company, job.source, job.url))


@lru_cache(maxsize=2048)
def _term_pattern(term: str) -> re.Pattern[str] | None:
    normalized_term = normalize_text(term)
    if not normalized_term:
        return None
    return re.compile(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])")


def _contains_term(text: str, term: str) -> bool:
    pattern = _term_pattern(term)
    return pattern is not None and pattern.search(text) is not None


def _find_terms(text: str, terms: Iterable[str]) -> tuple[str, ...]:
    return tuple(term for term in terms if _contains_term(text, term))


def canonical_country(country: str, location: str = "") -> str:
    normalized_country = normalize_text(country)
    if normalized_country in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[normalized_country]

    normalized_location = normalize_text(location)
    for alias, canonical in COUNTRY_ALIASES.items():
        if (len(alias) > 3 or alias in {"us", "usa", "uk", "gb", "gbr"}) and _contains_term(normalized_location, alias):
            return canonical
    for canonical, hints in LOCATION_COUNTRY_HINTS.items():
        if any(_contains_term(normalized_location, hint) for hint in hints):
            return canonical
    return ""


def parse_published_at(value: str) -> datetime | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if candidate.isdigit():
        try:
            return datetime.fromtimestamp(int(candidate), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(candidate[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    retained_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_QUERY_KEYS
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            urlencode(retained_query),
            "",
        )
    )


def evaluate_job(
    job: Job,
    criteria: SearchCriteria,
    *,
    now: datetime | None = None,
) -> MatchResult:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    title = normalize_text(job.title)
    full_text = normalize_text(
        " ".join((job.title, job.company, job.description, job.location, *job.tags))
    )
    automation_hits = _find_terms(full_text, criteria.automation_terms)
    life_science_hits = _find_terms(full_text, criteria.life_science_terms)
    required_hits = _find_terms(full_text, criteria.required_terms)
    title_automation_hits = _find_terms(title, criteria.automation_terms)
    trusted_company = any(
        _contains_term(normalize_text(job.company), company)
        for company in criteria.trusted_life_science_companies
    )
    priority_company = any(
        _contains_term(normalize_text(job.company), company)
        for company in criteria.priority_companies
    )

    country = canonical_country(job.country, job.location) or job.country.strip()
    target_countries = {canonical_country(value) or value for value in criteria.countries}
    target_geography = not target_countries or country in target_countries
    if job.remote and criteria.include_remote_without_target_country and not country:
        target_geography = True

    normalized_location = normalize_text(job.location)
    priority_location = any(
        _contains_term(normalized_location, location) for location in criteria.priority_locations
    )
    romandie_location = any(
        _contains_term(normalized_location, location) for location in criteria.romandie_locations
    )

    published_at = parse_published_at(job.published_at)
    age_days = (now - published_at).days if published_at else None
    recent_enough = (
        criteria.max_age_days is None
        or age_days is None
        or 0 <= age_days <= criteria.max_age_days
    )
    life_science_context = bool(life_science_hits or trusted_company)
    eligible = bool(automation_hits and target_geography and recent_enough and not is_excluded_job(job))
    if criteria.require_life_science_context:
        eligible = eligible and life_science_context
    if criteria.required_terms:
        eligible = eligible and bool(required_hits)

    score = 0
    reasons: list[str] = []
    if automation_hits:
        score += 30 + min(18, 3 * (len(automation_hits) - 1))
        reasons.append("automation/robotique")
    if title_automation_hits:
        score += 12
    if life_science_hits:
        score += 25 + min(12, 3 * (len(life_science_hits) - 1))
        reasons.append("pharma/biotech")
    elif trusted_company:
        score += 25
        reasons.append("entreprise life sciences")
    if required_hits:
        score += 20
        reasons.append("these/doctorat")
    if target_geography:
        score += 12
    if priority_location:
        score += 32
        reasons.append("priorite Geneve/Vaud")
    elif romandie_location:
        score += 20
        reasons.append("Suisse romande")
    elif country == "Switzerland":
        score += 8
    if priority_company and country == "Switzerland":
        score += 10
        reasons.append("employeur implante en Suisse romande")
    if job.direct_employer:
        score += 8
        reasons.append("site carriere officiel")
    if age_days is not None and age_days >= 0:
        score += max(0, 10 - min(age_days, 30) // 3)
        reasons.append(f"publiee il y a {age_days} j")

    return MatchResult(
        job=job,
        score=score,
        eligible=eligible,
        country=country,
        priority_location=priority_location,
        romandie_location=romandie_location,
        automation_hits=automation_hits,
        life_science_hits=life_science_hits,
        required_hits=required_hits,
        reasons=tuple(reasons),
    )


def _job_fingerprint(job: Job) -> str:
    canonical = canonical_url(job.url)
    if canonical:
        return f"url:{canonical}"
    title = normalize_text(job.title)
    company = normalize_text(job.company)
    location = normalize_text(job.location)
    return f"fields:{title}|{company}|{location}"


def deduplicate_results(results: Iterable[MatchResult]) -> list[MatchResult]:
    by_fingerprint: dict[str, MatchResult] = {}
    for result in results:
        fingerprint = _job_fingerprint(result.job)
        current = by_fingerprint.get(fingerprint)
        if current is None:
            by_fingerprint[fingerprint] = result
            continue
        preferred = result if result.score > current.score else current
        sources = sorted({current.job.source, result.job.source})
        by_fingerprint[fingerprint] = replace(
            preferred,
            job=replace(preferred.job, source=" + ".join(sources)),
        )
    return list(by_fingerprint.values())


def filter_and_rank(
    jobs: Iterable[Job],
    criteria: SearchCriteria | None = None,
    *,
    now: datetime | None = None,
) -> list[MatchResult]:
    active_criteria = criteria or SearchCriteria()
    evaluated = (evaluate_job(job, active_criteria, now=now) for job in jobs)
    eligible = (result for result in evaluated if result.eligible)
    return sorted(
        deduplicate_results(eligible),
        key=lambda result: (
            result.score,
            parse_published_at(result.job.published_at) or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )


def diversify_results(
    results: Iterable[MatchResult],
    *,
    batch_size: int = 2,
) -> list[MatchResult]:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    buckets: dict[str, list[MatchResult]] = {}
    company_order: list[str] = []
    for result in results:
        company_key = normalize_text(result.job.company)
        if company_key not in buckets:
            buckets[company_key] = []
            company_order.append(company_key)
        buckets[company_key].append(result)

    diversified: list[MatchResult] = []
    largest_bucket = max((len(bucket) for bucket in buckets.values()), default=0)
    for offset in range(0, largest_bucket, batch_size):
        for company_key in company_order:
            diversified.extend(buckets[company_key][offset : offset + batch_size])
    return diversified
