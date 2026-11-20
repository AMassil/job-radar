from datetime import UTC, datetime

from docs.job.job_radar.career_sites import (
    CareerCompany,
    enrich_workday_job,
    parse_rss,
    parse_workable,
    parse_workday,
    workday_country_filter,
)
from docs.job.job_radar.core import (
    DEFAULT_THESIS_TERMS,
    Job,
    SearchCriteria,
    canonical_country,
    canonical_url,
    diversify_results,
    filter_and_rank,
)
from docs.job.job_radar.exports import results_to_csv, results_to_html
from docs.job.job_radar.sources import (
    CAREER_SITES_SOURCE,
    collect_jobs,
    parse_arbeitnow,
    parse_france_travail,
    parse_jobicy,
    parse_job_listing_html,
    parse_remotive,
    parse_serpapi,
)
from docs.job.job_radar.thesis_sources import parse_abg_theses, parse_doctorat_gouv


NOW = datetime(2026, 7, 22, tzinfo=UTC)


def _job(**overrides: object) -> Job:
    values: dict[str, object] = {
        "source": "test",
        "source_id": "1",
        "title": "Laboratory Automation Engineer",
        "company": "Example Biotech",
        "location": "Lausanne, Vaud, Switzerland",
        "country": "Switzerland",
        "url": "https://example.test/jobs/1",
        "description": "Develop robotic liquid handling workflows for biologics discovery.",
        "published_at": "2026-07-20T08:00:00Z",
    }
    values.update(overrides)
    return Job(**values)  # type: ignore[arg-type]


def test_priority_location_ranks_before_other_target_location() -> None:
    lyon = _job(
        source_id="2",
        location="Lyon, France",
        country="France",
        url="https://example.test/jobs/2",
    )

    results = filter_and_rank([lyon, _job()], now=NOW)

    assert [result.job.location for result in results] == [
        "Lausanne, Vaud, Switzerland",
        "Lyon, France",
    ]
    assert results[0].priority_location is True


def test_empty_country_selection_enables_worldwide_results() -> None:
    worldwide_job = _job(
        location="Boston, Massachusetts, United States",
        country="United States",
    )

    results = filter_and_rank(
        [worldwide_job],
        SearchCriteria(countries=()),
        now=NOW,
    )

    assert len(results) == 1
    assert results[0].country == "United States"


def test_staffing_html_parser_extracts_unique_jobs() -> None:
    html = """
    <li class="cards__item bg-variant-white"><h3><a
    href="/emploi/automaticien-fh_granville_123_01c/">Automaticien (F/H)</a></h3>
    <ul><li class="cards__meta-item"><span><svg></svg></span>Granville, Manche</li></ul>
    <p>Industrie pharmaceutique, robots et automates.</p></li>
    <a href="/emploi/automaticien-fh_granville_123_01c/">voir le détail</a>
    """

    jobs = parse_job_listing_html(
        html,
        source="Randstad",
        base_url="https://www.randstad.fr",
        link_pattern=r"^/emploi/[^/]+_[^/]+/$",
        country="France",
    )

    assert len(jobs) == 1
    assert jobs[0].title == "Automaticien (F/H)"
    assert jobs[0].location == "Granville, Manche"
    assert "pharmaceutique" in jobs[0].description


def test_requires_both_automation_and_life_science_context() -> None:
    software_only = _job(
        source_id="3",
        title="QA Automation Engineer",
        company="Retail Software SA",
        description="Automate browser tests for an ecommerce platform.",
        url="https://example.test/jobs/3",
    )
    biology_only = _job(
        source_id="4",
        title="Research Scientist",
        description="Cell therapy assay development in a biotech laboratory.",
        url="https://example.test/jobs/4",
    )
    industrial_diagnostics = _job(
        source_id="5",
        company="Industrial Services",
        title="Automaticien maintenance",
        description="Diagnostic de pannes sur automates et robots industriels.",
        url="https://example.test/jobs/5",
    )

    assert filter_and_rank(
        [software_only, biology_only, industrial_diagnostics],
        now=NOW,
    ) == []


def test_thesis_search_requires_doctoral_opportunity_marker() -> None:
    phd = _job(
        title="PhD candidate in laboratory automation",
        description="Doctoral research project for robotic biologics discovery.",
    )
    permanent_role = _job(
        source_id="permanent",
        title="Laboratory Automation Engineer",
        url="https://example.test/jobs/permanent",
    )
    criteria = SearchCriteria(required_terms=DEFAULT_THESIS_TERMS)

    results = filter_and_rank([permanent_role, phd], criteria, now=NOW)

    assert [result.job.title for result in results] == [phd.title]
    assert results[0].required_hits
    assert "these/doctorat" in results[0].reasons


def test_thesis_search_recognizes_french_cifre_wording() -> None:
    cifre = _job(
        title="Thèse CIFRE en automatisation de laboratoire",
        description="Doctorat mené dans une entreprise biopharmaceutique.",
    )

    results = filter_and_rank(
        [cifre],
        SearchCriteria(required_terms=DEFAULT_THESIS_TERMS),
        now=NOW,
    )

    assert len(results) == 1
    assert "cifre" in results[0].required_hits


def test_doctorat_gouv_parser_reads_thesis_api_payload() -> None:
    jobs = parse_doctorat_gouv(
        {
            "content": [
                {
                    "id": 42,
                    "typeProposition": "proposition",
                    "theseTitre": "Robotisation de la bioproduction",
                    "etablissementLibelle": "Université Exemple",
                    "uniteRechercheVille": "Lyon",
                    "resume": "<p>Automatisation de cultures cellulaires.</p>",
                    "dateMiseEnLigne": "2026-07-20T00:00:00",
                    "domainesImpactListe": None,
                },
                {"id": 43, "typeProposition": "these", "theseTitre": "Thèse soutenue"},
            ]
        }
    )

    assert len(jobs) == 1
    assert jobs[0].source_id == "42"
    assert jobs[0].title == "Robotisation de la bioproduction"
    assert jobs[0].company == "Université Exemple"
    assert jobs[0].location == "Lyon"
    assert jobs[0].description == "Automatisation de cultures cellulaires."
    assert jobs[0].url == "https://app.doctorat.gouv.fr/proposition?id=42"


def test_abg_parser_keeps_only_thesis_cards() -> None:
    html = """
    <h2><a href="/fr/candidatOffres/show/id_offre/10/job/stage">Stage robotique</a></h2>
    <div class="societe">Example SA <span class="type">Stage</span></div>
    <div class="adresse">Paris, France</div>
    <div class="text">Une thèse est proposée ensuite.</div>
    <h2><a href="/fr/candidatOffres/show/id_offre/11/job/these">Thèse robotique biotech</a></h2>
    <div class="societe">Institut Exemple <span class="type">Thèse</span></div>
    <div class="adresse">Lausanne, Suisse</div>
    <div class="text">Automatisation de laboratoire biopharmaceutique.</div>
    """

    jobs = parse_abg_theses(html)

    assert len(jobs) == 1
    assert jobs[0].source_id == "11"
    assert jobs[0].company == "Institut Exemple"
    assert jobs[0].location == "Lausanne, Suisse"
    assert jobs[0].country == "Switzerland"


def test_accented_geneva_location_is_recognized() -> None:
    result = filter_and_rank(
        [_job(location="Genève, Suisse", country="")],
        now=NOW,
    )[0]

    assert result.country == "Switzerland"
    assert result.priority_location is True


def test_romandie_and_direct_career_jobs_receive_priority() -> None:
    criteria = SearchCriteria(
        trusted_life_science_companies=("UCB", "Roche", "Example Biotech"),
        priority_companies=("UCB",),
    )
    bulle = _job(
        company="UCB",
        location="Bulle, Fribourg, Switzerland",
        description="",
        direct_employer=True,
        url="https://careers.example/bulle",
    )
    basel = _job(
        company="Roche",
        location="Basel, Switzerland",
        description="",
        direct_employer=True,
        url="https://careers.example/basel",
    )
    lyon = _job(
        company="Example Biotech",
        location="Lyon, France",
        description="",
        url="https://board.example/lyon",
    )

    results = filter_and_rank([lyon, basel, bulle], criteria, now=NOW)

    assert [result.job.location for result in results] == [
        "Bulle, Fribourg, Switzerland",
        "Basel, Switzerland",
        "Lyon, France",
    ]
    assert results[0].romandie_location is True
    assert "site carriere officiel" in results[0].reasons


def test_old_jobs_are_excluded() -> None:
    old_job = _job(published_at="2026-01-01")

    assert filter_and_rank([old_job], SearchCriteria(max_age_days=60), now=NOW) == []


def test_duplicate_urls_merge_sources_and_remove_tracking() -> None:
    first = _job(source="source-a", url="https://example.test/jobs/1?utm_source=a")
    duplicate = _job(source="source-b", url="https://example.test/jobs/1?ref=board")

    results = filter_and_rank([first, duplicate], now=NOW)

    assert len(results) == 1
    assert results[0].job.source == "source-a + source-b"
    assert canonical_url(first.url) == "https://example.test/jobs/1"


def test_public_source_parsers_normalize_to_job_model() -> None:
    arbeitnow = parse_arbeitnow(
        {
            "data": [
                {
                    "slug": "automation-1",
                    "company_name": "Bio SA",
                    "title": "Automation Engineer",
                    "description": "<p>Biotech robotics</p>",
                    "remote": False,
                    "url": "https://example.test/a",
                    "tags": ["GMP"],
                    "job_types": ["full-time"],
                    "location": "Geneva, Switzerland",
                    "created_at": 1784707200,
                }
            ]
        }
    )[0]
    remotive = parse_remotive(
        {
            "jobs": [
                {
                    "id": 2,
                    "company_name": "Bio SA",
                    "title": "Robotics Scientist",
                    "description": "<b>Drug discovery</b>",
                    "url": "https://example.test/b",
                    "candidate_required_location": "France",
                    "publication_date": "2026-07-20T00:00:00Z",
                    "job_type": "full_time",
                    "tags": ["Laboratory"],
                }
            ]
        }
    )[0]
    jobicy = parse_jobicy(
        {
            "jobs": [
                {
                    "id": 3,
                    "jobTitle": "Lab Automation Specialist",
                    "companyName": "Bio SA",
                    "jobGeo": "Lausanne, Switzerland",
                    "url": "https://example.test/c",
                    "jobDescription": "<p>Biopharma</p>",
                    "pubDate": "2026-07-20",
                    "jobType": ["full-time"],
                    "jobIndustry": ["biotechnology"],
                }
            ]
        }
    )[0]

    assert arbeitnow.description == "Biotech robotics"
    assert arbeitnow.country == "Switzerland"
    assert remotive.remote is True
    assert remotive.country == "France"
    assert jobicy.location == "Lausanne, Switzerland"
    assert jobicy.employment_type == "full-time"


def test_official_source_parsers_choose_application_urls() -> None:
    france_travail = parse_france_travail(
        {
            "resultats": [
                {
                    "id": "123ABC",
                    "intitule": "Ingenieur automatisme pharma",
                    "description": "Qualification GMP",
                    "dateCreation": "2026-07-20T10:00:00Z",
                    "typeContratLibelle": "CDI",
                    "entreprise": {"nom": "Pharma France"},
                    "lieuTravail": {"libelle": "69 - Lyon"},
                    "origineOffre": {"urlOrigine": "https://example.test/apply"},
                }
            ]
        }
    )[0]
    serpapi = parse_serpapi(
        {
            "jobs_results": [
                {
                    "job_id": "google-1",
                    "title": "Laboratory Automation Engineer",
                    "company_name": "Bio Suisse",
                    "location": "Nyon",
                    "description": "Biotechnology",
                    "via": "Company site",
                    "detected_extensions": {"schedule_type": "Full-time"},
                    "apply_options": [{"title": "Company", "link": "https://example.test/job"}],
                }
            ]
        },
        fallback_country="Switzerland",
    )[0]

    assert france_travail.url == "https://example.test/apply"
    assert france_travail.country == "France"
    assert serpapi.url == "https://example.test/job"
    assert serpapi.country == "Switzerland"


def test_manual_company_builds_prefilled_search_url() -> None:
    manual = CareerCompany(
        name="Waters",
        careers_url="https://careers.example/waters",
        priority="standard",
        locations=("France",),
        sector="analytical instruments",
        connector={
            "type": "link",
            "search_url_template": "https://jobs.example/search?ss=1&searchKeyword={query}",
        },
    )
    bare = CareerCompany(
        name="AliveDx",
        careers_url="https://careers.example/alivedx",
        priority="romandie",
        locations=("Eysins, Vaud, Switzerland",),
        sector="diagnostics",
        connector={"type": "link"},
    )
    assert (
        manual.search_url("laboratory automation")
        == "https://jobs.example/search?ss=1&searchKeyword=laboratory%20automation"
    )
    assert manual.search_url("") == ""
    assert bare.search_url("automation") == ""


def test_direct_career_parsers_mark_employer_source() -> None:
    workday_company = CareerCompany(
        name="Ferring Pharmaceuticals",
        careers_url="https://careers.example/ferring",
        priority="romandie",
        locations=("Saint-Prex, Vaud, Switzerland",),
        sector="biopharma",
        connector={
            "type": "workday",
            "board_url": "https://ferring.example/jobs",
        },
    )
    workable_company = CareerCompany(
        name="Debiopharm",
        careers_url="https://careers.example/debiopharm",
        priority="romandie",
        locations=("Lausanne, Vaud, Switzerland",),
        sector="biopharma",
        connector={"type": "workable", "account": "debiopharm"},
    )
    workday = parse_workday(
        {
            "jobPostings": [
                {
                    "title": "Laboratory Automation Engineer",
                    "externalPath": "/job/123",
                    "locationsText": "Switzerland, Saint-Prex",
                    "postedOn": "Posted 2 Days Ago",
                    "bulletFields": ["Full time"],
                }
            ]
        },
        workday_company,
        now=NOW,
    )[0]
    workable = parse_workable(
        {
            "results": [
                {
                    "id": 7,
                    "shortcode": "ABC123",
                    "title": "Robotics Scientist",
                    "location": {
                        "city": "Lausanne",
                        "region": "Vaud",
                        "country": "Switzerland",
                        "countryCode": "CH",
                    },
                    "published": "2026-07-20T00:00:00Z",
                    "department": ["R&D"],
                    "type": "full",
                }
            ]
        },
        workable_company,
    )[0]

    assert workday.direct_employer is True
    assert workday.url == "https://ferring.example/jobs/job/123"
    assert workday.published_at.startswith("2026-07-20")
    assert workable.source == "Carriere · Debiopharm"
    assert workable.location == "Lausanne, Vaud, Switzerland"


def test_workday_country_facets_and_detail_enrichment() -> None:
    nested_facets = {
        "facets": [
            {
                "facetParameter": "locationMainGroup",
                "values": [
                    {
                        "facetParameter": "locationHierarchy2",
                        "descriptor": "Country",
                        "values": [
                            {"descriptor": "France", "id": "fr-id"},
                            {"descriptor": "Switzerland", "id": "ch-id"},
                            {"descriptor": "Germany", "id": "de-id"},
                        ],
                    }
                ],
            }
        ]
    }
    company = CareerCompany(
        name="BMS",
        careers_url="https://careers.example/bms",
        priority="romandie",
        locations=("Boudry, Neuchatel, Switzerland",),
        sector="biopharma",
        connector={"type": "workday", "board_url": "https://workday.example"},
    )
    listing = parse_workday(
        {
            "jobPostings": [
                {
                    "title": "Engineer",
                    "externalPath": "/job/123",
                    "locationsText": "Boudry",
                }
            ]
        },
        company,
    )[0]
    enriched = enrich_workday_job(
        listing,
        {
            "jobPostingInfo": {
                "title": "Process Engineer",
                "location": "Boudry, Neuchatel, Switzerland",
                "country": "Switzerland",
                "externalUrl": "https://workday.example/job/123",
                "jobDescription": "<p>Own GMP laboratory automation systems.</p>",
                "startDate": "2026-07-21",
                "postedOn": "Posted Today",
                "timeType": "Full time",
                "jobReqId": "R123",
            }
        },
    )

    assert workday_country_filter(nested_facets) == (
        "locationHierarchy2",
        ("fr-id", "ch-id"),
    )
    assert workday_country_filter(nested_facets, ()) is None
    assert enriched.description == "Own GMP laboratory automation systems."
    assert enriched.country == "Switzerland"
    assert enriched.published_at == "2026-07-21"
    assert enriched.employment_type == "Full time"

    subsidiary = enrich_workday_job(
        listing,
        {
            "jobPostingInfo": {
                "location": "Nyon, Vaud, Switzerland",
                "jobDescription": "Laboratory automation for diagnostics.",
            },
            "hiringOrganization": {"name": "Beckman Coulter International SA"},
        },
        use_hiring_organization=True,
    )
    assert subsidiary.company == "Beckman Coulter International SA"

    worldwide = enrich_workday_job(
        listing,
        {
            "jobPostingInfo": {
                "location": "Boston, Massachusetts, United States",
                "country": {"descriptor": "United States", "id": "us-id"},
            }
        },
    )
    assert worldwide.country == "United States"


def test_workday_location_facet_matches_country_codes_as_tokens() -> None:
    location_facets = {
        "facets": [
            {
                "facetParameter": "locationHierarchy2",
                "descriptor": "Country",
                "values": [{"descriptor": "Ireland", "id": "ie-id"}],
            },
            {
                "facetParameter": "locations",
                "descriptor": "Locations",
                "values": [
                    {"descriptor": "Boudry - CH", "id": "ch-id"},
                    {"descriptor": "ESP Huesca - Fraga", "id": "es-id"},
                    {"descriptor": "USA - California - San Francisco", "id": "us-id"},
                ],
            }
        ]
    }

    assert workday_country_filter(location_facets) == ("locations", ("ch-id",))
    assert canonical_country("", "Boudry - CH") == "Switzerland"


def test_standalone_romandie_cities_are_prioritized() -> None:
    visp = filter_and_rank([_job(location="CH - Visp")], now=NOW)[0]
    saint_prex = filter_and_rank([_job(location="Saint-Prex - CH")], now=NOW)[0]

    assert visp.romandie_location is True
    assert saint_prex.priority_location is True


def test_diversified_order_keeps_all_jobs_and_limits_company_runs() -> None:
    jobs = [
        _job(company="Lonza", source_id=str(index), url=f"https://example.test/l/{index}")
        for index in range(5)
    ] + [
        _job(company="Roche", source_id=str(index), url=f"https://example.test/r/{index}")
        for index in range(2)
    ]
    ranked = filter_and_rank(jobs, now=NOW)

    diversified = diversify_results(ranked, batch_size=2)

    assert len(diversified) == len(ranked)
    assert [result.job.company for result in diversified] == [
        "Lonza",
        "Lonza",
        "Roche",
        "Roche",
        "Lonza",
        "Lonza",
        "Lonza",
    ]


def test_rss_parser_uses_only_location_present_in_feed() -> None:
    company = CareerCompany(
        name="OM Pharma",
        careers_url="https://careers.example/om",
        priority="romandie",
        locations=("Meyrin, Geneva, Switzerland",),
        sector="biopharma",
        connector={"type": "rss"},
    )
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel><item>
      <title>Automation Engineer</title>
      <link>https://careers.example/om/123</link>
      <description><![CDATA[<p>GMP manufacturing automation</p>]]></description>
      <location>Meyrin, Geneva, Switzerland</location>
      <pubDate>Mon, 20 Jul 2026 10:00:00 GMT</pubDate>
      <guid>123</guid>
    </item></channel></rss>"""

    job = parse_rss(rss, company)[0]

    assert job.location == "Meyrin, Geneva, Switzerland"
    assert job.description == "GMP manufacturing automation"
    assert job.direct_employer is True


def test_rss_parser_ignores_no_jobs_sentinel() -> None:
    company = CareerCompany(
        name="OM Pharma",
        careers_url="https://careers.example/om",
        priority="romandie",
        locations=("Meyrin, Geneva, Switzerland",),
        sector="biopharma",
        connector={"type": "rss"},
    )
    rss = """<rss><channel><item>
      <title>No jobs currently available - Check out our other opportunities.</title>
      <link>https://careers.example/om</link><guid>0</guid>
    </item></channel></rss>"""

    assert parse_rss(rss, company) == []


def test_collect_jobs_defaults_to_official_career_registry() -> None:
    manual_company = CareerCompany(
        name="Romandie Biotech",
        careers_url="https://careers.example/romandie",
        priority="romandie",
        locations=("Lausanne, Vaud, Switzerland",),
        sector="biotech",
        connector={"type": "link"},
    )

    result = collect_jobs(
        career_companies=(manual_company,),
        enabled_companies=("Autre employeur",),
    )

    assert result.jobs == ()
    assert len(result.reports) == 1
    assert result.reports[0].source == "Romandie Biotech"
    assert result.reports[0].status == "manual_only"
    assert result.reports[0].url == "https://careers.example/romandie"
    assert CAREER_SITES_SOURCE == "Sites carriere officiels"


def test_ranked_results_export_to_csv_and_safe_html() -> None:
    ranked = filter_and_rank(
        [_job(title="Automation <Engineer>", url="https://example.test/job?a=1&b=2")],
        now=NOW,
    )

    csv_payload = results_to_csv(ranked).decode("utf-8-sig")
    html_payload = results_to_html(ranked, generated_at=NOW)

    assert "rang,score,titre,entreprise" in csv_payload
    assert "Automation <Engineer>" in csv_payload
    assert "Automation &lt;Engineer&gt;" in html_payload
    assert "https://example.test/job?a=1&amp;b=2" in html_payload