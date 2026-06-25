"""
FastAPI server implementing the front-end's frozen contract (BACKEND.md §0).

It wraps the EXISTING engine and services — nothing about the rules changes:
  - audit:        freight_audit.process_load() + MatchResult.to_dict()
  - persistence:  freight_audit.storage.get_store() (SQLite, or Postgres via DATABASE_URL)
  - exporters:    freight_audit.export() (QuickBooks IIF, TMS JSON, ...)
  - payments:     the stripe SDK (Stripe Checkout) when STRIPE_KEY is set

Set `API_BASE_URL` in the front-end's deploy.config.js to this server's URL and the
console fetches live results instead of running the in-browser port.

Run:
    pip install -e ".[ocr,api,dev]"
    FREIGHT_AUDIT_DB=./freight_audit.db uvicorn api.app:app --reload

Contract (identical to the front-end):
    GET  /findings            -> MatchResult[]  (undecided audit queue)
    POST /loads               -> MatchResult    (audit one bundle, persisted)
    POST /decisions           {id, kind, at}    (approve/flag -> store + audit log)
    POST /export/quickbooks   -> IIF            (real exporter; live QBO push needs OAuth)
    POST /ocr/confirm         {fields}          (confirmed OCR -> audit -> persist)
    POST /checkout            {plan}            (Stripe Checkout session)
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from freight_audit import process_load, ClientProfile, export
from freight_audit.storage import get_store
from freight_audit.secret_provider import get_secret

app = FastAPI(title="freight-audit API")

# allow the static front-end origin to call this API
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

_TENANT = "default"            # the contract is single-tenant; multi-tenant lives in freight_audit.api


def _loads_dir() -> Path:
    d = get_secret("LOADS_DIR")
    if d:
        return Path(d)
    return Path(__file__).resolve().parent.parent / "samples" / "loads"


def _profile():
    path = get_secret("CLIENT_PROFILE_PATH", "")
    return ClientProfile.load(path) if path and os.path.exists(path) else None


def _audit_bundle(bundle: dict):
    return process_load(bundle.get("rate_confirmation"), bundle.get("invoice"),
                        bundle.get("pod"), profile=_profile())


def _queue():
    """Audit every bundle in the loads dir -> MatchResults."""
    results = []
    for path in sorted(glob.glob(str(_loads_dir() / "*.json"))):
        with open(path) as fh:
            results.append(_audit_bundle(json.load(fh)))
    return results


# ---- the contract -------------------------------------------------------------
@app.get("/findings")
def findings():
    """The audit queue: every load not yet decided (decisions persist in the store,
    so a decided load drops off the queue in any browser/session)."""
    store = get_store()
    return [r.to_dict() for r in _queue()
            if not store.decisions_for(r.load_id, tenant_id=_TENANT)]


@app.post("/loads")
def audit_load(bundle: dict = Body(...)):
    result = _audit_bundle(bundle)
    get_store().save_result(result, client="web", actor="web", tenant_id=_TENANT)
    return result.to_dict()


_KIND = {"approve": "approved", "approved": "approved", "accept": "approved",
         "flag": "disputed", "dispute": "disputed", "disputed": "disputed",
         "reject": "disputed"}


@app.post("/decisions")
def decide(payload: dict = Body(...)):
    """Append-only decision: writes the decision + an audit_log row to the store."""
    load_id = payload.get("id") or payload.get("load_id")
    decision = _KIND.get(str(payload.get("kind", "")).lower())
    if not load_id or decision is None:
        raise HTTPException(status_code=400,
                            detail="decision needs {id, kind in approve|flag}")
    get_store().record_decision(
        load_id, decision, actor=payload.get("actor", "web"),
        note=f"kind={payload.get('kind')} at={payload.get('at')}", tenant_id=_TENANT)
    return {"ok": True, "load_id": load_id, "decision": decision}


@app.post("/export/quickbooks")
def export_quickbooks():
    """Run the real QuickBooks IIF exporter over the auto-approvable loads and return
    the importable file. Live push to QuickBooks Online requires an OAuth access token
    (QBO_ACCESS_TOKEN); when present the IIF-equivalent bills are POSTed to QBO_API_URL."""
    iif = export(_queue(), "quickbooks_iif")
    token = get_secret("QBO_ACCESS_TOKEN")
    qbo_url = get_secret("QBO_API_URL")
    pushed = False
    if token and qbo_url:
        import urllib.request
        req = urllib.request.Request(
            qbo_url, data=iif.encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            pushed = (getattr(resp, "status", None) or resp.getcode()) < 300
    return PlainTextResponse(iif, headers={
        "Content-Disposition": "attachment; filename=bills.iif",
        "X-QBO-Pushed": str(pushed)})


@app.post("/ocr/confirm")
def ocr_confirm(fields: dict = Body(...)):
    """Take human-confirmed OCR fields, build a load bundle, audit it, and persist."""
    if any(k in fields for k in ("rate_confirmation", "invoice", "pod")):
        bundle = fields
    else:
        bundle = {"invoice": fields}            # a flat confirmed-invoice field set
    result = _audit_bundle(bundle)
    get_store().save_result(result, client="web", actor="ocr", tenant_id=_TENANT)
    return result.to_dict()


@app.post("/checkout")
def checkout(payload: dict = Body(...)):
    """Create a Stripe Checkout session for a plan. With no STRIPE_KEY this returns a
    mock session (the documented offline stub); with a key it creates a real one."""
    plan = payload.get("plan", "pilot")
    key = get_secret("STRIPE_KEY") or get_secret("STRIPE_API_KEY")
    if not key:
        return {"mock": True, "plan": plan,
                "url": "https://checkout.stripe.com/mock-session",
                "note": "set STRIPE_KEY to create a real Checkout session"}
    import stripe
    session = stripe.checkout.Session.create(
        api_key=key,
        mode="payment" if plan == "one_time" else "subscription",
        line_items=[{"price": get_secret("STRIPE_PRICE_ID", "price_pilot"), "quantity": 1}],
        success_url=get_secret("CHECKOUT_SUCCESS_URL", "https://app.example/success"),
        cancel_url=get_secret("CHECKOUT_CANCEL_URL", "https://app.example/cancel"),
        metadata={"plan": plan})
    sid = session.get("id") if isinstance(session, dict) else session.id
    url = session.get("url") if isinstance(session, dict) else session.url
    return {"mock": False, "plan": plan, "id": sid, "url": url}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
