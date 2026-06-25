"""
OCR accuracy harness for hard scans.

degrade() applies rotation / blur / gaussian noise to a document image to simulate a
poor scan; benchmark_hardscan() renders synthetic invoices, degrades them, OCRs with
the freight layout, and scores against ground truth -- so the per-format accuracy of
a layout can be measured under degradation. Needs Pillow + tesseract.

BLOCKED (real accuracy numbers): a corpus of real hard scans is required to measure
true field accuracy; the harness here runs on synthetic degraded fixtures.
"""
from __future__ import annotations

import os
import random
import tempfile


def degrade(image, *, rotate: float = 0.0, blur: float = 0.0, noise: float = 0.0):
    """Return a degraded copy of a PIL image."""
    from PIL import ImageFilter
    img = image.convert("RGB")
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if noise:
        px = img.load()
        w, h = img.size
        rnd = random.Random(0)
        for _ in range(int(w * h * noise)):
            x, y = rnd.randrange(w), rnd.randrange(h)
            v = rnd.randint(0, 255)
            px[x, y] = (v, v, v)
    return img


def benchmark_hardscan(bundles, *, rotate: float = 0.0, blur: float = 0.0,
                       noise: float = 0.0) -> dict:
    """Render -> degrade -> OCR (freight layout) -> score; returns an accuracy report."""
    from .synth.render import render_invoice
    from .synth.generate import invoice_ground_truth
    from .synth.score import ocr_invoice_fields, score_invoice, aggregate_scores
    from .ocr import extract_receipt, FREIGHT_INVOICE_LAYOUT

    scores = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, bundle in enumerate(bundles):
            img = degrade(render_invoice(bundle), rotate=rotate, blur=blur, noise=noise)
            path = os.path.join(tmp, f"hard_{i}.png")
            img.save(path)
            receipt = extract_receipt(path, use_preprocess=True, layout=FREIGHT_INVOICE_LAYOUT)
            scores.append(score_invoice(ocr_invoice_fields(receipt),
                                        invoice_ground_truth(bundle)))
    agg = aggregate_scores(scores)
    agg["degradation"] = {"rotate": rotate, "blur": blur, "noise": noise}
    return agg
