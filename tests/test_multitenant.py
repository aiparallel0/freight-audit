"""
Tests for multi-tenant auth + tenant-scoped storage (PROMPTS #7):
cross-tenant access is impossible, and a legacy single-tenant DB migrates in place.
"""
import os
import sqlite3

import pytest

from freight_audit import (
    MatchEngine, RateConfirmation, CarrierInvoice, LineItem, to_cents,
)
from freight_audit.store import Store
from freight_audit.security import KeyStore


def _result(load_id):
    e = MatchEngine()
    rc = RateConfirmation(load_id, "B", "C", "O", "D", to_cents("$100.00"),
                          line_items=[LineItem("Linehaul", to_cents("$100.00"))])
    inv = CarrierInvoice("INV", load_id, "C", to_cents("$100.00"),
                         line_items=[LineItem("Linehaul", to_cents("$100.00"))])
    return e.match(rc, inv, None)


def test_store_tenant_isolation(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.save_result(_result("L-A1"), tenant_id="acme")
    s.save_result(_result("L-B1"), tenant_id="globex")

    # each tenant sees only its own load
    assert s.get_load("L-A1", tenant_id="acme") is not None
    assert s.get_load("L-A1", tenant_id="globex") is None          # B cannot read A's
    assert [l["load_id"] for l in s.all_loads(tenant_id="globex")] == ["L-B1"]
    assert s.report(tenant_id="acme")["loads"] == 1
    assert s.report(tenant_id="globex")["loads"] == 1

    # decisions + audit trail are tenant-scoped too
    s.record_decision("L-A1", "approved", actor="amy", tenant_id="acme")
    assert s.decisions_for("L-A1", tenant_id="acme")
    assert s.decisions_for("L-A1", tenant_id="globex") == []
    assert s.audit_trail("L-A1", tenant_id="globex") == []
    s.close()


def test_legacy_single_tenant_db_migrates(tmp_path):
    db = str(tmp_path / "legacy.db")
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE loads (
            load_id TEXT PRIMARY KEY, client TEXT, severity TEXT,
            auto_approvable INTEGER, net_impact_cents INTEGER,
            findings_json TEXT, processed_at TEXT NOT NULL);
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, load_id TEXT NOT NULL,
            decision TEXT NOT NULL, actor TEXT NOT NULL, decided_at TEXT NOT NULL, note TEXT);
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
            action TEXT NOT NULL, load_id TEXT, detail TEXT);
        INSERT INTO loads (load_id, client, severity, auto_approvable, net_impact_cents,
            findings_json, processed_at)
          VALUES ('L-OLD', 'legacy', 'warn', 0, 12345, '[]', '2026-01-01T00:00:00+00:00');
        """)
    con.commit()
    con.close()

    s = Store(db)                                       # opens -> migrates in place
    row = s.get_load("L-OLD", tenant_id="default")     # legacy rows land under 'default'
    assert row is not None and row["net_impact_cents"] == 12345
    assert s.get_load("L-OLD", tenant_id="acme") is None
    assert s._has_column("decisions", "tenant_id") and s._has_column("audit_log", "tenant_id")
    s.close()


def test_api_rejects_cross_tenant_reads(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from freight_audit.api import app

    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "audit.db"))
    ks = KeyStore(str(tmp_path / "keys.json"))
    key_a = ks.issue("a", tenant_id="acme")
    key_b = ks.issue("b", tenant_id="globex")
    c = TestClient(app)

    bundle = {
        "rate_confirmation": {"load_id": "L-API1", "agreed_total": "$100.00",
                              "line_items": [{"description": "Linehaul", "amount": "$100.00"}]},
        "invoice": {"invoice_number": "I1", "load_id": "L-API1", "carrier_name": "C",
                    "billed_total": "$100.00",
                    "line_items": [{"description": "Linehaul", "amount": "$100.00"}]},
    }
    assert c.post("/audit", json=bundle, headers={"X-API-Key": key_a}).status_code == 200
    # tenant A can read its load; tenant B cannot (scoped to globex -> 404)
    assert c.get("/loads/L-API1", headers={"X-API-Key": key_a}).status_code == 200
    assert c.get("/loads/L-API1", headers={"X-API-Key": key_b}).status_code == 404
