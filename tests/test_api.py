"""
Tests for the REST API (PROMPTS #3), via FastAPI TestClient (no live server).
Covers: auth required on writes/reads, a successful audit, and persist-then-retrieve.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from freight_audit.api import app  # noqa: E402
from freight_audit.security import KeyStore  # noqa: E402

CLEAN_BUNDLE = {
    "load_id": "L-1",
    "rate_confirmation": {
        "load_id": "L-1", "agreed_total": "$1,000.00",
        "line_items": [{"description": "Linehaul", "amount": "$1,000.00"}]},
    "invoice": {
        "invoice_number": "INV1", "load_id": "L-1", "carrier_name": "C",
        "billed_total": "$1,000.00",
        "line_items": [{"description": "Linehaul", "amount": "$1,000.00"}]},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "audit.db"))
    key = KeyStore(str(tmp_path / "keys.json")).issue("test")
    return TestClient(app), key


def test_healthz_is_open(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_audit_requires_api_key(client):
    c, _ = client
    assert c.post("/audit", json=CLEAN_BUNDLE).status_code == 401


def test_audit_with_key_returns_findings_and_severity(client):
    c, key = client
    r = c.post("/audit", json=CLEAN_BUNDLE, headers={"X-API-Key": key})
    assert r.status_code == 200
    data = r.json()
    assert data["load_id"] == "L-1"
    assert "severity" in data and isinstance(data["findings"], list)
    assert data["auto_approvable"] is True            # clean bundle


def test_load_is_persisted_then_retrievable(client):
    c, key = client
    h = {"X-API-Key": key}
    assert c.post("/audit", json=CLEAN_BUNDLE, headers=h).status_code == 200
    r = c.get("/loads/L-1", headers=h)
    assert r.status_code == 200 and r.json()["load_id"] == "L-1"
    assert c.get("/loads/L-1").status_code == 401      # read also gated
    assert c.get("/loads/NOPE", headers=h).status_code == 404
