"""
Synthetic, PII-free freight document generation + OCR accuracy benchmarking.

Three layers, importable independently:
  - generate.py : PII-free load bundles (pure stdlib; always available)
  - render.py   : render bundles to document images + ground truth (needs Pillow)
  - score.py    : score OCR output vs integer-cents ground truth (stdlib)

This closes the "needs real documents" gap without any real data: you own the
ground truth, and the rendered invoices round-trip through the `freight_invoice`
OCR layout for a labeled accuracy benchmark.
"""
from .generate import generate_load, sample_loads, invoice_ground_truth
from .score import (
    score_invoice, aggregate_scores, ocr_invoice_fields, normalize_text,
)

# Rendering needs Pillow (the `synth`/`ocr` extra); keep it optional.
try:
    from .render import (
        render_invoice, render_rate_confirmation, render_pod, render_load,
    )
    HAVE_RENDER = True
except ImportError:  # pragma: no cover - exercised only without Pillow
    HAVE_RENDER = False

__all__ = [
    "generate_load", "sample_loads", "invoice_ground_truth",
    "score_invoice", "aggregate_scores", "ocr_invoice_fields", "normalize_text",
    "render_invoice", "render_rate_confirmation", "render_pod", "render_load",
    "HAVE_RENDER",
]
