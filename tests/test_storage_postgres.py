"""
Postgres backend tests (PROMPTS #15 storage path). PG-gated: skipped unless
DATABASE_URL points at Postgres. The CI 'postgres' job sets DATABASE_URL and runs
this module against a real Postgres service. Unique per-test tenants keep it
isolated on a shared database.
"""
import os
import uuid

import pytest

from freight_audit import (
    MatchEngine, RateConfirmation, CarrierInvoice, LineItem, to_cents,
)

_URL = os.getenv("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _URL.startswith(("postgres://", "postgresql://")),
    reason="DATABASE_URL (postgres) not set")


def _store():
    from freight_audit.storage import get_store, PostgresStore
    s = get_store()
    assert isinstance(s, PostgresStore)
    return s


def _result(load_id, billed="$1,200.00", agreed="$1,000.00"):
    e = MatchEngine()
    rc = RateConfirmation(load_id, "B", "C", "O", "D", to_cents(agreed),
                          line_items=[LineItem("Linehaul", to_cents(agreed))])
    inv = CarrierInvoice("INV", load_id, "C", to_cents(billed),
                         line_items=[LineItem("Linehaul", to_cents(billed))])
    return e.match(rc, inv, None)


def test_pg_persist_get_and_report():
    t = f"acme-{uuid.uuid4().hex[:8]}"
    s = _store()
    s.save_result(_result("L1"), tenant_id=t)
    assert s.get_load("L1", t)["severity"] == "warn"
    assert s.report(t)["loads"] == 1
    s.close()


def test_pg_tenant_isolation():
    a, b = f"a-{uuid.uuid4().hex[:8]}", f"b-{uuid.uuid4().hex[:8]}"
    s = _store()
    s.save_result(_result("LX"), tenant_id=a)
    assert s.get_load("LX", a) is not None
    assert s.get_load("LX", b) is None
    assert s.all_loads(b) == []
    s.close()


def test_pg_decisions_usage_and_billing():
    t = f"t-{uuid.uuid4().hex[:8]}"
    s = _store()
    s.save_result(_result("LD"), tenant_id=t)
    s.record_decision("LD", "approved", actor="amy", tenant_id=t)
    s.record_usage(t, "LD", 40000, 0)
    assert s.decisions_for("LD", t)[0]["decision"] == "approved"
    from freight_audit.billing import bill_tenant, Pricing
    b = bill_tenant(s, t, Pricing(base_fee_cents=50, pct_savings=0.25))
    assert b["audits"] == 1 and isinstance(b["billing_cents"], int)
    s.close()
