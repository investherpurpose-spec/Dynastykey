"""Generic ArcGIS REST API client for bulk feature extraction.

Works against any FeatureServer or MapServer layer URL, e.g.:
    https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0

Handles both pagination styles:
  - resultOffset paging when the layer advertises supportsPagination
  - objectId-chunk paging as a fallback for older servers
"""

from __future__ import annotations

import json
import logging
import time
from typing import Iterator, Optional

import requests

log = logging.getLogger("scraper.arcgis")

DEFAULT_PAGE_SIZE = 1000
REQUEST_TIMEOUT = 120
MAX_RETRIES = 5
POLITE_DELAY = 0.25  # seconds between page requests

# Known Harris County layers (verify with `discover` — GIS servers move over time)
HARRIS_PARCELS_LAYER = (
    "https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0"
)
HARRIS_PARCELS_MAPSERVER = (
    "https://www.gis.hctx.net/arcgis/rest/services/HCAD/Parcels/MapServer/0"
)

# Approximate WGS84 bounding box for Harris County, TX. Public geographic fact,
# used only for gross centroid sanity (reject null-island / out-of-region
# points) — NOT a precise boundary test. Kept slightly loose to avoid false
# rejects on edge parcels.
HARRIS_BBOX = (-96.05, 29.40, -94.85, 30.25)  # (min_lon, min_lat, max_lon, max_lat)


class ArcGISError(RuntimeError):
    pass


class ArcGISPermanentError(ArcGISError):
    """A non-retryable ArcGIS error (e.g. bad field, 4xx) — fail fast."""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"User-Agent": "DynastykeyScraper/0.1 (public records research)"}
    )
    return s


def _is_permanent_status(status: Optional[int]) -> bool:
    # 4xx client errors are permanent, except 429 (rate limit -> retry).
    return status is not None and 400 <= status < 500 and status != 429


def _get_json(session: requests.Session, url: str, params: dict,
              max_retries: int = MAX_RETRIES) -> dict:
    """GET with retries/backoff. ArcGIS returns errors inside a 200 body.

    Retries transient failures (network, decode, 5xx, 429) with exponential
    backoff, but raises ArcGISPermanentError immediately on non-retryable
    client errors (4xx, bad query) so we don't hammer a request that can never
    succeed. No sleep after the final attempt.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            status = getattr(resp, "status_code", None)
            if _is_permanent_status(status):
                raise ArcGISPermanentError(f"HTTP {status} for {url}")
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                err = data["error"] or {}
                code = err.get("code") if isinstance(err, dict) else None
                try:
                    code = int(code) if code is not None else None
                except (TypeError, ValueError):
                    code = None
                if _is_permanent_status(code):
                    raise ArcGISPermanentError(json.dumps(err))
                raise ArcGISError(json.dumps(err))
            return data
        except ArcGISPermanentError:
            raise
        except (requests.RequestException, ValueError, ArcGISError) as exc:
            last_exc = exc
            if attempt == max_retries - 1:
                break
            wait = 2**attempt
            log.warning("request failed (%s), retry %d/%d in %ss",
                        exc, attempt + 1, max_retries, wait)
            time.sleep(wait)
    raise ArcGISError(f"giving up on {url}: {last_exc}")


def get_layer_info(layer_url: str, session: Optional[requests.Session] = None) -> dict:
    session = session or _session()
    return _get_json(session, layer_url.rstrip("/"), {"f": "json"})


def get_service_info(service_url: str) -> dict:
    """Info for a service or folder URL (lists layers/services)."""
    return _get_json(_session(), service_url.rstrip("/"), {"f": "json"})


def count_features(layer_url: str, where: str = "1=1",
                   session: Optional[requests.Session] = None) -> int:
    data = _get_json(
        session or _session(),
        layer_url.rstrip("/") + "/query",
        {"where": where, "returnCountOnly": "true", "f": "json"},
    )
    return int(data.get("count", 0))


def resolve_layer(layer_url: str, fallbacks: Optional[list] = None,
                  session: Optional[requests.Session] = None) -> tuple[str, dict]:
    """Return (working_url, layer_info), trying fallbacks if the primary moved.

    ArcGIS endpoints move over time, so a nightly run should degrade to a known
    alternate (e.g. the MapServer mirror) instead of failing. If `fallbacks` is
    None and the primary is the Harris parcels FeatureServer, the MapServer
    mirror is tried automatically.
    """
    session = session or _session()
    if fallbacks is None:
        fallbacks = ([HARRIS_PARCELS_MAPSERVER]
                     if layer_url.rstrip("/") == HARRIS_PARCELS_LAYER else [])
    candidates = [layer_url] + list(fallbacks)
    last_exc: Optional[Exception] = None
    for cand in candidates:
        try:
            info = get_layer_info(cand, session)
            if isinstance(info, dict) and ("fields" in info or info.get("type")):
                if cand != layer_url:
                    log.warning("primary layer unavailable; using fallback %s", cand)
                return cand, info
            last_exc = ArcGISError(f"{cand}: response is not a layer")
        except ArcGISError as exc:
            last_exc = exc
            log.warning("layer probe failed for %s: %s", cand, exc)
    raise ArcGISError(f"no working layer endpoint among {candidates}: {last_exc}")


def reconcile_count(expected: Optional[int], got: int,
                    tolerance: float = 0.0) -> tuple[bool, str]:
    """Compare a pulled row count against the layer's advertised count.

    tolerance is a fractional allowance (0.0 = exact). expected=None means the
    server count was unavailable, which is reported (not silently passed).
    """
    if expected is None:
        return True, f"got {got}; expected count unavailable"
    if expected == 0:
        return got == 0, f"got {got}; expected 0"
    delta = abs(got - expected) / expected
    ok = delta <= tolerance
    return ok, f"got {got} vs expected {expected} (delta {delta:.4f}, allow {tolerance})"


def in_bbox(lon, lat, bbox: tuple = HARRIS_BBOX) -> bool:
    """True if (lon, lat) is inside bbox and numeric."""
    try:
        lon_f, lat_f = float(lon), float(lat)
    except (TypeError, ValueError):
        return False
    mnlon, mnlat, mxlon, mxlat = bbox
    return mnlon <= lon_f <= mxlon and mnlat <= lat_f <= mxlat


def validate_point(lon, lat, bbox: tuple = HARRIS_BBOX):
    """Return ((lon, lat), None) if the point passes gross sanity, else
    (None, reason). Rejects no-geometry, non-numeric, null-island, out-of-bbox.
    """
    if lon is None or lat is None:
        return None, "no_geometry"
    try:
        lon_f, lat_f = float(lon), float(lat)
    except (TypeError, ValueError):
        return None, "non_numeric"
    if lon_f == 0 and lat_f == 0:
        return None, "null_island"
    if not in_bbox(lon_f, lat_f, bbox):
        return None, "out_of_bbox"
    return (lon_f, lat_f), None


def iter_features(
    layer_url: str,
    where: str = "1=1",
    out_fields: str = "*",
    include_geometry: bool = False,
    page_size: Optional[int] = None,
    start_offset: int = 0,
    limit: Optional[int] = None,
) -> Iterator[dict]:
    """Yield every feature dict ({'attributes': ..., 'geometry': ...}) from a layer."""
    session = _session()
    layer_url = layer_url.rstrip("/")
    # Resolve the endpoint first so a moved primary degrades to a fallback
    # (e.g. the MapServer mirror) instead of aborting the pull.
    layer_url, info = resolve_layer(layer_url, session=session)

    max_rc = int(info.get("maxRecordCount") or DEFAULT_PAGE_SIZE)
    page = min(page_size or max_rc, max_rc)
    adv = info.get("advancedQueryCapabilities") or {}
    supports_pagination = bool(adv.get("supportsPagination"))
    oid_field = info.get("objectIdField") or "OBJECTID"
    for f in info.get("fields") or []:
        if f.get("type") == "esriFieldTypeOID":
            oid_field = f["name"]
            break

    query_url = layer_url + "/query"
    base = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if include_geometry else "false",
        "outSR": 4326,
        "f": "json",
    }

    yielded = 0
    if supports_pagination:
        offset = start_offset
        while True:
            params = dict(base, resultOffset=offset, resultRecordCount=page)
            data = _get_json(session, query_url, params)
            feats = data.get("features") or []
            if not feats:
                return
            for feat in feats:
                yield feat
                yielded += 1
                if limit and yielded >= limit:
                    return
            offset += len(feats)
            log.info("fetched %d features (offset %d)", yielded, offset)
            if not data.get("exceededTransferLimit") and len(feats) < page:
                return
            time.sleep(POLITE_DELAY)
    else:
        # Fallback: fetch all object IDs once, then pull in chunks.
        ids_data = _get_json(
            session, query_url, {"where": where, "returnIdsOnly": "true", "f": "json"}
        )
        object_ids = sorted(ids_data.get("objectIds") or [])
        object_ids = object_ids[start_offset:]
        for i in range(0, len(object_ids), page):
            chunk = object_ids[i : i + page]
            params = dict(base, objectIds=",".join(str(x) for x in chunk))
            params.pop("where", None)
            data = _get_json(session, query_url, params)
            for feat in data.get("features") or []:
                yield feat
                yielded += 1
                if limit and yielded >= limit:
                    return
            log.info("fetched %d/%d features", yielded, len(object_ids))
            time.sleep(POLITE_DELAY)


def point_of(feature: dict) -> tuple[Optional[float], Optional[float]]:
    """Return (lon, lat) for a point geometry, or a rough centroid for polygons.

    Resilient to malformed geometry: any missing/non-numeric coordinate yields
    (None, None) rather than raising, so one bad feature can't abort a pull.
    """
    geom = feature.get("geometry")
    if not isinstance(geom, dict):
        return None, None
    try:
        if "x" in geom and "y" in geom:
            x, y = geom.get("x"), geom.get("y")
            if x is None or y is None:
                return None, None
            return float(x), float(y)
        rings = geom.get("rings") or geom.get("paths")
        if rings:
            pts = [pt for ring in rings for pt in ring
                   if isinstance(pt, (list, tuple)) and len(pt) >= 2]
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            if xs and ys:
                return sum(xs) / len(xs), sum(ys) / len(ys)
    except (TypeError, ValueError):
        return None, None
    return None, None
