"""Retirement scenarios: synthetic counts, never NASA network requests."""
from datetime import date
from types import SimpleNamespace
import polars as pl
import pytest
from embercal_engine import analytics
from embercal_engine.continuity import source_support


def test_native_support_uses_all_records_not_only_quality_passed(tmp_path):
    pl.DataFrame(dict(region=["Australia"]*3, sensor=["MODIS","MODIS","VIIRS"],
        acq_date=[date(2029,11,1),date(2029,12,10),date(2030,1,2)],
        qc_pass=[True,False,True])).write_parquet(tmp_path/"native.parquet")
    meta=pl.DataFrame(dict(country=["Australia"]*3,year=[2029,2029,2030],month=[11,12,1]))
    out=source_support(meta,tmp_path)
    assert out["has_modis"].to_list()==[True,True,False]
    assert out["modis_support_end"].to_list()==["2029-12-10"]*3


@pytest.mark.parametrize("reference_method,source",[("viirs_observed","S-NPP"),("viirs_scaled","NOAA-20 (scaled)")])
def test_modis_retirement_continues_without_summing_or_false_intervals(reference_method,source):
    store=SimpleNamespace(harm_report={},latest=(2030,3),latest_complete=(2030,3),
        cells=pl.DataFrame(dict(cell=[1],country=["Australia"])),
        monthly=pl.DataFrame(dict(cell=[1]*3,year=[2030]*3,month=[1,2,3],
            modis=[0.,0.,0.],viirs_ref=[250.,300.,0.],modis_harmonized=[7.,8.,9.],
            modis_model_var=[25.]*3,modis_frp=[0.]*3,snpp_frp=[0.]*3)),
        meta=pl.DataFrame(dict(country=["Australia"]*3,year=[2030]*3,month=[1,2,3],
            coverage=[1.]*3,has_modis=[True,False,False],
            method=[reference_method,reference_method,"no_data"],viirs_src=[source,source,None])))
    m=analytics.aoi_monthly(store,pl.DataFrame(dict(cell=[1],w=[1.])),1)
    # An observed local MODIS zero must not trigger switching during overlap.
    assert m["modis_harmonized"].to_list()==[7.,300.,None]
    assert m["modis_raw"].to_list()==[0.,None,None]
    out=analytics.calendar(store,m,"modis_harmonized",203001,203003,2030,2030)["months"]
    assert out[0]["value_kind"]=="model_estimate"
    assert out[1]["sensor"]==source and out[1]["method"]==reference_method
    assert out[1]["lo"] is None and out[1]["hi"] is None
    assert out[2]["value_kind"]=="unavailable" and out[2]["value"] is None
    assert out[2].get("percentile") is None


def test_missing_archive_does_not_guess_retirement(tmp_path):
    meta=pl.DataFrame(dict(country=["Australia"],year=[2030],month=[1]))
    assert source_support(meta,tmp_path).equals(meta)


def test_pipeline_persists_same_policy_and_preserves_overlap():
    from pipeline.source_support import apply_continuity
    monthly=pl.DataFrame(dict(country=["Australia"]*3,year=[2030]*3,month=[1,2,3],
        modis_harmonized=[167.,0.,0.],viirs_ref=[250.,300.,0.],modis_model_var=[4.,0.,0.]))
    meta=pl.DataFrame(dict(country=["Australia"]*3,year=[2030]*3,month=[1,2,3],
        has_modis=[True,False,False],method=["viirs_observed","viirs_scaled","no_data"]))
    out=apply_continuity(monthly,meta)
    assert out["modis_harmonized"].to_list()==[167.,300.,None]
    assert out["modis_model_var"].to_list()==[4.,None,None]


def test_zero_counts_do_not_claim_snpp_reference_available():
    from pipeline.harmonize import sensor_availability
    row={c:[0] for c in ("snpp_d","snpp_n","n20_d","n20_n","n21_d","n21_n")}
    out=sensor_availability(pl.DataFrame(dict(year=[2030],month=[1],**row)))
    assert out["snpp_av"].item() is False
