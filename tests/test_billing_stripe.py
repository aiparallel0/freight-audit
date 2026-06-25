"""
Billing -> Stripe tests (PROMPTS #13). The stripe SDK is monkeypatched, so there is
no live call. charge_tenant builds a PaymentIntent for the metered amount; the
webhook verifies + dispatches an event.

BLOCKED (live): a real test charge against Stripe test mode needs a Stripe test
secret key (sk_test_...), which is not available here. The integration code is real
(uses the stripe SDK); only the live execution is blocked.
"""
import pytest

import stripe

from freight_audit.store import Store
from freight_audit.billing import charge_tenant, Pricing


def test_charge_tenant_creates_stripe_payment_intent(tmp_path, monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return {"id": "pi_test_1", "status": "succeeded"}

    monkeypatch.setattr(stripe.PaymentIntent, "create", fake_create)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")

    s = Store(str(tmp_path / "b.db"))
    s.record_usage("acme", "L1", 40000, 0)            # value = $400
    out = charge_tenant(s, "acme", Pricing(base_fee_cents=50, pct_savings=0.25))

    # bill = 50*1 + floor(0.25 * 40000) = 50 + 10000 = 10050 cents
    assert out["bill"]["billing_cents"] == 10050
    assert captured["amount"] == 10050 and captured["currency"] == "usd"
    assert captured["metadata"]["tenant"] == "acme"
    assert captured["api_key"] == "sk_test_fake"      # key from the secret provider
    assert out["processor"] == "stripe" and out["status"] == "succeeded"
    s.close()


def test_charge_tenant_skips_zero_bill(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    s = Store(str(tmp_path / "z.db"))                 # no usage -> nothing to bill
    out = charge_tenant(s, "acme", Pricing(base_fee_cents=0, pct_savings=0.0))
    assert out["skipped"] is True
    s.close()


def test_webhook_verifies_signature(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import freight_audit.api as apimod
    from freight_audit.api import app
    from freight_audit.ratelimit import InMemoryRateLimiter

    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "k.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "d.db"))
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    apimod._limiter = InMemoryRateLimiter(limit_per_min=10000)

    def fake_construct(payload, sig, secret):
        assert secret == "whsec_test"
        if sig != "good":
            raise ValueError("bad signature")
        return {"type": "payment_intent.succeeded",
                "data": {"object": {"metadata": {"tenant": "acme"}, "amount": 10050}}}

    monkeypatch.setattr(stripe.Webhook, "construct_event", fake_construct)
    c = TestClient(app)

    ok = c.post("/billing/webhook", content=b"{}", headers={"Stripe-Signature": "good"})
    assert ok.status_code == 200 and ok.json()["type"] == "payment_intent.succeeded"
    bad = c.post("/billing/webhook", content=b"{}", headers={"Stripe-Signature": "bad"})
    assert bad.status_code == 400
