"""
Test suite for the matching engine.

Run with:  python -m pytest -q   (or)   python tests/test_engine.py

These tests lock in the money logic so a future change can't silently break a
client's audit. Each test maps to one real-world scenario. This is the "guard
every silent bug" discipline applied to the rules that touch customer dollars.
"""
import os


from freight_audit import (  # noqa: E402
    MatchEngine, EngineConfig, JsonFixtureProvider,
    RateConfirmation, CarrierInvoice, ProofOfDelivery, LineItem,
    FindingType, Severity, to_cents,
)
from freight_audit.normalize import normalize_category, is_same_load  # noqa: E402


def _has(result, ftype) -> bool:
    return any(f.type == ftype for f in result.findings)


def _impact(result, ftype) -> int:
    return sum(f.money_impact_cents for f in result.findings if f.type == ftype)


# ---------------------------------------------------------------------------
# money parsing
# ---------------------------------------------------------------------------
def test_money_parsing():
    assert to_cents("$1,850.00") == 185000
    assert to_cents("$0.00") == 0
    assert to_cents("(125.00)") == -12500
    assert to_cents(1850.0) == 185000
    assert to_cents(None) == 0
    # the bug we fixed: bare integers are DOLLARS, not cents
    assert to_cents(1234) == 123400
    assert to_cents(5) == 500
    assert to_cents(12345) == 1234500
    assert to_cents(True) == 0   # bool guard


def test_load_id_mismatch_blocks():
    from freight_audit import RateConfirmation, CarrierInvoice, MatchEngine
    e = MatchEngine()
    rc = RateConfirmation("AAA", "B", "C", "O", "D", to_cents("$100.00"))
    inv = CarrierInvoice("INV", "ZZZ", "C", to_cents("$100.00"))
    r = e.match(rc, inv, None)
    assert r.severity == Severity.BLOCK
    assert _has(r, FindingType.LOAD_ID_MISMATCH)


def test_inverted_pod_timestamps_block():
    from datetime import datetime
    from freight_audit import RateConfirmation, CarrierInvoice, ProofOfDelivery, MatchEngine
    e = MatchEngine()
    rc = RateConfirmation("L", "B", "C", "O", "D", to_cents("$100.00"),
                          line_items=[LineItem("Linehaul", to_cents("$100.00"))])
    inv = CarrierInvoice("INV", "L", "C", to_cents("$100.00"),
                         line_items=[LineItem("Linehaul", to_cents("$100.00"))])
    pod = ProofOfDelivery("L", True,
                          arrival_time=datetime(2026, 6, 1, 14, 0),
                          departure_time=datetime(2026, 6, 1, 8, 0))  # inverted
    r = e.match(rc, inv, pod)
    assert _has(r, FindingType.BAD_POD_DATA)
    # must NOT have computed any detention off the bad data
    assert not _has(r, FindingType.DETENTION_UNDERBILLED)


def test_overnight_detention_is_capped_not_silent():
    from datetime import datetime
    from freight_audit import RateConfirmation, CarrierInvoice, ProofOfDelivery, MatchEngine
    e = MatchEngine()
    rc = RateConfirmation("L", "B", "C", "O", "D", to_cents("$100.00"),
                          line_items=[LineItem("Linehaul", to_cents("$100.00"))],
                          approved_accessorials={"detention": to_cents("$50.00")})
    inv = CarrierInvoice("INV", "L", "C", to_cents("$100.00"),
                         line_items=[LineItem("Linehaul", to_cents("$100.00"))])
    pod = ProofOfDelivery("L", True,
                          arrival_time=datetime(2026, 6, 1, 8, 0),
                          departure_time=datetime(2026, 6, 2, 8, 0))  # 24h
    r = e.match(rc, inv, pod)
    # 24h is implausible -> flagged as bad data, and NOT turned into a $1,100 number
    assert _has(r, FindingType.BAD_POD_DATA)
    assert not _has(r, FindingType.DETENTION_UNDERBILLED)


def test_long_but_plausible_detention_is_capped():
    from datetime import datetime
    from freight_audit import RateConfirmation, CarrierInvoice, ProofOfDelivery, MatchEngine, EngineConfig
    e = MatchEngine(EngineConfig(max_detention_hours=8.0, implausible_time_on_site_hours=14.0))
    rc = RateConfirmation("L", "B", "C", "O", "D", to_cents("$100.00"),
                          line_items=[LineItem("Linehaul", to_cents("$100.00"),
                                               rate_cents=to_cents("$50.00"))],
                          approved_accessorials={"detention": to_cents("$50.00")},
                          free_time_hours=2.0)
    inv = CarrierInvoice("INV", "L", "C", to_cents("$100.00"),
                         line_items=[LineItem("Linehaul", to_cents("$100.00"))])
    # 12h on site: plausible (< 14h) but 10h past free time -> capped at 8h * $50 = $400
    pod = ProofOfDelivery("L", True,
                          arrival_time=datetime(2026, 6, 1, 6, 0),
                          departure_time=datetime(2026, 6, 1, 18, 0))
    r = e.match(rc, inv, pod)
    assert _impact(r, FindingType.DETENTION_UNDERBILLED) == -40000  # capped at 8h


# ---------------------------------------------------------------------------
# normalization
# ---------------------------------------------------------------------------
def test_category_normalization():
    assert normalize_category("Line Haul") == "linehaul"
    assert normalize_category("FSC") == "fuel"
    assert normalize_category("Detention (3 hrs)") == "detention"
    assert normalize_category("Driver Wait Time") == "detention"
    assert normalize_category("Liftgate Service") == "liftgate"
    assert normalize_category("Lumper Fee") == "lumper"
    assert normalize_category("Mystery Charge") == "other"
    # OCR-noisy fuzzy fallback
    assert normalize_category("Detenton") == "detention"


def test_load_id_matching():
    assert is_same_load("L-100482", "100482")
    assert is_same_load("100482", "L-100482")
    assert is_same_load("L-100481", "L-100481")
    assert not is_same_load("L-100481", "L-100999")


# ---------------------------------------------------------------------------
# end-to-end on each sample
# ---------------------------------------------------------------------------
def _run_sample(name):
    import json
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundle = json.loads(open(os.path.join(repo, "samples", "loads", name)).read())
    p = JsonFixtureProvider()
    e = MatchEngine()
    rc = p.extract_rate_confirmation(bundle["rate_confirmation"]) if bundle.get("rate_confirmation") else None
    inv = p.extract_invoice(bundle["invoice"]) if bundle.get("invoice") else None
    pod = p.extract_pod(bundle["pod"]) if bundle.get("pod") else None
    return e.match(rc, inv, pod)


def test_sample_clean_autoapproves():
    r = _run_sample("load_001_clean.json")
    assert r.auto_approvable
    assert r.severity == Severity.OK
    assert _has(r, FindingType.OK)


def test_sample_overcharge_and_duplicate():
    r = _run_sample("load_002_overcharge_duplicate.json")
    assert not r.auto_approvable
    assert _has(r, FindingType.TOTAL_MISMATCH)
    assert _has(r, FindingType.LINE_OVERCHARGE)
    assert _has(r, FindingType.DUPLICATE_LINE)
    # total billed 2500 vs agreed 2100 -> +400
    assert _impact(r, FindingType.TOTAL_MISMATCH) == 40000
    # duplicate fuel of $300
    assert _impact(r, FindingType.DUPLICATE_LINE) == 30000


def test_sample_unauthorized_accessorial():
    r = _run_sample("load_003_unauthorized_accessorial.json")
    assert _has(r, FindingType.UNAUTHORIZED_ACCESSORIAL)
    assert _impact(r, FindingType.UNAUTHORIZED_ACCESSORIAL) == 12500  # $125 liftgate


def test_sample_detention_unsupported_blocks():
    r = _run_sample("load_004_detention_unsupported.json")
    assert r.severity == Severity.BLOCK
    assert _has(r, FindingType.DETENTION_UNSUPPORTED)
    assert _has(r, FindingType.ACCESSORIAL_OVER_CAP)  # $225 billed vs $75 cap


def test_sample_detention_recoverable():
    r = _run_sample("load_005_detention_recoverable.json")
    assert _has(r, FindingType.DETENTION_UNDERBILLED)
    # 4.25h on site - 2.0h free = 2.25h @ $80 = $180 owed (negative = owed to carrier)
    assert _impact(r, FindingType.DETENTION_UNDERBILLED) == -18000


# ---------------------------------------------------------------------------
# targeted unit cases built in code (no fixtures)
# ---------------------------------------------------------------------------
def test_missing_invoice_blocks():
    e = MatchEngine()
    rc = RateConfirmation("L1", "B", "C", "A", "Z", to_cents("$100.00"))
    r = e.match(rc, None, None)
    assert r.severity == Severity.BLOCK
    assert _has(r, FindingType.MISSING_DOC)


def test_detention_underbilled_uses_rate_con_rate():
    e = MatchEngine()
    rc = RateConfirmation(
        "L9", "B", "C", "A", "Z", to_cents("$1,000.00"),
        line_items=[LineItem("Detention", 0, rate_cents=to_cents("$90.00"))],
        free_time_hours=2.0,
    )
    inv = CarrierInvoice("INV9", "L9", "C", to_cents("$1,000.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    from datetime import datetime
    pod = ProofOfDelivery("L9", True,
                          arrival_time=datetime(2026, 6, 1, 8, 0),
                          departure_time=datetime(2026, 6, 1, 13, 0))  # 5h on site
    r = e.match(rc, inv, pod)
    # 5h - 2h = 3h @ $90 = $270 owed
    assert _impact(r, FindingType.DETENTION_UNDERBILLED) == -27000


def test_tolerance_ignores_pennies():
    e = MatchEngine(EngineConfig(total_tolerance_cents=100))
    rc = RateConfirmation("L2", "B", "C", "A", "Z", to_cents("$500.00"),
                          line_items=[LineItem("Linehaul", to_cents("$500.00"))])
    inv = CarrierInvoice("INV2", "L2", "C", to_cents("$500.50"),
                         line_items=[LineItem("Linehaul", to_cents("$500.50"))])
    r = e.match(rc, inv, None)
    assert not _has(r, FindingType.TOTAL_MISMATCH)  # 50c within $1 tolerance


# ---------------------------------------------------------------------------
# hardening: messy real-world edge cases (PROMPTS.md #6)
# ---------------------------------------------------------------------------
def test_partial_pod_date_only_not_used_for_detention():
    """A POD with a date but no clock time parses to midnight; we must NOT fabricate
    detention off that fake 00:00 arrival."""
    from datetime import datetime
    e = MatchEngine()
    rc = RateConfirmation("L", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))],
                          free_time_hours=2.0)
    inv = CarrierInvoice("INV", "L", "C", to_cents("$1,000.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    pod = ProofOfDelivery("L", True,
                          arrival_time=datetime(2026, 6, 1, 0, 0),    # date only -> midnight
                          departure_time=datetime(2026, 6, 1, 5, 0))  # would be 5h on site
    r = e.match(rc, inv, pod)
    assert not _has(r, FindingType.DETENTION_UNDERBILLED)


def test_detention_billed_with_date_only_pod_is_unsupported():
    from datetime import datetime
    e = MatchEngine()
    rc = RateConfirmation("L", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))],
                          approved_accessorials={"detention": to_cents("$300.00")},
                          free_time_hours=2.0)
    inv = CarrierInvoice("INV", "L", "C", to_cents("$1,200.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,000.00")),
                                     LineItem("Detention", to_cents("$200.00"))])
    pod = ProofOfDelivery("L", True,
                          arrival_time=datetime(2026, 6, 1, 0, 0),
                          departure_time=datetime(2026, 6, 1, 0, 0))  # date only, no times
    r = e.match(rc, inv, pod)
    assert r.severity == Severity.BLOCK
    assert _has(r, FindingType.DETENTION_UNSUPPORTED)
    msg = " ".join(f.message for f in r.findings
                   if f.type == FindingType.DETENTION_UNSUPPORTED)
    assert "date" in msg.lower()


def test_multiple_detention_lines_flagged_and_summed():
    from datetime import datetime
    e = MatchEngine()
    rc = RateConfirmation("L", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))],
                          approved_accessorials={"detention": None},  # detention allowed
                          free_time_hours=2.0)
    inv = CarrierInvoice("INV", "L", "C", to_cents("$1,230.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,000.00")),
                                     LineItem("Detention", to_cents("$150.00")),
                                     LineItem("Detention - extra wait", to_cents("$80.00"))])
    pod = ProofOfDelivery("L", True,
                          arrival_time=datetime(2026, 6, 1, 6, 0),
                          departure_time=datetime(2026, 6, 1, 12, 0))  # 6h -> supported
    r = e.match(rc, inv, pod)
    assert _has(r, FindingType.MULTIPLE_DETENTION_LINES)
    # the flag itself carries no money impact (sum is verified, not double-counted)
    assert _impact(r, FindingType.MULTIPLE_DETENTION_LINES) == 0
    # both lines are detention and the POD supports the wait -> not "unsupported"
    assert not _has(r, FindingType.DETENTION_UNSUPPORTED)


def test_zero_amount_accessorial_not_flagged_unauthorized():
    e = MatchEngine()
    rc = RateConfirmation("L", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    inv = CarrierInvoice("INV", "L", "C", to_cents("$1,000.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,000.00")),
                                     LineItem("Liftgate", 0)])  # quoted but $0 / waived
    r = e.match(rc, inv, None)
    assert not _has(r, FindingType.UNAUTHORIZED_ACCESSORIAL)   # $0 is not an overcharge
    assert _has(r, FindingType.ZERO_AMOUNT_ACCESSORIAL)
    assert _impact(r, FindingType.ZERO_AMOUNT_ACCESSORIAL) == 0
    assert r.auto_approvable                                    # INFO only -> still safe


def test_flat_fee_detention_valued_as_flat_not_hourly():
    """Rate con prices detention as a flat fee; an underbilled claim is that flat
    amount regardless of hours (also covers the new extract.py field mapping)."""
    from datetime import datetime
    e = MatchEngine()
    rc = JsonFixtureProvider().extract_rate_confirmation({
        "load_id": "L", "agreed_total": "$1,000.00", "free_time_hours": 2.0,
        "detention_flat_fee": "$150.00",
        "line_items": [{"description": "Linehaul", "amount": "$1,000.00"}],
    })
    assert rc.detention_flat_fee_cents == to_cents("$150.00")
    inv = CarrierInvoice("INV", "L", "C", to_cents("$1,000.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    # 7h on site (5h past free): per-hour this would be a big number; flat stays $150
    pod_long = ProofOfDelivery("L", True,
                               arrival_time=datetime(2026, 6, 1, 6, 0),
                               departure_time=datetime(2026, 6, 1, 13, 0))
    assert _impact(e.match(rc, inv, pod_long), FindingType.DETENTION_UNDERBILLED) == -15000
    # fewer hours past free -> still the same flat fee
    pod_short = ProofOfDelivery("L", True,
                                arrival_time=datetime(2026, 6, 1, 6, 0),
                                departure_time=datetime(2026, 6, 1, 9, 30))  # 1.5h past free
    assert _impact(e.match(rc, inv, pod_short), FindingType.DETENTION_UNDERBILLED) == -15000
