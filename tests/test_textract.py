"""
Tests for the real AWS Textract provider (PROMPTS.md #1).

The boto3 Textract client is mocked -- there is NO live AWS call and boto3 does
not even need to be installed for these to run. They assert that Textract's
AnalyzeExpense / AnalyzeDocument(QUERIES) responses are mapped into the same
RateConfirmation / CarrierInvoice / ProofOfDelivery dataclasses the offline
JsonFixtureProvider produces, with money as integer cents.

Run:  python -m pytest tests/test_textract.py
"""
import sys
from datetime import datetime
from unittest.mock import MagicMock

from freight_audit import (  # noqa: E402
    TextractProvider, JsonFixtureProvider, MatchEngine, MatchResult,
)
from freight_audit.extract import DEFAULT_FREIGHT_QUERIES  # noqa: E402


# ---------------------------------------------------------------------------
# canned Textract responses (shapes copied from the real AnalyzeExpense /
# AnalyzeDocument(QUERIES) API; trimmed to the fields we map)
# ---------------------------------------------------------------------------
def _summary(label, text):
    return {"Type": {"Text": label}, "ValueDetection": {"Text": text}}


def _line(**fields):
    type_for = {"description": "ITEM", "amount": "PRICE",
                "rate": "UNIT_PRICE", "quantity": "QUANTITY"}
    return {"LineItemExpenseFields": [
        {"Type": {"Text": type_for[k]}, "ValueDetection": {"Text": v}}
        for k, v in fields.items()]}


EXPENSE_INVOICE = {
    "ExpenseDocuments": [{
        "SummaryFields": [
            _summary("VENDOR_NAME", "Ironwood Freight Inc"),
            _summary("INVOICE_RECEIPT_ID", "IW-7781"),
            _summary("INVOICE_RECEIPT_DATE", "2026-06-11"),
            _summary("TOTAL", "$2,500.00"),
        ],
        "LineItemGroups": [{"LineItems": [
            _line(description="Linehaul", amount="$1,900.00"),
            _line(description="Fuel Surcharge", amount="$300.00", quantity="1"),
            _line(description="Detention", amount="$160.00",
                  quantity="2", rate="$80.00"),
        ]}],
    }]
}

EXPENSE_RATECON = {
    "ExpenseDocuments": [{
        "SummaryFields": [
            _summary("VENDOR_NAME", "Ironwood Freight Inc"),
            _summary("RECEIVER_NAME", "Cardinal Logistics Brokerage"),
            _summary("TOTAL", "$1,700.00"),
        ],
        "LineItemGroups": [{"LineItems": [
            _line(description="Linehaul", amount="$1,450.00"),
            _line(description="Fuel Surcharge", amount="$250.00"),
        ]}],
    }]
}


def _query_response(answers: dict) -> dict:
    """Build an AnalyzeDocument(QUERIES) response: QUERY blocks linked to
    QUERY_RESULT blocks via ANSWER relationships."""
    blocks = []
    for i, (alias, text) in enumerate(answers.items()):
        qid, aid = f"q{i}", f"a{i}"
        blocks.append({"BlockType": "QUERY", "Id": qid,
                       "Query": {"Text": alias, "Alias": alias},
                       "Relationships": [{"Type": "ANSWER", "Ids": [aid]}]})
        blocks.append({"BlockType": "QUERY_RESULT", "Id": aid,
                       "Text": text, "Confidence": 99.0})
    return {"Blocks": blocks}


def _provider(expense=None, query=None):
    client = MagicMock()
    client.analyze_expense.return_value = expense or {"ExpenseDocuments": []}
    client.analyze_document.return_value = query or {"Blocks": []}
    return TextractProvider(client=client), client


# ---------------------------------------------------------------------------
# invoice
# ---------------------------------------------------------------------------
def test_invoice_mapping_from_expense():
    prov, client = _provider(EXPENSE_INVOICE, _query_response({"load_id": "L-100482"}))
    inv = prov.extract_invoice(b"<fake-jpeg-bytes>")

    assert inv.carrier_name == "Ironwood Freight Inc"
    assert inv.invoice_number == "IW-7781"
    assert inv.load_id == "L-100482"                 # filled by the QUERIES overlay
    assert inv.invoice_date == datetime(2026, 6, 11)
    assert inv.billed_total_cents == 250000          # $2,500.00 -> integer cents

    amounts = {li.description: li.amount_cents for li in inv.line_items}
    assert amounts == {"Linehaul": 190000, "Fuel Surcharge": 30000, "Detention": 16000}

    det = next(li for li in inv.line_items if li.description == "Detention")
    assert det.rate_cents == 8000        # UNIT_PRICE $80.00 -> rate_cents
    assert det.quantity == 2.0           # QUANTITY is a float, not money


def test_no_live_aws_and_bytes_payload():
    prov, client = _provider(EXPENSE_INVOICE, _query_response({"load_id": "L-1"}))
    prov.extract_invoice(b"rawbytes")

    client.analyze_expense.assert_called_once()
    assert client.analyze_expense.call_args.kwargs["Document"] == {"Bytes": b"rawbytes"}
    # the QUERIES request carries the configured freight queries
    qcfg = client.analyze_document.call_args.kwargs["QueriesConfig"]
    aliases = {q["Alias"] for q in qcfg["Queries"]}
    assert set(DEFAULT_FREIGHT_QUERIES) <= aliases


def test_money_fields_are_integer_cents():
    prov, _ = _provider(EXPENSE_INVOICE, _query_response({"load_id": "L-1"}))
    inv = prov.extract_invoice(b"x")
    assert isinstance(inv.billed_total_cents, int)
    for li in inv.line_items:
        assert isinstance(li.amount_cents, int)
        assert li.rate_cents is None or isinstance(li.rate_cents, int)


# ---------------------------------------------------------------------------
# rate confirmation
# ---------------------------------------------------------------------------
def test_rate_confirmation_uses_query_overlay():
    queries = _query_response({
        "load_id": "L-100485", "origin": "Phoenix, AZ",
        "destination": "El Paso, TX", "pickup_date": "2026-06-12"})
    prov, client = _provider(EXPENSE_RATECON, queries)
    rc = prov.extract_rate_confirmation(b"x")

    assert rc.broker_name == "Cardinal Logistics Brokerage"
    assert rc.carrier_name == "Ironwood Freight Inc"
    assert rc.load_id == "L-100485"
    assert rc.origin == "Phoenix, AZ"
    assert rc.destination == "El Paso, TX"
    assert rc.pickup_date == datetime(2026, 6, 12)
    assert rc.agreed_total_cents == 170000           # TOTAL -> agreed_total
    assert rc.line_total_cents == 170000             # $1,450 + $250
    client.analyze_document.assert_called_once()


# ---------------------------------------------------------------------------
# proof of delivery
# ---------------------------------------------------------------------------
def test_pod_mapping_and_time_on_site():
    queries = _query_response({
        "load_id": "L-100485",
        "arrival_time": "2026-06-13 06:00",
        "departure_time": "2026-06-13 10:15",
        "signed_by": "D. Salazar"})
    prov, _ = _provider({"ExpenseDocuments": []}, queries)
    pod = prov.extract_pod(b"x")

    assert pod.load_id == "L-100485"
    assert pod.signed_by == "D. Salazar"
    assert pod.delivered is True
    assert pod.time_on_site_hours == 4.25            # 06:00 -> 10:15


def test_delivered_is_coerced_to_bool():
    # a "No" answer must become False, not a truthy non-empty string
    prov, _ = _provider({"ExpenseDocuments": []},
                        _query_response({"load_id": "L-1", "delivered": "No"}))
    assert prov.extract_pod(b"x").delivered is False


# ---------------------------------------------------------------------------
# offline / optionality guarantees
# ---------------------------------------------------------------------------
def test_dict_passthrough_runs_offline_without_client():
    # a dict source is already-mapped fields -> no client touched, matches offline
    bundle = {"invoice_number": "INV-9", "load_id": "L-9", "carrier_name": "C",
              "billed_total": "$1,234.56",
              "line_items": [{"description": "Linehaul", "amount": "$1,234.56"}]}
    prov = TextractProvider()                        # no client, no boto3 needed
    inv = prov.extract_invoice(bundle)
    assert inv.billed_total_cents == 123456
    # identical to the offline provider's mapping
    assert inv == JsonFixtureProvider().extract_invoice(bundle)


def test_boto3_not_imported_when_client_injected():
    # exercising an injected client must not import boto3 (keeps it optional)
    prov, _ = _provider(EXPENSE_INVOICE, _query_response({"load_id": "L-1"}))
    prov.extract_invoice(b"x")
    assert "boto3" not in sys.modules


def test_missing_load_id_defaults_to_empty():
    # expense-only doc with no PO_NUMBER and no query answer -> "" (no KeyError)
    prov = TextractProvider(client=_provider(EXPENSE_RATECON)[1], queries={})
    prov._client.analyze_expense.return_value = EXPENSE_RATECON
    rc = prov.extract_rate_confirmation(b"x")
    assert rc.load_id == ""


# ---------------------------------------------------------------------------
# the mapped documents feed the unchanged matching engine
# ---------------------------------------------------------------------------
def test_mapped_documents_feed_match_engine():
    load = "L-100485"
    rc = _provider(EXPENSE_RATECON, _query_response({
        "load_id": load, "origin": "Phoenix, AZ", "destination": "El Paso, TX",
        "pickup_date": "2026-06-12"}))[0].extract_rate_confirmation(b"x")
    inv = _provider(EXPENSE_RATECON, _query_response({"load_id": load}))[0] \
        .extract_invoice(b"x")
    pod = _provider({"ExpenseDocuments": []}, _query_response({
        "load_id": load, "arrival_time": "2026-06-13 06:00",
        "departure_time": "2026-06-13 07:00", "signed_by": "D. Salazar"}))[0] \
        .extract_pod(b"x")

    result = MatchEngine().match(rc, inv, pod)
    assert isinstance(result, MatchResult)
    assert result.load_id == load
    # ids agree across all three docs -> no load-id mismatch finding
    assert not any(f.type.value == "load_id_mismatch" for f in result.findings)
