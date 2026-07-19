# Harris County — Data Acquisition Decision Matrix

At-a-glance acquisition decisions for the 7 core datasets. Full evidence,
grades, and validation strategies live in the per-dataset files; this matrix is
the one-page executive view. Specifics behind each "Why" remain subject to the
`UNVERIFIED` items noted in each dataset file (direct `.gov` reads were blocked
in the certification environment).

## Decision Matrix

| Dataset     | Best Method | Backup      | Why                                             |
| ----------- | ----------- | ----------- | ----------------------------------------------- |
| Property    | HCAD Bulk   | ArcGIS      | Official appraisal-district bulk; authoritative |
| Probate     | Clerk Feed  | Portal      | Feed more reliable & machine-readable           |
| Foreclosure | Clerk Feed  | Portal      | Feed more complete than per-search portal       |
| Tax         | TPIA Export | Portal      | No bulk roll — await TPIA confirmation           |
| 311         | Open Data   | API         | Good enrichment signal (not a primary trigger)  |
| Code        | Open Data   | TPIA        | Better property signals; Houston-only coverage  |
| GIS         | ArcGIS REST | Shapefile   | Sanctioned bulk API; parcel/geometry spine      |

## Reading the matrix

- **Best Method** = the certified preferred production source.
- **Backup** = documented fallback if the preferred source is unavailable.
- **Import** datasets: Property, GIS, 311, Code (Houston). **License**: Probate,
  Foreclosure (one Clerk-feed contract covers both). **Request (recurring TPIA)**:
  Tax full roll, unincorporated-county Code.
- **Scraping is no dataset's primary method** — portal scraping (Probate,
  Foreclosure, Tax) is a temporary fallback only.

## Method quick-reference

| Method label | What it means |
|---|---|
| HCAD Bulk | HCAD PDATA `real_acct` bulk text download (`scraper/hcad_bulk.py`) |
| ArcGIS / ArcGIS REST | Harris County ArcGIS parcels REST layer (`scraper/arcgis.py`) |
| Clerk Feed | Harris County Clerk licensed pipe-delimited FTP data feed (datasales@cco.hctx.net / 713-274-6390) |
| Portal | Per-search web portals (cclerk.hctx.net, hctax.net) — fallback only |
| TPIA / TPIA Export | Recurring Texas Public Information Act request |
| Open Data | City of Houston open data portal (data.houstontx.gov) |
| API | Socrata API on the City open data portal (UNVERIFIED endpoint) |
| Shapefile | HCAD GIS shapefile bulk download |

## Architectural takeaway

The two **Clerk Feed** rows (Probate + Foreclosure) are the one place the current
architecture should change: adopt a **licensed-feed importer** rather than
deepening portal scraping — a single contract covers both datasets. Property and
GIS already use the correct bulk/REST approach and their collectors should be
kept. See `SUMMARY.md` §5 for the full architectural recommendation.
