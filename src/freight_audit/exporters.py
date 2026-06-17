"""
Output adapters (improves "not ready" item #5).

Before: CSV + JSON only. A real client wants results pushed into the system they
already run on. This module adds real, named exporters behind a small registry so
adding a client's target = adding one function, and the CLI/UI can offer any of
them.

Included:
  - csv_export        : flat CSV (already had this; kept for parity)
  - quickbooks_iif    : QuickBooks-importable .IIF bills (the most-requested SMB target)
  - tms_json          : a clean, generic TMS payload (the shape most TMS REST APIs want)
  - exceptions_csv    : only the loads needing review (what an AP clerk works from)

These take the engine's MatchResult list and emit text. Wiring a live API push
(QuickBooks Online API, a specific TMS endpoint) is then just POSTing this payload.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Callable

from .models import MatchResult, cents_to_str


# ---------------------------------------------------------------------------
def csv_export(results: list[MatchResult]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["load_id", "severity", "auto_approvable", "overpayment", "recoverable",
                "net_impact", "n_findings", "findings"])
    for r in results:
        over = sum(f.money_impact_cents for f in r.findings if f.money_impact_cents > 0)
        rec = -sum(f.money_impact_cents for f in r.findings if f.money_impact_cents < 0)
        w.writerow([
            r.load_id, r.severity.value, r.auto_approvable,
            cents_to_str(over), cents_to_str(rec), cents_to_str(r.net_money_impact_cents),
            len(r.findings), " | ".join(f.message for f in r.findings),
        ])
    return buf.getvalue()


def exceptions_csv(results: list[MatchResult]) -> str:
    """Only loads that need human review -- the AP clerk's worklist."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["load_id", "severity", "money_at_stake", "what_to_check"])
    for r in results:
        if r.auto_approvable:
            continue
        at_stake = sum(abs(f.money_impact_cents) for f in r.findings)
        w.writerow([
            r.load_id, r.severity.value, cents_to_str(at_stake),
            " | ".join(f"{f.type.value}: {f.message}" for f in r.findings
                       if f.severity.value in ("warn", "block")),
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
def quickbooks_iif(results: list[MatchResult], vendor_default: str = "Carrier") -> str:
    """Emit a QuickBooks-importable IIF for the APPROVABLE invoices as Bills.

    IIF is the classic flat QuickBooks import format (tab-delimited, header rows
    !TRNS/!SPL/!ENDTRNS). We only export auto-approvable loads as payable bills;
    flagged loads are held for review (they don't belong in an auto-import).
    """
    lines = []
    lines.append("\t".join(["!TRNS", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT", "MEMO"]))
    lines.append("\t".join(["!SPL", "TRNSTYPE", "DATE", "ACCNT", "NAME", "AMOUNT", "MEMO"]))
    lines.append("!ENDTRNS")
    today = datetime.now().strftime("%m/%d/%Y")
    for r in results:
        if not r.auto_approvable or r.invoice is None:
            continue
        amt = r.invoice.billed_total_cents / 100.0
        vendor = (r.invoice.carrier_name or vendor_default).replace("\t", " ")
        memo = f"Load {r.load_id} inv {r.invoice.invoice_number}"
        # Bill: TRNS is the payable (negative to A/P), SPL the expense (positive)
        lines.append("\t".join(["TRNS", "BILL", today, "Accounts Payable", vendor,
                                f"-{amt:.2f}", memo]))
        lines.append("\t".join(["SPL", "BILL", today, "Freight Expense", vendor,
                                f"{amt:.2f}", memo]))
        lines.append("ENDTRNS")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
def tms_json(results: list[MatchResult]) -> str:
    """Generic TMS payload -- the shape most TMS REST endpoints accept on a
    write-back. Map field names to a specific TMS when you integrate one."""
    payload = []
    for r in results:
        payload.append({
            "load_id": r.load_id,
            "audit_status": "approved" if r.auto_approvable else "needs_review",
            "severity": r.severity.value,
            "invoice_number": r.invoice.invoice_number if r.invoice else None,
            "carrier": r.invoice.carrier_name if r.invoice else None,
            "billed_total": (r.invoice.billed_total_cents / 100.0) if r.invoice else None,
            "agreed_total": (r.rate_con.agreed_total_cents / 100.0) if r.rate_con else None,
            "net_adjustment": r.net_money_impact_cents / 100.0,
            "exceptions": [
                {"type": f.type.value, "severity": f.severity.value,
                 "message": f.message, "amount": f.money_impact_cents / 100.0}
                for f in r.findings if f.severity.value in ("warn", "block")
            ],
        })
    return json.dumps({"loads": payload,
                       "generated_at": datetime.now().isoformat(timespec="seconds")},
                      indent=2)


# ---------------------------------------------------------------------------
def roi_report(results: list[MatchResult]) -> str:
    """Human-readable savings/ROI report for a batch (the 'what did we save you'
    view). Aggregation logic lives in reporting.py so it's shared with the durable
    history report (store.report())."""
    from .reporting import report_from_results, format_report
    return format_report(report_from_results(results))


# ---------------------------------------------------------------------------
EXPORTERS: dict[str, Callable[[list[MatchResult]], str]] = {
    "csv": csv_export,
    "exceptions_csv": exceptions_csv,
    "quickbooks_iif": quickbooks_iif,
    "tms_json": tms_json,
    "roi_report": roi_report,
}

# file extension per exporter (for the CLI to name files sensibly)
EXPORTER_EXT = {
    "csv": ".csv", "exceptions_csv": ".csv",
    "quickbooks_iif": ".iif", "tms_json": ".json", "roi_report": ".txt",
}


def export(results: list[MatchResult], fmt: str) -> str:
    if fmt not in EXPORTERS:
        raise ValueError(f"unknown export format '{fmt}'. options: {list(EXPORTERS)}")
    return EXPORTERS[fmt](results)
