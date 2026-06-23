"""
Tests for the synthetic freight-document generator + OCR benchmark harness
(Phase C stage 8/track 3: PII-safe synthetics that close the "needs real docs" gap).

The generator + scorer tests need no OCR. The round-trip benchmark needs Pillow
(skipped without it) and the tesseract binary (skipped without it).
"""
import json
import os
import shutil

import pytest

from freight_audit import process_load, Severity, FindingType
from freight_audit.synth import (
    generate_load, invoice_ground_truth, score_invoice, aggregate_scores,
)


def _has(result, ftype):
    return any(f.type == ftype for f in result.findings)


# --- value generation (pure stdlib) ----------------------------------------
def test_generate_load_is_deterministic_and_well_shaped():
    a = generate_load(seed=42)
    b = generate_load(seed=42)
    assert a == b                                   # reproducible
    for key in ("load_id", "rate_confirmation", "invoice", "pod"):
        assert key in a
    # all three docs reference the same load
    assert a["rate_confirmation"]["load_id"] == a["load_id"]
    assert a["invoice"]["load_id"] == a["load_id"]
    assert a["pod"]["load_id"] == a["load_id"]


def test_ground_truth_money_is_consistent_integer_cents():
    gt = invoice_ground_truth(generate_load(seed=5, with_detention=True))
    assert isinstance(gt["billed_total_cents"], int)
    assert all(isinstance(c, int) for c in gt["line_amounts_cents"])
    # the total equals the sum of the line items, in cents
    assert gt["billed_total_cents"] == sum(gt["line_amounts_cents"])


def test_clean_load_audits_auto_approvable():
    b = generate_load(seed=1, with_detention=False)
    r = process_load(b["rate_confirmation"], b["invoice"], b["pod"])
    assert r.severity == Severity.OK and r.auto_approvable


def test_detention_load_flags_without_spurious_over_cap():
    b = generate_load(seed=2, with_detention=True)
    r = process_load(b["rate_confirmation"], b["invoice"], b["pod"])
    assert _has(r, FindingType.TOTAL_MISMATCH)             # billed adds approved detention
    assert not _has(r, FindingType.ACCESSORIAL_OVER_CAP)   # detention is uncapped, not over


# --- scoring harness (no OCR) ----------------------------------------------
def test_score_invoice_perfect_and_wrong():
    truth = invoice_ground_truth(generate_load(seed=2, with_detention=True))
    perfect = {
        "carrier_name": truth["carrier_name"],
        "billed_total": truth["billed_total_cents"] / 100,
        "line_amounts": [c / 100 for c in truth["line_amounts_cents"]],
    }
    s = score_invoice(perfect, truth)
    assert s["accuracy"] == 1.0 and s["line_amount_recall"] == 1.0

    wrong = dict(perfect, billed_total=perfect["billed_total"] + 1.00)
    assert score_invoice(wrong, truth)["checks"]["billed_total"] is False


def test_aggregate_scores_rolls_up():
    agg = aggregate_scores([
        {"accuracy": 1.0, "line_amount_recall": 1.0, "checks": {"billed_total": True}},
        {"accuracy": 0.5, "line_amount_recall": 0.5, "checks": {"billed_total": False}},
    ])
    assert agg["documents"] == 2
    assert 0.7 < agg["field_accuracy"] < 0.8
    assert agg["total_exact_rate"] == 0.5


# --- rendering (needs Pillow) ----------------------------------------------
def test_render_load_writes_images_and_ground_truth(tmp_path):
    pytest.importorskip("PIL")
    from freight_audit.synth import render_load
    out = render_load(generate_load(seed=4), str(tmp_path))
    for key in ("invoice", "rate_confirmation", "pod", "ground_truth", "bundle"):
        assert os.path.exists(out[key])
    gt = json.load(open(out["ground_truth"]))
    assert gt["invoice"]["billed_total_cents"] == sum(gt["invoice"]["line_amounts_cents"])


# --- the labeled OCR benchmark: render -> OCR -> score (needs tesseract) ----
def test_synthetic_invoice_ocr_benchmark(tmp_path):
    pytest.importorskip("PIL")
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not installed")
    from freight_audit.synth import render_invoice, ocr_invoice_fields
    from freight_audit.ocr import extract_receipt, FREIGHT_INVOICE_LAYOUT

    bundle = generate_load(seed=2, with_detention=True)
    img_path = tmp_path / "invoice.png"
    render_invoice(bundle).save(str(img_path))

    receipt = extract_receipt(str(img_path), use_preprocess=False,
                              layout=FREIGHT_INVOICE_LAYOUT)
    score = score_invoice(ocr_invoice_fields(receipt), invoice_ground_truth(bundle))
    # OCR of clean rendered text should recover the invoice total exactly and
    # most line amounts -- a real, PII-free accuracy benchmark.
    assert score["checks"]["billed_total"], score
    assert score["line_amount_recall"] >= 0.66, score
