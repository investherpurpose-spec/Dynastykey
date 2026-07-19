# Harris County — Production Readiness Assessment

**Question this answers:** *What is the shortest path to a reliable nightly
Harris County pipeline?*

**Status:** engineering planning only. No architecture, adapters, collectors, or
production code are created or modified in this sprint. This converts the prior
research (`county_source_registry/harris/`, the Decision Matrix, the Acquisition
Layer, Adapter Specifications, and the Certification Checklist) into a concrete,
sequenced execution roadmap.

**Effort convention.** "Eng effort" = focused engineering days for one engineer,
excluding external lead times (contracts, records requests). External lead times
are called out separately because on this project **they, not the code, are the
critical path.**

---

## 1. Per-dataset readiness

### Property Ownership
- **Current acquisition method:** HCAD bulk download — **already implemented**
  (`scraper/hcad_bulk.py`, `real_acct` tab-delimited/`cp1252`).
- **Target acquisition method:** same (HCAD bulk). No change.
- **Required adapter:** Bulk CSV Adapter (existing loader is the reference impl).
- **Engineering effort:** ~2–3 days (pin live URL, map columns to PDATA codebook,
  make schema validation exact, wire count/duplicate checks).
- **External dependency:** none.
- **Remaining unknowns:** live `download.hcad.org` URL currency; exact codebook
  columns (Property Type / Land Use / Status); true record count; refresh cadence.
- **Remaining risks:** silent schema drift if HCAD changes the layout; URL 404 on
  a new year.
- **Blocking issues:** none — this is the anchor and is closest to ready.
- **Recommended next action:** pull a live sample, confirm URL + codebook, lock
  the field map and count band.
- **Estimated completion effort:** **~3 days eng, 0 external.**

### GIS / Parcel
- **Current acquisition method:** ArcGIS REST bulk pull — **already implemented**
  (`scraper/arcgis.py`, paging/retry/resume).
- **Target acquisition method:** same (ArcGIS REST), + shapefile fallback.
- **Required adapter:** ArcGIS REST Adapter (existing client is the reference impl).
- **Engineering effort:** ~2–4 days (verify live layer metadata, confirm the
  current endpoint, add shapefile-download fallback, geospatial sanity checks).
- **External dependency:** none.
- **Remaining unknowns:** live parcels field list / record count /
  `supportsPagination`; whether the endpoint has moved; shapefile URL + cadence;
  Open Data Hub decommissioning status.
- **Remaining risks:** ArcGIS endpoints move over time (README already warns);
  requires the `discover` fallback to stay healthy.
- **Blocking issues:** none — but live verification was blocked in certification
  and must be done from a network-enabled run.
- **Recommended next action:** run `discover` against the live layer, confirm
  fields/count, add shapefile fallback.
- **Estimated completion effort:** **~3–4 days eng, 0 external.**

### Foreclosure
- **Current acquisition method:** none in production (portal scraping is only a
  documented fallback; no collector exists).
- **Target acquisition method:** Harris County Clerk **licensed FTP feed**
  (pipe-delimited daily index).
- **Required adapter:** **FTP Adapter (new)** — highest-value new component.
- **Engineering effort:** ~1–1.5 weeks eng *after* the feed + schema are in hand
  (fetch/parse pipe-delimited index, map fields, validate statutory invariants).
- **External dependency:** **YES — County Clerk data-sales/FTP agreement**
  (`datasales@cco.hctx.net` / 713-274-6390). Contract + credentials lead time is
  external and **UNVERIFIED**; likely the longest single item on the roadmap.
- **Remaining unknowns:** feed price/terms; exact index schema; whether the feed
  key eases HCAD parcel matching.
- **Remaining risks:** contract delay; schema differs from assumption; parcel
  linkage requires geocoding/legal-description matching.
- **Blocking issues:** **contract not yet initiated** — this blocks both
  Foreclosure and Probate.
- **Recommended next action:** **start the data-sales procurement conversation on
  day 1** (parallel to all other work) to obtain price, terms, and the index
  schema.
- **Estimated completion effort:** **~1–1.5 weeks eng + external contract lead
  time (critical path).**

### Probate
- **Current acquisition method:** none in production.
- **Target acquisition method:** **same County Clerk FTP feed** (one contract
  covers foreclosure + probate).
- **Required adapter:** FTP Adapter (shared with Foreclosure) + the existing
  identity-resolution layer.
- **Engineering effort:** ~3–5 days eng on top of the FTP Adapter (probate index
  parse is similar; the real work is decedent→owner resolution + a confidence
  gate).
- **External dependency:** shares the Foreclosure feed contract.
- **Remaining unknowns:** probate index schema; achievable decedent→HCAD match
  precision/recall.
- **Remaining risks:** **weak property linkage** — false-positive owner matches if
  auto-promoted; must gate lead promotion on match confidence + human review.
- **Blocking issues:** same feed contract; plus identity-resolution validation.
- **Recommended next action:** after the feed lands, validate the decedent→owner
  matcher against a labeled sample before promoting any probate leads.
- **Estimated completion effort:** **~1 week eng (post-feed) + shared contract.**

### Tax Delinquent
- **Current acquisition method:** none in production (no bulk source exists).
- **Target acquisition method:** monthly **sale-list import** + recurring **TPIA**
  for the full roll.
- **Required adapter:** Open Data / Bulk CSV Adapter for the sale list; **TPIA
  Adapter (new, process-heavy)** for the full roll.
- **Engineering effort:** ~3–5 days eng for the sale-list importer; the full-roll
  TPIA is mostly **process**, not code.
- **External dependency:** **YES — recurring Texas Public Information Act request**
  cadence with the Tax Office / County Attorney; turnaround + cost **UNVERIFIED**.
- **Remaining unknowns:** whether a bulk full-roll extract is obtainable, its
  format/cost/cadence; sale-list machine-readable format.
- **Remaining risks:** no live nightly source — "nightly tax" would over-promise;
  format instability of the sale list.
- **Blocking issues:** TPIA channel not yet established (for full roll).
- **Recommended next action:** import the monthly sale list first (fast win);
  file the first TPIA to learn the true full-roll format/cost.
- **Estimated completion effort:** **~1 week eng + recurring external TPIA
  (periodic, not nightly).**

### Houston 311
- **Current acquisition method:** none in production.
- **Target acquisition method:** City open-data import (CSV export; API as backup).
- **Required adapter:** Open Data Adapter (new).
- **Engineering effort:** ~2–4 days (straightforward open-data ingest + address
  match).
- **External dependency:** none (public open data).
- **Remaining unknowns:** exact refresh cadence (nightly?); Socrata API endpoint;
  category exclusions (e.g. BARC).
- **Remaining risks:** source self-disclosed as "may not reflect actual data" →
  **enrichment only, never a primary trigger.**
- **Blocking issues:** none.
- **Recommended next action:** treat as low priority; import as an enrichment
  overlay once the Open Data Adapter exists.
- **Estimated completion effort:** **~3 days eng, 0 external (enrichment role).**

### Code Enforcement
- **Current acquisition method:** none in production.
- **Target acquisition method:** City of Houston open-data import (DON dataset);
  TPIA for unincorporated county.
- **Required adapter:** Open Data Adapter (shared with 311); TPIA Adapter for the
  county gap.
- **Engineering effort:** ~3–5 days for the Houston import (reuses the 311 Open
  Data Adapter work).
- **External dependency:** TPIA for unincorporated-county coverage only.
- **Remaining unknowns:** DON dataset schema/cadence/API; whether unincorporated
  county publishes anything; IPS→Public Works reorg impact.
- **Remaining risks:** **coverage is Houston-incorporated only** — must not be
  presented as countywide.
- **Blocking issues:** none for the Houston portion.
- **Recommended next action:** import Houston DON after the Open Data Adapter is
  built; document the unincorporated gap explicitly.
- **Estimated completion effort:** **~4 days eng, TPIA external for the gap.**

---

## 2. Dependency roadmap (critical path)

The critical path is **not** the sum of tasks — most datasets are independent.
The binding constraint is the **County Clerk feed contract** (external lead time),
which gates the two highest-value distress datasets. The fastest reliable nightly
pipeline therefore **ships the spine + no-blocker signals first**, while the feed
and TPIA channels are procured **in parallel from day 1**.

```
DAY 1 (kick off in parallel, no code blocks these):
  ┌─ START County Clerk feed procurement ───────────────┐   (external, longest lead)
  └─ START first Tax TPIA request ──────────────────────┘   (external, periodic)

CRITICAL PATH TO FIRST NIGHTLY (all internal, no external blockers):

  Property (bulk) hardening ──┐
                              ├─► IDENTITY SPINE LOADED ─► IDENTITY RESOLUTION ─┐
  GIS (ArcGIS) verification ──┘   (parcels + owners)      (names/claude_match)  │
                                                                                │
                                                          Validation + gating ──┤
                                                                                ▼
                                                          NIGHTLY PIPELINE v1
                                                          (spine certified,
                                                           gating/alerts live)

FAST-FOLLOW SIGNALS ONTO v1 (no external blocker):
  Open Data Adapter ─► Houston Code Enforcement ─► (scoring input)
                    └► Houston 311 (enrichment) ──► (scoring input)

FEED-GATED PATH (joins when the external contract lands):
  Feed contract + schema ─► FTP Adapter ─► Foreclosure feed ─► Probate feed
                                                │                   │
                                                └──► Identity Resolution ──► Scoring ──► NIGHTLY v2

TPIA-GATED PATH (periodic, joins when first response lands):
  Tax TPIA process ─► Tax sale-list import (fast) ─► full-roll import ─► Normalization ─► Scoring
```

**Critical-path reading:**
- **Internal critical path to a first reliable nightly run:**
  `Property + GIS → identity spine → identity resolution → validation/gating →
  Nightly v1`. Everything here is code we can start today with no external blocker.
- **External critical path to full distress coverage:** the **feed contract** is
  the longest pole. Because its lead time is outside engineering control, it must
  be **started on day 1**, before any adapter code, so it is not the thing everyone
  waits on at the end.
- **The FTP Adapter is built against the real schema**, so it cannot be finished
  until the contract delivers the schema — another reason to start procurement first.

---

## 3. Production Readiness Scorecard

🟢 Complete · 🟡 Partial · 🔴 Not Ready

| Dataset | Research Complete | Acquisition Ready | Adapter Ready | Validation Ready | Automation Ready | **Production Ready** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Property** | 🟢 | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 |
| **GIS / Parcel** | 🟢 | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 |
| **Foreclosure** | 🟢 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 |
| **Probate** | 🟢 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 |
| **Tax Delinquent** | 🟢 | 🔴 | 🔴 | 🔴 | 🔴 | 🔴 |
| **Houston 311** | 🟢 | 🟡 | 🔴 | 🔴 | 🔴 | 🔴 |
| **Code Enforcement** | 🟢 | 🟡 | 🔴 | 🔴 | 🔴 | 🔴 |

**Scorecard reading:**
- **Research is 🟢 across the board** — the prior sprints did their job; nothing is
  blocked on "we don't know where the data is."
- **Property and GIS are the only 🟡 (partial) rows** — they have working
  collectors and just need verification + hardening. They are the shortest path to
  *any* nightly run and the spine everything else needs.
- **Foreclosure / Probate / Tax are 🔴 on acquisition** because of **external
  dependencies** (feed contract, TPIA), not missing design.
- **311 / Code are 🟡 acquisition / 🔴 adapter** — no external blocker, just need
  the Open Data Adapter built.
- **Nothing is 🟢 Production Ready yet** — honest state: we are pre-first-run.

---

## 4. Final question — the exact sequence I would execute as technical lead

*From today until nightly production launch. This is the shortest, lowest-rework,
lowest-risk path — it front-loads the one external blocker and ships the spine
before chasing distress signals.*

**Day 1 — unblock the long poles (before writing any code):**
1. **Open the County Clerk data-sales procurement** for the licensed feed — get
   price, terms, credentials, and the **exact index schema**. This is the longest
   external lead time; starting it first means the FTP Adapter has a schema to
   build against when we reach it, and the contract isn't the thing we wait on at
   the end.
2. **File the first Tax TPIA request** to learn the real full-roll format/cost/
   turnaround. Periodic by nature — start the clock now.

**Phase 1 — certify the spine (internal, ~1 week, no external blocker):**
3. **Property:** pull a live HCAD sample, confirm the URL + PDATA codebook, lock
   the field map and count/duplicate/freshness validation. Close its UNVERIFIED
   items. (Collector exists — this is verification, not new build.)
4. **GIS:** run `discover` against the live parcels layer, confirm endpoint +
   fields + count, add the shapefile fallback and geospatial sanity checks.
5. **Load the identity spine** (parcels + owners) and **validate identity
   resolution** (`names.py`/`claude_match.py`) — measure the parcels↔owner
   join rate; this is the join every other dataset depends on.

**Phase 2 — first reliable nightly run on the spine (~3–5 days):**
6. Stand up **nightly orchestration + validation gating** (count/schema/freshness
   thresholds, PASS/PARTIAL/FAIL, alerts, "preserve last good output on failure").
   Ship **Nightly v1** = Property + GIS certified, running nightly, gated, alerting.
   *This is the milestone that proves the pipeline is real.*

**Phase 3 — add no-blocker signals onto v1 (~1 week):**
7. Build the **Open Data Adapter** once; use it for **Houston Code Enforcement**
   (distress signal) and **Houston 311** (enrichment). Wire both into scoring via
   address→HCAD match. No external dependency, so these land while the feed
   contract is still in flight.

**Phase 4 — feed-gated distress datasets (when the contract lands):**
8. Build the **FTP Adapter** against the delivered schema. Ingest **Foreclosure**
   first (cleaner linkage), then **Probate**.
9. For Probate, **validate decedent→owner matching against a labeled sample and
   set a confidence gate** before promoting leads. Ship **Nightly v2** = spine +
   foreclosure + probate + code/311.

**Phase 5 — tax (periodic):**
10. Import the **monthly tax sale list** (fast win); ingest the **first TPIA
    full-roll response** when it arrives; normalize and feed scoring. Record tax
    explicitly as **periodic, not nightly.**

**Launch definition.** "Harris in nightly production" = **Nightly v1** is the true
launch of a *reliable nightly pipeline* (spine + gating + alerting); v2 and tax are
coverage expansions on a system that is already running every night. We ship the
running system early and grow its coverage — we do **not** wait for all seven
datasets before the first nightly run.

**Why this order minimizes risk and rework:**
- The **only external blocker is started first**, so procurement runs in parallel
  with a full phase of internal work instead of stalling the finish.
- The **spine ships before signals**, so identity resolution — the thing every
  distress dataset reuses — is proven once, early, not re-debugged per dataset.
- **One Open Data Adapter** serves two datasets; **one FTP contract/adapter**
  serves two datasets — no duplicated build.
- Each phase **ends in a running, gated system**, so value ships continuously and
  a slipped contract delays *coverage*, never the *existence* of a nightly pipeline.

---

## 5. Note on the proposed architecture freeze

Endorsed. The foundational artifacts (Source Registry, Decision Matrix,
Acquisition Layer, Adapter Specifications, Certification Checklist) are sufficient
to execute this roadmap. From here, the test for any new work is exactly the one
proposed: **does it move Harris County closer to a reliable nightly production
pipeline?** If yes, build it; if no, it goes to the future roadmap. This document
is the last planning artifact before execution — everything after it should be
code against this sequence, not new design.
