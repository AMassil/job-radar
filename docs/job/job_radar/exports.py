from __future__ import annotations

import csv
from datetime import UTC, datetime
from html import escape
from io import StringIO
from typing import Iterable

from .core import MatchResult, parse_published_at


CSV_COLUMNS = (
    "rang",
    "score",
    "titre",
    "entreprise",
    "lieu",
    "pays",
    "date_publication",
    "type_contrat",
    "source",
    "priorite",
    "mots_cles_automation",
    "mots_cles_life_sciences",
    "motifs",
    "url",
    "description",
    "adequation_cv",
    "priorite_candidature",
    "categorie",
    "famille",
    "atouts_cv",
    "points_a_verifier",
    "inconnues",
)


def _published_label(value: str) -> str:
    parsed = parse_published_at(value)
    return parsed.date().isoformat() if parsed else value


def result_rows(results: Iterable[MatchResult]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for rank, result in enumerate(results, start=1):
        if result.priority_location:
            priority = "Geneve / Vaud"
        elif result.romandie_location:
            priority = "Suisse romande"
        else:
            priority = ""
        rows.append(
            {
                "rang": rank,
                "score": result.score,
                "adequation_cv": result.profile_fit.fit if result.profile_fit else "",
                "priorite_candidature": result.profile_fit.priority if result.profile_fit else "",
                "categorie": result.profile_fit.band if result.profile_fit else "",
                "famille": result.profile_fit.family if result.profile_fit else "",
                "atouts_cv": " · ".join(result.profile_fit.strengths) if result.profile_fit else "",
                "points_a_verifier": " · ".join(result.profile_fit.concerns) if result.profile_fit else "",
                "inconnues": " · ".join(result.profile_fit.unknowns) if result.profile_fit else "",
                "titre": result.job.title,
                "entreprise": result.job.company,
                "lieu": result.job.location,
                "pays": result.country,
                "date_publication": _published_label(result.job.published_at),
                "type_contrat": result.job.employment_type,
                "source": result.job.source,
                "priorite": priority,
                "mots_cles_automation": ", ".join(result.automation_hits),
                "mots_cles_life_sciences": ", ".join(result.life_science_hits),
                "motifs": " · ".join(result.reasons),
                "url": result.job.url,
                "description": result.job.description,
            }
        )
    return rows


def results_to_csv(results: Iterable[MatchResult]) -> bytes:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    # Prevent spreadsheet formula execution from untrusted job descriptions/titles.
    writer.writerows({key: ("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)
                      for key, value in row.items()} for row in result_rows(results))
    return output.getvalue().encode("utf-8-sig")


def results_to_html(
    results: Iterable[MatchResult],
    *,
    generated_at: datetime | None = None,
) -> str:
    rows = result_rows(results)
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    body_rows = []
    for row in rows:
        url = escape(str(row["url"]), quote=True)
        title = escape(str(row["titre"]))
        description = escape(str(row["description"]))
        body_rows.append(
            "<article>"
            f"<div class=\"rank\">{row['rang']}</div>"
            "<div class=\"job\">"
            f"<h2><a href=\"{url}\">{title}</a></h2>"
            f"<p class=\"meta\"><strong>{escape(str(row['entreprise']))}</strong>"
            f" · {escape(str(row['lieu']))} · score {row['score']}</p>"
            f"<p class=\"signals\">{escape(str(row['motifs']))}</p>"
            f"<p>Adéquation CV : {row['adequation_cv']} · Priorité : {row['priorite_candidature']} · {escape(str(row['categorie']))}</p>"
            f"<p>Atouts : {escape(str(row['atouts_cv']))}</p>"
            f"<p>À vérifier : {escape(str(row['points_a_verifier']))} · {escape(str(row['inconnues']))}</p>"
            f"<p>{description}</p>"
            "</div>"
            "</article>"
        )
    content = "\n".join(body_rows) or '<p class="empty">Aucune offre retenue.</p>'
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Radar · veille emplois</title>
<style>
:root{{--ink:#17211d;--paper:#f4f6f3;--line:#cfd8d2;--green:#156b52;--coral:#b74f32}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 Aptos,Calibri,sans-serif}}
header{{padding:40px max(5vw,24px) 24px;border-bottom:3px solid var(--green);background:#fff}}
h1,h2{{font-family:Bahnschrift,'Arial Narrow',sans-serif;letter-spacing:0}}
h1{{margin:0 0 8px;font-size:32px}} header p{{margin:0;color:#53615a}}
main{{max-width:1100px;margin:auto;padding:24px max(3vw,16px) 64px}}
article{{display:grid;grid-template-columns:46px 1fr;gap:16px;padding:22px 0;border-bottom:1px solid var(--line)}}
.rank{{font:700 20px Bahnschrift,sans-serif;color:var(--coral)}}
h2{{margin:0 0 5px;font-size:20px}} a{{color:var(--green);text-decoration-thickness:1px;text-underline-offset:3px}}
.meta,.signals{{margin:4px 0;color:#53615a}} .signals{{font-size:13px;text-transform:uppercase}}
.job>p:last-child{{max-width:90ch}} .empty{{padding:32px 0}}
@media(max-width:600px){{article{{grid-template-columns:32px 1fr}}h1{{font-size:27px}}}}
</style>
</head>
<body>
<header><h1>Job Radar</h1><p>{len(rows)} offre(s) · export du {generated:%Y-%m-%d %H:%M} UTC</p></header>
<main>{content}</main>
</body>
</html>"""