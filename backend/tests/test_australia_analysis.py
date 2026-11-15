"""Integration checks use synthetic data only, never delivery results or NASA requests."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from ignis.studio_api import app

client = TestClient(app)

def test_readiness_is_independent_of_retired_pages():
    r = client.get('/api/analysis/readiness')
    assert r.status_code == 200
    assert r.json()['region'] == 'Australia'
    assert 'FIRMS_MAP_KEY' not in r.text

@pytest.fixture
def analysis_store(monkeypatch, tmp_path):
    pl = pytest.importorskip('polars')
    from embercal_engine import main, data
    cell = int((-35 + 90) / .1) * 3600 + int((148 + 180) / .1)
    monthly = []
    metadata = []
    for year in range(2015, 2025):
        for month in range(1, 13):
            value = (year - 2010) * month * 10.
            monthly.append(dict(cell=cell, year=year, month=month, modis_model_var=4., modis=value / 4, viirs_ref=value, modis_harmonized=value, modis_frp=2., snpp_frp=3.))
            metadata.append(dict(country='Australia', year=year, month=month, coverage=1., method='viirs_observed', viirs_src='S-NPP', has_modis=True, has_viirs=True))
    store = SimpleNamespace(
        dir=tmp_path,
        monthly=pl.DataFrame(monthly), meta=pl.DataFrame(metadata),
        cells=pl.DataFrame(dict(cell=[cell], country=['Australia'], lat=[-34.95], lon=[148.05], region_cell=[1])),
        forecasts=pl.DataFrame([dict(cell=cell, lead=l, target_year=2025, target_month=l, mean=100.) for l in (1, 2, 3)]),
        harm_report={'method_version': 'synthetic-test-only', 'variance_model': {}},
        fc_report={
            'target_series': 'modis_harmonized', 'issue_month': '2024-12', 'model_version': 'synthetic-test-only',
            'alpha_ncells': {'region': 25, 'country': 10000},
            'nb_alpha': {str(l): dict(cell=1., region=1., country=.1) for l in (1, 2, 3)},
            'skill': {str(l): dict(region={'crpss': -.1}, country={'crpss': .2}, by_country={'Australia': {'crpss': .2}}) for l in (1, 2, 3)},
            'feature_importance': {},
        },
        latest=(2024, 12), latest_complete=(2024, 12), manifest={},
        regions=[dict(id='Australia', name='Australia', bbox=[112, -44.5, 154.5, -9])],
    )
    monkeypatch.setattr(main, 'get_store', lambda: store)
    monkeypatch.setattr(data, '_store', store)
    main._wcache.clear()
    yield store
    main._wcache.clear()

def test_national_analysis_calendar_trends_and_csv(analysis_store):
    q = dict(aoi=dict(type='region', region='Australia'), start='2015-01', end='2024-12', baseline_start=2015, baseline_end=2024)
    response = client.post('/api/analysis/calendar', json=q)
    assert response.status_code == 200, response.text
    d = response.json()
    assert len(d['months']) == 120
    assert len(d['climatology']) == 12
    assert d['months'][0]['value'] == 50
    assert d['trend']['available']
    assert d['trend']['sen_slope_per_year'] > 0
    assert d['season']['peak_month'] == 12
    assert d['method_version'] == 'synthetic-test-only'
    csv = client.post('/api/analysis/export/calendar.csv', json=q)
    assert csv.status_code == 200
    assert len(csv.text.strip().splitlines()) == 121
    header=csv.text.splitlines()[0]
    assert all(field in header for field in ['uncertainty_status','nominal_interval_coverage','interval_validated_for_selection','baseline_status','source_policy'])

def test_small_australian_selection_uses_regional_skill(analysis_store):
    q = dict(aoi=dict(type='bbox', bbox=[148, -35, 148.1, -34.9]), baseline_start=2015, baseline_end=2024)
    r = client.post('/api/analysis/forecast', json=q)
    assert r.status_code == 200, r.text
    forecasts = r.json()['forecasts']
    assert len(forecasts) == 3
    assert all(f['source'] == 'climatology' and f['skill']['crpss'] == -.1 for f in forecasts)
    assert all(f['p10'] <= f['p50'] <= f['p90'] for f in forecasts)

@pytest.mark.parametrize('aoi', [dict(type='region', region='Sri_Lanka'), dict(type='bbox', bbox=[80, 7, 81, 8]), dict(type='point_radius', point=[80, 7], radius_km=20)])
def test_analysis_only_accepts_australia(analysis_store, aoi):
    assert client.post('/api/analysis/calendar', json={'aoi': aoi}).status_code == 422
    assert client.post('/api/analysis/hotspots', json={'aoi': aoi}).status_code == 422

def test_invalid_months_and_baselines_return_validation_error(analysis_store):
    base = dict(aoi=dict(type='region', region='Australia'))
    for extra in [dict(start='2020-13'), dict(start='2021-01', end='2020-01'), dict(baseline_start=2024, baseline_end=2015)]:
        assert client.post('/api/analysis/calendar', json=base | extra).status_code == 422

def test_hotspot_map_limit_preserves_full_query_dates_and_export(analysis_store):
    import datetime
    import polars as pl
    records = []
    for day, frp in [(1, 1.), (2, 100.)]:
        records.append(dict(latitude=-34.95, longitude=148.05, acq_date=datetime.date(2024, 1, day), acq_time='1200',
            satellite='S-NPP', sensor='VIIRS', frp=frp, conf_class='n', daynight='D', qc_pass=True, region='Australia',
            confidence_raw='n', scan=.4, track=.4, brightness=320., bright_bg=290., type=0, source='synthetic-test-only', cell=1))
    archive = analysis_store.dir / 'hotspots'
    archive.mkdir()
    pl.DataFrame(records).write_parquet(archive / 'fixture.parquet')
    analysis_store.hotspots_path = str(archive / '*.parquet').replace('\\', '/')
    q = dict(aoi=dict(type='bbox', bbox=[148, -35, 148.1, -34.9]), start_date='2024-01-01', end_date='2024-01-02', limit=1)
    r = client.post('/api/analysis/hotspots', json=q)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d['count'] == 2 and d['returned'] == 1 and d['truncated']
    assert d['date_range'] == ['2024-01-01', '2024-01-02']
    assert d['points'][0]['frp'] == 100.
    export = client.post('/api/analysis/export/hotspots.csv', json=q)
    assert export.status_code == 200
    assert len(export.text.strip().splitlines()) == 3

def test_missing_national_data_returns_actionable_503(monkeypatch, tmp_path):
    pytest.importorskip('polars')
    import embercal_engine.data as data
    import embercal_engine.main as main
    monkeypatch.setattr(data, '_store', None)
    monkeypatch.setattr(data.settings, 'data_dir', tmp_path)
    main._wcache.clear()
    r = client.get('/api/analysis/meta')
    assert r.status_code == 503
    assert 'Build-Australia.ps1' in r.json()['detail']


def test_default_series_is_calibrated_and_source_switch_series_is_rejected(analysis_store):
    query = dict(aoi=dict(type='region', region='Australia'), start='2015-01', end='2015-01')
    response = client.post('/api/analysis/calendar', json=query)
    assert response.status_code == 200
    assert response.json()['series'] == 'modis_harmonized'
    assert response.json()['months'][0]['value_kind'] == 'model_estimate'
    assert client.post('/api/analysis/calendar', json={**query, 'series':'harmonized'}).status_code == 422
    comparison = client.post('/api/analysis/compare', json=query)
    assert comparison.status_code == 200
    assert all('harmonized' not in row for row in comparison.json()['monthly'])


def test_forecast_rejects_outputs_trained_on_another_series(analysis_store):
    analysis_store.fc_report.pop('target_series')
    response = client.post('/api/analysis/forecast', json={'aoi':{'type':'region','region':'Australia'}})
    assert response.status_code == 200
    assert response.json()['available'] is False
    assert 'harmonized history' in response.json()['reason']


def test_unskillful_forecast_never_falls_back_to_model_with_short_baseline(analysis_store):
    response = client.post('/api/analysis/forecast', json={'aoi':{'type':'region','region':'Australia'},'baseline_start':2024,'baseline_end':2024})
    assert response.status_code == 200
    assert response.json()['available'] is False
    assert 'fewer than five' in response.json()['reason']
