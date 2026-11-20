"""Compact master/detail interface for jobs and the local application journal."""
from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from hashlib import sha256
import math
import re
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from .core import (
    DEFAULT_THESIS_TERMS, SearchCriteria, canonical_country, evaluate_job,
    filter_and_rank, normalize_text, parse_published_at,
)
from .exports import results_to_csv, results_to_html
from .matching import CandidateProfile, SKILL_GROUPS, family_terms, rank_for_profile
from .storage import RadarStore, STATUSES, job_key

PAGE_SIZE = 25


def load_profile(store: RadarStore | None) -> CandidateProfile:
    saved = store.get_state("profile") if store else {}
    saved = saved if isinstance(saved, dict) else {}
    # Older journals may contain experience/permit fields; deliberately ignore them.
    return CandidateProfile(
        skills=tuple(s for s in saved.get("skills", SKILL_GROUPS) if s in SKILL_GROUPS),
        preferred_countries=tuple(saved.get("preferred_countries", ("France", "Switzerland"))),
    )


def render_preferences(store: RadarStore | None):
    profile = load_profile(store)
    with st.form("ranking_preferences"):
        skills = st.multiselect("Compétences à valoriser", tuple(SKILL_GROUPS), default=profile.skills)
        countries = st.text_input("Pays préférés", ", ".join(profile.preferred_countries))
        st.caption("Le classement rapproche les métiers et les compétences. Les années d'expérience demandées et le droit au travail ne sont pas des critères de tri. Les intitulés seniors sont masqués par défaut, avec une option pour les afficher.")
        if st.form_submit_button("Enregistrer les préférences", disabled=store is None):
            preferred = tuple(dict.fromkeys(
                canonical_country(c.strip()) or c.strip()
                for c in countries.split(",") if c.strip()
            ))
            store.set_state("profile", asdict(CandidateProfile(tuple(skills), preferred)))
            st.rerun()


@st.cache_data(ttl=3600, max_entries=12, show_spinner=False)
def _rank_opportunities(jobs, criteria, profile, hour):
    now = datetime.now(UTC)
    return rank_for_profile(filter_and_rank(jobs, criteria, now=now), profile, now=now)


def _date_label(job):
    published = parse_published_at(job.published_at)
    return published.strftime("%d/%m/%Y") if published else "—"


def _followup(job, store, records, prefix):
    if store is None:
        st.caption("Le suivi local est indisponible.")
        return
    identity = job_key(job)
    record = records.get(identity, {})
    with st.form(f"{prefix}_{identity}"):
        status = st.selectbox("Statut", STATUSES, index=STATUSES.index(record.get("status", "À étudier")))
        notes = st.text_area("Notes et prochaine action", record.get("notes", ""), height=120)
        followup = st.date_input(
            "Relance", value=date.fromisoformat(record["follow_up"]) if record.get("follow_up") else None,
        )
        if st.form_submit_button("Enregistrer", type="primary"):
            store.update(job, status, notes, followup.isoformat() if followup else "")
            st.rerun()


def _detail(result, store, records, *, prefix):
    job, fit = result.job, result.profile_fit
    st.markdown(f"### {job.title}")
    st.caption(f"{job.company} · {job.location}")
    st.link_button("Ouvrir l'annonce ↗", job.url, width="stretch", type="primary")
    description, tracking = st.tabs(("Annonce", "Mon suivi"))
    with description:
        if fit:
            with st.expander("Correspondances au profil"):
                st.caption(f"Proximité métier et compétences : {fit.fit}/100 · {fit.family}")
                st.write(" · ".join(fit.strengths) or "Peu de correspondances explicites dans le texte.")
                if fit.senior:
                    st.caption("Intitulé senior.")
                st.caption("Indice de correspondance, pas une probabilité de réponse.")
        st.caption(f"{_date_label(job)} · {job.source}")
        # Plain text prevents source markup from creating misleading controls.
        st.text(job.description or "La description n'est pas fournie. Consultez l'annonce sur le site carrière.")
    with tracking:
        _followup(job, store, records, prefix)


def _export_menu(results):
    with st.popover("Exporter", width="stretch"):
        st.download_button("CSV", results_to_csv(results), file_name="job_radar_selection.csv", mime="text/csv")
        st.download_button("Rapport HTML", results_to_html(results), file_name="job_radar_selection.html", mime="text/html")


def _master_detail(results, store, records, *, prefix="offers", journal=False):
    signature = sha256("|".join(job_key(r.job) for r in results).encode()).hexdigest()[:12]
    left, right = st.columns([1.35, 1], gap="large")
    with left:
        footer = st.columns([2.5, 1.2, 1])
        pages = max(1, math.ceil(len(results) / PAGE_SIZE))
        with footer[1]:
            page = st.selectbox(
                "Page", range(1, pages + 1),
                format_func=lambda n: f"{n} / {pages}",
                key=f"{prefix}_page_{signature}", label_visibility="collapsed",
            )
        start = (page - 1) * PAGE_SIZE
        current = results[start:start + PAGE_SIZE]
        footer[0].caption(f"{len(results)} offres · {start + 1}–{start + len(current)}")
        with footer[2]:
            _export_menu(results)
        rows = []
        for result in current:
            row = {
                "Poste": result.job.title,
                "Entreprise": result.job.company,
                "Lieu": result.job.location,
            }
            if journal:
                record = records[job_key(result.job)]
                row.update({"Statut": record["status"], "Relance": record["follow_up"] or "—"})
            else:
                if result.profile_fit:
                    row["Profil"] = result.profile_fit.fit
                row["Date"] = _date_label(result.job)
            rows.append(row)
        event = st.dataframe(
            pd.DataFrame(rows), hide_index=True, width="stretch", height=620,
            row_height=38, selection_mode="single-row", on_select="rerun",
            key=f"{prefix}_table_{signature}_{page}",
            column_config={
                "Poste": st.column_config.TextColumn(width=235),
                "Entreprise": st.column_config.TextColumn(width=105),
                "Lieu": st.column_config.TextColumn(width=135),
                "Profil": st.column_config.ProgressColumn(min_value=0, max_value=100, width=60, format="%d"),
                "Date": st.column_config.TextColumn(width=88),
            },
        )
        st.caption("Sélectionnez une ligne pour lire l'annonce et enregistrer son suivi.")
    selected_rows = event.selection.rows
    selected = selected_rows[0] if selected_rows and selected_rows[0] < len(current) else 0
    with right:
        with st.container(border=True, height=675):
            _detail(current[selected], store, records, prefix=prefix)


def render_opportunities(collection, settings, companies, store: RadarStore | None):
    thesis = settings.get("search_mode") == "Thèse / doctorat"
    toolbar = st.columns([2.8, 1.5, 1.4, .8])
    keyword = toolbar[0].text_input(
        "Rechercher dans les offres", placeholder="Poste, compétence, entreprise…",
        label_visibility="collapsed", key="offer_search",
    )
    with toolbar[3]:
        with st.popover("Filtres", width="stretch"):
            show_senior = st.checkbox("Inclure les postes seniors", value=False, disabled=thesis)
            hide_internships = st.checkbox("Masquer les stages / internships")
            hide_technicians = st.checkbox("Masquer les postes de technicien")
            age = st.selectbox("Publication", (None, 30, 60, 90, 180), format_func=lambda v: "Toutes les dates" if v is None else f"Moins de {v} jours")
            remote = st.checkbox("Télétravail annoncé")
            favorites = st.checkbox("Favoris uniquement")
            hide_processed = st.checkbox("Masquer les candidatures déjà traitées")
            st.caption("Aucune exclusion sur les années d'expérience ou l'autorisation de travail.")
    criteria = SearchCriteria(
        countries=tuple(settings["countries"]), max_age_days=age,
        automation_terms=DEFAULT_THESIS_TERMS if thesis else family_terms(settings.get("families", ())) or SearchCriteria().automation_terms,
        trusted_life_science_companies=tuple(c.name for c in companies),
        require_life_science_context=not thesis,
    )
    results = filter_and_rank(collection.jobs, criteria) if thesis else _rank_opportunities(
        collection.jobs, criteria, load_profile(store),
        datetime.now(UTC).strftime("%Y-%m-%d %H"),
    )
    countries = toolbar[1].multiselect(
        "Pays des offres", sorted({r.country or "Pays inconnu" for r in results}),
        placeholder="Tous les pays", label_visibility="collapsed",
    )
    sort = toolbar[2].selectbox("Trier les offres", ("Pertinence", "Plus récentes", "Entreprise"), label_visibility="collapsed")
    records = store.records() if store else {}
    filtered = []
    for result in results:
        job, fit = result.job, result.profile_fit
        status = records.get(job_key(job), {}).get("status", "À étudier")
        if not show_senior and fit and fit.senior:
            continue
        title = normalize_text(job.title)
        if hide_internships and re.search(r"\b(?:stages?|stagiaires?|interns?|internships?)\b", normalize_text(f"{job.title} {job.employment_type}")):
            continue
        if hide_technicians and re.search(r"\b(?:technicien(?:ne)?s?|technicians?)\b", title):
            continue
        if countries and (result.country or "Pays inconnu") not in countries:
            continue
        if remote and not job.remote:
            continue
        if favorites and status not in ("Favori", "À préparer"):
            continue
        if hide_processed and status in ("Candidature envoyée", "Entretien", "Refus", "Écartée"):
            continue
        text = normalize_text(f"{job.title} {job.company} {job.location} {job.description}")
        if not all(token in text for token in normalize_text(keyword).split()):
            continue
        filtered.append(result)
    if sort == "Plus récentes":
        filtered.sort(key=lambda r: parse_published_at(r.job.published_at) or datetime.min.replace(tzinfo=UTC), reverse=True)
    elif sort == "Entreprise":
        filtered.sort(key=lambda r: normalize_text(r.job.company))
    if not filtered:
        st.info("Aucune offre dans cette sélection. Modifiez la recherche ou les filtres ; les sites carrière restent accessibles dans l'onglet dédié.")
        return
    _master_detail(filtered, store, records)


def render_pipeline(store: RadarStore | None):
    if store is None:
        st.info("Le suivi local est indisponible.")
        return
    records = store.records()
    tracked = [r for r in records.values() if r["status"] != "À étudier" or r["notes"] or r["follow_up"]]
    if not tracked:
        st.info("Sélectionnez une offre puis « Mon suivi » pour enregistrer un favori, des notes ou une candidature.")
        return
    search, stage, due_column = st.columns([2, 2, 1])
    needle = normalize_text(search.text_input("Rechercher une candidature", placeholder="Entreprise ou poste", label_visibility="collapsed"))
    statuses = stage.multiselect("Étapes", STATUSES, label_visibility="collapsed", placeholder="Toutes les étapes")
    only_due = due_column.checkbox("À relancer")
    today = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    visible = [r for r in tracked if
               (not statuses or r["status"] in statuses)
               and needle in normalize_text(f"{r['job'].title} {r['job'].company}")
               and (not only_due or (r["follow_up"] and r["follow_up"] <= today and r["status"] not in ("Refus", "Écartée")))]
    if not visible:
        st.info("Aucune candidature dans cette sélection.")
        return
    results = [evaluate_job(r["job"], SearchCriteria(countries=(), max_age_days=None)) for r in visible]
    _master_detail(results, store, records, prefix="journal", journal=True)
