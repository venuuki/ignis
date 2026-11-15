import numpy as np
import polars as pl

from pipeline.common import cell_center, cell_ids, load_config, normalise_firms
from pipeline.harmonize import pettitt


def test_grid_roundtrip():
    lat, lon = np.array([7.81, -33.9, 27.7]), np.array([80.71, 151.2, 85.3])
    c = cell_ids(lat, lon, 0.1)
    clat, clon = cell_center(c, 0.1)
    assert np.all(np.abs(clat - lat) <= 0.05 + 1e-9) and np.all(np.abs(clon - lon) <= 0.05 + 1e-9)


def test_normalise_modis_and_viirs():
    cfg = load_config()
    raw = pl.DataFrame({
        "latitude": ["7.8", "7.9", "8.0"], "longitude": ["80.7", "80.8", "80.9"],
        "brightness": ["320", None, None], "bright_ti4": [None, "340", "335"],
        "scan": ["1.0", "0.4", "0.4"], "track": ["1.0", "0.4", "0.4"],
        "acq_date": ["2020-03-01"] * 3, "acq_time": ["452", "0755", "0755"],
        "satellite": ["Aqua", "N", "N20"], "instrument": ["MODIS", "VIIRS", "VIIRS"],
        "confidence": ["20", "n", "h"], "version": ["6.1", "2", "2"],
        "bright_t31": ["300", None, None], "bright_ti5": [None, "295", "296"],
        "frp": ["10", "3", "4"], "daynight": ["D", "D", "N"], "type": ["0", "0", "2"],
    })
    out = normalise_firms(raw, "Sri_Lanka", "archive", cfg)
    assert out["satellite"].to_list() == ["Aqua", "S-NPP", "NOAA-20"]
    assert out["acq_time"].to_list()[0] == "0452"
    # MODIS confidence 20 < 30 fails QC; VIIRS type 2 (static source) fails QC
    assert out["qc_pass"].to_list() == [False, True, False]


def test_pettitt_detects_step():
    rng = np.random.default_rng(0)
    x = np.r_[rng.normal(0, 1, 60), rng.normal(2, 1, 60)]
    t, _, p = pettitt(x)
    assert 50 <= t <= 70 and p < 0.01
    _, _, p2 = pettitt(rng.normal(0, 1, 120))
    assert p2 > 0.05
