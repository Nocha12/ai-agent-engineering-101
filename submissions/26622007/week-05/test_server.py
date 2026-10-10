"""HTTP-level auth tests for server.py, in-process (no port, no model).

    .venv/bin/python -m pytest -q test_server.py
"""
import os

os.environ.setdefault("MARKET_ADMIN_KEY", "test-admin-key")

import pytest
from starlette.testclient import TestClient

import server

LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
HDR = {"Accept": "application/json, text/event-stream"}


@pytest.fixture()
def client():
    with TestClient(server.mcp.streamable_http_app()) as c:
        yield c


def bearer(token):
    return {**HDR, "Authorization": f"Bearer {token}"}


def mint(**over):
    claims = {"nid": "neg_x", "role": "buyer", "aud": server.RESOURCE, "scope": "negotiate", **over}
    return server.market.signer.mint(claims)


def test_no_token_is_401_with_www_authenticate(client):
    r = client.post("/mcp", json=LIST, headers=HDR)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer ")


def test_token_for_another_resource_is_401(client):
    r = client.post("/mcp", json=LIST, headers=bearer(mint(aud="http://elsewhere.example/mcp")))
    assert r.status_code == 401


def test_token_without_scope_is_403(client):
    r = client.post("/mcp", json=LIST, headers=bearer(mint(scope="")))
    assert r.status_code == 403 and "insufficient_scope" in r.headers["www-authenticate"]


def test_valid_token_passes_auth(client):
    r = client.post("/mcp", json=LIST, headers=bearer(mint()))
    assert r.status_code not in (401, 403)


def test_admin_routes_need_the_admin_key(client):
    body = {"item": "x", "reserve": 1, "budget": 2, "condition": "prompt"}
    assert client.post("/admin/open", json=body).status_code == 403
    r = client.post("/admin/open", json=body, headers={"X-Admin-Key": os.environ["MARKET_ADMIN_KEY"]})
    assert r.status_code == 200 and set(r.json()["tokens"]) == {"buyer", "seller"}
