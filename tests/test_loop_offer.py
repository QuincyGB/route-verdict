"""Loop offer block (Play 1, organic-buyer plan 2026-11-01): every
paid /v1/check delivery carries one additive top-level
``verdict_row_loop`` field quoting the fleet's repeat products
exactly as StallRow's enforcing code states them — and error
responses never carry it."""

import asyncio
import json

import app.main as main
from app import loop_offer
from app.fleet_pass import LEDGER_URL


def _offers_by_id(block):
    return {o["id"]: o for o in block["offers"]}


def test_block_terms_match_stallrow_enforcement():
    block = loop_offer.loop_offer()
    offers = _offers_by_id(block)
    assert block["discovery"] == "/.well-known/verdict-row.json"

    fpass = offers["fleet_pass"]
    assert fpass["url"] == f"{LEDGER_URL}/v1/fleet-pass"
    assert fpass["price_usd"] == 5.0
    assert fpass["terms"]["ttl_days"] == 90

    fpass20 = offers["fleet_pass_20"]
    assert fpass20["url"] == f"{LEDGER_URL}/v1/fleet-pass-20"
    assert fpass20["price_usd"] == 20.0

    pack = offers["recheck_pack"]
    assert pack["url"] == f"{LEDGER_URL}/v1/recheck/pack"
    assert pack["price_usd"] == 1.0
    assert pack["terms"]["credits"] == 250
    assert pack["terms"]["ttl_days"] == 90
    assert pack["terms"]["per_check_usd"] == 1.0 / 250
    assert pack["terms"]["per_call_price_usd"] == 0.005

    hook = offers["recheck_change_webhook"]
    assert hook["url"] == f"{LEDGER_URL}/v1/recheck/webhooks"
    assert hook["price_usd"] == 0
    assert "pack_id" in hook["terms"]["gate"]
    assert hook["terms"]["registration_debited"] is False

    feed = offers["recheck_feed"]
    assert feed["url"] == f"{LEDGER_URL}/v1/recheck/feed"
    assert feed["price_usd"] == 0


def _run_check_handler(monkeypatch, fake):
    monkeypatch.setattr(main, "run_check", fake)
    monkeypatch.setattr(main, "get_store", lambda: None)
    req = main.CheckRequest(resource_url="https://93.184.216.34/v1/verdict")
    return asyncio.run(main.check(req))


def test_paid_delivery_carries_the_block(monkeypatch):
    async def fake(resource_url, store, check_timeout=None):
        return {"verdict": "TRUSTED", "resource_url": resource_url}

    out = _run_check_handler(monkeypatch, fake)
    assert out["verdict"] == "TRUSTED"  # original fields untouched
    block = out["verdict_row_loop"]
    assert _offers_by_id(block)["fleet_pass"]["price_usd"] == 5.0


def test_failed_delivery_has_no_block(monkeypatch):
    async def fake(resource_url, store, check_timeout=None):
        raise ValueError("resource_url must be an http(s) URL")

    out = _run_check_handler(monkeypatch, fake)
    assert out.status_code == 422
    assert "verdict_row_loop" not in out.body.decode()
    assert json.loads(out.body)["error"]


def test_attach_passes_non_dicts_through():
    sentinel = object()
    assert loop_offer.attach_loop_offer(sentinel) is sentinel
