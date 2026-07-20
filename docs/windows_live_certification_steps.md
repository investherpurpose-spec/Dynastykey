# Harris P0 — Windows Live Certification Steps (Operator Guide)

This guide is written for **you**, the operator, to run on your **Windows**
machine using **Git Bash**. Copy each command exactly, run it, and **paste the
output back before moving on**. There is a **STOP** after every stage.

This guide only covers **Phase A (verify the live sources)** and the
**Controlled Trial**. Do **not** run a full Harris baseline yet, and nothing here
marks the system "live verified" — that is a decision we make together after
reviewing your pasted results.

Everything here writes to **trial-only files** (`data/harris-trial.db`,
`data/nightly-trial/`). Your real/production database is never touched, so there
is nothing to break.

---

## One-time setup

Open **Git Bash** (Start menu → type "Git Bash"). Then:

**Step 0.1 — check you have the tools**
```bash
python --version
git --version
```
- **Expected:** a Python 3.x version (e.g. `Python 3.11.x`) and a Git version.
- If `python` says "not found", try `py --version`. If that works, use `py`
  everywhere below instead of `python`.

**Step 0.2 — get the code**
```bash
cd ~
git clone https://github.com/investherpurpose-spec/Dynastykey.git
cd Dynastykey
git fetch origin
git checkout claude/harris-county-argis-scraper-lat1g6
git pull
git log --oneline -1
```
- **Expected:** the last line shows the latest commit (should start with
  `ae82dbb` or newer).

**Step 0.3 — install dependencies**
```bash
python -m pip install -r requirements.txt
```
- **Expected:** ends with `Successfully installed ...` (or "already satisfied").

**Step 0.4 — sanity check (offline, no internet needed)**
```bash
python -m pytest -q
```
- **Expected:** `127 passed` (or more). If anything fails, **STOP and paste it.**

### ⏸️ STOP 0 — paste the output of Steps 0.1–0.4 before continuing.

---

## Phase A — Verify the live sources

Goal: confirm we can actually reach HCAD and the ArcGIS parcel layer, and record
exactly what they return. We change **no** data here — these are read-only checks.

**Step A.1 — inspect the live ArcGIS parcel layer**
```bash
python main.py discover "https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0"
```
- **Expected (success):** a block like
  ```
  Layer: Parcels
    geometry: esriGeometryPolygon
    maxRecordCount: 1000            <- some number
    supportsPagination: True        <- MUST be True
    feature count: 1,5xx,xxx        <- ~1.5 million; write this number down
    fields:
      OBJECTID   esriFieldTypeOID
      HCAD_NUM   ...                 <- an account/parcel id field MUST be here
      ... (many fields)
  ```
  **What to check:** `supportsPagination: True`, an `esriFieldTypeOID` field is
  present (this is what makes paging safe), and a field that looks like the HCAD
  account number (`HCAD_NUM` or `LOWPARCELID`). **Write down the feature count.**
- **Expected (failure / blocked):** `feature count: unavailable (...)` or an
  error mentioning `403`, `Forbidden`, `Max retries`, or `giving up on`. That
  means your network cannot reach the GIS server — **STOP and paste it.**

**Step A.2 — confirm which HCAD data year is actually published**
```bash
curl -sI "https://download.hcad.org/data/CAMA/2026/Real_acct_owner.zip" | head -n 1
curl -sI "https://download.hcad.org/data/CAMA/2025/Real_acct_owner.zip" | head -n 1
```
- **Expected:** one of them prints `HTTP/1.1 200 OK` (or `HTTP/2 200`). **That
  year is the one to use below.** The other may say `403` or `404`.
- If **both** fail (403/000/no output), the download host is blocked from your
  network — **STOP and paste it.**

### ⏸️ STOP A — paste the output of Steps A.1 and A.2. Tell me:
1. the ArcGIS feature count, and
2. which year returned `200`.
**Wait for my confirmation before the trial.**

---

## Phase B — Controlled Trial (bounded, safe)

Goal: run the pipeline end-to-end on a **small, matched** population and check the
join rate — without pretending a small sample is a full run. This uses the
**trial profile**, which validates against bounded numbers instead of full
production counts. Production thresholds are **not** changed.

Replace `<YEAR>` below with the year that returned `200` in Step A.2.

**Step B.1 — run the controlled trial**
```bash
python scripts/nightly.py --county harris --profile trial \
  --work-dir data/nightly-trial --db data/harris-trial.db \
  --hcad-year <YEAR> --gis-limit 5000 --geometry \
  --trial-parcels 5000 --trial-owners 1500000
echo "exit code: $?"
```
- This loads the full HCAD owner file and **only 5,000 parcels**, then checks that
  those 5,000 parcels join to owners. It takes a while (the owner file is large).
- **Expected (success):** the last lines include
  ```
  ... run_finished ... status=PASS ...
  exit code: 0
  ```
  `exit code: 0` = PASS, `1` = PARTIAL (soft warnings, still OK to review),
  `2` = FAIL, `3` = another run is already running.

**Step B.2 — show the trial result**
```bash
python scripts/nightly.py --work-dir data/nightly-trial --status
```
- **Expected:** a "Nightly health" block showing the latest attempt `PASS` (or
  `PARTIAL`) and `published=True`.

**Step B.3 — show the key numbers (counts, join rate, reconciliation)**
```bash
python -c "import json; m=json.load(open('data/nightly-trial/latest_attempt.json')); \
print('status:', m['status'], '| profile:', m['profile'], '| published:', m['published']); \
[print(' -', s['name'], s['status'], '| rows:', s['rows'], '| detail:', s['detail']) for s in m['stages']]"
```
- **Expected (success):** an `identity_join` line showing `join_rate=0.9x` (should
  be **0.90 or higher**), a `gis_pull` line ~5000 rows, an `hcad_load` line with
  the owner count, and `validation ... PASS`.
- **Expected (failure):** `status: FAIL`. Common cause: the owner count fell
  outside the trial band. If a stage detail mentions the owner **row_count**, note
  the real owner number from the `hcad_load` detail and re-run Step B.1 with
  `--trial-owners <that number>`. If `join_rate` is low (well under 0.90), **STOP
  and paste it** — that is a real finding we need to discuss.

### ⏸️ STOP B — paste the output of Steps B.1–B.3. Do not run anything else.

---

## If the trial fails — rollback to the previous safe database

You are safe by design: the trial used its **own** files, so your real database
was never touched. To reset the trial and start clean:

```bash
# from inside the Dynastykey folder
rm -rf data/nightly-trial data/harris-trial.db data/harris-trial.db-wal data/harris-trial.db-shm
```
- This deletes **only** the trial artifacts. Nothing else is affected. You can
  then re-run Phase B from Step B.1.

**For any future full run** (later, not now), always back up first:
```bash
cp data/harris.db "data/harris.db.bak-$(date +%Y%m%d)"     # backup before a run
# to roll back after a bad run:
# mv "data/harris.db.bak-YYYYMMDD" data/harris.db
```
(You do **not** have a `data/harris.db` yet — that only appears when we run a full
baseline, which is a later stage we have not reached.)

---

## What NOT to do right now

- Do **not** run a full baseline (no `--gis-limit`) yet.
- Do **not** treat a green trial as "certified" — it is one data point for review.
- Do **not** proceed to any other work (Clerk feed, tax, dashboards, etc.).

After you paste STOP A and STOP B results, I will review them and tell you the
next step.
