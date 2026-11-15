from datetime import datetime, timedelta, timezone
import hashlib
import json
import pandas as pd
from fastapi.testclient import TestClient
from ignis.studio_api import app, DATA
import ignis.studio_regions as region

client = TestClient(app)

def test_bundled_provenance_matches_original_hashes():
    manifest = json.loads((DATA / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == expected

def test_key_status_does_not_expose_key(monkeypatch):
    monkeypatch.setenv('FIRMS_MAP_KEY','test-only-private-value')
    response = client.get('/api/status')
    assert response.status_code == 200
    assert response.json()['configured'] is True
    assert 'test-only-private-value' not in response.text

def test_missing_key_is_explicit(monkeypatch):
    monkeypatch.setenv('FIRMS_MAP_KEY','')
    monkeypatch.setattr(region,'load_dotenv',lambda *a,**k: None)
    end = datetime.now(timezone.utc).date()-timedelta(days=1)
    r=client.get('/api/region/observations',params=dict(west=145,south=-38,east=151,north=-32,start=end,end=end))
    assert r.status_code == 503
    assert 'FIRMS_MAP_KEY' in r.json()['detail']

def test_region_uses_original_pipeline_and_correct_spatial_filter(monkeypatch):
    monkeypatch.setenv('FIRMS_MAP_KEY','test-only')
    end=datetime.now(timezone.utc).date()-timedelta(days=1)
    called=[]
    def fetch(source,bbox,start,finish,cache,key):
        called.append(source)
        return pd.DataFrame([{'latitude':-35,'longitude':148,'acq_date':str(end)},
                             {'latitude':0,'longitude':0,'acq_date':str(end)}])
    monkeypatch.setattr(region,'fetch_range',fetch)
    r=client.get('/api/region/observations',params=dict(west=145,south=-38,east=151,north=-32,start=end,end=end))
    assert r.status_code == 200
    data=r.json()
    assert data['counts'] == {'modis':1,'viirs':1}
    assert len(data['points']) == 2
    assert data['coverage'] == 'unknown'
    assert called == ['MODIS_NRT','VIIRS_NOAA20_NRT']

def test_invalid_region_does_not_query_firms(monkeypatch):
    def forbidden(*a,**k):
        raise AssertionError('Invalid region should fail before ingestion')
    monkeypatch.setattr(region,'fetch_range',forbidden)
    end=datetime.now(timezone.utc).date()-timedelta(days=1)
    for bounds in [(151,-38,145,-32),(100,-38,151,-32)]:
        r=client.get('/api/region/observations',params=dict(zip(('west','south','east','north'),bounds))|{'start':str(end),'end':str(end)})
        assert r.status_code == 422
