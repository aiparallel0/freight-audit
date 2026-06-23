"""
Monitoring tests (PROMPTS #14): /metrics exposes per-tenant counters; the Notifier
dispatches through a fake transport; alert rules load and fire.
"""
import pytest

from freight_audit.metrics import Metrics
from freight_audit.notify import (
    Notifier, FakeTransport, load_alert_rules, evaluate_rules,
)


def test_metrics_render_prometheus():
    m = Metrics()
    m.record("acme", "/audit", 12.0, 200)
    m.record("acme", "/audit", 8.0, 500)
    text = m.render_prometheus()
    assert 'freight_audit_requests_total{tenant="acme",path="/audit"} 2' in text
    assert 'freight_audit_errors_total{tenant="acme",path="/audit"} 1' in text
    snap = m.snapshot()[0]
    assert snap["error_rate"] == 0.5 and snap["latency_avg_ms"] == 10.0


def test_metrics_endpoint_after_requests(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import freight_audit.api as apimod
    from freight_audit.api import app
    from freight_audit.metrics import METRICS
    from freight_audit.ratelimit import InMemoryRateLimiter
    from freight_audit.security import KeyStore

    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "k.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "d.db"))
    apimod._limiter = InMemoryRateLimiter(limit_per_min=10000)
    METRICS.reset()
    key = KeyStore(str(tmp_path / "k.json")).issue("api", "acme", role="api")
    c = TestClient(app)
    c.get("/usage", headers={"X-API-Key": key})       # tenant=acme
    c.get("/healthz")                                  # tenant=anonymous

    text = c.get("/metrics").text
    assert "freight_audit_requests_total" in text
    assert 'tenant="acme",path="/usage"' in text
    assert 'tenant="anonymous",path="/healthz"' in text


def test_alert_rules_load_and_fire_via_fake_transport():
    rules = load_alert_rules()
    assert rules and any(r["name"] == "high_error_rate" for r in rules)

    snapshot = [{"tenant": "acme", "path": "/audit", "count": 20,
                 "errors": 10, "error_rate": 0.5, "latency_avg_ms": 30.0}]
    firing = evaluate_rules(snapshot, rules)
    assert any(a["name"] == "high_error_rate" and a["severity"] == "page" for a in firing)

    fake = FakeTransport()
    notifier = Notifier(transport=fake)
    for alert in firing:
        notifier.dispatch(alert)
    assert fake.sent and "high_error_rate" in fake.sent[0]["subject"]


def test_default_notifier_is_fake_without_config(monkeypatch):
    monkeypatch.delenv("NOTIFY_TRANSPORT", raising=False)
    assert isinstance(Notifier().transport, FakeTransport)
