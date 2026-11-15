import pandas as pd

from ignis.historical_audit import vegetation_monthly_summary


def make_rows():
    return pd.DataFrame([
        {
            "type_code": "0",
            "date_utc": "2024-01-01",
            "month": "2024-01",
            "grid_cell_id": "x1_y1",
            "sensor": "modis",
            "satellite": "Aqua",
            "version": "61.03",
            "daynight": "D",
        },
        {
            "type_code": "0",
            "date_utc": "2024-01-01",
            "month": "2024-01",
            "grid_cell_id": "x1_y1",
            "sensor": "modis",
            "satellite": "Aqua",
            "version": "61.03",
            "daynight": "D",
        },
        {
            "type_code": "2",
            "date_utc": "2024-01-02",
            "month": "2024-01",
            "grid_cell_id": "x2_y2",
            "sensor": "modis",
            "satellite": "Aqua",
            "version": "61.03",
            "daynight": "D",
        },
    ])


def test_type_filter_and_cell_day_count():
    raw = make_rows()
    summary = vegetation_monthly_summary(raw)

    assert len(summary) == 1
    assert summary.iloc[0]["records"] == 2
    assert summary.iloc[0]["active_cell_days"] == 1
    assert summary.iloc[0]["dates_with_detections"] == 1
    assert summary.iloc[0]["version"] == "61.03"
    assert len(raw) == 3  # Input was not modified.


def test_evaluation_year_and_platform_separation():
    first = make_rows().iloc[[0]].copy()
    second = first.copy()
    second["date_utc"] = "2025-01-01"
    second["month"] = "2025-01"
    second["satellite"] = "Terra"

    summary = vegetation_monthly_summary(
        pd.concat([first, second], ignore_index=True)
    )

    assert len(summary) == 2
    assert set(summary["dataset_role"]) == {
        "development", "final_evaluation"
    }
    evaluation = summary.loc[
        summary["dataset_role"].eq("final_evaluation")
    ]
    assert evaluation.iloc[0]["satellite"] == "Terra"