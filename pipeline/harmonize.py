"""Step 2 - Harmonize MODIS and VIIRS into one consistent monthly fire record.

THE PROBLEM
    MODIS (2000-) has 1 km pixels; VIIRS (2012-) has 375 m pixels. VIIRS sees smaller fires
    and splits big fires over more pixels, so for the same burning it reports several times
    more hotspots. Joining the two records naively creates a fake jump in January 2012.

THE IDEA
    From 2012 to 2025 both sensors watched the same fires. For every 0.1deg grid cell and
    month in that overlap we know what MODIS saw (the inputs) and what VIIRS saw (the target).
    We learn that relationship, check it on years the model never saw, and then apply it to
    2000-2011, when only MODIS existed. Result: every month is expressed in one unit,
    "VIIRS S-NPP-equivalent hotspots".

THE STEPS (functions below, in this order)
    1. count_detections()        hotspots  -> counts per cell, month, satellite, day/night
    2. sensor_availability()     which satellites were flying each month
       month_coverage()          how much of each month has data
    3. noaa20_to_snpp_ratios()   VIIRS reference: S-NPP, with NOAA-20/21 filling gaps
    4. Tables / build_rows()     dense cell x month rows with the model inputs ("features")
    5. GlobalRatio / EmpiricalBayesRatio / GradientBoosting   candidate models
    6. evaluate(), fit_uncertainty(), pettitt_test()          validation
    7. main()                    train -> compare on held-out years -> refit -> apply -> save

MEMORY
    Australia alone has ~33 million detections, so the full cell x month table (~26 million
    rows) is never built at once: rows are built per (country, 2 years). Training keeps every
    cell-month with a detection plus a fixed 15% sample of empty ones, re-weighted by 1/0.15 so
    sums stay unbiased. Only non-empty cell-months are saved.

REFERENCES
    Roman et al. (2024) "Continuity between NASA MODIS Collection 6.1 and VIIRS Collection 2
    land products", Remote Sensing of Environment 302:113963 - Aqua (13:30 overpass, like
    S-NPP) as the reference, NOAA-20 -> S-NPP cross-calibration, orbit-drift handling,
    time-dependent calibration check, per-region reporting.

Usage:  python -m pipeline.harmonize
"""
from __future__ import annotations

import gc
import json
import math
import os
from calendar import monthrange
from dataclasses import dataclass
from datetime import date

import duckdb
import lightgbm as lgb
import numpy as np
import polars as pl

from pipeline.common import DATA, PROCESSED, load_config

# ------------------------------------------------------------------------------- settings
Z80 = 1.2815515655446004   # z-score of the 90th percentile -> central 80% interval = mean +/- Z80 * sd
EMPTY_SAMPLE_SHARE = 0.15  # share of empty cell-months kept for training (each then counts 1/0.15 times)
MIN_STORED_VALUE = 0.01    # modelled cell-months below this many hotspots are not stored (treated as 0)
YEARS_PER_CHUNK = 2        # rows are built 2 years x 1 country at a time to limit memory

# satellite count columns: <satellite>_<d|n> = day / night detections
COUNT_COLUMNS = ["terra_d", "terra_n", "aqua_d", "aqua_n", "snpp_d", "snpp_n",
                 "n20_d", "n20_n", "n21_d", "n21_n"]

# What the models see for each cell-month (all known from MODIS, so usable for 2000-2011 too)
FEATURES_FULL = [
    "terra_d", "terra_n", "aqua_d", "aqua_n",   # MODIS detections, by satellite and day/night
    "modis_frp", "modis_frp_max",               # fire radiative power (MW): fire size / intensity
    "reg_terra", "reg_aqua",                    # detections in the surrounding 0.5deg region
    "ctry_terra_pc", "ctry_aqua_pc",            # country-wide activity per cell (how active the season is)
    "clim_cell", "clim_cell_moy",               # this cell's long-term MODIS fire rate (all months / this month)
    "moy_sin", "moy_cos",                       # month of year on a circle (Dec is next to Jan)
    "lat", "lon", "ctry_code",                  # location and country
]
# Before July 2002 only Terra existed -> a separate model without Aqua inputs
FEATURES_TERRA_ONLY = [
    "terra_d", "terra_n", "terra_frp", "terra_frp_max", "reg_terra", "ctry_terra_pc",
    "clim_cell_terra", "clim_cell_moy_terra", "moy_sin", "moy_cos", "lat", "lon", "ctry_code",
]


def hotspot_files() -> str:
    return str(PROCESSED / "hotspots" / "*.parquet").replace("\\", "/")


# =============================================================================== 1. counting
def count_detections() -> tuple[pl.DataFrame, date]:
    """Count quality-passed detections per (country, cell, month), split by satellite and
    day/night, plus MODIS fire radiative power. Only non-empty cell-months are returned."""
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")

    def count(sat: str, dn: str) -> str:
        return f"count(*) filter (where satellite='{sat}' and daynight='{dn}')::int"

    counts = con.sql(f"""
        select region as country, cell, region_cell, year::int as year, month::int as month,
               {count('Terra', 'D')} as terra_d,   {count('Terra', 'N')} as terra_n,
               {count('Aqua', 'D')} as aqua_d,     {count('Aqua', 'N')} as aqua_n,
               {count('S-NPP', 'D')} as snpp_d,    {count('S-NPP', 'N')} as snpp_n,
               {count('NOAA-20', 'D')} as n20_d,   {count('NOAA-20', 'N')} as n20_n,
               {count('NOAA-21', 'D')} as n21_d,   {count('NOAA-21', 'N')} as n21_n,
               coalesce(sum(frp) filter (where satellite='Terra'), 0)::float as terra_frp,
               coalesce(max(frp) filter (where satellite='Terra'), 0)::float as terra_frp_max,
               coalesce(sum(frp) filter (where sensor='MODIS'), 0)::float  as modis_frp,
               coalesce(max(frp) filter (where sensor='MODIS'), 0)::float  as modis_frp_max,
               coalesce(sum(frp) filter (where satellite='S-NPP'), 0)::float as snpp_frp
        from '{hotspot_files()}' where qc_pass group by all
    """).pl()
    last_date = con.sql(f"select max(acq_date) from '{hotspot_files()}'").fetchall()[0][0]
    return counts, last_date


# =============================================================================== 2. months & sensors
def sensor_availability(counts: pl.DataFrame) -> pl.DataFrame:
    """Which satellites delivered data in each month (pooled over all regions).
    S-NPP counts as available when it produced at least 30% as many detections as NOAA-20/21
    (it has gaps, e.g. no near-real-time data in late 2026)."""
    per_month = counts.group_by("year", "month").agg(
        snpp=(pl.col("snpp_d") + pl.col("snpp_n")).sum(),
        n20=(pl.col("n20_d") + pl.col("n20_n")).sum(),
        n21=(pl.col("n21_d") + pl.col("n21_n")).sum())
    yyyymm = pl.col("year") * 100 + pl.col("month")
    snpp_ok = (pl.col("snpp") >= 0.3 * pl.max_horizontal("n20", "n21")) | ((pl.col("snpp") > 0) & (yyyymm <= 201712))
    return per_month.select(
        "year", "month",
        terra_av=yyyymm >= 200011,                      # Terra MODIS from Nov 2000
        aqua_av=yyyymm >= 200207,                       # Aqua MODIS from Jul 2002
        snpp_av=(yyyymm >= 201201) & snpp_ok,           # S-NPP VIIRS from Jan 2012
        n20_av=(yyyymm >= 201801) & (pl.col("n20") > 0),
        n21_av=(yyyymm >= 202301) & (pl.col("n21") > 0),
    )


def month_coverage(last_date: date) -> pl.DataFrame:
    """Every month from Nov 2000 to the latest data, with the fraction of the month that has data
    (1.0 for archive months; recent months can be partial)."""
    manifest_path = DATA / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    archive_end = date.fromisoformat(manifest.get("firms_archive", {}).get("last_date", str(last_date)))
    recent_start = manifest.get("firms_recent", {}).get("coverage_start")
    recent_start = date.fromisoformat(recent_start) if recent_start else last_date

    rows, year, month = [], 2000, 11
    while (year, month) <= (last_date.year, last_date.month):
        n_days = monthrange(year, month)[1]
        first, last = date(year, month, 1), date(year, month, n_days)
        covered_days = 0
        for start, end in [(first, min(last, archive_end)), (max(first, recent_start), min(last, last_date))]:
            if end >= start:
                covered_days += (end - start).days + 1
        rows.append((year, month, round(min(covered_days, n_days) / n_days, 4)))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return pl.DataFrame(rows, schema={"year": pl.Int32, "month": pl.Int32, "coverage": pl.Float32}, orient="row")


# =============================================================================== 3. VIIRS reference
def noaa20_to_snpp_ratios(counts: pl.DataFrame, availability: pl.DataFrame, pseudo_count: float = 50) -> dict:
    """S-NPP / NOAA-20 detection ratio per country and day/night, learned where both flew.
    Small countries borrow from the global ratio:  r = (sum S-NPP + k*r_global) / (sum NOAA-20 + k).
    Used to express NOAA-20/21 counts in S-NPP units when S-NPP has no data."""
    both_months = availability.filter(pl.col("snpp_av") & pl.col("n20_av")).select("year", "month")
    both = counts.join(both_months, on=["year", "month"])
    ratios = {}
    for dn in ("d", "n"):
        global_ratio = both[f"snpp_{dn}"].sum() / max(both[f"n20_{dn}"].sum(), 1)
        per_country = both.group_by("country").agg(s=pl.col(f"snpp_{dn}").sum(), n=pl.col(f"n20_{dn}").sum())
        ratios[dn] = {"global": float(global_ratio)}
        for r in per_country.iter_rows(named=True):
            ratios[dn][r["country"]] = float((r["s"] + pseudo_count * global_ratio) / (r["n"] + pseudo_count))
    return ratios


# =============================================================================== 4. features
@dataclass
class Tables:
    """Everything needed to build model rows, computed once."""
    cells: pl.DataFrame          # country, cell, region_cell, lat, lon
    counts: pl.DataFrame         # sparse detection counts per cell-month
    months: pl.DataFrame         # year, month, coverage
    availability: pl.DataFrame   # satellites flying per month
    ratios: dict                 # NOAA-20 -> S-NPP
    region_activity: pl.DataFrame
    country_activity: pl.DataFrame
    cell_climatology: pl.DataFrame
    cell_month_climatology: pl.DataFrame
    country_codes: dict


def make_tables(counts: pl.DataFrame, last_date: date, feature_years: list[int] | None = None) -> Tables:
    cells = counts.group_by("country", "cell").agg(pl.col("region_cell").first()).with_columns(
        lat=((pl.col("cell") // 3600) * 0.1 - 90 + 0.05).cast(pl.Float32),
        lon=((pl.col("cell") % 3600) * 0.1 - 180 + 0.05).cast(pl.Float32))
    availability = sensor_availability(counts)
    c = counts.with_columns(terra=pl.col("terra_d") + pl.col("terra_n"), aqua=pl.col("aqua_d") + pl.col("aqua_n"))

    region_activity = c.group_by("region_cell", "year", "month").agg(
        reg_terra=pl.col("terra").sum().cast(pl.Float32), reg_aqua=pl.col("aqua").sum().cast(pl.Float32))
    n_cells = cells.group_by("country").agg(n=pl.len())
    country_activity = (c.group_by("country", "year", "month").agg(t=pl.col("terra").sum(), a=pl.col("aqua").sum())
                        .join(n_cells, on="country")
                        .select("country", "year", "month",
                                ctry_terra_pc=(pl.col("t") / pl.col("n")).cast(pl.Float32),
                                ctry_aqua_pc=(pl.col("a") / pl.col("n")).cast(pl.Float32)))

    # Long-term MODIS fire rate of each cell over the Aqua era. Empty months count as zero,
    # so the mean is (sum of detections) / (number of months), not the mean of non-empty rows.
    aqua_months = availability.filter(pl.col("aqua_av"))
    if feature_years is not None:
        aqua_months = aqua_months.filter(pl.col("year").is_in(feature_years))
    aqua_months = aqua_months.select("year", "month")
    n_aqua_months = aqua_months.height
    n_per_calendar_month = aqua_months.group_by("month").agg(n=pl.len())
    aqua_era = c.join(aqua_months, on=["year", "month"])
    cell_climatology = aqua_era.group_by("cell").agg(
        clim_cell=((pl.col("terra") + pl.col("aqua")).sum() / n_aqua_months).cast(pl.Float32),
        clim_cell_terra=(pl.col("terra").sum() / n_aqua_months).cast(pl.Float32))
    cell_month_climatology = (aqua_era.group_by("cell", "month").agg(s=(pl.col("terra") + pl.col("aqua")).sum(), st=pl.col("terra").sum())
                              .join(n_per_calendar_month, on="month")
                              .select("cell", "month",
                                      clim_cell_moy=(pl.col("s") / pl.col("n")).cast(pl.Float32),
                                      clim_cell_moy_terra=(pl.col("st") / pl.col("n")).cast(pl.Float32)))
    countries = sorted(cells["country"].unique().to_list())
    return Tables(cells, counts, month_coverage(last_date), availability, noaa20_to_snpp_ratios(counts.filter(pl.col("year").is_in(feature_years)) if feature_years is not None else counts, availability),
                  region_activity, country_activity, cell_climatology, cell_month_climatology,
                  {name: i + 1 for i, name in enumerate(countries)})


def build_rows(t: Tables, country: str, years: list[int]) -> pl.DataFrame:
    """Dense rows (every cell x every month) for one country and a few years, with
    the VIIRS target `y` (null where no VIIRS satellite flew) and all model features."""
    country_cells = t.cells.filter(pl.col("country") == country)
    months = t.months.filter(pl.col("year").is_in(years))
    counts = t.counts.filter((pl.col("country") == country) & pl.col("year").is_in(years)).drop("country", "region_cell")
    rows = country_cells.join(months, how="cross").join(counts, on=["cell", "year", "month"], how="left")
    rows = rows.with_columns(pl.col(COUNT_COLUMNS + ["modis_frp", "modis_frp_max", "snpp_frp", "terra_frp", "terra_frp_max"]).fill_null(0))
    rows = rows.join(t.availability, on=["year", "month"], how="left")

    # VIIRS target in S-NPP units: S-NPP if available, otherwise NOAA-20 or NOAA-21 x ratio
    for dn in ("d", "n"):
        ratio = t.ratios[dn].get(country, t.ratios[dn]["global"])
        rows = rows.with_columns(
            pl.when(pl.col("snpp_av")).then(pl.col(f"snpp_{dn}").cast(pl.Float32))
            .when(pl.col("n20_av")).then((pl.col(f"n20_{dn}") * ratio).cast(pl.Float32))
            .when(pl.col("n21_av")).then((pl.col(f"n21_{dn}") * ratio).cast(pl.Float32))
            .otherwise(None).alias(f"y_{dn}"))

    rows = rows.with_columns(
        y=pl.col("y_d") + pl.col("y_n"),
        terra=pl.col("terra_d") + pl.col("terra_n"),
        aqua=pl.col("aqua_d") + pl.col("aqua_n"),
        moy_sin=(2 * math.pi * pl.col("month") / 12).sin().cast(pl.Float32),
        moy_cos=(2 * math.pi * pl.col("month") / 12).cos().cast(pl.Float32),
        ctry_code=pl.lit(t.country_codes[country], pl.Int32),
        t=pl.col("year") * 12 + pl.col("month") - 1,        # month index, used for the sampling hash
    ).with_columns(modis=pl.col("terra") + pl.col("aqua"))

    rows = (rows.join(t.region_activity, on=["region_cell", "year", "month"], how="left")
                .join(t.country_activity, on=["country", "year", "month"], how="left")
                .join(t.cell_climatology, on="cell", how="left")
                .join(t.cell_month_climatology, on=["cell", "month"], how="left"))
    context = ["reg_terra", "reg_aqua", "ctry_terra_pc", "ctry_aqua_pc",
               "clim_cell", "clim_cell_terra", "clim_cell_moy", "clim_cell_moy_terra"]
    return rows.with_columns(pl.col(context).fill_null(0))


def is_empty() -> pl.Expr:
    """A cell-month where neither MODIS nor VIIRS detected anything."""
    return (pl.col("modis") == 0) & (pl.col("y") == 0)


def keep_for_training() -> pl.Expr:
    """All non-empty rows + a fixed pseudo-random 15% of empty rows.
    The hash of (cell, month) makes the sample reproducible."""
    pseudo_random_0_999 = (pl.col("cell").cast(pl.UInt64) * 2654435761 + pl.col("t").cast(pl.UInt64) * 40503) % 1000
    return ~is_empty() | (pseudo_random_0_999 < int(EMPTY_SAMPLE_SHARE * 1000))


def training_weight(drift_from: int, drift_weight: float) -> pl.Expr:
    """Weight = (1/0.15 for sampled empty rows, else 1) x (orbit-drift down-weighting for recent years)."""
    sample_w = pl.when(is_empty()).then(1 / EMPTY_SAMPLE_SHARE).otherwise(1.0)
    drift_w = pl.when(pl.col("year") >= drift_from).then(drift_weight).otherwise(1.0)
    return (sample_w * drift_w).cast(pl.Float32)


# =============================================================================== 5. models
class GlobalRatio:
    """Simplest harmonization: one multiplier for day and one for night.
    R_dn = sum(VIIRS_dn) / sum(MODIS_dn);  prediction = R_d * MODIS_d + R_n * MODIS_n."""

    def fit(self, rows: pl.DataFrame) -> "GlobalRatio":
        w = rows["w"]
        self.ratio = {dn: float((rows[f"y_{dn}"] * w).sum() / max(((rows[f"terra_{dn}"] + rows[f"aqua_{dn}"]) * w).sum(), 1))
                      for dn in ("d", "n")}
        return self

    def predict(self, rows: pl.DataFrame) -> np.ndarray:
        return sum(self.ratio[dn] * (rows[f"terra_{dn}"] + rows[f"aqua_{dn}"]).to_numpy() for dn in ("d", "n"))


class EmpiricalBayesRatio:
    """Every cell gets its own VIIRS:MODIS ratio, but cells with few fires borrow strength
    from their region, country and season (hierarchical shrinkage):

        VIIRS = b + R * MODIS                        (separately for day and night)
        R     = R_global * f_country,month * f_region * f_cell
        f     = (sum(VIIRS - b) + k) / (sum(R_parent * MODIS) + k)

    With few fires f -> 1 (use the parent's ratio); with many fires f -> the cell's own ratio.
    b is the "small fires MODIS misses entirely" baseline: mean VIIRS count where MODIS saw 0.
    """

    LEVELS = [("country_month", ["country", "month"]), ("region", ["region_cell"]), ("cell", ["cell"])]

    def __init__(self, k: float, terra_only: bool = False):
        self.k, self.terra_only, self.params = k, terra_only, {}

    def _modis(self, dn: str) -> pl.Expr:
        return pl.col(f"terra_{dn}") if self.terra_only else pl.col(f"terra_{dn}") + pl.col(f"aqua_{dn}")

    def fit(self, rows: pl.DataFrame) -> "EmpiricalBayesRatio":
        k = self.k
        for dn in ("d", "n"):
            d = rows.select("country", "cell", "region_cell", "month", "w",
                            m=self._modis(dn).cast(pl.Float64), v=pl.col(f"y_{dn}").cast(pl.Float64))
            # small-fire baseline b (country x month, then cell x month, both shrunk)
            missed = d.filter(pl.col("m") == 0)
            b_global = (missed["v"] * missed["w"]).sum() / max(missed["w"].sum(), 1e-9)
            b_country = missed.group_by("country", "month").agg(sv=(pl.col("v") * pl.col("w")).sum(), sw=pl.col("w").sum())
            b_country = b_country.select("country", "month", b_cm=(pl.col("sv") + 6 * b_global) / (pl.col("sw") + 6))
            b_cell = (missed.join(b_country, on=["country", "month"])
                      .group_by("cell", "month").agg(sv=(pl.col("v") * pl.col("w")).sum(), sw=pl.col("w").sum(), b_cm=pl.col("b_cm").first())
                      .select("cell", "month", b=(pl.col("sv") + 6 * pl.col("b_cm")) / (pl.col("sw") + 6)))
            d = (d.join(b_country, on=["country", "month"], how="left").join(b_cell, on=["cell", "month"], how="left")
                  .with_columns(b=pl.col("b").fill_null(pl.col("b_cm")).fill_null(b_global)))
            d = d.with_columns(excess=(pl.col("v") - pl.col("b")) * pl.col("w"), mw=pl.col("m") * pl.col("w"))
            seen = d.filter(pl.col("m") > 0)
            r_global = max(seen["excess"].sum() / max(seen["mw"].sum(), 1e-9), 0.1)
            factors, cur = {}, seen.with_columns(R=pl.lit(r_global))
            for name, keys in self.LEVELS:
                f = (cur.group_by(keys).agg(se=pl.col("excess").sum(), sp=(pl.col("R") * pl.col("mw")).sum())
                     .select(*keys, f=((pl.col("se") + k) / (pl.col("sp") + k)).clip(0.05, 20)))
                factors[name] = f
                cur = cur.join(f, on=keys, how="left").with_columns(R=pl.col("R") * pl.col("f").fill_null(1.0)).drop("f")
            self.params[dn] = {"b_global": b_global, "b_country": b_country, "b_cell": b_cell, "r_global": r_global, "factors": factors}
        return self

    def predict(self, rows: pl.DataFrame) -> np.ndarray:
        total = np.zeros(rows.height)
        for dn in ("d", "n"):
            p = self.params[dn]
            d = rows.select("country", "cell", "region_cell", "month", m=self._modis(dn).cast(pl.Float64)).with_row_index("_row")
            d = (d.join(p["b_country"], on=["country", "month"], how="left").join(p["b_cell"], on=["cell", "month"], how="left")
                  .with_columns(b=pl.col("b").fill_null(pl.col("b_cm")).fill_null(p["b_global"]), R=pl.lit(p["r_global"])))
            for name, keys in self.LEVELS:
                d = d.join(p["factors"][name], on=keys, how="left").with_columns(R=pl.col("R") * pl.col("f").fill_null(1.0)).drop("f")
            d = d.sort("_row")
            total += (d["b"] + d["R"] * d["m"]).to_numpy()
        return np.maximum(total, 0)

    def summary(self) -> dict:
        return {dn: {"global_ratio": round(p["r_global"], 3), "small_fire_baseline": round(p["b_global"], 4),
                     "cells_with_own_factor": p["factors"]["cell"].height} for dn, p in self.params.items()}


class GradientBoosting:
    """LightGBM: an ensemble of small decision trees whose sum is log(expected VIIRS count).

        log mu(x) = sum_t  eta * tree_t(x)

    Each new tree corrects the errors left by the previous ones. The loss is the Tweedie
    deviance (power 1.35), suited to counts that are mostly zero with a long tail of huge
    months. The number of trees is chosen by early stopping on the last training year.
    """

    PARAMS = dict(objective="tweedie", tweedie_variance_power=1.35, learning_rate=0.05, num_leaves=63,
                  min_data_in_leaf=60, feature_fraction=0.85, bagging_fraction=0.8, bagging_freq=1,
                  lambda_l2=1.0, max_bin=127, verbose=-1, seed=7, num_threads=int(os.getenv("EMBERCAL_TRAIN_THREADS", "8")))

    def __init__(self, features: list[str]):
        self.features, self.model = features, None

    def fit(self, rows: pl.DataFrame) -> "GradientBoosting":
        X = rows.select(self.features).to_numpy().astype(np.float32)
        y, w = rows["y"].to_numpy(), rows["w"].to_numpy()
        categorical = [self.features.index("ctry_code")]
        is_val = (rows["year"] == rows["year"].max()).to_numpy()
        train_set = lgb.Dataset(X[~is_val], y[~is_val], weight=w[~is_val], categorical_feature=categorical)
        val_set = lgb.Dataset(X[is_val], y[is_val], weight=w[is_val], reference=train_set)
        probe = lgb.train(self.PARAMS, train_set, 3000, valid_sets=[val_set], callbacks=[lgb.early_stopping(100, verbose=False)])
        self.n_trees = max(probe.best_iteration, 50)
        del train_set, val_set, probe
        # refit on all rows with the chosen number of trees
        self.model = lgb.train(self.PARAMS, lgb.Dataset(X, y, weight=w, categorical_feature=categorical), self.n_trees)
        return self

    def predict(self, rows: pl.DataFrame) -> np.ndarray:
        return np.maximum(self.model.predict(rows.select(self.features).to_numpy().astype(np.float32)), 0)

    def importance(self) -> dict:
        gain = self.model.feature_importance("gain")
        return {f: round(float(g / gain.sum()), 4) for f, g in sorted(zip(self.features, gain), key=lambda t: -t[1])}


class Ensemble:
    """Average of the empirical-Bayes and gradient-boosting predictions."""

    def __init__(self, eb: EmpiricalBayesRatio, gbm: GradientBoosting):
        self.eb, self.gbm = eb, gbm

    def predict(self, rows: pl.DataFrame) -> np.ndarray:
        return 0.5 * (self.eb.predict(rows) + self.gbm.predict(rows))


# =============================================================================== 6. validation
def evaluate(rows: pl.DataFrame, pred: np.ndarray) -> dict:
    """Errors at the scales users look at: cell-month, 0.5deg region-month, country-month, country-year."""
    d = rows.select("country", "region_cell", "year", "month", "y").with_columns(p=pl.Series(pred))
    y = d["y"].to_numpy().astype(float)
    poisson_dev = 2 * np.mean(np.where(y > 0, y * np.log(np.maximum(y, 1e-9) / np.maximum(pred, 1e-9)), 0) - (y - pred))
    region_month = d.group_by("region_cell", "year", "month").agg(pl.col("y").sum(), pl.col("p").sum())
    country_month = d.group_by("country", "year", "month").agg(pl.col("y").sum(), pl.col("p").sum())
    country_year = d.group_by("country", "year").agg(pl.col("y").sum(), pl.col("p").sum())
    active = country_month.filter(pl.col("y") >= 20)

    def log_corr(t: pl.DataFrame) -> float:
        return float(np.corrcoef(np.log1p(t["y"].to_numpy()), np.log1p(t["p"].to_numpy()))[0, 1])

    return {
        "cell_mae": round(float(np.mean(np.abs(y - pred))), 4),
        "cell_poisson_deviance": round(float(poisson_dev), 4),
        "region_month_mae": round(float((region_month["y"] - region_month["p"]).abs().mean()), 3),
        "region_month_r_log": round(log_corr(region_month), 4),
        "country_month_mape_pct": round(float(((active["p"] - active["y"]).abs() / active["y"]).mean() * 100), 2) if active.height else None,
        "country_month_r_log": round(log_corr(country_month), 4),
        "country_month_bias_pct": round(float((country_month["p"].sum() - country_month["y"].sum()) / country_month["y"].sum() * 100), 2),
        "country_year_abs_err_pct": round(float(((country_year["p"] - country_year["y"]).abs() / country_year["y"]).mean() * 100), 2),
    }


def selection_score(m: dict) -> float:
    """Lower is better: country-month % error + region-month error + |bias| (on held-out years)."""
    return (m["country_month_mape_pct"] or 0) / 10 + m["region_month_mae"] + abs(m["country_month_bias_pct"]) / 10


def fit_uncertainty(rows: pl.DataFrame, pred: np.ndarray) -> dict:
    """Variance of a modelled cell-month:  Var = phi*mu + alpha*mu^2  (fitted on held-out residuals).
    Errors of neighbouring cells are correlated, so summed variances understate area uncertainty;
    kappa inflates them so that 80% of held-out truths fall in the 80% interval (per scale)."""
    y = rows["y"].to_numpy().astype(float)
    coef, *_ = np.linalg.lstsq(np.column_stack([pred, pred ** 2]), (y - pred) ** 2, rcond=None)
    phi, alpha = max(coef[0], 1.0), max(coef[1], 0.0)
    d = rows.select("country", "region_cell", "year", "month", "y").with_columns(
        p=pl.Series(pred), v=pl.Series(phi * pred + alpha * pred ** 2))
    out = {"phi": round(float(phi), 4), "alpha": round(float(alpha), 5)}
    for scale, keys in [("region", ["region_cell", "year", "month"]), ("country", ["country", "year", "month"])]:
        a = d.group_by(keys).agg(pl.col("y").sum(), pl.col("p").sum(), pl.col("v").sum()).filter(pl.col("v") > 0)
        z = np.abs((a["y"] - a["p"]).to_numpy()) / np.sqrt(a["v"].to_numpy())
        out[f"kappa_{scale}"] = round(max(float(np.quantile(z, 0.8) / Z80), 1.0), 3)
    return out


def pettitt(x: np.ndarray) -> tuple[int, float, float]:
    """Pettitt change-point test.  U_t = sum_{i<=t} sum_{j>t} sign(x_i - x_j),  K = max|U_t|,
    p ~ 2 exp(-6K^2 / (n^3 + n^2)). Returns (index of change point, K, p-value)."""
    n = len(x)
    s = np.sign(x[:, None] - x[None, :])
    U = np.array([s[: t + 1, t + 1:].sum() for t in range(n - 1)])
    K = np.abs(U).max()
    return int(np.abs(U).argmax()), float(K), min(1.0, 2 * math.exp(-6 * K ** 2 / (n ** 3 + n ** 2)))


def sensor_switch_test(country_months: pl.DataFrame) -> dict:
    """Is there a jump at the 2012 MODIS->VIIRS switch? Pettitt test on the deseasonalised log
    monthly series 2005-2018, for (a) naive splice: MODIS before 2012, VIIRS after; (b) harmonized."""
    results = {}
    for country in country_months["country"].unique().sort().to_list():
        c = (country_months.filter((pl.col("country") == country) & pl.col("year").is_between(2005, 2018)).sort("year", "month")
             .with_columns(naive=pl.when(pl.col("year") < 2012).then(pl.col("modis")).otherwise(pl.col("y"))))
        years, months = c["year"].to_numpy(), c["month"].to_numpy()
        res = {}
        for name, col in [("naive", "naive"), ("harm", "harmonized")]:
            v = np.log1p(c[col].to_numpy().astype(float))
            seasonal = np.array([np.median(v[months == m]) for m in range(1, 13)])
            x = v - seasonal[months - 1]
            t, K, p = pettitt(x)
            res[name] = {"change_point": f"{years[t]}-{months[t]:02d}", "K": K, "p_value": round(p, 5),
                         "jump_at_2012_factor": round(float(math.exp(x[years >= 2012].mean() - x[years < 2012].mean())), 2)}
        results[country] = res
    return results


# =============================================================================== 7. main
def build_training_data(t: Tables, hc: dict) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Overlap years with Aqua + S-NPP and complete coverage.
    Held-out years: every row (honest evaluation). Training years: sampled rows + weights."""
    y0, y1 = hc["overlap_years"]
    weight = training_weight(hc["drift_downweight_from"], hc["drift_weight"])
    train_parts, holdout_parts = [], []
    # a sample of overlap years is enough to learn the relationship; held-out years are always kept
    sampled = hc.get("training_years") or list(range(y0, y1 + 1))
    years = sorted(set(sampled) | set(hc["holdout_years"]))
    for country in t.country_codes:
        for i in range(0, len(years), YEARS_PER_CHUNK):
            rows = build_rows(t, country, years[i:i + YEARS_PER_CHUNK])
            rows = rows.filter(pl.col("aqua_av") & pl.col("snpp_av") & (pl.col("coverage") >= 0.999))
            holdout = pl.col("year").is_in(hc["holdout_years"])
            holdout_parts.append(rows.filter(holdout).with_columns(w=pl.lit(1.0, pl.Float32)))
            train_parts.append(rows.filter(~holdout & keep_for_training()).with_columns(w=weight))
    return pl.concat(train_parts), pl.concat(holdout_parts)


def legacy_main() -> None:
    cfg = load_config()
    hc = cfg["harmonization"]

    print("1-3. Counting detections, sensors, coverage ...", flush=True)
    counts, last_date = count_detections()
    t = make_tables(counts, last_date)
    countries = list(t.country_codes)
    print(f"     {counts.height:,} non-empty cell-months, {t.cells.height:,} cells, {countries}, data to {last_date}", flush=True)

    print(f"4. Building training rows (training years {hc.get('training_years')}, held-out {hc['holdout_years']}) ...", flush=True)
    train, holdout = build_training_data(t, hc)
    gc.collect()
    print(f"     training rows {train.height:,} (sampled), held-out rows {holdout.height:,} (all)", flush=True)

    print("5-6. Fitting candidate models and scoring on held-out years ...", flush=True)
    k = hc["shrinkage_k"]
    eb, gbm = EmpiricalBayesRatio(k).fit(train), GradientBoosting(FEATURES_FULL).fit(train)
    candidates = {"global_ratio": GlobalRatio().fit(train), "stratified_eb": eb, "gbm": gbm, "ensemble": Ensemble(eb, gbm)}
    preds = {"naive_modis": holdout["modis"].to_numpy().astype(float)}
    preds.update({name: m.predict(holdout) for name, m in candidates.items()})
    metrics = {name: evaluate(holdout, p) for name, p in preds.items()}
    for name, m in metrics.items():
        print(f"     {name:14s} country-year err {m['country_year_abs_err_pct']}%  bias {m['country_month_bias_pct']}%  "
              f"region-month MAE {m['region_month_mae']}", flush=True)
    metrics_by_country = {c: {n: evaluate(holdout.filter(pl.col("country") == c), preds[n][(holdout["country"] == c).to_numpy()])
                              for n in ("naive_modis", "global_ratio", "gbm")} for c in countries}

    eb_t, gbm_t = EmpiricalBayesRatio(k, terra_only=True).fit(train), GradientBoosting(FEATURES_TERRA_ONLY).fit(train)
    terra_models = {"stratified_eb": eb_t, "gbm": gbm_t, "ensemble": Ensemble(eb_t, gbm_t)}
    terra_preds = {n: m.predict(holdout) for n, m in terra_models.items()}
    terra_metrics = {n: evaluate(holdout, p) for n, p in terra_preds.items()}

    choices = ["stratified_eb", "gbm", "ensemble"]
    best = min(choices, key=lambda n: selection_score(metrics[n]))
    best_terra = min(choices, key=lambda n: selection_score(terra_metrics[n]))
    print(f"     selected: {best} (Terra-only period: {best_terra})", flush=True)
    uncertainty = {"full": fit_uncertainty(holdout, preds[best]), "terra_only": fit_uncertainty(holdout, terra_preds[best_terra])}

    # Calibration-by-year check (Roman et al. 2024): observed / predicted VIIRS per year should stay ~1
    calibration = []
    for name, rows, p in [("train", train, gbm.predict(train)), ("holdout", holdout, preds["gbm"])]:
        g = rows.select("year", "y", "w").with_columns(p=pl.Series(p)).group_by("year").agg(
            obs=(pl.col("y") * pl.col("w")).sum(), pred=(pl.col("p") * pl.col("w")).sum())
        calibration += [{"year": r["year"], "obs_over_pred": round(r["obs"] / max(r["pred"], 1e-9), 4), "set": name}
                        for r in g.iter_rows(named=True)]
    calibration.sort(key=lambda r: r["year"])
    yrs = np.array([r["year"] for r in calibration], float)
    calibration_trend = float(np.polyfit(yrs - yrs.mean(), [r["obs_over_pred"] for r in calibration], 1)[0])
    raw_ratio = (t.counts.join(t.availability.filter(pl.col("snpp_av")).select("year", "month"), on=["year", "month"])
                 .group_by("year").agg(v=(pl.col("snpp_d") + pl.col("snpp_n")).sum(), a=(pl.col("aqua_d") + pl.col("aqua_n")).sum()).sort("year"))

    print("7. Refitting the selected models on all overlap years ...", flush=True)
    weight = training_weight(hc["drift_downweight_from"], hc["drift_weight"])
    all_overlap = pl.concat([train, holdout.filter(keep_for_training()).with_columns(w=weight)])
    del train, holdout
    gc.collect()

    def refit(choice: str, features: list[str], terra_only: bool):
        e = EmpiricalBayesRatio(k, terra_only).fit(all_overlap) if choice != "gbm" else None
        g = GradientBoosting(features).fit(all_overlap) if choice != "stratified_eb" else None
        return {"stratified_eb": e, "gbm": g}.get(choice) or Ensemble(e, g)

    final_full = refit(best, FEATURES_FULL, False)
    final_terra = refit(best_terra, FEATURES_TERRA_ONLY, True)
    del all_overlap
    gc.collect()

    print("   Applying to every cell-month 2000-today ...", flush=True)
    stored, country_month_totals = [], []
    all_years = sorted(t.months["year"].unique().to_list())
    for country in countries:
        for i in range(0, len(all_years), YEARS_PER_CHUNK):
            rows = build_rows(t, country, all_years[i:i + YEARS_PER_CHUNK])
            if rows.is_empty():
                continue
            aqua = rows["aqua_av"].to_numpy()
            p_full, p_terra = final_full.predict(rows), final_terra.predict(rows)
            modis_harmonized = np.where(aqua, p_full, p_terra)          # MODIS converted to VIIRS units
            u_full, u_terra = uncertainty["full"], uncertainty["terra_only"]
            variance = np.where(aqua, u_full["phi"] * p_full + u_full["alpha"] * p_full ** 2,
                                u_terra["phi"] * p_terra + u_terra["alpha"] * p_terra ** 2)
            has_viirs = rows["y"].is_not_null().to_numpy()
            no_data = (rows["coverage"] <= 0).to_numpy()
            # final series: observed VIIRS where it exists, converted MODIS before 2012
            harmonized = np.where(no_data, np.nan, np.where(has_viirs, rows["y"].fill_null(0).to_numpy(), modis_harmonized))
            rows = rows.with_columns(
                modis_harmonized=pl.Series(np.where(no_data, np.nan, modis_harmonized), dtype=pl.Float32).fill_nan(None),
                harmonized=pl.Series(harmonized, dtype=pl.Float32).fill_nan(None),
                harm_var=pl.Series(np.where(no_data | has_viirs, 0.0, variance), dtype=pl.Float32),
                viirs_ref=pl.col("y"),
                snpp=pl.col("snpp_d") + pl.col("snpp_n"), n20=pl.col("n20_d") + pl.col("n20_n"), n21=pl.col("n21_d") + pl.col("n21_n"))
            country_month_totals.append(rows.filter(pl.col("coverage") > 0).group_by("country", "year", "month").agg(
                modis=pl.col("modis").sum(), y=pl.col("y").fill_null(0).sum(), harmonized=pl.col("harmonized").sum()))
            worth_storing = (pl.col("harmonized") >= MIN_STORED_VALUE) | (pl.col("modis") > 0) | (pl.col("viirs_ref").fill_null(0) > 0)
            stored.append(rows.filter(pl.col("harmonized").is_not_null() & worth_storing).select(
                "country", "cell", "region_cell", "year", "month", "terra", "aqua", "modis", "snpp", "n20", "n21",
                "viirs_ref", "modis_harmonized", "harmonized", "harm_var", "modis_frp", "snpp_frp"))
            del rows

    monthly = pl.concat(stored)
    monthly.write_parquet(PROCESSED / "monthly_cells.parquet", compression="zstd")
    t.cells.write_parquet(PROCESSED / "cells.parquet")
    meta = (pl.DataFrame({"country": countries}).join(t.months, how="cross").join(t.availability, on=["year", "month"], how="left")
            .select("country", "year", "month", "coverage",
                    method=pl.when(pl.col("coverage") <= 0).then(pl.lit("no_data"))
                    .when(pl.col("snpp_av")).then(pl.lit("viirs_observed"))
                    .when(pl.col("n20_av") | pl.col("n21_av")).then(pl.lit("viirs_scaled"))
                    .when(pl.col("aqua_av")).then(pl.lit("modis_model")).otherwise(pl.lit("modis_model_terra_only")),
                    viirs_src=pl.when(pl.col("snpp_av")).then(pl.lit("S-NPP")).when(pl.col("n20_av")).then(pl.lit("NOAA-20 (scaled)"))
                    .when(pl.col("n21_av")).then(pl.lit("NOAA-21 (scaled)")).otherwise(None)))
    meta.write_parquet(PROCESSED / "monthly_meta.parquet")

    switch = sensor_switch_test(pl.concat(country_month_totals))
    for c, r in switch.items():
        print(f"   2012 switch {c}: naive x{r['naive']['jump_at_2012_factor']} (p={r['naive']['p_value']}) -> "
              f"harmonized x{r['harm']['jump_at_2012_factor']} (p={r['harm']['p_value']})", flush=True)

    report = {
        "method_version": "harm-v2.0",
        "unit": "VIIRS S-NPP-equivalent hotspots (375 m, nominal+high confidence, vegetation fires)",
        "countries": countries, "grid": cfg["grid"], "cleaning": cfg["cleaning"],
        "overlap_years": hc["overlap_years"], "training_years": hc.get("training_years"), "holdout_years": hc["holdout_years"],
        "drift_downweight": {"from_year": hc["drift_downweight_from"], "weight": hc["drift_weight"]},
        "training_sampling": {"empty_cell_month_sample": EMPTY_SAMPLE_SHARE, "reweight": round(1 / EMPTY_SAMPLE_SHARE, 3)},
        "selected_model": best, "selected_model_terra_only": best_terra,
        "holdout_metrics": metrics, "holdout_metrics_terra_only": terra_metrics,
        "holdout_metrics_by_country": metrics_by_country,
        "variance_model": uncertainty,
        "noaa20_to_snpp_ratio": t.ratios,
        "stratified_eb_summary": eb.summary(),
        "gbm_feature_importance": gbm.importance(),
        "gbm_trees": gbm.n_trees,
        "calibration_by_year": calibration,
        "calibration_trend_per_year": round(calibration_trend, 5),
        "raw_viirs_to_aqua_ratio_by_year": [{"year": r["year"], "ratio": round(r["v"] / max(r["a"], 1), 3)} for r in raw_ratio.iter_rows(named=True)],
        "step_change_tests": switch,
        "rows_stored": monthly.height,
        "data_last_date": str(last_date),
    }
    (PROCESSED / "harmonization_report.json").write_text(json.dumps(report, indent=2, default=float))
    print(f"Saved monthly_cells.parquet ({monthly.height:,} rows), monthly_meta.parquet, cells.parquet, harmonization_report.json", flush=True)


def main() -> None:
    """Use the independent three-way protocol for all supported entry points."""
    from pipeline.harmonize_v3 import main as run_v3
    run_v3()


if __name__ == "__main__":
    main()
