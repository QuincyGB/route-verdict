"""W2 response upgrades: next_check + evidence on paid deliveries.

Shape mirrors the fleet reference (token-verdict
app/response_upgrades.py): handlers emit neither block; the delivery
layer (_attach_loop_offer) attaches both, filling settlement_tx from
the settlement receipt header when one is present. The fleet-internal
unpaid path calls the handler directly and never carries the blocks.
"""

import asyncio
import base64
import json
from types import SimpleNamespace

from fastapi.responses import JSONResponse

import app.main as main
from app.response_upgrades import NEXT_CHECKS, attach_upgrades, canonical_input_hash

TARGET = "https://target.example.com/v1/thing"


def test_next_check_map():
    nxt = NEXT_CHECKS["/v1/check"]
    assert nxt["name"] == "counterparty-report"
    assert nxt["service"] == "stall-row"
    assert nxt["path"] == "/v1/counterparty-report"
    assert nxt["price_usd"] == 0.10
    assert nxt["url"].endswith("/v1/counterparty-report")


def test_attach_upgrades_shape_and_hash():
    result = attach_upgrades({"verdict": "TRUSTED"}, "/v1/check", {"resource_url": TARGET})
    assert result["next_check"]["path"] == "/v1/counterparty-report"
    ev = result["evidence"]
    assert ev["settlement_tx"] is None
    assert ev["input_hash"] == canonical_input_hash({"resource_url": TARGET})
    assert ev["timestamp"].endswith("Z")
    assert canonical_input_hash({"b": 1, "a": 2}) == canonical_input_hash({"a": 2, "b": 1})


def test_attach_upgrades_passes_errors_through():
    resp = JSONResponse({"error": "nope"}, status_code=422)
    assert attach_upgrades(resp, "/v1/check", {}) is resp


def _request(path="/v1/check", paid_input=None):
    return SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path=path),
        headers={},
        state=SimpleNamespace(paid_input=paid_input or {"resource_url": TARGET}),
    )


def test_delivery_layer_attaches_upgrades_and_fills_settlement_tx():
    body = {"verdict": "TRUSTED", "score": 91}
    receipt = base64.b64encode(
        json.dumps({"transaction": "0xdeadbeef", "payer": "0xpayer", "network": "eip155:8453"}).encode()
    ).decode()
    response = JSONResponse(body, headers={"payment-response": receipt})
    out = asyncio.run(main._attach_loop_offer(_request(), response))
    data = json.loads(out.body)
    assert data["verdict"] == "TRUSTED"
    assert "verdict_row_loop" in data
    assert data["next_check"]["path"] == "/v1/counterparty-report"
    assert data["next_check"]["price_usd"] == 0.10
    assert data["evidence"]["settlement_tx"] == "0xdeadbeef"
    assert data["evidence"]["input_hash"] == canonical_input_hash({"resource_url": TARGET})
    assert data["evidence"]["timestamp"].endswith("Z")


def test_delivery_layer_no_receipt_keeps_settlement_tx_none():
    response = JSONResponse({"verdict": "TRUSTED", "score": 91})
    out = asyncio.run(main._attach_loop_offer(_request(), response))
    data = json.loads(out.body)
    assert data["evidence"]["settlement_tx"] is None
    assert "next_check" in data


def test_delivery_layer_error_body_carries_no_upgrades():
    response = JSONResponse({"error": "no route", "code": "no_route"})
    out = asyncio.run(main._attach_loop_offer(_request(), response))
    data = json.loads(out.body)
    assert "next_check" not in data and "evidence" not in data
