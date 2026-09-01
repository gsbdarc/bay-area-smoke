"""Shared download helper: cached, atomic, resumable-ish, polite.

Every network call in the pipeline goes through here so that the caching and
retry policy is defined exactly once.

Two properties matter more than speed:

1. **Atomic.** Downloads land in `<name>.part` and are renamed into place only
   after the full body arrives. A killed job therefore never leaves a truncated
   zip that a later run would happily treat as complete. This is the whole
   reason `run_all.py` can be re-run after a failure.
2. **Cached.** If the destination exists and is non-empty, we do not re-fetch.
   Raw data is immutable by convention (`data/raw/` is never edited in place),
   so a present file is a finished file.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import requests

# EPA asks for <=10 requests/minute with a pause between calls. We are well
# under that, but the courtesy delay is applied to keyed API calls in s02.
USER_AGENT = (
    "bay-area-smoke/1.0 (+https://github.com/gsbdarc/bay-area-smoke) "
    "research pipeline; contact via repo issues"
)

RETRY_STATUS = {429, 500, 502, 503, 504}


def log(msg: str) -> None:
    """Progress to stderr, so stdout stays clean for any piped output."""
    print(msg, file=sys.stderr, flush=True)


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def download(
    url: str,
    dest: Path,
    *,
    force: bool = False,
    tries: int = 4,
    timeout: int = 60,
    expect_min_bytes: int = 1,
    progress_every: int = 64 * 1024 * 1024,
) -> Path:
    """Fetch `url` to `dest`, skipping the work if it is already there.

    Streams to a `.part` sidecar and renames on success, so an interrupted run
    never leaves a half-file that looks complete to the next run.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size >= expect_min_bytes and not force:
        log(f"  cached  {dest.name} ({human(dest.stat().st_size)})")
        return dest

    part = dest.with_suffix(dest.suffix + ".part")
    last_err: Exception | None = None

    for attempt in range(1, tries + 1):
        try:
            with requests.get(
                url,
                stream=True,
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
            ) as r:
                if r.status_code in RETRY_STATUS:
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()

                total = int(r.headers.get("content-length") or 0)
                got = 0
                next_tick = progress_every
                with open(part, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=1 << 20):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        got += len(chunk)
                        if got >= next_tick:
                            pct = f" ({100 * got / total:.0f}%)" if total else ""
                            log(f"    ... {human(got)}{pct}")
                            next_tick += progress_every

            if got < expect_min_bytes:
                raise OSError(f"suspiciously small response: {got} bytes")

            part.replace(dest)
            log(f"  fetched {dest.name} ({human(got)})")
            return dest

        except Exception as exc:  # noqa: BLE001 -- retry anything transient
            last_err = exc
            part.unlink(missing_ok=True)
            if attempt < tries:
                back = 2**attempt
                log(f"  retry {attempt}/{tries - 1} for {dest.name} after {exc} "
                    f"({back}s)")
                time.sleep(back)

    raise RuntimeError(f"failed to download {url}: {last_err}")


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read `.env` into a dict without overwriting real environment variables.

    Deliberately tiny and dependency-free. Values already present in the
    environment win, so CI (which injects GitHub Actions secrets) needs no
    `.env` file at all and a stray local one can never shadow it.

    Nothing here ever logs a value.
    """
    import os

    root = Path(__file__).resolve().parent.parent
    path = path or root / ".env"
    out: dict[str, str] = {}

    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            out[key.strip()] = val.strip().strip("'\"")

    out.update({k: v for k, v in os.environ.items() if k in {"AQS_EMAIL", "AQS_KEY"}})
    return {k: v for k, v in out.items() if v}


def free_space_gb(path: Path) -> float:
    """Free space on the filesystem holding `path`, in GB."""
    p = Path(path)
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free / 1024**3


def require_space(path: Path, need_gb: float) -> None:
    """Fail before a huge download rather than halfway through it."""
    free = free_space_gb(path)
    if free < need_gb:
        raise SystemExit(
            f"Not enough disk at {path}: {free:.1f} GB free, need ~{need_gb:.1f} GB.\n"
            "Move the clone to scratch/project space (see docs/SERVER-SETUP.md)."
        )
