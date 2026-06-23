"""
Signup / account-creation tests (PROMPTS #4): successful signup issues a working
per-tenant key; invalid email -> 400; duplicate email -> 409.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import freight_audit.api as apimod  # noqa: E402
from freight_audit.api import app  # noqa: E402
from freight_audit.ratelimit import InMemoryRateLimiter  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "db.sqlite"))
    apimod._limiter = InMemoryRateLimiter(limit_per_min=10000)
    return TestClient(app)


def test_signup_creates_usable_account(client):
    r = client.post("/signup", json={"email": "ops@acme.co"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant"] == "ops@acme.co" and body["role"] == "admin"
    # the issued key authenticates against a protected endpoint
    assert client.get("/usage", headers={"X-API-Key": body["api_key"]}).status_code == 200


def test_signup_rejects_invalid_email(client):
    assert client.post("/signup", json={"email": "not-an-email"}).status_code == 400


def test_signup_rejects_duplicate_email(client):
    assert client.post("/signup", json={"email": "dup@acme.co"}).status_code == 200
    assert client.post("/signup", json={"email": "dup@acme.co"}).status_code == 409
