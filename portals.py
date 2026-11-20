"""Direct career-site access, independent of the collection state."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .core import is_excluded_employer, normalize_text
from .thesis_sources import THESIS_PORTALS

STAFFING_PORTALS = (
    ("Indeed Suisse · Vaud", "https://ch.indeed.com/jobs?q=automation+pharma&l=Vaud"),
    ("Indeed Suisse · Genève", "https://ch.indeed.com/jobs?q=automation+pharma&l=Geneve"),
    ("Indeed France", "https://fr.indeed.com/jobs?q=automatisation+pharmaceutique&l=France"),
    ("Randstad", "https://www.randstad.fr/"),
    ("Michael Page", "https://www.michaelpage.fr/"),
    ("Manpower France · Pharma", "https://www.manpower.fr/offre-emploi/chimie-pharmacie-cosmetologie/s6"),
    ("Adecco France", "https://www.adecco.com/fr-fr/offres-emploi?jobTitle=automatisme"),
)


def portal_rows(companies, reports=(), query="automation"):
    by_name = {report.source: report for report in reports}
    rows = []
    for company in sorted(companies, key=lambda c: (c.automatic, c.name.casefold())):
        if is_excluded_employer(company.name):
            continue
        report = by_name.get(company.name)
        status = "À consulter manuellement" if not company.automatic else "Non interrogé"
        if report and company.automatic:
            status = {"ok": "Collecté", "error": "Indisponible", "disabled": "Non sélectionné"}.get(report.status, report.status)
        rows.append({
            "Employeur": company.name,
            "Domaine": company.sector,
            "Implantations": " · ".join(company.locations),
            "Collecte": status,
            "Site carrière": str(company.connector.get("board_url") or company.careers_url),
            "Recherche directe": company.search_url(query),
            "automatic": company.automatic,
        })
    return rows


def render_portals(companies, reports=()):
    kinds, search, mode, query = st.columns([1.2, 1.7, 1.6, 1.6])
    kind = kinds.selectbox("Type de sites", ("Employeurs", "Agences", "Thèses"), label_visibility="collapsed")
    needle = normalize_text(search.text_input("Filtrer les sites", placeholder="Employeur, domaine ou lieu…", label_visibility="collapsed"))
    scope = mode.selectbox("Accès aux sites", ("Tous les sites", "À consulter manuellement", "Collecte automatique", "Sources indisponibles"), disabled=kind != "Employeurs", label_visibility="collapsed")
    keyword = query.text_input("Requête sur les portails", "automation", help="Préremplit les recherches sur les portails qui acceptent une requête dans leur URL.", disabled=kind != "Employeurs", label_visibility="collapsed")
    if kind == "Employeurs":
        rows = portal_rows(companies, reports, keyword)
        rows = [row for row in rows if
                (scope != "À consulter manuellement" or not row["automatic"])
                and (scope != "Collecte automatique" or row["automatic"])
                and (scope != "Sources indisponibles" or row["Collecte"] == "Indisponible")]
        rows = [{k: v for k, v in row.items() if k != "automatic"} for row in rows]
    elif kind == "Agences":
        rows = [{"Employeur": name, "Site carrière": url} for name, url in STAFFING_PORTALS]
    else:
        rows = [{"Employeur": p.name, "Domaine": p.description, "Implantations": p.country, "Site carrière": p.url} for p in THESIS_PORTALS]
    rows = [row for row in rows if needle in normalize_text(" ".join(str(v) for k, v in row.items() if k not in ("Site carrière", "Recherche directe")))]
    st.caption(f"{len(rows)} sites · Tous les liens restent accessibles, même sans lancer de collecte ou en cas d'erreur.")
    if not rows:
        st.info("Aucun site dans cette sélection.")
    else:
        st.dataframe(
            pd.DataFrame(rows), hide_index=True, width="stretch", height=620, row_height=39,
            column_config={
                "Employeur": st.column_config.TextColumn(width="medium"),
                "Domaine": st.column_config.TextColumn(width="medium"),
                "Implantations": st.column_config.TextColumn(width="large"),
                "Collecte": st.column_config.TextColumn(width="medium"),
                "Site carrière": st.column_config.LinkColumn(display_text="Ouvrir ↗", width="small"),
                "Recherche directe": st.column_config.LinkColumn(display_text="Rechercher ↗", width="small"),
            },
        )
    failures = [r for r in reports if r.status == "error" and not is_excluded_employer(r.source)]
    if failures:
        with st.expander(f"Diagnostic de collecte · {len(failures)} sources indisponibles"):
            for report in failures:
                st.text(f"{report.source} : {report.message}")
                if report.url:
                    st.link_button(f"Consulter {report.source}", report.url)
