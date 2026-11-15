"""Read-only UI adapter for existing IGNIS data and processing."""
from pathlib import Path
from functools import lru_cache
from datetime import date
from typing import Literal
import json

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .align import prepare, aggregate

from .studio_regions import router as regions_router

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "demo"

app = FastAPI(title="Embercal", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def read(name):
    path = DATA / name
    if not path.is_file():
        raise HTTPException(503, f"Missing dataset: {name}")
    return pd.read_csv(path, dtype={"month": str, "version": str})


def rows(frame):
    return json.loads(
        frame.to_json(orient="records", date_format="iso")
    )


@lru_cache(maxsize=1)
def monthly():
    m = read("modis_vegetation_monthly_development.csv")
    v = read("viirs_vegetation_monthly_development.csv")

    expected = pd.period_range(
        "2019-01", "2024-12", freq="M"
    ).astype(str)

    for frame in (m, v):
        if (
            not frame["dataset_role"].eq("development").all()
            or set(frame.month) != set(expected)
        ):
            raise HTTPException(
                503,
                "Expected development summaries for 2019-2024 only",
            )

    out = pd.DataFrame(
        index=pd.Index(expected, name="month")
    )
    out["viirs"] = v.groupby("month")["records"].sum()
    out["modis"] = m.groupby("month")["records"].sum()

    for sensor, frame in [("modis", m), ("viirs", v)]:
        for dn in ("D", "N"):
            out[f"{sensor}_{dn}"] = (
                frame.loc[frame.daynight.eq(dn)]
                .groupby("month")["records"]
                .sum()
                .reindex(expected, fill_value=0)
            )

    p = read("baseline_predictions.csv")
    p = p.loc[
        p.platform.eq("modis_pooled")
        & p.method.eq("daynight_training_total_ratio")
    ]

    if p.month.duplicated().any():
        raise HTTPException(
            503, "Duplicate experimental predictions"
        )

    out["experimental"] = p.set_index("month")["estimate"]

    if out.isna().any().any():
        raise HTTPException(503, "Incomplete calendar inputs")

    out = out.reset_index()
    out["year"] = out.month.str[:4].astype(int)
    return out


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "months": len(monthly()),
        "holdout": "2025 withheld",
    }


@app.get("/api/meta")
def meta():
    return {
        "region": "Southeastern Australia",
        "bbox": [145, -38, 151, -32],
        "manifest": json.loads(
            (DATA / "manifest.json").read_text(
                encoding="utf-8"
            )
        ),
    }


def selection(metric, start_year, end_year):
    if end_year < start_year:
        raise HTTPException(
            422, "End year precedes start year"
        )

    frame = monthly().copy()
    frame["value"] = frame[metric]
    return frame.loc[
        frame.year.between(start_year, end_year)
    ]


@app.get("/api/calendar")
def calendar(
    metric: Literal[
        "viirs", "modis", "experimental"
    ] = "viirs",
    start_year: int = Query(2019, ge=2019, le=2024),
    end_year: int = Query(2024, ge=2019, le=2024),
):
    frame = selection(metric, start_year, end_year)
    return {
        "rows": rows(frame),
        "metric": metric,
        "coverage": "unknown",
        "experimental": (
            "Held-out estimates, not a production calibration"
        ),
    }


@app.get("/api/validation")
def validation():
    return {
        "overall": rows(read("baseline_scores.csv")),
        "annual": rows(read("baseline_annual_scores.csv")),
        "stress": rows(read("stress_scores.csv")),
        "holdout": "2025 withheld",
    }


@lru_cache(maxsize=1)
def recent():
    frames = []

    for sensor in ("modis", "viirs"):
        frame, _ = prepare(
            pd.read_csv(
                DATA / f"{sensor}_observations.csv",
                dtype=str,
                keep_default_na=False,
            ),
            sensor,
        )
        frames.append(frame)

    return pd.concat(frames, ignore_index=True)


@app.get("/api/observations")
def observations(
    sensor: Literal["all", "modis", "viirs"] = "all",
    confidence: Literal[
        "all", "nominal_high"
    ] = "all",
    day: date | None = None,
):
    frame = recent().copy()

    if sensor != "all":
        frame = frame.loc[frame.sensor.eq(sensor)]

    if confidence == "nominal_high":
        frame = frame.loc[
            frame.sensor.eq("modis")
            | frame.confidence_raw.isin(["n", "h"])
        ]

    if day:
        frame = frame.loc[
            frame.date_utc.eq(day.isoformat())
        ]

    columns = [
        "sensor", "satellite", "latitude", "longitude",
        "date_utc", "acquisition_datetime_utc",
        "confidence_raw", "frp", "daynight",
        "grid_cell_id",
    ]

    return {
        "rows": rows(frame[columns]),
        "modis": int(frame.sensor.eq("modis").sum()),
        "viirs": int(frame.sensor.eq("viirs").sum()),
        "coverage": "unknown",
    }


@app.get("/api/alignment")
def alignment(
    grid_km: int = 10,
    confidence: Literal[
        "all", "nominal_high"
    ] = "all",
):
    if grid_km not in (5, 10, 20):
        raise HTTPException(
            422, "Choose 5, 10, or 20 km"
        )

    frames = {}

    for sensor in ("modis", "viirs"):
        frame = pd.read_csv(
            DATA / f"{sensor}_observations.csv",
            dtype=str,
            keep_default_na=False,
        )

        if (
            sensor == "viirs"
            and confidence == "nominal_high"
        ):
            frame = frame.loc[
                frame.confidence.isin(["n", "h"])
            ]

        frames[sensor], _ = prepare(
            frame, sensor, grid_km
        )

    result = aggregate(
        frames["modis"],
        frames["viirs"],
        "2026-11-08",
        "2026-11-12",
    )

    return {
        "cells": int(result.grid_cell_id.nunique()),
        "statuses": (
            result.detection_status.value_counts().to_dict()
        ),
        "coverage": "unknown",
    }


@app.get("/api/export/calendar.csv")
def export(
    metric: Literal[
        "viirs", "modis", "experimental"
    ] = "viirs",
    start_year: int = Query(2019, ge=2019, le=2024),
    end_year: int = Query(2024, ge=2019, le=2024),
):
    frame = selection(
        metric, start_year, end_year
    ).copy()
    frame["coverage_status"] = "unknown"
    frame["method"] = (
        "experimental held-out day/night estimates"
        if metric == "experimental"
        else "raw type=0 hotspot records"
    )

    return StreamingResponse(
        iter([frame.to_csv(index=False)]),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="embercal-{metric}.csv"'
            )
        },
    )

@app.get("/api/status")
def connection_status():
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env")
    key = os.getenv("FIRMS_MAP_KEY", "").strip()
    return {
        "configured": bool(key and key != "replace_with_your_private_firms_map_key"),
        "historical_region": "Southeastern Australia",
        "historical_years": [2019, 2024],
        "observation_window": ["2026-11-08", "2026-11-12"],
        "coverage": "unknown",
    }


app.include_router(regions_router)

from .analysis_bridge import router as analysis_router
app.include_router(analysis_router)

if (ROOT / "frontend" / "dist").is_dir():
    app.mount(
        "/",
        StaticFiles(
            directory=ROOT / "frontend" / "dist",
            html=True,
        ),
        name="web",
    )