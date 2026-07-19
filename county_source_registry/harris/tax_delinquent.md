# Harris County — Tax Delinquent Real Property (Dataset #2)

> **Evidence discipline.** Direct-page reads of `hctax.net` and
> `caopay.harriscountytx.gov` were **not possible** from this environment
> (network policy denial / HTTP 403). Owners and method existence are
> corroborated through official search-result summaries; granular specifics
> (record counts, exact fields, exact cadence, cost) are marked
> **UNVERIFIED — Further confirmation required.**

---

## 1. Official Data Owner

Two authoritative owners share this domain:

- **Harris County Tax Assessor-Collector** — <https://www.hctax.net/> — owns the
  tax account, the delinquent balance, and the monthly tax-sale listing.
  (VERIFIED via search results.)
- **Harris County Attorney (Delinquent Tax Collections)** — hosts the delinquent
  account search at <https://caopay.harriscountytx.gov/> for the countywide
  taxing jurisdictions it represents. (VERIFIED URL via search results.)

Litigated collections for many Harris County jurisdictions are handled by
outside delinquent-tax law firms (e.g., Linebarger; Perdue Brandon) — **UNVERIFIED**
which firm covers which roll and whether they expose any feed.

## 2. Official Acquisition Methods

| Method | Available? | Evidence |
|---|---|---|
| **Search Portal** (per-account delinquency) | **Yes** | hctax.net property search; County Attorney delinquent-account search at caopay.harriscountytx.gov. VERIFIED. |
| **Published tax-sale list** (monthly) | **Yes** | Tax-sale listing published in advance of the first-Tuesday sale: <https://www.hctax.net/Property/listings/taxsalelisting> and <https://www.hctax.net/Property/TaxSales>. VERIFIED. |
| **Struck-off / resale list** | Yes | Properties not sold at auction; <https://www.hctax.net/Property/AbandonedProperty.cshtml>. VERIFIED existence. |
| Bulk Download (full delinquent roll) | **Not found** | No clean countywide bulk export of the full delinquent roll is advertised. **UNVERIFIED** whether one exists on request. |
| Official API | **Not found** | No structured API advertised. |
| TPIA / Open Records | Yes (fallback) | The delinquent roll is a public record obtainable via a Public Information Act request to the Tax Office / County Attorney. Method exists; scope/cost **UNVERIFIED**. |
| Third-party aggregator | Yes (non-authoritative) | `countytaxsaleapp.org` (CTSA) and commercial lists (e.g., LienSuite) republish the sale list — **not** an authoritative source. |

**Key structural finding:** Unlike Property (Dataset #1), there is **no
first-class bulk download of the full delinquent tax roll**. Production access is
either (a) the monthly published sale list (a *subset* — only properties going to
auction), or (b) per-account lookups, or (c) a TPIA request for the full roll.
This asymmetry is the reason a per-account/search-style collector exists rather
than a bulk importer.

## 3. Data Characteristics

| Attribute | Value | Basis |
|---|---|---|
| Update frequency | Sale list published ~a few weeks before each first-Tuesday sale (monthly cycle); underlying delinquency updates as payments post | VERIFIED (monthly cycle) / exact cadence **UNVERIFIED** |
| Record volume | Sale list = hundreds to low-thousands/month (subset); full delinquent roll = much larger | **UNVERIFIED** exact counts |
| Format | Sale list published as web/PDF/spreadsheet listing | **UNVERIFIED** exact format |
| Key fields | account number, property description/address, taxing jurisdictions, judgment/minimum bid amounts, sale date | Partially VERIFIED / exact schema **UNVERIFIED** |
| Authentication | None for public listings | VERIFIED |
| Cost | Free to view listings; TPIA custom extracts may carry cost | VERIFIED (listings) / **UNVERIFIED** (extract cost) |
| Licensing | Public record | VERIFIED (nature) / exact terms **UNVERIFIED** |
| Geographic coverage | Harris County + constituent taxing units | VERIFIED |

## 4. Dynasty Key Required Fields

| Required field | Present? | Note |
|---|---|---|
| Parcel / Account Number | **Yes** | Tax account number (maps to HCAD account) |
| Property Address | Likely (sale list) | **UNVERIFIED** completeness on every list |
| Mailing Address | Partial | Owner mailing address may require join to HCAD |
| Owner Name | Partial | Present on some listings; join to HCAD for canonical |
| Property Type | **No** (not on sale list) | Join to HCAD |
| Land Use | **No** | Join to HCAD |
| **Distress Date** | **Yes** (proxy) | Delinquency/judgment date; sale-posting date |
| **Amount Due** | **Yes** | Judgment amount / minimum bid on the sale list |
| **Sale Date** | **Yes** | First-Tuesday sale date |
| Case Number | Partial | Tax-suit cause number where litigated |
| Status | **Yes** (proxy) | Active delinquent / struck-off / resale / sold |
| Source Updated Date | **No explicit field** | Derive from list publication date; **UNVERIFIED** |

**Missing:** canonical Property Type / Land Use / Mailing Address on the sale
list itself — these must be **cross-joined to HCAD** (Dataset #1) on the account
number. This is the intended stacking pattern, not a defect.

## 5. Reliability Assessment

| Dimension | Grade | Rationale |
|---|---|---|
| Official authority | A | Tax Office + County Attorney are the statutory owners. |
| Completeness | C–B | Monthly sale list is only the auction subset; full roll needs TPIA. |
| Freshness | B | Clear monthly cadence for the sale list; per-account balances current. |
| Stability | B | Portals stable, but listing format is presentation-oriented, not a stable feed. |
| Ease of automation | C | No bulk/API; scraping the listing or per-account lookups is brittle. |
| Bulk availability | C | No first-class bulk export of the full roll. |
| Long-term maintainability | C | Portal/format changes break scrapers; TPIA is manual. |

**Overall grade: C+** (authoritative and rich in distress fields, but weak on
bulk availability and automation — the datashape, not the authority, is the
limiter).

## 6. Production Recommendation

- **Preferred (for the actionable subset):** ingest the **monthly published
  tax-sale list** from hctax.net on the first-Tuesday cadence — it is the
  authoritative, date-bounded set of properties actually going to auction, and it
  carries Amount Due + Sale Date. Cross-join to HCAD for owner/type/land-use.
- **Secondary:** the **County Attorney delinquent-account search**
  (caopay.harriscountytx.gov) for per-account delinquency confirmation.
- **Fallback for the full roll:** a **recurring Texas Public Information Act
  request** to the Tax Assessor-Collector / County Attorney for the delinquent
  roll extract. Treat this as a scheduled, human-in-the-loop acquisition, not an
  automated feed.
- **Do NOT** treat third-party aggregators (CTSA, commercial list vendors) as the
  source of record — use them only as a cross-check.

**Architectural note:** The full delinquent roll has **no production-grade
automated source**. If Dynasty Key needs the *entire* delinquent roll nightly,
the honest recommendation is **not** to scrape it — it is to establish a
**recurring TPIA request cadence** (e.g., monthly) plus ingestion of the monthly
sale list, and to be explicit that "tax delinquent" coverage = the sale list +
periodic TPIA snapshots, not a live nightly full-roll feed. Do not force a
nightly-scraper architecture onto a source that does not support it.

## 7. Validation Strategy

- **Expected record count:** sale list within a plausible monthly band (alert on
  0 rows near a sale date, or an order-of-magnitude jump).
- **Schema validation:** require account number, amount due, and sale date on
  every sale-list row; reject rows missing sale date.
- **Required-field checks:** amount due numeric and > 0; sale date parses and is
  a future/first-Tuesday date at publication time.
- **Duplicate threshold:** an account may legitimately recur across months but
  should be unique within a single sale-list load.
- **Freshness check:** a new list must appear ahead of each first-Tuesday sale;
  alert if none is found in the expected window.
- **Cross-validation:** every sale-list account should resolve to an HCAD account
  (Dataset #1); unresolved accounts are a data-quality flag.

## Final Deliverable Questions

1. **Can we acquire this reliably?** Partially. The monthly sale-list *subset* —
   yes. The full delinquent roll — only via recurring TPIA, not reliably automated.
2. **Scrape / import / request / license?** **Import** the monthly sale list;
   **request (TPIA)** the full roll. Scraping is the last resort and should be
   avoided for the full roll.
3. **Can we automate it?** The sale-list ingest — partially (format is
   presentation-oriented). The full roll — no; it is a scheduled manual request.
4. **Would a professional data company build against this source?** Yes for the
   sale list; a serious operation would establish a TPIA/data-purchase channel
   rather than scrape the full roll.
5. **Would I personally certify this for nightly production?** **Not as a nightly
   full-roll feed.** I would certify: (a) monthly sale-list ingest on the
   first-Tuesday cadence, and (b) a recurring TPIA snapshot for the full roll.
   A nightly cadence over-promises against a source that updates monthly.

## Remaining Unknowns (UNVERIFIED — Further confirmation required)

- Exact sale-list format/URL stability and whether a machine-readable (CSV) form exists.
- Whether the Tax Office / County Attorney will provide a bulk delinquent-roll extract, its format, cadence, and cost.
- Exact field schema of the sale list and the County Attorney search.
- Which delinquent-tax law firm holds which roll and whether any expose a feed.

## Sources

- [Harris County Tax Office — Tax Sales](https://www.hctax.net/Property/TaxSales)
- [Harris County Tax Sales Lists](https://www.hctax.net/Property/listings/taxsalelisting)
- [Tax Sale FAQs (PDF)](https://www.hctax.net/About/Announcements/Tax%20Sale%20FAQs.pdf)
- [Delinquent / Abandoned Property](https://www.hctax.net/Property/AbandonedProperty.cshtml)
- [Search Delinquent Accounts — Harris County Attorney](https://caopay.harriscountytx.gov/)
