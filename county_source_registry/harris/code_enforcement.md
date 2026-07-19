# Harris County — Formal Code Enforcement (Dataset #5)

> **Evidence discipline.** Direct reads of `data.houstontx.gov` /
> `houstontx.gov` were **not possible** from this environment (network policy /
> HTTP 403). Owner and dataset existence are corroborated via official
> search-result summaries; specifics are marked
> **UNVERIFIED — Further confirmation required.**

---

## 1. Official Data Owner

Code enforcement in the Harris County area is **jurisdictionally split** — this
is the single most important fact about this dataset:

- **City of Houston** — Community Code Enforcement (formerly Department of
  Neighborhoods, Inspections & Public Service / IPS; the division moved to
  **Houston Public Works**), enforcing **Chapter 10 (Buildings and Neighborhood
  Protection)** of the City Code. Authoritative owner for **incorporated
  Houston**. (VERIFIED via search results.)
- **Harris County** (unincorporated areas) — the County enforces a narrower set
  of nuisance/health regulations (Texas counties have limited zoning/code
  authority). Authoritative owner for **unincorporated Harris County**.
  **UNVERIFIED** which department and what data access exists.
- **Other incorporated cities** within Harris County (Pasadena, Baytown, etc.)
  each own their own code enforcement. Out of scope unless separately certified.

**Coverage gap warning:** A "Harris County code enforcement" dataset does not
exist as a single authoritative feed. City of Houston covers only the
incorporated city; large unincorporated areas are covered (thinly) by the County
and are **UNVERIFIED**.

## 2. Official Acquisition Methods

| Method | Available? | Evidence |
|---|---|---|
| **Open Data Portal** (City of Houston) | **Yes** | *City of Houston Building Code Enforcement Violations (DON)* dataset on the City open data portal: <https://data.houstontx.gov/dataset/city-of-houston-building-code-enforcement-violations-don>. VERIFIED. |
| **311 intake** (violation reporting) | Yes | 311 service requests feed code cases (see `houston_311.md`); report via 713.837.0311 / online. VERIFIED. |
| Socrata API | Likely (portal is Socrata-based) | **UNVERIFIED** for this specific dataset. |
| Harris County (unincorporated) feed | **Not found** | **UNVERIFIED** whether any open dataset exists. |
| TPIA / Open Records | Yes (fallback) | Both City and County records obtainable by request. |

## 3. Data Characteristics

| Attribute | Value | Basis |
|---|---|---|
| Update frequency | Portal dataset refreshed on the City's schedule | **UNVERIFIED** exact cadence |
| Record volume | Substantial (citywide violations) | **UNVERIFIED** exact count |
| Format | Portal export (CSV) + likely Socrata API | Partially VERIFIED (portal) / API **UNVERIFIED** |
| Key fields | case ID, violation type (Chapter 10), address, status, open/close dates | Partially VERIFIED / exact schema **UNVERIFIED** |
| Authentication | None (open data) | VERIFIED (open portal) |
| Cost | Free | VERIFIED (open portal) |
| Licensing | City open-data terms of use | VERIFIED (nature) / exact terms **UNVERIFIED** |
| Geographic coverage | **Incorporated City of Houston only** | VERIFIED (jurisdiction) |

## 4. Dynasty Key Required Fields

| Required field | Present? | Note |
|---|---|---|
| Parcel / Account Number | **No** (likely) | Keyed on address, not HCAD account; match to HCAD needed |
| Property Address | **Yes** | Violation address |
| Mailing Address | **No** | Via HCAD after match |
| Owner Name | **No** (likely) | Via HCAD after match |
| Property Type | **No** | Via HCAD |
| Land Use | **No** | Via HCAD |
| **Distress Date** | **Yes** | Violation/case open date |
| Amount Due | Partial | Fines/liens may exist; **UNVERIFIED** as structured field |
| Sale Date | **No** | N/A |
| **Case Number** | **Yes** | Code case ID |
| Status | **Yes** | Open/closed/complied |
| Source Updated Date | Partial | Case update date; **UNVERIFIED** as field |

**Missing:** owner/parcel identity — must **match violation address → HCAD**
(Dataset #1) via geocoding/address normalization.

## 5. Reliability Assessment

| Dimension | Grade | Rationale |
|---|---|---|
| Official authority | A (City of Houston only) | Authoritative for incorporated Houston; **no** authoritative countywide source. |
| Completeness | C | Only incorporated Houston; unincorporated county is a gap. |
| Freshness | B | Open portal cadence (UNVERIFIED). |
| Stability | B | Open data portal is more stable than a scrape; departmental reorg (IPS→Public Works) is a churn risk. |
| Ease of automation | B | CSV/Socrata export is automatable; address→parcel match is the work. |
| Bulk availability | B | Portal supports bulk export/API. |
| Long-term maintainability | B | Portal is durable; jurisdiction split adds complexity. |

**Overall grade: C+** (good, automatable City source; capped by the
incorporated-only coverage and the unverified unincorporated-county gap).

## 6. Production Recommendation

- **Preferred:** ingest the **City of Houston Building Code Enforcement
  Violations (DON)** dataset from the open data portal (CSV export / Socrata API),
  and match addresses to HCAD.
- **Secondary:** correlate with Houston 311 code-related service requests (see
  `houston_311.md`) for earlier/complaint-stage signal.
- **Unincorporated Harris County:** **recurring TPIA request** to the responsible
  County department until/unless an open dataset is confirmed. Mark this coverage
  as a **known gap** in production.
- **Do NOT** claim countywide code-enforcement coverage from the City dataset
  alone.

**Architectural note:** No new architecture beyond the standard "open-data
import + HCAD address match." The important product decision is **scoping
honesty**: certify Houston-incorporated coverage and explicitly flag
unincorporated county as unverified/gap, rather than implying countywide coverage.

## 7. Validation Strategy

- **Expected record count:** within a plausible citywide band; alert on zero /
  order-of-magnitude shifts.
- **Schema validation:** require case ID, violation type, address, status, and
  open date.
- **Required-field checks:** address geocodable; open date parses.
- **Duplicate threshold:** dedupe on case ID; repeat violations per address are
  legitimate.
- **Freshness check:** portal `updated` timestamp advances on schedule.
- **Cross-validation:** address→HCAD match rate meets a target; unmatched
  addresses flagged.

## Final Deliverable Questions

1. **Can we acquire this reliably?** For **incorporated Houston** — yes (open
   data). For **unincorporated county** — not reliably; TPIA only.
2. **Scrape / import / request / license?** **Import** the City open dataset;
   **request (TPIA)** for unincorporated county.
3. **Can we automate it?** City portion — yes. County portion — no (manual TPIA).
4. **Would a professional data company build against this source?** Yes for
   Houston; a serious operation would document the jurisdiction split rather than
   overstate coverage.
5. **Would I personally certify this for nightly production?** **Yes for the City
   of Houston dataset, with an explicit coverage caveat.** I would **not** certify
   any claim of countywide code-enforcement coverage — the unincorporated-county
   source is UNVERIFIED and must be resolved (or disclosed as a gap) first.

## Remaining Unknowns (UNVERIFIED — Further confirmation required)

- Exact dataset schema, refresh cadence, and Socrata API endpoint for the DON dataset.
- Whether unincorporated Harris County publishes any code-enforcement data, and via what method.
- Whether fines/liens are exposed as structured fields.
- Impact of the IPS → Houston Public Works reorganization on dataset continuity.

## Sources

- [City of Houston Building Code Enforcement Violations (DON)](https://data.houstontx.gov/dataset/city-of-houston-building-code-enforcement-violations-don)
- [Community Code Enforcement — Houston Permitting Center](https://www.houstonpermittingcenter.org/community-code-enforcement)
- [City of Houston Open Data](https://data.houstontx.gov/)
- [Chapter 10 — Buildings and Neighborhood Protection (Municode)](https://library.municode.com/tx/houston/codes/code_of_ordinances?nodeId=COOR_CH10BUNEPR)
