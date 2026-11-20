from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from docs.job.job_radar import career_sites as sites


def company(kind="smartrecruiters"):
    return sites.CareerCompany(
        name="Example", careers_url="https://example.test/careers",
        priority="romandie", locations=("Lausanne, Switzerland",),
        sector="biotech", connector={"type": kind, "account": "Example", "board": "example"},
    )


def posting(identifier="1", country="ch", **kwargs):
    return {
        "id": identifier, "name": "Lab Automation Engineer",
        "location": {"city": "Lausanne", "country": country, "remote": True},
        "releasedDate": "2026-10-15T09:00:00Z",
        "typeOfEmployment": {"label": "Full-time"}, **kwargs,
    }


def test_smartrecruiters_parses_full_description_and_official_url():
    job = sites.parse_smartrecruiters({"content": [posting(
        jobAd={"sections": {
            "jobDescription": {"text": "<p>Develop <b>robotic workflows</b>.</p>"},
            "qualifications": {"text": "<p>Python &amp; liquid handling</p>"},
        }},
    ), posting("2", active=False), None, {}]}, company())[0]
    assert job.country == "Switzerland"
    assert "robotic workflows" in job.description
    assert "Python & liquid handling" in job.description
    assert job.url == "https://jobs.smartrecruiters.com/Example/1"
    assert job.direct_employer and job.remote
    assert job.employment_type == "Full-time"


def test_smartrecruiters_paginates_raw_rows_and_deduplicates_queries(monkeypatch):
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        if urlsplit(url).query:
            params = parse_qs(urlsplit(url).query)
            assert params["country"] == ["ch"]
            if params["offset"] == ["0"]:
                return {"totalFound": 3, "content": [posting(), posting("gone", active=False)]}
            assert params["offset"] == ["2"]
            return {"totalFound": 3, "content": [posting("2")]}
        return posting(url.rsplit("/", 1)[-1], jobAd={"sections": {
            "jobDescription": {"text": "<p>Full description</p>"},
        }})

    monkeypatch.setattr(sites, "_request_json", request)
    jobs = sites.fetch_career_company(
        company(), queries=("automation", "liquid handling"), countries=("CH",),
    )
    assert [job.source_id for job in jobs] == ["1", "2"]
    assert all(job.description == "Full description" for job in jobs)
    assert len([url for url in calls if not urlsplit(url).query]) == 2


def test_smartrecruiters_page_limit_and_detail_failure(monkeypatch):
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        if urlsplit(url).query:
            assert "country" not in parse_qs(urlsplit(url).query)
            return {"totalFound": 1000, "content": [posting()]}
        raise sites.CareerSiteError("HTTP 503")

    monkeypatch.setattr(sites, "_request_json", request)
    jobs = sites.fetch_career_company(company(), queries=("automation",), countries=(), max_pages=1)
    assert len(jobs) == 1
    assert len(calls) == 2


def test_smartrecruiters_filters_both_countries_and_handles_removed_details(monkeypatch):
    def request(url, **kwargs):
        if urlsplit(url).query:
            code = parse_qs(urlsplit(url).query)["country"][0]
            return {"totalFound": 1, "content": [posting(code, country=code)]}
        code = url.rsplit("/", 1)[-1]
        return posting(code, country=code, active=code != "fr")

    monkeypatch.setattr(sites, "_request_json", request)
    jobs = sites.fetch_career_company(company(), queries=("automation",))
    assert [job.country for job in jobs] == ["Switzerland"]


def test_ashby_ignores_unlisted_jobs_and_preserves_missing_country(monkeypatch):
    payload = {"jobs": [
        {"title": "Engineer", "jobUrl": "https://jobs.ashbyhq.com/example/1",
         "location": "Lausanne", "address": {"postalAddress": {"addressCountry": "CH"}},
         "descriptionHtml": "<p>Automate biology</p>", "workplaceType": "Remote"},
        {"title": "Engineer", "jobUrl": "https://jobs.ashbyhq.com/example/2",
         "location": "Unspecified"},
        {"title": "Hidden", "jobUrl": "https://jobs.ashbyhq.com/example/3", "isListed": False},
    ]}
    requests = []

    def request(url, **kwargs):
        requests.append(url)
        return payload

    monkeypatch.setattr(sites, "_request_json", request)
    jobs = sites.fetch_career_company(company("ashby"), queries=("automation", "robotics"))
    assert len(requests) == 1  # Complete feed, not one request per keyword.
    assert len(jobs) == 1
    assert jobs[0].description == "Automate biology"
    assert jobs[0].direct_employer and jobs[0].remote
    worldwide = sites.fetch_career_company(company("ashby"), queries=(), countries=())
    assert len(worldwide) == 2
    assert worldwide[1].country == ""


@pytest.mark.parametrize("parser,payload", [
    (sites.parse_smartrecruiters, {"error": "unavailable"}),
    (sites.parse_ashby, {"jobs": None}),
])
def test_invalid_feeds_raise_explicit_errors(parser, payload):
    with pytest.raises(sites.CareerSiteError):
        parser(payload, company())


@pytest.mark.parametrize("kind", ["ashby", "smartrecruiters"])
def test_missing_connector_configuration_raises(kind):
    with pytest.raises(sites.CareerSiteError):
        sites.fetch_career_company(replace(company(), connector={"type": kind}), queries=("automation",))


def test_registry_has_unique_names_and_complete_connector_configuration():
    companies = sites.load_career_companies()
    assert len({c.name.casefold() for c in companies}) == len(companies)
    required = {"workday": ("api_url", "board_url"), "workable": ("account",),
                "rss": ("url_template",), "smartrecruiters": ("account",),
                "ashby": ("board",), "greenhouse": ("board",), "link": ()}
    for c in companies:
        kind = c.connector["type"]
        assert kind in required
        assert all(c.connector.get(field) for field in required[kind])
    automatic = {c.name for c in companies if c.automatic}
    assert {"Eurofins", "SGS", "AbbVie", "SPT Labtech"} <= automatic


def test_adaptyv_is_not_in_registry():
    assert not any("adaptyv" in c.name.casefold() for c in sites.load_career_companies())
