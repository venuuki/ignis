"""AOI geometry -> weighted grid cells (fraction of each 0.1deg cell inside the AOI)."""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import polars as pl
import shapely
from shapely.geometry import box, shape

from .schemas import AOI

CELL = 0.1
NCOLS = 3600
KM_PER_DEG = 111.32


def aoi_geometry(aoi: AOI):
    if aoi.type == "bbox":
        return box(*aoi.bbox)
    if aoi.type == "polygon":
        g = shape(aoi.geometry)
        return g if g.is_valid else g.buffer(0)
    if aoi.type == "point_radius":
        lon, lat = aoi.point
        r_lat = aoi.radius_km / KM_PER_DEG
        r_lon = aoi.radius_km / (KM_PER_DEG * max(math.cos(math.radians(lat)), 0.01))
        a = np.linspace(0, 2 * np.pi, 97)
        return shapely.Polygon(np.column_stack([lon + r_lon * np.cos(a), lat + r_lat * np.sin(a)]))
    return None  # region handled by attribute


def aoi_key(aoi: AOI) -> str:
    return hashlib.sha1(json.dumps(aoi.model_dump(), sort_keys=True).encode()).hexdigest()


def area_km2(geom) -> float:
    """Approximate geodesic area (equirectangular scaling at each vertex latitude band)."""
    if geom is None or geom.is_empty:
        return 0.0
    lat = geom.centroid.y
    return float(geom.area * KM_PER_DEG ** 2 * math.cos(math.radians(lat)))


def cell_weights(aoi: AOI, cells: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """cells: frame with columns cell, country. Returns (cell, w) and an AOI summary."""
    if aoi.type == "region":
        sel = cells.filter(pl.col("country") == aoi.region).select("cell", w=pl.lit(1.0))
        n = sel.height
        lat = ((sel["cell"] // NCOLS) * CELL - 90 + CELL / 2).to_numpy() if n else np.array([0.0])
        area = float(np.sum(CELL * CELL * KM_PER_DEG ** 2 * np.cos(np.radians(lat)))) if n else 0.0
        return sel, {"area_km2": round(area), "cells": n, "effective_cells": float(n), "bounds": None}
    g = aoi_geometry(aoi)
    w, s, e, n = g.bounds
    c = cells.select("cell").unique()
    rows, cols = c["cell"] // NCOLS, c["cell"] % NCOLS
    lat0 = (rows * CELL - 90).to_numpy()
    lon0 = (cols * CELL - 180).to_numpy()
    pre = (lon0 + CELL >= w) & (lon0 <= e) & (lat0 + CELL >= s) & (lat0 <= n)
    cid = c["cell"].to_numpy()[pre]
    boxes = shapely.box(lon0[pre], lat0[pre], lon0[pre] + CELL, lat0[pre] + CELL)
    frac = shapely.area(shapely.intersection(boxes, g)) / (CELL * CELL) if len(boxes) else np.array([])
    keep = frac > 1e-4
    sel = pl.DataFrame({"cell": cid[keep].astype(np.int32), "w": frac[keep]})
    summary = {"area_km2": round(area_km2(g)), "cells": int(keep.sum()),
               "effective_cells": round(float(frac[keep].sum()), 2), "bounds": [round(v, 4) for v in g.bounds]}
    return sel, summary


def points_in_aoi(aoi: AOI, lon: np.ndarray, lat: np.ndarray, country: np.ndarray | None = None) -> np.ndarray:
    if aoi.type == "region":
        return country == aoi.region
    g = aoi_geometry(aoi)
    shapely.prepare(g)
    return shapely.contains_xy(g, lon, lat)


def prefilter_bounds(aoi: AOI) -> tuple[float, float, float, float] | None:
    if aoi.type == "region":
        return None
    return aoi_geometry(aoi).bounds
