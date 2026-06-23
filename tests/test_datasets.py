"""
Tests for the external-dataset adapters (CORD / SROIE / FATURA-style KIE) that map
into the scorer's truth shape. No downloads: representative annotation snippets only.
"""
import json

from freight_audit import to_cents
from freight_audit.synth import (
    cord_truth, sroie_truth, fatura_truth, kie_entities_truth, score_invoice,
)


def test_cord_truth_maps_menu_and_total():
    gt = {"gt_parse": {
        "menu": [{"nm": "Item A", "price": "10.00"},
                 {"nm": "Item B", "cnt": "2", "price": "5.00"}],
        "total": {"total_price": "15.00"}}}
    t = cord_truth(gt)
    assert t["billed_total_cents"] == to_cents("15.00")
    assert t["line_amounts_cents"] == [to_cents("10.00"), to_cents("5.00")]
    assert t["billed_total_cents"] == sum(t["line_amounts_cents"])
    assert t["carrier_name"] is None                      # CORD has no vendor
    # accepts the raw JSON string form too, and a single-item (dict) menu
    assert cord_truth(json.dumps(gt))["line_amounts_cents"] == t["line_amounts_cents"]
    one = cord_truth({"gt_parse": {"menu": {"nm": "X", "price": "9.00"},
                                   "total": {"total_price": "9.00"}}})
    assert one["line_amounts_cents"] == [to_cents("9.00")]


def test_sroie_truth_maps_company_and_total():
    t = sroie_truth({"company": "ACME CORP", "date": "01/01/2026",
                     "address": "1 Main St", "total": "42.50"})
    assert t["carrier_name"] == "ACME CORP"
    assert t["billed_total_cents"] == to_cents("42.50")
    assert t["line_amounts_cents"] == []                  # SROIE has no line items


def test_kie_entities_and_fatura_preset():
    entities = [
        {"label": "SELLER", "text": "Globex LLC"},
        {"label": "INVOICE_NO", "text": "INV-77"},
        {"label": "PRICE", "text": "100.00"},
        {"label": "PRICE", "text": "25.00"},
        {"label": "TOTAL", "text": "125.00"},
    ]
    t = fatura_truth(entities)
    assert t["carrier_name"] == "Globex LLC"
    assert t["invoice_number"] == "INV-77"
    assert t["billed_total_cents"] == to_cents("125.00")
    assert sorted(t["line_amounts_cents"]) == sorted([to_cents("100.00"), to_cents("25.00")])
    # custom label maps work for any KIE schema
    t2 = kie_entities_truth([{"class": "grand_total", "value": "9.99"}],
                            total_labels=("grand_total",))
    assert t2["billed_total_cents"] == to_cents("9.99")


def test_scorer_skips_fields_absent_in_truth():
    # CORD-style truth (no carrier): a perfect prediction still scores 1.0 and the
    # carrier check is simply not counted.
    truth = cord_truth({"gt_parse": {
        "menu": [{"nm": "A", "price": "10.00"}], "total": {"total_price": "10.00"}}})
    pred = {"billed_total": 10.00, "line_amounts": [10.00]}
    s = score_invoice(pred, truth)
    assert "carrier_name" not in s["checks"]
    assert s["accuracy"] == 1.0
