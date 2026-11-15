from datetime import datetime, timedelta, timezone
import hashlib
import json
import pandas as pd
from fastapi.testclient import TestClient
from ignis.studio_api import app, DATA

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


def test_retired_routes_are_not_registered():
    paths = client.get('/openapi.json').json()['paths']
    for path in ['/api/calendar', '/api/validation', '/api/meta', '/api/export/calendar.csv', '/api/region/observations']:
        assert path not in paths
    for path in ['/api/analysis/calendar', '/api/analysis/forecast', '/api/analysis/hotspots', '/api/observations', '/api/alignment']:
        assert path in paths
