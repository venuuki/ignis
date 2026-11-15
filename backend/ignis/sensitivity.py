"""Diagnostic sensitivity checks. No calibration or network requests."""

from pathlib import Path

import pandas as pd

from .align import prepare, aggregate


def main():
    input_dir = Path("outputs")
    output_dir = input_dir / "sensitivity"

    modis_raw = pd.read_csv(
        input_dir / "modis_observations.csv",
        dtype=str,
        keep_default_na=False,
    )
    viirs_raw = pd.read_csv(
        input_dir / "viirs_observations.csv",
        dtype=str,
        keep_default_na=False,
    )

    if modis_raw.empty or viirs_raw.empty:
        raise ValueError("Both input files must contain observations")

    # Derive the full date span represented in the input files.
    dates = pd.concat(
        [modis_raw["acq_date"], viirs_raw["acq_date"]],
        ignore_index=True,
    )
    dates = pd.to_datetime(dates, format="%Y-%m-%d", errors="raise")
    start = dates.min().date().isoformat()
    end = dates.max().date().isoformat()

    confidence = viirs_raw["confidence"].str.strip().str.lower()
    if not confidence.isin(["l", "n", "h"]).all():
        raise ValueError("Unexpected VIIRS confidence values")

    modes = {
        "all_records": viirs_raw,
        "viirs_nominal_high": viirs_raw.loc[
            confidence.isin(["n", "h"])
        ].copy(),
    }

    # Use a new directory rather than replacing previous diagnostics.
    output_dir.mkdir(parents=True, exist_ok=False)

    summaries = []

    for grid_km in (5, 10, 20):
        modis, _ = prepare(modis_raw, "modis", grid_km)

        for mode, viirs_input in modes.items():
            viirs, _ = prepare(viirs_input, "viirs", grid_km)
            comparison = aggregate(modis, viirs, start, end)

            status_counts = comparison["detection_status"].value_counts()
            active = comparison["detection_status"] != "neither"
            active_days = int(active.sum())
            both = int(status_counts.get("both", 0))

            summaries.append({
                "grid_km": grid_km,
                "mode": mode,
                "start": start,
                "end": end,
                "modis_records": len(modis),
                "viirs_records": len(viirs),
                "cells_with_any_detection": (
                    comparison["grid_cell_id"].nunique()
                ),
                "cell_days_with_any_detection": active_days,
                "both_cell_days": both,
                "modis_only_cell_days": int(
                    status_counts.get("modis_only", 0)
                ),
                "viirs_only_cell_days": int(
                    status_counts.get("viirs_only", 0)
                ),
                "shared_fraction_of_active_cell_days": (
                    both / active_days if active_days else float("nan")
                ),
            })

            comparison.to_csv(
                output_dir / f"comparison_{grid_km}km_{mode}.csv",
                index=False,
            )

    summary = pd.DataFrame(summaries)
    summary.to_csv(output_dir / "summary.csv", index=False)

    print(summary.to_string(index=False))
    print(f"\nSaved diagnostics to {output_dir.resolve()}")
    print(
        "\nThese are descriptive comparisons, not accuracy scores."
        "\nMODIS confidence filtering is unchanged."
        "\nCoverage remains unknown."
    )


if __name__ == "__main__":
    main()