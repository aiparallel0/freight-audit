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
# Real OCR adapters -- STUBS showing exactly where each API plugs in.
# Fill in the marked TODO when you have keys. The rest of the system is unchanged.
#
# WORKING REFERENCE: the `freight_audit.ocr` subpackage is a complete, runnable
# OCR extractor (Tesseract + preprocessing + a noise-tolerant parser + validation)
# that processes a document image end to end. It demonstrates the exact pattern
# these adapters follow -- photo -> OCR -> noisy text -> field mapping -> clean
# structure. Use it as the template when wiring a cloud API in here.
# ---------------------------------------------------------------------------
class TextractProvider(ExtractionProvider):
    """AWS Textract AnalyzeExpense / AnalyzeDocument(QUERIES).
    Often the cheapest option at scale for receipt-style documents."""

    def __init__(self, client=None, queries: dict | None = None):
        self.client = client            # boto3.client("textract")
        self.queries = queries or {}

    def _raw(self, source) -> dict:
        # TODO: call self.client.analyze_expense(Document={'Bytes': open(source,'rb').read()})
        #       or analyze_document(..., FeatureTypes=['QUERIES'], QueriesConfig=...)
        #       then flatten ExpenseDocuments / Blocks into a flat dict of fields.
        raise NotImplementedError(
            "Wire up boto3 Textract here. Map its fields, then reuse the JSON shape "
            "JsonFixtureProvider expects. The engine and tests do not change.")

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
