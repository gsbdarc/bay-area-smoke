#!/usr/bin/env python3
"""Master script: rebuild everything from raw sources to the site's JSON.

    python run_all.py                # full build (bootstrap included)
    python run_all.py --refresh-only # cheap stages only, for the monthly cron
    python run_all.py --verify       # ground-truth checks against the build
    python run_all.py --skip-api     # build without an EPA AQS key

Relative paths only, so a fresh clone rebuilds identically on any machine.
Stages are idempotent: completed downloads are cached, and re-running after a
failure resumes rather than restarting. To force a stage, delete its output in
`data/processed/`.

## The two modes, and why they differ

A **full build** runs the bootstrap, which pulls ~8.6 GB of ECHOLab data. That
is a one-time job for a machine with real disk and a fast network -- its small
output is committed, so nobody else ever has to repeat it.

A **refresh** (`--refresh-only`) skips the bootstrap and s01 entirely and
re-runs only what actually moves month to month. It is what the scheduled
GitHub Actions job calls. EPA regenerates its bulk files about twice a year, so
re-downloading them monthly would burn Actions minutes rewriting byte-identical
files.

## Raw data does not live in the repo

Multi-gigabyte downloads go to `/scratch/users/$USER` on the Yens (or
`$SCRATCH`, or `$BAS_RAW_DIR`), never into a backed-up home directory or the
repo. `scripts/config.py` resolves this; `docs/SERVER-SETUP.md` explains it.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

PYTHON = sys.executable

# (module path, human label, runs in a refresh?)
STAGES: list[tuple[str, str, bool]] = [
    ("bootstrap/b01_fetch_echolab.py", "ECHOLab bootstrap download", False),
    ("bootstrap/b02_pick_grid_cells.py", "grid cells + smoke extract", False),
    ("s01_fetch_epa_bulk.py", "EPA bulk PM2.5 2000-present", False),
    ("s02_fetch_epa_api.py", "EPA AQS API (provisional tail)", True),
    ("s03_fetch_hms.py", "NOAA HMS smoke plumes", True),
    ("s04_build_smoke.py", "build panel + attribute smoke", True),
    ("s05_build_site_data.py", "site JSON", True),
]


def run(script: str, label: str, extra: list[str] | None = None) -> None:
    path = SCRIPTS / script
    cmd = [PYTHON, str(path), *(extra or [])]
    print(f"\n{'=' * 72}\n  {label}\n  $ {' '.join(cmd[1:])}\n{'=' * 72}",
          flush=True)
    t0 = time.time()
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"\n*** stage failed: {script} "
                         f"(exit {result.returncode})")
    print(f"  [{time.time() - t0:.0f}s] {label} ok", flush=True)


def build(args: argparse.Namespace) -> None:
    for script, label, in_refresh in STAGES:
        if args.refresh_only and not in_refresh:
            print(f"  skip (refresh): {label}")
            continue
        if args.skip_bootstrap and script.startswith("bootstrap/"):
            print(f"  skip (--skip-bootstrap): {label}")
            continue

        extra: list[str] = []
        if script.startswith("s02"):
            if args.skip_api:
                print(f"  skip (--skip-api): {label}")
                continue
            if args.require_api:
                extra.append("--require-api")
        if script.startswith("s03") and args.refresh_only:
            # Only the current year changes; earlier years are already in the
            # committed parquet.
            extra += ["--first-year", str(args.this_year), "--merge"]

        run(script, label, extra)


def main(argv: list[str] | None = None) -> int:
    import datetime as dt

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--refresh-only", action="store_true",
                    help="skip the bootstrap and EPA bulk; for the cron job")
    ap.add_argument("--skip-bootstrap", action="store_true",
                    help="skip b01/b02 (their outputs are committed)")
    ap.add_argument("--skip-api", action="store_true",
                    help="build without an EPA AQS key; total PM2.5 will end "
                         "at the last certified bulk year")
    ap.add_argument("--require-api", action="store_true",
                    help="fail if the AQS API returns nothing")
    ap.add_argument("--verify", action="store_true",
                    help="run ground-truth checks against the built panel "
                         "instead of building")
    ap.add_argument("--this-year", type=int, default=dt.date.today().year)
    args = ap.parse_args(argv)

    if args.verify:
        from verify import main as verify_main  # noqa: PLC0415
        return verify_main([])

    t0 = time.time()
    build(args)
    print(f"\nAll stages complete in {(time.time() - t0) / 60:.1f} min.")
    print("Next: python run_all.py --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
