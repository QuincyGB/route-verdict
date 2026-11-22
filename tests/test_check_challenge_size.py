"""Regression pin for the 2026-11-22 token-verdict /v1/pre-trade outage.

Root cause (fleet-wide lesson): the unpaid 402 challenge travels
base64-encoded in the PAYMENT-REQUIRED response header, and the
Bazaar extension embeds the route's output schema in that challenge.
Adding the W2 next_check/evidence properties to an output schema
pushed token-verdict pre-trade's live header past the Railway
edge's ~16 KB response-header limit — the edge dropped the response
and every unpaid probe got an empty reply.

RouteVerdict's /v1/check live header is 13,980 chars — the closest
to the line in the fleet after pre-trade. The W2 upgrades therefore
attach in the delivery path only, never in discovery output
schemas. This test pins the budget so no future schema growth
re-crosses it silently: the paid route's PAYMENT-REQUIRED header
must stay under 15,000 chars in the test environment (the budget
keeps real headroom under the edge limit).
"""

from fastapi.testclient import TestClient

from app import main

CASES = (
    ("/v1/check", {"resource_url": "https://target.example.com/v1/thing"}),
)

HEADER_BUDGET = 15_000


def test_402_challenge_headers_stay_under_edge_limit():
    client = TestClient(main.app, raise_server_exceptions=False)
    for path, payload in CASES:
        r = client.post(path, json=payload)
        assert r.status_code == 402, (path, r.text)
        header = r.headers.get("payment-required")
        assert header, f"{path}: payment-required header missing"
        assert len(header) < HEADER_BUDGET, (
            f"{path}: payment-required header is {len(header)} chars "
            f"(budget {HEADER_BUDGET}); an oversized challenge header "
            "is dropped by the Railway edge as an empty reply"
        )
