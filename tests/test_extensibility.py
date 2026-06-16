"""
Tests for the client-extensibility features (items #3, #4, #5):
  - data-driven accessorial vocabulary + overlays + learning loop
  - per-client business-rule profiles (fuel formula, caps, disallowed)
  - output exporters (QuickBooks IIF, TMS JSON, exception CSV)

Run:  python tests/test_extensibility.py
"""
import glob
import json
import os
import importlib.resources

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from freight_audit import (  # noqa: E402
    Vocabulary, ClientProfile, MatchEngine, JsonFixtureProvider,
    export, EXPORTERS, RateConfirmation, CarrierInvoice, LineItem, to_cents,
)
from freight_audit.profiles import apply_profile_rules  # noqa: E402


def _all_results():
    prov, eng = JsonFixtureProvider(), MatchEngine()
    out = []
    for path in sorted(glob.glob(os.path.join(REPO, "samples", "loads", "*.json"))):
        b = json.load(open(path))
        rc = prov.extract_rate_confirmation(b["rate_confirmation"]) if b.get("rate_confirmation") else None
        inv = prov.extract_invoice(b["invoice"]) if b.get("invoice") else None
        pod = prov.extract_pod(b["pod"]) if b.get("pod") else None
        out.append(eng.match(rc, inv, pod))
    return out


# ---- #3 vocabulary -------------------------------------------------------
def test_vocab_loads_defaults():
    v = Vocabulary.load()
    assert v.categorize("FSC") == "fuel"
    assert v.categorize("Driver Wait Time") == "detention"
    assert v.categorize("Liftgate") == "liftgate"
    assert v.categorize("totally unknown widget") == "other"


def test_vocab_overlay_extends():
    base = Vocabulary.load()
    overlay = Vocabulary.load(
        client_overlay=str(importlib.resources.files("freight_audit") / "vocab" / "client_example.json"))
    # 'dwell' is only in the overlay
    assert base.categorize("dwell time") == "other"
    assert overlay.categorize("dwell time") == "detention"
    assert overlay.categorize("redelivery") == "reconsignment"


def test_vocab_learning_loop():
    v = Vocabulary.load()
    sugg = v.suggest_unmatched(["dock fee", "FSC", "completely random"])
    descs = {s["description"] for s in sugg}
    assert "dock fee" in descs        # unmatched -> suggested
    assert "FSC" not in descs         # already matches -> not suggested
    for s in sugg:
        assert "guess" in s and "confidence" in s


# ---- #4 profiles ---------------------------------------------------------
def test_profile_loads():
    p = ClientProfile.load(os.path.join(REPO, "samples", "profiles", "client_example.json"))
    assert p.name == "Acme Brokerage"
    assert p.fuel_rule.kind == "pct_of_linehaul"
    assert abs(p.fuel_rule.pct - 0.25) < 1e-9
    assert "tonu" in p.disallowed_accessorials
    assert p.global_accessorial_caps["lumper"] == to_cents("$150.00")


def test_profile_rules_fire():
    p = ClientProfile.load(os.path.join(REPO, "samples", "profiles", "client_example.json"))
    rc = RateConfirmation("L", "Acme", "C", "A", "Z", to_cents("$2000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1600.00")),
                                      LineItem("Fuel", to_cents("$400.00"))])
    inv = CarrierInvoice("INV", "L", "C", to_cents("$2475.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1600.00")),
                                     LineItem("Fuel Surcharge", to_cents("$600.00")),
                                     LineItem("Lumper", to_cents("$200.00")),
                                     LineItem("TONU", to_cents("$75.00"))])
    eng = MatchEngine(p.to_engine_config())
    eng.match(rc, inv, None)   # normalizes categories
    findings = apply_profile_rules(p, rc, inv)
    msgs = " ".join(f.message for f in findings)
    assert "tonu" in msgs.lower() and "disallowed" in msgs.lower()
    assert "lumper" in msgs.lower() and "cap" in msgs.lower()
    assert "fuel" in msgs.lower()   # formula breach
    # fuel: $600 billed vs $400 expected (25% of $1600) -> $200 over
    fuel = [f for f in findings if "fuel" in f.message.lower()][0]
    assert fuel.money_impact_cents == to_cents("$200.00")


# ---- #5 exporters --------------------------------------------------------
def test_exporters_registered():
    for name in ("csv", "exceptions_csv", "quickbooks_iif", "tms_json"):
        assert name in EXPORTERS


def test_quickbooks_iif_only_approvable():
    results = _all_results()
    iif = export(results, "quickbooks_iif")
    assert "!TRNS" in iif and "ENDTRNS" in iif
    # only the clean load (L-100481) should appear as a bill; flagged ones held
    assert "L-100481" in iif
    assert "L-100482" not in iif   # had overcharges -> not auto-imported


def test_tms_json_valid_and_complete():
    results = _all_results()
    data = json.loads(export(results, "tms_json"))
    assert "loads" in data and len(data["loads"]) == len(results)
    statuses = {l["audit_status"] for l in data["loads"]}
    assert "approved" in statuses and "needs_review" in statuses


def test_exceptions_csv_excludes_clean():
    results = _all_results()
    csv_text = export(results, "exceptions_csv")
    assert "L-100481" not in csv_text   # clean load not in the worklist
    assert "L-100484" in csv_text       # blocking load is
