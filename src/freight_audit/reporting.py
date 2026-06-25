"""
Savings / ROI reporting (completes Phase C stage 12).

The engine already computes per-load money impact; the CLI and review console show
per-batch totals. This module turns that into a durable, aggregate report -- the
"what did this save the client" view that proves ROI and drives renewal -- and it
works from EITHER a live batch of MatchResults OR the persisted history in store.py,
so the same numbers come out whether you just ran an audit or are reporting on a
quarter of processed loads.

Money stays integer cents throughout; formatting to dollars happens only at the edge.
"""
from __future__ import annotations

import json

from .models import cents_to_str


def aggregate(records) -> dict:
    """Aggregate an iterable of result-like records into ROI metrics.

    Each record is a dict: {severity, auto_approvable, findings}, where findings is
    a list of {type, severity, money_impact_cents}. This is the common shape both a
    MatchResult and a persisted store row reduce to."""
    s = {
        "loads": 0, "auto_approvable": 0, "needs_review": 0, "blocking": 0,
        "overpayment_caught_cents": 0, "recoverable_revenue_cents": 0,
        "net_impact_cents": 0, "dollars_touched_cents": 0,
        "by_finding_type": {}, "by_severity": {},
    }
    for rec in records:
        s["loads"] += 1
        if rec.get("auto_approvable"):
            s["auto_approvable"] += 1
        else:
            s["needs_review"] += 1
        sev = rec.get("severity") or "ok"
        s["by_severity"][sev] = s["by_severity"].get(sev, 0) + 1
        if sev == "block":
            s["blocking"] += 1
        for f in rec.get("findings") or []:
            amt = int(f.get("money_impact_cents") or 0)
            s["net_impact_cents"] += amt
            if amt > 0:
                s["overpayment_caught_cents"] += amt
            elif amt < 0:
                s["recoverable_revenue_cents"] += -amt
            ftype = f.get("type") or "other"
            if ftype == "ok":            # the synthetic clean marker isn't an exception
                continue
            bucket = s["by_finding_type"].setdefault(ftype, {"count": 0, "money_cents": 0})
            bucket["count"] += 1
            bucket["money_cents"] += amt
    s["dollars_touched_cents"] = (s["overpayment_caught_cents"]
                                  + s["recoverable_revenue_cents"])
    return s


def report_from_results(results) -> dict:
    """ROI report for a live batch of MatchResults."""
    def _rec(r):
        return {
            "severity": r.severity.value,
            "auto_approvable": r.auto_approvable,
            "findings": [{"type": f.type.value, "severity": f.severity.value,
                          "money_impact_cents": f.money_impact_cents}
                         for f in r.findings],
        }
    return aggregate(_rec(r) for r in results)


def report_from_store(store, tenant_id: str = "default") -> dict:
    """ROI report from persisted history for one tenant (duck-typed: all_loads())."""
    def _rec(row):
        return {
            "severity": row.get("severity"),
            "auto_approvable": bool(row.get("auto_approvable")),
            "findings": json.loads(row.get("findings_json") or "[]"),
        }
    return aggregate(_rec(row) for row in store.all_loads(tenant_id=tenant_id))


def format_report(summary: dict, title: str = "savings / ROI report") -> str:
    """Human-readable text version of an aggregate report."""
    lines = [f"FREIGHT-AUDIT  --  {title}",
             f"  loads processed       : {summary['loads']}",
             f"  auto-approvable       : {summary['auto_approvable']}",
             f"  needs review          : {summary['needs_review']}  "
             f"(blocking: {summary['blocking']})",
             f"  overpayment caught    : {cents_to_str(summary['overpayment_caught_cents'])}",
             f"  recoverable revenue   : {cents_to_str(summary['recoverable_revenue_cents'])}",
             f"  TOTAL $ touched       : {cents_to_str(summary['dollars_touched_cents'])}",
             f"  net adjustment        : {cents_to_str(summary['net_impact_cents'])}"]
    if summary["by_finding_type"]:
        lines.append("  by finding type:")
        for ftype, v in sorted(summary["by_finding_type"].items(),
                               key=lambda kv: -abs(kv[1]["money_cents"])):
            lines.append(f"     {ftype:<26} {v['count']:>3}x  {cents_to_str(v['money_cents'])}")
    return "\n".join(lines)
