"""Shared configuration: locations, monitors, counties, paths.

Every site ID, coordinate, and date here was verified against the live EPA
AirData files (not recalled from memory) on 2026-08-31. See PLAN.md.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Relative paths only -- never absolute. See Gentzkow & Shapiro.
ROOT = Path(__file__).resolve().parent.parent
PROCESSED = ROOT / "data" / "processed"
SITE_DATA = ROOT / "site" / "data"


def _default_raw_dir() -> Path:
    """Where multi-GB raw downloads land.

    Raw data is bulky, re-downloadable, and never edited in place, so it does
    not belong in a backed-up home directory. Stanford's Yen cluster gives home
    an 80 GiB soft quota and says plainly that it is for "small scripts and
    utilities"; `/scratch/users/$USER` is the 100 TB, non-backed-up space meant
    for exactly this. See https://rcpedia.stanford.edu/_user_guide/storage/

    Resolution order:
      1. `$BAS_RAW_DIR`            -- explicit override, always wins
      2. `/scratch/users/$USER`    -- Yen (and any host with the same layout)
      3. `$SCRATCH`                -- Sherlock and most other clusters
      4. `<repo>/data/raw`         -- laptops, CI, anywhere without scratch

    Scratch is purged after 90 days of no access. That is fine and deliberate:
    everything downstream lives in `data/processed/`, which is committed, so a
    purge costs a re-download and nothing else.
    """
    override = os.environ.get("BAS_RAW_DIR")
    if override:
        return Path(override).expanduser()

    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if user:
        yen_scratch = Path("/scratch/users") / user
        if yen_scratch.is_dir():
            return yen_scratch / "bay-area-smoke" / "raw"

    env_scratch = os.environ.get("SCRATCH")
    if env_scratch and Path(env_scratch).is_dir():
        return Path(env_scratch) / "bay-area-smoke" / "raw"

    return ROOT / "data" / "raw"


RAW = _default_raw_dir()

# EPA state code for California, and the ten counties we care about.
STATE_CA = "06"
BAY_AREA_COUNTIES = {
    "001": "Alameda",
    "013": "Contra Costa",
    "041": "Marin",
    "055": "Napa",
    "075": "San Francisco",
    "081": "San Mateo",
    "085": "Santa Clara",
    "087": "Santa Cruz",
    "095": "Solano",
    "097": "Sonoma",
}

# Full record we attempt. Total PM2.5 starts here; smoke attribution cannot
# begin before HMS does (2005-08-05).
FIRST_YEAR = 2000
HMS_FIRST_DATE = "2005-08-05"

# EPA parameter codes: 88101 is FRM/FEM PM2.5, 88502 is non-FRM. Bay Area
# continuous monitors reported under 88502 until roughly 2012-2015, and Point
# Reyes still does -- so we need both and dedupe, preferring 88101.
PARAM_FRM = "88101"
PARAM_NONFRM = "88502"


@dataclass(frozen=True)
class Monitor:
    """One EPA monitoring site contributing to a location's series."""

    site_id: str            # "06-041-0002"
    lat: float
    lon: float
    label: str
    # Inclusive date bounds where this monitor is the valid source for its
    # location. None means "no bound". Used to stitch site replacements.
    start: str | None = None
    end: str | None = None


@dataclass(frozen=True)
class Location:
    slug: str
    name: str
    lat: float
    lon: float
    blurb: str
    monitors: tuple[Monitor, ...] = field(default_factory=tuple)
    # Set when a location has no usable EPA monitor for part or all of the
    # record and must fall back to the modeled 10 km grid. Surfaced in the UI.
    coverage_note: str | None = None


LOCATIONS: tuple[Location, ...] = (
    Location(
        slug="point-reyes",
        name="Point Reyes",
        lat=38.122979,
        lon=-122.909440,
        blurb="Far north coast. Cleanest marine air in the region.",
        monitors=(
            Monitor("06-041-0002", 38.122979, -122.909440,
                    "Point Reyes National Seashore"),
        ),
        coverage_note=(
            "Reports under EPA parameter 88502, not 88101. It is the region's "
            "best coastal background site and one of only two Bay Area sites "
            "with any 2025 PM2.5 in the bulk files."
        ),
    ),
    Location(
        slug="half-moon-bay",
        name="Half Moon Bay",
        lat=37.463554,
        lon=-122.428586,
        blurb="Mid-peninsula coast, usually under the marine layer.",
        monitors=(),
        coverage_note=(
            "No EPA monitor has ever existed on the San Mateo coast. This "
            "series is entirely from the modeled 10 km grid."
        ),
    ),
    Location(
        slug="san-francisco",
        name="San Francisco",
        lat=37.765946,
        lon=-122.399044,
        blurb="The reference city. Potrero / Arkansas St.",
        monitors=(
            Monitor("06-075-0005", 37.765946, -122.399044,
                    "San Francisco - Arkansas St."),
        ),
    ),
    Location(
        slug="oakland",
        name="Oakland / Berkeley",
        lat=37.743065,
        lon=-122.169935,
        blurb="Inner East Bay.",
        monitors=(
            Monitor("06-001-0009", 37.743065, -122.169935, "Oakland"),
            Monitor("06-001-0011", 37.814781, -122.282347, "Oakland West"),
        ),
    ),
    Location(
        slug="redwood-city",
        name="Redwood City",
        lat=37.482934,
        lon=-122.203370,
        blurb="Mid-peninsula, bay side -- the sheltered counterpart to the coast.",
        monitors=(
            Monitor("06-081-1001", 37.482934, -122.203370, "Redwood City"),
        ),
    ),
    Location(
        slug="san-jose",
        name="San Jose",
        lat=37.348497,
        lon=-121.894898,
        blurb="South Bay, at the closed end of the bay.",
        monitors=(
            Monitor("06-085-0005", 37.348497, -121.894898, "San Jose - Jackson"),
        ),
    ),
    Location(
        slug="livermore",
        name="Livermore",
        lat=37.687526,
        lon=-121.784217,
        blurb="Inland East Bay. Hot, and smoke settles in.",
        monitors=(
            # Site replacement, not a data gap. Stitch or the line breaks.
            # Boundaries set from the live files: 06-001-0007's last reported
            # day is 2024-01-31. 06-001-0016 actually starts reporting in
            # 2023, but we hold it back to the handover so exactly one
            # instrument feeds the series at any moment.
            Monitor("06-001-0007", 37.687526, -121.784217, "Livermore",
                    end="2024-01-31"),
            Monitor("06-001-0016", 37.689750, -121.771550, "Livermore Portola",
                    start="2024-02-01"),
        ),
        coverage_note=(
            "Monitor 06-001-0007 was retired at the end of January 2024 and "
            "replaced by 06-001-0016 (Livermore Portola) 1.2 km away. The two "
            "are stitched into one series."
        ),
    ),
    Location(
        slug="napa",
        name="Napa",
        lat=38.278849,
        lon=-122.275024,
        blurb="Wine country. Where the outdoor weddings are.",
        monitors=(
            # Two sites, a clean handover, and no gap: 06-055-0003 reports
            # through 2018-03-31 and 06-055-0004 starts the very next day.
            # 06-055-0003 was missing from the original plan, which cost Napa
            # eleven years of measurement -- including the 2017 Tubbs/Atlas
            # fires, where it recorded 170.6 and 199.1 ug/m3. Napa is the
            # location this whole site exists for, so this matters.
            Monitor("06-055-0003", 38.310942, -122.296189, "Napa",
                    end="2018-03-31"),
            Monitor("06-055-0004", 38.278849, -122.275024, "Napa Valley College",
                    start="2018-04-01", end="2021-05-20"),
        ),
        coverage_note=(
            "Napa is measured from 2007 to 2021-05-19 by two monitors 4 km "
            "apart (06-055-0003, then 06-055-0004), stitched into one series. "
            "Napa County has had NO EPA PM2.5 monitor since. Before 2007 and "
            "after 2021 the smoke line is the modeled grid, and total PM2.5 "
            "is simply absent. Absence of a monitor is not absence of smoke."
        ),
    ),
    Location(
        slug="sebastopol",
        name="Sebastopol",
        lat=38.403765,
        lon=-122.818294,
        blurb="Sonoma wine country, near the 2017 Tubbs fire footprint.",
        monitors=(
            Monitor("06-097-0004", 38.403765, -122.818294, "Sebastopol"),
        ),
        coverage_note=(
            "Santa Rosa itself has no PM2.5 monitor. Sebastopol, ~12 km "
            "southwest, is the nearest measurement."
        ),
    ),
    Location(
        slug="santa-cruz",
        name="Santa Cruz",
        lat=36.983320,
        lon=-121.988220,
        blurb="South coast, outside the Bay Area air district.",
        monitors=(
            Monitor("06-087-0007", 36.983320, -121.988220, "Santa Cruz"),
        ),
        coverage_note=(
            "Managed by the Monterey Bay air district, which certifies data "
            "faster than BAAQMD -- so Santa Cruz is often the only Bay Area "
            "county present in recent EPA bulk files."
        ),
    ),
)

BY_SLUG = {loc.slug: loc for loc in LOCATIONS}

# Known events used as ground-truth assertions in --verify. If these fail, the
# join is wrong, not the world.
GROUND_TRUTH_EVENTS = (
    ("2017-10-09", ("sebastopol", "napa"), "Tubbs / Atlas fires"),
    ("2018-11-16", ("san-francisco", "oakland", "san-jose"), "Camp Fire smoke"),
    ("2020-09-09", ("san-francisco",), "Orange sky day (smoke aloft)"),
)
