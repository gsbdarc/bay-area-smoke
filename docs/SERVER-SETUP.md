# Running the pipeline on a server

This project is designed so the heavy work happens on a machine with real disk and a
fast network, not on a laptop and not on a GitHub Actions runner. The one-time
bootstrap downloads **~8.6 GB** from Dropbox (or ~1.8 GB from Harvard Dataverse if
you take the v1 fallback), plus ~280 MB of EPA bulk files and ~100 MB of NOAA
smoke-plume archives.

Everything here is host-agnostic; there is a Stanford-specific section at the end.

## 0. Where the raw data goes — read this first

**Raw downloads do not go in the repo and must not go in your home directory.**
`scripts/config.py` picks the destination automatically, in this order:

1. `$BAS_RAW_DIR` — explicit override, always wins
2. `/scratch/users/$USER` — the Yens
3. `$SCRATCH` — Sherlock and most other clusters
4. `<repo>/data/raw` — laptops and CI, where no scratch exists

Check what it resolved to before you start:

```bash
python -c "import sys; sys.path.insert(0,'scripts'); import config; print(config.RAW)"
```

On the Yens, home has an **80 GiB soft quota** and is documented as being for
"small scripts and utilities"; `/scratch/users/$USER` is the 100 TB space intended
for exactly this ([RCpedia storage](https://rcpedia.stanford.edu/_user_guide/storage/)).
Scratch is **not backed up and is purged after 90 days** — which is fine and
deliberate. Everything the site needs ends up in `data/processed/`, which is
committed, so a purge costs a re-download and nothing else.

## 1. Clone

```bash
git clone https://github.com/gsbdarc/bay-area-smoke.git
cd bay-area-smoke
```

## 2. Environment

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Deliberately **no geopandas or GDAL** — shapefiles are read with `pyshp` and geometry
handled by `shapely`, both pip-installable wheels. This matters on shared clusters
where you cannot install system packages.

Check it:

```bash
python -c "import pandas, numpy, shapefile, shapely, pandera; print('deps ok')"
```

## 3. Secrets

You need a free EPA AQS key for 2025-and-later data. It is emailed to you instantly:

```bash
curl "https://aqs.epa.gov/data/api/signup?email=YOUR_EMAIL"
```

Write it to `.env` without it ever appearing in your shell history or on screen:

```bash
cp .env.example .env
printf 'AQS_EMAIL=%s\n' "YOUR_EMAIL" > .env
printf 'AQS_KEY=' >> .env
read -rs k && echo "$k" >> .env && unset k
chmod 600 .env
```

`.env` is gitignored. Verify before you ever commit:

```bash
git check-ignore -v .env    # should print a .gitignore line
```

If the key is missing, the pipeline fails loudly at stage `s02` rather than silently
producing a dataset that stops in 2024.

## 4. Run

```bash
python run_all.py
```

Stages are idempotent and skip completed downloads, so re-running after a failure
resumes rather than restarting. To force a stage, delete its output in
`data/processed/`.

Expect roughly:

Measured on a Yen node, 2026-08-31:

| Stage | Time | Network |
|---|---|---|
| `b01` ECHOLab bootstrap | ~4 min | 8.6 GB |
| `b02` grid cells + 80 M-row scan | ~4 min | none (reads the zip) |
| `s01` EPA bulk 2000–present | 20–30 min | ~280 MB |
| `s02` EPA AQS API 2025→ | ~2 min | small (rate-limited on purpose) |
| `s03` NOAA HMS 2005→ | ~8 min | ~100 MB |
| `s04` build smoke panel | ~1 min | none |
| `s05` build site JSON | ~1 min | none |

`b01` and `b02` are one-time. Their small outputs are committed, so everyone
after you can run `python run_all.py --skip-bootstrap`.

**Run it under `tmux` or `screen`.** The bootstrap outlasts most SSH sessions and
every Kerberos ticket:

```bash
tmux new -s smoke
python run_all.py
# detach with Ctrl-b then d; reattach later with: tmux attach -t smoke
```

## 5. Publish

`site/data/` is committed, and pushing to `main` triggers the deploy workflow.

```bash
git checkout -b data/refresh-$(date -u +%Y-%m-%d)
git add data/processed site/data
git commit -m "data: rebuild panel and site JSON"
git push -u origin HEAD
gh pr create --fill
```

Then merge the PR. Watch the deploy:

```bash
gh run watch
```

## 6. Sanity checks before you trust the output

The test suite covers the pure logic; these check the *real* data actually landed:

```bash
pytest -q                                    # logic tests, no network
python run_all.py --verify                   # ground-truth event assertions
```

`--verify` asserts against known events — the October 2017 Tubbs fire spiking Napa and
Sebastopol, the November 2018 Camp Fire driving SF/Oakland/San Jose to Unhealthy for
about two weeks, and Half Moon Bay running cleaner than Livermore through September.
If those fail, something is wrong with the join, not with the world.

---

## Stanford specifics

Tested targets: the GSB **Yen** cluster (`yen.stanford.edu`) and **Sherlock**
(`login.sherlock.stanford.edu`).

**Get a Kerberos ticket first**, or the clone and push will fail:

```bash
kinit YOUR_SUNETID@stanford.edu
klist                 # confirm you have a ticket
```

Tickets expire. That is the main reason to run under `tmux` — a detached session
survives a dead ticket, though you will need a fresh `kinit` before the final
`git push`.

**Where to put the data.** Do not run the bootstrap in your home directory; the raw
downloads will blow through a home quota. Put the clone on scratch or project space and
check you have ~5 GB free:

```bash
df -h .
```

On Yen, `/zfs/projects/...` or your group's shared space is the right home for this. On
Sherlock, use `$SCRATCH`.

**Python.** If the system Python is older than 3.11, load a module first:

```bash
module avail python
module load python/3.12    # or whatever the cluster provides
```

**Do not run the bootstrap on a login node** if the cluster discourages it. On Yen,
`yen-slurm` handles batch work; on Sherlock, `sbatch` or an `sdev` interactive session.
The pipeline is single-threaded and I/O-bound — modest CPU, a few GB of RAM, and time.
