"""
OCR accuracy scoring harness (the "labeled benchmark" track).

Scores extracted invoice fields against integer-cents ground truth. Money is always
compared in CENTS (via to_cents), never as floats. The same scorer works on the
synthetic ground truth from generate.py and on any external dataset you map into the
same {predicted, truth} shape (e.g. FATURA / CORD / SROIE), so one harness covers
both. Mirrors the validate.py / reporting.py style already in the repo.
"""
from __future__ import annotations

import re
from collections import Counter

from ..models import to_cents


def normalize_text(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def ocr_invoice_fields(receipt) -> dict:
    """Map an OCR Receipt (freight_audit.ocr) into comparable invoice fields
    (carrier_name text + money as dollar floats, to be converted to cents on score)."""
    return {
        "carrier_name": receipt.merchant,
        "billed_total": receipt.total,
        "line_amounts": [l.line_total for l in receipt.lines if l.line_total is not None],
    }


def _multiset_overlap(a: list, b: list) -> int:
    return sum((Counter(a) & Counter(b)).values())


def score_invoice(predicted: dict, truth: dict) -> dict:
    """Score predicted invoice fields vs integer-cents ground truth.

    predicted: {carrier_name, billed_total (dollars), line_amounts (dollars list)}
    truth:     invoice_ground_truth() -> {carrier_name, billed_total_cents,
               line_amounts_cents, ...}
    Returns per-field correctness, an overall field accuracy, and line-amount recall.
    """
    checks: dict = {}

    pt = predicted.get("billed_total")
    checks["billed_total"] = pt is not None and to_cents(pt) == truth["billed_total_cents"]

    pc = normalize_text(predicted.get("carrier_name"))
    truth_first = normalize_text(truth["carrier_name"]).split()[0] if truth.get("carrier_name") else ""
    checks["carrier_name"] = bool(pc) and bool(truth_first) and truth_first in pc

    pred_cents = sorted(to_cents(x) for x in (predicted.get("line_amounts") or []))
    truth_cents = sorted(truth.get("line_amounts_cents") or [])
    matched = _multiset_overlap(pred_cents, truth_cents)
    checks["line_amounts"] = bool(truth_cents) and matched == len(truth_cents)

    n = len(checks)
    correct = sum(checks.values())
    return {
        "checks": checks,
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "line_amount_recall": round(matched / len(truth_cents), 4) if truth_cents else 1.0,
    }


def aggregate_scores(scores: list[dict]) -> dict:
    """Roll up many per-invoice scores into a benchmark summary."""
    if not scores:
        return {"documents": 0, "field_accuracy": 0.0, "line_amount_recall": 0.0}
    n = len(scores)
    field_acc = sum(s["accuracy"] for s in scores) / n
    recall = sum(s["line_amount_recall"] for s in scores) / n
    total_ok = sum(1 for s in scores if s["checks"].get("billed_total")) / n
    return {
        "documents": n,
        "field_accuracy": round(field_acc, 4),
        "line_amount_recall": round(recall, 4),
        "total_exact_rate": round(total_ok, 4),
    }
