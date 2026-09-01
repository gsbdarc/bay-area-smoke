"""Shared plumbing: cached downloads, CSV streaming, validation helpers."""
from __future__ import annotations

import io
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

USER_AGENT = (
    "bay-area-smoke/0.1 (https://github.com/gsbdarc/bay-area-smoke; "
    "research use; astorer@stanford.edu)"
)


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def download(url: str, dest: Path, *, force: bool = False, timeout: int = 300) -> Path:
    """Download to `dest`, skipping if it already exists.

    Streams to a .part file and renames on success, so an interrupted download
    never leaves a truncated file that a later run would happily treat as
    complete.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        log(f"cached: {dest.name} ({dest.stat().st_size:,} bytes)")
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    log(f"fetching: {url}")
    with requests.get(
        url, stream=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as r:
        r.raise_for_status()
        total = 0
        with open(part, "wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
                total += len(chunk)
    part.rename(dest)
    log(f"saved: {dest.name} ({total:,} bytes)")
    return dest


def try_download(url: str, dest: Path, **kw) -> Path | None:
    """Download, returning None on 404 instead of raising.

    Used where a missing file is expected and meaningful -- NOAA's HMS archive
    genuinely has gaps, and a missing day must become 'unknown', not a crash.
    """
    try:
        return download(url, dest, **kw)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            log(f"not available (404): {url}")
            return None
        raise


def read_zipped_csv(path: Path, **read_csv_kw) -> pd.DataFrame:
    """Read the single CSV inside a zip archive."""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected exactly one CSV in {path.name}, got {names}")
        with z.open(names[0]) as fh:
            # low_memory=False: EPA daily files have mixed types in the
            # method columns, and chunked inference emits a warning per file.
            read_csv_kw.setdefault("low_memory", False)
            return pd.read_csv(io.TextIOWrapper(fh, "utf-8"), **read_csv_kw)


def polite_sleep(seconds: float = 5.0) -> None:
    """EPA asks for a 5-second pause between AQS API requests. Honour it."""
    time.sleep(seconds)


def expect(condition: bool, message: str) -> None:
    """Assertion that fails loudly with context rather than a bare traceback."""
    if not condition:
        print(f"\nDATA CHECK FAILED: {message}\n", file=sys.stderr)
        raise AssertionError(message)


def check_no_duplicate_days(df: pd.DataFrame, keys=("location", "date")) -> None:
    dupes = df.duplicated(subset=list(keys)).sum()
    expect(
        dupes == 0,
        f"{dupes} duplicate rows on {keys}. EPA daily files emit multiple rows "
        f"per site-day (Sample Duration, POC, Pollutant Standard); dedupe before "
        f"this point or every statistic downstream is weighted wrong.",
    )


def report_coverage(df: pd.DataFrame, label: str) -> None:
    """Print a per-location coverage summary. Cheap, and catches silent gaps."""
    log(f"{label}: {len(df):,} rows, {df['location'].nunique()} locations")
    if df.empty:
        return
    summary = (
        df.assign(year=df["date"].dt.year)
        .groupby("location")
        .agg(
            first=("date", "min"),
            last=("date", "max"),
            days=("date", "count"),
            measured=("pm25", lambda s: int(s.notna().sum())),
        )
    )
    for loc, r in summary.iterrows():
        pct = 100 * r["measured"] / r["days"] if r["days"] else 0
        log(
            f"  {loc:15s} {r['first'].date()} .. {r['last'].date()}  "
            f"{r['measured']:6,}/{r['days']:6,} days measured ({pct:4.1f}%)"
        )
