"""Check shrinkage and uncertainty math without production or network data."""
import math
import numpy as np
import polars as pl
import pytest
from pipeline.calibration_experiment import RegionalRatio, finite_quantile, TRAIN, SELECT, INTERVAL, TEST


def rows():
    return pl.DataFrame(dict(cell=[1983280,1983280],year=[2013,2013],month=[1,2],
        m_d=[10.,10.],m_n=[10.,10.],y_d=[20.,40.],y_n=[10.,10.]))


def test_regional_ratio_shrinks_small_groups_and_preserves_order():
    data=rows()
    model=RegionalRatio('season',prior=1.).fit(data)
    # Day global ratio=3, night=1. Group ratios borrow 1 MODIS-count of evidence.
    expected=np.array([10*(20+3)/11+10,10*(40+3)/11+10])
    assert model.predict(data)==pytest.approx(expected)
    assert model.predict(data.reverse())==pytest.approx(expected[::-1])


def test_unseen_season_falls_back_to_training_global_ratio():
    model=RegionalRatio('season',prior=1.).fit(rows())
    unseen=rows().head(1).with_columns(month=pl.lit(3))
    assert model.predict(unseen).item()==40.
    # Future target columns are never used during prediction.
    changed=unseen.with_columns(y_d=pl.lit(1e9),y_n=pl.lit(1e9))
    assert model.predict(changed).item()==40.


def test_corrected_interval_quantile_does_not_silently_cap_small_sample():
    assert finite_quantile([1,2,3,4])==4
    assert finite_quantile(range(1,13))==11
    assert math.isinf(finite_quantile([1,2]))
    with pytest.raises(ValueError):finite_quantile([])


def test_all_experiment_roles_have_disjoint_years():
    groups=[set(TRAIN),set(SELECT),set(INTERVAL),set(TEST)]
    assert all(not a&b for i,a in enumerate(groups) for b in groups[i+1:])
