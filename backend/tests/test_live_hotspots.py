from datetime import datetime,timedelta,timezone
import io
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from ignis.studio_api import app
from embercal_engine import live_hotspots as live

client=TestClient(app)
NOW=datetime(2026,11,15,12,tzinfo=timezone.utc)

@pytest.fixture(autouse=True)
def isolate(monkeypatch,tmp_path):
    live._cache.clear();live._failed.clear()
    monkeypatch.setattr(live,'CACHE_DIR',tmp_path)
    monkeypatch.setattr(live,'utcnow',lambda:NOW)
    monkeypatch.setattr(live,'load_dotenv',lambda *a,**k:None)
    monkeypatch.delenv('FIRMS_MAP_KEY',raising=False)
    yield
    live._cache.clear();live._failed.clear()

def frame():
    return pd.DataFrame([
        dict(latitude=-35,longitude=148,acq_date='2026-11-15',acq_time='0900',confidence='n',frp=12),
        dict(latitude=-35,longitude=148,acq_date='2026-11-15',acq_time='0900',confidence='n',frp=12),
        dict(latitude=-35,longitude=148,acq_date='2026-11-14',acq_time='0900',confidence='h',frp=13),
        dict(latitude=-35,longitude=148,acq_date='2026-11-15',acq_time='1100',confidence='l',frp=3),
        dict(latitude=-35,longitude=148,acq_date='2026-11-15',acq_time='1500',confidence='h',frp=7),
        dict(latitude=-35,longitude=148,acq_date='2026-11-15',acq_time='2560',confidence='h',frp=7),
        dict(latitude=-10,longitude=145,acq_date='2026-11-15',acq_time='0900',confidence='h',frp=4),
    ])

def fake_download(product,*args):
    if product!='VIIRS_NOAA20_NRT':raise RuntimeError('source unavailable')
    return frame()

def test_live_default_national_utc_filter_duplicates_and_partial(monkeypatch):
    monkeypatch.setattr(live,'_download',fake_download)
    r=client.post('/api/analysis/live/hotspots',json={})
    assert r.status_code==200,r.text
    d=r.json();assert d['count']==1 and d['today_count']==1
    assert d['partial'] and not d['stale']
    assert d['points'][0]['time']=='0900'
    assert d['points'][0]['age_hours']==3
    assert 'key' not in d

def test_live_ttl_includes_today_and_refreshes_after_expiry(monkeypatch):
    calls=[]
    monkeypatch.setattr(live,'_download',lambda p,*args:(calls.append(p),fake_download(p))[1])
    assert client.post('/api/analysis/live/hotspots',json={}).status_code==200
    assert client.post('/api/analysis/live/hotspots',json={}).status_code==200
    assert calls.count('VIIRS_NOAA20_NRT')==1
    monkeypatch.setattr(live,'utcnow',lambda:NOW+timedelta(minutes=6))
    client.post('/api/analysis/live/hotspots',json={})
    assert calls.count('VIIRS_NOAA20_NRT')==2

def test_live_failure_uses_explicit_stale_cache_not_archive(monkeypatch):
    monkeypatch.setattr(live,'_download',fake_download)
    client.post('/api/analysis/live/hotspots',json={})
    monkeypatch.setattr(live,'utcnow',lambda:NOW+timedelta(minutes=6))
    def fail(*a):raise RuntimeError('secret-key-in-provider-url')
    monkeypatch.setattr(live,'_download',fail)
    r=client.post('/api/analysis/live/hotspots',json={})
    assert r.json()['stale'] and r.json()['count']==1
    assert 'secret-key' not in r.text

def test_invalid_selection_never_retrieves_data(monkeypatch):
    def forbidden(*a):raise AssertionError('network should not be called')
    monkeypatch.setattr(live,'_download',forbidden)
    assert client.post('/api/analysis/live/hotspots',json={'aoi':{'type':'region','region':'Canada'}}).status_code==422
    assert client.post('/api/analysis/live/hotspots',json={'hours':999}).status_code==422

def test_live_aoi_export_and_map_cap(monkeypatch):
    monkeypatch.setattr(live,'_download',fake_download)
    q={'hours':72,'limit':1,'aoi':{'type':'bbox','bbox':[147,-36,149,-34]}}
    d=client.post('/api/analysis/live/hotspots',json=q).json()
    assert d['count']==2 and d['returned']==1 and d['truncated']
    response=client.post('/api/analysis/live/export.csv',json=q)
    export=pd.read_csv(io.StringIO(response.text))
    assert len(export)==2 and export.partial_feed.all()
    assert client.post('/api/analysis/live/hotspots',json={'aoi':{'type':'bbox','bbox':[130,-36,131,-35]}}).json()['count']==0

def test_no_sources_does_not_report_zero(monkeypatch):
    def fail(*a):raise RuntimeError('private-url')
    monkeypatch.setattr(live,'_download',fail)
    r=client.post('/api/analysis/live/hotspots',json={})
    assert r.status_code==503 and 'private-url' not in r.text


@pytest.mark.parametrize('empty_all,missing_frp_all', [(False, False), (False, True), (True, True)])
def test_empty_and_missing_optional_columns_do_not_warn(monkeypatch, empty_all, missing_frp_all):
    import warnings
    def download(product, *args):
        records = frame().iloc[:1].copy()
        if empty_all or product == 'VIIRS_SNPP_NRT':
            return records.iloc[:0]
        if missing_frp_all or product == 'VIIRS_NOAA21_NRT':
            records = records.drop(columns='frp')
        if product == 'MODIS_NRT':
            records['confidence'] = '80'
            records['satellite'] = 'T'
        return records
    monkeypatch.setattr(live, '_download', download)
    with warnings.catch_warnings():
        warnings.simplefilter('error', FutureWarning)
        response = client.post('/api/analysis/live/hotspots', json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['partial'] is False
    assert result['count'] == (0 if empty_all else 3)
    if not empty_all:
        points = {point['satellite']: point for point in result['points']}
        assert points['NOAA-21']['frp'] is None
        assert points['Terra']['time'] == '0900'
        assert points['Terra']['frp'] == (None if missing_frp_all else 12)
