# Bay Area Smoke Trends

**How likely is wildfire smoke on a given date, at a given place in the Bay Area?**

If you are booking a wedding, a conference, or any outdoor event months or years out,
that is the question you actually need answered — and it is not the question an air
quality site normally answers. This project turns two decades of public monitoring and
modeled-smoke data into a **seasonal risk calendar**: for each of ten Bay Area
locations, the historical probability of a smoky day on each calendar date.

Live site: https://gsbdarc.github.io/bay-area-smoke/

> **Status: pipeline built and run.** All stages produce data and the site renders
> from it. See [`PLAN.md`](PLAN.md) for the original design and the notes below for
> where reality differed from it.

## What it shows

Ten locations chosen to span the real geographic contrast — the coast is not the city
and the city is not wine country:

Point Reyes · Half Moon Bay · San Francisco · Oakland/Berkeley · Redwood City ·
San Jose · Livermore · Napa · Sebastopol · Santa Cruz

Two metrics, toggleable:

- **Total PM2.5 / AQI** — what monitors actually measured. Includes non-smoke pollution.
- **Smoke-attributed PM2.5** — the wildfire component, isolated by the method of
  Childs et al. (2022): PM2.5 above a location- and month-specific non-smoke baseline,
  on days a NOAA satellite saw a smoke plume overhead.

## Data coverage, honestly

Different parts of the record have different standing, and the site distinguishes all
four rather than drawing one confident-looking line:

| Layer | Range | Provenance |
|---|---|---|
| Total PM2.5 | 2000 – 2024 | EPA AirData bulk, agency-certified |
| Total PM2.5 | 2025 – present | EPA AQS API, **provisional — and only some locations** (see below) |
| Smoke PM2.5 | 2006 – 2023 | Stanford ECHOLab v2, published |
| Smoke PM2.5 | 2024 – present | **Our extension** of their method, where a monitor still exists |

Smoke attribution cannot start before **2005-08-05**, when NOAA's smoke-plume record
begins.

### The 2025 hole, and why it is a moving target

The plan assumed the keyed AQS API would serve uncertified data and so cover
2025 → present. For a long time it did not: on 2026-09-01, September 2024
returned 524 rows for Alameda County and September 2025 returned **none**, via
both the daily and raw-sample endpoints. BAAQMD simply had not submitted
([issue #7](https://github.com/gsbdarc/bay-area-smoke/issues/7)).

Later the same day, Alameda started returning 2025 data. **BAAQMD's backfill is
arriving piecemeal, site by site** — Oakland and Concord first, with San
Francisco, Santa Clara, Sonoma and Napa still empty at the time of writing.
Sites run by other agencies (Point Reyes, Santa Cruz) were never affected.

So the size of the hole is genuinely in flux, and any number written here goes
stale. **The authoritative answer is the per-location coverage in
`site/data/locations.json`**, regenerated on every build and shown in the site's
methods table. The monthly refresh picks up new submissions automatically; no
code change is needed when a county appears.

None of this touches the headline view — the seasonal risk calendar rests on
2006–2024, close to two decades. It only shortens the tail of the time series,
and the site states the real end date per location rather than trailing off
without explanation.

### Known gaps that are not zeros

Three real holes in the monitoring network that would otherwise read as good news:

- **Napa has had no EPA PM2.5 monitor since 2021-05-20.** Its line comes from the
  modeled grid, not from measurement.
- **Half Moon Bay has never had one.** There is no EPA site on the San Mateo coast.
- **Santa Rosa has no PM2.5 monitor**; Sebastopol is the nearest, ~12 km southwest.

And one artifact that would read as a trend: Livermore's monitor was **replaced on
2024-02-04** (site `06-001-0007` → `06-001-0016`). The pipeline stitches them.

## How to reproduce

Full setup for a fresh machine — including the Stanford cluster — is in
[`docs/SERVER-SETUP.md`](docs/SERVER-SETUP.md). The short version:

```bash
git clone https://github.com/gsbdarc/bay-area-smoke.git
cd bay-area-smoke
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then add your free EPA AQS key -- see below
python run_all.py
```

`run_all.py` rebuilds everything from raw sources to the JSON the site loads. It uses
relative paths only and is safe to re-run; completed download stages are skipped.

### You need a free EPA AQS key

Only for 2025-and-later data. Request one (it is emailed to you, instantly, from
`aqsdatamart@epa.gov`):

```bash
curl "https://aqs.epa.gov/data/api/signup?email=YOUR_EMAIL"
```

Put it in `.env` as `AQS_EMAIL` and `AQS_KEY`. **Never commit it** — `.env` is
gitignored. EPA asks for max 10 requests/minute and a 5-second pause between calls;
the pipeline respects both.

### Script → output map

| Script | Produces |
|---|---|
| `scripts/bootstrap/b01_fetch_echolab.py` | raw ECHOLab v2 + 10 km grid (**~8.6 GB**, one time) |
| `scripts/bootstrap/b02_pick_grid_cells.py` | `data/processed/grid_cells.csv`, `echolab_smokepm.parquet` |
| `scripts/s01_fetch_epa_bulk.py` | `data/processed/epa_daily_bulk.parquet` (2000–2024 certified) |
| `scripts/s02_fetch_epa_api.py` | `data/processed/epa_daily_api.parquet` (2025→, provisional) |
| `scripts/s03_fetch_hms.py` | `data/processed/smoke_days.parquet` |
| `scripts/s04_build_smoke.py` | `data/processed/daily_panel.parquet`, `crossval.json` |
| `scripts/s05_build_site_data.py` | `site/data/*.json` → the charts |
| `scripts/verify.py` | ground-truth report (`run_all.py --verify`) |

Shared helpers: `scripts/fetch.py` (cached atomic downloads), `scripts/config.py`
(locations and monitors), `scripts/aqi.py`, `scripts/smoke.py`.

**Raw downloads do not live in the repo.** They go to `/scratch/users/$USER` on the
Yens, `$SCRATCH` elsewhere, or `$BAS_RAW_DIR` if you set it — falling back to
`data/raw/` only when no scratch space exists. Stanford's guidance is explicit that
home directories (80 GiB soft quota) are for "small scripts and utilities", not
multi-gigabyte datasets; see
[RCpedia storage](https://rcpedia.stanford.edu/_user_guide/storage/). Raw data is
**never edited in place**, and everything computed goes to `data/processed/`, which
*is* committed — that is what lets the monthly refresh run without ever
re-downloading 8.6 GB.

The ECHOLab v2 download is 8.6 GB rather than the 1.8 GB the plan estimated: it is a
shared Dropbox *folder* with no per-file URL, so `&dl=1` brings the county, tract and
ZCTA aggregations along with the 10 km grid we actually use. It buys three more years
of published smoke (through 2023) than the Dataverse v1 fallback, which is why we
take it.

## Caveats worth reading before you trust a number

- **Smoke PM2.5 is an attribution, not a measurement.** It is the PM2.5 anomaly above a
  local baseline on days smoke was overhead. It absorbs any other anomaly that happened
  to coincide, and it reports exactly zero on days the satellite saw no plume — even if
  smoke was present.
- **A plume overhead is not smoke at the surface.** NOAA HMS sees a vertical column.
  The clearest illustration is 2020-09-09, San Francisco's orange-sky day: dramatic
  overhead, but comparatively modest *ground-level* PM2.5.
- **Past frequency is not a forecast.** A 35% historical smoke rate on September 14 is
  a base rate from ~20 observations, not a probability for next year. Fire regimes are
  changing faster than the record can track.

## Contributing

Problems, questions, and ideas belong in
[issues](https://github.com/gsbdarc/bay-area-smoke/issues). Changes go through the
standard loop: branch → commit → pull request → review → merge.

## License

Code is [MIT](LICENSE). Data is [CC BY-SA 4.0](LICENSE-data) — ShareAlike is inherited
from Stanford ECHOLab's dataset and is not optional. Citation info in
[`CITATION.cff`](CITATION.cff); upstream sources and how to credit them are in
[`LICENSE-data`](LICENSE-data).

## Maintenance note

On public repos, **GitHub disables scheduled workflows after 60 days with no repository
activity**, and commits made by `GITHUB_TOKEN` do not count as activity. If the monthly
refresh goes quiet, re-enable it with `gh workflow enable refresh.yml`.
