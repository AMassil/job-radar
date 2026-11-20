"""Exercise the compact UI, legacy journals and manual portal access."""
from datetime import UTC, datetime
from dataclasses import replace

from streamlit.testing.v1 import AppTest

from docs.job.job_radar.core import Job
from docs.job.job_radar.sources import CollectionResult, SourceReport
from docs.job.job_radar.storage import RadarStore
from docs.job.job_radar.dashboard import load_profile
from docs.job.job_radar.career_sites import load_career_companies
from docs.job.job_radar.portals import portal_rows


def widget(elements, label):
    return next(element for element in elements if element.label == label)


def setup_store(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_RADAR_DB', str(tmp_path / 'radar.sqlite'))
    store = RadarStore()
    description = 'Develop laboratory automation workflows using Tecan FluentControl, Python and design of experiments for protein purification and antibody assay development. Work with scientists to validate liquid handling methods and ensure traceability. Minimum 3 years of experience required.'
    jobs = tuple(Job('fixture', str(i), 'Laboratory Automation Engineer', f'Biotech {i%8}', 'Paris, France', f'https://example.test/{i}', description=description, country='France', published_at=datetime.now(UTC).isoformat(), direct_employer=True) for i in range(45))
    jobs += (replace(jobs[0], company='Adaptyv', url='https://jobs.ashbyhq.com/adaptyv/old'),)
    jobs += (replace(jobs[0], title='Senior Laboratory Automation Engineer', url='https://example.test/senior'),)
    store.save_collection(CollectionResult(jobs, (SourceReport('fixture', 'ok', 46), SourceReport('Adaptyv', 'ok', 1))), {'countries': [], 'geography_mode': 'Worldwide', 'search_mode': 'Emploi', 'run_id': 1, 'searched_at': 'TEST', 'families': ['Automatisation de laboratoire']})
    # An old profile must not reinstate years/work-authorization assumptions.
    store.set_state('profile', {'experience_years': 0, 'authorized_countries': [], 'has_phd': False})
    return store


def test_compact_results_filters_pagination_and_persistent_followup(tmp_path, monkeypatch):
    store = setup_store(tmp_path, monkeypatch)
    app = AppTest.from_file('docs/job/job_radar_app.py', default_timeout=20).run()
    assert not app.exception
    assert not app.metric
    assert not app.get('vega_lite_chart')
    assert len(app.dataframe[0].value) == 25
    assert not app.number_input
    assert not any('Autorisation' in c.value for c in app.caption)
    assert 'Adaptyv' not in app.dataframe[0].value.to_string()
    assert 'Senior' not in app.dataframe[0].value.to_string()
    widget(app.selectbox, 'Page').select(2).run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 20
    widget(app.checkbox, 'Inclure les postes seniors').check().run()
    widget(app.selectbox, 'Page').select(2).run()
    assert len(app.dataframe[0].value) == 21
    assert 'Senior' in app.dataframe[0].value.to_string()
    widget(app.text_input, 'Rechercher dans les offres').set_value('no_such_job').run()
    assert not app.exception
    assert any('Aucune offre' in e.value for e in app.info)
    widget(app.text_input, 'Rechercher dans les offres').set_value('').run()
    widget(app.selectbox, 'Statut').select('Favori')
    widget(app.text_area, 'Notes et prochaine action').set_value('Projet Tecan')
    widget(app.button, 'Enregistrer').click().run()
    assert not app.exception
    assert any(r['status'] == 'Favori' and r['notes'] == 'Projet Tecan' for r in store.records().values())
    reopened = AppTest.from_file('docs/job/job_radar_app.py', default_timeout=20).run()
    reopened.session_state['radar_navigation'] = 'Candidatures'
    reopened.run()
    assert not reopened.exception
    assert reopened.dataframe[0].value['Statut'].tolist() == ['Favori']
    widget(reopened.button, 'Vider les résultats').click().run()
    assert not reopened.exception
    assert store.load_collection() is None
    assert any(r['status'] == 'Favori' for r in store.records().values())


def test_hide_internships_and_technicians_independently(tmp_path, monkeypatch):
    store = setup_store(tmp_path, monkeypatch)
    collection, settings = store.load_collection()
    base = collection.jobs[0]
    titles = [
        'Laboratory Automation Engineer',
        'Stage Laboratory Automation',
        'Laboratory Automation Internship',
        'Stagiaire Laboratory Automation',
        'Technicienne Laboratory Automation',
        'Laboratory Automation Technician',
    ]
    jobs = tuple(replace(base, title=title, url=f'https://example.test/filter/{i}') for i, title in enumerate(titles))
    jobs += (replace(base, employment_type='Internship', url='https://example.test/filter/type'),)
    store.save_collection(CollectionResult(jobs, ()), settings)
    app = AppTest.from_file('docs/job/job_radar_app.py', default_timeout=20).run()
    assert len(app.dataframe[0].value) == 7
    widget(app.checkbox, 'Masquer les stages / internships').check().run()
    assert len(app.dataframe[0].value) == 3
    widget(app.checkbox, 'Masquer les postes de technicien').check().run()
    assert app.dataframe[0].value['Poste'].tolist() == [titles[0]]
    widget(app.checkbox, 'Masquer les stages / internships').uncheck().run()
    assert len(app.dataframe[0].value) == 5
    assert not app.exception


def test_manual_sites_accessible_without_search_and_after_source_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_RADAR_DB', str(tmp_path / 'manual.sqlite'))
    app = AppTest.from_file('docs/job/job_radar_app.py', default_timeout=20).run()
    app.session_state['radar_navigation'] = 'Sites carrière'
    app.run()
    assert not app.exception
    frame = app.dataframe[0].value
    assert frame['Site carrière'].str.startswith('https://').all()
    assert 'Adaptyv' not in frame['Employeur'].tolist()
    widget(app.selectbox, 'Accès aux sites').select('À consulter manuellement').run()
    assert set(app.dataframe[0].value['Collecte']) == {'À consulter manuellement'}
    widget(app.selectbox, 'Type de sites').select('Agences').run()
    assert 'Indeed France' in app.dataframe[0].value['Employeur'].tolist()
    widget(app.selectbox, 'Type de sites').select('Thèses').run()
    assert 'EURAXESS' in app.dataframe[0].value['Employeur'].tolist()
    companies = load_career_companies()
    automatic = next(c for c in companies if c.automatic)
    rows = portal_rows(companies, (SourceReport(automatic.name, 'error', 0, 'Unavailable'),))
    row = next(r for r in rows if r['Employeur'] == automatic.name)
    assert row['Collecte'] == 'Indisponible'
    assert row['Site carrière'].startswith('https://')


def test_old_profile_is_migrated_without_eligibility_assumptions(tmp_path, monkeypatch):
    store = setup_store(tmp_path, monkeypatch)
    profile = load_profile(store)
    assert not hasattr(profile, 'experience_years')
    assert not hasattr(profile, 'authorized_countries')
    assert 'Python' in profile.skills
