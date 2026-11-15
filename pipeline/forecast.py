"""Step 4 - 1-3 month probabilistic hotspot forecasts.

Model: LightGBM (Tweedie) at **0.5deg region** level predicting the calibrated MODIS hotspot count for
target month t0+L (L = 1..3) from information available at the end of issue month t0:
  * fire history  - recent counts, 12-month total, same month last year, prior-years
                    climatology of the target month (strictly earlier years -> no leakage),
                    current anomaly, 2deg neighbourhood activity
  * NASA POWER    - root-zone soil wetness, precipitation (1 and 3-month), temperature,
                    humidity, wind anomalies (MERRA-2)
  * climate modes - ENSO (ONI) and IOD (DMI), lagged to what is published at issue time
  * seasonality, location, lead
The model is anchored on climatology through an offset:  log mu = log(climatology) + F(x),
so it learns departures from normal and falls back to climatology when drivers are silent.

Region forecasts are downscaled to 0.1deg cells by each cell's share of the region's
climatology for the target month, so any user AOI can be summed.

AOI distributions: negative binomial, over-dispersion alpha fitted by maximum likelihood on
back-test aggregates (cell / region / country). Evaluation: rolling-origin back-test with CRPS
skill vs climatology, tercile Brier skill score and MAE.

Usage:  python -m pipeline.forecast [--backtest-from 2023]
"""
from __future__ import annotations

import argparse
import json
import math
import os

import lightgbm as lgb
import numpy as np
import polars as pl
from scipy import stats

from pipeline.common import PROCESSED, cell_center, cell_ids, load_config

LEADS = (1, 2, 3)
FEATS = ["h0", "h1", "h2", "h12", "h_ly", "clim_t", "clim_0", "anom_0", "mac_h0", "mac_clim_t", "mac_anom_0",
         "GWETROOT_z", "GWETROOT", "GWETROOT_clim_t", "GWET6_z", "PREC1_z", "PREC3_z", "PREC6_z", "PREC12_z", "T2M_z", "T2M_MAX_z", "RH2M_z", "WS2M_z",
         "oni", "dmi", "moy_sin", "moy_cos", "lead", "lat", "lon", "ctry_code"]
PARAMS = dict(objective="tweedie", tweedie_variance_power=1.4, learning_rate=0.03, num_leaves=31,
              min_data_in_leaf=200, feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1,
              lambda_l2=10.0, verbose=-1, seed=11, num_threads=int(os.getenv("EMBERCAL_TRAIN_THREADS", "8")))
N_ROUNDS = 400
MACRO_DEG = 2.0
SCORE_SAMPLE = 25000  # region-level rows scored per lead (CRPS is computed exactly per row)


# =============================================================================== panel
def region_panel(cfg: dict) -> pl.DataFrame:
    """Dense region x month harmonized counts (null where the month is not fully observed)."""
    rdeg = cfg["grid"]["region_deg"]
    mc = pl.read_parquet(PROCESSED / "monthly_cells.parquet", columns=["country", "region_cell", "year", "month", "modis_harmonized"])
    h = mc.group_by("country", "region_cell", "year", "month").agg(h=pl.col("modis_harmonized").sum())
    meta = pl.read_parquet(PROCESSED / "monthly_meta.parquet").select("country", "year", "month", "coverage")
    regs = pl.read_parquet(PROCESSED / "cells.parquet").group_by("country", "region_cell").agg(ncell=pl.len())
    m = regs.join(meta, on="country").join(h, on=["country", "region_cell", "year", "month"], how="left")
    m = m.with_columns(h=pl.when(pl.col("coverage") >= 0.999).then(pl.col("h").fill_null(0)).otherwise(None))
    lat, lon = cell_center(m["region_cell"].to_numpy(), rdeg)
    m = m.with_columns(lat=pl.Series(lat), lon=pl.Series(lon), macro=pl.Series(cell_ids(lat, lon, MACRO_DEG)),
                       t=pl.col("year") * 12 + pl.col("month") - 1,
                       ctry_code=pl.col("country").rank("dense").cast(pl.Int32)).sort("region_cell", "t")
    return m


def build_panel(cfg: dict):
    m = region_panel(cfg)
    # prior-years climatology for each (region, month): mean of h in strictly earlier years
    hz, hn = pl.col("h").fill_null(0), pl.col("h").is_not_null().cast(pl.Int32)
    m = m.with_columns(_cs=hz.cum_sum().over("region_cell", "month") - hz, _cn=hn.cum_sum().over("region_cell", "month") - hn)
    m = m.with_columns(clim_prior=pl.when(pl.col("_cn") >= 3).then(pl.col("_cs") / pl.col("_cn")).otherwise(None)).drop("_cs", "_cn")
    mac = m.group_by("macro", "t").agg(mac_h=pl.col("h").sum(), mac_clim=pl.col("clim_prior").sum(),
                                       mac_n=pl.len(), _obs=pl.col("h").is_not_null().sum())
    mac = mac.with_columns(mac_h=pl.when(pl.col("_obs") > 0).then(pl.col("mac_h")).otherwise(None)).drop("_obs")
    m = m.join(mac, on=["macro", "t"], how="left")

    pw = pl.read_parquet(PROCESSED / "drivers_power.parquet").with_columns(t=pl.col("year") * 12 + pl.col("month") - 1)
    pw = pw.select("region_cell", "t", "month", "GWETROOT", "GWETROOT_z", "GWETROOT_clim", pl.col("PRECTOTCORR_z").alias("PREC1_z"),
                   "PREC3_z", "PREC6_z", "PREC12_z", "GWET6_z", "T2M_z", "T2M_MAX_z", "RH2M_z", "WS2M_z")
    gw_clim = pw.group_by("region_cell", "month").agg(GWETROOT_clim_t=pl.col("GWETROOT_clim").first())
    modes = pl.read_parquet(PROCESSED / "drivers_modes.parquet").with_columns(t=pl.col("year") * 12 + pl.col("month") - 1)
    full_t = pl.DataFrame({"t": np.arange(modes["t"].min(), m["t"].max() + 2)})
    modes = full_t.join(modes.select("t", "oni", "dmi"), on="t", how="left").sort("t").with_columns(
        pl.col("oni").forward_fill(), pl.col("dmi").forward_fill())
    modes = modes.with_columns(t=pl.col("t") + 1)  # published with ~1 month delay -> value of t0-1 known at t0
    return m, pw.drop("month", "GWETROOT_clim"), gw_clim, modes


def make_rows(m, pw, gw_clim, modes, leads=LEADS, issue_ts: list[int] | None = None) -> pl.DataFrame:
    """One row per (region, issue month t0, lead) with features at t0 and target at t0+lead."""
    base = m.with_columns(
        h0=pl.col("h"), h1=pl.col("h").shift(1).over("region_cell"), h2=pl.col("h").shift(2).over("region_cell"),
        h12=pl.col("h").rolling_sum(12, min_samples=9).over("region_cell"), clim_0=pl.col("clim_prior"), mac_h0=pl.col("mac_h"),
    ).with_columns(anom_0=(pl.col("h0") + 0.5) / (pl.col("clim_0") + 0.5),
                   mac_anom_0=(pl.col("mac_h0") + 0.5) / (pl.col("mac_clim") + 0.5))
    issue = base.filter(pl.col("t").is_in(issue_ts)) if issue_ts is not None else base
    issue = issue.select("country", "region_cell", "macro", "lat", "lon", "ctry_code", pl.col("t").alias("t0"),
                         "h0", "h1", "h2", "h12", "clim_0", "anom_0", "mac_h0", "mac_anom_0")
    issue = issue.join(pw, left_on=["region_cell", "t0"], right_on=["region_cell", "t"], how="left")
    issue = issue.join(modes, left_on="t0", right_on="t", how="left")
    tgt = base.select("region_cell", "t", "h", "clim_prior", "mac_clim", "mac_n")
    out = []
    for L in leads:
        r = issue.with_columns(lead=pl.lit(L, pl.Int32), tt=pl.col("t0") + L)
        r = r.join(tgt.rename({"t": "tt", "h": "y", "clim_prior": "clim_t", "mac_clim": "mac_clim_t", "mac_n": "mac_n_t"}),
                   on=["region_cell", "tt"], how="left")
        r = r.join(tgt.select("region_cell", (pl.col("t") + 12).alias("tt"), pl.col("h").alias("h_ly")), on=["region_cell", "tt"], how="left")
        r = r.with_columns(t_month=(pl.col("tt") % 12) + 1, t_year=pl.col("tt") // 12)
        r = r.join(gw_clim, left_on=["region_cell", "t_month"], right_on=["region_cell", "month"], how="left")
        r = r.with_columns(moy_sin=(2 * math.pi * pl.col("t_month") / 12).sin(), moy_cos=(2 * math.pi * pl.col("t_month") / 12).cos())
        out.append(r)
    return pl.concat(out, how="diagonal_relaxed")


def offset(rows: pl.DataFrame) -> np.ndarray:
    """log of the prior-years climatology of the target month (region blended 80/20 with its
    2deg neighbourhood average) - the model learns multiplicative departures from it."""
    mac_pc = (rows["mac_clim_t"].fill_null(0) / rows["mac_n_t"].fill_null(1).clip(1, None)).to_numpy().astype(float)
    c = rows["clim_t"].to_numpy().astype(float)
    c = np.where(np.isnan(c), mac_pc, c)
    return np.log(0.8 * c + 0.2 * mac_pc + 0.02)


def train(rows: pl.DataFrame) -> lgb.Booster:
    X = rows.select(FEATS).to_numpy().astype(np.float64)
    ds = lgb.Dataset(X, rows["y"].to_numpy(), init_score=offset(rows), categorical_feature=[FEATS.index("ctry_code")], free_raw_data=True)
    return lgb.train(PARAMS, ds, N_ROUNDS)


def predict(model: lgb.Booster, rows: pl.DataFrame) -> np.ndarray:
    raw = model.predict(rows.select(FEATS).to_numpy().astype(np.float64), raw_score=True)
    return np.exp(raw + offset(rows))


# =============================================================================== scoring
def nb(mu: float, alpha: float):
    mu = max(mu, 1e-6)
    n = 1.0 / max(alpha, 1e-6)
    return stats.nbinom(n, n / (n + mu))


def crps_nb(mu: float, alpha: float, y: float) -> float:
    d = nb(mu, alpha)
    K = int(max(y, d.ppf(0.9995))) + 1
    k = np.arange(K + 1)
    return float(np.sum((d.cdf(k) - (k >= y)) ** 2))


def crps_ens(sample: np.ndarray, y: float) -> float:
    s = np.asarray(sample, float)
    return float(np.mean(np.abs(s - y)) - 0.5 * np.mean(np.abs(s[:, None] - s[None, :])))


def fit_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    """Negative-binomial over-dispersion by maximum likelihood (robust to outliers)."""
    keep = mu > 0.05
    y, mu = y[keep], mu[keep]
    if len(y) > 6000:
        idx = np.random.default_rng(0).choice(len(y), 6000, replace=False)
        y, mu = y[idx], mu[idx]
    yi = np.round(y)
    grid = np.exp(np.linspace(np.log(0.01), np.log(10), 60))
    ll = [np.sum(stats.nbinom.logpmf(yi, 1 / a, (1 / a) / (1 / a + mu))) for a in grid]
    return float(grid[int(np.argmax(ll))])


def score_level(bt: pl.DataFrame, hist: pl.DataFrame, keys: list[str], alpha: float, sample: int | None = None) -> dict:
    """Aggregate back-test rows to `keys` and compute skill vs climatology (prior years only)."""
    agg = bt.group_by(keys + ["lead", "t_year", "t_month"]).agg(pl.col("y").sum(), pl.col("p").sum())
    if sample and agg.height > sample:
        agg = agg.sample(sample, seed=3)
    hk = hist.group_by(keys + ["year", "month"]).agg(pl.col("h").sum())
    hist_map: dict = {}
    for row in hk.iter_rows(named=True):
        hist_map.setdefault(tuple(row[k] for k in keys) + (row["month"],), []).append((row["year"], row["h"]))
    c_m = c_c = br_m = br_c = ae_m = ae_c = 0.0
    n = 0
    for r in agg.iter_rows(named=True):
        past = [v for (yy, v) in hist_map.get(tuple(r[k] for k in keys) + (r["t_month"],), []) if yy < r["t_year"] and v is not None]
        if len(past) < 6:
            continue
        past = np.array(past, float)
        y, mu = r["y"], r["p"]
        c_m += crps_nb(mu, alpha, y)
        c_c += crps_ens(past, y)
        lo, hi = np.quantile(past, [1 / 3, 2 / 3])
        d = nb(mu, alpha)
        pb, pa = d.cdf(np.floor(lo)), 1 - d.cdf(np.floor(hi))
        probs = np.array([pb, 1 - pb - pa, pa])
        ref = np.array([np.mean(past <= lo), np.mean((past > lo) & (past <= hi)), np.mean(past > hi)])
        obs = np.array([y <= lo, lo < y <= hi, y > hi], float)
        br_m += np.sum((probs - obs) ** 2)
        br_c += np.sum((ref - obs) ** 2)
        ae_m += abs(d.median() - y)
        ae_c += abs(np.median(past) - y)
        n += 1
    return {"n": n, "crpss": round(float(1 - c_m / c_c), 4) if c_c else None,
            "brier_skill_terciles": round(float(1 - br_m / br_c), 4) if br_c else None,
            "mae_model": round(float(ae_m / max(n, 1)), 2), "mae_climatology": round(float(ae_c / max(n, 1)), 2)}


def cell_shares(years: tuple[int, int]) -> pl.DataFrame:
    """Each 0.1deg cell's share of its region's mean harmonized count per calendar month."""
    mc = pl.read_parquet(PROCESSED / "monthly_cells.parquet", columns=["country", "cell", "region_cell", "year", "month", "modis_harmonized"])
    meta = pl.read_parquet(PROCESSED / "monthly_meta.parquet")
    full = meta.filter((pl.col("coverage") >= 0.999) & pl.col("year").is_between(*years)).group_by("country", "month").agg(ny=pl.len())
    cells = pl.read_parquet(PROCESSED / "cells.parquet").select("country", "cell", "region_cell")
    cm = cells.join(pl.DataFrame({"month": list(range(1, 13))}), how="cross")
    s = mc.filter(pl.col("year").is_between(*years)).group_by("cell", "month").agg(s=pl.col("modis_harmonized").sum())
    cm = cm.join(s, on=["cell", "month"], how="left").join(full, on=["country", "month"], how="left")
    cm = cm.with_columns(c=(pl.col("s").fill_null(0) / pl.col("ny").fill_null(1)))
    tot = cm.group_by("region_cell", "month").agg(tot=pl.col("c").sum(), n=pl.len())
    cm = cm.join(tot, on=["region_cell", "month"])
    return cm.select("country", "cell", "region_cell", "month",
                     share=pl.when(pl.col("tot") > 0).then(pl.col("c") / pl.col("tot")).otherwise(1.0 / pl.col("n")))


# =============================================================================== main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest-from", type=int, default=2023, help="first back-test year (2023 -> tests 2023-2025)")
    args = ap.parse_args()
    cfg = load_config()
    m, pw, gw_clim, modes = build_panel(cfg)
    rows = make_rows(m, pw, gw_clim, modes).filter(pl.col("t_year") >= 2003)
    lab = rows.filter(pl.col("y").is_not_null() & pl.col("h0").is_not_null())
    last_full_t = int(m.filter(pl.col("h").is_not_null())["t"].max())
    print(f"Regions {m['region_cell'].n_unique():,}; training rows {lab.height:,}; "
          f"last complete month {last_full_t // 12}-{last_full_t % 12 + 1:02d}", flush=True)

    # ---- rolling-origin back-test ---------------------------------------------------------------
    bt_parts = []
    last_year = int(lab["t_year"].max())
    for Y in range(args.backtest_from, last_year + 1):
        tr, te = lab.filter(pl.col("tt") < Y * 12), lab.filter(pl.col("t_year") == Y)
        bt_parts.append(te.with_columns(p=pl.Series(predict(train(tr), te))))
        print(f"  back-test {Y}: train={tr.height:,} test={te.height:,}", flush=True)
    bt = pl.concat(bt_parts)

    cells = pl.read_parquet(PROCESSED / "cells.parquet")
    ncells = {"cell": 1.0, "region": float(cells.group_by("region_cell").agg(n=pl.len())["n"].median()),
              "country": float(cells.group_by("country").agg(n=pl.len())["n"].median())}
    hist = m.filter(pl.col("h").is_not_null())
    # cell-level dispersion: downscale back-test region forecasts with pre-back-test shares
    shares_bt = cell_shares((2003, args.backtest_from - 1))
    rng_regions = bt["region_cell"].unique().sample(min(400, bt["region_cell"].n_unique()), seed=5)
    mc = pl.read_parquet(PROCESSED / "monthly_cells.parquet", columns=["cell", "year", "month", "modis_harmonized"])
    alphas, skill = {}, {}
    for L in LEADS:
        b = bt.filter(pl.col("lead") == L)
        al = {}
        for lvl, keys in [("region", ["region_cell"]), ("country", ["country"])]:
            a = b.group_by(keys + ["t_year", "t_month"]).agg(pl.col("y").sum(), pl.col("p").sum())
            al[lvl] = round(fit_alpha(a["y"].to_numpy(), a["p"].to_numpy()), 4)
        cb = b.filter(pl.col("region_cell").is_in(rng_regions.implode())).select("region_cell", "t_year", "t_month", "p")
        cb = cb.join(shares_bt, left_on=["region_cell", "t_month"], right_on=["region_cell", "month"])
        cb = cb.join(mc, left_on=["cell", "t_year", "t_month"], right_on=["cell", "year", "month"], how="left")
        al["cell"] = round(fit_alpha(cb["modis_harmonized"].fill_null(0).to_numpy(), (cb["p"] * cb["share"]).to_numpy()), 4)
        alphas[str(L)] = al
        skill[str(L)] = {"region": score_level(b, hist, ["region_cell"], al["region"], SCORE_SAMPLE),
                         "country": score_level(b, hist, ["country"], al["country"])}
        skill[str(L)]["by_country"] = {c: score_level(b.filter(pl.col("country") == c), hist.filter(pl.col("country") == c),
                                                      ["country"], al["country"]) for c in sorted(b["country"].unique().to_list())}
        print(f"  lead {L}: alpha={al} skill region={skill[str(L)]['region']['crpss']} country={skill[str(L)]['country']['crpss']} "
              f"by_country={ {c: v['crpss'] for c, v in skill[str(L)]['by_country'].items()} }", flush=True)

    hind = {}
    for L in LEADS:
        b = bt.filter(pl.col("lead") == L).group_by("country", "t_year", "t_month").agg(pl.col("y").sum(), pl.col("p").sum()).sort("country", "t_year", "t_month")
        for r in b.iter_rows(named=True):
            d = nb(r["p"], alphas[str(L)]["country"])
            hind.setdefault(r["country"], {}).setdefault(str(L), []).append(
                {"year": r["t_year"], "month": r["t_month"], "observed": round(r["y"], 1), "p10": float(d.ppf(0.1)),
                 "p50": float(d.median()), "p90": float(d.ppf(0.9)), "mean": round(r["p"], 1)})

    # ---- final model, live forecast, downscaled to cells ------------------------------------------
    final = train(lab)
    imp = final.feature_importance("gain")
    live = make_rows(m, pw, gw_clim, modes, issue_ts=[last_full_t])
    live = live.with_columns(mean=pl.Series(predict(final, live)))
    shares = cell_shares((2003, last_year))
    fc = live.select("region_cell", "t0", "lead", pl.col("t_year").alias("target_year"), pl.col("t_month").alias("target_month"), pl.col("mean").alias("rmean"))
    fc = fc.join(shares, left_on=["region_cell", "target_month"], right_on=["region_cell", "month"])
    fc = fc.select("country", "cell", "region_cell", "t0", "lead", "target_year", "target_month", mean=pl.col("rmean") * pl.col("share"))
    fc.write_parquet(PROCESSED / "forecasts.parquet")
    bt.select("country", "region_cell", "lead", "t_year", "t_month", "y", "p").write_parquet(PROCESSED / "forecast_backtest.parquet")

    report = {
        "model_version": "fc-lgbm-v3.1-calibrated-region",
        "target_series": "modis_harmonized",
        "harmonization_built_at": json.loads((PROCESSED / "harmonization_report.json").read_text()).get("built_at"),
        "validation_note":"Rolling-origin skill conditional on retrospectively calibrated history; not a fully prospective end-to-end validation.",
        "harmonization_version": json.loads((PROCESSED / "harmonization_report.json").read_text())["method_version"],
        "issue_month": f"{last_full_t // 12}-{last_full_t % 12 + 1:02d}",
        "leads": list(LEADS), "backtest_years": [args.backtest_from, last_year],
        "spatial_unit": "0.5deg region (downscaled to 0.1deg cells by climatological share)",
        "nb_alpha": alphas, "alpha_ncells": ncells, "skill": skill,
        "feature_importance": {f: round(float(v / imp.sum()), 4) for f, v in sorted(zip(FEATS, imp), key=lambda t: -t[1])},
        "hindcast": hind,
        "drivers": ["NASA POWER / MERRA-2: GWETROOT, PRECTOTCORR, T2M, T2M_MAX, RH2M, WS2M", "NOAA CPC ONI", "NOAA PSL DMI"],
    }
    (PROCESSED / "forecast_report.json").write_text(json.dumps(report, indent=2, default=float))
    print(f"Saved forecasts.parquet ({fc.height:,} cell rows, issued {report['issue_month']}) and forecast_report.json", flush=True)


if __name__ == "__main__":
    main()
