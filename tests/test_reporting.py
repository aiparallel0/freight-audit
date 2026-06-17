"""
Tests for savings / ROI reporting (Phase C stage 12).

Covers the same numbers coming out of (a) a live batch of MatchResults and
(b) the persisted history in store.py, plus the roi_report exporter. Money is
asserted in integer cents.
"""
import os

from freight_audit import (  # noqa: E402
    MatchEngine, JsonFixtureProvider, RateConfirmation, CarrierInvoice,
    ProofOfDelivery, LineItem, to_cents, export, EXPORTERS,
    report_from_results, format_report,
)
from freight_audit.store import Store  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _overcharged_result():
    e = MatchEngine()
    rc = RateConfirmation("L1", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    inv = CarrierInvoice("INV1", "L1", "C", to_cents("$1,200.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,200.00"))])
    return e.match(rc, inv, None)


def _recoverable_result():
    from datetime import datetime
    e = MatchEngine()
    rc = RateConfirmation("L2", "B", "C", "O", "D", to_cents("$1,000.00"),
                          line_items=[LineItem("Linehaul", to_cents("$1,000.00")),
                                      LineItem("Detention", 0, rate_cents=to_cents("$80.00"))],
                          approved_accessorials={"detention": to_cents("$80.00")},
                          free_time_hours=2.0)
    inv = CarrierInvoice("INV2", "L2", "C", to_cents("$1,000.00"),
                         line_items=[LineItem("Linehaul", to_cents("$1,000.00"))])
    pod = ProofOfDelivery("L2", True,
                          arrival_time=datetime(2026, 6, 1, 6, 0),
                          departure_time=datetime(2026, 6, 1, 10, 0))  # 4h -> 2h past free
    return e.match(rc, inv, pod)


def test_report_from_results_aggregates_money():
    over = _overcharged_result()       # +$200 overpayment (total + line)
    rec = _recoverable_result()        # 2h @ $80 = -$160 recoverable
    summary = report_from_results([over, rec])
    assert summary["loads"] == 2
    assert summary["overpayment_caught_cents"] == 40000   # total_mismatch $200 + line $200
    assert summary["recoverable_revenue_cents"] == 16000  # $160 detention owed
    assert summary["dollars_touched_cents"] == 56000
    assert summary["net_impact_cents"] == 40000 - 16000
    # broken out by finding type, money stays integer cents
    assert summary["by_finding_type"]["detention_underbilled"]["money_cents"] == -16000
    assert summary["by_finding_type"]["line_overcharge"]["count"] == 1


def test_report_from_store_matches_batch(tmp_path):
    over, rec = _overcharged_result(), _recoverable_result()
    db = str(tmp_path / "roi.db")
    store = Store(db)
    store.save_result(over)
    store.save_result(rec)
    hist = store.report()
    store.close()

    batch = report_from_results([over, rec])
    # the durable history report equals the live batch report
    for key in ("loads", "overpayment_caught_cents", "recoverable_revenue_cents",
                "dollars_touched_cents", "net_impact_cents"):
        assert hist[key] == batch[key]


def test_roi_report_exporter_registered_and_renders():
    assert "roi_report" in EXPORTERS
    text = export([_overcharged_result(), _recoverable_result()], "roi_report")
    assert "savings / ROI report" in text
    assert "$560.00" in text          # total dollars touched
    assert "recoverable revenue" in text


def test_format_report_is_stable_text():
    text = format_report(report_from_results([_overcharged_result()]))
    assert text.startswith("FREIGHT-AUDIT")
    assert "overpayment caught    : $400.00" in text
