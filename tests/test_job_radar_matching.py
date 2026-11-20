from dataclasses import replace
from datetime import UTC, datetime
import csv
import io

import pytest

from docs.job.job_radar.core import Job, SearchCriteria, canonical_country, canonical_url, filter_and_rank
from docs.job.job_radar.matching import CandidateProfile, assess_job, family_terms, rank_for_profile, is_senior_role
from docs.job.job_radar.storage import RadarStore, job_key
from docs.job.job_radar.sources import CollectionResult, SourceReport
from docs.job.job_radar.career_sites import CareerCompany, CareerSiteError, parse_greenhouse, fetch_career_company
from docs.job.job_radar.exports import results_to_csv, results_to_html

NOW = datetime(2026, 10, 27, tzinfo=UTC)
DESCRIPTION = 'Develop laboratory automation workflows using Tecan FluentControl, Python and design of experiments for protein purification and antibody assay development. Work with scientists to validate liquid handling methods and ensure traceability.'


def job(**changes):
    return replace(Job('fixture', '1', 'Junior Laboratory Automation Engineer', 'Biotech', 'Boston, United States', 'https://example.test/1', description=DESCRIPTION, country='United States', published_at='2026-10-25', direct_employer=True), **changes)


def test_profile_ranks_junior_above_senior_even_in_priority_location():
    junior = job()
    senior = job(title='Senior Laboratory Automation Engineer', location='Lausanne', country='Switzerland', url='https://example.test/2', description=DESCRIPTION + ' Minimum 8 years of experience required. PhD required.')
    results = rank_for_profile(filter_and_rank([senior, junior], SearchCriteria(countries=()), now=NOW), CandidateProfile(), now=NOW)
    assert results[0].job == junior
    assert results[0].profile_fit.band == 'Prioritaire'
    assert results[1].profile_fit.band != 'Prioritaire'
    assert results[1].profile_fit.senior
    assert results[1].profile_fit.fit == results[0].profile_fit.fit


def test_adjacent_roles_survive_eligibility_only_when_family_selected():
    adjacent = job(title='Bioprocess Scientist', description='Biotech protein purification and upstream process development.')
    assert not filter_and_rank([adjacent], SearchCriteria(countries=()), now=NOW)
    assert filter_and_rank([adjacent], SearchCriteria(countries=(), automation_terms=family_terms(['Bioproduction & procédés'])), now=NOW)


def test_missing_description_and_date_are_never_high_confidence():
    fit = assess_job(job(description='', published_at=''), 'United States', CandidateProfile(), now=NOW)
    assert fit.band != 'Prioritaire'
    assert fit.age_days is None
    assert any('Description' in x for x in fit.unknowns)
    assert not any('Autorisation' in x for x in fit.unknowns)


def test_corporate_boilerplate_does_not_make_recruiter_a_priority():
    fit = assess_job(job(title='Technical Recruiter'), 'United States', CandidateProfile(), now=NOW)
    assert fit.band != 'Prioritaire'
    assert any('support' in x for x in fit.concerns)


def test_industrial_automation_is_not_classified_as_lab_automation():
    fit = assess_job(job(title='Automation Engineer', description='PLC and SCADA for pharma manufacturing.'), 'United States', CandidateProfile(), now=NOW)
    assert fit.family == 'Automatisation industrielle'


@pytest.mark.parametrize('requirement', [
    'Minimum 3 years of experience required.',
    'Minimum 8 years of experience required.',
    'Must already be authorized to work. No visa sponsorship.',
    'PhD required. Fluent German required.',
])
def test_requirements_do_not_reduce_non_senior_opportunities(requirement):
    base = assess_job(job(), 'United States', CandidateProfile(), now=NOW)
    with_requirement = assess_job(job(description=DESCRIPTION + ' ' + requirement), 'United States', CandidateProfile(), now=NOW)
    assert with_requirement.fit == base.fit
    assert with_requirement.priority == base.priority
    assert not with_requirement.senior
    assert not with_requirement.concerns
    assert not with_requirement.unknowns


@pytest.mark.parametrize('title', [
    'Automation Project Manager', 'Responsable automatisation',
    'Automation Engineer II', '(Senior) Scientist', 'Scientist / Senior Scientist',
    'Junior / Senior Automation Engineer', 'Research Associate',
])
def test_regular_and_mixed_level_titles_are_not_senior_only(title):
    assert not is_senior_role(title)


@pytest.mark.parametrize('title', [
    'Senior Automation Engineer', 'Sr. Scientist', 'Principal Scientist',
    'Staff Engineer', 'Staff Bioprocess Engineer', 'Head of Automation', 'Director, Laboratory Automation',
])
def test_explicit_senior_titles(title):
    assert is_senior_role(title)


@pytest.mark.parametrize('changes', [
    {'company': 'Adaptyv'}, {'company': 'Adaptyv Bio'},
    {'url': 'https://jobs.ashbyhq.com/adaptyv/abc'},
    {'source': 'Sites carriere · Adaptyv'},
])
def test_excluded_employer_is_removed_from_old_results(changes):
    assert not filter_and_rank([job(**changes)], SearchCriteria(countries=()), now=NOW)


@pytest.mark.parametrize('text', ['Our company has 30 years of experience.', '5 years of experience preferred.', 'PhD preferred.', 'PhD not required.'])
def test_optional_requirements_and_company_history_are_not_hard_gaps(text):
    fit = assess_job(job(description=DESCRIPTION + ' ' + text), 'United States', CandidateProfile(), now=NOW)
    assert not fit.concerns


def test_internship_and_industrial_roles_remain_explorable():
    fit = assess_job(job(title='Automation Intern', description='PLC SCADA Siemens for pharma. Fluent German required. No visa sponsorship.'), 'Germany', CandidateProfile(), now=NOW)
    assert not fit.senior
    assert fit.family == 'Automatisation industrielle'
    assert not fit.concerns


@pytest.mark.parametrize('code,expected', [('US','United States'),('GB','United Kingdom'),('DE','Germany'),('BE','Belgium'),('CA','Canada')])
def test_worldwide_country_normalization(code, expected):
    assert canonical_country(code) == expected


def test_job_identity_retains_greenhouse_job_id_and_removes_tracking():
    assert canonical_url('https://example.test/careers?gh_jid=1&utm_source=test') == 'https://example.test/careers?gh_jid=1'
    assert job_key(job(url='https://example.test/careers?gh_jid=1')) != job_key(job(url='https://example.test/careers?gh_jid=2'))


def test_store_survives_restart_preserves_notes_and_keeps_absent_jobs(tmp_path):
    path = tmp_path / 'journal.sqlite'
    store = RadarStore(path)
    collection = CollectionResult((job(),), (SourceReport('test', 'ok', 1),))
    store.save_collection(collection, {'countries': [], 'run_id': 1})
    first = store.records()[job_key(job())]['first_seen']
    store.update(job(), 'Candidature envoyée', 'Mentionner projet Tecan', '2026-11-03')
    store.save_collection(collection, {'countries': [], 'run_id': 2})
    reopened = RadarStore(path)
    record = reopened.records()[job_key(job())]
    assert record['status'] == 'Candidature envoyée'
    assert record['notes'] == 'Mentionner projet Tecan'
    assert record['follow_up'] == '2026-11-03'
    assert record['first_seen'] == first
    assert reopened.load_collection()[0] == collection
    reopened.save_collection(CollectionResult((), ()), {})
    assert reopened.records()[job_key(job())]['status'] == 'Candidature envoyée'
    with pytest.raises(ValueError):
        reopened.update(job(), 'invented', '')


def test_greenhouse_parser_preserves_date_uncertainty_and_skips_prospects():
    company = CareerCompany('Example', 'https://example.test', 'standard', (), 'biotech', {'type':'greenhouse','board':'example'})
    entry = {'id':1, 'internal_job_id':2, 'title':'Lab automation', 'location':{'name':'Paris, France'}, 'absolute_url':'https://example.test/job', 'content':'&lt;p&gt;Python &amp; Tecan&lt;/p&gt;', 'updated_at':'2026-10-27'}
    jobs = parse_greenhouse({'jobs':[entry, {**entry, 'id':3, 'internal_job_id':None}]}, company)
    assert len(jobs) == 1
    assert jobs[0].description == 'Python & Tecan'
    assert jobs[0].country == 'France'
    assert jobs[0].published_at == ''
    with pytest.raises(CareerSiteError):
        parse_greenhouse({}, company)


def test_greenhouse_connector_filters_countries(monkeypatch):
    company = CareerCompany('Example', 'https://example.test', 'standard', (), 'biotech', {'type':'greenhouse','board':'example'})
    urls = []
    def request(url, **kwargs):
        urls.append(url)
        return {'jobs':[{'id':1, 'title':'Laboratory Automation', 'location':{'name':'Boston, United States'}, 'absolute_url':'https://example.test/job'}]}
    monkeypatch.setattr('docs.job.job_radar.career_sites._request_json', request)
    assert not fetch_career_company(company, queries=('automation',), countries=('France',))
    assert len(fetch_career_company(company, queries=('automation',), countries=())) == 1
    assert urls[0].endswith('/example/jobs?content=true')


def test_exports_include_explanations_and_escape_formula_and_html():
    ranked = rank_for_profile(filter_and_rank([job(title='=Junior laboratory automation <test>')], SearchCriteria(countries=()), now=NOW), CandidateProfile(), now=NOW)
    rows = list(csv.DictReader(io.StringIO(results_to_csv(ranked).decode('utf-8-sig'))))
    assert rows[0]['titre'].startswith("'=")
    assert rows[0]['adequation_cv']
    assert 'Python' in rows[0]['atouts_cv']
    assert '<test>' not in results_to_html(ranked)
