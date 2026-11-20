from __future__ import annotations

from datetime import datetime
import sqlite3
import time
from zoneinfo import ZoneInfo

import streamlit as st

from docs.job.job_radar.career_sites import CareerSiteError, load_career_companies
from docs.job.job_radar.core import is_excluded_employer, is_excluded_job
from docs.job.job_radar.dashboard import render_opportunities, render_pipeline, render_preferences
from docs.job.job_radar.matching import DEFAULT_FAMILIES, SEARCH_FAMILIES, family_queries
from docs.job.job_radar.portals import render_portals
from docs.job.job_radar.sources import (
    CAREER_SITES_SOURCE, STAFFING_SOURCES, THESIS_QUERIES,
    CollectionResult, collect_jobs,
)
from docs.job.job_radar.storage import RadarStore
from docs.job.job_radar.thesis_sources import collect_thesis_sources


def _theme():
    st.markdown("""
    <style>
      .stApp { background: #f7f8fa; color: #202a35; }
      [data-testid="stMainBlockContainer"] {
        max-width: 1800px; padding: 3.4rem 2rem 1rem;
      }
      h2 { font-size: 1.45rem !important; letter-spacing: -.04em; }
      h3 { font-size: 1.12rem !important; line-height: 1.4; }
      [data-testid="stVerticalBlock"] { gap: .65rem; }
      [data-testid="stDataFrame"] { border: 1px solid #e0e5eb; border-radius: 8px; }
      [data-testid="stVerticalBlockBorderWrapper"] { background: white; border-radius: 10px; }
      [data-testid="stMarkdownContainer"] p { line-height: 1.5; }
      [data-testid="stCaptionContainer"] { color: #657383; }
      [data-testid="stText"] { white-space: pre-wrap; font: inherit; line-height: 1.65; }
      [data-testid="stBaseButton-primary"], [data-testid="stBaseLinkButton-primary"] {
        background: #126078; border-color: #126078; color: white;
      }
      [data-testid="stButtonGroup"] button[aria-pressed="true"] {
        background: #e5f0f4; color: #126078; border-color: #b9d3dc;
      }
      .stTabs [data-baseweb="tab-list"] { gap: 1rem; }
      @media(max-width: 800px) {
        [data-testid="stMainBlockContainer"] { padding-left: .8rem; padding-right: .8rem; }
      }
    </style>
    """, unsafe_allow_html=True)


@st.cache_data(ttl=900, show_spinner=False)
def _cached_collection(company_names, queries, countries, source_names):
    return collect_jobs(
        enabled_sources=source_names, enabled_companies=company_names,
        queries=queries, countries=countries, timeout=25,
        career_max_pages=20, career_workers=6,
    )


@st.cache_data(ttl=900, show_spinner=False)
def _cached_thesis_collection(queries):
    return collect_thesis_sources(queries=queries, timeout=25)


def _clean_collection(collection):
    # Apply exclusions to old snapshots and existing Streamlit sessions as well.
    return CollectionResult(
        tuple(job for job in collection.jobs if not is_excluded_job(job)),
        tuple(report for report in collection.reports if not is_excluded_employer(report.source)),
    )


def _search_controls(companies, settings):
    search_mode = st.selectbox(
        "Type de recherche", ("Emploi", "Thèse / doctorat"),
        index=int(settings.get("search_mode") == "Thèse / doctorat"), key="search_mode_v2",
    )
    geography = st.selectbox(
        "Périmètre", ("France / Suisse", "Worldwide"),
        index=int(settings.get("geography_mode") == "Worldwide"), key="search_geography_v2",
    )
    countries = st.multiselect(
        "Pays à interroger", ("France", "Switzerland"),
        default=list(settings.get("countries") or ("France", "Switzerland")),
        format_func=lambda c: "Suisse" if c == "Switzerland" else c,
        disabled=geography == "Worldwide", key="search_countries_v2",
    )
    families = st.multiselect(
        "Métiers recherchés", tuple(SEARCH_FAMILIES),
        default=list(settings.get("families", DEFAULT_FAMILIES)),
        disabled=search_mode != "Emploi", key="search_families_v2",
    )
    automatic = tuple(sorted(c.name for c in companies if c.automatic))
    with st.expander("Sources et requêtes"):
        employers = st.multiselect("Employeurs à interroger", automatic, default=automatic, disabled=search_mode != "Emploi")
        staffing = st.checkbox("Inclure Randstad et Michael Page", value=bool(settings.get("include_staffing", True)), disabled=search_mode != "Emploi")
        defaults = THESIS_QUERIES if search_mode != "Emploi" else family_queries(families)
        queries = st.text_area(
            "Une requête par ligne", "\n".join(defaults), height=140,
            key=f"queries_v2_{search_mode}_{'_'.join(families)}",
        )
    submitted = st.button("Lancer la recherche", type="primary", width="stretch")
    return submitted, {
        "search_mode": search_mode, "geography_mode": geography,
        "countries": () if geography == "Worldwide" else tuple(countries),
        "families": families, "include_staffing": staffing,
        "companies": tuple(employers),
        "queries": tuple(dict.fromkeys(q.strip() for q in queries.splitlines() if q.strip())),
    }


def main():
    st.set_page_config(page_title="Job Radar", page_icon="◉", layout="wide")
    _theme()
    try:
        store = RadarStore()
        if "job_radar_collection" not in st.session_state:
            saved = store.load_collection()
            if saved:
                st.session_state["job_radar_collection"], st.session_state["job_radar_settings"] = saved
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        store = None
        st.warning(f"Suivi local indisponible : {exc}")
    try:
        companies = tuple(c for c in load_career_companies() if not is_excluded_employer(c.name))
    except CareerSiteError as exc:
        st.error(str(exc))
        return
    settings = st.session_state.get("job_radar_settings") or {}
    if st.session_state.pop("show_new_results", False):
        st.session_state["radar_navigation"] = "Offres"
    title, navigation, search, preferences = st.columns([1.2, 3.5, 1.5, .65], vertical_alignment="center")
    title.markdown("## Job Radar")
    with navigation:
        page = st.segmented_control(
            "Navigation", ("Offres", "Sites carrière", "Candidatures"),
            default="Offres", key="radar_navigation", label_visibility="collapsed",
        ) or "Offres"
    with search:
        with st.popover("Nouvelle recherche", icon=":material/search:", width="stretch"):
            submitted, requested = _search_controls(companies, settings)
    with preferences:
        with st.popover(":material/settings:", help="Préférences", width="stretch"):
            render_preferences(store)
            with st.expander("Données locales"):
                st.caption("Les résultats et le suivi sont conservés sur cet ordinateur. Vider les résultats conserve vos candidatures et vos notes.")
                if st.button("Vider les résultats"):
                    _cached_collection.clear()
                    _cached_thesis_collection.clear()
                    if store:
                        store.set_state("latest", None)
                    st.session_state.pop("job_radar_collection", None)
                    st.session_state.pop("job_radar_settings", None)
                    st.rerun()
    if submitted:
        if not requested["queries"] or (requested["geography_mode"] != "Worldwide" and not requested["countries"]) or (requested["search_mode"] == "Emploi" and not requested["companies"]):
            st.error("Choisissez au moins une requête, un pays (ou Worldwide) et un employeur.")
        else:
            start = time.perf_counter()
            with st.status("Recherche en cours…", expanded=False):
                if requested["search_mode"] != "Emploi":
                    collection = _cached_thesis_collection(requested["queries"])
                else:
                    sources = (CAREER_SITES_SOURCE,) + (STAFFING_SOURCES if requested["include_staffing"] else ())
                    collection = _cached_collection(requested["companies"], requested["queries"], requested["countries"], sources)
            collection = _clean_collection(collection)
            settings = {
                **requested, "run_id": time.time_ns(),
                "searched_at": datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y %H:%M"),
                "elapsed": time.perf_counter() - start,
            }
            st.session_state["job_radar_collection"] = collection
            st.session_state["job_radar_settings"] = settings
            st.session_state["show_new_results"] = True
            if store:
                try:
                    store.save_collection(collection, settings)
                except (OSError, sqlite3.Error) as exc:
                    st.warning(f"Sauvegarde impossible : {exc}")
            st.rerun()
    collection = st.session_state.get("job_radar_collection")
    if isinstance(collection, CollectionResult):
        collection = _clean_collection(collection)
    reports = collection.reports if isinstance(collection, CollectionResult) else ()
    if page == "Sites carrière":
        render_portals(companies, reports)
    elif page == "Candidatures":
        render_pipeline(store)
    elif isinstance(collection, CollectionResult):
        render_opportunities(collection, settings, companies, store)
    else:
        st.info("Lancez une nouvelle recherche, ou ouvrez directement les sites carrière.")
    if isinstance(collection, CollectionResult):
        errors = sum(r.status == "error" for r in reports)
        error_note = f" · {errors} source(s) indisponible(s), détails dans Sites carrière" if errors else ""
        st.caption(f"Dernière recherche : {settings.get('searched_at', '')} · {settings.get('geography_mode', '')} · {settings.get('search_mode', 'Emploi')}{error_note}")


if __name__ == "__main__":
    main()
