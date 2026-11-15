"""Read-only UI adapter for existing IGNIS data and processing."""
from pathlib import Path
from functools import lru_cache
from datetime import date
from typing import Literal
import json

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .align import prepare, aggregate


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


def rows(frame):
    return json.loads(
        frame.to_json(orient="records", date_format="iso")
    )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "region": "Australia",
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


@app.get("/api/status")
def connection_status():
    import os
    from dotenv import load_dotenv
    load_dotenv(ROOT / "backend" / ".env")
    key = os.getenv("FIRMS_MAP_KEY", "").strip()
    return {
        "configured": bool(key and key != "replace_with_your_private_firms_map_key"),
        "region": "Australia",
        "observation_window": ["2026-11-08", "2026-11-12"],
        "coverage": "unknown",
    }


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