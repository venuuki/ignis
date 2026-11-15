"""Loads processed pipeline outputs once at start-up.

monthly_cells.parquet  sparse: only non-empty cell-months (absent row = 0 hotspots)
monthly_meta.parquet   per country-month coverage, harmonization method and VIIRS source
cells.parquet          every 0.1deg cell with its country, 0.5deg region and centre
"""
from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

import polars as pl

from .config import settings


class DataStore:
    def __init__(self, data_dir: Path | None = None):
        self.dir = Path(data_dir or settings.data_dir)
        self.monthly = pl.read_parquet(self.dir / "monthly_cells.parquet")
        self.meta = pl.read_parquet(self.dir / "monthly_meta.parquet")
        self.cells = pl.read_parquet(self.dir / "cells.parquet")
        native=self.dir / "native_snpp_monthly.parquet"
        self.native_snpp=pl.read_parquet(native) if native.exists() else None
        self.hotspots_path = str(self.dir / "hotspots" / "*.parquet").replace("\\", "/")
        fc = self.dir / "forecasts.parquet"
        self.forecasts = pl.read_parquet(fc) if fc.exists() else None
        self.harm_report = self._json("harmonization_report.json")
        self.fc_report = self._json("forecast_report.json")
        mp = settings.manifest_path
        self.manifest = json.loads(mp.read_text()) if mp.exists() else {}
        ym = pl.col("year") * 100 + pl.col("month")
        latest = self.meta.filter(pl.col("coverage") > 0).select(ym.max()).item()
        full = self.meta.filter(pl.col("coverage") >= 0.999).select(ym.max()).item()
        self.latest = (latest // 100, latest % 100)
        self.latest_complete = (full // 100, full % 100)

    def _json(self, name: str) -> dict:
        p = self.dir / name
        return json.loads(p.read_text()) if p.exists() else {}

    @cached_property
    def regions(self) -> list[dict]:
        out = []
        for (country,), g in self.cells.group_by(["country"]):
            lat, lon = g["lat"], g["lon"]
            out.append({"id": country, "name": country.replace("_", " "),
                        "bbox": [round(float(lon.min()) - 0.05, 2), round(float(lat.min()) - 0.05, 2),
                                 round(float(lon.max()) + 0.05, 2), round(float(lat.max()) + 0.05, 2)],
                        "cells": g.height})
        return sorted(out, key=lambda r: r["id"])


_store: DataStore | None = None


def get_store() -> DataStore:
    from fastapi import HTTPException
    global _store
    if _store is None:
        try:
            _store = DataStore()
        except FileNotFoundError as exc:
            raise HTTPException(503, "Australia analysis data are not built. Run Build-Australia.ps1 from the project folder.") from exc
        if set(_store.cells["country"].unique().to_list()) != {"Australia"}:
            _store = None
            raise HTTPException(503, "Processed datasets must contain Australia only. Rebuild with Build-Australia.ps1.")
    return _store
