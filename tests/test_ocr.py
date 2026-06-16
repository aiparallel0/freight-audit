"""
Tests for the real-OCR receipt pipeline.

These run against the actual demo receipt photo, so they prove the extraction +
validation works end to end on a real (noisy) document, not just synthetic data.

Run:  python tests/test_ocr.py
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from freight_audit.ocr import extract_receipt, parse_money, parse_receipt  # noqa: E402
from freight_audit.ocr import validate, Severity  # noqa: E402

RECEIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample_receipt.png")


# ---- noise-tolerant money parser (item #6) --------------------------------
def test_parse_money_repairs_ocr_noise():
    assert parse_money("33,92") == 33.92        # decimal comma
    assert parse_money("RM 19.00") == 19.00
    assert parse_money("§0.00") == 50.00         # OCR § -> 5
    assert parse_money("-RM 0.02") == -0.02
    assert parse_money("1,234.56") == 1234.56    # thousands + decimal
    assert parse_money("3,02") == 3.02
    assert parse_money("garbage") is None
    assert parse_money("") is None


# ---- parser on a fixed text block (deterministic, no OCR) -----------------
SAMPLE_TEXT = """MR D.I.Y. (JOHOR) SDN BHD
-INVOICE-
CHOPPING BOARD 35.5x25.5CM 803M#
EZ10HD05 - 24
8970669 1 X 19.00 19.00
AIR PRESSURE SPRAYER SX-575-1 1.5L
HC03-7 - 15
9066468 1 X 8.02 8.02
Item(s) : 2 Qty(s) : 2
TOTAL RM 27.02
TOTAL ROUNDED RM 27.00
CASH RM 30.00
CHANGE RM 3.00
12-01-19 21:13 SH01 ZK09
"""


def test_parser_structures_text():
    r = parse_receipt(SAMPLE_TEXT)
    assert r.invoice_marker == "INVOICE"
    assert "SDN BHD" in (r.merchant or "")
    assert len(r.lines) == 2
    assert r.lines[0].description.startswith("CHOPPING BOARD")
    assert r.lines[0].line_total == 19.00
    assert r.lines[1].description.startswith("AIR PRESSURE")
    assert r.total == 27.02
    assert r.cash == 30.00
    assert r.change == 3.00
    assert r.declared_item_count == 2


def test_parser_validation_consistent_block():
    r = parse_receipt(SAMPLE_TEXT)
    res = validate(r)
    # line sum 27.02 == total; change 30-27 == 3; rounds to 27.00
    names = {c.name: c.severity for c in res.checks}
    assert names.get("line_sum_vs_total") == Severity.OK
    assert names.get("change") == Severity.OK
    assert res.confident


# ---- the synthetic receipt fixture (no personal data) ---------------------
def test_receipt_extracts_three_lines():
    r = extract_receipt(RECEIPT, use_preprocess=True)
    assert len(r.lines) == 3, f"expected 3 line items, got {len(r.lines)}"
    descs = " ".join(l.description.upper() for l in r.lines)
    assert "WIDGET ALPHA" in descs
    assert "GADGET BETA" in descs
    assert "DOOHICKEY GAMMA" in descs


def test_receipt_totals_and_math():
    r = extract_receipt(RECEIPT, use_preprocess=True)
    assert r.total == 23.00
    assert r.total_rounded == 23.00
    assert r.cash == 50.00
    assert r.change == 27.00
    # line items must sum to the printed total (12.00 + 4.50 + 6.50)
    line_sum = round(sum(l.line_total for l in r.lines), 2)
    assert line_sum == 23.00


def test_receipt_validates_confident():
    r = extract_receipt(RECEIPT, use_preprocess=True)
    res = validate(r)
    assert res.confident, [f"{c.name}:{c.severity.value}" for c in res.checks]
    assert res.severity in (Severity.OK, Severity.INFO)


# ---- #2 layout profiles (config-driven field mapping) ---------------------
def test_default_layout_parses_receipt():
    from freight_audit.ocr import DEFAULT_LAYOUT
    r = extract_receipt(RECEIPT, use_preprocess=True, layout=DEFAULT_LAYOUT)
    assert len(r.lines) == 3 and r.total == 23.00


def test_different_layout_parses_tabular_invoice():
    """The same parser + a different profile handles a totally different layout."""
    from freight_audit.ocr import TABULAR_LAYOUT
    from freight_audit.ocr import parse_receipt
    text = (
        "ACME FREIGHT SERVICES LLC\n"
        "INVOICE #4402\n"
        "Linehaul Atlanta to Tampa       1   1800.00   1800.00\n"
        "Fuel Surcharge                  1    300.00    300.00\n"
        "Detention 2 hrs                 2     75.00    150.00\n"
        "Subtotal                                       2250.00\n"
        "Amount Due                                      2250.00\n"
        "01/15/2026 14:30\n"
    )
    r = parse_receipt(text, layout=TABULAR_LAYOUT)
    assert r.currency == "USD"
    assert "ACME FREIGHT" in (r.merchant or "")
    assert len(r.lines) == 3
    descs = " ".join(l.description.lower() for l in r.lines)
    assert "linehaul" in descs and "fuel" in descs and "detention" in descs
    assert r.lines[0].line_total == 1800.00
    assert r.total == 2250.00


# ---- #6 confidence routing -----------------------------------------------
def test_ocr_confidence_available():
    from freight_audit.ocr import ocr_mean_confidence
    conf = ocr_mean_confidence(RECEIPT, use_preprocess=True)
    assert 0.0 <= conf <= 100.0
    assert conf > 40.0   # this is a legible receipt; sanity floor
