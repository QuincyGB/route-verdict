"""GET /v1/history — the free per-resource check-history read the
fleet timeline aggregates. Offline: the store is seeded directly,
no probe ever runs."""

import pytest
from fastapi.testclient import TestClient

from app import main
from app.store import CheckStore

URL = "https://seller.example.com/x402/thing"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store = CheckStore(str(tmp_path / "checks.db"))
    monkeypatch.setattr(main, "_store", store)
    for i in range(3):
        store.save(
            {
                "resource_url": URL,
                "checked_at": f"2026-10-2{i}T00:00:00Z",
                "verdict": "TRUSTED",
                "score": 95.0,
            }
        )
    store.save(
        {
            "resource_url": "https://other.example.com/x",
            "checked_at": "2026-10-20T00:00:00Z",
            "verdict": "CAUTION",
            "score": 50.0,
        }
    )
    return TestClient(main.app, raise_server_exceptions=False)


def test_history_returns_only_that_url_oldest_first(client):
    resp = client.get("/v1/history", params={"resource_url": URL})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resource_url"] == URL
    assert body["count"] == 3
    assert [c["checked_at"] for c in body["checks"]] == [
        "2026-10-20T00:00:00Z",
        "2026-10-21T00:00:00Z",
        "2026-10-22T00:00:00Z",
    ]


def test_history_unknown_url_is_empty_not_an_error(client):
    resp = client.get(
        "/v1/history", params={"resource_url": "https://nope.example.com/x"}
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "resource_url": "https://nope.example.com/x",
        "count": 0,
        "checks": [],
    }


def test_history_requires_a_url(client):
    resp = client.get("/v1/history", params={"resource_url": "  "})
    assert resp.status_code == 422
