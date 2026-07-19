# Harris County — Data Acquisition Certification Summary

**Prepared:** 2026-07-19 · **Scope:** research & certification only (no
collectors written or modified) · **Discipline:** evidence-based; every
unconfirmable specific is tagged **UNVERIFIED — Further confirmation required.**

> **Environment limitation affecting evidence quality.** Outbound HTTPS from the
> certification environment to the authoritative government hosts
> (`gis.hctx.net`, `hcad.org`, `cclerk.hctx.net`, `hctax.net`,
> `data.houstontx.gov`) was **blocked by network policy / returned HTTP 403**.
> Official **owners and acquisition-method existence** are corroborated through
> official search-result summaries and (for Property and GIS) this repo's own
> collector code (direct evidence). **Granular specifics** — exact field lists,
> record counts, refresh cadences, costs, and licensing terms — **could not be
> read directly** and are marked UNVERIFIED throughout. Final production sign-off
> requires confirming these from an environment with access to the sources.

---

## 1. Certification Status (per dataset)

| # | Dataset | Owner | Preferred method | Grade | Certification status |
|---|---|---|---|---|---|
| 1 | **Property Ownership** | HCAD | Bulk download (PDATA `real_acct`) | **A** | **CERTIFIED (conditional)** — confirm live URL + codebook mapping |
| 2 | **Tax Delinquent** | Tax Assessor-Collector + County Attorney | Monthly sale list + recurring TPIA (full roll) | **C+** | **CONDITIONAL** — sale-list subset certifiable; full roll needs TPIA channel |
| 3 | **Foreclosure** | County Clerk | **Licensed FTP feed** (pipe-delimited) | **A** feed / C portal | **CERTIFIED via feed (conditional on contract)** — NOT via scraping |
| 4 | **Probate** | County Clerk | **Licensed FTP feed** (same contract) | **B** feed / C– portal | **CONDITIONAL** — feed certifiable; lead promotion needs match validation |
| 5 | **Code Enforcement** | City of Houston (+ County gap) | Open data import (DON dataset) | **C+** | **CONDITIONAL** — Houston only; unincorporated county UNVERIFIED gap |
| 6 | **Houston 311** | City of Houston | Open data import (enrichment) | **B** access / C signal | **CERTIFIED as enrichment only** — not a primary trigger |
| 7 | **GIS / Parcel** | HCAD / Harris County | ArcGIS REST bulk + shapefile fallback | **A–** | **CERTIFIED (conditional)** — verify live layer metadata/endpoint |

**No dataset is unconditionally certified**, because the authoritative pages
could not be read directly from this environment. Property, Foreclosure (feed),
and GIS are the closest to production-ready.

## 2. Reliability Grades (at a glance)

```
A   Property Ownership (HCAD bulk)
A   Foreclosure   — ONLY via the licensed County Clerk feed (C if scraped)
A-  GIS / Parcel  (ArcGIS REST + shapefile)
B   Probate       — via feed; property-linkage is the risk
B   Houston 311   — easy access, weak signal (enrichment)
C+  Code Enforcement — Houston-only; county gap
C+  Tax Delinquent — authoritative but no bulk full-roll feed
```

## 3. Remaining Unknowns (consolidated — UNVERIFIED)

- **Property:** live `download.hcad.org` URL; exact codebook columns; refresh cadence; record count.
- **Tax Delinquent:** whether a bulk full-roll extract is obtainable (format/cost/cadence); sale-list machine-readable format; which law firm holds which roll.
- **Foreclosure/Probate:** **County Clerk feed price, terms, and exact index schema**; how well the feed key eases HCAD parcel matching; achievable decedent→owner match precision.
- **Code Enforcement:** DON dataset schema/cadence/API; **whether unincorporated Harris County publishes any data**; impact of the IPS→Public Works reorg.
- **Houston 311:** refresh cadence (nightly?); Socrata API endpoint; category exclusions.
- **GIS:** live parcels field list, count, `supportsPagination`; whether the current endpoint has moved; shapefile URL/cadence; Open Data Hub decommissioning status.

## 4. Acquisition Strategy per Dataset (import / license / request / scrape)

| Dataset | Strategy | Rationale |
|---|---|---|
| Property Ownership | **Import** (bulk) | Authoritative first-class bulk download. |
| GIS / Parcel | **Import** (ArcGIS REST + shapefile) | Sanctioned bulk API; already implemented well. |
| Foreclosure | **License + Import** (Clerk FTP feed) | Authoritative, daily, machine-readable; scraping is fallback only. |
| Probate | **License + Import** (same feed) | One contract covers foreclosure + probate. |
| Houston 311 | **Import** (open data, enrichment role) | Easy; low standalone value. |
| Code Enforcement (Houston) | **Import** (open data) | Open portal dataset. |
| Code Enforcement (unincorporated county) | **Request (TPIA)** | No open dataset confirmed. |
| Tax Delinquent (sale list) | **Import** | Monthly authoritative subset. |
| Tax Delinquent (full roll) | **Request (recurring TPIA)** | No bulk export exists. |

**Datasets that should be IMPORTED, not scraped:** Property, GIS, Houston 311,
Houston Code Enforcement, Tax sale list.
**Datasets that should be LICENSED (feed):** Foreclosure, Probate.
**Datasets requiring recurring TEXAS PUBLIC INFORMATION ACT requests:** Tax
Delinquent full roll; unincorporated-county Code Enforcement.
**Scraping is recommended for NONE as the primary method** — only as a temporary
fallback for Foreclosure/Probate before the feed contract is in place.

## 5. Architectural Recommendation (STOP-AND-RECOMMEND, per CTO rule)

**Finding:** The highest-value distress datasets — **Foreclosure and Probate** —
have a **production-grade authoritative source that this repo's current
architecture does not use**: the **Harris County Clerk licensed data feed**
(index data as **pipe-delimited `.txt`**, delivered **daily over FTP, purchased
monthly**; images as TIFF; contact `datasales@cco.hctx.net` / 713-274-6390). A
**single feed contract covers both foreclosure and probate.**

**Recommendation (make this decision before writing/expanding collectors):**

1. **Adopt a feed-ingestion architecture** for Foreclosure + Probate — a
   scheduled fetcher for the daily pipe-delimited index files — instead of a
   per-search ASPX scraper against `cclerk.hctx.net`. The feed is more complete,
   more stable, and vastly easier to automate than the web portal.
2. **Keep the existing bulk/REST architecture** for Property (HCAD bulk) and GIS
   (ArcGIS REST) — those are already the correct approaches and need no change.
3. **Reuse the repo's HCAD-matching layer** (`scraper/names.py`,
   `scraper/claude_match.py`) as the shared **identity-resolution** step that
   links feed records (foreclosure grantors, probate decedents, code-enforcement
   and 311 addresses) to HCAD parcels/owners. This is the real engineering
   investment and is architecture-neutral.
4. **Do not force the per-search scraper pattern onto the Clerk source.** Portal
   scraping is the documented fallback only.

**Net:** one new component (a licensed-feed importer) + the existing HCAD matcher
covers Foreclosure and Probate at grade A/B. This is the single most important
architectural change surfaced by the certification.

## 6. Engineering Priorities (recommended order)

1. **Confirm the two blocking procurement/verification items** (no code):
   (a) obtain the County Clerk feed price/terms/schema; (b) verify live HCAD bulk
   URL + GIS endpoint metadata from a network-enabled environment.
2. **Property (Dataset #1)** — pin the live bulk URL and map columns to the PDATA
   codebook; make schema validation exact. *(Collector already exists — keep it.)*
3. **GIS (Dataset #7)** — verify live layer metadata; add the shapefile-download
   fallback. *(Collector already exists — keep it.)*
4. **Foreclosure + Probate (Datasets #3, #4)** — build the **licensed-feed
   importer** (new component) once the contract is in place; wire the HCAD matcher.
5. **Tax Delinquent (Dataset #2)** — ingest the monthly sale list; establish the
   recurring TPIA cadence for the full roll.
6. **Code Enforcement (Dataset #5)** — import the Houston DON dataset; document
   the unincorporated-county gap; open a TPIA if that coverage is required.
7. **Houston 311 (Dataset #6)** — lowest priority; import as an enrichment overlay.

## 7. Which collectors to rewrite / keep / add

- **KEEP (correct architecture):**
  - `scraper/hcad_bulk.py` (Property bulk) — sound; only pin URL + codebook mapping.
  - `scraper/arcgis.py` (GIS REST) — sound; add shapefile fallback + verify endpoint.
  - `scraper/names.py` / `scraper/claude_match.py` (identity resolution) — keep and
    invest here; it is the shared spine for every distress dataset.
- **ADD (new architecture — the key change):**
  - A **County Clerk licensed-feed importer** (pipe-delimited FTP index) serving
    **both Foreclosure and Probate**.
  - Open-data importers for **Houston 311** and **Houston Code Enforcement (DON)**.
  - A **monthly tax-sale-list importer** + a **recurring TPIA intake** process
    (partly human-in-the-loop) for the full delinquent roll and unincorporated
    code enforcement.
- **DO NOT build as primary:** a `cclerk.hctx.net` per-search foreclosure/probate
  scraper — use the feed instead (scraper only as temporary fallback).

## 8. Datasets requiring recurring Texas Public Information Act requests

- **Tax Delinquent — full roll** (no bulk export exists).
- **Unincorporated Harris County code enforcement** (no confirmed open dataset).
- (Property/GIS custom fields via TPIA only if a needed column is absent from PDATA.)

## 9. Estimated effort to make Harris County production-ready

*Engineering estimate; procurement/legal timelines are external and UNVERIFIED.*

| Workstream | Effort (eng.) | Notes |
|---|---|---|
| Verify live sources + pin URLs/schemas (Property, GIS) | **~2–4 days** | Needs network access to the gov hosts. |
| County Clerk feed contract + importer (Foreclosure + Probate) | **~1–2 weeks eng** after contract | Contract lead time is external/UNVERIFIED and may dominate. |
| HCAD identity-resolution hardening (shared matcher) | **~1–2 weeks** | Highest-leverage; used by every distress dataset. |
| Tax sale-list importer + TPIA cadence | **~3–5 days** eng + ongoing manual TPIA | Full roll is process, not code. |
| Houston Code Enforcement + 311 open-data importers | **~3–5 days** | Straightforward open-data ingest. |
| Validation/monitoring per dataset (counts, schema, freshness, cross-join) | **~1 week** | Per the validation strategies in each file. |

**Rough total:** on the order of **4–6 engineering weeks** to a certified
production state for the automatable datasets, **plus external timelines** for
(a) the County Clerk feed contract and (b) establishing TPIA cadences — both
UNVERIFIED and potentially the critical path.

## 10. Bottom line

- **Property and GIS** are on the right architecture today; certify after quick
  live verification. **Keep those collectors.**
- **Foreclosure and Probate** should move to the **licensed County Clerk feed** —
  the single most important architectural change; **do not deepen portal
  scraping.**
- **Tax Delinquent** and **unincorporated Code Enforcement** are the weak spots:
  no clean bulk source; plan for **monthly sale-list import + recurring TPIA**,
  and be honest that these are not live nightly feeds.
- **Houston 311 and Houston Code Enforcement** are easy open-data imports; 311 is
  enrichment-only.
- **Nothing is unconditionally certified** until the sources are read directly and
  the Clerk-feed terms are confirmed — every conditional above is explicitly
  gated on a stated UNVERIFIED item.

### Per-dataset detail: see `property.md`, `tax_delinquent.md`, `foreclosure.md`, `probate.md`, `code_enforcement.md`, `houston_311.md`, `gis.md`.
