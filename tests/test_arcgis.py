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
