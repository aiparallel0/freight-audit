"""
Fixture tests for the scaffolded (BLOCKED-on-external) features:
  SSO (#5-SSO), per-client layout tuning (#6), intake channels (#7),
  live-TMS write-back (#11). Each external dependency is BLOCKED; the code is tested
  against fixtures / mock transports.
"""
import io
import json
import time
import urllib.request

import pytest

# ---- SSO (mock IdP) -------------------------------------------------------
from freight_audit.sso import (
    OIDCConfig, jwt_encode, verify_id_token, build_authorize_url, InvalidToken,
)


def _oidc():
    return OIDCConfig("iss", "cid", "shh", "https://idp/authorize",
                      "https://idp/token", "https://app/callback")


def test_sso_verifies_mock_idp_token():
    cfg = _oidc()
    token = jwt_encode({"sub": "u1", "email": "a@b.co", "aud": "cid",
                        "exp": time.time() + 3600}, "shh")
    p = verify_id_token(token, cfg)
    assert p["sub"] == "u1" and p["email"] == "a@b.co"


def test_sso_rejects_tampered_expired_and_wrong_aud():
    cfg = _oidc()
    good = jwt_encode({"sub": "u", "aud": "cid", "exp": time.time() + 3600}, "shh")
    with pytest.raises(InvalidToken):
        verify_id_token(good[:-3] + "zzz", cfg)                     # bad signature
    with pytest.raises(InvalidToken):
        verify_id_token(jwt_encode({"sub": "u", "aud": "cid", "exp": time.time() - 1}, "shh"), cfg)
    with pytest.raises(InvalidToken):
        verify_id_token(jwt_encode({"sub": "u", "aud": "other", "exp": time.time() + 9}, "shh"), cfg)


def test_sso_authorize_url():
    u = build_authorize_url(_oidc(), "state123", "nonce123")
    assert "client_id=cid" in u and "response_type=code" in u and "state=state123" in u


# ---- per-client layout tuning --------------------------------------------
def test_client_layout_override_and_default(tmp_path):
    from freight_audit.client_layouts import load_client_layout
    from freight_audit.ocr.layouts import DEFAULT_LAYOUT
    d = tmp_path / "layouts"
    d.mkdir()
    (d / "acme.json").write_text(json.dumps({"name": "acme_invoice", "currency": "USD"}))
    lp = load_client_layout("acme", config_dir=str(d))
    assert lp.name == "acme_invoice" and lp.currency == "USD"
    assert load_client_layout("nobody", config_dir=str(d)) is DEFAULT_LAYOUT


# ---- intake channels ------------------------------------------------------
def test_intake_email_attachment():
    from email.message import EmailMessage
    from freight_audit.intake import parse_eml
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "ops@carrier", "audit@us", "invoice"
    m.set_content("see attached")
    bundle = {"load_id": "L-1", "invoice": {"invoice_number": "INV1", "load_id": "L-1",
              "billed_total": "$100.00", "line_items": [{"description": "Linehaul", "amount": "$100.00"}]}}
    m.add_attachment(json.dumps(bundle).encode(), maintype="application",
                     subtype="json", filename="load.json")
    out = parse_eml(m.as_bytes())
    assert len(out) == 1 and out[0]["invoice"]["invoice_number"] == "INV1"


def test_intake_edi_210():
    from freight_audit.intake import parse_edi_210
    edi = ("ISA*00*~GS*IN*~ST*210*0001~B3**INV777*****145000~N9*BM*L-100485~"
           "L1*1*140000***Linehaul~L1*2*5000***Fuel~SE*7*0001~")
    b = parse_edi_210(edi)
    assert b["load_id"] == "L-100485"
    assert b["invoice"]["invoice_number"] == "INV777"
    assert b["invoice"]["billed_total"] == "$1450.00"
    descs = {li["description"] for li in b["invoice"]["line_items"]}
    amts = {li["amount"] for li in b["invoice"]["line_items"]}
    assert descs == {"Linehaul", "Fuel"} and amts == {"$1400.00", "$50.00"}


class _Resp(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def getcode(self):
        return 200


def test_intake_tms_pull_mocked(monkeypatch):
    from freight_audit.intake import tms_pull

    def fake(req, timeout=None):
        assert req.full_url == "https://tms/api/loads"
        assert req.headers.get("Authorization") == "Bearer tok"
        return _Resp(json.dumps({"loads": [{"load_id": "L-9",
                     "invoice": {"invoice_number": "I9"}}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = tms_pull("https://tms/api/loads", "tok")
    assert out[0]["load_id"] == "L-9" and out[0]["invoice"]["invoice_number"] == "I9"
    monkeypatch.delenv("TMS_PULL_URL", raising=False)
    with pytest.raises(ValueError):
        tms_pull(None)


# ---- live-TMS write-back --------------------------------------------------
def test_tms_writeback_maps_and_posts(monkeypatch):
    from freight_audit import (MatchEngine, RateConfirmation, CarrierInvoice,
                               LineItem, to_cents)
    from freight_audit.tms_writeback import SchemaMapping, map_payload, write_back
    e = MatchEngine()
    rc = RateConfirmation("L1", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    inv = CarrierInvoice("INV", "L1", "C", to_cents("$1,200.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,200.00"))])
    results = [e.match(rc, inv, None)]
    mapping = SchemaMapping({"load_id": "shipmentId", "audit_status": "status",
                             "net_adjustment": "adjustmentUsd"})
    rec = map_payload(results, mapping)["records"][0]
    assert rec["shipmentId"] == "L1" and "status" in rec and "adjustmentUsd" in rec

    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return _Resp(b"")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    out = write_back(results, mapping, "https://tms/writeback", "tok")
    assert out["status"] == 200 and out["records"] == 1
    assert captured["body"]["records"][0]["shipmentId"] == "L1"
    monkeypatch.delenv("TMS_WRITEBACK_URL", raising=False)
    with pytest.raises(ValueError):
        write_back(results, mapping, None)
