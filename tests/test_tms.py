"""
Tests for the live TMS write-back adapter (PROMPTS #5).

urllib.request.urlopen is monkeypatched, so there is NO live network call. We
assert the correct tms_json body + auth header go to the env-configured endpoint.
"""
import io
import json
import urllib.request

import pytest

from freight_audit import MatchEngine, RateConfirmation, CarrierInvoice, LineItem, to_cents
from freight_audit.exporters import tms_json
from freight_audit.tms import post_to_tms


class _FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def getcode(self):
        return 200


def _results():
    e = MatchEngine()
    rc = RateConfirmation("L1", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    inv = CarrierInvoice("INV1", "L1", "C", to_cents("$1,200.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,200.00"))])
    return [e.match(rc, inv, None)]


def test_post_to_tms_sends_payload_and_auth(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setenv("FREIGHT_AUDIT_TMS_URL", "https://tms.example/api/audit")
    monkeypatch.setenv("FREIGHT_AUDIT_TMS_TOKEN", "secret-xyz")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    results = _results()
    out = post_to_tms(results)

    assert captured["url"] == "https://tms.example/api/audit"
    assert captured["method"] == "POST"
    # the body is exactly the generic tms_json payload
    assert json.loads(captured["data"].decode()) == json.loads(tms_json(results))
    # credentials come from the env, not source
    assert captured["headers"]["authorization"] == "Bearer secret-xyz"
    assert out["status"] == 200 and out["loads_sent"] == 1 and out["response"] == {"ok": True}


def test_post_to_tms_requires_endpoint(monkeypatch):
    monkeypatch.delenv("FREIGHT_AUDIT_TMS_URL", raising=False)
    with pytest.raises(ValueError):
        post_to_tms(_results(), endpoint=None)
