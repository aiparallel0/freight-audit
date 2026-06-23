"""
Tests for the metering + billing layer (monetization scaffold).

Pricing/isolation use the store directly; charge() is mocked (no live call); the
API metering path uses TestClient. Money is asserted in integer cents.
"""
import io
import json
import urllib.request

import pytest

from freight_audit import (
    MatchEngine, RateConfirmation, CarrierInvoice, LineItem, to_cents,
)
from freight_audit.store import Store
from freight_audit.billing import Pricing, bill_tenant, charge


def _overcharge_result(load_id):
    e = MatchEngine()
    rc = RateConfirmation(load_id, "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    inv = CarrierInvoice("INV", load_id, "C", to_cents("$1,200.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,200.00"))])
    return e.match(rc, inv, None)


def _meter(store, tenant, result):
    over = sum(f.money_impact_cents for f in result.findings if f.money_impact_cents > 0)
    rec = -sum(f.money_impact_cents for f in result.findings if f.money_impact_cents < 0)
    store.save_result(result, tenant_id=tenant)
    store.record_usage(tenant, result.load_id, over, rec)
    return over, rec


def test_bill_tenant_is_integer_cents(tmp_path):
    s = Store(str(tmp_path / "b.db"))
    over1, _ = _meter(s, "acme", _overcharge_result("L1"))
    over2, _ = _meter(s, "acme", _overcharge_result("L2"))
    value = over1 + over2
    pricing = Pricing(base_fee_cents=50, pct_savings=0.25)

    b = bill_tenant(s, "acme", pricing)
    assert b["audits"] == 2
    assert b["overpayment_caught_cents"] == value
    assert b["value_cents"] == value
    expected = 50 * 2 + int(0.25 * value)
    assert b["billing_cents"] == expected and isinstance(b["billing_cents"], int)
    s.close()


def test_usage_is_tenant_isolated(tmp_path):
    s = Store(str(tmp_path / "b.db"))
    _meter(s, "acme", _overcharge_result("LA"))
    _meter(s, "globex", _overcharge_result("LB"))
    assert bill_tenant(s, "acme")["audits"] == 1
    assert bill_tenant(s, "globex")["audits"] == 1
    # globex's bill never reflects acme's usage
    assert bill_tenant(s, "globex")["overpayment_caught_cents"] == \
        bill_tenant(s, "acme")["overpayment_caught_cents"]   # same scenario, separate rows
    s.close()


class _FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def getcode(self):
        return 200


def test_charge_posts_to_env_endpoint(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setenv("FREIGHT_AUDIT_BILLING_URL", "https://pay.example/charge")
    monkeypatch.setenv("FREIGHT_AUDIT_BILLING_TOKEN", "sk-test")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    out = charge(14600, "acme")
    assert captured["url"] == "https://pay.example/charge"
    assert json.loads(captured["data"].decode()) == {
        "tenant": "acme", "amount_cents": 14600, "currency": "usd"}
    assert captured["headers"]["authorization"] == "Bearer sk-test"
    assert out["status"] == 200


def test_charge_requires_endpoint(monkeypatch):
    monkeypatch.delenv("FREIGHT_AUDIT_BILLING_URL", raising=False)
    with pytest.raises(ValueError):
        charge(100, "acme")


def test_api_meters_and_bills_per_tenant(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from freight_audit.api import app
    from freight_audit.security import KeyStore

    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "audit.db"))
    ks = KeyStore(str(tmp_path / "keys.json"))
    key_a = ks.issue("a", tenant_id="acme")
    key_b = ks.issue("b", tenant_id="globex")
    c = TestClient(app)

    bundle = {
        "rate_confirmation": {"load_id": "L-API1", "agreed_total": "$1,000.00",
                              "line_items": [{"description": "Linehaul", "amount": "$1,000.00"}]},
        "invoice": {"invoice_number": "I1", "load_id": "L-API1", "carrier_name": "C",
                    "billed_total": "$1,200.00",
                    "line_items": [{"description": "Linehaul", "amount": "$1,200.00"}]},
    }
    assert c.post("/audit", json=bundle, headers={"X-API-Key": key_a}).status_code == 200

    bill_a = c.get("/usage", headers={"X-API-Key": key_a}).json()
    assert bill_a["audits"] == 1 and bill_a["overpayment_caught_cents"] > 0
    # tenant B has no usage -> empty bill (isolation)
    assert c.get("/usage", headers={"X-API-Key": key_b}).json()["audits"] == 0
    # health check reports db + version
    h = c.get("/healthz").json()
    assert h["status"] == "ok" and h["db"] == "ok" and h["version"]
