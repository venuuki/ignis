"""Shared paths, config, grid helpers and FIRMS record normalisation (Polars)."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
CONFIG_PATH = Path(__file__).with_name("config.yaml")


def load_env() -> None:
    """Load KEY=VALUE pairs from the project .env (e.g. FIRMS_MAP_KEY) without overriding the environment."""
    import os

    from dotenv import load_dotenv
    for env in (ROOT / ".env", ROOT / "backend" / ".env"):
        load_dotenv(env, override=False)

SATELLITE_MAP = {
    "Terra": "Terra", "T": "Terra",
    "Aqua": "Aqua", "A": "Aqua",
    "N": "S-NPP", "NPP": "S-NPP", "Suomi NPP": "S-NPP",
    "N20": "NOAA-20", "1": "NOAA-20", "J1": "NOAA-20", "NOAA-20": "NOAA-20",
    "N21": "NOAA-21", "2": "NOAA-21", "J2": "NOAA-21", "NOAA-21": "NOAA-21",
}

HOTSPOT_SCHEMA = {
    "region": pl.Utf8, "latitude": pl.Float64, "longitude": pl.Float64,
    "acq_date": pl.Date, "acq_time": pl.Utf8, "year": pl.Int16, "month": pl.Int8,
    "sensor": pl.Utf8, "satellite": pl.Utf8, "confidence_raw": pl.Utf8, "conf_class": pl.Utf8,
    "frp": pl.Float64, "scan": pl.Float64, "track": pl.Float64, "brightness": pl.Float64,
    "bright_bg": pl.Float64, "daynight": pl.Utf8, "type": pl.Int8,
    "cell": pl.Int32, "region_cell": pl.Int32, "source": pl.Utf8, "qc_pass": pl.Boolean,
}


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- regular lat/lon grid
def cell_expr(lat: pl.Expr, lon: pl.Expr, deg: float) -> pl.Expr:
    ncols = int(round(360 / deg))
    row = ((lat + 90.0) / deg).floor().cast(pl.Int32)
    col = ((lon + 180.0) / deg).floor().cast(pl.Int32)
    return row * ncols + col


def cell_ids(lat: np.ndarray, lon: np.ndarray, deg: float) -> np.ndarray:
    ncols = int(round(360 / deg))
    return (np.floor((lat + 90) / deg).astype(np.int64) * ncols + np.floor((lon + 180) / deg).astype(np.int64)).astype(np.int32)


def cell_center(cell: np.ndarray | int, deg: float) -> tuple[np.ndarray, np.ndarray]:
    ncols = int(round(360 / deg))
    cell = np.asarray(cell)
    row, col = cell // ncols, cell % ncols
    return row * deg - 90 + deg / 2, col * deg - 180 + deg / 2


def parent_cell(cell: np.ndarray, deg: float, parent_deg: float) -> np.ndarray:
    lat, lon = cell_center(cell, deg)
    return cell_ids(lat, lon, parent_deg)


# ---------------------------------------------------------------- FIRMS normalisation
def read_firms_csv(src: str | Path | bytes) -> pl.DataFrame:
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    df = pl.read_csv(src, infer_schema_length=0)  # everything as string; cast explicitly
    return df


def normalise_firms(df: pl.DataFrame, region: str, source: str, cfg: dict) -> pl.DataFrame:
    """Convert a raw FIRMS CSV frame (MODIS or VIIRS; archive, NRT or API) to the unified schema."""
    if df.is_empty():
        return pl.DataFrame(schema=HOTSPOT_SCHEMA)
    cols = df.columns
    num = lambda c: pl.col(c).cast(pl.Float64, strict=False) if c in cols else pl.lit(None, pl.Float64)  # noqa: E731
    bright = "brightness" if "brightness" in cols else "bright_ti4"
    bg = "bright_t31" if "bright_t31" in cols else "bright_ti5"
    instrument = pl.col("instrument") if "instrument" in cols else pl.lit("VIIRS")
    g, cl = cfg["grid"], cfg["cleaning"]

    out = df.with_columns(
        is_modis=instrument.str.to_uppercase().str.starts_with("MODIS"),
        _date=pl.col("acq_date").str.to_date(strict=False),
        _conf=pl.col("confidence").cast(pl.Utf8).str.strip_chars(),
    ).select(
        region=pl.lit(region),
        latitude=num("latitude"),
        longitude=num("longitude"),
        acq_date=pl.col("_date"),
        acq_time=pl.col("acq_time").cast(pl.Utf8).str.zfill(4),
        year=pl.col("_date").dt.year().cast(pl.Int16),
        month=pl.col("_date").dt.month().cast(pl.Int8),
        sensor=pl.when(pl.col("is_modis")).then(pl.lit("MODIS")).otherwise(pl.lit("VIIRS")),
        satellite=pl.col("satellite").cast(pl.Utf8).str.strip_chars().replace(SATELLITE_MAP),
        confidence_raw=pl.col("_conf"),
        conf_class=pl.when(pl.col("is_modis")).then(
            pl.when(pl.col("_conf").cast(pl.Float64, strict=False) < 30).then(pl.lit("low"))
            .when(pl.col("_conf").cast(pl.Float64, strict=False) >= 80).then(pl.lit("high"))
            .otherwise(pl.lit("nominal"))
        ).otherwise(
            pl.col("_conf").str.to_lowercase().replace_strict({"l": "low", "low": "low", "n": "nominal", "nominal": "nominal", "h": "high", "high": "high"}, default="unknown", return_dtype=pl.Utf8)
        ),
        _conf_num=pl.col("_conf").cast(pl.Float64, strict=False),
        frp=num("frp"),
        scan=num("scan"),
        track=num("track"),
        brightness=num(bright),
        bright_bg=num(bg),
        daynight=pl.col("daynight").cast(pl.Utf8).str.slice(0, 1).str.to_uppercase(),
        # NRT products have no 'type' -> -1 (cleaned later with the archive static-source mask)
        type=(pl.col("type").cast(pl.Int8, strict=False).fill_null(-1) if "type" in cols else pl.lit(-1, pl.Int8)),
        source=pl.lit(source),
    ).drop_nulls(["latitude", "longitude", "acq_date"])

    out = out.with_columns(
        cell=cell_expr(pl.col("latitude"), pl.col("longitude"), g["cell_deg"]),
        region_cell=cell_expr(pl.col("latitude"), pl.col("longitude"), g["region_deg"]),
        qc_pass=(
            pl.when(pl.col("sensor") == "MODIS")
            .then(pl.col("_conf_num").fill_null(0) >= cl["modis_min_confidence"])
            .otherwise(pl.col("conf_class").replace({"low": "l", "nominal": "n", "high": "h"}).is_in(cl["viirs_confidence"]))
            & pl.col("type").is_in(cl["keep_types"] + [-1])
        ),
    )
    return out.select([pl.col(c).cast(t) for c, t in HOTSPOT_SCHEMA.items()])


def write_manifest(entry: dict) -> None:
    path = DATA / "manifest.json"
    manifest = json.loads(path.read_text()) if path.exists() else {}
    for v in entry.values():
        if isinstance(v, dict):
            v["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest.update(entry)
    path.write_text(json.dumps(manifest, indent=2))
