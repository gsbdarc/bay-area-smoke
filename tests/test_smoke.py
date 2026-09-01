"""Smoke-attribution tests.

The background median is the part of the Childs et al. method most easily got
wrong, so it is pinned here against hand-computed values rather than a golden
file.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from smoke import attribute_smoke, nonsmoke_background  # noqa: E402


def _september(year: int, pm25: float, n: int = 10, plume: int = 0):
    """n consecutive September days at one location, all the same value."""
    return pd.DataFrame(
        {
            "location": "napa",
            "date": pd.date_range(f"{year}-09-01", periods=n, freq="D"),
            "pm25": float(pm25),
            "plume": plume,
        }
    )


@pytest.fixture
def three_septembers():
    # Distinct levels per year so a centered 3-year pool is identifiable.
    return pd.concat(
        [_september(2017, 10.0), _september(2018, 20.0), _september(2019, 30.0)],
        ignore_index=True,
    )


def test_background_pools_three_centered_years(three_septembers):
    bg = nonsmoke_background(three_septembers)
    got = {(r.year, r.month): r.background for r in bg.itertuples(index=False)}

    # 2018 pools 2017+2018+2019 -> median of 10x10, 10x20, 10x30 = 20
    assert got[(2018, 9)] == 20.0
    # 2017 pools 2016(absent)+2017+2018 -> median of 10x10, 10x20 = 15
    assert got[(2017, 9)] == 15.0
    # 2019 pools 2018+2019+2020(absent) -> median of 10x20, 10x30 = 25
    assert got[(2019, 9)] == 25.0


def test_background_is_not_a_rolling_day_window(three_septembers):
    # A +/-N-day window would give ~the same value in every year, since each
    # September is internally constant. The centered-year pooling must not.
    bg = nonsmoke_background(three_septembers)
    assert bg["background"].nunique() == 3


def test_background_ignores_smoke_days():
    # A huge smoke day inside the month must not drag the background up.
    clean = _september(2018, 20.0, n=12)
    smoky = _september(2018, 500.0, n=3, plume=1)
    smoky["date"] = pd.date_range("2018-09-20", periods=3, freq="D")
    bg = nonsmoke_background(pd.concat([clean, smoky], ignore_index=True))
    assert bg.loc[bg["month"] == 9, "background"].iloc[0] == 20.0


def test_thin_background_is_nan_not_a_guess():
    # Fewer than MIN_BACKGROUND_OBS clean days -> we decline to estimate.
    bg = nonsmoke_background(_september(2018, 20.0, n=3))
    assert np.isnan(bg["background"].iloc[0])
    assert bg["nobs"].iloc[0] == 3


def test_trailing_window_for_the_current_year(three_septembers):
    # A centered window needs year+1, which does not exist for the current
    # year. The trailing variant pools year-2, year-1, year.
    bg = nonsmoke_background(three_septembers, trailing_only_from=2019)
    got = {(r.year, r.month): r.background for r in bg.itertuples(index=False)}
    # 2019 now pools 2017+2018+2019 -> median of 10,20,30 blocks = 20
    assert got[(2019, 9)] == 20.0
    # 2018 is before the cutoff, so still centered -> 20
    assert got[(2018, 9)] == 20.0


def test_smoke_pm_is_positive_part_of_anomaly(three_septembers):
    extra = pd.DataFrame(
        {
            "location": "napa",
            "date": pd.to_datetime(["2018-09-20", "2018-09-21"]),
            "pm25": [100.0, 15.0],
            "plume": [1, 1],
        }
    )
    out = attribute_smoke(pd.concat([three_septembers, extra], ignore_index=True))
    by_date = out.set_index("date")["smoke_pm"]

    # background for Sept 2018 is 20
    assert by_date["2018-09-20"] == pytest.approx(80.0)
    # negative anomaly on a smoke day clips to zero, it does not go negative
    assert by_date["2018-09-21"] == pytest.approx(0.0)


def test_high_pm25_without_a_plume_is_zero_smoke():
    # This is the defining property of the method: smoke is zero BY
    # CONSTRUCTION on non-smoke days, however dirty the air was. Winter wood
    # smoke and traffic must not be attributed to wildfire.
    base = _september(2018, 20.0, n=12)
    dirty = _september(2018, 300.0, n=1, plume=0)
    dirty["date"] = pd.to_datetime(["2018-09-25"])
    out = attribute_smoke(pd.concat([base, dirty], ignore_index=True))
    assert out.set_index("date")["smoke_pm"]["2018-09-25"] == 0.0


def test_unknown_plume_status_is_nan_not_zero():
    # Days where the HMS archive has no file must read as "we don't know",
    # never as "clean" -- otherwise gaps look like good air.
    base = _september(2018, 20.0, n=12)
    gap = _september(2018, 50.0, n=1)
    gap["date"] = pd.to_datetime(["2018-09-26"])
    gap["plume"] = np.nan
    out = attribute_smoke(pd.concat([base, gap], ignore_index=True))
    assert np.isnan(out.set_index("date")["smoke_pm"]["2018-09-26"])


def test_missing_pm25_on_a_smoke_day_is_nan_not_zero():
    base = _september(2018, 20.0, n=12)
    gap = _september(2018, 20.0, n=1, plume=1)
    gap["date"] = pd.to_datetime(["2018-09-27"])
    gap["pm25"] = np.nan
    out = attribute_smoke(pd.concat([base, gap], ignore_index=True))
    assert np.isnan(out.set_index("date")["smoke_pm"]["2018-09-27"])


def test_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing columns"):
        nonsmoke_background(pd.DataFrame({"location": [], "date": []}))


def test_no_measurement_still_yields_zero_smoke_on_clear_days():
    """Document the raw method's behaviour, which s04 then has to correct.

    `attribute_smoke` sets smoke to exactly 0 whenever no plume was overhead,
    even with no PM2.5 at all. That is right for a station that is measuring --
    no plume means no wildfire PM2.5 regardless of concentration -- and it is
    what the reference implementation does.

    It is *wrong* for a location with no instrument, where it manufactures
    confident zeros from nothing. s04 masks those; this test pins the
    underlying behaviour so that if it ever changes, the mask gets revisited
    rather than silently becoming a no-op.
    """
    d = _september(2018, 20.0, n=12)
    blind = _september(2018, 20.0, n=2, plume=0)
    blind["date"] = pd.to_datetime(["2018-09-28", "2018-09-29"])
    blind["pm25"] = np.nan

    out = attribute_smoke(pd.concat([d, blind], ignore_index=True))
    got = out.set_index("date")["smoke_pm"]
    assert got["2018-09-28"] == 0.0
    assert got["2018-09-29"] == 0.0


def test_unmeasured_smoke_day_is_nan_so_gaps_cannot_read_as_clean():
    """The other half of the same story: a plume overhead with no measurement
    is unknown, never zero. Together with the s04 mask this is what stops a
    monitor outage from diluting a location's climatology with clean years."""
    d = _september(2018, 20.0, n=12)
    blind = _september(2018, 20.0, n=1, plume=1)
    blind["date"] = pd.to_datetime(["2018-09-30"])
    blind["pm25"] = np.nan

    out = attribute_smoke(pd.concat([d, blind], ignore_index=True))
    assert np.isnan(out.set_index("date")["smoke_pm"]["2018-09-30"])
