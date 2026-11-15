from types import SimpleNamespace
import polars as pl
from embercal_engine.analytics import calendar
from pipeline.calibration_gate import safeguards


def test_geographic_gate_rejects_regression_hidden_by_national_gain():
    baseline=dict(mean_objective=20.,mean_area_wape=dict(australia=10.,southwest=20.))
    candidate=dict(mean_objective=10.,fold_wins=4,mean_area_wape=dict(australia=5.,southwest=22.))
    result=safeguards(candidate,baseline)
    assert not result['eligible'] and 'Regional error guardrail: southwest' in result['reasons']
    candidate['mean_area_wape']['southwest']=19.
    assert safeguards(candidate,baseline)['eligible']


def test_gate_requires_consistency_across_training_years():
    baseline=dict(mean_objective=20.,mean_area_wape=dict(australia=10.))
    candidate=dict(mean_objective=15.,fold_wins=2,mean_area_wape=dict(australia=8.))
    assert not safeguards(candidate,baseline)['eligible']


def data(value,lo,hi):
    return pl.DataFrame([dict(year=2020,month=1,modis_harmonized=value,model_lo=lo,model_hi=hi,
        coverage=1.,method='viirs_observed',viirs_src='S-NPP')])


def test_zero_width_range_is_unquantified_not_false_certainty():
    store=SimpleNamespace(latest=(2020,1),latest_complete=(2020,1))
    result=calendar(store,data(0.,0.,0.),'modis_harmonized',202001,202001,2020,2020)
    row=result['months'][0]
    assert row['lo'] is None and row['hi'] is None
    assert row['uncertainty_status']=='not_quantified'
    assert row['baseline_status']=='limited' and row.get('label') is None
    assert result['current']['latest_complete'].get('label') is None


def test_nonzero_residual_range_is_explicitly_approximate_for_selected_area():
    store=SimpleNamespace(latest=(2020,1),latest_complete=(2020,1))
    row=calendar(store,data(100.,80.,120.),'modis_harmonized',202001,202001,2020,2020)['months'][0]
    assert row['lo']==80. and row['hi']==120.
    assert row['nominal_interval_coverage']==.8
    assert row['interval_validated_for_selection'] is False
    assert row['uncertainty_status']=='approximate_residual_range'
