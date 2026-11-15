"""Step 1 - Download FIRMS archive (country yearly files) + recent data, clean, and store
all hotspots as Parquet: one file per country / sensor / year in data/processed/hotspots/,
so very large countries (Australia: ~30 million detections) never need to fit in memory at once.

Archive files are public: https://firms.modaps.eosdis.nasa.gov/data/country/
Recent data (the current year, not yet in the yearly archive) is fetched from the FIRMS
area API when FIRMS_MAP_KEY is set (SP where available, NRT after); otherwise only the public
last-7-days NRT feed is used.

Usage:  python -m pipeline.ingest_firms [--refresh]
"""
from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import duckdb
import polars as pl
import requests

from pipeline.common import PROCESSED, RAW, load_config, load_env, normalise_firms, read_firms_csv, write_manifest
from pipeline.downloads import download_archive

ARCHIVE_URL = "https://firms.modaps.eosdis.nasa.gov/data/country/{s}/{y}/{s}_{y}_{c}.csv"
API_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/{bbox}/{days}/{d}"
AVAIL_URL = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{key}/ALL"
API_MAX_DAYS = 5  # FIRMS area API accepts a day range of 1..5
NRT_FEEDS = {  # {r} = FIRMS NRT region name (config: nrt_region)
    "MODIS": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_{r}_7d.csv",
    "VIIRS_SNPP": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_{r}_7d.csv",
    "VIIRS_NOAA20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_{r}_7d.csv",
    "VIIRS_NOAA21": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_{r}_7d.csv",
}
HOT_DIR = PROCESSED / "hotspots"
DEDUP = ["latitude", "longitude", "acq_date", "acq_time", "satellite"]
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "EmberCal-pipeline/1.0"


def hotspot_glob() -> str:
    return str(HOT_DIR / "*.parquet").replace("\\", "/")


# =============================================================================== archive
def archive_file(sensor: str, year: int, country: str, cfg: dict, refresh: bool) -> tuple[str, int, str, int]:
    """Download one FIRMS country-year file, normalise it and store it as Parquet.
    Returns (sensor, year, country, rows); rows < 0 is an HTTP error code."""
    out = HOT_DIR / f"{country}__{sensor}__{year}.parquet"
    if out.exists() and not refresh:
        print(f'  Cached: {out.name}', flush=True)
        return sensor, year, country, pl.scan_parquet(out).select(pl.len()).collect().item()
    csv = RAW / "firms_archive" / sensor / f"{sensor}_{year}_{country}.csv"
    if not csv.exists() or refresh:
        status = download_archive(ARCHIVE_URL.format(s=sensor, y=year, c=country), csv, SESSION, refresh=refresh)
        if status != 200:
            return sensor, year, country, -status
    df = normalise_firms(read_firms_csv(csv), country, "archive", cfg).unique(subset=DEDUP, keep="first")
    temporary = out.with_name(out.name + '.part')
    df.write_parquet(temporary, compression="zstd")
    temporary.replace(out)
    print(f'  Completed: {out.name} ({df.height:,} rows)', flush=True)
    if csv.stat().st_size > 20_000_000:  # large raw CSVs are not kept (the Parquet holds every field)
        csv.unlink()
    return sensor, year, country, df.height


# =============================================================================== recent (API)
def data_availability(key: str) -> dict[str, tuple[date, date]]:
    r = SESSION.get(AVAIL_URL.format(key=key), timeout=60)
    r.raise_for_status()
    out = {}
    for line in r.text.strip().splitlines()[1:]:
        p = line.split(",")
        if len(p) == 3:
            out[p[0]] = (date.fromisoformat(p[1]), date.fromisoformat(p[2]))
    return out


def fetch_recent_api(key: str, bbox: list[float], start: date, end: date) -> list[tuple[pl.DataFrame, bool]]:
    """Fetch [start, end] from the FIRMS area API for MODIS, S-NPP, NOAA-20 and NOAA-21.
    For every day the quality-controlled Standard Processing (SP) product is used where it exists,
    otherwise the Near-Real-Time (NRT) product (chosen with the FIRMS data-availability endpoint).
    Returns (frame, country_attributed=False) - attribution is done later through grid cells."""
    avail = data_availability(key)
    frames, failures = [], []
    box = ",".join(str(v) for v in bbox)
    for prod, inst in [("MODIS", "MODIS"), ("VIIRS_SNPP", "VIIRS"), ("VIIRS_NOAA20", "VIIRS"), ("VIIRS_NOAA21", "VIIRS")]:
        segments = []
        sp, nrt = avail.get(f"{prod}_SP"), avail.get(f"{prod}_NRT")
        sp_end = min(sp[1], end) if sp and sp[1] >= start else start - timedelta(days=1)
        if sp_end >= start:
            segments.append((f"{prod}_SP", start, sp_end))
        if nrt:
            n0, n1 = max(sp_end + timedelta(days=1), nrt[0], start), min(nrt[1], end)
            if n1 >= n0:
                segments.append((f"{prod}_NRT", n0, n1))
        for src, s0, s1 in segments:
            d, n = s0, 0
            while d <= s1:
                days = min(API_MAX_DAYS, (s1 - d).days + 1)
                url = API_URL.format(key=key, src=src, bbox=box, days=days, d=d.isoformat())
                # cache complete past chunks on disk so re-runs do not spend the API quota again
                cache = RAW / "firms_api" / f"{src}_{box.replace(',', '_')}_{d.isoformat()}_{days}.csv"
                chunk_end = d + timedelta(days=days - 1)
                content = cache.read_bytes() if cache.exists() else None
                attempt, r = 0, None
                while content is None and attempt < 15:
                    attempt += 1
                    r = SESSION.get(url, timeout=300)
                    if r.status_code == 200 and r.text.startswith("latitude"):
                        content = r.content
                        if chunk_end < date.today() - timedelta(days=3):
                            cache.parent.mkdir(parents=True, exist_ok=True)
                            cache.write_bytes(content)
                    elif "transaction limit" in r.text.lower():
                        print("  FIRMS transaction limit reached - waiting 60 s ...", flush=True)
                        time.sleep(60)
                    elif attempt >= 3:
                        break
                if content is None:
                    failures.append(f"{src} {d}: {r.status_code if r else '-'} {r.text[:80] if r else ''!r}")
                else:
                    df = read_firms_csv(content)
                    if df.height:
                        if "instrument" not in df.columns:
                            df = df.with_columns(instrument=pl.lit(inst))
                        frames.append((df, False))
                        n += df.height
                d += timedelta(days=days)
            print(f"  {src:18s} {s0} -> {s1}: {n:,} detections", flush=True)
    if failures:
        # never write silent gaps: a missing chunk would look like "no fires"
        raise RuntimeError(f"{len(failures)} FIRMS API requests failed, e.g. {failures[:3]}")
    return frames


def fetch_nrt_feed(bbox: list[float], nrt_region: str) -> tuple[list[tuple[pl.DataFrame, bool]], date | None]:
    """Public last-7-days feeds (no key). Returns bbox-filtered frames and the first date covered."""
    frames, first = [], None
    w, s, e, n = bbox
    for name, url in NRT_FEEDS.items():
        try:
            r = SESSION.get(url.format(r=nrt_region), timeout=300)
            r.raise_for_status()
            df = read_firms_csv(r.content)
        except Exception as exc:  # NRT is optional
            print(f"  NRT feed {name} unavailable: {exc}")
            continue
        if df.height:
            d0 = df.select(pl.col("acq_date").str.to_date().min()).item()
            first = d0 if first is None else min(first, d0)
        if "instrument" not in df.columns:
            df = df.with_columns(instrument=pl.lit("MODIS" if name == "MODIS" else "VIIRS"))
        lat, lon = pl.col("latitude").cast(pl.Float64), pl.col("longitude").cast(pl.Float64)
        frames.append((df.filter(lon.is_between(w, e) & lat.is_between(s, n)), False))
    return frames, first


# =============================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download archive files")
    ap.add_argument("--workers", type=int, choices=range(1, 5), default=1, help="Concurrent archive downloads (default: 1 for reliable large transfers)")
    args = ap.parse_args()
    cfg = load_config()
    HOT_DIR.mkdir(parents=True, exist_ok=True)
    legacy = PROCESSED / "hotspots.parquet"
    if legacy.exists():
        legacy.unlink()

    jobs = [(sensor, y, reg["name"]) for reg in cfg["regions"]
            for sensor, (y0, y1) in cfg["years"].items() for y in range(y0, y1 + 1)]
    print(f"Archive: {len(jobs)} FIRMS country-year files ...", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(lambda j: archive_file(*j, cfg, args.refresh), jobs))
    missing = [r[:3] for r in results if r[3] < 0]
    print(f"  ok={len(results) - len(missing)} missing={len(missing)} {missing[:5]}; "
          f"rows={sum(r[3] for r in results if r[3] > 0):,}", flush=True)

    con = duckdb.connect()
    arch = str(HOT_DIR / "*__*__*.parquet").replace("\\", "/")
    last_archive = con.sql(f"select max(acq_date) from '{arch}'").fetchall()[0][0]
    print(f"Archive last date: {last_archive}", flush=True)

    # static-source mask (~1 km) from archive 'type' 1/2/3 -> applied to NRT data (which has no 'type')
    static = pl.Series("k", [r[0] for r in con.sql(
        f"select distinct cast(round(latitude*100) as int)::varchar || '_' || cast(round(longitude*100) as int)::varchar "
        f"from '{arch}' where type in (1, 2, 3)").fetchall()], dtype=pl.Utf8)
    kexpr = (pl.col("latitude") * 100).round().cast(pl.Int32).cast(pl.Utf8) + "_" + (pl.col("longitude") * 100).round().cast(pl.Int32).cast(pl.Utf8)

    load_env()
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    recent_start, recent_rows, last_date = None, 0, last_archive
    for f in HOT_DIR.glob("*__recent.parquet"):
        f.unlink()
    for reg in cfg["regions"]:
        start = last_archive + timedelta(days=1)
        if start > date.today():
            continue
        if key:
            print(f"Fetching {reg['name']} {start} -> {date.today()} via FIRMS API ...", flush=True)
            raws, source, first = fetch_recent_api(key, reg["bbox"], start, date.today()), "api", start
        else:
            print(f"No FIRMS_MAP_KEY: using public 7-day NRT feed for {reg['name']}", flush=True)
            (raws, first), source = fetch_nrt_feed(reg["bbox"], reg.get("nrt_region", "South_Asia")), "nrt"
        if first:
            first = max(first, start)
            recent_start = first if recent_start is None else min(recent_start, first)
        # bbox data are attributed to the country through its 0.1deg cells in the 25-year archive
        own = [r[0] for r in con.sql(f"select distinct cell from '{arch}' where region = $r", params={"r": reg["name"]}).fetchall()]
        parts = []
        for raw, attributed in raws:
            df = normalise_firms(raw, reg["name"], source, cfg).filter(pl.col("acq_date") > last_archive)
            if not attributed:
                df = df.filter(pl.col("cell").is_in(own))
            parts.append(df.with_columns(qc_pass=pl.col("qc_pass") & ~kexpr.is_in(static.implode())))
        if parts:
            df = pl.concat(parts).unique(subset=DEDUP, keep="first").sort("acq_date", "acq_time")
            df.write_parquet(HOT_DIR / f"{reg['name']}__recent.parquet", compression="zstd")
            recent_rows += df.height
            if df.height:
                last_date = max(last_date, df["acq_date"].max())
    print(f"Recent hotspots: {recent_rows:,} rows, last date {last_date}", flush=True)
    write_manifest({
        "firms_archive": {"source": ARCHIVE_URL, "files": len(results) - len(missing), "last_date": str(last_archive)},
        "firms_recent": {"mode": "api" if key else "nrt_7day", "coverage_start": str(recent_start) if recent_start else None,
                         "last_date": str(last_date)},
    })


if __name__ == "__main__":
    main()
