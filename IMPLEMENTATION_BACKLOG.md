# Harris County — Implementation Backlog

A prioritized execution queue for shipping the Harris County nightly production
pipeline. **Not a design document** — the design is frozen in `docs/` and
`county_source_registry/`. This is the concrete work queue; each sprint closes
items here and moves Harris closer to a reliable nightly pipeline.

**Gate for every item** (from the execution rule): does it *reduce operational
risk*, *improve nightly reliability*, *improve data quality*, or *reduce manual
intervention*? If none, defer to the future roadmap.

**Reference:** `docs/harris_production_readiness.md` (roadmap + critical path),
`docs/adapter_specifications.md`, `docs/acquisition_layer.md`.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done

---

## Standing constraint — evidence source of truth (2026-08-08)

The verified evidence library is built by **manual validation on the operator's
Windows machine**. That validation is the **authoritative source of truth** for
verified cases. This constraint governs every item below and every future item:

- **Do NOT generate synthetic/synthetic-proof evidence** as a substitute for
  verified cases. Synthetic fixtures remain allowed *only* as finding-tests for
  code behavior, never as entries in the verified library.
- **Do NOT add new platform features or new scoring/selection layers.** Feature
  growth is paused.
- **Assume new verified cases arrive daily** via the validation/import workflow.
- **Keep future work focused on *leveraging* the growing evidence set** —
  ingesting it cleanly, and making existing systems read from it — **not on
  replacing or expanding around it.**

The immediate unblocked work item is the daily-reviewed-CSV → verified-library
import path (below); it waits on the first real batches from the Windows
validation workflow. Live source pulls (`hcad.org`, `download.hcad.org`,
`gis.hctx.net`) remain network-blocked in this environment, so real data enters
only through that operator workflow.

### Import boundary — Excel scientific-notation account recovery `[~]`

`scraper/validation_import.py` (+ `tests/test_validation_import.py`) defends the
operator import seam against Excel mangling 13-digit HCAD accounts into
scientific notation (`1002580000022` → `1.00258E+12`). Deterministic, refuse-to-
guess recovery: a sci value is never treated as canonical; it is recovered only
when **exactly one** clean candidate account is consistent with it; leading zeros
preserved; already-canonical values passed through unchanged; the reviewed CSV is
read **read-only** (reviewer decisions/notes never mutated). Ambiguous rows (0 or
≥2 consistent candidates) are held for manual account confirmation. This lives
strictly at the boundary — no resolver/legal-matcher/scorer/Aurora change.

**Open for production wiring (needs operator confirmation):**
- the real `validation_sample.csv` column names (account + legal-key columns);
- the candidate source — the recovery needs an *uncorrupted* candidate set, so
  the module takes an injected `legal_lookup(subdivision, lot, block)`; a real
  legal index (or the resolver's live candidate output) must be wired in, since
  no legal index exists in-repo yet and the review rows currently carry only a
  name-match `candidate_acct` (which is itself Excel-corrupted in the CSV).

---

## P0 — Production Spine (Phase 1) — datasets already under engineering control

Goal: a stable, gated, monitored nightly run on Property + GIS (the identity
spine). No external dependencies. This is the milestone that proves the pipeline.

> **Certification language.** A successful live run marks source access + schema
> **LIVE VERIFIED** only — it does NOT make the system **PRODUCTION CERTIFIED**.
> Production certification requires the full 8-item procedure in
> `docs/harris_p0_certification.md` (verified metadata/fields, controlled sample
> inspection, complete baseline run, source-count reconciliation, manually
> checked parcel matches, ≥3 repeated scheduled PASS runs, a forced-failure
> recovery test, and no silent publication of partial data). All live items
> remain 🔴 BLOCKED in the build environment.

- [x] **Add validation gates** (`scraper/validate.py`) — **DONE**
  - Row-count band, required-field non-null rate, duplicate-key rate, freshness
    from `_scrape_meta.updated_at`, and parcels↔owner join rate; returns a
    structured PASS/PARTIAL/FAIL report. Default `HARRIS_SPINE_SPECS` for the
    parcels+owners spine. Wired as `python main.py validate` (exit 2=FAIL,
    1=PARTIAL, 0=PASS so nightly automation/CI can gate on it). 22 unit tests.
- [x] **Harden HCAD bulk importer** (`scraper/hcad_bulk.py`) — **IMPLEMENTATION DONE**
  - Implemented: input/file checks; fail-loud required-column (`acct`) assertion
    (`HCADSchemaError`) + expected-column (`mailto`/`mail_addr_1`) warning;
    chunked/streaming load into a STAGING table with atomic swap; checkpointed
    safe resume (`--resume`); duplicate `acct` handling (UNIQUE + INSERT OR
    IGNORE, keep-first); malformed-row quarantine (`{table}__quarantine`, with
    reasons); row-count reconciliation; `LoadManifest` metrics persisted to
    `_load_manifest`; last-good-output preservation (old table untouched until
    swap); atomic verified download (`.part` + `is_zipfile`). 18 fixture tests.
  - **Implementation status:** ✅ complete & tested (offline, real-zip fixtures).
  - **Live-source verification status:** 🔴 **BLOCKED** — outbound to
    `download.hcad.org` / `hcad.org` denied by network policy. Cannot confirm the
    live URL currency, the true column list vs the PDATA codebook, or real record
    counts. Grounded columns only (`acct`, `mailto`, `mail_addr_1`) are assumed;
    the exact schema stays UNVERIFIED. **NOT production-certified** until a
    network-enabled run validates a live sample.
- [x] **Harden ArcGIS pipeline** (`scraper/arcgis.py`) — **IMPLEMENTATION DONE**
  - Implemented: retry hardening (permanent 4xx / bad-query → `ArcGISPermanentError`
    fail-fast, transient 5xx/429/network retried, no sleep after final attempt);
    `resolve_layer()` endpoint fallback (primary FeatureServer → MapServer mirror)
    wired into `iter_features` so a moved endpoint degrades instead of aborting;
    geospatial sanity (`HARRIS_BBOX`, `in_bbox`, `validate_point` rejecting
    no_geometry / non_numeric / null_island / out_of_bbox); malformed-geometry-
    resilient `point_of` (never raises); `reconcile_count()` pull vs advertised
    count. **Deterministic pagination:** `paging_metadata()`/`stable_order_field()`
    page with `orderByFields=<objectIdField> ASC` and fail loud if pagination is
    advertised but no stable ordering field / orderBy support exists — so
    resume-by-offset cannot gap or duplicate; the object-id field + order is
    preserved in the run manifest `source_metadata`. 36 offline tests.
  - **Implementation status:** ✅ complete & tested (offline, faked session).
  - **Live-source verification status:** 🔴 **BLOCKED** — outbound to
    `gis.hctx.net` denied by network policy. Cannot confirm the live layer
    endpoint currency, field list, `supportsPagination`, or real feature count.
    **Shapefile-download fallback deferred** — it needs a new shapefile
    dependency and the live file to build against; not added under the
    architecture freeze without live access. **NOT production-certified** until a
    network-enabled run validates the live layer.
- [x] **GIS staged load / last-good preservation** (`scraper/db.py`) — **DONE**
  - `load_features_staged` + `promote_staging`: GIS pulls land in
    `parcels__staging` and are atomically swapped into the live `parcels` table
    only after count reconciliation, so a mid-pagination failure or shortfall
    (`FeatureReconcileError`) can never make partial GIS output current;
    `--resume` continues staging. Nightly `gis_pull` uses it. 6 proof tests
    (`tests/test_db_staged.py`). Closes the gap where the old direct-write pull
    could publish a partial parcels table.
- [x] **Controlled-trial mode** (`validate.trial_specs` + `nightly --profile trial`) — **DONE**
  - Bounded-population validation bands from operator-supplied denominators;
    rate checks stay at production strength; production `HARRIS_SPINE_SPECS`
    never mutated; trial/production baselines profile-scoped so they never cross.
    Prevents mismatched-population sample runs. 6 tests.
- [x] **Nightly runner** (`scripts/nightly.py`) — **IMPLEMENTATION DONE**
  - Orchestrates classified stages preflight → hcad_load → gis_pull → validation
    → publish (identity-join inserted by the spine item). Unique run_id + git
    code_version; per-stage durations/rows/warnings/errors; critical vs
    non-critical stages; PASS/PARTIAL/FAIL exit 0/1/2; atomic stale-aware file
    lock (exit 3 if already running); run-completion vs publication tracked
    separately; latest_attempt vs latest_success preserved (failed run never
    clobbers last good). Does not duplicate importer last-good logic. 13 tests.
  - **Implementation status:** ✅ complete & tested (injected fake stages).
  - **Live-source verification status:** 🔴 BLOCKED — default HCAD/GIS stages
    need live access; only orchestration is verified offline.
- [x] **Monitoring & logging** (`scraper/monitoring.py`) — **DONE**
  - Structured JSON-lines event log (`make_logger`), durable per-run manifest
    archive with retention (`prune_runs`), alert emission on any non-PASS run to
    `notifications.log` (`emit_alert`), and a health/status reader
    (`run_history`, `summarize_health`, `format_health`, consecutive-failure
    streak) surfaced via `nightly.py --status`. No external platform/framework.
    Wired into the runner. 10 tests.
- [x] **Identity-resolution spine check** (`scraper/spine.py`) — **DONE**
  - Measures unique parcels/accounts, duplicate-key counts/rates, matched/
    unmatched parcels, parcel→owner join rate, owner-name/address/parcel-address
    completeness, and GIS centroid validity (when geometry present). Compares
    critical metrics against the prior successful run and **fails loud on a
    material regression** (>10% drop); low join rate / elevated duplicates /
    missing baseline → PARTIAL. Wired as the critical `identity_join` nightly
    stage; metrics recorded in the manifest so each run compares to the last
    success. 13 spine tests + 3 runner integration tests.

## P1 — Clerk Feed enablement (Phase 4 prep + build)

Gated on the external County Clerk data-sales/FTP contract (start procurement in
parallel; do not block P0).

- [ ] **FTP Adapter** (`scraper/adapters/ftp.py`) — build against the delivered
  index schema (pipe-delimited).
- [ ] **Foreclosure integration** — ingest + HCAD parcel match + validation.
- [ ] **Probate integration** — ingest + decedent→owner match with a confidence
  gate before lead promotion.

## P2 — Open Data expansion (Phase 3)

No external dependency; build once the spine is stable.

- [ ] **Open Data Adapter** (`scraper/adapters/open_data.py`) — Socrata/CKAN
  download + API mode.
- [ ] **Houston Code Enforcement** integration (DON dataset) — distress signal.
- [ ] **Houston 311** integration — enrichment only (never a primary trigger).

## P3 — Tax integration (Phase 5)

Gated on the recurring TPIA process (periodic, not nightly).

- [ ] **TPIA Adapter** (`scraper/adapters/tpia.py`) — request-lifecycle tracking
  + ingest of the fulfilled extract.
- [ ] **Tax sale-list importer** — fast monthly win (first-Tuesday cadence).
- [ ] **Tax Delinquent full-roll** integration from TPIA responses.

## Deferred / future roadmap

- Portal Adapter (last-resort fallbacks) — only if a feed/TPIA path stalls.
- Additional counties — apply the County Certification Checklist per county.
- Batch ranking / opportunity scoring model — after the spine + signals land.

---

## Execution notes

- **Critical path:** start the Clerk feed contract + first Tax TPIA on day 1 (P1/
  P3 external legs) so they run in parallel with P0; the code for P1/P3 waits on
  their schemas, but the procurement clock should not wait on the code.
- **Ship early:** P0 delivers a running gated nightly pipeline (Nightly v1);
  P1–P3 grow coverage onto a system that is already running.
- Documentation is frozen; update `docs/` only if implementation changes the
  design.
