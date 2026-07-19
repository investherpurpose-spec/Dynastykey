# Acquisition Layer — Reusable County Data-Acquisition Architecture

**Status:** design / blueprint only. No collectors, adapters, or production code
are written or modified in this sprint. This document defines the target
architecture that every future county plugs into.

**Origin:** distilled from the Harris County certification sprint
(`county_source_registry/harris/`). Harris proved that counties do **not** need
bespoke collectors — they need a small set of **acquisition methods** (bulk,
FTP, ArcGIS REST, open data, TPIA, portal) that recur across every county. This
layer turns those methods into **reusable adapters**.

---

## 1. Problem this replaces

Today the platform thinks in **county-specific collectors**: a Harris ArcGIS
scraper, a Harris HCAD bulk loader, and (implicitly) a different bespoke thing
per future county. That does not scale — every new county would re-implement
paging, retries, provenance, and normalization from scratch.

The Harris Decision Matrix showed the real pattern: **the same six acquisition
methods recur across all datasets and all counties.** A county is not a new
collector; it is a **configuration** that binds each of its datasets to one of a
fixed set of adapters.

```
OLD:  county  ->  bespoke collector per dataset   (N counties x M datasets = NxM implementations)
NEW:  county  ->  config binding each dataset to 1 of 6 adapters   (6 adapters, reused everywhere)
```

## 2. Design principles

1. **Method over county.** An adapter encapsulates an *acquisition method*, never
   a county. Harris and any future county share the same `ArcGISRESTAdapter`.
2. **Authoritative-source bias.** Adapters exist for the methods the
   certification ranked highest (bulk, feed, REST, open data). The Portal Adapter
   is explicitly the last resort.
3. **Uniform contract.** Every adapter takes a typed config and emits the **same
   output shape** (`AcquisitionRecord` stream + a `RunManifest`), so everything
   downstream (normalization, identity resolution, scoring) is adapter-agnostic.
4. **Provenance is mandatory.** Every record carries where/when/how it was
   acquired — this extends the `_scrape_meta` provenance already in `scraper/db.py`.
5. **Reuse what exists.** The current `arcgis.py` (paging, retry, resumability),
   `hcad_bulk.py` (bulk parse), `db.py` (field sanitize + provenance), and
   `names.py`/`claude_match.py` (identity resolution) are the **reference
   implementations** the adapters generalize — not code to throw away.
6. **Config-driven, testable, dependency-light.** Adapters are pure "source →
   normalized records" components; no adapter reaches into scoring or county
   business logic.

## 3. Where the Acquisition Layer sits in the pipeline

The existing repo already has the downstream stages; the Acquisition Layer
formalizes and standardizes the front of the pipe.

```
                    ┌───────────────────────────── ACQUISITION LAYER (this sprint) ─────────────────────────────┐
  Authoritative     │   Adapter (1 of 6)                                                                          │
  source            │   ├─ fetch (auth, retry, paging)                                                            │
  (bulk / FTP /     │   ├─ parse (CSV / pipe / JSON / shapefile)                                                  │
   REST / open      │   ├─ per-record validate                                                                    │
   data / TPIA /    │   └─ emit AcquisitionRecord stream + RunManifest                                            │
   portal)          │                                                                                             │
                    └──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                                                │  uniform AcquisitionRecord stream
                                                                ▼
   [1] NORMALIZATION / LANDING      -> scraper/db.py today: sanitize() field names, esri/type mapping,
                                       land rows into storage, write _scrape_meta provenance
                                                                │
                                                                ▼
   [2] FIELD MAPPING                 -> map source fields -> Dynasty Key canonical fields
                                       (Parcel/Account, Property Address, Mailing Address, Owner Name,
                                        Property Type, Land Use, Distress Date, Amount Due, Sale Date,
                                        Case Number, Status, Source Updated Date)
                                                                │
                                                                ▼
   [3] IDENTITY RESOLUTION           -> scraper/names.py (normalize/token/fuzzy) + claude_match.py (LLM),
                                       resolve records to an HCAD parcel/owner (the join spine)
                                                                │
                                                                ▼
   [4] OPPORTUNITY SCORING           -> (future) stacking distress signals per canonical parcel
                                                                │
                                                                ▼
   [5] EXPORT / ACTION LIST          -> nightly leads
```

**Key boundary:** an adapter's job ends at **[1] — it hands off a validated,
provenance-tagged `AcquisitionRecord` stream.** Field mapping, identity
resolution, and scoring are shared, adapter-independent stages. This is what lets
one adapter serve every county.

## 4. The common adapter contract

Every adapter — regardless of method — conforms to one interface. (Illustrative
signature; **not** to be implemented this sprint.)

```
Adapter
  .method_id            # "bulk_csv" | "ftp" | "arcgis_rest" | "open_data" | "tpia" | "portal"
  .validate_config(cfg) -> ConfigReport      # fail fast on bad/missing config
  .probe(cfg)           -> SourceProbe        # cheap reachability/metadata check (count, schema, freshness)
  .acquire(cfg, cursor) -> Iterator[AcquisitionRecord], RunManifest
```

### Shared input: `AcquisitionConfig`
```
county_id            e.g. "harris"
dataset_id           e.g. "property" | "foreclosure" | ...
method_id            which adapter
source_locator       URL / host+path / portal id / request template
auth_ref             reference to a secret (never an inline credential)
schema_expectation   expected fields + required-field list + types
field_map            source_field -> Dynasty Key canonical field
validation_rules     counts/ranges/freshness/duplicate thresholds (see §5 of each adapter)
retry_policy         max attempts, backoff base, polite delay
cadence              how often this dataset is refreshed (nightly / weekly / monthly / on-request)
```

### Shared output: `AcquisitionRecord`
```
canonical_fields     dict of Dynasty Key canonical fields (post field-map, pre identity-resolution)
raw                  original source row (audit)
provenance           {county_id, dataset_id, method_id, source_locator,
                      acquired_at, source_updated_at, run_id, record_hash}
validation           {status: ok|warn|reject, reasons: [...]}
```

### Shared output: `RunManifest`
```
run_id, county_id, dataset_id, method_id
started_at, ended_at, cursor_out (for resumability)
records_emitted, records_rejected, reject_reasons{}
expected_count, count_delta_pct
freshness{source_updated_at, is_stale}
status: PASS | PARTIAL | FAIL
```

`RunManifest` is the standardized equivalent of the current `_scrape_meta` row
and the self-verification/stacking dictionaries — one shape for every adapter,
so nightly gating and alerting are uniform across counties.

## 5. Harris County → adapter binding (worked example)

Applying the layer to the Harris Decision Matrix. This is the template every
future county fills in.

| Dataset          | Best Method → Adapter          | Backup → Adapter            | Certification grade |
|------------------|--------------------------------|-----------------------------|---------------------|
| Property         | HCAD Bulk → **Bulk CSV Adapter** | ArcGIS → **ArcGIS REST Adapter** | A |
| GIS / Parcel     | ArcGIS REST → **ArcGIS REST Adapter** | Shapefile → **Bulk CSV Adapter** (file variant) | A– |
| Foreclosure      | Clerk Feed → **FTP Adapter**   | Portal → **Portal Adapter** | A (feed) |
| Probate          | Clerk Feed → **FTP Adapter**   | Portal → **Portal Adapter** | B (feed) |
| Tax Delinquent   | TPIA Export → **TPIA Adapter** | Portal → **Portal Adapter** | C+ |
| Houston 311      | Open Data → **Open Data Adapter** | API → **Open Data Adapter** (API mode) | B / enrichment |
| Code Enforcement | Open Data → **Open Data Adapter** | TPIA → **TPIA Adapter** | C+ |

Observations that generalize to all counties:
- **Six adapters cover every Harris dataset**, primary *and* backup.
- The **FTP Adapter** serves two datasets (Foreclosure + Probate) from one Clerk
  feed contract — adapters are many-datasets-to-one.
- Backups are just a *different adapter binding* on the same dataset, so failover
  is a config switch, not new code.

## 6. Adapter roster (specs in `adapter_specifications.md`)

| Adapter | Primary role | Harris datasets it serves |
|---|---|---|
| **Bulk CSV Adapter** | Download + parse a bulk flat file (CSV/TSV/pipe/zip; shapefile as a file variant) | Property (primary), GIS (backup) |
| **FTP Adapter** | Scheduled pull of licensed/government feed files over FTP/SFTP | Foreclosure, Probate |
| **ArcGIS REST Adapter** | Paged bulk query of an ArcGIS FeatureServer/MapServer layer | GIS (primary), Property (backup) |
| **Open Data Adapter** | Socrata/CKAN/ArcGIS-Hub open-data download or API (SoQL) | Houston 311, Code Enforcement |
| **TPIA Adapter** | Human-in-the-loop recurring Texas Public Information Act request intake | Tax (primary), Code (backup), unincorporated gaps |
| **Portal Adapter** | Per-search web portal extraction — **last resort / fallback only** | Foreclosure/Probate/Tax backups |

## 7. Certification gate

No county is production-ready until every one of its core datasets passes the
**County Certification Checklist** (`county_certification_checklist.md`). The
checklist is the reusable gate; this Acquisition Layer is the reusable machinery
the checklist certifies against.

## 8. Explicitly out of scope for this sprint

- No adapters are implemented.
- No collectors are written or modified.
- No production code changes.
- Data-source specifics remain as certified in `county_source_registry/harris/`
  (including all `UNVERIFIED` items — those must be resolved per-county at
  certification time, not assumed here).

## 9. Related documents

- `docs/adapter_specifications.md` — full per-adapter specifications.
- `docs/county_certification_checklist.md` — the reusable go-live gate.
- `county_source_registry/harris/` — the Harris certification this design is built on.
