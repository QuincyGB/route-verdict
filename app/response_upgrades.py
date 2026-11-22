"""Response upgrades for paid RouteVerdict deliveries (W2 build):
every paid response carries two extra top-level blocks.

``next_check`` — one suggested follow-up route (name + path + price),
machine-readable, so a buyer agent can chain the natural next check
without scraping docs. A route check surfaces the target endpoint's
payTo wallet; the natural follow-up is vetting that counterparty, so
/v1/check points at StallRow's /v1/counterparty-report ($0.10 —
stall-row app/config.py PRICE_UNITS), with service + absolute url
since it lives on a sibling stall.

``evidence`` — {settlement_tx, input_hash, timestamp}:
  * input_hash — sha256 of the canonical request input (the route's
    request model, JSON with sorted keys and compact separators), so
    a buyer can prove exactly which input a delivery answered.
  * timestamp — UTC delivery time (ISO 8601, Z suffix).
  * settlement_tx — the on-chain settlement transaction. It is NOT
    available at handler time: the x402 middleware settles AFTER the
    handler returns, so it is attached centrally at the delivery
    layer (app/main.py's _attach_loop_offer, the path every funding
    route passes through) and filled there from the settlement
    receipt header when one is present. Fleet-Pass-funded deliveries
    carry no per-call receipt, so their settlement_tx stays None —
    the pass debit is their settlement record.

Attached at the delivery layer only: error responses, the unpaid
402 challenge, and the fleet-internal unpaid path (which calls the
handler directly, bypassing the middleware) never carry these
blocks.

HEADER-BUDGET RULE (2026-11-22 pre-trade outage): these blocks are
attached to paid response BODIES only. They are deliberately NOT
added to discovery.py output schemas — the Bazaar extension embeds
the output schema in the base64 PAYMENT-REQUIRED header and an
oversized schema pushes the challenge past the Railway edge's
~16 KB header limit. tests/test_check_challenge_size.py pins the
budget.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .fleet_pass import LEDGER_URL

# Follow-up suggestions per paid route. The counterparty report
# price mirrors the enforcing config (stall-row app/config.py
# PRICE_UNITS for /v1/counterparty-report).
NEXT_CHECKS: dict[str, dict] = {
    "/v1/check": {
        "name": "counterparty-report",
        "service": "stall-row",
        "path": "/v1/counterparty-report",
        "url": f"{LEDGER_URL.rstrip('/')}/v1/counterparty-report",
        "price_usd": 0.10,
    },
}


def canonical_input_hash(input_obj: dict) -> str:
    """sha256 hex of the canonical JSON encoding of a request input."""
    canonical = json.dumps(input_obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def attach_upgrades(result, route: str, input_obj: dict, settlement_tx=None):
    """Add next_check + evidence to a successful paid delivery dict.
    Anything that is not a dict (error JSONResponses) passes through
    untouched, so failures never carry the blocks."""
    if not isinstance(result, dict):
        return result
    next_check = NEXT_CHECKS.get(route)
    if next_check is not None:
        result["next_check"] = dict(next_check)
    result["evidence"] = {
        "settlement_tx": settlement_tx,
        "input_hash": canonical_input_hash(input_obj),
        "timestamp": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    return result
