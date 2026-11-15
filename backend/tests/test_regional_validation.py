"""Numerical checks of the offline evaluation, independent of delivery data."""
import pytest
from pipeline.regional_validation import errors, transitions, anomaly_check


def test_wape_and_bias_have_distinct_meanings():
    m = errors([100., 100.], [120., 80.])
    assert m['monthly_mae'] == 20.
    assert m['wape_pct'] == 20.
    assert m['bias_pct'] == 0.
    assert errors([0., 0.], [1., 2.])['wape_pct'] is None


def test_handoff_removes_real_seasonal_change_from_error():
    rows = [dict(year=2020, month=12, reference=100., prediction=80.),
            dict(year=2021, month=1, reference=300., prediction=290.)]
    cut = transitions(rows)[0]
    assert cut['reference_jump'] == 200.
    assert cut['switched_jump'] == 220.
    assert cut['excess_jump'] == 20.


def test_handoff_does_not_bridge_missing_months():
    rows = [dict(year=2019, month=12, reference=100., prediction=100.),
            dict(year=2021, month=1, reference=100., prediction=100.)]
    assert transitions(rows) == []


def test_anomaly_baseline_excludes_target_year_and_zero_peer_medians():
    rows = [dict(year=y, month=1, reference=v, prediction=v) for y,v in [(2019,100.),(2020,100.),(2021,1000.)]]
    assert anomaly_check(rows,'prediction') == dict(n_months=3,agreement_pct=100.)
    for r in rows:r['prediction'] = 0.
    assert anomaly_check(rows,'prediction')['agreement_pct'] == pytest.approx(200/3)
    zeros = [dict(year=y,month=1,reference=0.,prediction=1.) for y in [2019,2020,2021]]
    assert anomaly_check(zeros,'prediction') == dict(n_months=0,agreement_pct=None)
