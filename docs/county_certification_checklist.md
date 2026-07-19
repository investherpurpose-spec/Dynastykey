# County Certification Checklist

**Status:** reusable process document (blueprint only — no code). Every future
county must satisfy this checklist **before it is considered production-ready.**
It is the go-live gate for the Acquisition Layer (`acquisition_layer.md`) and is
applied per **dataset**, then rolled up to a county-level verdict.

**How to use it.** Copy this file to
`county_source_registry/<county>/CERTIFICATION.md`, fill in the tables, and
require every core dataset to reach **CERTIFIED** (or an explicitly accepted
conditional) before the county goes to nightly production. The Harris County
registry (`county_source_registry/harris/`) is the worked reference.

**Evidence discipline (inherited from the Harris sprint).** Every checklist item
must be backed by official documentation or direct evidence. Anything that cannot
be verified is marked **`UNVERIFIED — Further confirmation required`** and blocks
unconditional certification for that dataset.

---

## Part A — Core datasets to certify (per county)

The standard core set (adjust per county, but justify omissions):

1. Property Ownership
2. Tax Delinquent Real Property
3. Foreclosure Notices & Sales
4. Probate
5. Code Enforcement
6. 311 / Service Requests (enrichment)
7. GIS / Parcel Data

---

## Part B — Per-dataset certification checklist

Complete this block for **each** core dataset. A dataset is **CERTIFIED** only
when every mandatory item is ✅ (or a caveat is explicitly accepted and recorded).

| # | Checklist item | Evidence required | Status |
|---|---|---|---|
| 1 | **Official source identified** | Named authoritative data owner + official URL/contact | ☐ |
| 2 | **Preferred acquisition method documented** | Method + adapter binding (§ adapter roster) | ☐ |
| 3 | **Backup acquisition method documented** | Second method + adapter binding; failover is a config switch | ☐ |
| 4 | **Required fields mapped** | Dynasty Key canonical fields → source fields; missing fields listed | ☐ |
| 5 | **Sample data validated** | Real sample pulled; schema, counts, encoding checked against expectation | ☐ |
| 6 | **Normalization plan completed** | Field map + type coercion + stage [1]/[2] landing plan defined | ☐ |
| 7 | **Identity resolution verified** | Records resolve to the parcel/owner spine at an acceptable match rate | ☐ |
| 8 | **Opportunity scoring ready** | Distress signal + date + status usable by the scoring stage | ☐ |
| 9 | **Nightly automation feasible** | Cadence + auth + retry make automated runs viable (or marked periodic/manual) | ☐ |

### Mandatory supporting detail per dataset

- **Adapter binding:** Best = ______ (adapter) · Backup = ______ (adapter)
- **Reliability grade:** A+ / A / B / C / D (with rationale — see Harris files)
- **Cadence:** nightly / weekly / monthly / on-request (**must match reality** —
  do not certify "nightly" for a source that updates monthly)
- **Authentication:** none / token / FTP credentials / request-identity
- **Required-field gaps:** list canonical fields the source lacks + how they're
  filled (usually via cross-join to the Property/GIS spine)
- **Validation thresholds:** expected count band · duplicate threshold ·
  freshness window · required-field non-null rules
- **UNVERIFIED items:** every specific that could not be confirmed from an
  official source (these block unconditional certification)

### Dataset verdict

- ☐ **CERTIFIED** — all mandatory items ✅, no blocking UNVERIFIED item
- ☐ **CONDITIONAL** — certifiable subset defined; blocking items explicitly listed
  and accepted (e.g. "sale-list only; full roll pending TPIA")
- ☐ **NOT CERTIFIED** — blocking gaps remain

---

## Part C — Item-by-item acceptance criteria

**1. Official source identified.** The *authoritative owner* (statutory
custodian), not an aggregator. Third-party republishers (e.g. commercial list
vendors) are never the source of record — only a cross-check.

**2. Preferred acquisition method documented.** Chosen via the adapter selection
order (Bulk → FTP → ArcGIS REST → Open Data → TPIA → Portal). If the choice is
**Portal Adapter**, that is automatically a certification caveat and a to-do to
replace.

**3. Backup acquisition method documented.** A real fallback bound to a second
adapter, so a source outage is a config switch, not an outage of the dataset.

**4. Required fields mapped.** Map the 12 Dynasty Key canonical fields — Parcel/
Account Number, Property Address, Mailing Address, Owner Name, Property Type,
Land Use, Distress Date, Amount Due, Sale Date, Case Number, Status, Source
Updated Date — to source fields. Every unmapped field is listed with its fill
strategy (usually a cross-join to the Property/GIS spine).

**5. Sample data validated.** Pull a **real** sample through the bound adapter and
confirm schema, count band, encoding, and required-field presence. No dataset is
certified on documentation alone — a live sample is mandatory. (In the Harris
sprint several specifics remained `UNVERIFIED` precisely because live reads were
blocked; those must be closed here.)

**6. Normalization plan completed.** Define stage [1] landing (`db.py`-style
field sanitize + provenance) and stage [2] field mapping. Confirm the adapter's
output shape flows cleanly into storage.

**7. Identity resolution verified.** Records must resolve to the parcel/owner
spine (`names.py`/`claude_match.py`) at an acceptable, measured match rate.
Datasets with weak linkage (e.g. Probate decedent→owner) require a confidence
gate and human review before leads promote — never auto-promote low-confidence
identity matches.

**8. Opportunity scoring ready.** The dataset contributes a usable distress
signal (a Distress Date, a Status, and where relevant an Amount Due / Sale Date)
that the scoring stage can stack per canonical parcel. Enrichment-only datasets
(e.g. 311) are marked as such and never originate a lead.

**9. Nightly automation feasible.** The cadence, authentication, and retry
strategy make automated runs viable — **or** the dataset is explicitly recorded
as **periodic/manual** (e.g. a TPIA-Adapter dataset). Certifying "nightly" for a
monthly or on-request source is a defect.

---

## Part D — County-level roll-up

A county is **PRODUCTION-READY** only when:

- ☐ All core datasets are **CERTIFIED** or **CONDITIONAL-with-accepted-caveats**.
- ☐ Property **and** GIS are CERTIFIED (they are the identity spine everything
  else joins to — no county is production-ready without them).
- ☐ Every dataset has a bound preferred **and** backup adapter.
- ☐ Every remaining **UNVERIFIED** item is either resolved or explicitly accepted
  as a known limitation with an owner and a follow-up.
- ☐ Datasets requiring recurring **TPIA** requests have an established, scheduled
  request cadence (not an intention).
- ☐ Any **Portal Adapter** binding is logged as tech-debt with a replacement plan.
- ☐ Per-dataset validation thresholds (count/freshness/duplicate/required-field)
  are defined so nightly gating can go PARTIAL/FAIL correctly.
- ☐ Estimated engineering + external (contract/TPIA) effort documented.

### County certification summary table (fill per county)

| Dataset | Best adapter | Backup adapter | Grade | Cadence | Verdict | Blocking UNVERIFIED |
|---|---|---|---|---|---|---|
| Property | | | | | | |
| Tax Delinquent | | | | | | |
| Foreclosure | | | | | | |
| Probate | | | | | | |
| Code Enforcement | | | | | | |
| 311 | | | | | | |
| GIS / Parcel | | | | | | |

### Final county verdict

- ☐ **PRODUCTION-READY**
- ☐ **CONDITIONAL** (list blocking items + owners)
- ☐ **NOT READY**

---

## Part E — Worked reference: Harris County

Harris is the first county through this gate. Per its registry
(`county_source_registry/harris/`):

| Dataset | Best adapter | Backup adapter | Grade | Verdict (as certified) |
|---|---|---|---|---|
| Property | Bulk CSV | ArcGIS REST | A | CERTIFIED (conditional: live URL + codebook) |
| GIS | ArcGIS REST | Bulk CSV (shapefile) | A– | CERTIFIED (conditional: verify live layer) |
| Foreclosure | FTP | Portal | A (feed) | CERTIFIED via feed (conditional: contract) |
| Probate | FTP | Portal | B | CONDITIONAL (match validation) |
| Code Enforcement | Open Data | TPIA | C+ | CONDITIONAL (Houston-only; county gap) |
| 311 | Open Data | Open Data (API) | B / enrichment | CERTIFIED as enrichment only |
| Tax Delinquent | TPIA | Portal | C+ | CONDITIONAL (sale-list + TPIA) |

Harris is **CONDITIONAL** overall: no dataset is unconditionally certified until
the blocked live reads and the County Clerk feed terms are confirmed. That is the
exact template — a county reaches production-ready by closing each conditional's
named UNVERIFIED item.
