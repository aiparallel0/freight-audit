"""
Web layer tests (PROMPTS #2 landing, #3 demo/trial, #4 signup page, #10 review
console served by the API). Via TestClient. The OCR demo path is skipped without
tesseract; the fast bundle path and the review flow need neither.
"""
import shutil

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import freight_audit.api as apimod  # noqa: E402
import freight_audit.web.pages as pages  # noqa: E402
from freight_audit.api import app  # noqa: E402
from freight_audit.security import KeyStore  # noqa: E402
from freight_audit.storage import get_store  # noqa: E402
from freight_audit.ratelimit import InMemoryRateLimiter  # noqa: E402
from freight_audit import MatchEngine, RateConfirmation, CarrierInvoice, LineItem, to_cents  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHT_AUDIT_KEYS", str(tmp_path / "keys.json"))
    monkeypatch.setenv("FREIGHT_AUDIT_DB", str(tmp_path / "db.sqlite"))
    apimod._limiter = InMemoryRateLimiter(limit_per_min=10000)
    pages._DEMO_COUNTS.clear()
    return TestClient(app), KeyStore(str(tmp_path / "keys.json"))


def test_landing_has_real_copy(env):
    c, _ = env
    r = c.get("/")
    assert r.status_code == 200
    html = r.text.lower()
    assert "stop paying freight invoices" in r.text.lower()
    assert "how it works" in html and "start free" in html
    assert "lorem" not in html                      # no placeholder copy


def test_signup_page_renders_form(env):
    c, _ = env
    r = c.get("/signup")
    assert r.status_code == 200 and 'id=email' in r.text


def test_demo_bundle_path_returns_result(env):
    c, _ = env
    r = c.post("/demo/audit", data={"sample": "load_002_overcharge_duplicate.json"},
               headers={"X-Trial-Id": "t-bundle"})
    assert r.status_code == 200
    d = r.json()
    assert d["load_id"] == "L-100482" and d["steps"][-1] == "result"
    assert any(f["money_impact_cents"] for f in d["findings"])


def test_demo_trial_limit_enforced(env, monkeypatch):
    c, _ = env
    monkeypatch.setenv("DEMO_TRIAL_LIMIT", "1")
    h = {"X-Trial-Id": "t-limit"}
    assert c.post("/demo/audit", data={"sample": "load_001_clean.json"}, headers=h).status_code == 200
    assert c.post("/demo/audit", data={"sample": "load_001_clean.json"}, headers=h).status_code == 429


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_demo_ocr_path_runs(env):
    c, _ = env
    import os
    png = os.path.join(os.path.dirname(pages.__file__), "static", "sample-invoice.png")
    with open(png, "rb") as fh:
        r = c.post("/demo/audit", files={"file": ("sample-invoice.png", fh, "image/png")},
                   headers={"X-Trial-Id": "t-ocr"})
    assert r.status_code == 200
    d = r.json()
    assert d["steps"][0] == "ocr" and "severity" in d


def test_review_console_lists_and_persists_correction(env, tmp_path):
    c, ks = env
    tenant = "acme@x.co"
    key = ks.issue("rev", tenant_id=tenant, role="reviewer")
    # seed a non-auto-approvable load for this tenant
    e = MatchEngine()
    rc = RateConfirmation("L-R1", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    inv = CarrierInvoice("INV", "L-R1", "C", to_cents("$1,200.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,200.00"))])
    store = get_store()
    store.save_result(e.match(rc, inv, None), tenant_id=tenant)

    listing = c.get("/review", headers={"X-API-Key": key})
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert any(it["load_id"] == "L-R1" for it in items)

    sub = c.post("/review/L-R1", json={"decision": "correct", "note": "fix linehaul"},
                 headers={"X-API-Key": key})
    assert sub.status_code == 200 and sub.json()["decision"] == "disputed"
    # persisted through the existing review path
    assert get_store().decisions_for("L-R1", tenant)[0]["decision"] == "disputed"
    # decided item drops off the queue
    assert all(it["load_id"] != "L-R1" for it in c.get("/review", headers={"X-API-Key": key}).json()["items"])
