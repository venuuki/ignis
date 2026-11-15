"""EmberCal API - harmonized MODIS/VIIRS burning activity calendar."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta

import duckdb
import numpy as np
import polars as pl
from cachetools import LRUCache
from fastapi import APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import analytics as an
from .aoi import cell_weights, aoi_key, points_in_aoi, prefilter_bounds
from .config import settings
from .data import get_store
from .schemas import AOI, HotspotQuery, Query
from .scope import validate_australia
from .evidence import evidence_for

app = APIRouter(prefix="/api/analysis", tags=["Australia analysis"])
_wcache: LRUCache = LRUCache(maxsize=256)
UNIT = "VIIRS S-NPP-equivalent hotspots"
SERIES_INFO = {
 "modis_harmonized": {"label":"Harmonized", "unit":UNIT, "description":"Model predictions from MODIS throughout the record, including the overlap years. Production predictions are not independent validation."},
 "modis_raw": {"label":"MODIS", "unit":"Quality-filtered MODIS detections", "description":"Terra/Aqua native counts aggregated to the analysis grid."},
 "viirs_raw": {"label":"VIIRS reference", "unit":UNIT, "description":"Measured S-NPP, with NOAA-20/21 scaled to S-NPP units where the reference is unavailable. This is not always raw VIIRS."},
}


def weights_for(aoi: AOI):
    validate_australia(aoi)
    k = aoi_key(aoi)
    if k not in _wcache:
        store = get_store()
        w, summary = cell_weights(aoi, store.cells)
        if w.is_empty():
            raise HTTPException(422, "The area of interest does not overlap the processed data extent. "
                                     "See /api/meta for available regions.")
        summary["name"] = ("Australia" if aoi.type == "region" else
            "Custom polygon" if aoi.type == "polygon" else f"Point {aoi.point[1]:.3f}, {aoi.point[0]:.3f} · {aoi.radius_km:g} km" if aoi.type == "point_radius" else "Custom bounding box")
        summary["selection"] = aoi.model_dump(exclude_none=True)
        summary["aggregation"] = "Fractional grid-cell weighting; boundary counts are approximations."
        if summary["area_km2"] and summary["area_km2"] < 500:
            summary["warning"] = "AOI is smaller than ~4 grid cells (0.1 deg); monthly counts are area-weighted approximations."
        _wcache[k] = (w, summary, an.aoi_monthly(store, w, summary["effective_cells"]))
    return _wcache[k]


def baseline(q: Query) -> tuple[int, int]:
    b0 = q.baseline_start if q.baseline_start is not None else settings.baseline_start
    b1 = q.baseline_end if q.baseline_end is not None else settings.baseline_end
    if not 2000 <= b0 <= b1 <= 2100:
        raise HTTPException(422, "Baseline must be ordered years between 2000 and 2100")
    return b0, b1


# =============================================================================== meta
@app.get("/health")
def health():
    s = get_store()
    return {"status": "ok", "latest_month": "%d-%02d" % s.latest, "latest_complete_month": "%d-%02d" % s.latest_complete}


@app.get("/meta")
def meta():
    s = get_store()
    return {
        "unit": UNIT,
        "series": SERIES_INFO,
        "method_version":s.harm_report.get("method_version"),
        "coverage_note":"Partition date availability is not cloud-free satellite viewing coverage.",
        "regions": s.regions,
        "record_start": "2000-11",
        "latest_month": "%d-%02d" % s.latest,
        "latest_complete_month": "%d-%02d" % s.latest_complete,
        "default_baseline": [settings.baseline_start, settings.baseline_end],
        "grid": s.harm_report.get("grid"),
        "manifest": s.manifest,
        "datasets": [
            {"name": "MODIS Collection 6.1 Active Fire (Terra, Aqua)", "provider": "NASA FIRMS / LANCE", "resolution": "1 km", "period": "2000-11 ->"},
            {"name": "VIIRS 375 m Active Fire (S-NPP, NOAA-20, NOAA-21)", "provider": "NASA FIRMS / LANCE", "resolution": "375 m", "period": "2012-01 ->"},
            {"name": "NASA POWER (MERRA-2) monthly climate", "provider": "NASA LaRC / GMAO", "resolution": "0.5 deg", "period": "2001 ->"},
            {"name": "ENSO Oceanic Nino Index", "provider": "NOAA CPC"},
            {"name": "Indian Ocean Dipole (DMI)", "provider": "NOAA PSL"},
        ],
    }


@app.get("/methods")
def methods():
    s = get_store()
    fc = {k: v for k, v in s.fc_report.items() if k != "hindcast"}
    return {"harmonization": s.harm_report, "forecast": fc}


# =============================================================================== analytics
@app.post("/aoi")
def aoi_info(aoi: AOI):
    _, summary, _ = weights_for(aoi)
    return summary


@app.post("/calendar")
def calendar(q: Query):
    s = get_store()
    w, summary, monthly = weights_for(q.aoi)
    b0, b1 = baseline(q)
    start, end = an.ym_int(q.start, (2000, 11)), an.ym_int(q.end, s.latest)
    if start > end:
        raise HTTPException(422, "start must be before end")
    res = an.calendar(s, monthly, q.series, start, end, b0, b1)
    return {"aoi": summary, "unit": SERIES_INFO[q.series]["unit"], "series_info":SERIES_INFO[q.series], "observation_coverage":"unknown",
            "series": q.series, "baseline": [b0, b1], **res, "method_version": s.harm_report.get("method_version")}


@app.post("/forecast")
def forecast(q: Query):
    s = get_store()
    w, summary, monthly = weights_for(q.aoi)
    b0, b1 = baseline(q)
    res = an.forecast(s, w, summary["effective_cells"], monthly, b0, b1)
    # recent observed context for the fan chart
    recent = monthly.filter(pl.col("coverage") >= 0.999).sort("year", "month").tail(24)
    res["recent"] = [{"year": r["year"], "month": r["month"], "value": an._r(r["modis_harmonized"])} for r in recent.iter_rows(named=True)]
    res["aoi"] = summary
    res["unit"] = UNIT
    return res


@app.post("/compare")
def compare(q: Query):
    """Harmonization explorer: raw MODIS vs raw VIIRS vs harmonized series."""
    s = get_store()
    _, summary, monthly = weights_for(q.aoi)
    start, end = an.ym_int(q.start, (2000, 11)), an.ym_int(q.end, s.latest)
    m = monthly.filter((pl.col("year") * 100 + pl.col("month")).is_between(start, end) & (pl.col("coverage") > 0))
    rows = [{"year": r["year"], "month": r["month"], "modis_raw": an._r(r["modis_raw"]), "viirs_raw": an._r(r["viirs_raw"]),
             "modis_harmonized": an._r(r["modis_harmonized"]), "method": "modis_model_terra_only" if (r["year"],r["month"]) < (2002,7) else "modis_model"}
            for r in m.iter_rows(named=True)]
    ann = m.filter(pl.col("coverage") >= 0.999).group_by("year").agg(
        pl.col("modis_raw").sum(), pl.col("viirs_raw").sum(),
        pl.col("modis_harmonized").sum(), n=pl.len()).filter(pl.col("n") == 12).sort("year")
    overlap = m.filter(pl.col("viirs_raw").is_not_null() & (pl.col("coverage") >= 0.999))
    ov = {}
    if overlap.height >= 12:
        v, h = overlap["viirs_raw"].to_numpy(), overlap["modis_harmonized"].to_numpy()
        mo = overlap["modis_raw"].to_numpy()
        ov = {"months": overlap.height,
              "viirs_to_modis_ratio": an._r(v.sum() / max(mo.sum(), 1e-9), 2),
              "harmonized_vs_viirs_bias_pct": an._r((h.sum() - v.sum()) / max(v.sum(), 1e-9) * 100, 1),
              "harmonized_vs_viirs_r_log": an._r(float(np.corrcoef(np.log1p(v), np.log1p(h))[0, 1]), 3),
              "evaluation_type":"production_in_sample",
              "note": "In the overlap period the MODIS-derived harmonized series is compared with observed VIIRS for this AOI (in-sample)."}
    rep = s.harm_report
    return {"aoi": summary, "monthly": rows, "independent_test":evidence_for(s, weights_for(q.aoi)[0], summary["effective_cells"]),
            "annual": [{k: (an._r(v) if isinstance(v, float) else v) for k, v in r.items() if k != "n"} for r in ann.iter_rows(named=True)],
            "aoi_overlap_check": ov,
            "global_validation": {"selected_model": rep.get("selected_model"), "holdout_years": rep.get("holdout_years"),
                                  "holdout_metrics": rep.get("holdout_metrics"), "step_change_tests": rep.get("step_change_tests")}}


@app.post("/drivers")
def drivers(aoi: AOI):
    """Recent fire-weather context (NASA POWER anomalies) + climate modes for the AOI."""
    s = get_store()
    w, summary, _ = weights_for(aoi)
    pw_path = s.dir / "drivers_power.parquet"
    if not pw_path.exists():
        return {"available": False}
    reg = s.cells.select("cell", "region_cell").join(w, on="cell").group_by("region_cell").agg(w=pl.col("w").sum())
    zc = ["GWETROOT_z", "PRECTOTCORR_z", "PREC3_z", "T2M_z", "RH2M_z", "WS2M_z"]
    raw = ["GWETROOT", "PRECTOTCORR", "T2M", "T2M_MAX", "RH2M", "WS2M"]
    pw = pl.read_parquet(pw_path).join(reg, on="region_cell")
    pw = pw.with_columns([pl.when(pl.col(c).is_finite()).then(pl.col(c)).otherwise(None) for c in zc + raw])  # JSON-safe
    agg = pw.group_by("year", "month").agg([((pl.col(c) * pl.col("w")).sum() / pl.col("w").sum()).alias(c) for c in zc + raw]).sort("year", "month").tail(6)
    modes = pl.read_parquet(s.dir / "drivers_modes.parquet").sort("year", "month")
    oni = modes.filter(pl.col("oni").is_not_null()).tail(1).row(0, named=True)
    dmi = modes.filter(pl.col("dmi").is_not_null()).tail(1).row(0, named=True)
    enso = "El Nino" if oni["oni"] >= 0.5 else "La Nina" if oni["oni"] <= -0.5 else "Neutral"
    iod = "Positive" if dmi["dmi"] >= 0.4 else "Negative" if dmi["dmi"] <= -0.4 else "Neutral"
    return {"available": True, "aoi": summary,
            "monthly": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in agg.iter_rows(named=True)],
            "enso": {"oni": oni["oni"], "phase": enso, "as_of": f"{oni['year']}-{oni['month']:02d}"},
            "iod": {"dmi": dmi["dmi"], "phase": iod, "as_of": f"{dmi['year']}-{dmi['month']:02d}"},
            "legend": {"GWETROOT": "root-zone soil wetness (0-1); negative anomaly = drier fuels",
                       "PRECTOTCORR": "precipitation mm/day", "PREC3_z": "3-month precipitation anomaly (std units)",
                       "T2M": "2 m temperature (C)", "RH2M": "relative humidity (%)", "WS2M": "wind speed (m/s)"},
            "source": "NASA POWER (MERRA-2), NOAA CPC ONI, NOAA PSL DMI"}


# =============================================================================== hotspots
def _hotspot_frame(q: HotspotQuery) -> pl.DataFrame:
    validate_australia(q.aoi)
    s=get_store()
    if not list((s.dir/"hotspots").glob("*.parquet")):
        raise HTTPException(503,"Hotspot archive is missing. Import Australian Parquets or run ingestion.")
    with duckdb.connect() as con:
        maxd=con.sql(f"select max(acq_date) from '{s.hotspots_path}'").fetchone()[0]
        end=date.fromisoformat(q.end_date) if q.end_date else maxd
        start=date.fromisoformat(q.start_date) if q.start_date else end-timedelta(days=6)
        if start > end: raise HTTPException(422,"Start date must precede end date")
        where=["acq_date between $start and $end"]
        parameters={"start":start,"end":end}
        bounds=prefilter_bounds(q.aoi)
        if bounds:
            where.append("longitude between $west and $east and latitude between $south and $north")
            parameters.update(dict(zip(("west","south","east","north"),bounds)))
        else:
            where.append("region = $region");parameters['region']=q.aoi.region
        if q.qc_only: where.append("qc_pass")
        if not q.sensors: raise HTTPException(422,"Choose at least one sensor")
        where.append("sensor in ("+','.join("'"+x+"'" for x in q.sensors)+")")
        df=con.sql(f"select * from '{s.hotspots_path}' where {' and '.join(where)} order by acq_date, acq_time",params=parameters).pl()
    if df.is_empty(): return df
    if q.aoi.type in ('polygon','point_radius'):
        mask=points_in_aoi(q.aoi,df['longitude'].to_numpy(),df['latitude'].to_numpy(),df['region'].to_numpy())
        df=df.filter(pl.Series(mask))
    return df

@app.post("/evidence")
def evidence(q: Query):
    s=get_store();w,summary,_=weights_for(q.aoi)
    return {"aoi":summary,**evidence_for(s,w,summary['effective_cells'])}

@app.post("/daily")
def daily(q: HotspotQuery):
    if q.start_date and q.end_date and (date.fromisoformat(q.end_date)-date.fromisoformat(q.start_date)).days > 366:
        raise HTTPException(422,"Choose at most 366 days for daily observations")
    df=_hotspot_frame(q)
    grouped=(df.group_by('acq_date').agg(
        modis=(pl.col('sensor')=='MODIS').sum(),
        snpp=(pl.col('satellite')=='S-NPP').sum(),noaa20=(pl.col('satellite')=='NOAA-20').sum(),
        noaa21=(pl.col('satellite')=='NOAA-21').sum()).sort('acq_date')) if df.height else None
    days=[]
    if q.start_date and q.end_date:
        start,end=date.fromisoformat(q.start_date),date.fromisoformat(q.end_date)
        lookup={str(row['acq_date']):row for row in grouped.iter_rows(named=True)} if grouped is not None else {}
        for i in range((end-start).days+1):
            d=str(start+timedelta(days=i));row=lookup.get(d,{k:0 for k in ('modis','snpp','noaa20','noaa21')})
            days.append({'date':d,**{k:row[k] for k in ('modis','snpp','noaa20','noaa21')},'viewing_coverage':'unknown'})
    elif grouped is not None:
        days=[{'date':str(r['acq_date']),**{k:r[k] for k in ('modis','snpp','noaa20','noaa21')},'viewing_coverage':'unknown'} for r in grouped.iter_rows(named=True)]
    return {'days':days,'total_records':df.height,'calibration_resolution':'monthly',
        'note':'Daily counts retain sensor identity. The monthly calibration is not extrapolated to individual days; no detections does not demonstrate no fire.'}


@app.post("/hotspots")
def hotspots(q: HotspotQuery):
    df = _hotspot_frame(q)
    n = df.height
    date_range = [str(df["acq_date"].min()), str(df["acq_date"].max())] if n else [None, None]
    lim = q.limit or settings.max_map_points
    if n > lim:
        df = df.sort("frp", descending=True).head(lim)
    pts = [{"lat": r["latitude"], "lon": r["longitude"], "date": str(r["acq_date"]), "time": r["acq_time"], "satellite": r["satellite"],
            "sensor": r["sensor"], "frp": r["frp"], "confidence": r["conf_class"], "daynight": r["daynight"]}
           for r in df.iter_rows(named=True)]
    by_sat = df.group_by("satellite").len().sort("satellite").rows() if n else []
    return {"count": n, "returned": len(pts), "truncated": n > lim, "by_satellite": dict(by_sat), "points": pts,
            "date_range": date_range}


# =============================================================================== exports
def _csv_stream(header: list[str], rows) -> StreamingResponse:
    def gen():
        buf = io.StringIO()
        wr = csv.writer(buf)
        wr.writerow(header)
        yield buf.getvalue()
        for r in rows:
            buf.seek(0)
            buf.truncate()
            wr.writerow(r)
            yield buf.getvalue()
    return gen()


@app.post("/export/hotspots.csv")
def export_hotspots(q: HotspotQuery):
    df = _hotspot_frame(q.model_copy(update={"start_date": q.start_date or "2000-11-01"}) if not q.start_date else q)
    cols = ["latitude", "longitude", "acq_date", "acq_time", "satellite", "sensor", "confidence_raw", "conf_class", "frp",
            "scan", "track", "brightness", "bright_bg", "daynight", "type", "source", "qc_pass", "cell", "region"]
    df = df.select(cols) if df.height else pl.DataFrame(schema={c: pl.Utf8 for c in cols})

    def chunks(step: int = 100_000):
        yield df.head(0).write_csv()
        for i in range(0, df.height, step):
            yield df.slice(i, step).write_csv(include_header=False)

    return StreamingResponse(chunks(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=embercal_hotspots.csv"})


@app.post("/export/calendar.csv")
def export_calendar(q: Query):
    res = calendar(q)
    cols = ["year", "month", "value", "lo", "hi", "projected", "coverage", "partial", "method", "sensor", "value_kind", "observation_coverage", "baseline_n_years", "baseline_first_year", "baseline_last_year", "uncertainty_note",
            "percent_of_median", "percentile", "z_robust", "label", "climatology_median"]
    med = {c["month"]: c.get("median") for c in res["climatology"]}
    export_meta={"area_name":res["aoi"]["name"],"area_selection":__import__('json').dumps(res["aoi"]["selection"]),
                 "series":q.series,"unit":res["unit"],"method_version":res.get("method_version"),"baseline_start":res["baseline"][0],"baseline_end":res["baseline"][1]}
    cols += list(export_meta)
    rows = ([export_meta[c] if c in export_meta else med.get(m["month"]) if c == "climatology_median" else m.get(c) for c in cols] for m in res["months"])
    return StreamingResponse(_csv_stream(cols, rows), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=embercal_calendar_{q.series}.csv"})


