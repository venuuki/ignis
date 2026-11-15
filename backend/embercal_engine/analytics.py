"""Calendar, anomaly, trend, season and forecast analytics for an AOI."""
from __future__ import annotations

import math
from calendar import month_abbr
from datetime import date

import numpy as np
import polars as pl
from scipy import stats

from .data import DataStore

Z80 = 1.2815515655446004
MIN_COVERAGE_FOR_ANOMALY = 0.35  # partial months with less data get no anomaly label
LABELS = [(10, "Much below normal"), (33.3, "Below normal"), (66.7, "Near normal"), (90, "Above normal"), (101, "Much above normal")]


def _interp_log(n: float, pts: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation in log(n)."""
    n = max(n, pts[0][0])
    if n >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= n <= x1:
            f = (math.log(n) - math.log(x0)) / (math.log(x1) - math.log(x0))
            return y0 + f * (y1 - y0)
    return pts[-1][1]


def label_for(pct: float | None) -> str | None:
    if pct is None:
        return None
    for th, lab in LABELS:
        if pct < th:
            return lab
    return LABELS[-1][1]


def percentile_rank(values: np.ndarray, x: float) -> float | None:
    if len(values) == 0 or x is None:
        return None
    return float((np.sum(values < x) + 0.5 * np.sum(values == x)) / len(values) * 100)


def ym_int(s: str | None, default: tuple[int, int]) -> int:
    if not s:
        return default[0] * 100 + default[1]
    y, m = s.split("-")
    return int(y) * 100 + int(m)


# =============================================================================== AOI series
def aoi_monthly(store: DataStore, weights: pl.DataFrame, eff_cells: float) -> pl.DataFrame:
    """Monthly AOI totals for every series + harmonization uncertainty (80% interval)."""
    vm = store.harm_report.get("variance_model", {}).get("full", {})
    kappa = _interp_log(max(eff_cells, 1.0), [(1, 1.0), (25, vm.get("kappa_region", 1.0)), (1000, vm.get("kappa_country", 1.0))])
    j = store.monthly.join(weights, on="cell", how="inner")
    model_variance = (pl.col("modis_model_var") * pl.col("w")**2).sum() if "modis_model_var" in j.columns else pl.lit(None, pl.Float64)
    snpp = (pl.col("snpp") * pl.col("w")).sum() if "snpp" in j.columns else pl.lit(None, pl.Float64)
    agg = j.group_by("year", "month").agg(
        model_var=model_variance, viirs_snpp_raw=snpp,
        harmonized=(pl.col("harmonized") * pl.col("w")).sum(),
        var=(pl.col("harm_var") * pl.col("w")**2).sum(),
        modis_raw=(pl.col("modis") * pl.col("w")).sum(),
        viirs_raw=(pl.col("viirs_ref") * pl.col("w")).sum(),
        modis_harmonized=(pl.col("modis_harmonized") * pl.col("w")).sum(),
        frp_modis=(pl.col("modis_frp") * pl.col("w")).sum(),
        frp_snpp=(pl.col("snpp_frp") * pl.col("w")).sum(),
    )
    # every month of the record for the AOI's countries; months absent from the sparse table = 0
    countries = weights.join(store.cells.select("cell", "country"), on="cell")["country"].unique()
    meta = store.meta.filter(pl.col("country").is_in(countries.implode())).group_by("year", "month").agg(
        coverage=pl.col("coverage").max(), method=pl.col("method").mode().first(),
        viirs_src=pl.col("viirs_src").drop_nulls().mode().first())
    native=getattr(store,"native_snpp",None)
    if native is not None:
        counts=native.join(weights,on="cell").group_by("year","month").agg(viirs_snpp_raw=(pl.col("snpp")*pl.col("w")).sum())
        agg=agg.drop("viirs_snpp_raw").join(counts,on=["year","month"],how="full",coalesce=True)
        present=native.group_by("year","month").agg(_snpp_present=pl.col("snpp").sum()>0)
        meta=meta.join(present,on=["year","month"],how="left")
    else:
        meta=meta.with_columns(_snpp_present=pl.col("viirs_src")=="S-NPP")
    m = meta.join(agg, on=["year", "month"], how="left").sort("year", "month")
    has_data = pl.col("coverage") > 0
    viirs_era = pl.col("method").is_in(["viirs_observed", "viirs_scaled"])
    zero = lambda c: pl.when(has_data).then(pl.col(c).fill_null(0)).otherwise(None)  # noqa: E731
    m = m.with_columns(
        harmonized=zero("harmonized"), var=zero("var"), modis_raw=zero("modis_raw"), modis_harmonized=zero("modis_harmonized"),
        frp_modis=zero("frp_modis"), frp_snpp=zero("frp_snpp"),
        viirs_snpp_raw=pl.when(has_data & pl.col("_snpp_present").fill_null(False)).then(pl.col("viirs_snpp_raw").fill_null(0)).otherwise(None),
        viirs_raw=pl.when(has_data & viirs_era).then(pl.col("viirs_raw").fill_null(0)).otherwise(None),
    )
    terra_vm=store.harm_report.get("variance_model",{}).get("terra_only",vm)
    terra_kappa=_interp_log(max(eff_cells,1.),[(1,1.),(25,terra_vm.get("kappa_region",1.)),(1000,terra_vm.get("kappa_country",1.))])
    early=(pl.col("year")<2002)|((pl.col("year")==2002)&(pl.col("month")<7))
    m=m.with_columns(_kappa=pl.when(early).then(pl.lit(terra_kappa)).otherwise(pl.lit(kappa)))
    sd = (m["var"].fill_null(0).clip(0, None) ** .5) * m["_kappa"] * Z80
    model_sd = (m["model_var"].clip(0, None) ** .5) * m["_kappa"] * Z80
    m=m.with_columns(_sd=sd,_model_sd=model_sd)
    model_month=pl.col("method").is_in(["modis_model","modis_model_terra_only"])
    return m.with_columns(
        lo=pl.when(model_month).then((pl.col("harmonized")-pl.col("_sd")).clip(0,None)).otherwise(None),
        hi=pl.when(model_month).then(pl.col("harmonized")+pl.col("_sd")).otherwise(None),
        model_lo=(pl.col("modis_harmonized")-pl.col("_model_sd")).clip(0,None),
        model_hi=pl.col("modis_harmonized")+pl.col("_model_sd"),
    ).drop("_sd","_model_sd","_kappa")


# =============================================================================== calendar
def calendar(store: DataStore, monthly: pl.DataFrame, series: str, start: int, end: int, b0: int, b1: int) -> dict:
    m = monthly.with_columns(ym=pl.col("year") * 100 + pl.col("month"), value=pl.col(series))
    m = m.with_columns(projected=pl.when((pl.col("coverage") > 0) & (pl.col("coverage") < 0.999))
                       .then(pl.col("value") / pl.col("coverage")).otherwise(pl.col("value")))
    base = m.filter(pl.col("year").is_between(b0, b1) & (pl.col("coverage") >= 0.999) & pl.col("value").is_not_null())
    clim = {}
    for mo in range(1, 13):
        v = base.filter(pl.col("month") == mo)["value"].to_numpy()
        allv = m.filter((pl.col("month") == mo) & (pl.col("coverage") >= 0.999) & pl.col("value").is_not_null())["value"].to_numpy()
        if len(v):
            med = float(np.median(v))
            mad = float(np.median(np.abs(v - med))) * 1.4826
            q = np.quantile(v, [0.1, 0.25, 0.75, 0.9])
            clim[mo] = {"month": mo, "name": month_abbr[mo], "median": med, "mean": float(v.mean()), "p10": float(q[0]), "p25": float(q[1]),
                        "p75": float(q[2]), "p90": float(q[3]), "min": float(v.min()), "max": float(v.max()),
                        "record_max": float(allv.max()) if len(allv) else None, "n_years": len(v), "first_year": int(base.filter(pl.col("month")==mo)["year"].min()), "last_year": int(base.filter(pl.col("month")==mo)["year"].max()), "_v": v, "_mad": mad}
        else:
            clim[mo] = {"month": mo, "name": month_abbr[mo], "median": None, "n_years": 0, "_v": np.array([]), "_mad": 0}

    rows = []
    sel = m.filter(pl.col("ym").is_between(start, end)).sort("ym")
    for r in sel.iter_rows(named=True):
        c = clim[r["month"]]
        val, proj, cov = r["value"], r["projected"], r["coverage"] or 0
        modelled = series == "modis_harmonized" or (series == "harmonized" and r["method"] in ("modis_model", "modis_model_terra_only"))
        source_method = r["method"] if series == "harmonized" else (
            ("modis_model_terra_only" if (r["year"], r["month"]) < (2002,7) else "modis_model") if series == "modis_harmonized" else series)
        sensor = (("MODIS (Terra)" if (r["year"],r["month"]) < (2002,7) else "MODIS (Terra/Aqua)") if series in ("modis_raw","modis_harmonized") else
                  "S-NPP" if series == "viirs_snpp_raw" else r["viirs_src"] if series == "viirs_raw" or not modelled else
                  "MODIS (Terra)" if source_method == "modis_model_terra_only" else "MODIS (Terra/Aqua)")
        kind = "model_estimate" if modelled else "scaled_reference" if r["method"] == "viirs_scaled" and series in ("harmonized","viirs_raw") else "observed_count"
        cell = {"year":r["year"],"month":r["month"],"value":_r(val),"coverage":round(cov,3),
                "partial":0 < cov < .999,"method":source_method,"sensor":sensor if val is not None else None,
                "value_kind":kind if val is not None else "unavailable","baseline_n_years":c["n_years"],
                "baseline_first_year":c.get("first_year"),"baseline_last_year":c.get("last_year"),
                "observation_coverage":"unknown"}
        cell["lo"]=_r(r.get("model_lo") if series == "modis_harmonized" else r.get("lo")) if modelled else None
        cell["hi"]=_r(r.get("model_hi") if series == "modis_harmonized" else r.get("hi")) if modelled else None
        cell["uncertainty_note"]=("Residual calibration interval; satellite omission/measurement uncertainty is not included." if modelled else
            "Measurement and reference-scaling uncertainty are not quantified; no zero-width interval is implied.")
        if val is not None and c["median"] is not None and cov >= .999:
            x = proj
            cell["projected"] = None
            cell["percent_of_median"] = round(x / c["median"] * 100, 1) if c["median"] > 0 else None
            cell["percentile"] = _r(percentile_rank(c["_v"], x), 1)
            cell["z_robust"] = round((x - c["median"]) / c["_mad"], 2) if c["_mad"] > 0 else None
            lab = label_for(cell["percentile"])
            if not cell["partial"] and c.get("record_max") is not None and x >= c["record_max"] and x > 0 and c["n_years"] >= 5:
                lab = "Record high"
            cell["label"] = lab
        rows.append(cell)

    # annual totals (complete years only are used for ranking/trend)
    ann = m.filter(pl.col("ym").is_between(start, end)).group_by("year").agg(
        total=pl.col("value").sum(), valid=pl.col("value").count(), n_full=((pl.col("coverage") >= .999) & pl.col("value").is_not_null()).sum()).sort("year")
    annual = [{"year": r["year"], "total": _r(r["total"]) if r["valid"] else None, "complete": r["n_full"] == 12} for r in ann.iter_rows(named=True)]

    out = {"months": rows, "climatology": [{k: (_r(v) if isinstance(v, float) else v) for k, v in c.items() if not k.startswith("_")} for c in clim.values()],
           "annual": annual}
    out["current"] = current_status(m, clim, store)
    out["season"] = season(clim)
    out["trend"] = trend([a for a in annual if a["complete"]])
    out["rankings"] = rankings(rows, annual, clim)
    return out


def _r(v, nd: int = 1):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), nd)


def current_status(m: pl.DataFrame, clim: dict, store: DataStore) -> dict:
    def status(y: int, mo: int) -> dict | None:
        r = m.filter((pl.col("year") == y) & (pl.col("month") == mo))
        if r.is_empty() or r["value"][0] is None:
            return None
        r = r.row(0, named=True)
        c = clim[mo]
        cov = r["coverage"] or 0
        proj = r["projected"]
        d = {"year": y, "month": mo, "name": f"{month_abbr[mo]} {y}", "count": _r(r["value"]), "coverage": round(cov, 3),
             "partial": cov < 0.999, "projected": None, "baseline_n_years":c["n_years"],
             "median": _r(c["median"]), "p25": _r(c.get("p25")), "p75": _r(c.get("p75")), "p90": _r(c.get("p90"))}
        if c["median"] is not None and cov >= .999:
            d["percent_of_median"] = round(proj / c["median"] * 100, 1) if c["median"] > 0 else None
            d["percentile"] = _r(percentile_rank(c["_v"], proj), 1)
            d["label"] = label_for(d["percentile"])
            d["difference"] = _r(proj - c["median"])
        if cov < 0.999:
            d["note"] = f"Partial month: partition span covers {cov*100:.0f}% of calendar days. Count is not extrapolated; full-month anomaly is withheld. This is not satellite viewing coverage."
        return d

    ly, lm = store.latest
    cy, cm = store.latest_complete
    return {"latest": status(ly, lm), "latest_complete": status(cy, cm)}


def season(clim: dict) -> dict:
    med = np.array([clim[m]["median"] or 0 for m in range(1, 13)], float)
    if med.sum() <= 0:
        med = np.array([clim[m].get("mean") or 0 for m in range(1, 13)], float)
    if med.sum() <= 0:
        return {"has_season": False}
    peak = int(np.argmax(med))
    active = med >= 0.2 * med[peak]
    s = e = peak
    while active[(s - 1) % 12] and (s - 1) % 12 != peak:
        s -= 1
    while active[(e + 1) % 12] and (e + 1) % 12 != (s % 12):
        e += 1
    months = [(i % 12) + 1 for i in range(s, e + 1)]
    crit_th = np.quantile(med, 0.75)
    critical = [i + 1 for i in range(12) if med[i] >= crit_th and med[i] > 0]
    return {"has_season": True, "peak_month": peak + 1, "start_month": months[0], "end_month": months[-1],
            "season_months": months, "critical_months": critical,
            "share_of_annual_in_season_pct": round(float(med[[x - 1 for x in months]].sum() / med.sum() * 100), 1),
            "method": "Months with climatological median >= 20% of the peak month (contiguous around the peak); critical = top quartile months."}


def trend(annual: list[dict]) -> dict:
    a = [x for x in annual if x["total"] is not None]
    if len(a) < 8:
        return {"available": False, "reason": "Need at least 8 complete years"}
    yrs = np.array([x["year"] for x in a], float)
    tot = np.array([x["total"] for x in a], float)
    tau, p = stats.kendalltau(yrs, tot)
    if not np.isfinite(p):
        tau, p = 0., 1.
    slope, intercept, lo, hi = stats.theilslopes(tot, yrs)
    mean = tot.mean()
    recent = tot[-5:].mean()
    return {"available": True, "years": [int(yrs[0]), int(yrs[-1])], "mann_kendall_tau": round(float(tau), 3), "p_value": round(float(p), 4),
            "significant": bool(p < 0.05), "sen_slope_per_year": round(float(slope), 2), "slope_ci95": [round(float(lo), 2), round(float(hi), 2)],
            "pct_per_year": round(float(slope / mean * 100), 2) if mean > 0 else None,
            "intercept": float(intercept),
            "direction": "increasing" if slope > 0 and p < 0.05 else "decreasing" if slope < 0 and p < 0.05 else "no significant trend",
            "recent_5yr_mean": round(float(recent), 1), "long_term_mean": round(float(mean), 1)}


def rankings(rows: list[dict], annual: list[dict], clim: dict) -> dict:
    full = [r for r in rows if not r["partial"] and r["value"] is not None]
    top_m = sorted(full, key=lambda r: -r["value"])[:5]
    top_y = sorted([a for a in annual if a["complete"] and a["total"] is not None], key=lambda a: -a["total"])[:5]
    busiest = sorted([c for c in clim.values() if c["median"] is not None], key=lambda c: -(c.get("mean") or 0))[:3]
    return {"top_months": [{"year": r["year"], "month": r["month"], "value": r["value"]} for r in top_m],
            "top_years": [{"year": a["year"], "total": a["total"]} for a in top_y],
            "busiest_calendar_months": [{"month": c["month"], "name": c["name"], "mean": _r(c["mean"])} for c in busiest]}


# =============================================================================== forecast
def nb_dist(mu: float, alpha: float):
    mu = max(mu, 1e-6)
    n = 1 / max(alpha, 1e-6)
    return stats.nbinom(n, n / (n + mu))


def forecast(store: DataStore, weights: pl.DataFrame, eff_cells: float, monthly: pl.DataFrame, b0: int, b1: int) -> dict:
    source_version = store.fc_report.get("harmonization_version")
    if store.harm_report.get("method_version", "").startswith("harm-v3") and (source_version != store.harm_report.get("method_version") or store.fc_report.get("harmonization_built_at") != store.harm_report.get("built_at")):
        return {"available":False,"reason":"Forecast was trained with an older harmonization. Rebuild the forecast step."}
    if store.forecasts is None or not store.fc_report:
        return {"available": False, "reason": "Forecast model not trained - run the pipeline forecast step"}
    rep = store.fc_report
    f = store.forecasts.join(weights, on="cell", how="inner").group_by("lead", "target_year", "target_month").agg(
        mean=(pl.col("mean") * pl.col("w")).sum()).sort("lead")
    nc = rep["alpha_ncells"]
    out = []
    for r in f.iter_rows(named=True):
        L = str(r["lead"])
        al = rep["nb_alpha"][L]
        alpha = _interp_log(max(eff_cells, 1), [(1, al["cell"]), (nc["region"], al["region"]), (nc["country"], al["country"])])
        hist = monthly.filter((pl.col("month") == r["target_month"]) & pl.col("year").is_between(b0, b1)
                              & (pl.col("coverage") >= 0.999) & pl.col("harmonized").is_not_null())["harmonized"].to_numpy()

        # Back-tested skill for this kind of area (region-sized, or the whole country if that is the AOI)
        lvl = "country" if eff_cells >= max(nc["country"]*.99, store.cells.height*.99) else "region"
        sk = rep["skill"][L][lvl]
        countries = weights.join(store.cells.select("cell", "country"), on="cell")["country"].unique().to_list()
        if lvl == "country" and len(countries) == 1 and countries[0] in rep["skill"][L].get("by_country", {}):
            sk, lvl = rep["skill"][L]["by_country"][countries[0]], f"{lvl} ({countries[0].replace('_', ' ')})"
        skillful = bool((sk.get("crpss") or 0) > 0)

        e = {"lead": r["lead"], "target_year": r["target_year"], "target_month": r["target_month"],
             "target": f"{month_abbr[r['target_month']]} {r['target_year']}",
             "skill": {"level": lvl, **sk}, "skillful": skillful}
        if not skillful and len(hist) >= 5:
            # Safety rule: the model did not beat climatology in back-tests here, so we show the
            # climatological outlook (the historical spread of this calendar month) instead.
            q10, q50, q90 = np.quantile(hist, [0.1, 0.5, 0.9])
            e.update(source="climatology", mean=_r(float(hist.mean())), p10=float(q10), p50=float(q50), p90=float(q90))
            terciles = {"below": 0.333, "near": 0.334, "above": 0.333}
        else:
            d = nb_dist(r["mean"], alpha)
            e.update(source="model", mean=_r(r["mean"]), p10=float(d.ppf(0.1)), p50=float(d.median()), p90=float(d.ppf(0.9)))
            terciles = None
        if len(hist) >= 5:
            lo, hi = np.quantile(hist, [1 / 3, 2 / 3])
            if terciles is None:
                pb, pa = float(d.cdf(np.floor(lo))), float(1 - d.cdf(np.floor(hi)))
                terciles = {"below": round(pb, 3), "near": round(max(0.0, 1 - pb - pa), 3), "above": round(pa, 3)}
            e.update(climatology_median=_r(float(np.median(hist))), tercile_bounds=[_r(lo), _r(hi)], terciles=terciles,
                     outlook=max(terciles, key=terciles.get))
        out.append(e)
    iy, im = map(int, rep["issue_month"].split("-"))
    today = date.today()
    stale = (today.year * 12 + today.month) - (iy * 12 + im) > 2
    return {"available": True, "experimental": True, "validation_note":rep.get("validation_note"), "model_version": rep["model_version"], "issued_from_data_through": rep["issue_month"],
            "stale": stale,
            "warning": ("Forecast is issued from the last complete month of data. Set FIRMS_MAP_KEY and re-run the pipeline "
                        "to bring the record (and forecast) up to date." if stale else None),
            "forecasts": out, "drivers": rep.get("drivers"), "top_features": dict(list(rep["feature_importance"].items())[:8])}
