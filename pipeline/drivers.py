"""Step 3 - Fire-driver data for forecasting.

* NASA POWER (MERRA-2 based) monthly climate per 0.5deg region cell:
    GWETROOT  root-zone soil wetness (fuel dryness)      PRECTOTCORR precipitation (mm/day)
    T2M       2 m air temperature                         T2M_MAX     max temperature
    RH2M      relative humidity                           WS2M        wind speed
  Completed years come from the monthly endpoint; the current year is built from the
  daily endpoint (POWER is updated with a few days latency).
* Climate modes: ENSO Oceanic Nino Index (NOAA CPC) and Indian Ocean Dipole DMI (NOAA PSL).

Usage:  python -m pipeline.drivers
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import duckdb
import numpy as np
import polars as pl
import requests

from pipeline.common import PROCESSED, RAW, cell_center, cell_ids, load_config, write_manifest

PARAMS = ["GWETROOT", "PRECTOTCORR", "T2M", "T2M_MAX", "RH2M", "WS2M"]
POWER_M = ("https://power.larc.nasa.gov/api/temporal/monthly/point?parameters={p}&community=AG"
           "&longitude={lon}&latitude={lat}&start={y0}&end={y1}&format=JSON")
POWER_D = ("https://power.larc.nasa.gov/api/temporal/daily/point?parameters={p}&community=AG"
           "&longitude={lon}&latitude={lat}&start={d0}&end={d1}&format=JSON")
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
DMI_URL = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "EmberCal-pipeline/1.0"


def power_get(url: str) -> dict:
    """GET a NASA POWER URL politely: on 429 (rate limit) or 5xx, wait and retry (up to ~5 min)."""
    for attempt in range(8):
        r = SESSION.get(url, timeout=180)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(min(10 * 2 ** attempt, 120))
            continue
        r.raise_for_status()
    r.raise_for_status()
    return {}


def fetch_power(dcell: int, deg: float, y0: int, y1: int, today: date) -> list[dict]:
    cache = RAW / "power" / f"d{deg}_{dcell}_{y0}_{y1}_{today.isoformat()}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    lat, lon = cell_center(dcell, deg)
    lat, lon = round(float(lat), 3), round(float(lon), 3)
    rows: dict[tuple[int, int], dict] = {}
    par = power_get(POWER_M.format(p=",".join(PARAMS), lat=lat, lon=lon, y0=y0, y1=y1))["properties"]["parameter"]
    for p in PARAMS:
        for k, v in par[p].items():
            y, m = int(k[:4]), int(k[4:])
            if m == 13:
                continue  # annual value
            rows.setdefault((y, m), {"driver_cell": dcell, "year": y, "month": m})[p] = None if v == -999 else v
    # current year from daily data
    d0 = date(y1 + 1, 1, 1)
    if d0 <= today:
        par = power_get(POWER_D.format(p=",".join(PARAMS), lat=lat, lon=lon, d0=d0.strftime("%Y%m%d"),
                                       d1=today.strftime("%Y%m%d")))["properties"]["parameter"]
        acc: dict[tuple[int, int], dict[str, list]] = {}
        for p in PARAMS:
            for k, v in par[p].items():
                if v == -999:
                    continue
                key = (int(k[:4]), int(k[4:6]))
                acc.setdefault(key, {}).setdefault(p, []).append(v)
        for key, vals in acc.items():
            rec = rows.setdefault(key, {"driver_cell": dcell, "year": key[0], "month": key[1]})
            for p, lst in vals.items():
                rec[p] = float(np.max(lst)) if p == "T2M_MAX" else float(np.mean(lst))
            rec["days"] = max(len(v) for v in vals.values())
    out = list(rows.values())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out))
    return out


def parse_oni(text: str) -> pl.DataFrame:
    seas = {"DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12}
    rows = []
    for line in text.splitlines()[1:]:
        p = line.split()
        if len(p) == 4 and p[0] in seas:
            rows.append((int(p[1]), seas[p[0]], float(p[3])))  # centre month of the 3-month season
    return pl.DataFrame(rows, schema=["year", "month", "oni"], orient="row")


def parse_dmi(text: str) -> pl.DataFrame:
    rows = []
    for line in text.splitlines():
        p = line.split()
        if len(p) == 13 and p[0].isdigit() and 1870 < int(p[0]) < 2100:
            for m, v in enumerate(p[1:], 1):
                if float(v) > -99:
                    rows.append((int(p[0]), m, float(v)))
    return pl.DataFrame(rows, schema=["year", "month", "dmi"], orient="row")


def main() -> None:
    today = date.today() - timedelta(days=1)
    cfg = load_config()
    ddeg = cfg["grid"].get("driver_deg", 1.0)
    regs = pl.read_parquet(PROCESSED / "cells.parquet").select("region_cell").unique()
    rlat, rlon = cell_center(regs["region_cell"].to_numpy(), cfg["grid"]["region_deg"])
    regs = regs.with_columns(driver_cell=pl.Series(cell_ids(rlat, rlon, ddeg)))
    dcells = regs["driver_cell"].unique().to_list()
    y1 = today.year - 1
    print(f"Fetching NASA POWER for {len(dcells)} {ddeg}-degree cells covering {regs.height} regions "
          f"(2001-{y1} monthly + {today.year} daily) ...", flush=True)
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(lambda c: fetch_power(c, ddeg, 2001, y1, today), dcells))
    clim = pl.DataFrame([r for rr in res for r in rr], infer_schema_length=None)
    if "days" not in clim.columns:
        clim = clim.with_columns(days=pl.lit(None, pl.Int64))
    # anomalies vs 2001-2020 monthly climatology (per driver cell)
    base = clim.filter(pl.col("year").is_between(2001, 2020)).group_by("driver_cell", "month").agg(
        [pl.col(p).mean().alias(f"{p}_clim") for p in PARAMS] + [pl.col(p).std().alias(f"{p}_sd") for p in PARAMS])
    clim = clim.join(base, on=["driver_cell", "month"], how="left").with_columns(
        # z-score; undefined (null) where a variable never changes in that month (sd = 0, e.g. desert soil wetness)
        [pl.when(pl.col(f"{p}_sd") > 1e-9).then((pl.col(p) - pl.col(f"{p}_clim")) / pl.col(f"{p}_sd")).otherwise(None).alias(f"{p}_z")
         for p in PARAMS]
    ).drop([f"{p}_sd" for p in PARAMS]).sort("driver_cell", "year", "month")
    # rolling 3-month precipitation anomaly (drought signal)
    # antecedent conditions: in arid/grassland regions (e.g. inland Australia) rain 6-12 months earlier
    # grows the fuel that burns later, so longer windows matter as much as recent dryness
    clim = clim.with_columns(
        PREC3_z=pl.col("PRECTOTCORR_z").rolling_mean(3, min_samples=1).over("driver_cell"),
        PREC6_z=pl.col("PRECTOTCORR_z").rolling_mean(6, min_samples=3).over("driver_cell"),
        PREC12_z=pl.col("PRECTOTCORR_z").rolling_mean(12, min_samples=6).over("driver_cell"),
        GWET6_z=pl.col("GWETROOT_z").rolling_mean(6, min_samples=3).over("driver_cell"),
    )
    # every 0.5-degree region gets the drivers of the driver cell it falls in
    clim = regs.join(clim, on="driver_cell", how="inner").drop("driver_cell").sort("region_cell", "year", "month")
    clim.write_parquet(PROCESSED / "drivers_power.parquet")

    oni = parse_oni(SESSION.get(ONI_URL, timeout=60).text)
    dmi = parse_dmi(SESSION.get(DMI_URL, timeout=60).text)
    modes = oni.join(dmi, on=["year", "month"], how="full", coalesce=True).sort("year", "month")
    modes.write_parquet(PROCESSED / "drivers_modes.parquet")
    print(f"Saved drivers_power.parquet ({clim.height:,} rows, to {clim['year'].max()}-{clim.filter(pl.col('year') == clim['year'].max())['month'].max():02d}) "
          f"and drivers_modes.parquet (ONI to {oni.tail(1).rows()[0][:2]}, DMI to {dmi.tail(1).rows()[0][:2]})")
    write_manifest({"nasa_power": {"source": "https://power.larc.nasa.gov (MERRA-2)", "params": PARAMS, "last_date": str(today)},
                    "climate_modes": {"oni": ONI_URL, "dmi": DMI_URL}})


if __name__ == "__main__":
    main()
