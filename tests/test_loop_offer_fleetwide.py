"""Loop block delivery layer + per-wallet loop memory (Next-20
#1 + #5, 2026-11-03): the verdict_row_loop block now attaches at
the x402 middleware delivery layer, so EVERY successful paid
delivery on this stall's priced route carries it — per-call and
Fleet-Pass funded alike — and a returning payer's block is
personalised from the check store's payer_history (their own
calls / distinct days / total spent, plus an adaptive Recheck
pack offer with identity-bound deep links on StallRow). Error
responses and unpaid 402 challenges never carry it and are never
recorded; the 402 amount is unchanged.

The per-call tests bypass the payment middleware in-process with
a passthrough that stamps the verified payment payload on
request.state exactly as the real middleware does (the paywall
contract itself is pinned by test_paywall.py and the unpaid test
below, which runs against the real middleware)."""

import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import app.main as main
from app.fleet_pass import LEDGER_URL
from app.store import CheckStore

WALLET = "0x1111111111111111111111111111111111111111"
WALLET2 = "0x2222222222222222222222222222222222222222"
OWNER = "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
URL = "https://93.184.216.34/v1/verdict"
CHECK_UNITS = main.PRICE_UNITS["/v1/check"]


def _payload(wallet):
    return SimpleNamespace(
        payload=SimpleNamespace(
            authorization=SimpleNamespace(**{"from": wallet}),
            signature="0xdeadbeef",
        )
    )


async def _fake_check(resource_url, store, check_timeout=None):
    return {"verdict": "TRUSTED", "resource_url": resource_url}


def _settled_client(monkeypatch, tmp_path, wallet=WALLET):
    """TestClient on a fresh store whose payment middleware is a
    passthrough stamping `wallet`'s verified payload — a settled
    per-call delivery, in-process."""
    fresh = CheckStore(str(tmp_path / "rv.db"))
    monkeypatch.setattr(main, "_store", fresh)
    monkeypatch.setattr(main, "run_check", _fake_check)

    async def passthrough(request, call_next):
        request.state.payment_payload = _payload(wallet)
        return await call_next(request)

    monkeypatch.setattr(main, "_payment_middleware", passthrough)
    return TestClient(main.app, raise_server_exceptions=False), fresh


def _check(client):
    return client.post("/v1/check", json={"resource_url": URL})


# --- (a) settled deliveries carry the block ------------------------------

def test_per_call_delivery_carries_block(monkeypatch, tmp_path):
    client, _store = _settled_client(monkeypatch, tmp_path)
    r = _check(client)
    assert r.status_code == 200, r.text
    block = r.json()["verdict_row_loop"]
    offers = {o["id"]: o for o in block["offers"]}
    assert offers["recheck_pack"]["price_usd"] == 1.0
    assert offers["fleet_pass"]["url"] == f"{LEDGER_URL}/v1/fleet-pass"
    # First-time payer: the static block, no personalised section.
    assert "returning_payer" not in block


def _install_ledger(monkeypatch):
    real = httpx.AsyncClient

    def handler(request):
        if request.url.path.endswith("/debit"):
            return httpx.Response(200, json={"debit_id": "fpd_abc"})
        return httpx.Response(200, json={"ok": True})

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler), timeout=10.0)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_fleet_pass_funded_delivery_carries_block(monkeypatch, tmp_path):
    fresh = CheckStore(str(tmp_path / "rv.db"))
    monkeypatch.setattr(main, "_store", fresh)
    monkeypatch.setattr(main, "run_check", _fake_check)
    _install_ledger(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)

    r = client.post(
        "/v1/check", json={"resource_url": URL},
        headers={"X-Fleet-Pass": "fpass_ok"},
    )
    assert r.status_code == 200, r.text
    assert "verdict_row_loop" in r.json()
    assert "returning_payer" not in r.json()["verdict_row_loop"]


# --- (b) a returning payer's delivery is personalised --------------------

def test_second_per_call_delivery_is_personalised(monkeypatch, tmp_path):
    client, store = _settled_client(monkeypatch, tmp_path)

    first = _check(client)
    assert "returning_payer" not in first.json()["verdict_row_loop"]

    second = _check(client)
    assert second.status_code == 200, second.text
    rp = second.json()["verdict_row_loop"]["returning_payer"]
    assert rp["identity"] == WALLET
    assert rp["identity_kind"] == "evm_wallet"
    assert rp["calls"] == 2
    assert rp["calls_this_route"] == 2
    assert rp["distinct_days"] == 1
    assert rp["total_spent_usd"] == 2 * CHECK_UNITS / 1_000_000

    offer = rp["adaptive_offer"]
    assert offer["credits"] == 250
    assert offer["price_usd"] == 1.0
    assert "250" in offer["line"] and "2" in offer["line"]
    assert offer["deep_link"] == f"{LEDGER_URL}/v1/recheck/pack?payer={WALLET}"
    assert offer["webhook_deep_link"] == (
        f"{LEDGER_URL}/v1/recheck/webhooks?payer={WALLET}"
    )

    history = store.payer_history(WALLET)
    assert history["calls"] == 2
    assert history["total_units"] == 2 * CHECK_UNITS
    assert history["routes"]["/v1/check"]["calls"] == 2


def test_other_wallet_still_gets_the_static_block(monkeypatch, tmp_path):
    client, _store = _settled_client(monkeypatch, tmp_path)
    _check(client)
    _check(client)

    async def passthrough2(request, call_next):
        request.state.payment_payload = _payload(WALLET2)
        return await call_next(request)

    monkeypatch.setattr(main, "_payment_middleware", passthrough2)
    r = _check(client)
    assert r.status_code == 200, r.text
    assert "returning_payer" not in r.json()["verdict_row_loop"]


def test_fleet_pass_repeat_delivery_is_personalised(monkeypatch, tmp_path):
    fresh = CheckStore(str(tmp_path / "rv.db"))
    monkeypatch.setattr(main, "_store", fresh)
    monkeypatch.setattr(main, "run_check", _fake_check)
    _install_ledger(monkeypatch)
    client = TestClient(main.app, raise_server_exceptions=False)
    headers = {"X-Fleet-Pass": "fpass_repeat"}

    client.post("/v1/check", json={"resource_url": URL}, headers=headers)
    second = client.post(
        "/v1/check", json={"resource_url": URL}, headers=headers
    )
    assert second.status_code == 200, second.text
    rp = second.json()["verdict_row_loop"]["returning_payer"]
    # The pass ledger records no buyer wallet: the credential id is
    # the payer identity available, and the deep link pre-binds it.
    assert rp["identity"] == "fleet-pass:fpass_repeat"
    assert rp["identity_kind"] == "funding_credential"
    assert rp["calls"] == 2 and rp["calls_this_route"] == 2


# --- (c) 402 challenges and error responses carry no block ---------------

def test_unpaid_challenge_carries_no_block_and_records_nothing(
    monkeypatch, tmp_path
):
    fresh = CheckStore(str(tmp_path / "rv.db"))
    monkeypatch.setattr(main, "_store", fresh)
    client = TestClient(main.app, raise_server_exceptions=False)

    r = _check(client)
    assert r.status_code == 402, r.text
    assert "verdict_row_loop" not in r.text
    challenge = json.loads(base64.b64decode(r.headers["payment-required"]))
    accept = challenge["accepts"][0]
    assert accept["amount"] == str(CHECK_UNITS) == "50000"
    assert accept["payTo"] == OWNER
    assert fresh.payer_history(WALLET) is None


def test_error_responses_carry_no_block(monkeypatch, tmp_path):
    client, store = _settled_client(monkeypatch, tmp_path)

    # Validation error on the priced route (settled passthrough).
    r = client.post("/v1/check", json={})
    assert r.status_code == 422, r.text
    assert "verdict_row_loop" not in r.text

    # Handler failure (bad target): 422, nothing delivered.
    async def failing(resource_url, store, check_timeout=None):
        raise ValueError("resource_url must be an http(s) URL")

    monkeypatch.setattr(main, "run_check", failing)
    r = _check(client)
    assert r.status_code == 422, r.text
    assert "verdict_row_loop" not in r.text

    assert store.payer_history(WALLET) is None


def test_oversized_delivery_is_not_rewritten_but_is_recorded(
    monkeypatch, tmp_path
):
    """The streaming guard: a JSON response advertising a body over
    the injection cap passes through untouched — no buffering, no
    block — while the settled delivery still lands in payer
    memory."""
    fresh = CheckStore(str(tmp_path / "rv.db"))
    monkeypatch.setattr(main, "_store", fresh)
    big = JSONResponse(
        {"dataset": "x"},
        headers={"content-length": str(main._LOOP_MAX_JSON_BYTES + 1)},
    )
    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/v1/check"),
        state=SimpleNamespace(payment_payload=_payload(WALLET)),
    )
    out = asyncio.run(main._attach_loop_offer(request, big))
    assert out is big
    assert fresh.payer_history(WALLET)["calls"] == 1


# --- payer memory store semantics -----------------------------------------

def test_store_counts_calls_days_and_spend(tmp_path):
    store = CheckStore(str(tmp_path / "rv.db"))
    day0 = 1_700_000_000.0

    snap = store.record_payer_delivery(WALLET, "/v1/check", CHECK_UNITS, day0)
    assert snap["prior_calls"] == 0
    assert snap["calls"] == 1 and snap["distinct_days"] == 1

    # Same day again: stall-wide days stay 1, spend sums.
    snap = store.record_payer_delivery(
        WALLET, "/v1/check", CHECK_UNITS, day0 + 60
    )
    assert snap["prior_calls"] == 1
    assert snap["calls"] == 2 and snap["distinct_days"] == 1
    assert snap["total_units"] == 2 * CHECK_UNITS

    # Next UTC day: a new distinct day.
    snap = store.record_payer_delivery(
        WALLET, "/v1/check", CHECK_UNITS, day0 + 86_400
    )
    assert snap["calls"] == 3 and snap["distinct_days"] == 2
    assert store.payer_history(WALLET2) is None


def test_price_units_derived_from_settings():
    assert CHECK_UNITS == 50000
    assert main._price_units(main.settings.price_usdc) == CHECK_UNITS
