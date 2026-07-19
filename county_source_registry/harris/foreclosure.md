# Harris County — Foreclosure Notices and Sales (Dataset #3)

> **Evidence discipline.** Direct reads of `cclerk.hctx.net` were **not
> possible** from this environment (network policy denial / HTTP 403). Owner and
> method existence are corroborated via official search-result summaries;
> specifics (record counts, exact fields, feed cost/cadence) are marked
> **UNVERIFIED — Further confirmation required.**

---

## 1. Official Data Owner

**Harris County Clerk's Office** — the statutory recorder of real-property
instruments, including **Notices of Trustee's Sale / Substitute Trustee's Sale**
(non-judicial foreclosure postings).

- Foreclosure search: <https://www.cclerk.hctx.net/Applications/WebSearch/FRCL_R.aspx> (VERIFIED URL via search results).
- Public records portal: <https://www.cclerk.hctx.net/PublicRecords.aspx>.
- Foreclosure Listing Service phone line: **(281) 363-2631** (VERIFIED via search results).

Under Texas law a Notice of (Substitute) Trustee's Sale **must be filed with the
county clerk and posted at least 21 calendar days before the sale** (first
Tuesday of the month). Sales are held at the Bayou City Event Center, 9401 Knight
Road, Houston. (VERIFIED via search results / Texas State Law Library guide.)

## 2. Official Acquisition Methods

| Method | Available? | Evidence |
|---|---|---|
| **Search Portal** (notices of sale) | **Yes** | County Clerk foreclosure search by sale date (FRCL_R.aspx). VERIFIED. |
| **Licensed Government Data Feed / Data Sales** | **Yes** | County Clerk sells index data (pipe-delimited `.txt`) and image data (TIFF) by custom date range on CD/DVD/hard drive; **daily data over FTP, purchased monthly**. Contact **datasales@cco.hctx.net / 713-274-6390**. VERIFIED via search results. |
| Foreclosure Listing Service | Yes | Phone/subscription list (281) 363-2631. VERIFIED existence. |
| Bulk Download (free) | No | No free countywide bulk download; bulk is the paid data-sales channel. |
| Open Data Portal / API | Not found | No Socrata/REST feed advertised. |
| TPIA / Open Records | Yes (fallback) | Recorded instruments are public record; obtainable by request. |

**Pivotal finding (architectural):** The County Clerk operates an **official
licensed bulk/FTP data feed** — index data as **pipe-delimited text**, refreshed
**daily and purchased monthly over FTP**. This is a *production-grade,
authoritative, machine-readable* channel that covers foreclosure notices (and
probate — see `probate.md`). It is materially superior to scraping the
per-search web portal.

## 3. Data Characteristics

| Attribute | Value | Basis |
|---|---|---|
| Update frequency | Notices filed continuously; each posted ≥21 days pre-sale; feed delivers **daily** data | VERIFIED (21-day rule + daily feed) |
| Record volume | Monthly first-Tuesday sale cohort = hundreds–low thousands; annual notices much larger | **UNVERIFIED** exact counts |
| Format (feed) | **Pipe-delimited `.txt`** index + TIFF images | VERIFIED via search results |
| Format (portal) | HTML search results / document images | VERIFIED (nature) |
| Key fields | document/film code, recording date, sale date, grantor/grantee (borrower/trustee), property description, instrument type | Partially VERIFIED / exact schema **UNVERIFIED** |
| Authentication | None for portal; **contract/purchase** for the feed | VERIFIED (feed is paid) |
| Cost | Portal free; **feed is paid** (index and image priced separately; FTP monthly) | VERIFIED that it is paid / exact price **UNVERIFIED** |
| Licensing | Public record distributed under a data-sales agreement | VERIFIED (nature) / terms **UNVERIFIED** |
| Geographic coverage | All of Harris County | VERIFIED |

## 4. Dynasty Key Required Fields

| Required field | Present? | Note |
|---|---|---|
| Parcel / Account Number | **Partial / No** | Clerk records key on legal description, not HCAD account; must geocode/match to HCAD |
| Property Address | Partial | Legal description present; street address may need derivation |
| Mailing Address | Partial | Borrower address may appear; join to HCAD for canonical |
| Owner Name | **Yes** | Grantor/borrower (mortgagor) on the notice |
| Property Type | **No** | Join to HCAD |
| Land Use | **No** | Join to HCAD |
| **Distress Date** | **Yes** | Notice filing/posting date |
| Amount Due | Partial | Debt amount may appear in the notice text; **UNVERIFIED** as a structured field |
| **Sale Date** | **Yes** | Scheduled first-Tuesday sale date |
| **Case Number** | **Yes** (proxy) | Clerk's file/film code; cause number if judicial |
| Status | **Yes** (proxy) | Notice filed / postponed / sold |
| Source Updated Date | **Yes** (feed) | Daily feed timestamp / recording date |

**Missing:** reliable **HCAD account / parcel linkage** and structured Property
Type / Land Use — these require **matching to HCAD** (Dataset #1) via legal
description or address geocoding. This match step is the main engineering cost of
this dataset.

## 5. Reliability Assessment

| Dimension | Grade (feed) | Grade (portal) | Rationale |
|---|---|---|---|
| Official authority | A+ | A+ | County Clerk is the statutory recorder. |
| Completeness | A | B | Feed = all recorded notices; portal search may window results. |
| Freshness | A | A | 21-day statutory lead + daily feed. |
| Stability | A | C | Contracted feed schema is stable; web portal markup is not. |
| Ease of automation | A | D | Pipe-delimited FTP files vs. brittle ASPX scraping. |
| Bulk availability | A | D | Feed is bulk by design; portal is per-search. |
| Long-term maintainability | A | D | Feed contract is durable; scrapers rot. |

**Overall grade: A (via the licensed feed)** / **C (via portal scraping).** The
*source* is A+; the grade depends entirely on which acquisition method is chosen.

## 6. Production Recommendation

- **Preferred:** **License the Harris County Clerk data feed** (index data,
  pipe-delimited `.txt`, delivered daily over FTP, purchased monthly). Ingest as
  a bulk importer; match to HCAD for parcel/address/type enrichment.
- **Secondary / Fallback:** the County Clerk **foreclosure search portal**
  (FRCL_R.aspx) filtered by upcoming first-Tuesday sale date, for the actionable
  monthly cohort if the feed is not yet contracted.
- **Do NOT** build the long-term pipeline on portal scraping — it is the fallback,
  not the production source.

**Architectural recommendation (STOP-AND-RECOMMEND per CTO rule):** A
production-quality foreclosure source (the licensed pipe-delimited FTP feed)
requires a **bulk-import / feed-ingestion architecture**, not a per-search
scraper. **Before building or expanding a foreclosure scraper, adopt the feed
importer.** Concretely: procure the data-sales/FTP agreement, stand up a
scheduled fetcher for the daily pipe-delimited index files, and reuse the
HCAD-matching logic to resolve parcels. Do not force the existing per-search
scraper architecture onto this superior source. (This same feed also delivers
Probate — see `probate.md` — so one feed contract covers two datasets.)

## 7. Validation Strategy

- **Expected record count:** each first-Tuesday cohort within a plausible band;
  alert on 0 notices in a pre-sale window.
- **Schema validation:** require instrument type = trustee/substitute-trustee
  sale, a sale date, and a recording/posting date on every row.
- **Required-field checks:** sale date parses to a first Tuesday; posting date ≥
  21 days before sale date (statutory invariant — flag violations).
- **Duplicate threshold:** postponed/re-posted sales legitimately repeat; dedupe
  on file code + sale date, flag excess duplication.
- **Freshness check:** daily feed file must arrive each business day; alert on a
  gap.
- **Cross-validation:** match rate to HCAD parcels should meet a target
  threshold; low match rate signals a parsing/geocoding regression.

## Final Deliverable Questions

1. **Can we acquire this reliably?** **Yes** — via the licensed County Clerk feed
   (authoritative, daily, machine-readable).
2. **Scrape / import / request / license?** **License + import** the feed.
   Scrape the portal only as a fallback.
3. **Can we automate it?** Yes — the feed is built for automated ingestion.
4. **Would a professional data company build against this source?** Yes — the
   licensed clerk feed is exactly what professional real-estate-data companies use.
5. **Would I personally certify this for nightly production?** **Yes — but only
   via the feed, not portal scraping.** Certification is conditional on procuring
   the data-sales/FTP agreement and confirming the index schema and price. Via
   scraping alone I would **not** certify it as durable.

## Remaining Unknowns (UNVERIFIED — Further confirmation required)

- Exact price, contract terms, and licensing of the County Clerk index feed.
- Exact index schema (which columns; whether debt amount and street address are structured).
- Whether the feed includes a stable key that eases HCAD parcel matching.
- Portal search result caps / rate limits if used as fallback.

## Sources

- [Foreclosures — Harris County Clerk](https://www.cclerk.hctx.net/Applications/WebSearch/FRCL_R.aspx)
- [Public Records — Harris County Clerk](https://www.cclerk.hctx.net/PublicRecords.aspx)
- [Real Property Records — Harris County Clerk](https://www.cclerk.hctx.net/RealProperty.aspx)
- [Before the Sale — Foreclosure (Texas State Law Library)](https://guides.sll.texas.gov/foreclosure/before-the-sale)
- County Clerk Data Sales: datasales@cco.hctx.net / 713-274-6390; Foreclosure Listing Service (281) 363-2631.
