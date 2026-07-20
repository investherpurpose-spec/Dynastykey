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

## P0 — Production Spine (Phase 1) — datasets already under engineering control

Goal: a stable, gated, monitored nightly run on Property + GIS (the identity
spine). No external dependencies. This is the milestone that proves the pipeline.

- [x] **Add validation gates** (`scraper/validate.py`) — **DONE**
  - Row-count band, required-field non-null rate, duplicate-key rate, freshness
    from `_scrape_meta.updated_at`, and parcels↔owner join rate; returns a
    structured PASS/PARTIAL/FAIL report. Default `HARRIS_SPINE_SPECS` for the
    parcels+owners spine. Wired as `python main.py validate` (exit 2=FAIL,
    1=PARTIAL, 0=PASS so nightly automation/CI can gate on it). 22 unit tests.
- [ ] **Harden HCAD bulk importer** (`scraper/hcad_bulk.py`)
  - Confirm/parametrize live URL; assert required columns before load
    (SchemaDrift fail-loud); expose the field map; record source/published date.
  - *Acceptance:* missing required column fails the run instead of loading; a
    live sample validates.
- [ ] **Harden ArcGIS pipeline** (`scraper/arcgis.py`)
  - Verify live layer metadata; add shapefile-download fallback path; geospatial
    sanity (centroid within Harris bbox); endpoint-moved → MapServer/`discover`.
  - *Acceptance:* endpoint drift degrades gracefully to fallback; bad geometry
    rejected with a reason.
- [ ] **Nightly runner** (`scripts/nightly.py`)
  - Orchestrate pull-parcels → load-owners → identity-spine join → validation
    gates; exit codes PASS/PARTIAL/FAIL; preserve last-good output on failure.
  - *Acceptance:* one command runs the spine end to end and gates on validation.
- [ ] **Monitoring & logging**
  - Structured run log + a machine-readable run manifest per nightly run
    (counts, deltas, freshness, status); alert hook on FAIL/PARTIAL.
  - *Acceptance:* every run emits a manifest; a failing run is visibly flagged.
- [ ] **Identity-resolution spine check**
  - Measure parcels↔owner join rate after load; expose it as a validation metric.
  - *Acceptance:* join rate below threshold trips a validation warning.

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
