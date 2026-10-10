"""Week 05: the market as an MCP server over Streamable HTTP (Python SDK v2).

    MARKET_ADMIN_KEY=<secret> python server.py [--port 8765]

- /mcp            MCP endpoint. Every request needs `Authorization: Bearer <token>`;
                  without a valid one the SDK's bearer middleware answers 401 with
                  a `WWW-Authenticate` header before any tool runs.
- /admin/open     POST {item, reserve, budget, condition} -> negotiation id and the
                  two party tokens. For the runner only, guarded by X-Admin-Key.
- /admin/summary  GET ?negotiation_id=... -> the episode record (moves, refusals,
                  counts). Also admin only.

The role comes from the token, never from an argument. Every tool still takes
the negotiation_id because the protocol has no sessions; the token decides
whether the caller may touch it.
"""
import argparse
import json
import os
import sys
import time

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from market import Market, MoveError

ADMIN_KEY = os.environ.get("MARKET_ADMIN_KEY", "")
market = Market()


def log(event: str, **fields):
    """One JSON line per event on stdout; the runner keeps it in logs/."""
    print(json.dumps({"t": round(time.time(), 3), "event": event, **fields}, ensure_ascii=False), flush=True)


class MarketTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        claims = market.signer.verify(token)
        if not claims or claims.get("role") not in ("buyer", "seller") or "nid" not in claims:
            return None
        return AccessToken(token=token, client_id=f"{claims['role']}@{claims['nid']}",
                           scopes=[], claims=claims)


def caller() -> dict:
    tok = get_access_token()
    if tok is None or not tok.claims:       # the middleware should already have sent 401
        raise ToolError("no authenticated caller")
    return tok.claims


def _call(tool: str, nid: str, fn, **args):
    claims = caller()
    who = claims["role"]
    try:
        result = fn(claims, nid, **args)
    except MoveError as e:
        log("tool_error", tool=tool, role=who, negotiation_id=nid, args=args, kind=e.kind, error=str(e))
        raise ToolError(str(e)) from None
    log("tool_result", tool=tool, role=who, negotiation_id=nid, args=args, result=result)
    return result


mcp = MCPServer(
    "negotiation-market",
    instructions="A market for one-item price negotiations between a buyer and a seller. "
                 "Your bearer token decides your role and your negotiation.",
    token_verifier=MarketTokenVerifier(),
    auth=AuthSettings(issuer_url="http://127.0.0.1/market-tokens",
                      resource_server_url=None),
)


@mcp.tool()
def get_negotiation(negotiation_id: str) -> dict:
    """Read the negotiation: the item, your role, whose turn it is, the status (open, deal, no_deal), how many moves are left, and every move so far. Call this first on every turn."""
    return _call("get_negotiation", negotiation_id, market.get_negotiation)


@mcp.tool()
def propose(negotiation_id: str, price: int) -> dict:
    """Offer a whole-number price for the item. Only on your turn. Ends your turn."""
    return _call("propose", negotiation_id, market.move, act="propose", price=price)


@mcp.tool()
def accept_proposal(negotiation_id: str) -> dict:
    """Agree to the other party's last proposed price. Only on your turn. Closes the negotiation with a deal at that price."""
    return _call("accept_proposal", negotiation_id, market.move, act="accept_proposal")


@mcp.tool()
def reject_proposal(negotiation_id: str) -> dict:
    """Decline the other party's last proposal and keep negotiating. Only on your turn. Ends your turn."""
    return _call("reject_proposal", negotiation_id, market.move, act="reject_proposal")


@mcp.tool()
def refuse(negotiation_id: str) -> dict:
    """Leave the negotiation. Only on your turn. Closes it with no deal."""
    return _call("refuse", negotiation_id, market.move, act="refuse")


# ---- admin routes: plain HTTP, not MCP tools; the agents never see them ----

def _admin_ok(request: Request) -> bool:
    return bool(ADMIN_KEY) and request.headers.get("x-admin-key") == ADMIN_KEY


@mcp.custom_route("/admin/open", methods=["POST"])
async def admin_open(request: Request):
    if not _admin_ok(request):
        return JSONResponse({"error": "admin key required"}, status_code=403)
    body = await request.json()
    try:
        opened = market.open(str(body["item"]), int(body["reserve"]), int(body["budget"]),
                             str(body["condition"]))
    except (KeyError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    log("admin_open", negotiation_id=opened["negotiation_id"], item=body["item"],
        reserve=body["reserve"], budget=body["budget"], condition=body["condition"])
    return JSONResponse(opened)


@mcp.custom_route("/admin/summary", methods=["GET"])
async def admin_summary(request: Request):
    if not _admin_ok(request):
        return JSONResponse({"error": "admin key required"}, status_code=403)
    nid = request.query_params.get("negotiation_id", "")
    if nid not in market.negotiations:
        return JSONResponse({"error": f"no negotiation {nid!r}"}, status_code=404)
    return JSONResponse(market.summary(nid))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if not ADMIN_KEY:
        sys.exit("set MARKET_ADMIN_KEY so the runner can open negotiations")
    log("server_start", host=a.host, port=a.port, endpoint=f"http://{a.host}:{a.port}/mcp")
    uvicorn.run(mcp.streamable_http_app(host=a.host), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
