"""Initial *raw* sensor comparison. NOT a harmonization or accuracy model."""
from __future__ import annotations
import pandas as pd


def standardize(frame: pd.DataFrame, sensor: str) -> pd.DataFrame:
    needed = {"latitude", "longitude", "acq_date"}
    if not needed.issubset(frame.columns):
        raise ValueError(f"Missing required columns: {sorted(needed - set(frame.columns))}")
    result = frame.copy()
    result["sensor"] = sensor
    result["date"] = pd.to_datetime(result["acq_date"], errors="raise").dt.normalize()
    result["latitude"] = pd.to_numeric(result["latitude"], errors="raise")
    result["longitude"] = pd.to_numeric(result["longitude"], errors="raise")
    if not result["latitude"].between(-90, 90).all() or not result["longitude"].between(-180, 180).all():
        raise ValueError("Invalid coordinates")
    return result


def daily_comparison(modis: pd.DataFrame, viirs: pd.DataFrame, start, end) -> pd.DataFrame:
    """Zero-fill dates ONLY for counts of detections in returned records.

    A zero is NOT evidence of zero fires or complete satellite observation coverage.
    """
    dates = pd.date_range(start, end, freq="D")
    out = pd.DataFrame({"date": dates})
    for name, frame in (("modis", modis), ("viirs", viirs)):
        clean = standardize(frame, name)
        counts = clean.groupby("date").size().rename(f"{name}_detections")
        out = out.merge(counts, left_on="date", right_index=True, how="left")
        out[f"{name}_detections"] = out[f"{name}_detections"].fillna(0).astype(int)
    out["difference_viirs_minus_modis"] = out["viirs_detections"] - out["modis_detections"]
    return out
