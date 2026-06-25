"""
Auth hardening tests (PROMPTS #5): role enforcement, per-key rate limiting (429),
key rotation, and expiry (401). Via FastAPI TestClient; no live server.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import freight_audit.api as apimod  # noqa: E402
from freight_audit.api import app  # noqa: E402
from freight_audit.security import KeyStore, hash_key  # noqa: E402
from freight_audit.ratelimit import InMemoryRateLimiter  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "db.sqlite"))
    apimod._limiter = InMemoryRateLimiter(limit_per_min=10000)   # high; per-test isolation
    return TestClient(app), KeyStore(str(tmp_path / "keys.json"))


def test_role_enforced_on_key_management(env):
    c, ks = env
    api_key = ks.issue("api", "acme", role="api")
    admin_key = ks.issue("admin", "acme", role="admin")
    # an 'api' role key may not manage keys
    assert c.post("/keys", json={"role": "api"}, headers={"X-API-Key": api_key}).status_code == 403
    # an 'admin' key may
    r = c.post("/keys", json={"role": "reviewer"}, headers={"X-API-Key": admin_key})
    assert r.status_code == 200 and r.json()["role"] == "reviewer"


def test_rate_limit_returns_429(env):
    c, ks = env
    apimod._limiter = InMemoryRateLimiter(limit_per_min=2)
    key = ks.issue("api", "acme", role="api")
    assert c.get("/usage", headers={"X-API-Key": key}).status_code == 200
    assert c.get("/usage", headers={"X-API-Key": key}).status_code == 200
    assert c.get("/usage", headers={"X-API-Key": key}).status_code == 429


def test_key_rotation_revokes_old(env):
    c, ks = env
    key = ks.issue("api", "acme", role="api")
    new_key = c.post("/keys/rotate", headers={"X-API-Key": key}).json()["api_key"]
    assert new_key and new_key != key
    assert c.get("/usage", headers={"X-API-Key": key}).status_code == 401      # old revoked
    assert c.get("/usage", headers={"X-API-Key": new_key}).status_code == 200  # new works


def test_expired_key_rejected(env):
    c, ks = env
    key = ks.issue("api", "acme", role="api")
    ks.keys[hash_key(key)]["expires_at"] = "2000-01-01T00:00:00+00:00"
    ks._save()
    assert c.get("/usage", headers={"X-API-Key": key}).status_code == 401
