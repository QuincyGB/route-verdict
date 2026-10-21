"""RouteVerdict — x402 pay-per-call trust & liveness checks for x402 APIs.

Paid:   POST /v1/check   (USDC via x402 v2 "exact" scheme on Base)
Free:   GET  /, /health, /quote, /.well-known/x402, /skill.md, /llms.txt

The check itself is observation-only: it probes a target endpoint WITHOUT
paying it and reports liveness, terms, discovery-schema quality, catalog
presence, and a diff against the previous check.
"""

from __future__ import annotations

import logging
import os

# Some shells/hosts export a no_proxy list containing bracketed IPv6
# entries ("[::1]"). The x402 SDK's httpx-based facilitator client parses
# that list as URLs and dies with "Invalid port: ':1]'", which turns every
# unpaid call into a 500 instead of a 402 — fatal optics for a trust
# service. Scrub just the bracketed entries before any SDK client is
# constructed. (Token Verdict only scrubbed this in its test conftest;
# RouteVerdict scrubs in-process so any host shell is safe.)
for _var in ("no_proxy", "NO_PROXY"):
    _val = os.environ.get(_var)
    if _val:
        _kept = [e for e in _val.split(",") if "[" not in e and "]" not in e]
        os.environ[_var] = ",".join(_kept)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption, RouteConfig
from x402.http.middleware import fastapi_payment_middleware
from x402.mechanisms.evm.exact.server import ExactEvmScheme
from x402.server import x402ResourceServer

from .checker import run_check
from .config import load_settings
from .discovery import bazaar_extension, llms_txt, skill_md, x402_manifest
from .store import CheckStore

log = logging.getLogger("route-verdict")
logging.basicConfig(level=logging.INFO)

settings = load_settings()  # raises if payout wallet is a placeholder

# --- x402 wiring -------------------------------------------------------------
# The facilitator is chosen by X402_FACILITATOR_URL alone (plus CDP keys for
# the CDP branch). Cataloging is settlement-driven per facilitator — each
# index lists only settlements through its OWN facilitator — so the env
# choice decides which catalog a settled payment feeds:
#   * CDP (api.cdp.coinbase.com, needs CDP_API_KEY_ID/SECRET): CDP Bazaar.
#   * PayAI (facilitator.payai.network, no key for exact): PayAI index.
#   * default keyless ultravioletadao: settles Base, feeds no major index.
# One deployment settles through one facilitator at a time; to appear in
# both indexes, settle at least one call under each configuration.
if settings.use_cdp:
    from .cdp_auth import cdp_facilitator_config

    assert settings.cdp_api_key_id and settings.cdp_api_key_secret
    facilitator = HTTPFacilitatorClient(
        cdp_facilitator_config(settings.cdp_api_key_id, settings.cdp_api_key_secret)
    )
    log.info("facilitator: CDP (Bazaar cataloging path)")
elif settings.use_payai:
    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=settings.facilitator_url))
    log.info("facilitator: PayAI (PayAI index cataloging path)")
else:
    facilitator = HTTPFacilitatorClient(FacilitatorConfig(url=settings.facilitator_url))
    log.info("facilitator: %s (%s)", settings.facilitator_url, settings.network)

resource_server = x402ResourceServer(facilitator)
resource_server.register(settings.network, ExactEvmScheme())

CHECK_DESCRIPTION = (
    "Send any x402 resource URL, get a trust verdict on the endpoint "
    "itself. Returns TRUSTED / CAUTION / AVOID with a 0-100 score from an "
    "observation-only live probe (never pays the target): payment terms, "
    "Bazaar discovery-schema quality, CDP Bazaar and PayAI catalog "
    "presence, plus a diff against the previous check. Use this when an "
    "agent is about to pay an unfamiliar x402 endpoint and needs to know "
    "it is alive, honestly described, and unchanged first."
)

routes = {
    "POST /v1/check": RouteConfig(
        accepts=[
            PaymentOption(
                scheme="exact",
                pay_to=settings.pay_to,
                price=settings.price_usdc,
                network=settings.network,
            )
        ],
        description=CHECK_DESCRIPTION,
        mime_type="application/json",
        service_name=settings.service_name,  # ≤32 chars, forwarded to Bazaar
        tags=["x402", "trust", "liveness", "monitoring", "verification", "agents"],
        extensions={"bazaar": bazaar_extension()},
    ),
}

# --- app ---------------------------------------------------------------------
app = FastAPI(title="route-verdict", version="1.0.0")


@app.middleware("http")
async def x402_middleware(request: Request, call_next):
    return await fastapi_payment_middleware(routes, resource_server)(request, call_next)


class CheckRequest(BaseModel):
    resource_url: str = Field(description="Full URL of the x402 resource to audit")


# The diff memory. Created lazily so importing the app (tests, tooling)
# never touches the filesystem until a real check runs.
_store: CheckStore | None = None


def get_store() -> CheckStore:
    global _store
    if _store is None:
        _store = CheckStore(settings.db_path)
    return _store


def _public_base(request: Request) -> str:
    """Public base URL of this service. Railway terminates TLS at the edge,
    so the request scheme arrives as http — indexers and agents need https."""
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RouteVerdict — trust &amp; liveness checks for x402 endpoints</title>
<meta name="description" content="Before an agent pays an unfamiliar x402 endpoint, it pays $0.05 to ask RouteVerdict: is it alive, what does it charge, is its discovery schema real, is it cataloged — and has anything changed since last time?">
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         max-width: 760px; margin: 40px auto; padding: 0 20px 60px;
         line-height: 1.55; color: #16202a; background: #fbfcfd; }
  h1 { font-size: 2rem; margin-bottom: .2em; }
  .tag { font-size: 1.1rem; color: #33475b; margin-top: 0; }
  code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  pre { background: #0f1b26; color: #dcebf5; padding: 14px 16px;
        border-radius: 8px; overflow-x: auto; font-size: .85rem; }
  table { border-collapse: collapse; width: 100%; margin: 1em 0; }
  td, th { border-bottom: 1px solid #dde5ec; padding: 6px 8px;
           text-align: left; vertical-align: top; }
  .pill { display: inline-block; background: #e7f0e9; color: #1d5c33;
          border-radius: 999px; padding: 2px 10px; font-size: .85rem; }
  a { color: #0b5cad; }
  .limits { background: #fdf6e5; border: 1px solid #ecd9a0;
            border-radius: 8px; padding: 10px 14px; }
</style>
</head>
<body>
<h1>RouteVerdict</h1>
<p class="tag">The trust layer for x402 itself. Before an agent pays an
unfamiliar x402 endpoint, it pays <strong>$0.05</strong> to ask RouteVerdict
first: is it alive, what does it charge, is its discovery schema real, is it
cataloged — and has anything changed since last time? No accounts, no API
keys: <em>payment is the authentication.</em></p>

<p><span class="pill">Live on Base mainnet</span>
<span class="pill">x402 v2 · exact scheme</span>
<span class="pill">$0.05 USDC / check</span>
<span class="pill">Observation only — never pays the target</span></p>

<h2>What a check returns</h2>
<p>One <code>POST /v1/check</code> with a resource URL returns:</p>
<ul>
  <li>a verdict — <strong>TRUSTED</strong>, <strong>CAUTION</strong>, or
      <strong>AVOID</strong> — with a 0-100 trust score and every deduction
      itemized</li>
  <li><strong>liveness</strong> — the endpoint probed live, unpaid: HTTP
      status, latency, whether its 402 parses as valid x402</li>
  <li><strong>terms</strong> — price, network, asset and payTo from the
      live challenge</li>
  <li><strong>schema quality</strong> — whether the Bazaar discovery output
      schema types real fields or is hollow (<code>{"type", "example"}</code>
      only — the shape 87.8% of listings ship)</li>
  <li><strong>catalog presence</strong> — listed in the CDP Bazaar? In the
      PayAI index? (a failed lookup reports <code>unknown</code>, never a
      fabricated "not listed")</li>
  <li><strong>diff</strong> — what changed since the previous check of the
      same URL: price, payTo, schema score, listing presence, liveness</li>
</ul>

<h2>How to call it</h2>
<pre>curl -X POST https://route-verdict-production.up.railway.app/v1/check \\
  -H 'content-type: application/json' \\
  -d '{"resource_url":"https://some-service.example.com/v1/endpoint"}'</pre>
<p>Unpaid calls return an HTTP 402 x402 challenge. Pay $0.05 USDC on Base
with any x402 v2 client, retry with the payment header, and the report
comes back. There is no signup and no key to manage — the settlement is
the credential.</p>

<h2>Endpoints</h2>
<table>
  <tr><th>Route</th><th>Price</th><th>Purpose</th></tr>
  <tr><td><code>POST /v1/check</code></td><td>$0.05</td>
      <td>Trust &amp; liveness check for one x402 resource URL (paid, x402)</td></tr>
  <tr><td><code>GET /quote</code></td><td>free</td>
      <td>Price, asset, network, pay-to wallet</td></tr>
  <tr><td><code>GET /.well-known/x402</code></td><td>free</td>
      <td>Machine-readable service manifest</td></tr>
  <tr><td><code>GET /skill.md</code></td><td>free</td>
      <td>Documentation written for agents</td></tr>
  <tr><td><code>GET /llms.txt</code></td><td>free</td>
      <td>Compact agent index</td></tr>
  <tr><td><code>GET /health</code></td><td>free</td><td>Liveness</td></tr>
</table>
<p>Source code:
<a href="https://github.com/QuincyGB/route-verdict">github.com/QuincyGB/route-verdict</a>
(public source — the scoring is deterministic and fully inspectable, and
the test suite ships with it). Sibling service:
<a href="https://github.com/QuincyGB/token-verdict">Token Verdict</a>,
risk verdicts for token contracts.</p>

<div class="limits">
<strong>Honest limits:</strong> RouteVerdict reads what an endpoint
publishes and what the public catalogs hold. It never settles a test
payment to the target, does not grade the paid payload's quality, and
cannot see inside the target's wallet — the diff flags payTo changes,
it does not explain them.
</div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def landing():
    return LANDING_HTML


@app.get("/health")
async def health():
    return {"ok": True, "service": settings.service_name}


@app.get("/quote")
async def quote():
    """Free: what the paid endpoint costs and where it settles."""
    return {
        "service": settings.service_name,
        "endpoint": "/v1/check",
        "price": settings.price_usdc,
        "asset": "USDC",
        "network": settings.network,
        "payTo": settings.pay_to,
        "protocol": "x402 v2 (exact scheme)",
    }


@app.get("/.well-known/x402")
async def well_known(request: Request):
    return JSONResponse(x402_manifest(settings, _public_base(request)))


@app.get("/skill.md", response_class=PlainTextResponse)
async def skill(request: Request):
    return skill_md(settings, _public_base(request))


@app.get("/llms.txt", response_class=PlainTextResponse)
async def llms(request: Request):
    return llms_txt(settings, _public_base(request))


@app.post("/v1/check")
async def check(req: CheckRequest):
    """Paid endpoint — unreachable until the x402 middleware verifies payment."""
    resource_url = req.resource_url.strip()
    try:
        return await run_check(
            resource_url,
            get_store(),
            check_timeout=settings.fetch_timeout_s,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception:  # noqa: BLE001 — never leak tracebacks to callers
        log.exception("check failed for %s", resource_url)
        return JSONResponse({"error": "check failed"}, status_code=502)


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    main()
