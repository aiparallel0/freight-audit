"""
FastAPI scaffold for freight-audit (Phase 1 of BACKEND.md).

This wraps the EXISTING engine (`freight_audit.process_load`) in the exact HTTP
contract the front-end (review_ui/console + deploy.config.js) already calls.
Set `API_BASE_URL` in the front-end's deploy.config.js to this server's URL and
the UI fetches live results instead of running the in-browser port.

Run:
    pip install -e ".[ocr,dev]" fastapi uvicorn
    uvicorn api.app:app --reload

Contract (keep identical to the front-end):
    GET  /findings            -> MatchResult[]  (the audit queue)
    POST /loads               -> MatchResult    (audit one bundle)
    POST /decisions           {id, kind, at}    (append-only)
    POST /export/quickbooks   -> IIF            (TODO: real QBO OAuth)
    POST /ocr/confirm         {fields}          (TODO: persist + re-audit)
    POST /checkout            {plan}            (TODO: Stripe)
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware

from freight_audit import process_load, ClientProfile
from freight_audit.models import cents_to_str

app = FastAPI(title="freight-audit API")

# allow the static front-end origin to call this API
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "loads"
PROFILE_PATH = os.environ.get("CLIENT_PROFILE_PATH", "")


# ---- serialization: MatchResult -> the front-end's findings.json shape --------
def _doc(d):
    if d is None:
        return None
    out = {}
    for k in ("load_id", "broker_name", "carrier_name", "origin", "destination",
              "invoice_number", "invoice_date", "delivered", "arrival_time",
              "departure_time", "signed_by", "free_time_hours"):
        if hasattr(d, k):
            out[k] = getattr(d, k)
    for tot in ("agreed_total_cents", "billed_total_cents"):
        if hasattr(d, tot):
            out[tot] = getattr(d, tot)
    if hasattr(d, "line_items"):
        out["line_items"] = [
            {"description": li.description, "amount_cents": li.amount_cents,
             "category": li.category, "rate_cents": li.rate_cents}
            for li in d.line_items
        ]
    if hasattr(d, "approved_accessorials"):
        out["approved_accessorials"] = d.approved_accessorials
    return out


def to_dict(result) -> dict:
    return {
        "load_id": result.load_id,
        "severity": result.severity.value,
        "net_money_impact_cents": result.net_money_impact_cents,
        "auto_approvable": result.auto_approvable,
        "findings": [
            {"type": f.type.value, "severity": f.severity.value,
             "message": f.message, "money_impact_cents": f.money_impact_cents}
            for f in result.findings
        ],
        "rate_con": _doc(result.rate_con),
        "invoice": _doc(result.invoice),
        "pod": _doc(result.pod),
    }


def _profile():
    return ClientProfile.load(PROFILE_PATH) if PROFILE_PATH else None


def _audit_bundle(bundle: dict):
    return process_load(
        bundle.get("rate_confirmation"), bundle.get("invoice"),
        bundle.get("pod"), profile=_profile(),
    )


# ---- endpoints ----------------------------------------------------------------
@app.get("/findings")
def findings():
    out = []
    for path in sorted(glob.glob(str(SAMPLES / "*.json"))):
        with open(path) as fh:
            out.append(to_dict(_audit_bundle(json.load(fh))))
    return out


@app.post("/loads")
def audit_load(bundle: dict = Body(...)):
    return to_dict(_audit_bundle(bundle))


# Phase 2: wire freight_audit.store (SQLite/Postgres) instead of this stub.
_DECISIONS: list[dict] = []


@app.post("/decisions")
def decide(payload: dict = Body(...)):
    _DECISIONS.append(payload)          # TODO: store.append_decision(...) + audit_log
    return {"ok": True, "count": len(_DECISIONS)}


@app.post("/export/quickbooks")
def export_qbo():
    # TODO: run exporters IIF + push via QuickBooks OAuth (Phase 4)
    return {"ok": True, "note": "wire freight_audit.exporters + QBO OAuth"}


@app.post("/ocr/confirm")
def ocr_confirm(fields: dict = Body(...)):
    # TODO: persist corrected fields, build a bundle, re-audit (Phase 5)
    return {"ok": True}


@app.post("/checkout")
def checkout(payload: dict = Body(...)):
    # TODO: create a Stripe Checkout session, handle webhook (Phase 3)
    return {"ok": True, "plan": payload.get("plan")}
