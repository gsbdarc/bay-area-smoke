"""Shared configuration: locations, monitors, counties, paths.

Every site ID, coordinate, and date here was verified against the live EPA
AirData files (not recalled from memory) on 2026-08-31. See PLAN.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Relative paths only -- never absolute. See Gentzkow & Shapiro.
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
SITE_DATA = ROOT / "site" / "data"

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
            Monitor("06-001-0007", 37.687526, -121.784217, "Livermore",
                    end="2024-02-04"),
            Monitor("06-001-0016", 37.689750, -121.771550, "Livermore Portola",
                    start="2024-02-05"),
        ),
        coverage_note=(
            "Monitor 06-001-0007 was retired on 2024-02-04 and replaced by "
            "06-001-0016 (Livermore Portola) 1.2 km away. The two are stitched "
            "into one series."
        ),
    ),
    Location(
        slug="napa",
        name="Napa",
        lat=38.278849,
        lon=-122.275024,
        blurb="Wine country. Where the outdoor weddings are.",
        monitors=(
            Monitor("06-055-0004", 38.278849, -122.275024, "Napa Valley College",
                    start="2018-01-01", end="2021-05-20"),
        ),
        coverage_note=(
            "Napa County has had NO EPA PM2.5 monitor since 2021-05-20. Only "
            "2018-2021 is measured; everything else is the modeled grid. Absence "
            "of a monitor is not absence of smoke."
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
