"""
OCR accuracy benchmark runner: render synthetic bundles -> OCR with the
freight_invoice layout -> score against integer-cents ground truth.

This is what ties the three tracks together into one number. Heavy imports
(Pillow, the ocr extra, the tesseract binary) are done lazily so importing this
module stays cheap and dependency-free.
"""
from __future__ import annotations

import os
import tempfile


def benchmark_bundles(bundles, *, use_preprocess: bool = False) -> dict:
    """Render each bundle's invoice, OCR it, and score vs ground truth.
    Returns the aggregate report plus per-document scores."""
    from .render import render_invoice
    from .generate import invoice_ground_truth
    from .score import ocr_invoice_fields, score_invoice, aggregate_scores
    from ..ocr import extract_receipt, FREIGHT_INVOICE_LAYOUT

    scores = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, bundle in enumerate(bundles):
            path = os.path.join(tmp, f"invoice_{i}.png")
            render_invoice(bundle).save(path)
            receipt = extract_receipt(path, use_preprocess=use_preprocess,
                                      layout=FREIGHT_INVOICE_LAYOUT)
            scores.append(score_invoice(ocr_invoice_fields(receipt),
                                        invoice_ground_truth(bundle)))
    agg = aggregate_scores(scores)
    agg["per_document"] = scores
    return agg
