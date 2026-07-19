# Adapter Specifications

**Status:** design / blueprint only. No adapter is implemented in this sprint.
This document specifies six reusable acquisition adapters plus the base contract
they share. Specifications are grounded in the existing reference code
(`scraper/arcgis.py`, `scraper/hcad_bulk.py`, `scraper/db.py`,
`scraper/names.py`, `scraper/claude_match.py`) and the Harris certification
(`county_source_registry/harris/`).

Each adapter is documented against the required nine attributes: **Purpose ·
Supported data sources · Expected inputs · Expected outputs · Validation
requirements · Error handling · Retry strategy · Authentication requirements ·
Where it fits into the normalization pipeline.**

---

## 0. Base Adapter Contract (shared by all six)

All adapters share one interface and one output shape so downstream stages are
adapter-agnostic (see `acquisition_layer.md` §4).

- **Inputs:** an `AcquisitionConfig` (`county_id`, `dataset_id`, `method_id`,
  `source_locator`, `auth_ref`, `schema_expectation`, `field_map`,
  `validation_rules`, `retry_policy`, `cadence`) and an optional `cursor`
  (resumability token).
- **Outputs:** an iterator of `AcquisitionRecord` (canonical fields + raw +
  provenance + per-record validation) and a `RunManifest` (counts, freshness,
  status PASS/PARTIAL/FAIL). Mirrors today's `_scrape_meta` provenance and the
  nightly self-verification/stacking manifests, unified into one shape.
- **Universal validation:** every record carries `provenance` (county, dataset,
  method, source_locator, `acquired_at`, `source_updated_at`, `run_id`,
  `record_hash`); every run emits an `expected_count` vs `count_delta_pct` check
  and a freshness check.
- **Universal error taxonomy:** `ConfigError` (fail fast, no fetch),
  `SourceUnavailableError` (retryable transport), `SchemaDriftError`
  (fields/types changed — fail loud, do not silently load), `ValidationError`
  (per-record; routed to reject bucket with reason), `PartialRunError` (some
  pages/files failed — run is PARTIAL, prior good output preserved).
- **Universal retry:** exponential backoff `base**attempt` with a polite
  inter-request delay, matching the current `arcgis.py` (`MAX_RETRIES=5`,
  `2**attempt`, `POLITE_DELAY`). Non-retryable errors (auth, config, schema
  drift) are **not** retried.
- **Pipeline fit:** every adapter hands off at **stage [1] Normalization/Landing**
  (`scraper/db.py`-style `sanitize()` + provenance), then the shared stages [2]
  field mapping → [3] identity resolution (`names.py`/`claude_match.py`) → [4]
  scoring run identically regardless of adapter.

Per-adapter sections below specify only what differs from this base.

---

## 1. Bulk CSV Adapter

**Purpose.** Download a single authoritative bulk flat file and parse it into
canonical records. This is the highest-trust, lowest-complexity adapter and the
default choice whenever an official bulk export exists. Generalizes the existing
`scraper/hcad_bulk.py`.

**Supported data sources.**
- HCAD PDATA bulk property/owner files (`real_acct` etc.) — **Harris Property (primary)**.
- Any county appraisal-district or agency bulk export: CSV, TSV, pipe-delimited,
  fixed-width, or a `.zip` containing such files.
- **File variant:** GIS **shapefile** bulk downloads (Harris GIS backup) — same
  download+parse flow with a shapefile reader instead of a delimited reader.

**Expected inputs.**
- `source_locator`: file URL or URL template (e.g. `.../{year}/Real_acct_owner.zip`).
- `member_hint`: which file inside a zip (e.g. `real_acct`).
- `format`: delimiter + encoding (Harris uses **tab-delimited, `cp1252`** — from
  `hcad_bulk.iter_owner_rows`).
- `schema_expectation`: required columns (e.g. `acct`, owner, mailing, site addr).
- `field_map`: source columns → canonical fields.

**Expected outputs.** `AcquisitionRecord` per row; `RunManifest` with row count,
file size, and the file's published date as `source_updated_at`.

**Validation requirements.**
- Assert required columns present **before** load; `SchemaDriftError` if missing
  (never silently load a changed layout).
- Row count within configured band (Harris Property: heuristic ~1.2M–1.8M until
  `UNVERIFIED` true count is confirmed).
- Key column (`acct`) present and near-unique; flag duplicate rate over threshold.
- Encoding sanity (replacement-char rate under threshold).

**Error handling.** Download failure → `SourceUnavailableError` (retry). Corrupt
zip / missing member → `ConfigError`/`SchemaDriftError` (no retry). Partial
parse → PARTIAL run, prior good file retained.

**Retry strategy.** Retry the *download* with exponential backoff; parsing is
deterministic and not retried. Support resumable download (size check + skip if
already fully downloaded, as `download_bulk_zip` does today).

**Authentication requirements.** Typically none (public bulk). If a portal
requires a login/token, use `auth_ref` (never inline).

**Pipeline fit.** Emits at stage [1]; Property/GIS records join to the HCAD
parcel spine at stage [3]. Because bulk files change slowly, cadence is the
file's publication schedule, **not** nightly.

---

## 2. FTP Adapter

**Purpose.** Scheduled pull of **licensed government / data-sales feed files**
over FTP/SFTP — the production-grade path the Harris certification identified for
Foreclosure and Probate (the County Clerk pipe-delimited daily feed). This is the
single most important *new* adapter: it replaces portal scraping for the
highest-value distress datasets.

**Supported data sources.**
- **Harris County Clerk licensed feed** — pipe-delimited `.txt` index files,
  delivered daily, purchased monthly (`datasales@cco.hctx.net`). Serves
  **Foreclosure + Probate** from one contract.
- Any county that offers a licensed/subscription file feed (index/image drops)
  over FTP/SFTP.

**Expected inputs.**
- `source_locator`: host + directory + filename pattern (e.g. daily `YYYYMMDD` files).
- `auth_ref`: FTP/SFTP credentials reference (from secret store).
- `format`: **pipe-delimited** for the Clerk index (per certification).
- `cursor`: last-processed file date/name for incremental daily pulls.
- `schema_expectation` + `field_map` for the index layout (**`UNVERIFIED` exact
  schema — must be confirmed with the feed contract at certification**).

**Expected outputs.** `AcquisitionRecord` per index row; `RunManifest` listing
files consumed, per-file counts, and the feed date as `source_updated_at`.

**Validation requirements.**
- Expected daily file present; a missing day → freshness alert (not silent skip).
- Instrument/case type matches the dataset (e.g. trustee-sale notices for
  foreclosure; probate case types for probate).
- Statutory invariants where known (foreclosure: posting date ≥ 21 days before
  sale date — flag violations).
- Dedupe on file code + date; postponed/re-posted items legitimately repeat.

**Error handling.** Connection/auth failure → distinguish `SourceUnavailableError`
(retry) from auth-`ConfigError` (no retry, alert — a lapsed contract must page a
human). Missing expected file → PARTIAL + freshness alert. Malformed file →
`SchemaDriftError`.

**Retry strategy.** Exponential backoff on transport errors; **do not** retry
auth failures. Idempotent by `cursor` — re-running re-consumes only unprocessed
files.

**Authentication requirements.** **Required** — FTP/SFTP credentials tied to the
data-sales agreement, stored as a secret and referenced via `auth_ref`. Prefer
SFTP; credentials never logged or committed.

**Pipeline fit.** Emits at stage [1]; Foreclosure/Probate records have **no HCAD
account key**, so stage [3] identity resolution (grantor/decedent →
`names.py`/`claude_match.py` against the HCAD owner spine) is the critical,
higher-cost step. Probate leads should not auto-promote until the
decedent→owner match clears a confidence gate (per `probate.md`).

---

## 3. ArcGIS REST Adapter

**Purpose.** Paged bulk extraction from an ArcGIS `FeatureServer`/`MapServer`
layer. This is the reference adapter the existing `scraper/arcgis.py` already
implements well; the spec generalizes it to any county's ArcGIS parcel/GIS layer.

**Supported data sources.**
- **Harris GIS/parcels** layer (`.../HCAD/Parcels/FeatureServer/0`) — **GIS
  (primary)**; also a **Property backup** (account + address without owner name).
- Any county ArcGIS REST layer (parcels, address points, jurisdictions).

**Expected inputs.**
- `source_locator`: layer URL (support a `discover` probe to relocate moved
  endpoints, as `main.py discover` does).
- `where`, `out_fields`, `include_geometry`, `outSR` (default 4326), `page_size`.
- `cursor`: `resultOffset` (or objectId chunk) for resumability.
- `field_map` from layer fields → canonical.

**Expected outputs.** `AcquisitionRecord` per feature (attributes + optional
centroid/geometry via `point_of`); `RunManifest` with feature count and layer
edit date as `source_updated_at`.

**Validation requirements.**
- Read layer metadata first: honor `maxRecordCount`,
  `advancedQueryCapabilities.supportsPagination`, `objectIdField` (as
  `iter_features` does).
- Account key (`HCAD_NUM`/`LOWPARCELID`) present; geometry parses; centroid
  within county bounding box (reject null-island / out-of-county).
- Count vs `count_features()` expected total; parcels↔owner join-rate check at
  stage [3].

**Error handling.** ArcGIS returns errors **inside HTTP 200 bodies** — detect the
`error` key and raise `ArcGISError` (already implemented). Endpoint moved (404 /
empty) → try configured MapServer fallback, then `discover`, then
`SourceUnavailableError`.

**Retry strategy.** Exactly today's pattern: `MAX_RETRIES=5`, `2**attempt`
backoff, `POLITE_DELAY` between pages; two paging modes (`resultOffset` when
supported, objectId-chunk fallback for older servers).

**Authentication requirements.** Usually none (public REST). If a layer is
token-secured (ArcGIS token / API key), acquire via `auth_ref` and refresh on
expiry.

**Pipeline fit.** Emits at stage [1]; provides the **parcel geometry + account
spine** that stage [3] uses to resolve every other dataset to a mappable parcel.
Cadence follows the fabric (quarterly-ish), not nightly.

---

## 4. Open Data Adapter

**Purpose.** Acquire from a municipal/county **open-data portal** — either a bulk
export download or the portal's API (Socrata SoQL / CKAN / ArcGIS Hub). Covers
easy, public, machine-readable municipal datasets.

**Supported data sources.**
- **City of Houston open data** (`data.houstontx.gov`): **Houston 311** and
  **Code Enforcement (DON)** datasets.
- Any Socrata/CKAN/ArcGIS-Hub portal for a county's municipal data.
- Two modes: **download mode** (CSV export) and **API mode** (SoQL/paged JSON) —
  API is the documented Harris *backup* for 311.

**Expected inputs.**
- `source_locator`: dataset id / export URL / API endpoint (**Socrata endpoint
  `UNVERIFIED` for Houston — confirm at certification**).
- `query`: SoQL/filter (e.g. incremental by updated date), `page_size`.
- `cursor`: last `updated_at`/offset for incremental pulls.
- `field_map` + `schema_expectation`.

**Expected outputs.** `AcquisitionRecord` per row; `RunManifest` with dataset
`updated_at` metadata as `source_updated_at`.

**Validation requirements.**
- Require case/SR id, type, status, open date, and a geocodable address.
- Honor the source's own data-quality caveats (Houston 311 is self-disclosed as
  "may not reflect actual data" → mark 311 records as **enrichment-only**, never
  a primary trigger, per `houston_311.md`).
- Coverage caveat for Code Enforcement: **Houston-incorporated only**; the
  unincorporated-county gap is handled by the TPIA Adapter, not this one.
- Freshness from portal metadata; dedupe on record id.

**Error handling.** Portal 5xx / rate-limit (429) → `SourceUnavailableError`
(retry, respect `Retry-After`). Schema change in the export → `SchemaDriftError`.
Empty result near expected-update window → PARTIAL + freshness alert.

**Retry strategy.** Exponential backoff; honor `Retry-After`/throttle headers;
API mode paginates with `cursor` and is idempotent.

**Authentication requirements.** Usually none. An **app token** raises Socrata
rate limits — supply via `auth_ref` if configured; never inline.

**Pipeline fit.** Emits at stage [1]; 311/Code records are address-keyed →
stage [3] address→HCAD match. 311 feeds **scoring as enrichment** (stage [4]),
not as an originating signal.

---

## 5. Public Information Act (TPIA) Adapter

**Purpose.** Standardize the **human-in-the-loop recurring Texas Public
Information Act request** as a first-class acquisition method for datasets that
have **no bulk/API/feed** — so "we file a monthly records request" is a tracked,
validated pipeline stage rather than an ad-hoc manual chore.

**Supported data sources.**
- **Harris Tax Delinquent full roll** (no bulk export exists) — **primary**.
- **Unincorporated Harris County code enforcement** (no confirmed open dataset).
- Any county dataset that is public record but only released on request.

**Expected inputs.**
- `source_locator`: the responsible records officer/office + request template.
- `cadence`: request frequency (e.g. monthly) and expected turnaround.
- `intake_spec`: how the response arrives (email/CD/portal), expected format
  (CSV/PDF/spreadsheet — often **`UNVERIFIED` until first fulfillment**).
- `schema_expectation` + `field_map` applied once the extract lands.

**Expected outputs.** Same `AcquisitionRecord`/`RunManifest` shape once the
response file is ingested; plus a **request-lifecycle record** (requested_at,
acknowledged_at, fulfilled_at, cost, custodian) so the manual leg is auditable.

**Validation requirements.**
- Track request SLA; alert if a scheduled request is unfiled or a response is
  overdue (freshness = "did the recurring request cycle actually happen").
- On fulfillment, run the same schema/count/duplicate validation as the Bulk CSV
  Adapter (a TPIA response is usually a delimited/PDF extract).
- Cost tracking per request (extracts may carry a fee — `UNVERIFIED` amounts).

**Error handling.** Non-fulfillment / redaction / format change → route to a
**human queue**, not a retry loop. Cost-estimate-required → pause for approval.
A missed cycle → PARTIAL coverage flagged in the county's certification status.

**Retry strategy.** **Not** automated network retry — retries here mean
*re-filing on the next cadence* or following up with the custodian. The adapter
schedules and reminds; it does not hammer an endpoint.

**Authentication requirements.** None technical; identity/contact of the
requester is part of the request record. Handle any PII in responses per policy.

**Pipeline fit.** Once a response lands it joins at stage [1] exactly like a bulk
file. The distinctive part is upstream: this adapter makes the **manual
acquisition schedule** observable so a county isn't silently missing its tax /
unincorporated-code coverage. Datasets served here are **not** nightly feeds —
certification must record them as periodic.

---

## 6. Portal Adapter (last resort)

**Purpose.** Extract from a **per-search web portal** when no bulk, feed, API, or
open-data path exists. Explicitly the **last resort** — the certification ranks
scraping below every other method, and this adapter exists only as a documented
fallback (e.g. `cclerk.hctx.net`/`hctax.net` search pages) until a feed/TPIA
channel is in place.

**Supported data sources.**
- Harris **Foreclosure/Probate portal** search (backup to the FTP feed).
- Harris **Tax** sale-list/search pages (backup to TPIA).
- Any county portal with no better method — used sparingly and temporarily.

**Expected inputs.**
- `source_locator`: portal search URL + query parameters (date ranges,
  case types).
- `pacing`: conservative rate limits and politeness delays.
- `schema_expectation` + `field_map` against the scraped result shape (brittle by
  nature).
- `cursor`: date-window or result-page pointer.

**Expected outputs.** Same `AcquisitionRecord`/`RunManifest` shape; provenance
explicitly marks `method_id="portal"` so downstream can weight it as
lower-stability.

**Validation requirements.**
- Stricter than other adapters: result-count sanity per window, mandatory
  required fields, and **structural drift detection** (portal markup changes
  frequently) → `SchemaDriftError` on layout change rather than silent bad data.
- Cross-check a sample against the authoritative record when possible.

**Error handling.** Layout/markup change → fail loud, alert, **do not** emit
guessed data. Blocking/CAPTCHA/rate-limit → back off hard and alert; never
attempt evasion. Treat repeated blocks as a signal to prioritize the
feed/TPIA/open-data path instead.

**Retry strategy.** Minimal and polite: conservative backoff, low concurrency,
strict rate caps. Prefer failing a run over aggressive retry that looks like
abuse.

**Authentication requirements.** None assumed; respect the portal's terms of use.
Do **not** build credentialed or evasive scraping.

**Pipeline fit.** Emits at stage [1] like any adapter, but flagged low-stability;
downstream may require corroboration before promoting portal-only records to
leads. **Architecturally, every Portal Adapter binding is a to-do to replace it**
with a higher adapter — its presence in a county config is a certification
caveat, not a finished state.

---

## 7. Adapter selection guide (decision order)

When binding a county dataset to an adapter, choose in this order (highest
authority/automatability first — the Harris certification's ranking):

1. **Bulk CSV Adapter** — is there an official bulk export? (best)
2. **FTP Adapter** — is there a licensed/government feed?
3. **ArcGIS REST Adapter** — is it geospatial with an ArcGIS layer?
4. **Open Data Adapter** — is it on an open-data portal (download or API)?
5. **TPIA Adapter** — public record but only on request? (periodic, not nightly)
6. **Portal Adapter** — none of the above? (last resort; log it as a gap to fix)

## 8. What this sprint does NOT do

- Does not implement any adapter or base class.
- Does not modify `scraper/*` or any production code.
- Does not resolve the `UNVERIFIED` source specifics — those are confirmed
  per-county during certification (`county_certification_checklist.md`).
