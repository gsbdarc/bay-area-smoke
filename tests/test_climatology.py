"""Climatology denominator tests.

The two statistics the day card shows are easy to conflate, and conflating them
is exactly the bug this file exists to prevent:

  * `p_*`          a PER-DAY rate  -- exceedances / day-observations
  * `years_hit_*`  a PER-YEAR count -- years with >=1 exceedance in the window

They are both correct and they are not the same number. Worse, each needs its
own denominator: a year can have enough AQI data to qualify and not enough
smoke data, so a single shared `n_years` will pair one metric's numerator with
the other's denominator. San Jose on 22 November published "9 of 20" when the
truth was 9 of 23, because 20 was the *smoke* year count.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CLIM = ROOT / "site" / "data" / "climatology.json"
pytestmark = pytest.mark.skipif(
    not CLIM.exists(), reason="site data not built; run run_all.py first"
)


@pytest.fixture(scope="module")
def clim():
    return json.loads(CLIM.read_text())["by_location"]


def test_each_metric_carries_its_own_denominators(clim):
    for slug, rows in clim.items():
        for key in ("n_years_aqi", "n_years_smoke", "n_obs_aqi", "n_obs_smoke"):
            assert key in rows, f"{slug} missing {key}"


def test_years_hit_never_exceeds_its_own_year_count(clim):
    """The bug's signature: a numerator larger than its denominator, or a
    ratio above 1, because the two came from different metrics."""
    for slug, rows in clim.items():
        for metric in ("aqi", "smoke"):
            hit = rows[f"years_hit_{metric}"]
            n = rows[f"n_years_{metric}"]
            for s, (h, tot) in enumerate(zip(hit, n)):
                assert h <= tot, (
                    f"{slug} slot {s} ({metric}): {h} of {tot} years -- "
                    "numerator exceeds denominator"
                )


def test_per_day_rate_is_consistent_with_its_observation_count(clim):
    """`p * n_obs` must be a whole number of days, within rounding."""
    for slug, rows in clim.items():
        for metric in ("aqi", "smoke"):
            for s, (p, nobs) in enumerate(
                zip(rows[f"p_{metric}"], rows[f"n_obs_{metric}"])
            ):
                if p is None:
                    continue
                hits = p * nobs
                assert abs(hits - round(hits)) < 0.05, (
                    f"{slug} slot {s} ({metric}): p={p} x n_obs={nobs} "
                    f"= {hits}, not a whole day count"
                )


def test_per_day_rate_never_exceeds_the_per_year_rate(clim):
    """A day-rate above the year-rate is impossible: if x% of days in the
    window are bad, at least x% of years must contain a bad day."""
    for slug, rows in clim.items():
        for metric in ("aqi", "smoke"):
            for s in range(len(rows[f"p_{metric}"])):
                p = rows[f"p_{metric}"][s]
                n = rows[f"n_years_{metric}"][s]
                if p is None or not n:
                    continue
                year_rate = rows[f"years_hit_{metric}"][s] / n
                assert p <= year_rate + 1e-9, (
                    f"{slug} slot {s} ({metric}): per-day {p:.3f} exceeds "
                    f"per-year {year_rate:.3f}"
                )


def test_san_jose_22_november_keeps_its_denominators_apart(clim):
    """The exact case that surfaced the bug.

    Deliberately NOT pinned to exact counts. This test was originally written
    against `n_obs_aqi == 342` and started failing the moment the AirNow
    backfill added real days -- a correct build tripping a stale constant.
    Coverage grows every month, so the durable assertion is the *relationship*
    that was broken, not the arithmetic of one snapshot.

    The bug: `n_years` was a single shared value, so the AQI numerator was
    published against the smoke denominator. At this slot the two genuinely
    differ, which is exactly why it showed up here.
    """
    import datetime as dt
    slot = (dt.date(2020, 11, 22) - dt.date(2020, 1, 1)).days
    r = clim["san-jose"]

    assert r["n_years_aqi"][slot] != r["n_years_smoke"][slot], (
        "this slot is only a useful regression test while the two metrics "
        "qualify different numbers of years"
    )
    # The per-day rate must be computed against day-observations...
    hits = r["p_aqi"][slot] * r["n_obs_aqi"][slot]
    assert abs(hits - round(hits)) < 0.05
    # ...and the per-year count against years, never mixed.
    assert r["years_hit_aqi"][slot] <= r["n_years_aqi"][slot]
    # The two rates must not be confusable: per-day well below per-year.
    assert r["p_aqi"][slot] < r["years_hit_aqi"][slot] / r["n_years_aqi"][slot]
