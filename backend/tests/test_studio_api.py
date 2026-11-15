from fastapi.testclient import TestClient
import io
import pandas as pd

from ignis.studio_api import app

client = TestClient(app)




def test_recent_data_and_confidence_filter():
    data = client.get("/api/observations").json()
    assert data["modis"] == 43
    assert data["viirs"] == 495

    filtered = client.get(
        "/api/observations?confidence=nominal_high"
    ).json()

    assert filtered["modis"] == 43
    assert filtered["viirs"] == 312


def test_alignment_controls_use_real_processing():
    assert client.get(
        "/api/alignment?grid_km=10"
    ).json()["cells"] == 76

    assert client.get(
        "/api/alignment?grid_km=20&confidence=nominal_high"
    ).json()["cells"] == 59

    assert client.get(
        "/api/alignment?grid_km=3"
    ).status_code == 422




