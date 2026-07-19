# Harris County — Houston 311 (Dataset #6)

> **Evidence discipline.** Direct reads of `data.houstontx.gov` /
> `houstontx.gov/311` were **not possible** from this environment (network
> policy / HTTP 403). Owner and dataset existence are corroborated via official
> search-result summaries; specifics are marked
> **UNVERIFIED — Further confirmation required.**

---

## 1. Official Data Owner

**City of Houston 311 Houston Service Helpline** — the City's non-emergency
service-request system. Authoritative owner of the 311 service-request record.

- 311 Service Request Data page: <https://houstontx.gov/311/servicerequestdata.html> (VERIFIED URL via search results).
- City Open Data portal: <https://data.houstontx.gov/> (VERIFIED).
- Intake: 713.837.0311 / online. (VERIFIED via search results.)

Scope note: this is a **City of Houston** system. It does **not** cover
unincorporated Harris County or other cities. 311 is a **complaint/interest
signal** (potholes, nuisance, debris, code complaints), not a title/ownership
record — it is supplementary, not a core distress source.

## 2. Official Acquisition Methods

| Method | Available? | Evidence |
|---|---|---|
| **Open Data Portal / Download** | **Yes** | 311 service-request data published on houstontx.gov/311 and the City open data portal. VERIFIED. |
| Socrata API | Likely (portal is Socrata-based) | **UNVERIFIED** for the Houston 311 dataset specifically. |
| Bulk file (CSV) | Likely | Service-request data page distributes downloadable extracts. Format **UNVERIFIED**. |
| TPIA / Open Records | Yes (fallback) | Public record. |

**Data-quality caveat (from the source itself):** search results surface an
explicit disclaimer that the **311 open data feed "may not be reflective of the
actual data."** Treat 311 as a soft signal and never as a system of record.

## 3. Data Characteristics

| Attribute | Value | Basis |
|---|---|---|
| Update frequency | Regularly refreshed (prior research indicated a roughly nightly refresh) | **UNVERIFIED** — cadence not confirmed from source in this environment |
| Record volume | Large (millions of historical requests over multiple years) | **UNVERIFIED** exact count |
| Format | Downloadable extract (CSV) + likely Socrata API | Partially VERIFIED (download exists) / API **UNVERIFIED** |
| Key fields | case/SR number, SR type, status, open/close dates, address/location, department, channel | Partially VERIFIED / exact schema **UNVERIFIED** |
| Exclusions | Prior research indicated certain categories (e.g., BARC/animal) excluded | **UNVERIFIED** |
| Authentication | None (open data) | VERIFIED (open portal) |
| Cost | Free | VERIFIED |
| Licensing | City open-data **Terms of Use** (data.houstontx.gov terms page) | VERIFIED existence / exact terms **UNVERIFIED** |
| Geographic coverage | **City of Houston only** | VERIFIED (jurisdiction) |

## 4. Dynasty Key Required Fields

| Required field | Present? | Note |
|---|---|---|
| Parcel / Account Number | **No** | Keyed on address/location; match to HCAD needed |
| Property Address | **Yes** (approx.) | SR location/address (may be intersection/approximate) |
| Mailing Address | **No** | Via HCAD after match |
| Owner Name | **No** | Requester is usually anonymous; owner via HCAD |
| Property Type | **No** | Via HCAD |
| Land Use | **No** | Via HCAD |
| **Distress Date** | **Yes** (weak) | SR open date (interest signal, not legal distress) |
| Amount Due | **No** | N/A |
| Sale Date | **No** | N/A |
| **Case Number** | **Yes** | SR number |
| Status | **Yes** | Open/closed/resolved |
| Source Updated Date | **Yes** | SR update/close date |

**Missing:** owner/parcel identity and any legal distress — 311 is a
**location-tagged interest signal**. Its only path to a parcel is address→HCAD
matching, and its addresses can be approximate.

## 5. Reliability Assessment

| Dimension | Grade | Rationale |
|---|---|---|
| Official authority | A (as a 311 record) | City owns its 311 data. |
| Completeness | B | Large and citywide-Houston, but self-disclosed as possibly not fully reflective; category exclusions. |
| Freshness | B | Frequent refresh (cadence UNVERIFIED). |
| Stability | B | Open portal is reasonably stable. |
| Ease of automation | A | Open CSV/API is easy to automate. |
| Bulk availability | A | Bulk download is a first-class method. |
| Long-term maintainability | B | Portal durable; schema drift possible. |
| **Signal value for Dynasty Key** | **C** | Supplementary interest signal; low standalone distress value. |

**Overall grade: B on data quality/access, but C as a distress signal.** Easy to
acquire; low intrinsic value as a lead source. Best used as an **enrichment
overlay**, not a primary trigger.

## 6. Production Recommendation

- **Preferred:** **Import** the 311 open dataset (bulk CSV, and Socrata API if
  confirmed) as a **supplementary enrichment layer**; match to HCAD by address.
- **Use it to score/enrich**, not to originate leads (e.g., density of nuisance
  SRs near a parcel already flagged by tax/foreclosure/probate).
- **Do NOT** treat 311 as a distress system of record given the source's own
  "may not reflect actual data" caveat and City-only coverage.

**Architectural note:** No new architecture — standard open-data import + address
match. The correct decision is **priority**: 311 is the lowest-priority of the
seven datasets and should not consume core engineering ahead of Property, Tax,
Foreclosure, and Probate.

## 7. Validation Strategy

- **Expected record count:** incremental daily volume within a plausible band;
  alert on zero or a spike.
- **Schema validation:** require SR number, SR type, status, open date, location.
- **Required-field checks:** open date parses; location geocodable.
- **Duplicate threshold:** dedupe on SR number.
- **Freshness check:** newest SR date is recent relative to refresh cadence
  (once cadence VERIFIED).
- **Cross-validation:** address→HCAD match rate; treat unmatched/approximate
  locations as low-confidence.

## Final Deliverable Questions

1. **Can we acquire this reliably?** Yes — open data, easy to acquire.
2. **Scrape / import / request / license?** **Import** (open data download / API).
3. **Can we automate it?** Yes — straightforwardly.
4. **Would a professional data company build against this source?** As an
   **enrichment** input, yes; not as a primary lead source.
5. **Would I personally certify this for nightly production?** **Yes as a
   supplementary enrichment feed, no as a primary trigger.** The access is
   certifiable; the *signal* is weak and self-disclosed as imperfect, so I would
   certify it only in an enrichment/scoring role and after confirming the refresh
   cadence and category exclusions.

## Remaining Unknowns (UNVERIFIED — Further confirmation required)

- Exact refresh cadence (nightly?) and the Socrata API endpoint for Houston 311.
- Exact schema and which categories are excluded (e.g., BARC).
- Address quality/geocodability distribution.
- Exact open-data terms of use for reuse.

## Sources

- [311 Service Request Data — City of Houston](https://houstontx.gov/311/servicerequestdata.html)
- [City of Houston Open Data](https://data.houstontx.gov/)
- [How to use 311 — City of Houston](https://www.houstontx.gov/311/howtouse.html)
- [Houston 311 Service Requests (data.world mirror, non-authoritative)](https://data.world/houston/houston-311-service-requests)
