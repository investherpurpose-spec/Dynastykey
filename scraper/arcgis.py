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


class ArcGISError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {"User-Agent": "DynastykeyScraper/0.1 (public records research)"}
    )
    return s


def _get_json(session: requests.Session, url: str, params: dict) -> dict:
    """GET with retries/backoff. ArcGIS returns errors inside a 200 body."""
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "error" in data:
                raise ArcGISError(json.dumps(data["error"]))
            return data
        except (requests.RequestException, ValueError, ArcGISError) as exc:
            last_exc = exc
            wait = 2**attempt
            log.warning("request failed (%s), retry %d/%d in %ss", exc, attempt + 1, MAX_RETRIES, wait)
            time.sleep(wait)
    raise ArcGISError(f"giving up on {url}: {last_exc}")


def get_layer_info(layer_url: str, session: Optional[requests.Session] = None) -> dict:
    session = session or _session()
    return _get_json(session, layer_url.rstrip("/"), {"f": "json"})


def get_service_info(service_url: str) -> dict:
    """Info for a service or folder URL (lists layers/services)."""
    return _get_json(_session(), service_url.rstrip("/"), {"f": "json"})


def count_features(layer_url: str, where: str = "1=1") -> int:
    data = _get_json(
        _session(),
        layer_url.rstrip("/") + "/query",
        {"where": where, "returnCountOnly": "true", "f": "json"},
    )
    return int(data.get("count", 0))


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
    info = get_layer_info(layer_url, session)

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
    """Return (lon, lat) for a point geometry, or a rough centroid for polygons."""
    geom = feature.get("geometry")
    if not geom:
        return None, None
    if "x" in geom and "y" in geom:
        return geom.get("x"), geom.get("y")
    rings = geom.get("rings") or geom.get("paths")
    if rings:
        pts = [pt for ring in rings for pt in ring]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            return sum(xs) / len(xs), sum(ys) / len(ys)
    return None, None
