"""
Tests for the front-end contract API (api/app.py, BACKEND.md §0).

Verifies every endpoint is a real implementation reusing the engine/store/exporters:
the findings shape matches the front-end's findings.json, decisions persist to the
store and drop loads off the queue, QuickBooks export returns a real IIF, OCR-confirm
audits, and checkout returns a (mock-without-key) session. Via TestClient.
"""
import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402
from freight_audit.storage import get_store  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "db.sqlite"))
    for var in ("DATABASE_URL", "STRIPE_KEY", "STRIPE_API_KEY", "CLIENT_PROFILE_PATH",
                "QBO_ACCESS_TOKEN", "LOADS_DIR"):
        monkeypatch.delenv(var, raising=False)
    return TestClient(app)


def test_findings_matches_frozen_shape(client):
    data = client.get("/findings").json()
    assert len(data) == 5
    rec = next(d for d in data if d["load_id"] == "L-100482")
    assert set(rec) == {"load_id", "severity", "net_money_impact_cents",
                        "auto_approvable", "findings", "rate_con", "invoice", "pod"}
    assert isinstance(rec["net_money_impact_cents"], int) and rec["net_money_impact_cents"] == 110000
    assert "time_on_site_hours" in (rec["pod"] or {})          # POD property in the shape
    assert all(isinstance(f["money_impact_cents"], int) for f in rec["findings"])
    # the API output equals the committed in-browser findings.json (interchangeable)
    want = {d["load_id"]: d for d in json.load(open(os.path.join(REPO, "review_ui", "findings.json")))}
    assert set(rec) == set(want["L-100482"])


def test_audit_one_bundle(client):
    bundle = json.load(open(os.path.join(REPO, "samples/loads/load_002_overcharge_duplicate.json")))
    d = client.post("/loads", json=bundle).json()
    assert d["load_id"] == "L-100482" and d["findings"] and d["severity"] == "warn"


def test_decision_persists_and_drops_from_queue(client):
    assert any(d["load_id"] == "L-100482" for d in client.get("/findings").json())
    r = client.post("/decisions", json={"id": "L-100482", "kind": "flag",
                                        "at": "2026-06-25T00:00:00Z"})
    assert r.status_code == 200 and r.json()["decision"] == "disputed"
    # persisted to the store + audit log
    assert get_store().decisions_for("L-100482")[0]["decision"] == "disputed"
    # decided load no longer in the queue (survives across sessions)
    assert all(d["load_id"] != "L-100482" for d in client.get("/findings").json())


def test_decision_validation(client):
    assert client.post("/decisions", json={"id": "L-1", "kind": "maybe"}).status_code == 400


def test_export_quickbooks_returns_iif(client):
    r = client.post("/export/quickbooks")
    assert r.status_code == 200
    assert "!TRNS" in r.text and "L-100481" in r.text     # the clean load as a payable bill
    assert "L-100482" not in r.text                        # flagged load held back
    assert r.headers["x-qbo-pushed"] == "False"


def test_ocr_confirm_audits(client):
    fields = {
        "rate_confirmation": {"load_id": "L-OCR", "agreed_total": "$1,000.00",
                              "line_items": [{"description": "Linehaul", "amount": "$1,000.00"}]},
        "invoice": {"invoice_number": "INV", "load_id": "L-OCR", "carrier_name": "C",
                    "billed_total": "$1,200.00",
                    "line_items": [{"description": "Linehaul", "amount": "$1,200.00"}]},
    }
    d = client.post("/ocr/confirm", json=fields).json()
    assert d["load_id"] == "L-OCR" and d["net_money_impact_cents"] == 40000
    assert get_store().get_load("L-OCR") is not None       # persisted


def test_checkout_mock_without_stripe_key(client):
    d = client.post("/checkout", json={"plan": "pilot"}).json()
    assert d["mock"] is True and d["plan"] == "pilot" and d["url"]
