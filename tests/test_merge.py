"""
Tests for the merged backend: the frozen contract routes are served by the ONE
unified app, backed by the real multi-tenant store. Covers the persisted,
tenant-scoped queue + isolation, the open/required auth modes, and that the finished
console is served same-origin.
"""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import freight_audit.api as apimod  # noqa: E402
from api.app import app  # noqa: E402  (the merged app)
from freight_audit.security import KeyStore  # noqa: E402
from freight_audit.ratelimit import InMemoryRateLimiter  # noqa: E402

_BUNDLE_A = {
    "rate_confirmation": {"load_id": "L-MERGE-A", "agreed_total": "$1,000.00",
                          "line_items": [{"description": "Linehaul", "amount": "$1,000.00"}]},
    "invoice": {"invoice_number": "IA", "load_id": "L-MERGE-A", "carrier_name": "C",
                "billed_total": "$1,200.00",
                "line_items": [{"description": "Linehaul", "amount": "$1,200.00"}]},
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "db.sqlite"))
    for v in ("DATABASE_URL", "AUTH_REQUIRED", "STRIPE_KEY", "STRIPE_API_KEY",
              "LOADS_DIR", "CLIENT_PROFILE_PATH"):
        monkeypatch.delenv(v, raising=False)
    apimod._limiter = InMemoryRateLimiter(limit_per_min=100000)
    return TestClient(app), KeyStore(str(tmp_path / "keys.json")), monkeypatch


def test_persisted_queue_is_tenant_scoped(env):
    c, ks, _ = env
    ka = ks.issue("a", "acme", role="api")
    kb = ks.issue("b", "globex", role="api")
    # tenant A submits a custom load -> it persists and shows in A's queue
    assert c.post("/loads", json=_BUNDLE_A, headers={"X-API-Key": ka}).json()["load_id"] == "L-MERGE-A"
    a_ids = {d["load_id"] for d in c.get("/findings", headers={"X-API-Key": ka}).json()}
    assert "L-MERGE-A" in a_ids
    # tenant B has no bundles -> sees the samples demo, never A's load
    b_ids = {d["load_id"] for d in c.get("/findings", headers={"X-API-Key": kb}).json()}
    assert "L-MERGE-A" not in b_ids and "L-100482" in b_ids


def test_auth_open_by_default_then_required(env):
    c, ks, mp = env
    assert c.get("/findings").status_code == 200          # open: no key -> tenant 'default'
    mp.setenv("AUTH_REQUIRED", "1")
    assert c.get("/findings").status_code == 401          # required: no key -> 401
    key = ks.issue("a", "acme", role="api")
    assert c.get("/findings", headers={"X-API-Key": key}).status_code == 200


def test_invalid_key_rejected(env):
    c, _, _ = env
    assert c.get("/findings", headers={"X-API-Key": "fm_bogus"}).status_code == 401


def test_finished_console_is_served(env):
    c, _, _ = env
    r = c.get("/console/console.dc.html")
    assert r.status_code == 200 and len(r.content) > 1000


def test_saas_and_contract_coexist(env):
    # the same app still serves the SaaS surface
    c, _, _ = env
    assert c.get("/healthz").json()["status"] == "ok"
    assert c.post("/signup", json={"email": "merge@acme.co"}).status_code == 200
