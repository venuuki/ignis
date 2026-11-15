from datetime import date
import pytest
from pydantic import ValidationError
from embercal_engine.schemas import Query

@pytest.mark.parametrize('start,end',[(1999,2022),(2003,date.today().year+1),(2022,2003)])
def test_baseline_rejects_unsupported_or_reversed_years(start,end):
    with pytest.raises(ValidationError):
        Query(aoi={'type':'region','region':'Australia'},baseline_start=start,baseline_end=end)

def test_baseline_accepts_exact_minimum_and_current_year():
    q=Query(aoi={'type':'region','region':'Australia'},baseline_start=2000,baseline_end=date.today().year)
    assert q.baseline_start==2000 and q.baseline_end==date.today().year
