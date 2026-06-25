"""
Render synthetic load bundles to document IMAGES (+ ground-truth sidecars).

Pillow-based (same approach as tests/fixtures/make_sample_receipt.py) -- no heavy
system deps, and the invoice is laid out in the two-column "DESCRIPTION ... $AMOUNT"
shape the `freight_invoice` OCR layout profile parses, so an image round-trips
cleanly back through the OCR pipeline for accuracy benchmarking.

Pillow is import-guarded: value generation (generate.py) works without it; only
rendering needs the `synth` (or `ocr`) extra.
"""
from __future__ import annotations

import json
import os

from PIL import Image, ImageDraw, ImageFont

from ..models import to_cents
from .generate import invoice_ground_truth

_FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
)


def _font(size: int):
    for path in _FONT_PATHS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _money(cents: int) -> str:
    return "$" + f"{cents / 100:,.2f}"


def _row(label: str, cents: int, width: int = 46) -> str:
    """A monospace two-column row: label left, $amount flush-ish right."""
    amt = _money(cents)
    pad = max(2, width - len(label) - len(amt))
    return f"{label}{' ' * pad}{amt}"


def _render(lines: list[str], *, width: int = 700, line_h: int = 30,
            pad: int = 26, size: int = 20) -> "Image.Image":
    img = Image.new("RGB", (width, pad * 2 + line_h * len(lines)), "white")
    d = ImageDraw.Draw(img)
    f = _font(size)
    y = pad
    for ln in lines:
        d.text((pad, y), ln, fill="black", font=f)
        y += line_h
    return img


def render_invoice(bundle: dict) -> "Image.Image":
    inv = bundle["invoice"]
    lines = [inv["carrier_name"],
             f"INVOICE  {inv['invoice_number']}",
             f"Date {inv['invoice_date']}   Load {inv['load_id']}",
             ""]
    for li in inv["line_items"]:
        lines.append(_row(li["description"], to_cents(li["amount"])))
    lines += ["", _row("Invoice Total", to_cents(inv["billed_total"]))]
    return _render(lines)


def render_rate_confirmation(bundle: dict) -> "Image.Image":
    rc = bundle["rate_confirmation"]
    lines = [rc["broker_name"],
             "RATE CONFIRMATION",
             f"Load {rc['load_id']}   Carrier {rc['carrier_name']}",
             f"{rc['origin']}  to  {rc['destination']}",
             f"Pickup {rc['pickup_date']}   Free time {rc['free_time_hours']:g} hrs",
             ""]
    for li in rc["line_items"]:
        lines.append(_row(li["description"], to_cents(li["amount"])))
    lines += ["", _row("Agreed Total", to_cents(rc["agreed_total"]))]
    return _render(lines)


def render_pod(bundle: dict) -> "Image.Image":
    pod = bundle["pod"]
    lines = ["PROOF OF DELIVERY",
             f"Load {pod['load_id']}",
             f"Delivered: {'YES' if pod.get('delivered') else 'NO'}",
             f"Arrival   {pod.get('arrival_time', '')}",
             f"Departure {pod.get('departure_time', '')}",
             f"Signed by {pod.get('signed_by', '')}"]
    return _render(lines)


_RENDERERS = {
    "invoice": render_invoice,
    "rate_confirmation": render_rate_confirmation,
    "pod": render_pod,
}


def render_load(bundle: dict, outdir: str, *, also_bundle: bool = True) -> dict:
    """Render all three docs of a bundle to PNGs in outdir, write the integer-cents
    ground-truth sidecar, and (optionally) the engine-ready bundle JSON. Returns a
    dict of written paths."""
    os.makedirs(outdir, exist_ok=True)
    lid = bundle["load_id"]
    out: dict = {}
    for doc, fn in _RENDERERS.items():
        path = os.path.join(outdir, f"{lid}_{doc}.png")
        fn(bundle).save(path)
        out[doc] = path
    gt = {"load_id": lid, "invoice": invoice_ground_truth(bundle)}
    gt_path = os.path.join(outdir, f"{lid}.gt.json")
    with open(gt_path, "w") as fh:
        json.dump(gt, fh, indent=2)
    out["ground_truth"] = gt_path
    if also_bundle:
        b_path = os.path.join(outdir, f"{lid}.bundle.json")
        with open(b_path, "w") as fh:
            json.dump(bundle, fh, indent=2)
        out["bundle"] = b_path
    return out
