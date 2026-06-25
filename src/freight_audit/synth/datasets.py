"""
Adapters that map EXTERNAL invoice/receipt datasets into the score.py `truth` shape,
so the same OCR-accuracy harness benchmarks against them once downloaded locally.

These are pure record -> dict mappers (no network, no dataset deps): you load the
dataset yourself (Hugging Face `datasets`, a JSON/JSONL dir, etc.) and pass each
record's annotation here. Money is parsed with to_cents so it matches how the OCR
side is parsed -- the benchmark measures "did OCR read the same number as the label".

Coverage differs by dataset; score_invoice only scores the fields a truth dict
actually provides:
  - CORD     : line items + total (no vendor)         -> cord_truth()
  - SROIE    : company + total (no line items)         -> sroie_truth()
  - FATURA / any KIE set of labelled entities          -> kie_entities_truth()
"""
from __future__ import annotations

import json

from ..models import to_cents


def _truth(*, carrier_name=None, invoice_number=None, billed_total_cents=0,
           line_amounts_cents=None, line_descriptions=None) -> dict:
    return {
        "carrier_name": carrier_name,
        "invoice_number": invoice_number,
        "billed_total_cents": billed_total_cents,
        "line_amounts_cents": list(line_amounts_cents or []),
        "line_descriptions": list(line_descriptions or []),
    }


# --- CORD (naver-clova-ix/cord-v2) -----------------------------------------
def cord_truth(ground_truth) -> dict:
    """Map a CORD `ground_truth` (dict or JSON string, with `gt_parse`) to truth.
    Uses menu[].price as line amounts and total.total_price as the total."""
    gt = json.loads(ground_truth) if isinstance(ground_truth, str) else ground_truth
    parse = gt.get("gt_parse", gt)
    menu = parse.get("menu", [])
    if isinstance(menu, dict):
        menu = [menu]
    line_cents, descs = [], []
    for item in menu:
        if item.get("price") is not None:
            line_cents.append(to_cents(item["price"]))
            descs.append(str(item.get("nm", "")))
    total = (parse.get("total") or {}).get("total_price")
    return _truth(
        billed_total_cents=to_cents(total) if total is not None else 0,
        line_amounts_cents=line_cents, line_descriptions=descs)


# --- SROIE (company / date / address / total) ------------------------------
def sroie_truth(fields: dict) -> dict:
    """Map a SROIE key file ({company, date, address, total}) to truth."""
    total = fields.get("total")
    return _truth(
        carrier_name=fields.get("company"),
        billed_total_cents=to_cents(total) if total else 0)


# --- Generic labelled-entity KIE (FATURA, FUNSD-style, etc.) ----------------
# Best-effort label presets -- CONFIRM the exact class names in your download and
# override as needed (FATURA ships its own class list).
FATURA_LABELS = {
    "total_labels": ("TOTAL", "GROSS_WORTH", "TOTAL_GROSS_WORTH", "AMOUNT_DUE"),
    "item_labels": ("PRICE", "GROSS_WORTH_ITEM", "LINE_TOTAL", "ITEM_GROSS_WORTH"),
    "vendor_labels": ("SELLER", "VENDOR", "SELLER_NAME"),
    "number_labels": ("INVOICE_NO", "INVOICE_NUMBER", "NUMBER"),
}


def kie_entities_truth(entities, *, total_labels, item_labels=(),
                       vendor_labels=(), number_labels=()) -> dict:
    """Map a list of labelled entities -- each {"label"/"class", "text"/"value"} --
    to truth. Label matching is case-insensitive. First match wins for scalars;
    every item-labelled entity contributes a line amount."""
    def lab(e):
        return str(e.get("label") or e.get("class") or "").strip().lower()

    def txt(e):
        v = e.get("text") if e.get("text") is not None else e.get("value")
        return "" if v is None else str(v)

    tl = {s.lower() for s in total_labels}
    il = {s.lower() for s in item_labels}
    vl = {s.lower() for s in vendor_labels}
    nl = {s.lower() for s in number_labels}

    total_cents, vendor, number, items = None, None, None, []
    for e in entities:
        label, text = lab(e), txt(e)
        if not text:
            continue
        if label in tl and total_cents is None:
            total_cents = to_cents(text)
        elif label in il:
            items.append(to_cents(text))
        elif label in vl and vendor is None:
            vendor = text
        elif label in nl and number is None:
            number = text
    return _truth(carrier_name=vendor, invoice_number=number,
                  billed_total_cents=total_cents if total_cents is not None else 0,
                  line_amounts_cents=items)


def fatura_truth(entities) -> dict:
    """FATURA convenience wrapper (verify label names against your download)."""
    return kie_entities_truth(entities, **FATURA_LABELS)
