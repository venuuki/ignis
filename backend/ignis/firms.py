"""Small, testable NASA FIRMS Area API client with persistent response caching."""
from __future__ import annotations
import hashlib
import io
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
import pandas as pd

BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
# NASA FIRMS Area API source IDs; availability varies by date and product.
SOURCES = {
    "modis": "MODIS_NRT",
    "viirs": "VIIRS_SNPP_NRT",
    "viirs_noaa20": "VIIRS_NOAA20_NRT",
    "viirs_noaa21": "VIIRS_NOAA21_NRT",
}
# Study area: southeastern Australia, encompassing parts of NSW and Victoria.
STUDY_BBOX = (145.0, -38.0, 151.0, -32.0)  # west, south, east, north


class FirmsError(RuntimeError):
    pass


def _validate_bbox(bbox):
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must be (west,south,east,north) in WGS84 degrees")


def _cache_file(cache_dir: Path, source: str, bbox, start: date, days: int) -> Path:
    identifier = json.dumps([source, list(bbox), start.isoformat(), days], separators=(",", ":"))
    return cache_dir / (hashlib.sha256(identifier.encode()).hexdigest() + ".csv")


def fetch_window(source: str, bbox, start: date, days: int, cache_dir: Path,
                 key: str | None = None, offline: bool = False,
                 client: httpx.Client | None = None) -> pd.DataFrame:
    """Fetch 1-5 days; cache raw CSV, including header-only zero-detection responses."""
    if source not in SOURCES.values():
        raise ValueError(f"Unsupported source: {source}")
    _validate_bbox(bbox)
    if not 1 <= days <= 5:
        raise ValueError("FIRMS Area API windows must contain 1-5 days")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_file(cache_dir, source, bbox, start, days)
    if path.exists():
        return pd.read_csv(path)
    if offline:
        raise FirmsError(f"Missing offline cache: {path.name}")
    key = key or os.getenv("FIRMS_MAP_KEY")
    if not key or key == "replace_with_your_private_firms_map_key":
        raise FirmsError("Set FIRMS_MAP_KEY in .env or environment; never commit it")
    bbox_str = ",".join(f"{v:g}" for v in bbox)
    url = f"{BASE_URL}/{quote(key, safe='')}/{source}/{bbox_str}/{days}/{start.isoformat()}"
    owned = client is None
    session = client or httpx.Client(timeout=45, follow_redirects=True)
    try:
        for attempt in range(3):
            try:
                response = session.get(url)
                response.raise_for_status()
                body = response.text
                if not body.strip() or body.lstrip().startswith(("<", "{")):
                    raise FirmsError("Unexpected FIRMS response; verify key, source and date availability")
                parsed = pd.read_csv(io.StringIO(body))
                if not {"latitude", "longitude", "acq_date"}.issubset(parsed.columns):
                    raise FirmsError(f"FIRMS response missing expected columns: {list(parsed.columns)}")
                temp = path.with_suffix(".tmp")
                temp.write_text(body, encoding="utf-8")
                temp.replace(path)
                return parsed
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in (429, 500, 502, 503, 504):
                    raise FirmsError(f"FIRMS rejected request (HTTP {status}); verify key and product") from exc
                if attempt == 2:
                    raise FirmsError(f"FIRMS request failed after retries: {type(exc).__name__}") from exc
                time.sleep(2 ** attempt)
    finally:
        if owned:
            session.close()


def fetch_range(source: str, bbox, start: date, end: date, cache_dir: Path,
                key: str | None = None, offline: bool = False,
                client: httpx.Client | None = None) -> pd.DataFrame:
    """Inclusive range, split into legal 5-day windows. Does not silently fetch huge ranges."""
    if end < start:
        raise ValueError("end must be >= start")
    if (end - start).days > 30:
        raise ValueError("Phase 1 limits queries to 31 days to protect the free API budget")
    frames = []
    day = start
    while day <= end:
        days = min(5, (end - day).days + 1)
        frames.append(fetch_window(source, bbox, day, days, cache_dir, key, offline, client))
        day += timedelta(days=days)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
