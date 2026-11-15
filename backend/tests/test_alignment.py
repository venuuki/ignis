import pandas as pd
import pytest

from ignis.align import prepare, aggregate


def fixture(sensor="modis", **changes):
    row = {
        "latitude": "-35",
        "longitude": "148",
        "acq_date": "2026-11-09",
        "acq_time": "0054",
        "satellite": "Aqua" if sensor == "modis" else "N20",
        "instrument": "MODIS" if sensor == "modis" else "VIIRS",
        "confidence": "80" if sensor == "modis" else "n",
        "frp": "12.3",
        "version": "6.1NRT" if sensor == "modis" else "2.0NRT",
    }
    row.update(changes)
    return pd.DataFrame([row])


def test_timestamp_and_confidence():
    result, _ = prepare(fixture(), "modis")
    assert result.iloc[0]["acquisition_datetime_utc"] == pd.Timestamp(
        "2026-11-09T00:54:00Z"
    )
    assert result.iloc[0]["confidence_raw"] == "80"


def test_identical_location_has_identical_cell():
    modis, _ = prepare(fixture(), "modis")
    viirs, _ = prepare(fixture("viirs"), "viirs")
    assert modis.iloc[0]["grid_cell_id"] == viirs.iloc[0]["grid_cell_id"]
    assert viirs.iloc[0]["confidence_raw"] == "n"


def test_duplicates_and_daily_aggregation():
    raw = fixture()
    modis, report = prepare(pd.concat([raw, raw]), "modis")
    viirs, _ = prepare(fixture("viirs"), "viirs")
    result = aggregate(modis, viirs, "2026-11-09", "2026-11-10")
    assert report["exact_duplicates_removed"] == 1
    assert result["modis_count"].tolist() == [1, 0]
    assert result["viirs_count"].tolist() == [1, 0]
    assert result["detection_status"].tolist() == ["both", "neither"]
    assert result["modis_coverage_status"].eq("unknown").all()
    assert pd.isna(result.iloc[1]["raw_count_ratio"])


@pytest.mark.parametrize("changes", [
    {"latitude": "95"},
    {"longitude": "invalid"},
    {"acq_time": "2460"},
    {"acq_date": "2026-02-30"},
    {"frp": "-1"},
])
def test_invalid_values(changes):
    with pytest.raises(ValueError):
        prepare(fixture(**changes), "modis")


def test_missing_column():
    with pytest.raises(ValueError, match="missing columns"):
        prepare(fixture().drop(columns="acq_time"), "modis")


def test_empty_datasets():
    modis, _ = prepare(fixture().iloc[:0], "modis")
    viirs, _ = prepare(fixture("viirs").iloc[:0], "viirs")
    result = aggregate(modis, viirs, "2026-11-09", "2026-11-10")
    assert result.empty
    assert "modis_count" in result.columns