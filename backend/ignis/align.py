"""Phase 2: spatial/temporal alignment, not sensor harmonization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyproj import Transformer

CRS = "EPSG:3577"


def prepare(
    frame: pd.DataFrame,
    sensor: str,
    grid_km: float = 10,
) -> tuple[pd.DataFrame, dict]:
    """Validate records and assign a fixed Australian equal-area grid."""
    if sensor not in {"modis", "viirs"}:
        raise ValueError("sensor must be modis or viirs")
    if not np.isfinite(grid_km) or grid_km <= 0:
        raise ValueError("grid_km must be positive and finite")

    required = {
        "latitude", "longitude", "acq_date", "acq_time",
        "satellite", "instrument", "confidence", "frp", "version",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{sensor}: missing columns: {sorted(missing)}")

    # Only exact duplicate input rows are removed.
    out = frame.drop_duplicates().copy()
    report = {
        "input_rows": len(frame),
        "exact_duplicates_removed": len(frame) - len(out),
        "retained_rows": len(out),
    }

    for column in ("latitude", "longitude", "frp"):
        out[column] = pd.to_numeric(out[column], errors="raise")
        if not np.isfinite(out[column]).all():
            raise ValueError(f"{sensor}: invalid {column}")

    if not out["latitude"].between(-90, 90).all():
        raise ValueError(f"{sensor}: invalid latitude")
    if not out["longitude"].between(-180, 180).all():
        raise ValueError(f"{sensor}: invalid longitude")
    if (out["frp"] < 0).any():
        raise ValueError(f"{sensor}: negative FRP")

    # This first implementation deliberately supports Australia only.
    in_australia = (
        out["longitude"].between(112, 154)
        & out["latitude"].between(-44, -10)
    )
    if not in_australia.all():
        raise ValueError(
            "EPSG:3577 implementation supports Australian observations only"
        )

    expected = "MODIS" if sensor == "modis" else "VIIRS"
    instruments = out["instrument"].astype("string").str.upper()
    if instruments.isna().any() or not instruments.eq(expected).all():
        raise ValueError(f"{sensor}: unexpected instrument")

    # Read input as strings so leading-zero times and confidence survive.
    times = out["acq_time"].astype("string").str.strip()
    if not times.str.fullmatch(r"\d{1,4}", na=False).all():
        raise ValueError(f"{sensor}: acquisition time must be HHMM")
    times = times.str.zfill(4)

    dates = out["acq_date"].astype("string").str.strip()
    if not dates.str.fullmatch(r"\d{4}-\d{2}-\d{2}", na=False).all():
        raise ValueError(f"{sensor}: acquisition date must be YYYY-MM-DD")
    if (
        (times.str[:2].astype(int) > 23)
        | (times.str[2:].astype(int) > 59)
    ).any():
        raise ValueError(f"{sensor}: invalid acquisition time")

    out["acquisition_datetime_utc"] = pd.to_datetime(
        dates + " " + times,
        format="%Y-%m-%d %H%M",
        utc=True,
        errors="raise",
    )
    out["date_utc"] = (
        out["acquisition_datetime_utc"].dt.strftime("%Y-%m-%d")
    )
    out["sensor"] = sensor
    out["confidence_raw"] = out["confidence"]
    out["source_product"] = out["source_product"].fillna("unknown") \
        if "source_product" in out else "unknown"

    # Product version and satellite are preserved; an exact API source
    # cannot be proven solely from these CSVs.
    transformer = Transformer.from_crs(
        "EPSG:4326", CRS, always_xy=True
    )
    x, y = transformer.transform(
    out["longitude"].tolist(),
    out["latitude"].tolist(),
    )
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("Projection produced invalid coordinates")

    cell_m = grid_km * 1000
    out["x_m"] = x
    out["y_m"] = y
    out["grid_x"] = np.floor(np.asarray(x) / cell_m).astype("int64")
    out["grid_y"] = np.floor(np.asarray(y) / cell_m).astype("int64")
    out["grid_cell_id"] = (
        "x" + out["grid_x"].astype(str)
        + "_y" + out["grid_y"].astype(str)
    )

    return out, report


def aggregate(
    modis: pd.DataFrame,
    viirs: pd.DataFrame,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Complete dates for cells with any detection in the selected period."""
    dates = pd.date_range(start, end, freq="D")
    if dates.empty:
        raise ValueError("end must be on or after start")

    date_strings = dates.strftime("%Y-%m-%d")
    frames = {
        name: frame[frame["date_utc"].isin(date_strings)].copy()
        for name, frame in (("modis", modis), ("viirs", viirs))
    }

    cells = pd.concat(
        [f[["grid_cell_id", "grid_x", "grid_y"]] for f in frames.values()],
        ignore_index=True,
    ).drop_duplicates()

    calendar = pd.DataFrame({"date_utc": date_strings})
    result = cells.merge(calendar, how="cross")
    keys = ["grid_cell_id", "date_utc"]

    for sensor, frame in frames.items():
        grouped = (
            frame.groupby(keys)
            .agg(
                count=("sensor", "size"),
                frp_sum_mw=("frp", "sum"),
            )
            .reset_index()
            .rename(columns={
                "count": f"{sensor}_count",
                "frp_sum_mw": f"{sensor}_frp_sum_mw",
            })
        )
        result = result.merge(grouped, on=keys, how="left")
        result[f"{sensor}_count"] = (
            result[f"{sensor}_count"].fillna(0).astype(int)
        )
        result[f"{sensor}_frp_sum_mw"] = (
            result[f"{sensor}_frp_sum_mw"].fillna(0)
        )
        result[f"{sensor}_detected"] = result[f"{sensor}_count"] > 0
        result[f"{sensor}_coverage_status"] = "unknown"

    result["raw_count_difference"] = (
        result["viirs_count"] - result["modis_count"]
    )
    result["raw_count_ratio"] = (
        result["viirs_count"]
        / result["modis_count"].replace(0, np.nan)
    )
    result["detection_status"] = np.select(
        [
            result["modis_detected"] & result["viirs_detected"],
            result["modis_detected"],
            result["viirs_detected"],
        ],
        ["both", "modis_only", "viirs_only"],
        default="neither",
    )

    return result.sort_values(keys).reset_index(drop=True)


def plot_outputs(
    modis: pd.DataFrame,
    viirs: pd.DataFrame,
    comparison: pd.DataFrame,
    output: Path,
    grid_km: float,
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(18, 5))

    for sensor, frame in (("MODIS", modis), ("VIIRS", viirs)):
        axes[0].scatter(
            frame["x_m"] / 1000,
            frame["y_m"] / 1000,
            s=12,
            alpha=0.65,
            label=sensor,
        )
    axes[0].set(
        title="Detection locations",
        xlabel="Easting (km)",
        ylabel="Northing (km)",
    )
    axes[0].set_aspect("equal")
    axes[0].legend()

    daily = comparison.groupby("date_utc")[
        ["modis_count", "viirs_count"]
    ].sum()
    for sensor in ("modis", "viirs"):
        axes[1].plot(
            daily.index, daily[f"{sensor}_count"],
            marker="o", label=sensor.upper(),
        )
    axes[1].set(title="Daily hotspot records", ylabel="Records")
    axes[1].tick_params(axis="x", rotation=45)
    axes[1].legend()

    totals = comparison.groupby(["grid_x", "grid_y"])[
        "raw_count_difference"
    ].sum().reset_index()
    limit = max(
        1.0,
        float(totals["raw_count_difference"].abs().max()),
    ) if len(totals) else 1.0
    dots = axes[2].scatter(
        (totals["grid_x"] + 0.5) * grid_km,
        (totals["grid_y"] + 0.5) * grid_km,
        c=totals["raw_count_difference"],
        cmap="coolwarm",
        vmin=-limit,
        vmax=limit,
        marker="s",
        s=35,
    )
    axes[2].set(
        title="Cell totals: VIIRS minus MODIS",
        xlabel="Easting (km)",
        ylabel="Northing (km)",
    )
    axes[2].set_aspect("equal")
    fig.colorbar(dots, ax=axes[2], label="Raw record difference")
    fig.suptitle(
        f"UNHARMONIZED — {grid_km:g} km grid — coverage unknown"
    )
    fig.tight_layout()
    fig.savefig(output / "alignment_comparison.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modis", type=Path,
                        default=Path("outputs/modis_observations.csv"))
    parser.add_argument("--viirs", type=Path,
                        default=Path("outputs/viirs_observations.csv"))
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--grid-km", type=float, default=10)
    parser.add_argument("--output", type=Path,
                        default=Path("outputs/phase2"))
    args = parser.parse_args()

    start = pd.Timestamp(args.start).date().isoformat()
    end = pd.Timestamp(args.end).date().isoformat()
    if end < start:
        parser.error("--end must be on or after --start")

    # Prevent accidental replacement of an earlier analysis.
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Output directory is not empty; choose a new --output")

    frames, reports = {}, {}
    for sensor, path in (("modis", args.modis), ("viirs", args.viirs)):
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
        frame, report = prepare(raw, sensor, args.grid_km)
        selected = frame["date_utc"].between(start, end)
        report["outside_requested_dates"] = int((~selected).sum())
        frames[sensor] = frame.loc[selected].copy()
        reports[sensor] = report

    comparison = aggregate(
        frames["modis"], frames["viirs"], start, end
    )

    args.output.mkdir(parents=True, exist_ok=True)
    for sensor, frame in frames.items():
        frame.to_csv(args.output / f"{sensor}_aligned.csv", index=False)

    comparison.to_csv(
        args.output / "daily_grid_comparison.csv", index=False
    )
    comparison[
        comparison["detection_status"].isin(["modis_only", "viirs_only"])
    ].to_csv(args.output / "single_sensor_cell_days.csv", index=False)

    plot_outputs(
        frames["modis"], frames["viirs"],
        comparison, args.output, args.grid_km,
    )

    metadata = {
        "method": "alignment only; no calibration",
        "crs": CRS,
        "grid_km": args.grid_km,
        "grid_origin_m": [0, 0],
        "grid_assignment": "floor(projected_coordinate / cell_size)",
        "calendar": "UTC",
        "start": start,
        "end": end,
        "coverage": "unknown; zero counts mean no retained records",
        "spatial_extent": "cells with detections in selected period",
        "frp": "raw observation sums, not energy or harmonized FRP",
        "inputs": {
            "modis": str(args.modis.resolve()),
            "viirs": str(args.viirs.resolve()),
        },
        "validation": reports,
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))
    print(f"Saved Phase 2 outputs to {args.output.resolve()}")


if __name__ == "__main__":
    main()