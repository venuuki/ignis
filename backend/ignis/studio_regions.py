"""Recent FIRMS observations for a selected geographic rectangle."""

from datetime import date, datetime, timezone
from pathlib import Path
import os
import json

import pandas as pd
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query

from .firms import fetch_range, FirmsError
from .compare import standardize, daily_comparison

router = APIRouter(
    prefix="/api/region",
    tags=["Selected region"],
)

ROOT = Path(__file__).resolve().parents[2]


@router.get("/observations")
def selected_region(
    west: float = Query(..., ge=-180, le=180),
    south: float = Query(..., ge=-85, le=85),
    east: float = Query(..., ge=-180, le=180),
    north: float = Query(..., ge=-85, le=85),
    start: date = Query(...),
    end: date = Query(...),
):
    if west >= east or south >= north:
        raise HTTPException(
            422,
            "Bounds must satisfy west < east and south < north.",
        )

    if not (112 <= west < east <= 154.5 and -44.5 <= south < north <= -9):
        raise HTTPException(422, "This prototype supports selections within the Australia study extent only.")

    # Initial volume limits for interactive requests.
    if east - west > 20 or north - south > 20:
        raise HTTPException(
            422,
            "Choose a region at most 20 degrees wide "
            "and 20 degrees high for this prototype.",
        )

    today = datetime.now(timezone.utc).date()

    if end < start or (end - start).days > 2:
        raise HTTPException(
            422,
            "Choose an inclusive date range of 1 to 3 days.",
        )

    if end > today or (today - start).days > 30:
        raise HTTPException(
            422,
            "Use recent UTC dates within the last 30 days, "
            "not future dates.",
        )

    load_dotenv(ROOT / "backend" / ".env")
    key = os.getenv("FIRMS_MAP_KEY", "").strip()

    if (
        not key
        or key == "replace_with_your_private_firms_map_key"
    ):
        raise HTTPException(
            503,
            "Set FIRMS_MAP_KEY in backend/.env, "
            "then restart the server.",
        )

    bbox = (west, south, east, north)
    frames = {}
    points = []

    products = {
        "modis": "MODIS_NRT",
        "viirs": "VIIRS_NOAA20_NRT",
    }

    try:
        for sensor, product in products.items():
            raw = fetch_range(
                product,
                bbox,
                start,
                end,
                ROOT / "data" / "regional_cache",
                key=key,
            )

            clean = standardize(raw, sensor)

            clean = clean.loc[
                clean.latitude.between(south, north)
                & clean.longitude.between(west, east)
                & clean["date"].between(
                    pd.Timestamp(start),
                    pd.Timestamp(end),
                )
            ].copy()

            frames[sensor] = clean

            for row in clean.to_dict(orient="records"):
                points.append({
                    "sensor": sensor,
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                })

    except FirmsError:
        raise HTTPException(
            502,
            "FIRMS request failed. Check the backend key, "
            "NASA product availability, and network connection.",
        ) from None

    daily = daily_comparison(
        frames["modis"],
        frames["viirs"],
        start,
        end,
    )

    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")

    return {
        "bbox": list(bbox),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "counts": {
            sensor: len(frame)
            for sensor, frame in frames.items()
        },
        "daily": json.loads(
            daily.to_json(orient="records")
        ),
        "points": points[:10000],
        "point_limit": 10000,
        "total_records": len(points),
        "coverage": "unknown",
        "scope": (
            "Raw recent NRT detections, all returned hotspot types. "
            "No harmonization or historical baseline."
        ),
    }