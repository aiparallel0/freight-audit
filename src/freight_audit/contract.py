"""
The front-end's frozen contract (BACKEND.md §0), merged into the main FastAPI app.

These routes are the same interface the uploaded console calls -- GET /findings,
POST /loads, POST /decisions, POST /ocr/confirm, POST /export/quickbooks,
POST /checkout -- but they run on the SAME production services as the rest of the
app: the multi-tenant store (a persisted, tenant-scoped audit queue), the exporters,
billing/Stripe, and usage metering. Nothing is a stub.

Auth follows the deploy.config "one variable, offline stub" model: open (tenant
'default') by default so the console works out of the box; tenant-scoped when a valid
X-API-Key is supplied; required when AUTH_REQUIRED is set. The finished console
(review_ui/) is served same-origin at /console.
"""
from __future__ import annotations

import glob
import json
import os
from typing import Optional

from fastapi import Body, Depends, Header, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import process_load, ClientProfile, export
from .api import app, _store, _keystore
from .secret_provider import get_secret

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))


def _loads_dir() -> str:
    return get_secret("LOADS_DIR") or os.path.join(_REPO, "samples", "loads")


def _profile():
    path = get_secret("CLIENT_PROFILE_PATH", "")
    return ClientProfile.load(path) if path and os.path.exists(path) else None


def _audit_bundle(bundle: dict):
    return process_load(bundle.get("rate_confirmation"), bundle.get("invoice"),
                        bundle.get("pod"), profile=_profile())


def contract_tenant(x_api_key: Optional[str] = Header(default=None)) -> str:
    """A valid key -> its tenant; no key -> 'default' (offline/demo) unless
    AUTH_REQUIRED is set; an invalid/expired key -> 401."""
    if x_api_key:
        tenant = _keystore().tenant_for(x_api_key)
        if tenant is None:
            raise HTTPException(status_code=401, detail="invalid or expired API key")
        return tenant
    if get_secret("AUTH_REQUIRED"):
        raise HTTPException(status_code=401, detail="API key required")
    return "default"


def _queue_bundles(store, tenant: str) -> list[dict]:
    """The tenant's persisted bundles, or the bundled samples as the empty-state demo."""
    rows = store.all_bundles(tenant)
    if rows:
        return [json.loads(r["bundle_json"]) for r in rows]
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(_loads_dir(), "*.json")))]


@app.get("/findings")
def findings(tenant: str = Depends(contract_tenant)):
    store = _store()
    out = []
    for bundle in _queue_bundles(store, tenant):
        result = _audit_bundle(bundle)
        if store.decisions_for(result.load_id, tenant_id=tenant):   # decided -> off queue
            continue
        out.append(result.to_dict())
    return out


@app.post("/loads")
def audit_one(bundle: dict = Body(...), tenant: str = Depends(contract_tenant)):
    result = _audit_bundle(bundle)
    store = _store()
    store.save_bundle(tenant, result.load_id, bundle)
    store.save_result(result, client=tenant, actor="web", tenant_id=tenant)
    over = sum(f.money_impact_cents for f in result.findings if f.money_impact_cents > 0)
    rec = -sum(f.money_impact_cents for f in result.findings if f.money_impact_cents < 0)
    store.record_usage(tenant, result.load_id, over, rec)
    return result.to_dict()


_KIND = {"approve": "approved", "approved": "approved", "accept": "approved",
         "flag": "disputed", "dispute": "disputed", "disputed": "disputed",
         "reject": "disputed"}


@app.post("/decisions")
def decide(payload: dict = Body(...), tenant: str = Depends(contract_tenant)):
    load_id = payload.get("id") or payload.get("load_id")
    decision = _KIND.get(str(payload.get("kind", "")).lower())
    if not load_id or decision is None:
        raise HTTPException(status_code=400, detail="decision needs {id, kind in approve|flag}")
    _store().record_decision(load_id, decision, actor=payload.get("actor", "web"),
                             note=f"kind={payload.get('kind')} at={payload.get('at')}",
                             tenant_id=tenant)
    return {"ok": True, "load_id": load_id, "decision": decision}


@app.post("/export/quickbooks")
def export_quickbooks(tenant: str = Depends(contract_tenant)):
    store = _store()
    results = [_audit_bundle(b) for b in _queue_bundles(store, tenant)]
    iif = export(results, "quickbooks_iif")
    token, url = get_secret("QBO_ACCESS_TOKEN"), get_secret("QBO_API_URL")
    pushed = False
    if token and url:
        import urllib.request
        req = urllib.request.Request(
            url, data=iif.encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            pushed = (getattr(resp, "status", None) or resp.getcode()) < 300
    return PlainTextResponse(iif, headers={
        "Content-Disposition": "attachment; filename=bills.iif", "X-QBO-Pushed": str(pushed)})


@app.post("/ocr/confirm")
def ocr_confirm(fields: dict = Body(...), tenant: str = Depends(contract_tenant)):
    bundle = (fields if any(k in fields for k in ("rate_confirmation", "invoice", "pod"))
              else {"invoice": fields})
    result = _audit_bundle(bundle)
    store = _store()
    store.save_bundle(tenant, result.load_id, bundle)
    store.save_result(result, client=tenant, actor="ocr", tenant_id=tenant)
    return result.to_dict()


@app.post("/checkout")
def checkout(payload: dict = Body(...)):
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
    return {"mock": False, "plan": plan,
            "id": session.get("id") if isinstance(session, dict) else session.id,
            "url": session.get("url") if isinstance(session, dict) else session.url}


# serve the finished console (uploaded review_ui/) from the same origin as the API
_RUI = os.path.join(_REPO, "review_ui")
if os.path.isdir(_RUI):
    app.mount("/console", StaticFiles(directory=_RUI, html=True), name="console")
