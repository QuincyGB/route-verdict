"""Graceful-failure pass (Next-20 #18): caller faults answer in the
fleet {error, code} shape naming the field, no bare 500 ever leaves,
and a failed catalog lookup is "unknown" — never a crash.

The paid-path tests bypass the x402 middleware in-process (the
paywall contract itself is pinned by test_paywall.py: unpaid probes
of every shape still get the 402 first).
"""

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import catalog


@pytest.fixture()
def client(monkeypatch):
    async def passthrough(request, call_next):
        return await call_next(request)

    monkeypatch.setattr(main, "_payment_middleware", passthrough)
    return TestClient(main.app, raise_server_exceptions=False)


def test_missing_field_is_named_in_fleet_shape(client):
    r = client.post("/v1/check", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "'resource_url' is required" in body["error"]


def test_malformed_json_is_named_in_fleet_shape(client):
    r = client.post(
        "/v1/check",
        content=b"{not-json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "JSON" in body["error"]


def test_wrong_type_is_named_in_fleet_shape(client):
    r = client.post("/v1/check", json={"resource_url": {"x": 1}})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert "'resource_url'" in body["error"]


def test_unhandled_failure_is_fleet_shaped_502(client, monkeypatch):
    def boom():
        raise RuntimeError("store exploded")

    monkeypatch.setattr(main, "get_store", boom)
    r = client.get("/v1/history", params={"resource_url": "https://x.example/v1/y"})
    assert r.status_code == 502
    assert r.json() == {"error": "internal failure", "code": "internal_error"}


class _ExplodingClient:
    async def get(self, *a, **k):
        raise RuntimeError("event loop is closed")


@pytest.mark.anyio
async def test_catalog_lookup_non_http_failure_is_unknown():
    # A lookup failure that is NOT an httpx.HTTPError (the Next-20 #18
    # harness found RuntimeError escaping) still reports "unknown",
    # per the catalog contract — it must not crash the check.
    cdp = await catalog.cdp_bazaar_presence(
        _ExplodingClient(), "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F", "https://x.example/v1/y"
    )
    assert cdp["status"] == "unknown"
    payai = await catalog.payai_presence(_ExplodingClient(), "https://x.example/v1/y")
    assert payai["status"] == "unknown"
