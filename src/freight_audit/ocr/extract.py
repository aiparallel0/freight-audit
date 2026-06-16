"""
A REAL extraction provider (not a stub).

This is the answer to item #1 on the "not ready" list -- "Real OCR extraction ...
the single biggest gap between 'runs on samples' and 'runs on a client'." Here the
`_raw()` that used to raise NotImplementedError actually runs: it preprocesses a
photo, OCRs it with Tesseract, and maps the noisy text into clean structured
fields.

The document here is a retail receipt (the demo image provided), but the SHAPE is
identical to the freight case: photo -> OCR -> noisy text -> field mapping ->
clean dataclass -> validation. Swapping Tesseract for AWS Textract / Google
Document AI / Veryfi is changing one method; the parsing/validation around it is
the part that's yours, and the part that's the moat.

It also demonstrates items #2 (field mapping for a real layout), #3 (a vocabulary
of line patterns), and #6 (handling messy real OCR -- note the noise-tolerant
money parser that repairs '33,92' -> 33.92 and 'RH'/'RN' -> RM).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pytesseract
from PIL import Image

try:
    from .preprocess import preprocess
    import cv2
    _HAVE_CV = True
except Exception:  # pragma: no cover - preprocessing optional
    _HAVE_CV = False

from .layouts import LayoutProfile, DEFAULT_LAYOUT


# ---------------------------------------------------------------------------
# structured output
# ---------------------------------------------------------------------------
@dataclass
class ReceiptLine:
    description: str
    qty: Optional[float]
    unit_price: Optional[float]
    line_total: Optional[float]
    raw: str = ""

    def __repr__(self):
        return f"<{self.description!r} qty={self.qty} unit={self.unit_price} total={self.line_total}>"


@dataclass
class Receipt:
    merchant: Optional[str] = None
    invoice_marker: Optional[str] = None
    datetime_str: Optional[str] = None
    lines: list[ReceiptLine] = field(default_factory=list)
    declared_item_count: Optional[int] = None
    declared_qty_count: Optional[int] = None
    total: Optional[float] = None
    rounding_adjustment: Optional[float] = None
    total_rounded: Optional[float] = None
    cash: Optional[float] = None
    change: Optional[float] = None
    currency: str = "RM"
    raw_text: str = ""


# ---------------------------------------------------------------------------
# noise-tolerant money parsing (item #6: messy real OCR)
# ---------------------------------------------------------------------------
_MONEY_RE = re.compile(r"(-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})|-?\d+[.,]\d{2}|-?\d+)")


def parse_money(token: str) -> Optional[float]:
    """Repair OCR money noise: '33,92' -> 33.92, '1.234,56' tolerated, 'RM§0.00'.
    Returns dollars/ringgit as float, or None."""
    if token is None:
        return None
    t = token.strip()
    # common OCR letter->digit fixes inside numbers
    t = (t.replace("§", "5").replace("O", "0").replace("o", "0")
           .replace("l", "1").replace("I", "1").replace("S", "5").replace("B", "8"))
    t = re.sub(r"[^\d.,\-]", "", t)
    if not t or t in {"-", ".", ","}:
        return None
    # if both separators present, the last one is the decimal
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        # a single comma with exactly 2 trailing digits => decimal comma (33,92)
        if re.search(r",\d{2}$", t):
            t = t.replace(",", ".")
        else:
            t = t.replace(",", "")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def _find_amount_after(label_variants, text) -> Optional[float]:
    for line in text.splitlines():
        up = line.upper()
        for lab in label_variants:
            if lab in up:
                m = _MONEY_RE.findall(line)
                if m:
                    return parse_money(m[-1])
    return None


# ---------------------------------------------------------------------------
# the OCR step (this is the part you swap for a cloud API)
# ---------------------------------------------------------------------------
def ocr_image(image_path: str, use_preprocess: bool = True) -> str:
    """Photo -> raw text. Swap this body for Textract/Google/Veryfi in production;
    the parser below is unchanged."""
    if use_preprocess and _HAVE_CV:
        binary = preprocess(image_path, scale=2.0)
        tmp = image_path + ".pre.png"
        cv2.imwrite(tmp, binary)
        try:
            return pytesseract.image_to_string(Image.open(tmp), config=r"--oem 3 --psm 6")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    return pytesseract.image_to_string(Image.open(image_path), config=r"--oem 3 --psm 6")


def ocr_mean_confidence(image_path: str, use_preprocess: bool = True) -> float:
    """Tesseract's own per-word confidence, averaged (0-100). A document-level
    signal: low mean confidence => route the whole doc to human review (item #6)."""
    img_path = image_path
    tmp = None
    try:
        if use_preprocess and _HAVE_CV:
            binary = preprocess(image_path, scale=2.0)
            tmp = image_path + ".conf.png"
            cv2.imwrite(tmp, binary)
            img_path = tmp
        data = pytesseract.image_to_data(
            Image.open(img_path), config=r"--oem 3 --psm 6",
            output_type=pytesseract.Output.DICT)
        confs = [int(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and int(c) >= 0]
        return round(sum(confs) / len(confs), 1) if confs else 0.0
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------------------
# field mapping (items #2 + #3: real layout + line vocabulary)
# ---------------------------------------------------------------------------
# a priced line looks like: "8970669    1 X 19.00   19.00"
_LINE_WITH_PRICE = re.compile(
    r"(?P<code>\d{6,})\s+(?P<qty>\d+)\s*[xX]\s*(?P<unit>\d+[.,]\d{2})\s+(?P<total>\d+[.,]\d{2})")


def parse_receipt(text: str, layout: "LayoutProfile | None" = None) -> Receipt:
    layout = layout or DEFAULT_LAYOUT
    line_re = layout.compiled_line()
    dt_re = layout.compiled_datetime()
    stop = [w.upper() for w in layout.description_stopwords]
    r = Receipt(raw_text=text, currency=layout.currency)
    lines = [ln.rstrip() for ln in text.splitlines()]

    # merchant = first line mentioning a company suffix
    suffix_re = re.compile(r"\b(" + "|".join(re.escape(s) for s in layout.merchant_suffixes) + r")\b")
    for ln in lines:
        if suffix_re.search(ln.upper()) and len(ln.strip()) > 5:
            r.merchant = re.sub(r"\s+", " ", ln.strip())
            break

    if any("INVOICE" in ln.upper() for ln in lines):
        r.invoice_marker = "INVOICE"

    for ln in lines:
        m = dt_re.search(ln)
        if m:
            r.datetime_str = m.group(1)
            break

    # priced lines. Two layout shapes are supported by the profile's regex:
    #   (a) the line regex itself has a 'desc' group (description-inline, tabular)
    #   (b) it doesn't -> use the best *preceding* descriptive line (SKU-preceded)
    desc_candidates: list[str] = []

    def _is_sku_like(s: str) -> bool:
        return bool(re.fullmatch(r"[A-Z0-9]{2,}[\- ]+\d{1,3}", s.strip()))

    def _best_desc() -> str:
        best, best_score = "", -1
        for c in desc_candidates:
            score = sum(ch.isalpha() for ch in c)
            if not _is_sku_like(c) and score > best_score:
                best, best_score = c, score
        return best

    has_desc_group = "desc" in line_re.groupindex
    for ln in lines:
        m = line_re.search(ln)
        if m and m.group("total"):
            if has_desc_group:
                desc = (m.group("desc") or "").strip()
            else:
                desc = _best_desc()
            # guard: skip lines whose 'description' is actually a totals/footer label
            if has_desc_group and any(k in desc.upper() for k in stop):
                continue
            r.lines.append(ReceiptLine(
                description=re.sub(r"\s+", " ", desc.strip()) or "(unnamed item)",
                qty=parse_money(m.group("qty")) if "qty" in m.groupdict() else None,
                unit_price=parse_money(m.group("unit")) if "unit" in m.groupdict() else None,
                line_total=parse_money(m.group("total")),
                raw=ln.strip(),
            ))
            desc_candidates = []
        else:
            up = ln.upper()
            if re.search(r"[A-Z]", up) and not any(k in up for k in stop):
                desc_candidates.append(ln)

    # totals block -- label keywords come from the layout profile
    def _amt(field_key, fallback_labels=()):
        labels = layout.field_labels.get(field_key, list(fallback_labels))
        return _find_amount_after(tuple(l.upper() for l in labels), text)

    r.total = _amt("total", ("TOTAL ",)) or _find_amount_after(("TOTAL",), text)
    r.total_rounded = _amt("total_rounded", ("TOTAL ROUNDED", "ROUNDED"))
    r.rounding_adjustment = _amt("rounding_adjustment", ("ROUNDING",))
    r.cash = _amt("cash", ("CASH",))
    r.change = _amt("change", ("CHANGE",))

    m = re.search(r"ITEM\(?S?\)?\s*[:.]?\s*(\d+)", text.upper())
    if m:
        r.declared_item_count = int(m.group(1))
    m = re.search(r"QTY\(?S?\)?\s*[:.]?\s*(\d+)", text.upper())
    if m:
        r.declared_qty_count = int(m.group(1))

    return r


def extract_receipt(image_path: str, use_preprocess: bool = True,
                    layout: "LayoutProfile | None" = None) -> Receipt:
    """Top-level: photo -> structured Receipt, using an optional layout profile."""
    return parse_receipt(ocr_image(image_path, use_preprocess=use_preprocess), layout=layout)
