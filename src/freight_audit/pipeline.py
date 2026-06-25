"""
Image -> audit bridge (PROMPTS #2).

The OCR pipeline and the matching engine were separate: OCR turned a photo into a
Receipt, the engine audited dataclasses, but nothing joined them. This module is
the thin bridge -- OCR each document image, map the extracted fields into the
engine's RateConfirmation / CarrierInvoice / ProofOfDelivery, and run MatchEngine.

OCR is imported lazily so the core engine still installs/runs without the `ocr`
extra; money goes through to_cents (integer cents), and a client ClientProfile can
be applied exactly like process_load() does.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import (
    CarrierInvoice, RateConfirmation, ProofOfDelivery, LineItem, MatchResult,
    FindingType, to_cents,
)
from .match import MatchEngine

_LOAD_RE = re.compile(r"\b(?:load|ref(?:erence)?|pro)\b\s*[#:]?\s*([A-Za-z0-9][A-Za-z0-9\-]{3,})", re.I)
_DT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}|\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2})")
_ARRIVAL_RE = re.compile(r"arriv\w*\s*[:\-]?\s*(.+)", re.I)
_DEPART_RE = re.compile(r"depart\w*\s*[:\-]?\s*(.+)", re.I)
_SIGNED_RE = re.compile(r"sign(?:ed)?(?:\s*by)?\s*[:\-]?\s*([A-Za-z][A-Za-z.''\- ]+)", re.I)


def _ocr(image_path: str):
    # lazy import: the `ocr` extra (pytesseract/opencv/pillow) stays optional
    from .ocr import extract_receipt, FREIGHT_INVOICE_LAYOUT
    return extract_receipt(image_path, use_preprocess=False, layout=FREIGHT_INVOICE_LAYOUT)


def _load_id(text: str, default: str = "") -> str:
    m = _LOAD_RE.search(text or "")
    return m.group(1) if m else default


def _line_items(receipt) -> list[LineItem]:
    items = []
    for line in receipt.lines:
        if line.line_total is None:
            continue
        items.append(LineItem(description=line.description,
                              amount_cents=to_cents(line.line_total)))
    return items


def _parse_dt(value: str):
    from .extract import _parse_dt as parse
    m = _DT_RE.search(value or "")
    return parse(m.group(1)) if m else None


def _to_invoice(receipt) -> CarrierInvoice:
    return CarrierInvoice(
        invoice_number="", load_id=_load_id(receipt.raw_text),
        carrier_name=receipt.merchant or "",
        billed_total_cents=to_cents(receipt.total or 0),
        line_items=_line_items(receipt))


def _to_rate_con(receipt) -> RateConfirmation:
    return RateConfirmation(
        load_id=_load_id(receipt.raw_text), broker_name=receipt.merchant or "",
        carrier_name="", origin="", destination="",
        agreed_total_cents=to_cents(receipt.total or 0),
        line_items=_line_items(receipt))


def _to_pod(receipt) -> ProofOfDelivery:
    raw = receipt.raw_text or ""
    arr, dep, sign = _ARRIVAL_RE.search(raw), _DEPART_RE.search(raw), _SIGNED_RE.search(raw)
    return ProofOfDelivery(
        load_id=_load_id(raw), delivered="not delivered" not in raw.lower(),
        arrival_time=_parse_dt(arr.group(1)) if arr else None,
        departure_time=_parse_dt(dep.group(1)) if dep else None,
        signed_by=sign.group(1).strip() if sign else None)


def audit_documents(rate_con_img: Optional[str] = None,
                    invoice_img: Optional[str] = None,
                    pod_img: Optional[str] = None, *,
                    profile=None, engine: MatchEngine | None = None) -> MatchResult:
    """OCR up to three document images and audit them. Any image may be None.
    Returns a MatchResult, exactly as process_load() would from JSON."""
    rc = _to_rate_con(_ocr(rate_con_img)) if rate_con_img else None
    inv = _to_invoice(_ocr(invoice_img)) if invoice_img else None
    pod = _to_pod(_ocr(pod_img)) if pod_img else None

    if engine is None:
        engine = MatchEngine(profile.to_engine_config()) if profile else MatchEngine()
    result = engine.match(rc, inv, pod)
    if profile is not None and inv is not None:
        from .profiles import apply_profile_rules
        extra = apply_profile_rules(profile, rc, inv)
        if extra and len(result.findings) == 1 and result.findings[0].type == FindingType.OK:
            result.findings = []
        result.findings.extend(extra)
    return result


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="freight-audit-pipeline",
        description="OCR rate-con / invoice / POD images and audit them end to end.")
    ap.add_argument("--rate-con", help="rate confirmation image")
    ap.add_argument("--invoice", help="carrier invoice image")
    ap.add_argument("--pod", help="proof-of-delivery image")
    ap.add_argument("--profile", help="path to a client ClientProfile JSON")
    args = ap.parse_args(argv)
    if not any([args.rate_con, args.invoice, args.pod]):
        ap.error("provide at least one of --rate-con / --invoice / --pod")
    profile = None
    if args.profile:
        from .profiles import ClientProfile
        profile = ClientProfile.load(args.profile)
    result = audit_documents(args.rate_con, args.invoice, args.pod, profile=profile)
    print(result.summary())
    return 0 if result.severity.value != "block" else 2


if __name__ == "__main__":
    raise SystemExit(main())
