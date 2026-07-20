"""Offline tests for the hardened ArcGIS client.

Uses a fake requests.Session so no network is touched: retry classification,
endpoint fallback, geospatial sanity, count reconciliation, and malformed-
geometry resilience.
"""

import json

import pytest
import requests

from scraper import arcgis


class FakeResp:
    def __init__(self, payload, status_code=200, raise_exc=None):
        self._payload = payload
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Returns queued responses in order; records call count."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        r = self._responses.pop(0)
        if callable(r):
            return r(url, params)
        return r


# ---- _get_json retry classification --------------------------------------

def test_get_json_success():
    sess = FakeSession([FakeResp({"count": 5})])
    data = arcgis._get_json(sess, "http://x/query", {})
    assert data["count"] == 5
    assert sess.calls == 1


def test_get_json_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    sess = FakeSession([
        FakeResp(None, raise_exc=requests.ConnectionError("boom")),
        FakeResp({"ok": True}),
    ])
    data = arcgis._get_json(sess, "http://x", {}, max_retries=3)
    assert data == {"ok": True}
    assert sess.calls == 2


def test_get_json_permanent_4xx_not_retried():
    sess = FakeSession([FakeResp({}, status_code=404)])
    with pytest.raises(arcgis.ArcGISPermanentError):
        arcgis._get_json(sess, "http://x", {}, max_retries=5)
    assert sess.calls == 1  # no retry


def test_get_json_body_error_permanent_code():
    sess = FakeSession([FakeResp({"error": {"code": 400, "message": "bad field"}})])
    with pytest.raises(arcgis.ArcGISPermanentError):
        arcgis._get_json(sess, "http://x", {}, max_retries=5)
    assert sess.calls == 1


def test_get_json_body_error_transient_retried(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    sess = FakeSession([
        FakeResp({"error": {"code": 503, "message": "busy"}}),
        FakeResp({"ok": 1}),
    ])
    data = arcgis._get_json(sess, "http://x", {}, max_retries=3)
    assert data == {"ok": 1}
    assert sess.calls == 2


def test_get_json_gives_up_after_max_retries(monkeypatch):
    slept = []
    monkeypatch.setattr(arcgis.time, "sleep", lambda s: slept.append(s))
    sess = FakeSession([FakeResp(None, raise_exc=requests.ConnectionError("x"))
                        for _ in range(3)])
    with pytest.raises(arcgis.ArcGISError):
        arcgis._get_json(sess, "http://x", {}, max_retries=3)
    assert sess.calls == 3
    assert len(slept) == 2  # no sleep after the final attempt


# ---- resolve_layer fallback ----------------------------------------------

def test_resolve_layer_primary_ok():
    info = {"fields": [], "type": "Feature Layer", "name": "Parcels"}
    sess = FakeSession([FakeResp(info)])
    url, got = arcgis.resolve_layer("http://primary/0", fallbacks=[], session=sess)
    assert url == "http://primary/0"
    assert got["name"] == "Parcels"


def test_resolve_layer_falls_back(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    # primary 404 (permanent) then fallback succeeds
    good = {"fields": [], "type": "Feature Layer"}
    sess = FakeSession([
        FakeResp({}, status_code=404),
        FakeResp(good),
    ])
    url, got = arcgis.resolve_layer("http://primary/0",
                                    fallbacks=["http://mirror/0"], session=sess)
    assert url == "http://mirror/0"


def test_resolve_layer_all_fail(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    sess = FakeSession([
        FakeResp({}, status_code=404),
        FakeResp({}, status_code=404),
    ])
    with pytest.raises(arcgis.ArcGISError):
        arcgis.resolve_layer("http://a/0", fallbacks=["http://b/0"], session=sess)


def test_resolve_layer_non_layer_response_rejected():
    # a folder/service response (no fields/type) is not a layer
    sess = FakeSession([FakeResp({"services": ["x"]})])
    with pytest.raises(arcgis.ArcGISError):
        arcgis.resolve_layer("http://svc", fallbacks=[], session=sess)


# ---- reconcile_count ------------------------------------------------------

def test_reconcile_exact():
    ok, detail = arcgis.reconcile_count(100, 100)
    assert ok
    ok, _ = arcgis.reconcile_count(100, 99)
    assert not ok


def test_reconcile_tolerance():
    ok, _ = arcgis.reconcile_count(1000, 995, tolerance=0.01)
    assert ok
    ok, _ = arcgis.reconcile_count(1000, 980, tolerance=0.01)
    assert not ok


def test_reconcile_unknown_expected():
    ok, detail = arcgis.reconcile_count(None, 42)
    assert ok
    assert "unavailable" in detail


def test_reconcile_zero_expected():
    assert arcgis.reconcile_count(0, 0)[0]
    assert not arcgis.reconcile_count(0, 3)[0]


# ---- geospatial sanity ----------------------------------------------------

def test_in_bbox_harris_point():
    # Downtown Houston ~(-95.37, 29.76)
    assert arcgis.in_bbox(-95.37, 29.76)


def test_in_bbox_rejects_far_point():
    assert not arcgis.in_bbox(-80.0, 40.0)  # Pennsylvania-ish


def test_in_bbox_non_numeric():
    assert not arcgis.in_bbox("abc", None)


@pytest.mark.parametrize("lon,lat,reason", [
    (None, None, "no_geometry"),
    ("x", "y", "non_numeric"),
    (0, 0, "null_island"),
    (-80.0, 40.0, "out_of_bbox"),
])
def test_validate_point_rejections(lon, lat, reason):
    pt, got = arcgis.validate_point(lon, lat)
    assert pt is None
    assert got == reason


def test_validate_point_accepts_harris():
    pt, reason = arcgis.validate_point(-95.37, 29.76)
    assert reason is None
    assert pt == (-95.37, 29.76)


# ---- point_of resilience --------------------------------------------------

def test_point_of_point_geometry():
    assert arcgis.point_of({"geometry": {"x": -95.3, "y": 29.7}}) == (-95.3, 29.7)


def test_point_of_polygon_centroid():
    poly = {"geometry": {"rings": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}}
    lon, lat = arcgis.point_of(poly)
    assert lon == pytest.approx(0.8)  # mean of the 5 ring vertices
    assert lat == pytest.approx(0.8)


def test_point_of_no_geometry():
    assert arcgis.point_of({}) == (None, None)
    assert arcgis.point_of({"geometry": None}) == (None, None)


def test_point_of_malformed_does_not_raise():
    assert arcgis.point_of({"geometry": {"x": "nan-ish", "y": None}}) == (None, None)
    assert arcgis.point_of({"geometry": {"rings": [[["a", "b"]]]}}) == (None, None)
    assert arcgis.point_of({"geometry": {"rings": [[[1]]]}}) == (None, None)  # short vertex


# ---- iter_features uses resolver + paging (fully faked) --------------------

class DetServer:
    """Deterministic fake ArcGIS layer backed by a sorted OBJECTID list.

    Honors resultOffset/resultRecordCount against the ordered list and records
    every query's params so tests can assert orderByFields was sent.
    """
    def __init__(self, info, oids):
        self.info = info
        self.oids = list(oids)
        self.query_params = []

    def get(self, url, params=None, timeout=None):
        params = params or {}
        if url.endswith("/query"):
            self.query_params.append(dict(params))
            if params.get("returnCountOnly"):
                return FakeResp({"count": len(self.oids)})
            if params.get("returnIdsOnly"):
                return FakeResp({"objectIds": list(self.oids)})
            if params.get("objectIds"):  # objectId-chunk fallback path
                ids = [int(x) for x in str(params["objectIds"]).split(",")]
                feats = [{"attributes": {"OBJECTID": i}} for i in ids]
                return FakeResp({"features": feats})
            off = int(params.get("resultOffset", 0))
            cnt = int(params.get("resultRecordCount", len(self.oids)))
            window = self.oids[off:off + cnt]
            feats = [{"attributes": {"OBJECTID": i}} for i in window]
            exceeded = (off + cnt) < len(self.oids)
            return FakeResp({"features": feats, "exceededTransferLimit": exceeded})
        return FakeResp(self.info)  # layer metadata (resolve_layer)


def _paged_layer_info(**over):
    info = {
        "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}],
        "type": "Feature Layer",
        "maxRecordCount": 2,
        "advancedQueryCapabilities": {"supportsPagination": True},
        "objectIdField": "OBJECTID",
    }
    info.update(over)
    return info


# ---- deterministic ordered paging -----------------------------------------

def test_paging_metadata_order_field():
    plan = arcgis.paging_metadata(_paged_layer_info())
    assert plan["order_by_field"] == "OBJECTID"
    assert plan["order_by"] == "OBJECTID ASC"
    assert plan["supports_pagination"] is True


def test_pagination_sends_orderby(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    server = DetServer(_paged_layer_info(), oids=[1, 2, 3, 4, 5])
    monkeypatch.setattr(arcgis, "_session", lambda: server)

    feats = list(arcgis.iter_features("http://layer/0"))
    assert [f["attributes"]["OBJECTID"] for f in feats] == [1, 2, 3, 4, 5]
    # every paged query carried a stable ascending order on the OID field
    paged = [p for p in server.query_params
             if "resultOffset" in p and "returnCountOnly" not in p]
    assert paged and all(p.get("orderByFields") == "OBJECTID ASC" for p in paged)


def test_paging_is_deterministic_across_calls(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    oids = list(range(1, 8))
    monkeypatch.setattr(arcgis, "_session", lambda: DetServer(_paged_layer_info(), oids))
    first = [f["attributes"]["OBJECTID"] for f in arcgis.iter_features("http://layer/0")]
    monkeypatch.setattr(arcgis, "_session", lambda: DetServer(_paged_layer_info(), oids))
    second = [f["attributes"]["OBJECTID"] for f in arcgis.iter_features("http://layer/0")]
    assert first == second == oids  # same order every time


# ---- resume from a checkpoint: no gaps, no duplicates ---------------------

def test_resume_from_offset_no_gaps_no_dupes(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    oids = list(range(1, 8))  # 7 features

    # simulate an interruption after 3 features, then resume from offset 3
    monkeypatch.setattr(arcgis, "_session", lambda: DetServer(_paged_layer_info(), oids))
    first_part = [f["attributes"]["OBJECTID"]
                  for f in arcgis.iter_features("http://layer/0", limit=3)]
    assert first_part == [1, 2, 3]

    monkeypatch.setattr(arcgis, "_session", lambda: DetServer(_paged_layer_info(), oids))
    resumed = [f["attributes"]["OBJECTID"]
               for f in arcgis.iter_features("http://layer/0", start_offset=3)]

    combined = first_part + resumed
    assert combined == oids            # no gaps
    assert len(combined) == len(set(combined))  # no duplicates


def test_resume_offset_sends_orderby(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    server = DetServer(_paged_layer_info(), oids=list(range(1, 6)))
    monkeypatch.setattr(arcgis, "_session", lambda: server)
    list(arcgis.iter_features("http://layer/0", start_offset=2))
    paged = [p for p in server.query_params if "resultOffset" in p]
    assert paged[0]["resultOffset"] == 2
    assert all(p.get("orderByFields") == "OBJECTID ASC" for p in paged)


# ---- fail loud when no stable ordering field exists -----------------------

def test_paging_metadata_raises_without_oid():
    info = {"type": "Feature Layer", "fields": [{"name": "NAME", "type": "esriFieldTypeString"}],
            "advancedQueryCapabilities": {"supportsPagination": True}}
    with pytest.raises(arcgis.ArcGISError):
        arcgis.paging_metadata(info)


def test_paging_metadata_raises_when_orderby_unsupported():
    info = _paged_layer_info(
        advancedQueryCapabilities={"supportsPagination": True, "supportsOrderBy": False})
    with pytest.raises(arcgis.ArcGISError):
        arcgis.paging_metadata(info)


def test_iter_features_raises_when_no_stable_order(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    bad_info = {"type": "Feature Layer",
                "fields": [{"name": "NAME", "type": "esriFieldTypeString"}],
                "maxRecordCount": 2,
                "advancedQueryCapabilities": {"supportsPagination": True}}
    monkeypatch.setattr(arcgis, "_session", lambda: DetServer(bad_info, oids=[1, 2, 3]))
    with pytest.raises(arcgis.ArcGISError):
        list(arcgis.iter_features("http://layer/0"))


def test_non_paginated_fallback_still_deterministic(monkeypatch):
    # no pagination -> objectId-chunk fallback, which sorts ids ascending
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    info = {"type": "Feature Layer",
            "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}],
            "maxRecordCount": 2, "objectIdField": "OBJECTID",
            "advancedQueryCapabilities": {"supportsPagination": False}}
    monkeypatch.setattr(arcgis, "_session", lambda: DetServer(info, oids=[5, 3, 1, 4, 2]))
    feats = list(arcgis.iter_features("http://layer/0"))
    assert [f["attributes"]["OBJECTID"] for f in feats] == [1, 2, 3, 4, 5]  # sorted


def test_iter_features_paginates_and_resolves(monkeypatch):
    monkeypatch.setattr(arcgis.time, "sleep", lambda *_: None)
    layer_info = {
        "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}],
        "type": "Feature Layer",
        "maxRecordCount": 2,
        "advancedQueryCapabilities": {"supportsPagination": True},
        "objectIdField": "OBJECTID",
    }
    page1 = {"features": [{"attributes": {"OBJECTID": 1}},
                          {"attributes": {"OBJECTID": 2}}],
             "exceededTransferLimit": True}
    page2 = {"features": [{"attributes": {"OBJECTID": 3}}]}
    responses = [FakeResp(layer_info),  # resolve_layer / get_layer_info
                 FakeResp(page1), FakeResp(page2)]
    monkeypatch.setattr(arcgis, "_session", lambda: FakeSession(responses))

    feats = list(arcgis.iter_features("http://layer/0"))
    assert [f["attributes"]["OBJECTID"] for f in feats] == [1, 2, 3]
