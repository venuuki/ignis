from datetime import date
from pathlib import Path
import httpx
import pandas as pd
import pytest
from ignis.firms import fetch_window, fetch_range, FirmsError, STUDY_BBOX
from ignis.compare import daily_comparison

CSV = "latitude,longitude,acq_date,confidence,frp\n-35.0,148.0,2026-11-10,80,12.3\n-35.1,148.1,2026-11-10,70,9.2\n"


def test_download_then_offline_cache(tmp_path):
    requests = []
    def handler(req):
        requests.append(str(req.url))
        return httpx.Response(200, text=CSV)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = fetch_window("MODIS_NRT", STUDY_BBOX, date(2026, 11, 10), 2, tmp_path, key="test", client=client)
    second = fetch_window("MODIS_NRT", STUDY_BBOX, date(2026, 11, 10), 2, tmp_path, offline=True)
    assert len(first) == len(second) == 2
    assert len(requests) == 1
    assert "/MODIS_NRT/145,-38,151,-32/2/2026-11-10" in requests[0]


def test_five_day_chunking(tmp_path):
    seen = []
    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, text=CSV)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        frame = fetch_range("VIIRS_SNPP_NRT", STUDY_BBOX, date(2026, 11, 1), date(2026, 11, 11), tmp_path, key="test", client=client)
    assert len(seen) == 3
    assert "/5/2026-11-01" in seen[0]
    assert "/5/2026-11-06" in seen[1]
    assert "/1/2026-11-11" in seen[2]
    assert len(frame) == 6


def test_daily_comparison_includes_missing_dates():
    m = pd.read_csv(__import__("io").StringIO(CSV))
    v = m.iloc[:1].copy()
    v["acq_date"] = "2026-11-11"
    out = daily_comparison(m, v, "2026-11-10", "2026-11-12")
    assert out["modis_detections"].tolist() == [2, 0, 0]
    assert out["viirs_detections"].tolist() == [0, 1, 0]
    assert out["difference_viirs_minus_modis"].tolist() == [-2, 1, 0]


def test_bad_key_and_offline_miss(tmp_path):
    with pytest.raises(FirmsError):
        fetch_window("MODIS_NRT", STUDY_BBOX, date(2026, 11, 10), 1, tmp_path, offline=True)
    with pytest.raises(ValueError):
        fetch_window("MODIS_NRT", STUDY_BBOX, date(2026, 11, 10), 6, tmp_path)


def test_invalid_response_not_cached(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="invalid key"))) as client:
        with pytest.raises(FirmsError):
            fetch_window("MODIS_NRT", STUDY_BBOX, date(2026, 11, 10), 1, tmp_path, key="test", client=client)
    assert not list(tmp_path.glob("*.csv"))
