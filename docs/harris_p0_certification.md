# Harris County P0 — Live Certification Procedure

Operational runbook for taking the P0 property spine from *implementation
complete* to *production certified* on a network-enabled host. This is an
execution/ops document, not architecture — the frozen design docs are unchanged.

Outbound access to `hcad.org` / `download.hcad.org` and `gis.hctx.net` is
**blocked in the build environment**, so every item below that touches a live
source is BLOCKED here and must be run where those hosts are reachable.

---

## 1. Certification language (authoritative definitions)

Two distinct states — do not conflate them:

- **LIVE VERIFIED** — one successful live run has confirmed **source access and
  schema** (the URL resolves, auth works, the expected columns/fields are
  present, and a bounded pull parses). A single green run earns this and nothing
  more.
- **PRODUCTION CERTIFIED** — the spine may run unattended nightly. This is a
  higher bar and is **not** implied by a single successful run.

**PRODUCTION CERTIFIED requires ALL of:**

1. Verified source **metadata and fields** (HCAD codebook columns + ArcGIS layer
   fields confirmed against a live sample).
2. **Controlled sample inspection** (a bounded population manually eyeballed).
3. A **complete baseline run** (full HCAD + full GIS, staged and promoted).
4. **Source-count reconciliation** (loaded counts vs the source's own counts).
5. **Manually checked parcel matches** (spot-check parcel→owner joins against
   the HCAD public record).
6. **Repeated successful scheduled runs** (≥ 3 consecutive nightly PASS).
7. A **forced-failure recovery test** (a deliberately broken run must FAIL, not
   publish, and preserve the last good spine).
8. **No silent publication of partial data** (proven by the staged-load and
   regression gates below).

Until all eight hold, the spine is at most LIVE VERIFIED.

---

## 2. Confirm the HCAD data year FIRST (do not assume)

The year is **not** hardcoded. `main.py load-owners` defaults to the current
calendar year; `scripts/nightly.py` requires an explicit `--hcad-year`/`--hcad-zip`.
The Texas certified roll typically finalizes ~late July, so mid-year the current
roll may still be **preliminary** — confirm which year/zip actually exists:

```bash
# list what HCAD currently publishes (confirm the year in the file listing)
#   open in a browser or curl where reachable:
curl -sI https://download.hcad.org/data/CAMA/2026/Real_acct_owner.zip   # expect 200
curl -sI https://download.hcad.org/data/CAMA/2025/Real_acct_owner.zip   # fallback check
# also eyeball the official downloads page for the published year:
#   https://hcad.org/pdata/pdata-property-downloads.html
```

**Acceptance:** exactly one `CAMA/<year>/Real_acct_owner.zip` returns 200 and
matches the year shown on the PDATA downloads page. Record that year; use it for
every command below. *(Evidence during this sprint: HCAD publishes a
`/resources/2026/` data directory and today is 2026-07, so the live year is
most likely 2026 — but this is inference, marked UNVERIFIED until the curl above
returns 200.)*

---

## 3. Controlled trial — do NOT run a mismatched population

A full HCAD load with an arbitrary `--gis-limit 5000` validates a 5,000-parcel
GIS slice against ~1.5M owners under production count bands — the bands are
meaningless for that population and the run is not a valid certification signal.
Use ONE of these instead. **Production thresholds are never weakened** to pass a
sample; trial bands are a separate profile.

### Method 1 (preferred) — matched account subset
Pull only the GIS parcels for a defined HCAD account subset, and load only those
owners, so both sides describe the same population:

```bash
# pick a small, real account subset (e.g. 2,000 accounts) and pull matching parcels
python main.py pull \
  "https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0" \
  --table parcels --where "HCAD_NUM IN ('<acct1>','<acct2>', ...)" --geometry
# load owners filtered to the same accounts (or load full owners; join direction
# parcels->owners is still valid because the parcels are a strict subset)
python main.py --db data/harris.db load-owners --year <YEAR>
python main.py --db data/harris.db validate     # rate checks are population-independent
```

### Method 2 (implemented) — explicit trial profile
`scripts/nightly.py --profile trial` validates against **operator-supplied
bounded denominators**, isolated from production baselines (a trial run never
becomes, and never regresses against, a production baseline — enforced by
profile-scoped baseline loading):

```bash
python scripts/nightly.py --county harris --profile trial \
  --work-dir data/nightly-trial --db data/harris-trial.db \
  --hcad-year <YEAR> --gis-limit 5000 --geometry \
  --trial-parcels 5000 --trial-owners 1500000
# trial bands: parcels ~5000 +/-20%, owners ~1.5M +/-20%; join-rate/duplicate/
# completeness stay at production strength.
```

**Acceptance (either method):** exit 0 (PASS) or exit 1 (PARTIAL with only
soft warnings); join_rate ≥ 0.90 on the bounded population.

---

## 4. ArcGIS last-good preservation — exact proof

- **Staging destination.** A GIS pull loads into `parcels__staging`, never the
  live `parcels` table (`db.load_features_staged` → `create_feature_table` on the
  staging name).
- **Promotion transaction.** `db.promote_staging` runs, in order and committed as
  one unit: `DROP TABLE IF EXISTS parcels` → `ALTER TABLE parcels__staging RENAME
  TO parcels` → migrate the `_scrape_meta` provenance row → `commit`. This is the
  **only** point a pull becomes current.
- **Behavior after mid-pagination failure.** A raising feature stream propagates
  out of `load_features_staged` **before** reconciliation and promotion, so the
  live `parcels` table is untouched and the partial data is quarantined in
  `parcels__staging`. A reconciliation shortfall (staged count < advertised count
  − tolerance) raises `FeatureReconcileError` and also blocks promotion.
- **Restart/resume behavior.** `--resume` re-opens `parcels__staging`, counts the
  rows already staged, and continues the pull from that offset into the same
  staging table; promotion happens only once the full pull reconciles.
- **Tests proving partial GIS output cannot become current**
  (`tests/test_db_staged.py`):
  - `test_midpull_failure_leaves_previous_table_current` — a good 2-row spine
    stays current after a second pull explodes mid-pagination.
  - `test_reconcile_shortfall_blocks_promotion` — a short pull never promotes.
  - `test_resume_after_partial_completes` — resume continues staging and only
    then promotes, with unique keys (no dupes).
  - `test_staged_load_promotes` / `test_reconcile_within_tolerance_promotes` —
    the success path.

The same guarantee exists on the HCAD side (`hcad_bulk.load_owners` staging +
atomic swap), so neither spine table can be replaced by partial data.

---

## 5. Full network-enabled certification procedure

Run on a host with outbound to `hcad.org` and `gis.hctx.net`. `<YEAR>` = the
value confirmed in §2. Work dir `data/nightly`; db `data/harris.db`.

### Step A — verify source metadata + fields (→ LIVE VERIFIED on success)
```bash
python main.py discover \
  "https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0"
```
- **Expect:** layer name, a `maxRecordCount`, `supportsPagination: True`, and a
  field list including the parcel key (`HCAD_NUM`/`LOWPARCELID`) and a site
  address field.
- **Acceptance:** the endpoint resolves and the parcel-key + address fields are
  present. Record the field list as evidence. If the primary endpoint 404s,
  confirm the MapServer fallback resolves.
- Confirm HCAD columns against the codebook: after Step C, check the `owners`
  table has `acct`, `mailto`, `mail_addr_1`.

### Step B — controlled trial (bounded, §3)
Run Method 1 or Method 2. **Acceptance:** PASS/PARTIAL, join_rate ≥ 0.90.

### Step C — complete baseline run (full population)
```bash
python scripts/nightly.py --county harris \
  --work-dir data/nightly --db data/harris.db \
  --hcad-year <YEAR> --geometry
echo "exit=$?"
```
- **Expect (exit 0):** `run_finished ... status=PASS published=True`; `parcels`
  and `owners` tables populated; `parcels__staging`/`owners__staging` gone.
- **Acceptance thresholds (production bands):**
  - parcels row count in 1,200,000–1,800,000 (adjust once the true count is
    known — see manual step), owners similar;
  - `identity_join` join_rate ≥ 0.80 (target ≥ 0.95);
  - parcel-key duplicate rate ≤ 0.01, account duplicate rate ≤ 0.001;
  - owner-name completeness ≥ 0.90; GIS validity (centroids in bbox) ≥ 0.98 with
    `--geometry`.
```bash
python scripts/nightly.py --work-dir data/nightly --status
python main.py --db data/harris.db validate            # exit 0 = PASS
python main.py --db data/harris.db stats
```

### Step D — source-count reconciliation
```bash
# GIS: loaded parcels vs the layer's advertised count
python -c "from scraper import arcgis; print('layer count:', arcgis.count_features(arcgis.HARRIS_PARCELS_LAYER))"
python main.py --db data/harris.db stats   # compare 'parcels' row count
# HCAD: loaded owners vs the LoadManifest read/inserted/duplicates/quarantined
python -c "import sqlite3,json; c=sqlite3.connect('data/harris.db'); \
print(json.loads(c.execute(\"select manifest from _load_manifest where table_name='owners'\").fetchone()[0]))"
```
- **Acceptance:** loaded parcels within 2% of the advertised layer count; HCAD
  `rows_read == rows_inserted + duplicates + quarantined` (reconciled=True) and
  quarantined rate < 0.5%.

### Step E — manual parcel-match inspection
Pick 10 random accounts and verify the joined owner/address against the HCAD
public record (`arcweb.hcad.org/parcel-viewer`):
```bash
python main.py --db data/harris.db match --name "<a real owner name>"
sqlite3 data/harris.db \
 "SELECT p.hcad_num, o.mailto, p.site_addr_1, o.mail_addr_1 \
  FROM parcels p JOIN owners o ON o.acct=p.hcad_num LIMIT 10;"
```
- **Acceptance:** ≥ 9/10 sampled parcels show owner name + address matching the
  HCAD public record. Record the sample as evidence.

### Step F — forced-failure recovery test
Prove a broken run FAILs, does not publish, and preserves the last good spine:
```bash
# point at a bogus HCAD zip -> critical stage FAIL
python scripts/nightly.py --county harris --work-dir data/nightly \
  --db data/harris.db --hcad-zip /nonexistent.zip --gis-limit 100 --geometry
echo "exit=$?"     # expect 2 (FAIL)
python scripts/nightly.py --work-dir data/nightly --status
```
- **Acceptance:** exit 2; `latest_attempt.json` status FAIL, `published:false`;
  `latest_success.json` still the Step C run (unchanged run_id); a line appended
  to `notifications.log`; `parcels`/`owners` tables from Step C intact.

### Step G — repeated scheduled runs
Schedule the Step C command nightly (cron/systemd-timer). **Acceptance:** ≥ 3
consecutive PASS runs; each run’s spine metrics within 10% of the prior (no
material-regression FAIL); `--status` shows a clean streak.

---

## 6. Rollback procedure

The design makes rollback mostly automatic, but explicit recovery:

- **A failed nightly run** publishes nothing — `latest_success.json` and the live
  `parcels`/`owners` tables are already the previous good state. No action needed;
  investigate via `notifications.log` + `runs/<run_id>.json`.
- **A bad promotion slipped through** (e.g. thresholds set too loose): re-run the
  last good load. Because loads stage-and-swap, simply re-running Step C with a
  known-good `--hcad-zip`/year restores a good table atomically.
- **Restore the database file:** keep a copy of `data/harris.db` before each
  certified run (`cp data/harris.db data/harris.db.bak-<date>`); to roll back,
  stop runs and `mv data/harris.db.bak-<date> data/harris.db`.
- **Abort an in-flight pull:** interrupt is safe — staging holds the partial, the
  live table is untouched; resume with `--resume` or discard staging.

---

## 7. Evidence required to mark P0 PRODUCTION CERTIFIED

Attach to the certification record:

1. Confirmed HCAD year + the 200-response for its `Real_acct_owner.zip` (§2).
2. `discover` output showing the live ArcGIS field list (Step A).
3. Trial run manifest (Step B) with join_rate.
4. Baseline `latest_success.json` + `validate` PASS output (Step C).
5. Reconciliation numbers: parcels vs layer count; HCAD LoadManifest (Step D).
6. The 10-row manual parcel-match sample with pass/fail (Step E).
7. Forced-failure `latest_attempt.json` (FAIL/unpublished) + preserved
   `latest_success.json` + `notifications.log` line (Step F).
8. Three consecutive PASS `runs/<run_id>.json` manifests (Step G).

When all eight are recorded, P0 moves from LIVE VERIFIED to PRODUCTION CERTIFIED.
Only then does Phase 2 (Clerk feed / Tax TPIA) begin.

---

## 8. Certification attempt log (verified facts only)

- **2026-07-20 — BLOCKED, not attempted.** Live certification could not begin.
  The build/execution environment's network policy denies outbound CONNECT to
  all three required hosts (proxy gateway 403):
  - `download.hcad.org:443` — connect_rejected (policy denial)
  - `hcad.org:443` — connect_rejected (policy denial)
  - `www.gis.hctx.net:443` — connect_rejected (policy denial)
  A real `scripts/nightly.py` invocation (`--hcad-year 2026 --gis-limit 50
  --geometry`) executed and FAILED loud at the `hcad_load` stage with a
  transport error, `published=false`, no `latest_success` written, and a
  `notifications.log` alert emitted — confirming fail-loud/no-silent-publish
  behavior, but producing **no** live source evidence. Phases A–D remain
  unexecuted. Current state: **NOT READY** (not yet LIVE VERIFIED).
  Certification requires re-running this runbook from a host whose network
  policy permits `hcad.org`, `download.hcad.org`, and `gis.hctx.net`.
