"""The paywall contract: unpaid POST /v1/check gets a 402 whose accepts
carry RouteVerdict's real terms ($0.05 USDC on Base to the owner wallet)
and the full Bazaar discovery extension; free routes stay open.

Offline: the middleware builds the challenge locally; no facilitator
settlement happens for an unpaid call.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main

USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


@pytest.fixture(scope="module")
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def _challenge(response):
    raw = response.headers.get("payment-required")
    assert raw, "402 response must carry a payment-required header"
    return json.loads(base64.b64decode(raw))


def test_unpaid_check_is_paywalled_with_correct_accepts(client):
    r = client.post("/v1/check", json={"resource_url": "https://target.example.com/v1/thing"})
    assert r.status_code == 402
    challenge = _challenge(r)
    assert challenge["x402Version"] == 2
    accepts = challenge["accepts"]
    assert len(accepts) == 1
    a = accepts[0]
    assert a["scheme"] == "exact"
    assert a["network"] == "eip155:8453"
    assert a["asset"] == USDC
    assert a["amount"] == "50000"  # $0.05 in USDC smallest units
    assert a["payTo"] == main.settings.pay_to
    assert a["payTo"] == "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"


def test_402_carries_full_bazaar_extension(client):
    r = client.post("/v1/check", json={"resource_url": "https://target.example.com/v1/thing"})
    assert r.status_code == 402
    challenge = _challenge(r)
    bazaar = challenge["extensions"]["bazaar"]
    output = bazaar["info"]["output"]
    # Not hollow: a typed schema with real properties, not {type, example}.
    assert "schema" in output
    assert "score" in output["schema"]["properties"]
    assert "verdict" in output["schema"]["properties"]


def test_health_reports_check_store_storage(client):
    body = client.get("/health").json()
    storage = body["storage"]
    assert storage["check_db_path"] == main.settings.db_path
    expected = "persistent" if main.settings.db_persistent else "ephemeral"
    assert storage["check_store"] == expected


def test_free_routes_stay_open(client):
    assert client.get("/health").status_code == 200
    quote = client.get("/quote")
    assert quote.status_code == 200
    body = quote.json()
    assert body["price"] == "$0.05"
    assert body["endpoint"] == "/v1/check"
    assert body["payTo"] == "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
    assert client.get("/").status_code == 200
