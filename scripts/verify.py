"""Ground-truth verification of the built panel: `python run_all.py --verify`.

`pytest` covers the pure logic with synthetic inputs and no network. This is the
other half -- it checks the *real* assembled data against events we already know
the answer to. If these fail, the join is wrong, not the world.

The checks are deliberately about things that would be invisible in a summary
statistic but obvious to anyone who lived through them:

1. **2017-10-09, Tubbs/Atlas.** Sebastopol and Napa must be extreme. If a
   timezone or join is off by a day this is where it shows.
2. **2018-11-08..21, Camp Fire.** SF, Oakland and San Jose sustained a
   fortnight of Unhealthy air. Tests duration, not just a spike.
3. **2020-09-09, the orange-sky day.** A plume must be overhead in SF while
   *surface* PM2.5 stays comparatively modest. This is the one check that would
   catch us conflating "smoke in the column" with "smoke in your lungs" -- the
   central caveat of the whole project.
4. **Half Moon Bay is cleaner than Livermore in September.** The coast/inland
   contrast is the reason the site has ten locations instead of one.
5. **Structural invariants** -- no NaN in published JSON, gap-free panel,
   smoke_pm never negative, AQI recomputed on one scale.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import PROCESSED, SITE_DATA  # noqa: E402
from fetch import log  # noqa: E402

PANEL = PROCESSED / "daily_panel.parquet"

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
_results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool, detail: str = "", *, soft: bool = False) -> bool:
    """Record a check. `soft` downgrades a failure to a warning."""
    status = PASS if ok else (WARN if soft else FAIL)
    _results.append((status, name, detail))
    return ok


def at(panel: pd.DataFrame, slug: str, date: str) -> pd.Series | None:
    m = panel[(panel["location"] == slug) & (panel["date"] == pd.Timestamp(date))]
    return None if m.empty else m.iloc[0]


def verify_events(panel: pd.DataFrame) -> None:
    # --- 1. Tubbs / Atlas, October 2017 -------------------------------------
    # The fires started overnight on 10-08/10-09; the worst surface air in
    # Sonoma and Napa was 10-09 through 10-13.
    for slug in ("sebastopol", "napa"):
        win = panel[
            (panel["location"] == slug)
            & panel["date"].between("2017-10-09", "2017-10-14")
        ]
        peak = win["pm25"].max()
        check(
            f"Tubbs 2017: {slug} peaks above AQI 150 (PM2.5 > 55)",
            pd.notna(peak) and peak > 55,
            f"peak PM2.5 = {peak:.1f}" if pd.notna(peak) else "no data",
        )
        # The modelled layer must see it too. This is the check that the grid
        # extract is joined to the right cell -- a mis-joined cell would still
        # look like plausible smoke, just not Tubbs-shaped.
        smoke_peak = win["smoke_pm"].max()
        check(
            f"Tubbs 2017: {slug} modelled smoke is extreme too",
            pd.notna(smoke_peak) and smoke_peak > 40,
            f"peak smoke PM2.5 = {smoke_peak:.1f}"
            if pd.notna(smoke_peak) else "no data",
        )

    # --- 2. Camp Fire, November 2018 ----------------------------------------
    # Two solid weeks, not a spike. Require a sustained median, which a
    # one-day join error would not produce.
    for slug in ("san-francisco", "oakland", "san-jose"):
        win = panel[
            (panel["location"] == slug)
            & panel["date"].between("2018-11-08", "2018-11-21")
        ]
        med = win["pm25"].median()
        n_usg = int((win["aqi"] >= 101).sum())
        check(
            f"Camp Fire 2018: {slug} sustained (median PM2.5 > 35)",
            pd.notna(med) and med > 35,
            f"median PM2.5 = {med:.1f} over {len(win)} days, "
            f"{n_usg} at/above AQI 101" if pd.notna(med) else "no data",
        )

    # --- 3. The orange-sky day, 2020-09-09 ----------------------------------
    # The single most instructive day in the record: a thick plume aloft over
    # San Francisco, but surface PM2.5 far lower than the sky implied.
    row = at(panel, "san-francisco", "2020-09-09")
    if row is None:
        check("Orange-sky 2020-09-09: row exists", False, "missing from panel")
    else:
        check(
            "Orange-sky 2020-09-09: HMS plume overhead in SF",
            row["plume"] == 1,
            f"plume = {row['plume']}, density = {row['density']}",
        )
        # It was genuinely bad, but nothing like the sky suggested -- well
        # short of the Camp Fire peak. This is the caveat, asserted.
        sep = panel[
            (panel["location"] == "san-francisco")
            & panel["date"].between("2020-08-15", "2020-09-30")
        ]
        worse = int((sep["pm25"] > row["pm25"]).sum())
        check(
            "Orange-sky 2020-09-09: surface PM2.5 below the season's worst "
            "(plume aloft != smoke at the surface)",
            pd.notna(row["pm25"]) and worse > 0,
            f"PM2.5 = {row['pm25']:.1f}; {worse} other days that season were "
            f"worse at the surface",
        )

    # --- 4. Coast vs inland, September --------------------------------------
    sept = panel[panel["date"].dt.month == 9]
    hmb = sept[sept["location"] == "half-moon-bay"]["smoke_pm"].mean()
    liv = sept[sept["location"] == "livermore"]["smoke_pm"].mean()
    check(
        "September: Half Moon Bay cleaner than Livermore (smoke PM2.5)",
        pd.notna(hmb) and pd.notna(liv) and hmb < liv,
        f"Half Moon Bay {hmb:.2f} vs Livermore {liv:.2f} ug/m3",
    )


def verify_structure(panel: pd.DataFrame) -> None:
    # Gap-free panel: every location has every day exactly once.
    days = pd.date_range(panel["date"].min(), panel["date"].max(), freq="D")
    n_loc = panel["location"].nunique()
    check(
        "panel is gap-free (n_locations x n_days)",
        len(panel) == n_loc * len(days),
        f"{len(panel):,} rows vs {n_loc} x {len(days):,} = {n_loc * len(days):,}",
    )
    check(
        "no duplicate location-days",
        not panel.duplicated(subset=["location", "date"]).any(),
    )

    neg = int((panel["smoke_pm"] < 0).sum())
    check("smoke PM2.5 is never negative", neg == 0, f"{neg} negative values")

    # Non-smoke days must be exactly zero smoke -- but only for OUR method,
    # where that is true by construction. ECHOLab's published values come from
    # their own plume determination, which includes HYSPLIT/AOD gap-filling we
    # deliberately skip, so they legitimately attribute smoke on some days our
    # raw HMS scan calls clear. Applying our invariant to their numbers would
    # be checking them against a rule they never claimed to follow.
    ours = panel[(panel["smoke_source"] == "ours") & (panel["plume"] == 0)]
    bad = int((ours["smoke_pm"].fillna(0) != 0).sum())
    check(
        "our smoke PM2.5 is exactly 0 on non-plume days (by construction)",
        bad == 0,
        f"{bad} of {len(ours):,} non-plume days carry non-zero smoke",
    )

    # The published-vs-ours plume disagreement is informational, not a fault --
    # but it should stay small, and a jump would mean our HMS scan has drifted.
    pub = panel[(panel["smoke_source"].str.startswith("echolab"))
                & (panel["plume"] == 0) & panel["smoke_pm"].notna()]
    n_dis = int((pub["smoke_pm"] > 0).sum())
    frac = n_dis / max(len(pub), 1)
    check(
        "ECHOLab smoke on days our HMS scan saw no plume is rare (<2%)",
        frac < 0.02,
        f"{n_dis:,} of {len(pub):,} published non-plume days ({frac:.2%}); "
        "expected -- they gap-fill plumes with HYSPLIT/AOD and we do not",
        soft=True,
    )

    # AQI must be consistent with the current breakpoints, not the stored
    # historical column. Spot-check the Good/Moderate boundary at 9.0.
    from aqi import pm25_to_aqi  # noqa: PLC0415
    check(
        "AQI recomputed on the current (2024) scale",
        pm25_to_aqi(9.0) == 50 and pm25_to_aqi(9.1) == 51,
        f"pm25_to_aqi(9.0) = {pm25_to_aqi(9.0)}, "
        f"pm25_to_aqi(9.1) = {pm25_to_aqi(9.1)}",
    )

    # Missing data must never have been silently filled with zero.
    for slug in ("napa", "half-moon-bay"):
        g = panel[panel["location"] == slug]
        zeros = int((g["pm25"] == 0).sum())
        check(
            f"{slug}: absent monitor reads as NaN, not 0",
            zeros == 0,
            f"{int(g['pm25'].isna().sum()):,} NaN days, {zeros} exact zeros",
        )


def verify_site_json() -> None:
    if not SITE_DATA.exists():
        check("site JSON exists", False, "run s05 first")
        return

    files = sorted(SITE_DATA.rglob("*.json"))
    check("site JSON files written", len(files) >= 3, f"{len(files)} files")

    for path in files:
        text = path.read_text()
        if "NaN" in text or "Infinity" in text:
            check(f"{path.name} is valid JSON", False, "contains NaN/Infinity")
            continue
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            check(f"{path.name} parses", False, str(exc))

    meta_path = SITE_DATA / "locations.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        check(
            "locations.json lists all ten locations",
            len(meta.get("locations", [])) == 10,
            f"{len(meta.get('locations', []))} locations",
        )
        xv = meta.get("crossval", {})
        r = xv.get("pearson_r_all")
        if r is not None:
            # Our reimplementation should track the published product closely.
            # Below ~0.8 is a finding worth an issue, not something to bury.
            check(
                "our smoke method correlates with ECHOLab on the overlap",
                r >= 0.8,
                f"pearson r = {r} over {xv.get('n_overlap_days'):,} days",
                soft=True,
            )


def main(argv: list[str] | None = None) -> int:
    if not PANEL.exists():
        raise SystemExit(
            "--verify: data/processed/daily_panel.parquet missing. "
            "Run `python run_all.py` first."
        )
    panel = pd.read_parquet(PANEL)
    log(f"[verify] {len(panel):,} location-days, "
        f"{panel['date'].min().date()} -> {panel['date'].max().date()}\n")

    verify_events(panel)
    verify_structure(panel)
    verify_site_json()

    width = max(len(n) for _, n, _ in _results) + 2
    for status, name, detail in _results:
        mark = {PASS: "ok  ", FAIL: "FAIL", WARN: "warn"}[status]
        print(f"  [{mark}] {name:<{width}} {detail}")

    n_fail = sum(1 for s, _, _ in _results if s == FAIL)
    n_warn = sum(1 for s, _, _ in _results if s == WARN)
    n_pass = sum(1 for s, _, _ in _results if s == PASS)
    print(f"\n  {n_pass} passed, {n_warn} warnings, {n_fail} failed")

    if n_fail:
        print("\n  Ground-truth checks failed. Something is wrong with the "
              "pipeline, not with the historical record. Open an issue.")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
