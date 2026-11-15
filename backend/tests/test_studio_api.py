from fastapi.testclient import TestClient
import io
import pandas as pd

from ignis.studio_api import app

client = TestClient(app)


def test_calendar_uses_actual_development_data():
    response = client.get("/api/calendar")
    assert response.status_code == 200

    data = response.json()["rows"]
    assert len(data) == 72
    assert sum(row["viirs"] for row in data) == 481884
    assert sum(row["modis"] for row in data) == 97806
    assert max(row["year"] for row in data) == 2024


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


def test_export_and_holdout_protection():
    assert client.get(
        "/api/calendar?start_year=2025"
    ).status_code == 422

    response = client.get(
        "/api/export/calendar.csv"
        "?metric=modis&start_year=2024&end_year=2024"
    )

    table = pd.read_csv(io.StringIO(response.text))

    assert len(table) == 12
    assert table["value"].sum() == 6631
    assert table["coverage_status"].eq("unknown").all()


def test_validation_retains_the_failed_stress_test():
    data = client.get("/api/validation").json()

    row = next(
        row for row in data["stress"]
        if row["fold"] == "holdout_2019_2020"
        and row["method"] == "daynight_multipliers"
    )

    assert row["wape_percent"] > 70