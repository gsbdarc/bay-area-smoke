"""Source-precedence tests.

Three feeds carry the same instruments' readings with different amounts of QA
applied: certified bulk AirData, the AQS API, and the AirNow real-time files.
AirNow exists here only to fill the hole BAAQMD's slow AQS submissions leave in
2025+ -- it must never displace a better-processed number for a day we already
have.

Getting this backwards would be invisible: the values are similar, so a wrongly
ranked feed produces a plausible series that is quietly less trustworthy than
the one we had.
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from s04_build_smoke import SOURCE_RANK, load_measurements  # noqa: E402

PANEL = ROOT / "data" / "processed" / "daily_panel.parquet"
BULK = ROOT / "data" / "processed" / "epa_daily_bulk.parquet"
AIRNOW = ROOT / "data" / "processed" / "airnow_daily.parquet"


def test_certified_outranks_both_provisional_feeds():
    assert SOURCE_RANK["certified"] < SOURCE_RANK["provisional"]
    assert SOURCE_RANK["provisional"] < SOURCE_RANK["provisional-airnow"]


@pytest.mark.skipif(not (BULK.exists() and AIRNOW.exists()),
                    reason="processed data not built")
def test_airnow_never_displaces_a_certified_site_day():
    """The invariant, checked against the real files rather than a fixture."""
    merged = load_measurements()
    bulk = pd.read_parquet(BULK)
    certified = bulk[bulk["provenance"] == "certified"][["site_id", "date"]]

    # Every certified site-day must still be certified after the merge.
    got = merged.merge(certified, on=["site_id", "date"], how="inner")
    bad = got[got["provenance"] != "certified"]
    assert bad.empty, (
        f"{len(bad)} certified site-days were overwritten by a provisional "
        f"feed, e.g.\n{bad.head().to_string()}"
    )


@pytest.mark.skipif(not AIRNOW.exists(), reason="processed data not built")
def test_airnow_only_covers_the_period_aqs_cannot():
    """AirNow is a gap-filler, not a parallel history. Fetching it back into
    the certified era would add churn and risk without adding information."""
    air = pd.read_parquet(AIRNOW)
    if air.empty:
        pytest.skip("no AirNow rows")
    assert air["date"].min() >= pd.Timestamp("2025-01-01")


@pytest.mark.skipif(not PANEL.exists(), reason="panel not built")
def test_panel_records_which_feed_each_day_came_from():
    panel = pd.read_parquet(PANEL)
    measured = panel[panel["pm25"].notna()]
    assert (measured["pm25_source"] != "none").all(), (
        "a day with a measurement must name the feed it came from"
    )
    unmeasured = panel[panel["pm25"].isna()]
    assert (unmeasured["pm25_source"] == "none").all(), (
        "a day with no measurement must not claim a source"
    )


@pytest.mark.skipif(not PANEL.exists(), reason="panel not built")
def test_airnow_actually_closed_the_san_francisco_2025_hole():
    """Issue #13's specific complaint: nothing for SF in 2025."""
    panel = pd.read_parquet(PANEL)
    sf = panel[(panel["location"] == "san-francisco")
               & (panel["date"].dt.year == 2025)]
    n = int(sf["pm25"].notna().sum())
    assert n > 300, f"San Francisco has only {n} measured days in 2025"
