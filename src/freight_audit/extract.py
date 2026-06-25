"""
Extraction layer = the COMMODITY part you build ON TOP of, not the part you sell.

The contract: a provider takes raw document bytes (or a parsed JSON fixture) and
returns one of our clean dataclasses. The matching engine never knows or cares
which provider produced them.

Today: `JsonFixtureProvider` reads hand-made sample JSON so the whole pipeline runs
offline with zero API keys or cost -- perfect for the demo and tests.

Tomorrow: drop in `TextractProvider` / `GoogleDocAIProvider` / `VeryfiProvider`.
Their job is ONLY: bytes -> raw fields -> map into RateConfirmation/CarrierInvoice/
ProofOfDelivery. The field-mapping glue (and the accessorial dictionary in
normalize.py) is your accumulated edge, not the OCR.
"""
from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

from .models import (
    CarrierInvoice, LineItem, ProofOfDelivery, RateConfirmation, to_cents,
)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _line_items(raw: list[dict]) -> list[LineItem]:
    items = []
    for li in raw or []:
        items.append(LineItem(
            description=li.get("description", ""),
            amount_cents=to_cents(li.get("amount")),
            quantity=li.get("quantity"),
            rate_cents=to_cents(li["rate"]) if li.get("rate") is not None else None,
        ))
    return items


class ExtractionProvider(ABC):
    """Interface every OCR/extraction backend implements."""

    @abstractmethod
    def extract_rate_confirmation(self, source) -> RateConfirmation: ...

    @abstractmethod
    def extract_invoice(self, source) -> CarrierInvoice: ...

    @abstractmethod
    def extract_pod(self, source) -> ProofOfDelivery: ...


# ---------------------------------------------------------------------------
# Offline provider -- runs today, no keys, no cost
# ---------------------------------------------------------------------------
class JsonFixtureProvider(ExtractionProvider):
    """Reads pre-extracted JSON (what an OCR API *would* hand back after field
    mapping). Lets you run, demo, and test the entire matching engine offline."""

    def _load(self, source) -> dict:
        if isinstance(source, dict):
            return source
        path = Path(source)
        return json.loads(path.read_text())

    def extract_rate_confirmation(self, source) -> RateConfirmation:
        d = self._load(source)
        approved = {}
        for k, v in (d.get("approved_accessorials") or {}).items():
            approved[k] = None if v is None else to_cents(v)
        return RateConfirmation(
            load_id=str(d["load_id"]),
            broker_name=d.get("broker_name", ""),
            carrier_name=d.get("carrier_name", ""),
            origin=d.get("origin", ""),
            destination=d.get("destination", ""),
            agreed_total_cents=to_cents(d.get("agreed_total")),
            line_items=_line_items(d.get("line_items")),
            approved_accessorials=approved,
            pickup_date=_parse_dt(d.get("pickup_date")),
            free_time_hours=float(d.get("free_time_hours", 2.0)),
            detention_flat_fee_cents=(to_cents(d["detention_flat_fee"])
                                      if d.get("detention_flat_fee") is not None else None),
            raw_source=str(source if not isinstance(source, dict) else "inline"),
        )

    def extract_invoice(self, source) -> CarrierInvoice:
        d = self._load(source)
        return CarrierInvoice(
            invoice_number=str(d.get("invoice_number", "")),
            load_id=str(d["load_id"]),
            carrier_name=d.get("carrier_name", ""),
            billed_total_cents=to_cents(d.get("billed_total")),
            line_items=_line_items(d.get("line_items")),
            invoice_date=_parse_dt(d.get("invoice_date")),
            raw_source=str(source if not isinstance(source, dict) else "inline"),
        )

    def extract_pod(self, source) -> ProofOfDelivery:
        d = self._load(source)
        return ProofOfDelivery(
            load_id=str(d["load_id"]),
            delivered=bool(d.get("delivered", True)),
            arrival_time=_parse_dt(d.get("arrival_time")),
            departure_time=_parse_dt(d.get("departure_time")),
            signed_by=d.get("signed_by"),
            raw_source=str(source if not isinstance(source, dict) else "inline"),
        )


# ---------------------------------------------------------------------------
# Real cloud-OCR adapters. `TextractProvider` below is fully implemented (boto3);
# `GoogleDocAIProvider` / `VeryfiProvider` remain STUBS showing where each API
# plugs in -- fill in the marked TODO when you have keys. The rest of the system
# is unchanged either way.
#
# WORKING REFERENCE: the `freight_audit.ocr` subpackage is a complete, runnable
# OCR extractor (Tesseract + preprocessing + a noise-tolerant parser + validation)
# that processes a document image end to end. It demonstrates the exact pattern
# these adapters follow -- photo -> OCR -> noisy text -> field mapping -> clean
# structure. Use it as the template when wiring a cloud API in here.
# ---------------------------------------------------------------------------
# --- Textract response -> our JSON shape ------------------------------------
# AnalyzeExpense returns labelled SummaryFields + LineItemGroups. We flatten the
# standard expense labels onto the keys JsonFixtureProvider already reads. Money
# values are passed through AS-IS (strings like "$2,500.00"); to_cents converts
# them to integer cents downstream -- no float money is ever created here.
_EXPENSE_SUMMARY_MAP = {
    "VENDOR_NAME": ("carrier_name",),
    "RECEIVER_NAME": ("broker_name",),
    "TOTAL": ("billed_total", "agreed_total"),
    "INVOICE_RECEIPT_ID": ("invoice_number",),
    "INVOICE_RECEIPT_DATE": ("invoice_date",),
    "PO_NUMBER": ("load_id",),
}
_EXPENSE_LINEITEM_MAP = {
    "ITEM": "description",
    "PRICE": "amount",
    "UNIT_PRICE": "rate",
    "QUANTITY": "quantity",
}
# Freight-specific fields a generic expense model won't know; AnalyzeDocument
# QUERIES fills them. Aliases match the JSON keys JsonFixtureProvider consumes,
# so a query answer maps straight through.
DEFAULT_FREIGHT_QUERIES = {
    "load_id": "What is the load or reference number?",
    "origin": "What is the origin or pickup city and state?",
    "destination": "What is the destination or delivery city and state?",
    "pickup_date": "What is the pickup date?",
    "arrival_time": "What time did the truck arrive at the stop?",
    "departure_time": "What time did the truck depart the stop?",
    "signed_by": "Who signed for the delivery?",
}
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DELIVERED_FALSE = {"no", "n", "false", "0", "not delivered", "undelivered"}


def _read_document_bytes(source) -> bytes:
    """Accept raw bytes or a path; return the document bytes for Textract."""
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    return Path(source).read_bytes()


def _field_type(field: dict) -> str:
    return ((field.get("Type") or {}).get("Text") or "").upper()


def _field_value(field: dict) -> str:
    return ((field.get("ValueDetection") or {}).get("Text") or "").strip()


def _coerce_quantity(text: str) -> Optional[float]:
    """Quantity is a count/hours, not money -> a plain float is correct here."""
    m = _NUMBER_RE.search(str(text))
    return float(m.group()) if m else None


def _coerce_delivered(value) -> bool:
    return str(value).strip().lower() not in _DELIVERED_FALSE


def _map_expense_response(resp: dict) -> dict:
    """Flatten an AnalyzeExpense response into the fixture JSON shape."""
    out: dict = {}
    for doc in resp.get("ExpenseDocuments") or []:
        for f in doc.get("SummaryFields") or []:
            value = _field_value(f)
            if not value:
                continue
            for key in _EXPENSE_SUMMARY_MAP.get(_field_type(f), ()):
                out.setdefault(key, value)          # first reading wins
        items: list[dict] = []
        for group in doc.get("LineItemGroups") or []:
            for li in group.get("LineItems") or []:
                row: dict = {}
                for f in li.get("LineItemExpenseFields") or []:
                    key = _EXPENSE_LINEITEM_MAP.get(_field_type(f))
                    value = _field_value(f)
                    if not key or not value:
                        continue
                    row[key] = _coerce_quantity(value) if key == "quantity" else value
                if row.get("description") or row.get("amount"):
                    items.append(row)
        if items:
            out.setdefault("line_items", items)
    return out


def _map_query_response(resp: dict) -> dict:
    """Map AnalyzeDocument(QUERIES) answers to {alias: best-confidence text}."""
    blocks = resp.get("Blocks") or []
    by_id = {b.get("Id"): b for b in blocks}
    out: dict = {}
    for b in blocks:
        if b.get("BlockType") != "QUERY":
            continue
        alias = (b.get("Query") or {}).get("Alias")
        if not alias:
            continue
        answer, best_conf = "", -1.0
        for rel in b.get("Relationships") or []:
            if rel.get("Type") != "ANSWER":
                continue
            for aid in rel.get("Ids") or []:
                ans = by_id.get(aid) or {}
                text = (ans.get("Text") or "").strip()
                conf = float(ans.get("Confidence") or 0.0)
                if text and conf > best_conf:
                    answer, best_conf = text, conf
        if answer:
            out[alias] = answer
    return out


class TextractProvider(ExtractionProvider):
    """AWS Textract adapter (AnalyzeExpense + optional AnalyzeDocument QUERIES).

    Strategy: AnalyzeExpense harvests the receipt-style fields (vendor, total,
    invoice id/date, line items) that map cleanly onto a CarrierInvoice; an
    optional QUERIES pass fills freight-specific fields a generic expense model
    can't know (load number, origin/destination, pickup date, POD arrival /
    departure, signed-by). Both responses are flattened into the exact JSON shape
    `JsonFixtureProvider` consumes, so the matching engine -- and every existing
    test -- is unchanged.

    Credentials are never hard-coded: the boto3 client is created lazily and reads
    them from the standard AWS environment (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
    / AWS_SESSION_TOKEN; region from AWS_REGION or AWS_DEFAULT_REGION). boto3 is an
    optional dependency (`pip install 'freight-audit[textract]'`) imported only when
    a client is actually built, so the core engine installs and runs without it.
    Inject a `client` to run tests with no live AWS call.
    """

    def __init__(self, client=None, queries: dict | None = None, *,
                 region_name: str | None = None, use_expense: bool = True):
        self._client = client
        self.queries = dict(DEFAULT_FREIGHT_QUERIES if queries is None else queries)
        self.region_name = region_name
        self.use_expense = use_expense

    @property
    def client(self):
        """Lazily build a boto3 Textract client; import is guarded so boto3 stays
        optional for the core install."""
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - only without the extra
                raise ImportError(
                    "TextractProvider requires boto3. Install with "
                    "`pip install 'freight-audit[textract]'`.") from exc
            region = (self.region_name or os.getenv("AWS_REGION")
                      or os.getenv("AWS_DEFAULT_REGION"))
            self._client = boto3.client("textract", region_name=region)
        return self._client

    def _raw(self, source) -> dict:
        # A dict is already-mapped fields -> pass straight through (fully offline).
        if isinstance(source, dict):
            return source
        data = _read_document_bytes(source)
        fields: dict = {}
        if self.use_expense:
            fields.update(_map_expense_response(
                self.client.analyze_expense(Document={"Bytes": data})))
        if self.queries:
            overlay = _map_query_response(self.client.analyze_document(
                Document={"Bytes": data},
                FeatureTypes=["QUERIES"],
                QueriesConfig={"Queries": [
                    {"Text": q, "Alias": alias} for alias, q in self.queries.items()]},
            ))
            fields.update(overlay)              # explicit queries beat expense guesses
        if "delivered" in fields:
            fields["delivered"] = _coerce_delivered(fields["delivered"])
        fields.setdefault("load_id", "")        # never KeyError in the dataclass map
        return fields

    def extract_rate_confirmation(self, source) -> RateConfirmation:
        return JsonFixtureProvider().extract_rate_confirmation(self._raw(source))

    def extract_invoice(self, source) -> CarrierInvoice:
        return JsonFixtureProvider().extract_invoice(self._raw(source))

    def extract_pod(self, source) -> ProofOfDelivery:
        return JsonFixtureProvider().extract_pod(self._raw(source))


class GoogleDocAIProvider(ExtractionProvider):
    """Google Document AI (Invoice parser / custom processor). Strong on
    mixed-quality, multi-format docs."""

    def __init__(self, processor_name: str | None = None, client=None):
        self.processor_name = processor_name
        self.client = client

    def _raw(self, source) -> dict:
        # TODO: documentai client.process_document(...) -> map entities to the JSON shape.
        raise NotImplementedError("Wire up Google Document AI here.")

    def extract_rate_confirmation(self, source) -> RateConfirmation:
        return JsonFixtureProvider().extract_rate_confirmation(self._raw(source))

    def extract_invoice(self, source) -> CarrierInvoice:
        return JsonFixtureProvider().extract_invoice(self._raw(source))

    def extract_pod(self, source) -> ProofOfDelivery:
        return JsonFixtureProvider().extract_pod(self._raw(source))


class VeryfiProvider(ExtractionProvider):
    """Veryfi -- pre-trained on logistics docs incl. BOL; fastest to a first demo
    on real documents because it needs little setup."""

    def __init__(self, client_id: str | None = None, api_key: str | None = None):
        self.client_id = client_id
        self.api_key = api_key

    def _raw(self, source) -> dict:
        # TODO: POST the document to Veryfi, map the JSON response to our shape.
        raise NotImplementedError("Wire up Veryfi here.")

    def extract_rate_confirmation(self, source) -> RateConfirmation:
        return JsonFixtureProvider().extract_rate_confirmation(self._raw(source))

    def extract_invoice(self, source) -> CarrierInvoice:
        return JsonFixtureProvider().extract_invoice(self._raw(source))

    def extract_pod(self, source) -> ProofOfDelivery:
        return JsonFixtureProvider().extract_pod(self._raw(source))


PROVIDERS = {
    "offline": JsonFixtureProvider,
    "textract": TextractProvider,
    "google": GoogleDocAIProvider,
    "veryfi": VeryfiProvider,
}
