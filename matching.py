"""Rank by subject-matter fit. Only explicit senior titles are marked out of target."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import re
from typing import Iterable

from .core import Job, MatchResult, _contains_term, normalize_text, parse_published_at

# Search terms also drive eligibility, so adjacent roles survive the initial filter.
SEARCH_FAMILIES = {
    "Automatisation de laboratoire": ("laboratory automation", "lab automation", "liquid handling", "tecan", "fluentcontrol", "laboratory robotics", "automation", "automatisation"),
    "Bioproduction & procédés": ("bioprocess", "bioproduction", "process development", "upstream", "downstream", "purification", "bioprocedes"),
    "Essais & protéines": ("assay development", "research associate", "protein", "antibody", "bioanalytical", "analytical development"),
    "Applications & outils scientifiques": ("application scientist", "applications scientist", "field application", "scientific software", "laboratory informatics", "lims"),
    "Automatisation industrielle": ("automation", "automatisation", "automatisme", "robotics", "plc", "scada", "dcs", "mes"),
}
SKILL_GROUPS = {
    "Tecan / FluentControl": ("tecan", "fluentcontrol"),
    "Python": ("python",),
    "Robotique de laboratoire": ("liquid handling", "liquid handler", "laboratory automation", "lab automation", "pipetting", "pipetage"),
    "DoE": ("doe", "design of experiments", "experimental design", "plans d'experiences"),
    "Protéines / anticorps / ADC": ("protein", "proteins", "proteine", "proteines", "antibody", "antibodies", "anticorps", "adc", "biologics"),
    "Bioproduction / purification": ("bioproduction", "bioprocess", "purification", "upstream", "downstream", "usp", "dsp"),
    "Essais & culture cellulaire": ("assay", "assays", "elisa", "cell culture", "culture cellulaire", "pcr"),
    "Données & logiciels": ("docker", "postgresql", "aws", "streamlit", "bioinformatics", "bioinformatique"),
}
DEFAULT_FAMILIES = tuple(SEARCH_FAMILIES)[:2]
SEARCH_QUERIES = {
    "Automatisation de laboratoire": ("automation", "automatisation", "liquid handling", "tecan"),
    "Bioproduction & procédés": ("bioprocess", "bioproduction", "process development", "purification"),
    "Essais & protéines": ("assay development", "research associate", "protein engineering", "analytical development"),
    "Applications & outils scientifiques": ("application scientist", "applications scientist", "scientific software", "laboratory informatics"),
    "Automatisation industrielle": ("automation", "automatisme", "robotics", "scada"),
}


@dataclass(frozen=True)
class CandidateProfile:
    skills: tuple[str, ...] = tuple(SKILL_GROUPS)
    preferred_countries: tuple[str, ...] = ("France", "Switzerland")


@dataclass(frozen=True)
class ProfileFit:
    fit: int
    priority: int
    band: str
    family: str
    strengths: tuple[str, ...]
    concerns: tuple[str, ...]
    unknowns: tuple[str, ...]
    experience_required: int | None
    age_days: int | None
    senior: bool = False


def family_terms(families: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(term for family in families for term in SEARCH_FAMILIES.get(family, ())))


def family_queries(families: Iterable[str]) -> tuple[str, ...]:
    """Avoid redundant ATS searches while retaining richer local matching terms."""
    return tuple(dict.fromkeys(term for family in families for term in SEARCH_QUERIES.get(family, ())))


def _has(text: str, terms: Iterable[str]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def is_senior_role(title: str) -> bool:
    text = normalize_text(title)
    # Mixed-level advertisements are still opportunities, not senior-only roles.
    if _has(text, ("junior", "graduate", "entry level", "entry-level")):
        return False
    if re.search(r"\(senior\)|(?:scientist|engineer)\s*[/–-]\s*senior", text):
        return False
    if re.search(r"\bstaff(?:\s+\w+){0,3}\s+(?:engineer|scientist)\b", text):
        return True
    return _has(text, (
        "senior", "sr", "principal", "staff scientist", "staff engineer",
        "director", "directeur", "directrice", "head of", "vice president",
        "vp", "lead scientist", "lead engineer", "lead researcher",
    ))


def assess_job(job: Job, country: str, profile: CandidateProfile, *, now: datetime | None = None) -> ProfileFit:
    title = normalize_text(job.title)
    description = normalize_text(job.description)
    text = f"{title} {description}"
    classification_terms = {
        name: tuple(term for term in terms if term not in ("automation", "automatisation", "automatisme", "robotics"))
        for name, terms in SEARCH_FAMILIES.items()
    }
    title_family = next((name for name, terms in classification_terms.items() if _has(title, terms)), None)
    family = title_family or next((name for name, terms in classification_terms.items() if _has(description, terms)), "À explorer")
    strengths = tuple(name for name in profile.skills if name in SKILL_GROUPS and _has(text, SKILL_GROUPS[name]))
    concerns: list[str] = []
    unknowns: list[str] = []
    fit = min(70, len(strengths) * 14) + (20 if family != "À explorer" else 0) + (10 if title_family else 0)
    senior = is_senior_role(job.title)
    if senior:
        concerns.append("Intitulé explicitement senior")
    # These adjustments express a different job domain, not ineligibility.
    if _has(title, ("qa automation", "test automation", "sdet")):
        concerns.append("Tests logiciels plutôt qu'automatisation de laboratoire")
        fit -= 35
    if _has(title, ("accountant", "finance", "recruiter", "talent acquisition", "human resources", "marketing", "sales", "legal", "account executive", "business development")):
        concerns.append("Fonction commerciale ou support")
        fit -= 35
    if len(description) < 180:
        unknowns.append("Description partielle")
        fit = min(fit, 55)
    now = now or datetime.now(UTC)
    published = parse_published_at(job.published_at)
    age = (now - published).days if published else None
    if age is None or age < 0:
        age = None
    fit = max(0, min(100, fit))
    freshness = 10 if age is not None and age <= 7 else 6 if age is not None and age <= 30 else 0
    priority = min(100, round(fit * .8) + freshness + (5 if country in profile.preferred_countries else 0) + (5 if job.direct_employer else 0))
    band = "Senior" if senior else "Prioritaire" if fit >= 65 else "À examiner" if fit >= 40 else "Faible adéquation"
    return ProfileFit(fit, priority, band, family, strengths, tuple(concerns), tuple(unknowns), None, age, senior)


def rank_for_profile(results: Iterable[MatchResult], profile: CandidateProfile, *, now: datetime | None = None) -> list[MatchResult]:
    enriched = [replace(result, profile_fit=assess_job(result.job, result.country, profile, now=now)) for result in results]
    return sorted(enriched, key=lambda r: (not r.profile_fit.senior, r.profile_fit.priority, r.profile_fit.fit, r.score), reverse=True)
