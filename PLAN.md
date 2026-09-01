# Bay Area Smoke Trends — implementation plan

## Context

Wildfire smoke is now the dominant air-quality risk in the California Bay Area. It is
sharply seasonal and sharply local: on the same September afternoon, Half Moon Bay can
be clean marine air while Napa is at Unhealthy. Anyone booking an outdoor event months
or years ahead — a wedding, a conference, a festival — has no good way to ask the
practical question: *"If I book September 14 in Napa, what are the odds of smoke?"*

This project builds a public, reproducible, open-data web tool that answers exactly
that. It lives at `gsbdarc/bay-area-smoke`, publishes to GitHub Pages, and refreshes
itself from public sources on a schedule.

The headline view is a **seasonal risk calendar**: for each location, the historical
probability of a smoky day on each calendar date, with the long-run time series
underneath as supporting evidence.

## Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| Headline view | Seasonal risk calendar (day-of-year probability), time series secondary |
| Smoke metric | Both total PM2.5/AQI **and** smoke-attributed PM2.5, toggleable |
| Repo | `gsbdarc/bay-area-smoke`, **public** |
| Time range | 2000 → present (see coverage note below) |
| 2024–present | ECHOLab as published backbone; **we extend it ourselves** to today |
| EPA AQS API key | User signs up; stored as a GitHub Actions secret |
| Spatial unit | ECHOLab **10 km grid cells** at named places |
| Where it runs | Scaffold here, **execute in a Claude Code session on the server** |
| Deploy target | GitHub Pages (server is only where the pipeline runs) |

## Execution model

Two phases, with the repo itself as the handoff artifact.

**Phase A — this session, on the laptop.** Create `gsbdarc/bay-area-smoke` (public),
scaffold it, commit **this plan as `PLAN.md`**, and push. Nothing heavy runs here; no
large data is downloaded locally. Ends with a repo that is ready to clone and a short
`docs/SERVER-SETUP.md`.

**Phase B — Claude Code session on the server.** Clone the repo, build the venv, run
`python run_all.py`, commit the processed data, and let the deploy workflow publish.
The server session reads `PLAN.md` and picks up from the "Pipeline" section below, so
it needs no context from this conversation.

Why split it: the one-time ECHOLab bootstrap pulls ~1.8 GB and the HMS archive is
another ~100 MB across 21 annual bundles. That belongs on a fast-network box with real
disk, not a laptop and not a GitHub Actions runner.

**Secret handling across both phases.** The AQS key never enters the repo or the
transcript. It lives in three places, each set by an interactive prompt:
- laptop: `~/.aqs.env` (outside the repo)
- server: `.env` in the clone (gitignored, `chmod 600`)
- CI: `gh secret set AQS_KEY` / `AQS_EMAIL` on the repo

Scripts read `AQS_EMAIL` / `AQS_KEY` from the environment and fail with a clear message
if unset, so a missing key is never a silent partial build.

## Key research findings that shape the design

These were verified live, not recalled. They are the reason the pipeline looks the way
it does.

1. **ECHOLab smoke PM2.5 is frozen.** v1 (Harvard Dataverse, `doi:10.7910/DVN/DJVMTV`,
   CC BY-SA 4.0) covers 2006-01-01 → **2020-12-31**. v2 beta (Dropbox, folder
   `version2.0_thru_2023`) covers → **2023-12-31**. The lab's page still promises 2024
   data "by Jan 2025" and has not delivered. There is no API and no rolling feed.
   → We must extend the series ourselves for 2024–present.

2. **ECHOLab files are sparsely encoded.** Per the official README: rows exist only for
   smoke days. An *absent* (cell, date) row means non-smoke day = 0 µg/m³; an *explicit*
   0 means smoke was overhead but PM2.5 was not elevated. A few rows have a genuinely
   empty value — those are NA, **not** 0. Getting this wrong silently inflates or
   deflates every statistic on the page.

3. **EPA bulk AirData is missing recent Bay Area monitors.** The `daily_88101_2025.zip`
   and `_2026.zip` files contain only Santa Cruz county for our region. **2024 is the
   last complete year** — it has all nine BAAQMD counties at 334–366 days each. The
   cause is certification lag: BAAQMD's own 2025 Network Plan still calls its 2023–24
   PM2.5 values "preliminary pending data certification."
   → Bulk gives us 2000 → 2024-12-31; the keyed AQS API (which serves uncertified data)
   fills 2025 → present, labelled **provisional** in the UI.

   Also: the bulk files were last rebuilt 2026-07-13 and the 2026 file stops at
   **2026-05-31**. EPA regenerates roughly twice a year, so a daily cron against bulk is
   pure waste — see the cadence note under Workflows.

4. **One site emits many rows per day** in the daily files — `Sample Duration` is
   `1 HOUR` / `24 HOUR` / `24-HR BLK AVG`, further multiplied by `POC`,
   `Pollutant Standard`, and `Event Type`. Filter to the 24-hour durations, pick one
   standard, use `Arithmetic Mean` as the daily µg/m³, then dedupe on (site, date).
   Naive loading double- or triple-counts days. Note also the lowercase `c` in
   `"county Name"` in `daily_aqi_by_county_YYYY.csv`.

5. **Napa county has no long-running EPA PM2.5 monitor** in the bulk files. This is
   precisely why the modeled 10 km grid matters — it is the only way to give wine
   country real coverage.

6. **The AQI scale changed on 2024-05-06.** Current PM2.5 breakpoints (µg/m³ → AQI):
   0.0–9.0 Good; 9.1–35.4 Moderate; 35.5–55.4 USG; 55.5–125.4 Unhealthy;
   125.5–225.4 Very Unhealthy; 225.5–325.4 Hazardous. AQI values stored in EPA's
   historical files used the breakpoints in force at the time and are **not comparable
   across years**. → Recompute AQI from raw concentration with current breakpoints for
   every year, so the whole series sits on one scale.

7. **NOAA HMS smoke polygons are live and free.** Annual bundles at
   `.../Smoke_Polygons/Shapefile/Annual_Bundles/hms_smokeYYYY.zip`, years 2005→2026,
   646 KB–15 MB each. Fields: `Satellite, Start, End, Density`, WGS84,
   `Start`/`End` encoded `YYYYDDD HHMM`. This is what makes extending ECHOLab possible.

8. **GitHub Pages serves gzip only** (no brotli), and `GITHUB_TOKEN` commits do **not**
   trigger other workflows. → The refresh job must deploy in the same run.

### Coverage consequence, to be stated plainly in the UI

- **Total PM2.5 / AQI:** 2000 → 2024 certified (bulk), 2025 → present provisional (API)
- **Smoke-attributed PM2.5:** 2006 → 2023 published (ECHOLab), 2024 → present our extension

HMS itself only starts 2005-08-05, so no smoke attribution is possible before then.

Four provenance states, and the UI distinguishes all four rather than drawing one
undifferentiated line: certified measurement, provisional measurement, published model,
our model. The climatology denominators must count only years with adequate coverage at
that location — otherwise Napa's post-2021 monitor gap silently reads as "no smoke."

## Data sources

| Source | Use | Access |
|---|---|---|
| ECHOLab smoke PM2.5 v2 (10 km grid) | Smoke layer 2006–2023 | Dropbox folder zip, `&dl=1` |
| ECHOLab v1 (10 km grid), Dataverse | Stable fallback, 2006–2020 | `dataverse.harvard.edu/api/access/datafile/8550337?format=original` |
| `10km_grid_wgs84.shp` | grid_id → geometry | Dataverse `8550317` (+ `.dbf` 8550315, `.shx` 8550318, `.prj` 8550314) |
| EPA AirData `daily_88101_YYYY.zip`, `daily_88502_YYYY.zip` | Total PM2.5, 2000–2024 | Public, no key |
| EPA AQS API (`byCounty`, `param=88101`) | Total PM2.5, 2025→present (provisional) | Free key (email signup) |
| EPA `aqs_sites.zip` | Site names, lat/lon, open/close dates | Public, no key |
| NOAA HMS annual bundles | Smoke-day flags, 2005→present | Public, no key |

**Dropbox link rot is a real risk.** Mitigation: the heavy ECHOLab download is a
**one-time bootstrap run locally**, and the small Bay Area subset is committed to the
repo. If the link rotates later, the committed extract still works and only the
bootstrap step needs repair. The cron job never touches Dropbox.

## Locations (10)

Chosen for the coast / bay / inland contrast the user asked for. Each maps to the
ECHOLab 10 km cell containing the point, plus the nearest EPA monitor where one exists.

| Location | Why | EPA monitor (verified against live files) |
|---|---|---|
| Point Reyes / West Marin | Far north coast, cleanest marine air | **06-041-0002**, rural, est. 1987; reports under **88502** not 88101 |
| Half Moon Bay | Mid-peninsula coast, marine layer | **none** — no EPA site on the San Mateo coast. Grid only. |
| San Francisco | The reference city | 06-075-0005 (Arkansas St., Potrero) |
| Oakland / Berkeley | Inner East Bay | 06-001-0009 (Oakland), 06-001-0011 (Oakland West) |
| Redwood City | Mid-peninsula, bay side | 06-081-1001 |
| San Jose | South Bay, the user asked for it | 06-085-0005 (Jackson) |
| Livermore | Inland East Bay, hot and trapped | 06-001-0007 → **06-001-0016** (swapped 2024-02-04) |
| Napa | Wine country / wedding venues | 06-055-0004, but **only 2018 → 2021-05-20**. Grid otherwise. |
| Sebastopol (Sonoma) | Wine country; nearest PM2.5 to 2017 Tubbs | 06-097-0004 — **Santa Rosa itself has no PM2.5 monitor** |
| Santa Cruz | South coast | 06-087-0007 |

### Monitor discontinuities that must be handled explicitly

These are artifacts that look exactly like real signal if ignored, so each gets code
that handles it *and* a note in the UI:

- **Livermore site swap, 2024-02-04.** `06-001-0007` ends, `06-001-0016` (Livermore
  Portola) begins. Stitch into one series or the inland East Bay line breaks mid-2024.
- **Napa goes dark 2021-05-20.** Napa County has had zero EPA PM2.5 coverage since.
  Its line must come from the modeled grid, not be drawn as if measured.
- **Point Reyes lives in 88502.** If we only pull 88101 we lose the single best coastal
  background site entirely.
- **88101/88502 crossover.** 88502 carries the continuous monitors ~1999–2012 and is
  near-useless for the Bay Area after ~2015 (by 2018 only Point Reyes remains). Union
  both, dedupe on (site, date), prefer 88101 where both exist.

## Pipeline

Python ≥3.11 in a project-local `.venv`, created the same way on the server, the
laptop, and CI. Pinned `requirements.txt`. Reading shapefiles via **pyshp + shapely**
rather than geopandas — no GDAL system dependency, which matters on a shared cluster
where we can't install system packages.

Raw downloads land in `data/raw/` (gitignored, never edited). Everything computed goes
to `data/processed/`. Every stage validates with **pandera** schemas.

```
scripts/
  bootstrap/
    b01_fetch_echolab.py   # one-time, local: big Dropbox/Dataverse pulls
    b02_pick_grid_cells.py # shapefile -> the 10 cells we care about
  s01_fetch_epa_bulk.py    # AirData 2000-2023, Bay Area counties only
  s02_fetch_epa_api.py     # AQS API 2024->present (needs secret)
  s03_fetch_hms.py         # HMS annual bundles -> per-location smoke_day flags
  s04_build_smoke.py       # Childs method for the extension period
  s05_build_site_data.py   # climatology + columnar JSON for the site
run_all.py                 # master script, relative paths only
```

### The smoke-attribution method (extension period only)

Reimplements the station-level half of Childs et al. 2022, verified against their R
source (`04_01_calculate_station_smokePM_using_polygons.R`):

1. Complete location × day panel; missing PM2.5 stays NA, never 0.
2. `plume = 1` if the point falls inside any HMS polygon that day (any density);
   NA when the HMS file is missing for that date.
3. Background = median PM2.5 over **non-smoke days**, grouped by
   (location, calendar month), pooled across year−1, year, year+1.
4. `smokePM = plume ? max(pm25 − background, 0) : 0`.

Two honest deviations, both to be disclosed on the page:
- The 3-year window is **centered**, which is impossible for the current year. For
  2026 we use a trailing 3-year window and label those values provisional.
- We skip their HYSPLIT/AOD gap-filling and the gradient-boosted spatial
  interpolation. We are producing point estimates at 10 locations, not a national grid.

The seam between published ECHOLab values and our extension will be visible in the UI
(different line treatment) and stated in the methods section — not blended silently.

### Site data output

Columnar JSON (`{"date":[...], "pm25":[...], "smokepm":[...]}`), which cuts size ~3–5×
versus row objects and parses faster:

- `site/data/locations.json` — metadata, grid cell ids, monitor ids
- `site/data/daily/<slug>.json` — daily series per location
- `site/data/climatology.json` — day-of-year statistics

Estimated total well under 2 MB uncompressed — comfortably in the "effectively free"
range for Pages.

**Climatology, per location per day-of-year** (±7-day window to smooth):
- fraction of years exceeding each threshold
- median and 90th percentile PM2.5
- worst year on record and its value

Two thresholds, user-toggleable: **AQI ≥ 101** (Unhealthy for Sensitive Groups — the
"don't hold it outside" line) and **smoke PM2.5 ≥ 5 µg/m³** (noticeable smoke).

## Site

Plain static HTML/CSS/ESM in `site/`, no build step. **Observable Plot 0.6.17** via
esm.sh — it is the one library that covers both required chart types idiomatically
(`Plot.cell` for the calendar, `Plot.lineY`/`areaY` for the series, `Plot.tip` for
tooltips) at ~160 KB gzipped.

Layout, top to bottom:
1. Location picker (10 places) + metric toggle (total AQI / smoke PM2.5).
2. **Seasonal risk calendar** — day-of-year × probability, the headline. Click a date
   for a detail card: "Sept 14 in Napa: smoky in 6 of 17 years (35%); worst 2020."
3. **Small-multiples strip** — the same calendar for all 10 locations at once, so the
   coast-vs-inland contrast is visible in one glance.
4. **Long-run time series** — 2000→present daily, major fire years annotated
   (2017 Tubbs, 2018 Camp, 2020 August Complex/CZU, 2021 Dixie).
5. **Methods & caveats** — data provenance, the ECHOLab/extension seam, and the
   "smoke aloft" caveat (HMS sees a column, not the surface; 2020-09-09, the orange-sky
   day, had modest *surface* PM2.5 — a perfect illustration).

I will run the `dataviz` skill before writing any chart code, and check the rendered
page in Chrome.

## Repo scaffolding

Following the github-for-research conventions: GitHub Flow throughout (branch → commit
→ PR → merge), issues for known problems, Claude credited on every commit and PR.

- `PLAN.md` — this plan, committed so the server session can execute from it
- `docs/SERVER-SETUP.md` — clone, `python -m venv`, `pip install -r requirements.txt`,
  write `.env`, `python run_all.py`. Written host-agnostically, with a Stanford section
  covering `kinit` and running long jobs under `tmux` so they survive ticket expiry.
- `README.md` with a "How to reproduce" section and a script → output map
- `LICENSE` (MIT, code) + `LICENSE-data` (**CC BY-SA 4.0** — required, ECHOLab's
  share-alike propagates to our derived extract) + `CITATION.cff`
- `.gitignore` from GitHub's Python template plus `data/raw/`, `.venv`, `.env`
- `.env.example` listing `AQS_EMAIL` / `AQS_KEY` by name only; real values go in
  GitHub Actions secrets and are never committed. Push protection enabled.
- Issues opened up front for the two known data problems: the EPA bulk Bay Area gap,
  and the ECHOLab freeze.

**Workflows** (versions verified current as of 2026-08-31):

- `deploy.yml` — on push to `main`; `checkout@v7`, `configure-pages@v6`,
  `upload-pages-artifact@v5`, `deploy-pages@v5`; `permissions: contents:read,
  pages:write, id-token:write`; `concurrency: {group: pages}`.
- `refresh.yml` — cron `17 6 1 * *` (monthly, off-the-hour; GitHub delays runs at :00),
  fetches recent data, commits, **and deploys in the same job** because `GITHUB_TOKEN`
  commits do not trigger other workflows. Adds `contents: write`. Guards against empty
  commits and rebases before push.

  **Cadence rationale:** bulk AirData only moves ~twice a year and the AQS API's
  Bay Area backfill changes slowly, so daily polling would burn Actions minutes to
  rewrite identical files. Monthly matches how fast the upstream data actually moves.
  HMS is the one genuinely daily source, but it only matters for the current fire
  season; `workflow_dispatch` covers the "check it now" case during an active fire.

Pages source set via `gh api -X POST repos/gsbdarc/bay-area-smoke/pages
-f build_type=workflow` (my token has `repo` scope and I am an org admin).

Known trap to document in the README: on public repos, scheduled workflows are disabled
after 60 days of no activity, and bot commits do not count as activity.

## Verification

1. **Data integrity** — pandera schemas on every stage; row counts before/after each
   join; assert the sparse-encoding expansion produces exactly
   `n_locations × n_days` rows; assert no NaN leaks into published JSON.
2. **Ground truth spot checks** against known events, as explicit test assertions:
   - 2017-10-09 Tubbs → Santa Rosa and Napa extreme
   - 2018-11-09..21 Camp Fire → SF, Oakland, San Jose sustained Unhealthy+
   - 2020-09-09 orange-sky day → HMS plume present but *surface* PM2.5 only moderate
     (this is the caveat, and a real test that we haven't conflated the two)
   - Half Moon Bay consistently cleaner than Livermore in September
3. **Cross-validation** — where ECHOLab published values and our extension method
   overlap (2006–2023), run our method on the same years and report correlation. If it
   diverges badly, that is a finding and gets logged as an issue, not buried.
4. **Reproducibility** — `python run_all.py` from a fresh clone rebuilds everything.
5. **End-to-end** — trigger the deploy workflow, then load the live Pages URL in Chrome:
   confirm charts render, the location picker and metric toggle work, tooltips fire, no
   console errors, and the page is responsive at mobile width.

## Open risk

The Dropbox v2 link is the least durable dependency. If it fails at bootstrap time, the
fallback is Dataverse v1 (2006–2020, stable DOI) with our extension covering 2021→
instead of 2024→. That widens the "our method" span but breaks nothing, and I'll note
which path was taken in the README.
