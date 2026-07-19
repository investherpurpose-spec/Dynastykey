# Harris County — Probate (Dataset #4)

> **Evidence discipline.** Direct reads of `cclerk.hctx.net` /
> `probate.harriscountytx.gov` were **not possible** from this environment
> (network policy denial / HTTP 403). Owner and method existence are
> corroborated via official search-result summaries; specifics are marked
> **UNVERIFIED — Further confirmation required.**

---

## 1. Official Data Owner

**Harris County Clerk's Office** is the clerk of the **Harris County Probate
Courts** (four statutory probate courts) and is the authoritative owner of
probate case records (estate administrations, wills, guardianships).

- Probate court case search: <https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate> (VERIFIED URL via search results).
- Probate Courts: <https://probate.harriscountytx.gov/> (court/administrative site).

Probate matters to Dynasty Key because an open estate frequently signals an
inherited/soon-to-transfer property (a distress/opportunity signal) tied to a
decedent who is (or was) an owner of record in HCAD.

## 2. Official Acquisition Methods

| Method | Available? | Evidence |
|---|---|---|
| **Search Portal** (probate case search) | **Yes** | County Clerk CourtSearch (CaseType=Probate). VERIFIED. |
| **Licensed Government Data Feed / Data Sales** | **Yes** | The County Clerk Historical Records / data-sales channel explicitly includes **county court and probate court records**; index data is pipe-delimited `.txt`, images TIFF, FTP daily purchased monthly (**datasales@cco.hctx.net / 713-274-6390**). VERIFIED via search results. |
| Bulk Download (free) | No | No free bulk probate export advertised. |
| Open Data Portal / API | Not found | No Socrata/REST probate feed advertised. |
| TPIA / Open Records | Yes (fallback) | Probate records are public; obtainable by request. |

**Finding:** The **same County Clerk licensed data feed** identified for
foreclosure (`foreclosure.md`) explicitly covers **probate court records**. One
data-sales/FTP agreement therefore covers **both** foreclosure and probate — a
strong argument for the feed-import architecture over two separate scrapers.

## 3. Data Characteristics

| Attribute | Value | Basis |
|---|---|---|
| Update frequency | Cases filed continuously; feed delivers daily data | VERIFIED (feed daily) / court filing cadence continuous |
| Record volume | Thousands of probate filings/year | **UNVERIFIED** exact count |
| Format (feed) | Pipe-delimited `.txt` index + TIFF images | VERIFIED via search results |
| Format (portal) | HTML case search results / document images | VERIFIED (nature) |
| Key fields | cause/case number, filing date, case type, decedent name, applicant/executor, attorney | Partially VERIFIED / exact schema **UNVERIFIED** |
| Authentication | None for portal; purchase for feed | VERIFIED (feed paid) |
| Cost | Portal free; feed paid | VERIFIED (nature) / price **UNVERIFIED** |
| Licensing | Public record under data-sales agreement | VERIFIED (nature) / terms **UNVERIFIED** |
| Geographic coverage | Harris County probate courts | VERIFIED |

## 4. Dynasty Key Required Fields

| Required field | Present? | Note |
|---|---|---|
| Parcel / Account Number | **No** | Probate case has no parcel; must link decedent → HCAD owner by name/address |
| Property Address | **No** | Not in the case record; derive via decedent→HCAD match |
| Mailing Address | Partial | Applicant/attorney address may appear |
| Owner Name | **Yes** (proxy) | Decedent name = prior owner-of-record candidate |
| Property Type | **No** | Via HCAD after match |
| Land Use | **No** | Via HCAD after match |
| **Distress Date** | **Yes** | Probate filing date |
| Amount Due | **No** | N/A (estate value not a structured lien field) |
| Sale Date | **No** | N/A unless an estate sale is ordered |
| **Case Number** | **Yes** | Probate cause number |
| Status | **Yes** (proxy) | Pending / administration / closed |
| Source Updated Date | **Yes** (feed) | Feed/filing timestamp |

**Missing (critical):** probate has **no property linkage** — the entire value
depends on **matching the decedent to an HCAD owner-of-record** (name + last
known address). This is the hardest match in the whole registry (name-only,
deceased owner, possible heirs/trusts) and is exactly where the project's
`scraper/claude_match.py` (Claude-assisted name matching) is justified.

## 5. Reliability Assessment

| Dimension | Grade (feed) | Grade (portal) | Rationale |
|---|---|---|---|
| Official authority | A+ | A+ | County Clerk is the statutory probate clerk. |
| Completeness | A | B | Feed = all filings; portal search may window. |
| Freshness | A | A | Daily feed; filings recorded promptly. |
| Stability | A | C | Feed schema stable; portal markup not. |
| Ease of automation | B | D | Feed is clean, but decedent→parcel match is hard for both. |
| Bulk availability | A | D | Feed is bulk; portal per-search. |
| Long-term maintainability | A | D | Feed durable; scraper brittle. |

**Overall grade: B (via feed)** / **C– (via portal).** Downgraded from the
foreclosure feed's A mainly because of the **weak/absent property linkage** — the
data itself is authoritative and clean, but converting a probate case into a
parcel-level opportunity is intrinsically lower-confidence.

## 6. Production Recommendation

- **Preferred:** the **County Clerk licensed data feed** (same agreement as
  foreclosure), ingesting the probate index, then **matching decedents to HCAD
  owners** with the local + Claude-assisted matcher already in this repo.
- **Secondary / Fallback:** the **CourtSearch probate portal** filtered by recent
  filing dates for the actionable recent cohort.
- **Treat probate as a lower-confidence signal:** require a successful,
  reviewed decedent→parcel match before promoting a probate case to an
  actionable opportunity.

**Architectural note:** Same recommendation as foreclosure — **use the feed
importer**, not a per-search scraper. Because one feed covers both foreclosure
and probate, the feed-import architecture pays for itself across two datasets.
The genuinely new engineering here is the **decedent→owner identity-resolution**
step; certify that separately with a human-review threshold before probate leads
go live.

## 7. Validation Strategy

- **Expected record count:** probate filings per week/month within a plausible
  band; alert on zero.
- **Schema validation:** require cause number, filing date, case type, and
  decedent name on every row.
- **Required-field checks:** filing date parses; case type ∈ known probate types.
- **Match-quality gate:** decedent→HCAD match must clear a confidence threshold;
  log low-confidence matches for human review rather than auto-promoting.
- **Duplicate threshold:** dedupe on cause number; multiple filings per estate
  are expected.
- **Freshness check:** daily feed arrival; alert on gaps.
- **Cross-validation:** sample matched cases against HCAD owner-of-record to
  confirm the decedent was actually the owner.

## Final Deliverable Questions

1. **Can we acquire this reliably?** **Yes** — via the County Clerk feed (same as
   foreclosure). The *case data* is reliable; the *property linkage* is the risk.
2. **Scrape / import / request / license?** **License + import** the feed; scrape
   the portal only as fallback.
3. **Can we automate it?** Ingest — yes. The decedent→parcel match — partially
   automatable, but needs a human-review confidence gate.
4. **Would a professional data company build against this source?** Yes; probate
   "heir/estate" leads are a known product built on exactly this clerk feed.
5. **Would I personally certify this for nightly production?** **Yes for
   ingestion; conditionally for lead promotion.** Certify feed ingestion once the
   agreement/schema are confirmed. Do **not** auto-promote probate leads until the
   decedent→owner matcher is validated against a labeled sample and a confidence
   threshold is set — otherwise false-positive owner links would ship.

## Remaining Unknowns (UNVERIFIED — Further confirmation required)

- Exact probate index schema in the feed (fields available for matching).
- Feed price/terms (shared with foreclosure — one agreement).
- Achievable decedent→HCAD match precision/recall.
- Whether the portal exposes enough to serve as a viable fallback at scale.

## Sources

- [Probate Court Case Search — Harris County Clerk](https://www.cclerk.hctx.net/applications/websearch/CourtSearch.aspx?CaseType=Probate)
- [Harris County Probate Courts](https://probate.harriscountytx.gov/)
- [Public Records — Harris County Clerk](https://www.cclerk.hctx.net/PublicRecords.aspx)
- County Clerk Data Sales (probate + county court records included): datasales@cco.hctx.net / 713-274-6390.
