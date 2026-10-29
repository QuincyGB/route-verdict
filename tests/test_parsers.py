"""Offline tests for the x402 challenge parser.

The parser reads other people's 402s, so it must be liberal: v1 and v2
shapes, body or base64 header, partial garbage. A parse failure is a
finding the checker reports, never an exception.
"""

import base64
import json

from app.parsers import format_price, parse_challenge

USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
PAY_TO = "0x1111111111111111111111111111111111111111"

ACCEPT = {
    "scheme": "exact",
    "network": "eip155:8453",
    "asset": USDC,
    "amount": "20000",
    "payTo": PAY_TO,
    "maxTimeoutSeconds": 300,
}


def v2_body(**over):
    body = {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {
            "url": "https://target.example.com/v1/thing",
            "description": "A thing.",
            "mimeType": "application/json",
        },
        "accepts": [dict(ACCEPT)],
        "extensions": {"bazaar": {"info": {"input": {}, "output": {}}}},
    }
    body.update(over)
    return json.dumps(body).encode()


def test_v2_body_parses_fully():
    p = parse_challenge(402, {"content-type": "application/json"}, v2_body())
    assert p.valid is True
    assert p.source == "body"
    assert p.x402_version == 2
    assert p.resource_description == "A thing."
    assert p.bazaar == {"info": {"input": {}, "output": {}}}
    a = p.primary_accept()
    assert a.scheme == "exact"
    assert a.network == "eip155:8453"
    assert a.asset == USDC
    assert a.amount == "20000"
    assert a.pay_to == PAY_TO
    assert a.max_timeout_seconds == 300


def test_v1_max_amount_required_maps_to_amount():
    body = json.dumps(
        {
            "x402Version": 1,
            "accepts": [
                {
                    "scheme": "exact",
                    "network": "eip155:8453",
                    "asset": USDC,
                    "maxAmountRequired": "5000",
                    "payTo": PAY_TO,
                }
            ],
        }
    ).encode()
    p = parse_challenge(402, {}, body)
    assert p.valid is True
    assert p.x402_version == 1
    assert p.primary_accept().amount == "5000"


def test_header_challenge_parses():
    payload = {"x402Version": 2, "accepts": [dict(ACCEPT)]}
    header = base64.b64encode(json.dumps(payload).encode()).decode()
    p = parse_challenge(402, {"payment-required": header}, b"")
    assert p.valid is True
    assert p.source == "header"
    assert p.primary_accept().pay_to == PAY_TO


def test_garbage_body_is_invalid_not_exception():
    p = parse_challenge(402, {}, b"<html>pay up</html>")
    assert p.valid is False
    assert p.error


def test_no_accepts_is_invalid():
    p = parse_challenge(402, {}, json.dumps({"x402Version": 2}).encode())
    assert p.valid is False
    assert "accepts" in p.error


def test_non_402_without_challenge_is_invalid():
    p = parse_challenge(200, {}, json.dumps({"hello": "world"}).encode())
    assert p.valid is False


def test_primary_accept_prefers_base():
    eth = dict(ACCEPT, network="eip155:1")
    p = parse_challenge(402, {}, v2_body(accepts=[eth, dict(ACCEPT)]))
    assert p.primary_accept().network == "eip155:8453"


def test_format_price_known_and_unknown_assets():
    assert format_price("20000", "eip155:8453", USDC) == "$0.02"
    assert format_price("50000", "eip155:8453", USDC) == "$0.05"
    assert format_price("20000", "eip155:8453", "0x0000000000000000000000000000000000000001") is None
    assert format_price(None, "eip155:8453", USDC) is None
    assert format_price("not-a-number", "eip155:8453", USDC) is None


def test_format_price_preserves_sub_cent_precision():
    # Regression (aicom review, 2026-10-29): a $0.001 endpoint rendered
    # price_usdc "$0.00" while amount_units correctly said "1000".
    assert format_price("1000", "eip155:8453", USDC) == "$0.001"
    assert format_price("5000", "eip155:8453", USDC) == "$0.005"
    assert format_price("1", "eip155:8453", USDC) == "$0.000001"
    assert format_price("10000", "eip155:8453", USDC) == "$0.01"
    assert format_price("500000", "eip155:8453", USDC) == "$0.50"
    assert format_price("1000000", "eip155:8453", USDC) == "$1.00"
    assert format_price("0", "eip155:8453", USDC) == "$0.00"
