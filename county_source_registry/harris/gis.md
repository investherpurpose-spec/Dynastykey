# Harris County — GIS / Parcel Data (Dataset #7)

> **Evidence discipline.** Direct reads of `gis.hctx.net` and `hcad.org` were
> **not possible** from this environment — outbound HTTPS to `www.gis.hctx.net`
> was **denied by network policy (HTTP 403 at the proxy CONNECT stage)**, and
> `hcad.org` returned 403 to WebFetch. Live layer metadata (field list, record
> count, pagination flag) therefore **could not be inspected** and is marked
> **UNVERIFIED — Further confirmation required.** The *endpoints Dynasty Key
> targets* are, however, **direct code evidence** from this repo's collector.

---

## 1. Official Data Owner

Two authoritative GIS owners:

- **Harris Central Appraisal District (HCAD)** — authoritative **parcel geometry
  + HCAD account** (the cadastral fabric). Published via HCAD GIS downloads and
  the county-hosted ArcGIS services. (VERIFIED owner; download page cadence UNVERIFIED.)
- **Harris County (Universal Services / GIS)** — county base-map/ArcGIS
  infrastructure (`gis.hctx.net`) and the (being-decommissioned) Harris County
  Open Data ArcGIS Hub. (VERIFIED that county GIS infra exists.)

## 2. Official Acquisition Methods

| Method | Available? | Evidence |
|---|---|---|
| **ArcGIS REST API** (parcels, paged query) | **Yes** | The collector queries `https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0` (MapServer fallback `.../arcgis/rest/services/HCAD/Parcels/MapServer/0`). Direct **code** evidence (`scraper/arcgis.py`). Live reachability from this environment **UNVERIFIED** (blocked). |
| **GIS Bulk Download** (shapefiles) | Yes | HCAD publishes parcel GIS downloads (shapefiles) via PDATA GIS downloads page. VERIFIED existence / URL & quarterly cadence **UNVERIFIED**. |
| Open Data Portal (ArcGIS Hub) | Partial / deprecating | Harris County Open Data ArcGIS Hub historically offered CSV/KML/GeoJSON/GeoTIFF + GeoServices/WMS/WFS; reported to be **decommissioning** (contact gisinfo@hctx.net). **UNVERIFIED** current status. |
| TPIA / Open Records | Yes (fallback) | GIS data is public record. |

**What Dynasty Key currently targets (direct code evidence):**
`scraper/arcgis.py` implements a robust ArcGIS REST client — it reads layer
metadata (`maxRecordCount`, `advancedQueryCapabilities.supportsPagination`,
`objectIdField`), pages via `resultOffset` when supported and falls back to
objectId-chunk paging, requests `outSR=4326`, and is throttled/retried/resumable.
This is a **correct, production-appropriate** ArcGIS extraction design.

## 3. Data Characteristics

| Attribute | Value | Basis |
|---|---|---|
| Update frequency | Parcel fabric updated on HCAD/county GIS cadence (shapefiles historically ~quarterly) | **UNVERIFIED** exact cadence |
| Record volume | ~1.5M parcels (order of magnitude; README) | **UNVERIFIED** exact count |
| Format | ArcGIS REST JSON/GeoJSON (query); Shapefile (bulk); Hub CSV/KML/GeoJSON/GeoTIFF | Partially VERIFIED (code + Hub history) |
| Key fields | HCAD account (`HCAD_NUM` / `LOWPARCELID`), site address, geometry (polygon/centroid) | Partially VERIFIED (README/code) / exact field list **UNVERIFIED** (layer metadata blocked) |
| Pagination | `supportsPagination` honored with objectId fallback | VERIFIED (code) / live flag value **UNVERIFIED** |
| Authentication | None (public REST) | VERIFIED (nature) |
| Cost | Free | VERIFIED (nature) |
| Licensing | Public record; county/HCAD GIS terms | **UNVERIFIED** exact terms |
| Geographic coverage | All of Harris County | VERIFIED |

## 4. Dynasty Key Required Fields

| Required field | Present? | Note |
|---|---|---|
| Parcel / Account Number | **Yes** | `HCAD_NUM` / `LOWPARCELID` — the join key to HCAD ownership |
| Property Address | **Yes** | Site address on the parcels layer |
| Mailing Address | **No** | Not on parcels; join to HCAD owner file (Dataset #1) |
| Owner Name | **No** (on GIS) | Join to HCAD owner file |
| Property Type | Partial | State-class/land-use codes may be on the layer; **UNVERIFIED** |
| Land Use | Partial | **UNVERIFIED** exact field |
| Distress Date | **No** | N/A (GIS is geometry/identity) |
| Amount Due | **No** | N/A |
| Sale Date | **No** | N/A |
| Case Number | **No** | N/A |
| Status | Partial | Parcel/account status codes may exist; **UNVERIFIED** |
| Source Updated Date | Partial | Layer edit date may exist; **UNVERIFIED** |
| **Geometry / coordinates** | **Yes** | Polygon + centroid (`outSR=4326`) — the geocoding backbone |

**Role:** GIS is the **spatial spine + account/address anchor**. It carries no
distress signal by itself; its value is the parcel geometry and the `HCAD_NUM`
key that lets every other dataset be resolved to a mappable parcel.

## 5. Reliability Assessment

| Dimension | Grade | Rationale |
|---|---|---|
| Official authority | A+ | HCAD/county are the authoritative cadastral sources. |
| Completeness | A | Countywide parcels + geometry + account key. |
| Freshness | B | Quarterly-ish fabric updates (cadence UNVERIFIED). |
| Stability | B | ArcGIS endpoints "move over time" (collector README warns of this and provides `discover` + fallback). |
| Ease of automation | A | Standard ArcGIS REST paging; already implemented cleanly. |
| Bulk availability | A | REST query + shapefile bulk both available. |
| Long-term maintainability | B+ | Endpoint drift is the main risk; mitigated by discovery + shapefile fallback. |

**Overall grade: A–** (authoritative, complete, automatable; held from A by
endpoint-drift risk and the fact that live metadata could not be verified here).

## 6. Production Recommendation

- **Preferred (geometry + key at scale):** **ArcGIS REST bulk query** of the HCAD
  Parcels layer (as implemented) with `outSR=4326`, pagination, and resumability;
  refresh on the fabric's update cadence.
- **Secondary / Fallback:** **HCAD GIS shapefile bulk download** (PDATA GIS) if
  the REST endpoint moves or rate-limits; and the `discover` step to relocate
  endpoints when they change.
- **Attribute enrichment:** always join parcels `HCAD_NUM` → HCAD owner file
  (Dataset #1) for owner/mailing/type rather than relying on GIS attributes alone.
- **Do NOT** depend on the Harris County Open Data ArcGIS Hub as a primary
  channel — it is reportedly being decommissioned.

**Architectural note:** No architectural change needed — the ArcGIS REST
bulk-extraction design already in the repo is the right approach for GIS. Two
hardening items: (1) add the shapefile-download fallback explicitly to survive
endpoint drift, and (2) verify live layer metadata (fields, count, pagination)
once network access to `gis.hctx.net` is available, since it could not be
confirmed here.

## 7. Validation Strategy

- **Expected record count:** ~1.5M parcels; alert on a large deviation (bounds
  heuristic until VERIFIED).
- **Schema validation:** require `HCAD_NUM`/`LOWPARCELID`, site address, and a
  valid geometry; fail on missing account key.
- **Required-field checks:** account key present and mostly unique; geometry
  parses and centroid falls within Harris County bounds.
- **Duplicate threshold:** account key effectively unique per parcel; flag
  excess duplication.
- **Freshness check:** compare layer/download edit date against expected cadence.
- **Cross-validation:** parcels `HCAD_NUM` ↔ HCAD owner `acct` join rate should
  be very high; a drop indicates one side drifted.
- **Geospatial sanity:** coordinates within Harris County bounding box; reject
  null-island / out-of-county points.

## Final Deliverable Questions

1. **Can we acquire this reliably?** Yes — authoritative ArcGIS REST + shapefile
   bulk, both public and free.
2. **Scrape / import / request / license?** **Import** via ArcGIS REST bulk query
   (and shapefile download). This is a sanctioned bulk API, not fragile scraping.
3. **Can we automate it?** Yes — already automated cleanly, with pagination and
   resume.
4. **Would a professional data company build against this source?** Yes — county
   ArcGIS parcel services + HCAD shapefiles are the industry-standard parcel spine.
5. **Would I personally certify this for nightly production?** **Yes, on the
   fabric's cadence rather than nightly**, and after (a) verifying live layer
   metadata/endpoint once `gis.hctx.net` is reachable and (b) wiring the shapefile
   fallback. Parcel geometry changes slowly, so daily re-pulls are wasteful; the
   method and design are certifiable.

## Remaining Unknowns (UNVERIFIED — Further confirmation required)

- Live parcels-layer field list, exact record count, and `supportsPagination`
  value (endpoint blocked from this environment).
- Whether `www.gis.hctx.net/arcgishcpid/.../HCAD/Parcels/FeatureServer/0` is the
  current authoritative endpoint or has moved.
- Exact HCAD GIS shapefile download URL and refresh cadence.
- Current status of the Harris County Open Data ArcGIS Hub decommissioning.
- Exact GIS licensing/reuse terms.

## Sources

- Direct code evidence: `scraper/arcgis.py`, `README.md` (this repo) — target endpoints and extraction design.
- [PDATA — Harris Central Appraisal District](https://hcad.org/hcad-online-services/pdata/) (GIS downloads live under PDATA).
- [HCAD Parcel Viewer v2.1](https://arcweb.hcad.org/parcel-viewer-v2.0/)
- Harris County GIS contact: gisinfo@hctx.net (Open Data Hub decommissioning).
