"""Verdict Row loop offer: the machine-readable block attached to
paid /v1/check deliveries (Play 1 of the organic-buyer plan,
2026-11-01).

The wallets that buy this check buy it repeatedly, per call, and
they read this payload — no human inbox is involved. The delivery
therefore carries the fleet's repeat products. The products live on
StallRow (the pass/recheck ledger), so the terms below MIRROR the
constants that enforce them there — stall-row
app/services/recheck.py (PACK_CREDITS 250, PACK_PRICE_UNITS
1_000_000, PACK_TTL_DAYS 90) and app/services/fleetpass.py
(PASS_PRICE_UNITS 5_000_000, PASS_V2_PRICE_UNITS 20_000_000,
PASS_TTL_DAYS 90) and stall-row config PRICE_UNITS for /v1/recheck
(5_000) — the same mirror discipline as app/fleet_pass.py's price
table. The tests pin every figure.

Additive only: the HTTP layer adds one top-level
``verdict_row_loop`` field to a successful delivery dict. Error
responses and the unpaid 402 challenge never carry it.

Next-20 #1 + #5 (2026-11-03): the block now attaches at the delivery
layer in app/main.py's x402 middleware, so every successful paid
delivery on this stall's priced route carries it — per-call and
Fleet-Pass funded alike — and a RETURNING payer's block is
personalised: a ``returning_payer`` section carries their own
recorded history with this stall (calls, distinct days, total
spent — see CheckStore.record_payer_delivery in app/store.py) and
an adaptive Recheck-pack offer with deep links pre-binding the
pack / webhook offers to their identity. First-time payers (and
payers whose identity the delivery does not expose) get the
static block.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from .fleet_pass import LEDGER_URL

LOOP_KEY = "verdict_row_loop"

# StallRow-enforced terms, mirrored (see module docstring).
RECHECK_PACK_CREDITS = 250
RECHECK_PACK_PRICE_USD = 1.00
RECHECK_PACK_TTL_DAYS = 90
RECHECK_SINGLE_PRICE_USD = 0.005
FLEET_PASS_PRICE_USD = 5.00
FLEET_PASS_20_PRICE_USD = 20.00
FLEET_PASS_TTL_DAYS = 90


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


def _returning_payer_block(payer: dict, route: str | None) -> dict:
    """The personalised section for a returning payer. `payer` is a
    CheckStore payer-history snapshot (totals AFTER the current
    delivery was recorded); every figure in the adaptive offer line
    is derived from that snapshot or the mirrored enforcing
    constants above. The repeat products live on StallRow, so the
    deep links are StallRow URLs pre-binding the offer to this
    payer's identity."""
    ledger = LEDGER_URL.rstrip("/")
    identity = payer["payer_key"]
    pack_usd = RECHECK_PACK_PRICE_USD
    per_check_usd = pack_usd / RECHECK_PACK_CREDITS
    route_calls = (
        payer.get("routes", {}).get(route, {}).get("calls", 0) if route else 0
    )
    total_spent_usd = payer["total_units"] / 1_000_000
    line = (
        f"Returning payer: this identity has paid for "
        f"{payer['calls']} deliveries at this stall "
        f"({route_calls} on this route, "
        f"{payer['distinct_days']} distinct day(s), "
        f"${total_spent_usd:.3f} total) — the Recheck pack does "
        f"{RECHECK_PACK_CREDITS} checks for ${pack_usd:.2f} "
        f"(${per_check_usd:.3f} a check instead of "
        f"${RECHECK_SINGLE_PRICE_USD:.3f} per Recheck call), and a "
        f"Fleet Pass makes this check debit its listed price from "
        f"a prepaid balance instead of settling per call."
    )
    return {
        "identity": identity,
        "identity_kind": (
            "evm_wallet" if identity.startswith("0x") else "funding_credential"
        ),
        "calls": payer["calls"],
        "calls_this_route": route_calls,
        "distinct_days": payer["distinct_days"],
        "total_spent_usd": total_spent_usd,
        "first_seen": _iso(payer["first_seen_epoch"]),
        "last_seen": _iso(payer["last_seen_epoch"]),
        "adaptive_offer": {
            "line": line,
            "offer_id": "recheck_pack",
            "route": "/v1/recheck/pack",
            "url": f"{ledger}/v1/recheck/pack",
            "price_usd": pack_usd,
            "credits": RECHECK_PACK_CREDITS,
            "per_check_usd": per_check_usd,
            "per_call_price_usd": RECHECK_SINGLE_PRICE_USD,
            "deep_link": (
                f"{ledger}/v1/recheck/pack?payer={quote(identity, safe='')}"
            ),
            "webhook_deep_link": (
                f"{ledger}/v1/recheck/webhooks?payer={quote(identity, safe='')}"
            ),
        },
    }


def loop_offer(payer: dict | None = None, route: str | None = None) -> dict:
    """The loop block for a RouteVerdict delivery. Repeat products
    are StallRow routes, so their entries carry absolute URLs. With
    a `payer` history snapshot (a returning payer), the block gains
    a personalised ``returning_payer`` section; without one it is
    the static Play 1 block, byte-for-byte the original shape."""
    ledger = LEDGER_URL.rstrip("/")
    block = {
        "about": (
            "Paying per call to check the same routes on a loop? "
            "Fund a Fleet Pass once and this check debits its listed "
            "$0.05 from the balance — or watch an endpoint with "
            "Recheck at $0.004 per change-check and get a webhook "
            "when it changes."
        ),
        "discovery": "/.well-known/verdict-row.json",
        "offers": [
            {
                "id": "fleet_pass",
                "route": "/v1/fleet-pass",
                "url": f"{ledger}/v1/fleet-pass",
                "method": "POST",
                "price_usd": FLEET_PASS_PRICE_USD,
                "what_it_is": (
                    "A prepaid USDC balance held on StallRow that "
                    "spends across all five Verdict Row stalls at "
                    "their listed per-route prices — including this "
                    "service's /v1/check at $0.05. Present the pass "
                    "id in the X-Fleet-Pass header and calls debit "
                    "the balance instead of settling per call."
                ),
                "terms": {
                    "ttl_days": FLEET_PASS_TTL_DAYS,
                    "balance_route": "/v1/fleet-pass/balance",
                    "ledger": ledger,
                },
            },
            {
                "id": "fleet_pass_20",
                "route": "/v1/fleet-pass-20",
                "url": f"{ledger}/v1/fleet-pass-20",
                "method": "POST",
                "price_usd": FLEET_PASS_20_PRICE_USD,
                "what_it_is": (
                    "The $20 Fleet Pass: the same prepaid balance "
                    "ledger and terms as /v1/fleet-pass, sized as a "
                    "framework working balance."
                ),
                "terms": {
                    "ttl_days": FLEET_PASS_TTL_DAYS,
                    "balance_route": "/v1/fleet-pass/balance",
                    "ledger": ledger,
                },
            },
            {
                "id": "recheck_pack",
                "route": "/v1/recheck/pack",
                "url": f"{ledger}/v1/recheck/pack",
                "method": "POST",
                "price_usd": RECHECK_PACK_PRICE_USD,
                "what_it_is": (
                    "250 Recheck change-checks against ONE endpoint "
                    "URL on StallRow, bought once and spent via POST "
                    "/v1/recheck with the pack_id — change-detection "
                    "for endpoints you would otherwise pay to "
                    "re-check by hand."
                ),
                "terms": {
                    "credits": RECHECK_PACK_CREDITS,
                    "ttl_days": RECHECK_PACK_TTL_DAYS,
                    "per_check_usd": RECHECK_PACK_PRICE_USD / RECHECK_PACK_CREDITS,
                    "per_call_price_usd": RECHECK_SINGLE_PRICE_USD,
                    "bound_to": "one endpoint URL, fixed at purchase",
                    "balance_route": "/v1/recheck/pack-balance",
                    "ledger": ledger,
                },
            },
            {
                "id": "recheck_change_webhook",
                "route": "/v1/recheck/webhooks",
                "url": f"{ledger}/v1/recheck/webhooks",
                "method": "POST",
                "price_usd": 0,
                "what_it_is": (
                    "Register a callback for Recheck CHANGED events "
                    "on StallRow (feed-wide, one resource URL, or "
                    "one origin) and get HMAC-SHA256-signed "
                    "deliveries when a watched resource changes, "
                    "instead of polling with paid checks."
                ),
                "terms": {
                    "gate": (
                        "exactly one funding credential: a live "
                        "Recheck pack_id, or a fleet_pass_id with "
                        "balance for at least one Recheck check "
                        "($0.005); a pack-funded subscription must "
                        "filter to exactly the pack's URL"
                    ),
                    "registration_debited": False,
                    "deliveries_debited": False,
                    "ledger": ledger,
                },
            },
            {
                "id": "recheck_feed",
                "route": "/v1/recheck/feed",
                "url": f"{ledger}/v1/recheck/feed",
                "method": "GET",
                "price_usd": 0,
                "what_it_is": (
                    "Free public JSONL feed of recent Recheck "
                    "results on StallRow — poll it instead of "
                    "re-paying for checks already run."
                ),
                "terms": {"ledger": ledger},
            },
        ],
    }
    if payer is not None:
        block["returning_payer"] = _returning_payer_block(payer, route)
    return block


def attach_loop_offer(result, payer: dict | None = None, route: str | None = None):
    """Add the loop block to a successful delivery dict. Anything
    that is not a dict (error JSONResponses) passes through
    untouched, so failures never carry the offer."""
    if isinstance(result, dict):
        result[LOOP_KEY] = loop_offer(payer=payer, route=route)
    return result
