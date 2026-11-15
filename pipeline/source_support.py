"""Conservative monthly source continuity on the existing S-NPP reference scale.

Only months AFTER the final supplied MODIS month continue with VIIRS.
Zero local counts never trigger a sensor switch. The final MODIS month remains
MODIS-derived: hotspot files cannot establish within-month viewing coverage.
"""
from pathlib import Path
import polars as pl

SOURCE_POLICY = "modis_then_viirs_v1"
REFERENCE_POLICY = (
    "Calibrated MODIS through the final supplied MODIS month; VIIRS reference thereafter "
    "on the S-NPP-equivalent scale. No summing or averaging. Native archive date bounds "
    "describe supplied data, not satellite retirement or cloud-free viewing coverage."
)


def source_support(meta: pl.DataFrame, archive: Path) -> pl.DataFrame:
    files = list(archive.glob("*.parquet"))
    if not files:
        return meta  # Legacy data: never guess a transition without source records.
    native = pl.scan_parquet([str(p) for p in files])
    bounds = (native.filter(pl.col("sensor") == "MODIS")
        .group_by("region").agg(
            _first_modis=pl.col("acq_date").min(),
            _last_modis=pl.col("acq_date").max()).collect()
        .rename({"region": "country"}))
    m = meta.drop([c for c in ("has_modis",) if c in meta.columns]).join(bounds, on="country", how="left")
    ym = pl.col("year") * 100 + pl.col("month")
    first = pl.col("_first_modis").dt.year()*100 + pl.col("_first_modis").dt.month()
    last = pl.col("_last_modis").dt.year()*100 + pl.col("_last_modis").dt.month()
    return m.with_columns(
        has_modis=((ym >= first) & (ym <= last)).fill_null(False),
        modis_support_end=pl.col("_last_modis").cast(pl.String),
    ).drop("_first_modis", "_last_modis")


def apply_continuity(monthly: pl.DataFrame, meta: pl.DataFrame) -> pl.DataFrame:
    """Persist the same source policy used by the API, preserving overlap values."""
    if "has_modis" not in meta.columns:
        raise ValueError("Native source support is required to build a continuous record")
    keys=["country", "year", "month"]
    m=monthly.join(meta.select(*keys,"has_modis","method"),on=keys,how="left")
    reference=pl.col("method").is_in(["viirs_observed","viirs_scaled"])
    return m.with_columns(
        modis_harmonized=pl.when(pl.col("has_modis")).then(pl.col("modis_harmonized"))
            .when(reference).then(pl.col("viirs_ref")).otherwise(None),
        modis_model_var=pl.when(pl.col("has_modis")).then(pl.col("modis_model_var")).otherwise(None),
    ).drop("has_modis","method")
