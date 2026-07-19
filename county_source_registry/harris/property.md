# Harris County — Property Ownership (Dataset #1)

> **Evidence discipline.** Every claim below is tagged with its evidence
> basis. Items that could not be confirmed from an authoritative source in
> this environment are marked **UNVERIFIED — Further confirmation required.**
> Direct-page reads of `hcad.org` were **not possible** from this environment
> (outbound HTTPS to the host was denied by network policy / returned HTTP 403),
> so specifics such as exact field lists, record counts, file sizes, and refresh
> timestamps are corroborated only through search-result summaries and the
> project's own collector code, not through direct inspection of the source.

---

## 1. Official Data Owner

**Harris Central Appraisal District (HCAD)** — the appraisal district
responsible for appraising all real and personal property in Harris County for
ad valorem tax purposes. HCAD is the authoritative owner of the parcel/account
ownership record.

- Portal: <https://hcad.org/> — *"Harris Central Appraisal District"* (VERIFIED via search results; note the district now brands as **Harris Central** Appraisal District).
- Public Data portal (PDATA): <https://hcad.org/hcad-online-services/pdata/> and <https://hcad.org/pdata/pdata-property-downloads.html> (VERIFIED URLs via search results).
- Codebook: <https://hcad.org/assets/uploads/pdf/pdataCodebook.pdf> (VERIFIED URL via search results).

Note on authority boundary: HCAD owns the **appraisal / ownership** record.
It does **not** own the tax-collection or delinquency record (that is the Harris
County Tax Assessor-Collector — see `tax_delinquent.md`). Ownership as recorded
by deed transfer is owned by the **Harris County Clerk** (see `foreclosure.md`);
HCAD's owner-of-record is derived from those filings and lags them.

## 2. Official Acquisition Methods

| Method | Available? | Evidence |
|---|---|---|
| **Bulk Download** (primary) | **Yes** | HCAD PDATA publishes raw tab-delimited text files (Real & Personal Property Database) for import into user databases. VERIFIED via search results. |
| GIS Download (parcels/shapefiles) | Yes | Covered in `gis.md`. |
| Official API (structured/paged) | Partial | ArcGIS REST parcels layer exists (see `gis.md`); HCAD does not advertise a REST API for the ownership *text* files. |
| Open Data Portal (Socrata/ArcGIS Hub) | No dedicated Socrata | HCAD distributes files via PDATA, not a Socrata portal. |
| Texas Public Information Act (TPIA) / Open Records | Yes (fallback) | HCAD states custom/specific extracts can be requested via an open records request with a cost estimate returned for approval. VERIFIED via search results. |
| Search Portal (per-record) | Yes | Parcel Viewer / property search at <https://arcweb.hcad.org/parcel-viewer-v2.0/> and property search on hcad.org (not a bulk method). |

**What Dynasty Key currently targets (direct code evidence):**
`scraper/hcad_bulk.py` downloads
`https://download.hcad.org/data/CAMA/{year}/Real_acct_owner.zip`, extracts
`real_acct.txt` (tab-delimited, `cp1252`), and loads it as the `owners` table
keyed on `acct`. This is the account-number → owner-name + mailing-address +
site-address mapping. **The chosen acquisition method is already Bulk Download —
the correct, authoritative method.** (The exact `download.hcad.org/data/CAMA/...`
URL pattern is **UNVERIFIED** against the live server from this environment; the
collector's own README notes it may 404 and instructs falling back to the PDATA
downloads page.)

## 3. Data Characteristics

| Attribute | Value | Basis |
|---|---|---|
| Update frequency | Appraisal roll is annual; PDATA files refreshed on HCAD's publication schedule through the appraisal year | **UNVERIFIED** exact cadence |
| Record volume | ~1.5 million real-property accounts (order of magnitude; the collector README cites "~1.5M parcels") | **UNVERIFIED** exact count |
| Format | Tab-delimited text (`.txt`) inside a `.zip`; `cp1252` encoding | VERIFIED (code) + search results |
| Key fields present | account number (`acct`), owner/mail name (`mailto`), mailing address, site/situs address, legal description, appraised/market values | Partially VERIFIED (README + code); exact column set **UNVERIFIED** against codebook |
| Authentication | None (public download) | VERIFIED via search results ("available to users") |
| Cost | Free for the standard PDATA files; custom open-records extracts carry a cost estimate | VERIFIED (free tier) / cost of custom **UNVERIFIED** |
| Licensing / terms | Public record; HCAD provides raw data "as is," offers no technical assistance | VERIFIED via search results; exact reuse license text **UNVERIFIED** |
| Geographic coverage | All of Harris County | VERIFIED (appraisal district mandate) |

## 4. Dynasty Key Required Fields

| Required field | Present? | Source field / note |
|---|---|---|
| Parcel / Account Number | **Yes** | `acct` (HCAD account) — joins to ArcGIS `HCAD_NUM` |
| Property Address | **Yes** | site/situs address (`site_addr_*`) |
| Mailing Address | **Yes** | owner mailing address (`mail_addr_*`) |
| Owner Name | **Yes** | `mailto` |
| Property Type | Likely (state class / property use codes in real_acct) | **UNVERIFIED** exact field |
| Land Use | Likely (state category code) | **UNVERIFIED** exact field |
| Distress Date | **No** | Not an ownership-file concept — comes from tax/foreclosure/probate sources |
| Amount Due | **No** | Not in appraisal data — comes from Tax Assessor-Collector |
| Sale Date | **No** | Deed/sale dates come from County Clerk records |
| Case Number | **No** | N/A for appraisal ownership |
| Status | Partial (account status codes may exist) | **UNVERIFIED** |
| Source Updated Date | **No explicit per-record field** | Must be derived from the file/roll year; **UNVERIFIED** |

**Missing / not-owned by this source:** Distress Date, Amount Due, Sale Date,
Case Number, and a reliable per-record Source Updated Date. These are expected
to be missing — Property Ownership is the *identity/anchor* dataset, not a
distress-signal dataset. It supplies the parcel, owner, and address spine that
the distress datasets (tax, foreclosure, probate) attach to.

## 5. Reliability Assessment

| Dimension | Grade | Rationale |
|---|---|---|
| Official authority | A+ | HCAD is the statutory appraisal district; no more authoritative owner exists. |
| Completeness (of ownership) | A | Countywide, one row per account, owner + both addresses. |
| Freshness | B | Ownership lags deed recording; roll is annual with in-year updates (exact cadence UNVERIFIED). |
| Stability of source | A | Long-standing PDATA program; format stable across years. |
| Ease of automation | A | Single bulk file, deterministic parse, resumable load already implemented. |
| Bulk availability | A+ | First-class bulk download is the *intended* distribution method. |
| Long-term maintainability | A | Yearly URL pattern; low surface area; no per-record scraping. |

**Overall grade: A** (A+ on authority and bulk availability; held from A+ overall
only by freshness lag and the UNVERIFIED live URL/cadence).

## 6. Production Recommendation

- **Preferred:** HCAD PDATA **bulk download** of the Real Account / Owner file
  (`real_acct.txt` + owner data), loaded on HCAD's publication cadence. This is
  the method Dynasty Key already uses and it is the authoritative one.
- **Secondary / Fallback:** HCAD **open-records (TPIA) request** for a custom
  extract if a needed field is absent from the standard PDATA files, or the
  ArcGIS parcels layer (see `gis.md`) for the account+address spine if the bulk
  text download is temporarily unavailable.
- **Do NOT** rely on the per-parcel search portal for bulk ownership — it is a
  human lookup tool, not a production feed.

**Architectural note:** No architectural change is required for Property
Ownership. The current bulk-import approach is correct and should be **kept**.
The only hardening needed is to pin/confirm the live download URL and codebook
column mapping (see Remaining Unknowns).

## 7. Validation Strategy

- **Expected record count:** on the order of ~1.5M real-property accounts;
  alert if a load comes in <1.2M or >1.8M (bounds are heuristic until the true
  count is VERIFIED).
- **Schema validation:** assert presence of `acct`, owner name, mailing-address
  and site-address columns against the PDATA codebook before load; fail the run
  on missing required columns rather than silently loading.
- **Required-field non-null:** `acct` must be present and unique-ish; owner name
  present for the large majority of rows.
- **Duplicate threshold:** `acct` should be effectively unique per roll year;
  flag if duplicate `acct` rate exceeds a small tolerance.
- **Freshness check:** compare the file/roll year and download timestamp against
  expected; alert if the published file has not advanced past the prior load
  when a new roll is expected.
- **Random sample verification:** spot-check N accounts against the HCAD parcel
  viewer to confirm owner/address agreement.
- **Cross-validation:** join `acct` → ArcGIS parcels `HCAD_NUM`; the join rate
  should be very high. A low join rate indicates a schema/version drift on
  either side.

## Final Deliverable Questions

1. **Can we acquire this reliably?** Yes — via an authoritative first-class bulk
   download from HCAD.
2. **Scrape / import / request / license?** **Import** (bulk download). Request
   (TPIA) only for custom fields.
3. **Can we automate it?** Yes — already automated (`scraper/hcad_bulk.py`),
   deterministic and resumable.
4. **Would a professional data company build against this source?** Yes — HCAD
   PDATA is the standard authoritative source used across the industry.
5. **Would I personally certify this for nightly production?** **Yes, with one
   qualification.** The source and method are sound and certifiable. Ownership
   does not actually *change* nightly, so a nightly re-download is wasteful;
   certify it on HCAD's publication cadence (verify cadence — UNVERIFIED). Before
   final sign-off, confirm the live `download.hcad.org` URL and map columns to
   the PDATA codebook so schema validation is exact rather than heuristic.

## Remaining Unknowns (UNVERIFIED — Further confirmation required)

- Exact live bulk URL and whether `download.hcad.org/data/CAMA/{year}/Real_acct_owner.zip` is current.
- Exact column list / codebook mapping (Property Type, Land Use, Status fields).
- Exact PDATA refresh cadence and whether a per-record "last updated" exists.
- True current record count and file size.
- Precise reuse/licensing terms for the PDATA files.

## Sources

- [PDATA — Harris Central Appraisal District](https://hcad.org/hcad-online-services/pdata/)
- [Download Property Data](https://hcad.org/pdata/pdata-property-downloads.html)
- [PDATA Codebook (PDF)](https://hcad.org/assets/uploads/pdf/pdataCodebook.pdf)
- [Public Data Help](https://hcad.org/hcad-online-services/pdata/pdata-help)
- [HCAD Parcel Viewer v2.1](https://arcweb.hcad.org/parcel-viewer-v2.0/)
- Direct code evidence: `scraper/hcad_bulk.py`, `README.md` (this repo).
