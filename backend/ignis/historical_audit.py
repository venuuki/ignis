"""Audit historical FIRMS records without changing raw input files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from .align import prepare


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def vegetation_monthly_summary(clean: pd.DataFrame) -> pd.DataFrame:
    """Summarize presumed vegetation-fire detections, without calibration."""
    vegetation = clean.loc[clean["type_code"].eq("0")].copy()

    if not vegetation["daynight"].isin(["D", "N"]).all():
        raise ValueError("Unexpected daynight value")

    vegetation["dataset_role"] = "development"
    vegetation.loc[
        vegetation["date_utc"].ge("2025-01-01"),
        "dataset_role",
    ] = "final_evaluation"

    # A cell-day can contain multiple hotspot records.
    vegetation["cell_day"] = (
        vegetation["grid_cell_id"] + "|" + vegetation["date_utc"]
    )

    summary = (
        vegetation.groupby(
            ["dataset_role", "month", "satellite", "version", "daynight"],
            dropna=False,
        )
        .agg(
            records=("sensor", "size"),
            dates_with_detections=("date_utc", "nunique"),
            active_cell_days=("cell_day", "nunique"),
        )
        .reset_index()
    )
    summary["coverage_status"] = "unknown"
    return summary

def audit(
    path: Path,
    sensor: str,
    start: str,
    end: str,
    output: Path,
) -> dict:
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)

    if raw.empty:
        raise ValueError(f"{sensor}: archive contains no records")
    if "type" not in raw.columns:
        raise ValueError(f"{sensor}: missing archive type field")

    blank_counts = {
        column: int(raw[column].str.strip().eq("").sum())
        for column in raw.columns
    }

    # Reuse validation, exact-duplicate removal, UTC timestamp parsing,
    # and the existing projected grid assignment.
    clean, validation = prepare(raw, sensor, grid_km=10)

    within_dates = clean["date_utc"].between(start, end)
    within_bbox = (
        clean["longitude"].between(145, 151)
        & clean["latitude"].between(-38, -32)
    )

    # Fail visibly rather than silently exclude unexpected records.
    if not within_dates.all():
        raise ValueError(
            f"{sensor}: {int((~within_dates).sum())} records "
            "outside requested dates"
        )
    if not within_bbox.all():
        raise ValueError(
            f"{sensor}: {int((~within_bbox).sum())} records "
            "outside requested study bounds"
        )

    # Preserve raw type while providing a consistent grouping label.
    clean["type_code"] = clean["type"].str.strip().replace(
        "", "missing"
    )
    clean["month"] = (
        clean["acquisition_datetime_utc"].dt.strftime("%Y-%m")
    )
    
    timing = clean.loc[clean["type_code"].eq("0")].copy()
    timing["utc_hour"] = timing["acquisition_datetime_utc"].dt.hour

    # Approximate local solar time, not civil time or a coverage estimate.
    timing["solar_hour_approx"] = (
        timing["acquisition_datetime_utc"].dt.hour
        + timing["acquisition_datetime_utc"].dt.minute / 60
        + timing["longitude"] / 15
    ) % 24
    timing["solar_hour_bin"] = (
        timing["solar_hour_approx"].floordiv(1).astype(int)
    )

    # Exclude the reserved final-evaluation year from these diagnostics.
    timing = timing.loc[timing["date_utc"].lt("2025-01-01")]

    for label, hour_column in (
        ("utc", "utc_hour"),
        ("solar", "solar_hour_bin"),
    ):
        (
            timing.groupby(
                ["month", "satellite", "version", "daynight", hour_column]
            )
            .agg(
                records=("sensor", "size"),
                dates_with_detections=("date_utc", "nunique"),
            )
            .reset_index()
            .to_csv(
                output / f"{sensor}_vegetation_timing_{label}.csv",
                index=False,
            )
        )    
    
    vegetation_summary = vegetation_monthly_summary(clean)

    for role in ("development", "final_evaluation"):
        selected = vegetation_summary.loc[
            vegetation_summary["dataset_role"].eq(role)
        ]
        selected.to_csv(
            output / f"{sensor}_vegetation_monthly_{role}.csv",
            index=False,
        )

    months = pd.period_range(start=start, end=end, freq="M").astype(str)

    monthly = pd.DataFrame(index=pd.Index(months, name="month"))
    monthly["all_records"] = (
        clean.groupby("month").size().reindex(months, fill_value=0)
    )
    monthly["type_0_records"] = (
        clean.loc[clean["type_code"].eq("0")]
        .groupby("month").size().reindex(months, fill_value=0)
    )
    monthly["dates_with_detections"] = (
        clean.groupby("month")["date_utc"]
        .nunique().reindex(months, fill_value=0)
    )
    monthly["coverage_status"] = "unknown"
    monthly.reset_index().to_csv(
        output / f"{sensor}_monthly.csv", index=False
    )

    # These summaries let us inspect product composition and possible
    # changes over time without assuming equivalence between sensors.
    groups = {
        "satellite": ["month", "satellite"],
        "confidence": ["month", "confidence_raw"],
        "type": ["month", "type_code"],
        "version": ["month", "version"],
        "daynight": ["month", "daynight"],
    }
    for name, columns in groups.items():
        (
            clean.groupby(columns, dropna=False)
            .size()
            .rename("records")
            .reset_index()
            .to_csv(output / f"{sensor}_monthly_by_{name}.csv", index=False)
        )

    def distribution(column: str) -> dict:
        return {
            str(key): int(value)
            for key, value in clean[column].value_counts(
                dropna=False
            ).items()
        }

    report = {
        "input_file": str(path.resolve()),
        "sha256": sha256(path),
        "columns": list(raw.columns),
        **validation,
        "first_detection_utc": str(
            clean["acquisition_datetime_utc"].min()
        ),
        "last_detection_utc": str(
            clean["acquisition_datetime_utc"].max()
        ),
        "satellites": distribution("satellite"),
        "versions": distribution("version"),
        "confidence": distribution("confidence_raw"),
        "types": distribution("type_code"),
        "daynight": distribution("daynight"),
        "blank_fields": {
            key: value for key, value in blank_counts.items() if value
        },
        "months_without_records": monthly.index[
            monthly["all_records"].eq(0)
        ].tolist(),
        "notes": [
            "Summaries use exact-deduplicated records.",
            "No confidence or type filtering applied to all_records.",
            "type_0_records is a separate diagnostic subset.",
            "Zero records do not establish clear-sky observations.",
            "Date endpoints describe detections, not coverage.",
            "Version labels are recorded without reinterpretation.",
        ],
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modis", type=Path,
        default=Path(
            "data/historical/raw/modis/fire_archive_M-C61_847917.csv"
        ),
    )
    parser.add_argument(
        "--viirs", type=Path,
        default=Path(
            "data/historical/raw/viirs_noaa20/"
            "fire_archive_J1V-C2_847918.csv"
        ),
    )
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument(
        "--output", type=Path,
        default=Path("outputs/historical_audit"),
    )
    args = parser.parse_args()

    start = pd.Timestamp(args.start).date().isoformat()
    end = pd.Timestamp(args.end).date().isoformat()
    if end < start:
        parser.error("--end must be on or after --start")

    for path in (args.modis, args.viirs):
        if not path.is_file():
            parser.error(f"Input file not found: {path}")

    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output directory is not empty; choose a new --output")
    args.output.mkdir(parents=True, exist_ok=True)

    report = {
        "requested_start": start,
        "requested_end": end,
        "bbox_west_south_east_north": [145, -38, 151, -32],
        "method": "historical audit only; no harmonization",
    }

    for sensor, path in (("modis", args.modis), ("viirs", args.viirs)):
        report[sensor] = audit(
            path, sensor, start, end, args.output
        )

    (args.output / "audit_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    print(json.dumps(report, indent=2))
    print(f"\nSaved audit to {args.output.resolve()}")


if __name__ == "__main__":
    main()