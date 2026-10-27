"""Tests for the Bazaar/PayAI cataloging wiring — mirroring Token Verdict's.

Pins the three things the catalog indexes actually check:
  * facilitator selection is config-driven (PayAI by URL alone, CDP only
    with URL + both keys, keyless default otherwise);
  * every description that reaches a 402 / manifest stays UNDER 500
    characters (longer descriptions break CDP payments);
  * the bazaar discovery extension carries a FULL typed output schema
    and passes the x402 SDK's own facilitator-side validators, with the
    shipped example validated against that schema.
"""

import os

import jsonschema
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings, load_settings
from app.discovery import (
    CHECK_OUTPUT_EXAMPLE,
    CHECK_OUTPUT_SCHEMA,
    bazaar_extension,
    x402_manifest,
)

CDP_URL = "https://api.cdp.coinbase.com/platform/v2/x402"
PAYAI_URL = "https://facilitator.payai.network"


def _settings(**env):
    """load_settings() with a controlled environment."""
    keys = ["X402_FACILITATOR_URL", "CDP_API_KEY_ID", "CDP_API_KEY_SECRET"]
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        return load_settings()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _bare_settings(facilitator_url, **kw):
    return Settings(
        pay_to="0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F",
        price_usdc="$0.05",
        network="eip155:8453",
        facilitator_url=facilitator_url,
        service_name="route-verdict",
        fetch_timeout_s=10,
        port=3000,
        db_path=":memory:",
        **kw,
    )


# --- facilitator selection ----------------------------------------------------

def test_payai_selected_by_url_alone():
    s = _settings(X402_FACILITATOR_URL=PAYAI_URL)
    assert s.use_payai is True
    assert s.use_cdp is False


def test_default_facilitator_is_neither():
    s = _settings()
    assert s.use_payai is False
    assert s.use_cdp is False


def test_cdp_needs_url_and_both_keys():
    assert _settings(X402_FACILITATOR_URL=CDP_URL).use_cdp is False
    assert _settings(X402_FACILITATOR_URL=CDP_URL, CDP_API_KEY_ID="id").use_cdp is False
    s = _settings(
        X402_FACILITATOR_URL=CDP_URL, CDP_API_KEY_ID="id", CDP_API_KEY_SECRET="secret"
    )
    assert s.use_cdp is True
    assert s.use_payai is False


def test_payai_url_constant_matches_settings_selection():
    from app.config import PAYAI_FACILITATOR_URL

    assert PAYAI_FACILITATOR_URL == PAYAI_URL
    assert _bare_settings(PAYAI_FACILITATOR_URL).use_payai is True


def test_defaults_are_route_verdict_terms():
    s = _settings()
    assert s.pay_to == "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
    assert s.price_usdc == "$0.05"
    assert s.network == "eip155:8453"
    assert s.service_name == "route-verdict"


# --- description length -------------------------------------------------------

def test_all_descriptions_under_500_chars():
    settings = load_settings()
    manifest = x402_manifest(settings, "https://example.test")
    descriptions = [
        main.CHECK_DESCRIPTION,
        manifest["description"],
        *[r["description"] for r in manifest["resources"]],
    ]
    for d in descriptions:
        assert len(d) < 500, f"description too long ({len(d)} chars): {d[:60]}…"
    # The quality signal the Oct 2026 crawl found 94% of listings missing.
    assert "Use this when" in main.CHECK_DESCRIPTION


# --- bazaar extension ----------------------------------------------------------

def test_extension_passes_sdk_validators():
    from x402.extensions.bazaar import (
        validate_discovery_extension,
        validate_discovery_extension_spec,
    )

    ext = bazaar_extension()
    result = validate_discovery_extension(ext)
    assert result.valid, result.errors
    spec = validate_discovery_extension_spec(ext)
    assert spec.valid, spec.errors


def test_routes_carry_the_same_extension():
    route = main.routes["POST /v1/check"]
    assert route.extensions["bazaar"] == bazaar_extension()


def test_output_schema_types_every_field():
    props = CHECK_OUTPUT_SCHEMA["properties"]
    assert set(CHECK_OUTPUT_SCHEMA["required"]) == {
        "resource_url", "checked_at", "verdict", "score", "reasons",
        "liveness", "terms", "schema_quality", "catalog", "diff",
    }
    assert props["verdict"]["enum"] == ["TRUSTED", "CAUTION", "AVOID"]
    assert props["score"]["type"] == "number"
    liveness = props["liveness"]["properties"]
    assert liveness["x402_valid"]["type"] == "boolean"
    assert liveness["http_status"]["type"] == ["integer", "null"]
    catalog = props["catalog"]["properties"]
    assert catalog["cdp_bazaar"]["properties"]["status"]["enum"] == [
        "listed", "not_listed", "unknown",
    ]
    diff = props["diff"]["properties"]
    assert diff["prior_check"]["type"] == "boolean"
    # The example must validate — and the schema must be reachable from
    # the extension itself, not hollow.
    ext = bazaar_extension()
    assert ext["info"]["output"]["schema"] == CHECK_OUTPUT_SCHEMA
    assert ext["schema"]["properties"]["output"]["properties"]["example"] == (
        CHECK_OUTPUT_SCHEMA
    )


def test_example_validates_against_output_schema():
    jsonschema.validate(instance=CHECK_OUTPUT_EXAMPLE, schema=CHECK_OUTPUT_SCHEMA)


def test_example_under_settled_sample_limit():
    """Settled's extractor silently drops published output samples over
    ~1.5 KB serialized (measured 2026-10-25: this route's 1,453 B sample
    was kept and graded as_advertised; Token Verdict's 1,721 B sample was
    dropped -> nothing_to_compare). The fleet pin is <=1,450 B; the
    2026-10-27 trim shortened two catalog detail strings (generator +
    example together) to get back under it."""
    import json

    assert len(json.dumps(CHECK_OUTPUT_EXAMPLE).encode()) <= 1450


# --- discovery surfaces stay consistent ---------------------------------------

@pytest.fixture(scope="module")
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def test_discovery_endpoints_live_and_consistent(client):
    manifest = client.get("/.well-known/x402")
    assert manifest.status_code == 200
    body = manifest.json()
    assert body["service"] == "route-verdict"
    resource = body["resources"][0]
    assert resource["price"] == "$0.05"
    assert resource["accepts"][0]["amount"] == "50000"
    assert resource["accepts"][0]["payTo"] == "0x097f9685D1e0752bF8B02d61E5Ce9be7eA0FBA7F"
    assert resource["inputSchema"]["required"] == ["resource_url"]
    assert resource["outputSchema"] == CHECK_OUTPUT_SCHEMA
    assert len(body["description"]) < 500

    assert client.get("/llms.txt").status_code == 200
    assert client.get("/skill.md").status_code == 200
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert "/v1/check" in openapi.json()["paths"]
