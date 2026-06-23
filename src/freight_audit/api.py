"""
REST API (PROMPTS #3).

A small FastAPI app over the engine:
  - GET  /healthz        -> liveness (open)
  - POST /audit          -> audit a load-bundle JSON, return findings + severity
  - POST /audit/upload   -> audit document images via the OCR pipeline
  - GET  /loads/{id}     -> retrieve a previously persisted load

Write/read endpoints are gated by the existing API-key store (security.KeyStore,
X-API-Key header); each processed load is persisted via store.Store. FastAPI and
the OCR pipeline are imported lazily/optionally so the core install is unaffected.

Run:  uvicorn freight_audit.api:app
Configure:  FREIGHT_AUDIT_KEYS (key store path), FREIGHT_AUDIT_DB (sqlite path).
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile

from .security import KeyStore
from .store import Store

KEYS_ENV = "FREIGHT_AUDIT_KEYS"
DB_ENV = "FREIGHT_AUDIT_DB"

logger = logging.getLogger("freight_audit.api")
app = FastAPI(title="freight-audit", version="0.1.0")


def _usage_amounts(result) -> tuple[int, int]:
    """(overpayment, recoverable) cents surfaced by a result, for metering."""
    over = sum(f.money_impact_cents for f in result.findings if f.money_impact_cents > 0)
    rec = -sum(f.money_impact_cents for f in result.findings if f.money_impact_cents < 0)
    return over, rec


def _record(result, tenant: str) -> None:
    store = _store()
    store.save_result(result, client=tenant, actor="api", tenant_id=tenant)
    over, rec = _usage_amounts(result)
    store.record_usage(tenant, result.load_id, over, rec)
    logger.info("audited tenant=%s load=%s severity=%s net=%s",
                tenant, result.load_id, result.severity.value,
                result.net_money_impact_cents)


def _keystore() -> KeyStore:
    return KeyStore(os.getenv(KEYS_ENV, "api_keys.json"))


def _store() -> Store:
    return Store(os.getenv(DB_ENV, "freight_audit.db"))


def require_tenant(x_api_key: Optional[str] = Header(default=None)) -> str:
    """Resolve the tenant the API key belongs to; 401 if missing/invalid. All
    reads/writes are then scoped to this tenant, so keys can't cross tenants."""
    tenant = _keystore().tenant_for(x_api_key) if x_api_key else None
    if tenant is None:
        raise HTTPException(status_code=401, detail="missing or invalid API key")
    return tenant


def _payload(result) -> dict:
    return {
        "load_id": result.load_id,
        "severity": result.severity.value,
        "auto_approvable": result.auto_approvable,
        "net_impact_cents": result.net_money_impact_cents,
        "findings": [
            {"type": f.type.value, "severity": f.severity.value,
             "message": f.message, "money_impact_cents": f.money_impact_cents}
            for f in result.findings
        ],
    }


@app.get("/healthz")
def healthz() -> dict:
    try:
        _store().conn.execute("SELECT 1").fetchone()
        db = "ok"
    except Exception:  # pragma: no cover - only on a broken db
        db = "error"
    return {"status": "ok" if db == "ok" else "degraded", "db": db, "version": app.version}


@app.post("/audit")
def audit(bundle: dict, tenant: str = Depends(require_tenant)) -> dict:
    from . import process_load
    result = process_load(bundle.get("rate_confirmation"),
                          bundle.get("invoice"), bundle.get("pod"))
    _record(result, tenant)
    return _payload(result)


@app.post("/audit/upload")
async def audit_upload(rate_con: Optional[UploadFile] = File(default=None),
                       invoice: Optional[UploadFile] = File(default=None),
                       pod: Optional[UploadFile] = File(default=None),
                       tenant: str = Depends(require_tenant)) -> dict:
    from .pipeline import audit_documents
    tmp = tempfile.mkdtemp()
    paths: dict[str, str] = {}
    for name, upload in (("rate_con", rate_con), ("invoice", invoice), ("pod", pod)):
        if upload is not None:
            p = os.path.join(tmp, os.path.basename(upload.filename or f"{name}.png"))
            with open(p, "wb") as fh:
                fh.write(await upload.read())
            paths[name] = p
    if not paths:
        raise HTTPException(status_code=400, detail="provide at least one document image")
    result = audit_documents(paths.get("rate_con"), paths.get("invoice"), paths.get("pod"))
    _record(result, tenant)
    return _payload(result)


@app.get("/loads/{load_id}")
def get_load(load_id: str, tenant: str = Depends(require_tenant)) -> dict:
    row = _store().get_load(load_id, tenant_id=tenant)
    if row is None:
        raise HTTPException(status_code=404, detail="load not found")
    return row


@app.get("/usage")
def usage(tenant: str = Depends(require_tenant)) -> dict:
    """The tenant's billing record (what to charge) -- see billing.py."""
    from .billing import bill_tenant
    return bill_tenant(_store(), tenant)
