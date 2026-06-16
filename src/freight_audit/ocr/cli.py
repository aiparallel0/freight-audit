#!/usr/bin/env python3
"""Command-line interface for the freight_audit OCR pipeline.

Installed as the ``freight-audit-ocr`` console script:

    freight-audit-ocr path/to/receipt.jpg          # extract + validate
    freight-audit-ocr receipt.jpg --no-preprocess  # raw vs preprocessed OCR
    freight-audit-ocr receipt.jpg --json out.json  # structured output
    freight-audit-ocr receipt.jpg --layout tabular_invoice

Bring your own document image -- none is bundled. Requires the ``ocr`` extra
(pytesseract, opencv-python-headless, pillow) and the system ``tesseract`` binary.
"""
from __future__ import annotations

import argparse
import json
import sys

from .extract import extract_receipt, ocr_mean_confidence
from .layouts import LAYOUTS
from .validate import validate

C = {"ok": "\033[92m", "info": "\033[96m", "warn": "\033[93m",
     "block": "\033[91m", "end": "\033[0m"}


def _fmt(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "-"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="freight-audit-ocr", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="path to a document image (jpg/png)")
    ap.add_argument("--layout", choices=list(LAYOUTS), default="generic_receipt",
                    help="layout profile to use (default: generic_receipt)")
    ap.add_argument("--no-preprocess", action="store_true")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--json", help="write structured JSON here")
    args = ap.parse_args(argv)

    def col(sev):
        return sev.upper() if args.no_color else f"{C.get(sev,'')}{sev.upper()}{C['end']}"

    layout = LAYOUTS[args.layout]
    print("=" * 70)
    print("FREIGHT-AUDIT OCR  --  photo -> OCR -> structured -> validated")
    print("=" * 70)
    print(f"image: {args.image}")
    print(f"layout: {args.layout}   preprocess: {'OFF' if args.no_preprocess else 'ON'}\n")

    receipt = extract_receipt(args.image, use_preprocess=not args.no_preprocess, layout=layout)

    print("--- EXTRACTED FIELDS ---")
    print(f"  merchant      : {receipt.merchant}")
    print(f"  doc type      : {receipt.invoice_marker or '-'}")
    print(f"  datetime      : {receipt.datetime_str or '-'}")
    print(f"  currency      : {receipt.currency}")
    print("  line items:")
    for ln in receipt.lines:
        print(f"    - {ln.description:<34} qty={_fmt(ln.qty)}  "
              f"unit={_fmt(ln.unit_price)}  total={_fmt(ln.line_total)}")
    print(f"\n  TOTAL          : {_fmt(receipt.total)}")
    print(f"  TOTAL ROUNDED  : {_fmt(receipt.total_rounded)}")
    print(f"  CASH / CHANGE  : {_fmt(receipt.cash)} / {_fmt(receipt.change)}")

    try:
        conf = ocr_mean_confidence(args.image, use_preprocess=not args.no_preprocess)
        print(f"  OCR confidence : {conf}%")
    except Exception:
        pass

    result = validate(receipt)
    print("\n--- VALIDATION ---")
    for c in result.checks:
        print(f"  [{col(c.severity.value)}] {c.name}: {c.message}")

    print("\n" + "=" * 70)
    verdict = ("CONFIDENT -- extraction is internally consistent"
               if result.confident else
               "NEEDS REVIEW -- a human should verify flagged fields")
    print(f"VERDICT: {col(result.severity.value)}  {verdict}")
    print("=" * 70)

    if args.json:
        out = {
            "merchant": receipt.merchant, "datetime": receipt.datetime_str,
            "currency": receipt.currency,
            "lines": [vars(l) for l in receipt.lines],
            "total": receipt.total, "total_rounded": receipt.total_rounded,
            "cash": receipt.cash, "change": receipt.change,
            "validation": {
                "severity": result.severity.value, "confident": result.confident,
                "checks": [{"name": c.name, "severity": c.severity.value,
                            "message": c.message} for c in result.checks],
            },
        }
        open(args.json, "w").write(json.dumps(out, indent=2))
        print(f"\nJSON written -> {args.json}")

    return 0 if result.confident else 2


if __name__ == "__main__":
    raise SystemExit(main())
