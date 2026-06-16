"""Generate a synthetic receipt image for OCR tests/demo.

Ships NO personal data: the merchant, items, and totals are invented. The layout
mimics a thermal retail receipt (code / "qty X unit total" lines, totals block,
5-cent rounding) so the generic layout profile parses and validates it.

Run directly to (re)generate tests/fixtures/sample_receipt.png:
    python -m tests.fixtures.make_sample_receipt
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont


LINES = [
    "        SAMPLE MART SDN BHD",
    "         (CO.REG :000000-X)",
    "   LOT 00, JALAN CONTOH, 00000",
    "            -INVOICE-",
    "-----------------------------------",
    "WIDGET ALPHA 10CM",
    "AA0001 - 01",
    "1000001          1 X 12.00   12.00",
    "GADGET BETA 250ML",
    "BB0002 - 02",
    "1000002          1 X  4.50    4.50",
    "DOOHICKEY GAMMA PACK",
    "CC0003 - 03",
    "1000003          2 X  3.25    6.50",
    "-----------------------------------",
    "Item(s) : 3        Qty(s) : 4",
    "-----------------------------------",
    "TOTAL                    RM 23.00",
    "ROUNDING ADJUSTMENT      -RM 0.00",
    "TOTAL ROUNDED            RM 23.00",
    "CASH                     RM 50.00",
    "CHANGE                   RM 27.00",
    "-----------------------------------",
    "01-01-25 12:00  SH00 ZK00  T0 R000",
    "      THANK YOU - COME AGAIN",
]


def _font(size: int):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make(out_path: str) -> str:
    W, line_h, pad = 520, 26, 24
    H = pad * 2 + line_h * len(LINES)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    f = _font(18)
    y = pad
    for ln in LINES:
        d.text((pad, y), ln, fill="black", font=f)
        y += line_h
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.save(out_path)
    return out_path


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    p = make(os.path.join(here, "sample_receipt.png"))
    print(f"wrote {p}")
