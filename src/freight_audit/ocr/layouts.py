"""
Config-driven layout profiles (improves "not ready" item #2).

Before: parse_receipt() had one hardcoded layout baked in. Every new document
layout (a different carrier's invoice, a different POD form) would mean editing the
parser. Now a layout is a PROFILE -- a set of named regex patterns plus a few
options -- so onboarding a new document type is writing a profile (data), not code.

A profile says, per field, "here's how to find it in THIS layout." The generic
parser applies the profile. Ship a default; add one per client document type.

This is exactly the "tuned per client against their real samples" work, made into
configuration instead of surgery. (And the patterns themselves accumulate into your
moat -- a competitor starts with none.)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LayoutProfile:
    name: str = "generic_receipt"
    # regex (with named groups) that matches a priced line item.
    # must expose groups: qty, unit, total  (and optionally code)
    line_item_pattern: str = (
        r"(?P<code>\d{6,})\s+(?P<qty>\d+)\s*[xX]\s*"
        r"(?P<unit>\d+[.,]\d{2})\s+(?P<total>\d+[.,]\d{2})")
    # label keywords for each scalar field (case-insensitive substring match)
    field_labels: dict[str, list[str]] = field(default_factory=lambda: {
        "total": ["total "],
        "total_rounded": ["total rounded", "rounded"],
        "rounding_adjustment": ["rounding"],
        "cash": ["cash"],
        "change": ["change"],
    })
    # words that mark a line as NOT a product description (footer/address/totals)
    description_stopwords: list[str] = field(default_factory=lambda: [
        "TOTAL", "ROUNDING", "CASH", "CHANGE", "ITEM(S)", "QTY", "INVOICE",
        "SDN BHD", "CO.REG", "JALAN", "KAWASAN", "KEMBANGAN", "OPERATOR",
        "EXCHANGE", "REFUND", "RECEIPT", "TESCO", "DIY", "PERINDUSTRIAN", "SELANGOR",
    ])
    merchant_suffixes: list[str] = field(default_factory=lambda: [
        "SDN BHD", "BHD", "LLC", "INC", "LTD", "CO",
    ])
    datetime_pattern: str = r"\b(\d{2}-\d{2}-\d{2,4}\s+\d{2}:\d{2})\b"
    currency: str = "RM"

    @classmethod
    def load(cls, path: str) -> "LayoutProfile":
        with open(path) as fh:
            d = json.load(fh)
        base = cls()
        return cls(
            name=d.get("name", base.name),
            line_item_pattern=d.get("line_item_pattern", base.line_item_pattern),
            field_labels=d.get("field_labels", base.field_labels),
            description_stopwords=d.get("description_stopwords", base.description_stopwords),
            merchant_suffixes=d.get("merchant_suffixes", base.merchant_suffixes),
            datetime_pattern=d.get("datetime_pattern", base.datetime_pattern),
            currency=d.get("currency", base.currency),
        )

    def compiled_line(self) -> re.Pattern:
        return re.compile(self.line_item_pattern)

    def compiled_datetime(self) -> re.Pattern:
        return re.compile(self.datetime_pattern)


# the shipped default (matches the MR D.I.Y. style receipt)
DEFAULT_LAYOUT = LayoutProfile()


# An alternative layout, to prove the system generalizes: a "tabular" invoice where
# the line is "DESCRIPTION ........ QTY  UNIT  TOTAL" (description-first, no SKU).
TABULAR_LAYOUT = LayoutProfile(
    name="tabular_invoice",
    line_item_pattern=(
        r"(?P<desc>.+?)\s+(?P<qty>\d+)\s+"
        r"\$?(?P<unit>\d+[.,]\d{2})\s+\$?(?P<total>\d+[.,]\d{2})\s*$"),
    field_labels={
        "total": ["subtotal", "total"],
        "total_rounded": ["amount due", "total due"],
        "cash": ["paid", "tendered"],
        "change": ["change"],
    },
    datetime_pattern=r"\b(\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2})\b",
    currency="USD",
)

# The domain layout: a freight carrier invoice / rate con where accessorials are
# listed two-column as "DESCRIPTION ............ $AMOUNT" (no qty/unit columns) and
# money carries a $ sign and thousands separators ("$1,900.00"). This is the most
# common real freight format and the one the retail-receipt layouts don't cover.
FREIGHT_INVOICE_LAYOUT = LayoutProfile(
    name="freight_invoice",
    line_item_pattern=(
        r"(?P<desc>[A-Za-z][A-Za-z0-9 &/().',-]*?)\s+"
        r"\$?(?P<total>\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})\s*$"),
    field_labels={
        "total": ["invoice total", "total due", "balance due", "amount due", "total"],
    },
    description_stopwords=[
        "INVOICE", "TOTAL", "SUBTOTAL", "BALANCE", "AMOUNT", "DUE", "REMIT",
        "BILL TO", "SHIP TO", "LOAD", "BOL", "DATE", "TERMS", "CARRIER",
        "BROKER", "PAGE",
    ],
    merchant_suffixes=[
        "LLC", "INC", "LTD", "CO", "FREIGHT", "LOGISTICS", "CARRIERS",
        "TRANSPORT", "TRUCKING", "TRANSPORTATION",
    ],
    datetime_pattern=r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b",
    currency="USD",
)

LAYOUTS = {
    "generic_receipt": DEFAULT_LAYOUT,
    "tabular_invoice": TABULAR_LAYOUT,
    "freight_invoice": FREIGHT_INVOICE_LAYOUT,
}
