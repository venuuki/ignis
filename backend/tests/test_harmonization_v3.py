from datetime import date
import numpy as np
import polars as pl
import pytest
from pipeline import harmonize as h
from pipeline.harmonize_v3 import split_years
from embercal_engine import analytics

def test_three_way_split_cannot_reuse_final_test_for_selection():
    cfg={'harmonization':dict(overlap_years=[2012,2025],training_years=[2013,2016],calibration_years=[2017],holdout_years=[2019])}
    assert split_years(cfg)==([2013,2016],[2017],[2019])
    cfg['harmonization']['calibration_years']=[2019]
    with pytest.raises(ValueError,match='disjoint'):split_years(cfg)

def test_learned_features_and_platform_scaling_exclude_test_years(monkeypatch):
    captured=[]
    def ratios(counts,availability):
        captured.extend(counts['year'].unique().to_list())
        return {dn:{'global':1.,'Australia':1.} for dn in ('d','n')}
    monkeypatch.setattr(h,'noaa20_to_snpp_ratios',ratios)
    records=[]
    for year,multiplier in [(2013,1),(2019,1000)]:
        row={c:0 for c in h.COUNT_COLUMNS}
        row.update(country='Australia',cell=1983280,region_cell=1,year=year,month=1,
            terra_d=20*multiplier,aqua_d=50*multiplier,snpp_d=200*multiplier,
            modis_frp=200.,modis_frp_max=100.,terra_frp=30.,terra_frp_max=20.,snpp_frp=100.)
        records.append(row)
    t=h.make_tables(pl.DataFrame(records),date(2019,1,31),feature_years=[2013])
    assert captured==[2013]
    assert t.cell_climatology['clim_cell'].item()==70.
    assert 'terra_frp' in h.FEATURES_TERRA_ONLY
    assert 'modis_frp' not in h.FEATURES_TERRA_ONLY

def test_measured_reference_has_no_zero_width_calibration_interval():
    from types import SimpleNamespace
    store=SimpleNamespace(harm_report={},cells=pl.DataFrame(dict(cell=[1],country=['Australia'])),
        monthly=pl.DataFrame(dict(cell=[1],year=[2020],month=[1],harmonized=[250.],harm_var=[0.],
        modis=[100.],viirs_ref=[250.],modis_harmonized=[240.],modis_model_var=[25.],snpp=[250],modis_frp=[10.],snpp_frp=[10.])),
        meta=pl.DataFrame(dict(country=['Australia'],year=[2020],month=[1],coverage=[1.],method=['viirs_observed'],viirs_src=['S-NPP'])))
    out=analytics.aoi_monthly(store,pl.DataFrame(dict(cell=[1],w=[1.])),1)
    assert out['lo'].item() is None and out['hi'].item() is None
    assert out['model_lo'].item() <240 <out['model_hi'].item()
    assert out['viirs_snpp_raw'].item()==250
    half=analytics.aoi_monthly(store,pl.DataFrame(dict(cell=[1],w=[.5])),.5)
    assert half['model_hi'].item()-120 == pytest.approx((out['model_hi'].item()-240)/2)

def test_native_snpp_records_do_not_depend_on_reference_platform():
    from types import SimpleNamespace
    store=SimpleNamespace(harm_report={},cells=pl.DataFrame(dict(cell=[1],country=['Australia'])),
        monthly=pl.DataFrame(dict(cell=[1],year=[2026],month=[11],harmonized=[250.],harm_var=[0.],modis=[100.],
            viirs_ref=[250.],modis_harmonized=[240.],snpp=[0],modis_frp=[1.],snpp_frp=[1.])),
        meta=pl.DataFrame(dict(country=['Australia'],year=[2026],month=[11],coverage=[.4667],method=['viirs_scaled'],viirs_src=['NOAA-20 (scaled)'])),
        native_snpp=pl.DataFrame(dict(country=['Australia'],cell=[1],year=[2026],month=[11],snpp=[7.])))
    d=analytics.aoi_monthly(store,pl.DataFrame(dict(cell=[1],w=[1.])),1)
    assert d['viirs_snpp_raw'].item()==7.
    assert d['viirs_raw'].item()==250.

def test_unavailable_series_has_no_complete_zero_years():
    from types import SimpleNamespace
    rows=[dict(year=2011,month=m,viirs_raw=None,coverage=1.,method='modis_model',viirs_src=None) for m in range(1,13)]
    store=SimpleNamespace(latest=(2011,12),latest_complete=(2011,12))
    d=analytics.calendar(store,pl.DataFrame(rows),'viirs_raw',201101,201112,2003,2022)
    assert d['annual']==[{'year':2011,'total':None,'complete':False}]
    assert not d['trend']['available']

def test_partial_month_does_not_receive_projected_anomaly_or_complete_year():
    from types import SimpleNamespace
    rows=[]
    for year in range(2018,2025):
        for month in range(1,13):
            rows.append(dict(year=year,month=month,harmonized=100.,modis_raw=30.,viirs_raw=100.,
                coverage=.5 if (year,month)==(2024,12) else 1.,method='viirs_observed',viirs_src='S-NPP',lo=None,hi=None))
    store=SimpleNamespace(latest=(2024,12),latest_complete=(2024,11))
    result=analytics.calendar(store,pl.DataFrame(rows),'harmonized',201801,202412,2018,2023)
    latest=result['months'][-1]
    assert latest['partial'] and latest.get('label') is None and latest.get('projected') is None
    assert result['annual'][-1]['complete'] is False
    assert result['current']['latest'].get('percentile') is None

def test_raw_modis_source_never_claims_noaa20():
    from types import SimpleNamespace
    data=pl.DataFrame([dict(year=2024,month=11,modis_raw=100.,harmonized=250.,coverage=1.,method='viirs_scaled',viirs_src='NOAA-20 (scaled)')])
    store=SimpleNamespace(latest=(2024,11),latest_complete=(2024,11))
    r=analytics.calendar(store,data,'modis_raw',202411,202411,2024,2024)['months'][0]
    assert r['sensor']=='MODIS (Terra/Aqua)'
    assert r['value_kind']=='observed_count' and r['lo'] is None
