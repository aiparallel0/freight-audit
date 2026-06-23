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
import time
from typing import Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from .security import KeyStore, DuplicateAccount, InvalidEmail
from .storage import get_store
from .ratelimit import InMemoryRateLimiter
from .secret_provider import get_secret
from .metrics import METRICS

KEYS_ENV = "FREIGHT_AUDIT_KEYS"

logger = logging.getLogger("freight_audit.api")
app = FastAPI(title="freight-audit", version="0.1.0")

_limiter = None


def _get_limiter():
    global _limiter
    if _limiter is None:
        _limiter = InMemoryRateLimiter()
    return _limiter


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    key = request.headers.get("X-API-Key")
    tenant = (_keystore().tenant_for(key) if key else None) or "anonymous"
    METRICS.record(tenant, request.url.path, elapsed_ms, response.status_code)
    return response


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


def _store():
    return get_store()


def _authenticate(x_api_key: Optional[str]) -> dict:
    """Resolve {tenant, role, key} from an API key; 401 if missing/invalid/expired,
    429 if the per-key rate limit is exceeded."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="missing API key")
    ks = _keystore()
    tenant = ks.tenant_for(x_api_key)          # None if invalid / revoked / expired
    if tenant is None:
        raise HTTPException(status_code=401, detail="invalid or expired API key")
    if not _get_limiter().allow(x_api_key):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    return {"tenant": tenant, "role": ks.role_for(x_api_key), "key": x_api_key}


def require_tenant(x_api_key: Optional[str] = Header(default=None)) -> str:
    """Any valid key; returns its tenant. Reads/writes are scoped to this tenant."""
    return _authenticate(x_api_key)["tenant"]


def _role_dep(*allowed: str):
    """Dependency factory: caller must hold one of `allowed` (admin is always allowed).
    Returns the caller's tenant."""
    def dep(x_api_key: Optional[str] = Header(default=None)) -> str:
        ctx = _authenticate(x_api_key)
        if ctx["role"] != "admin" and ctx["role"] not in allowed:
            raise HTTPException(status_code=403,
                                detail=f"role '{ctx['role']}' not permitted")
        return ctx["tenant"]
    return dep


auditor = _role_dep("api")        # api or admin -> may run audits / read loads
reviewer = _role_dep("reviewer")  # reviewer or admin -> may decide reviews
admin_only = _role_dep()          # admin only -> manage keys


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


@app.get("/metrics")
def metrics_endpoint():
    """Prometheus metrics: per-tenant request count, error count, avg latency."""
    return PlainTextResponse(METRICS.render_prometheus())


@app.post("/audit")
def audit(bundle: dict, tenant: str = Depends(auditor)) -> dict:
    from . import process_load
    result = process_load(bundle.get("rate_confirmation"),
                          bundle.get("invoice"), bundle.get("pod"))
    _record(result, tenant)
    return _payload(result)


@app.post("/audit/upload")
async def audit_upload(rate_con: Optional[UploadFile] = File(default=None),
                       invoice: Optional[UploadFile] = File(default=None),
                       pod: Optional[UploadFile] = File(default=None),
                       tenant: str = Depends(auditor)) -> dict:
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
def get_load(load_id: str, tenant: str = Depends(auditor)) -> dict:
    row = _store().get_load(load_id, tenant_id=tenant)
    if row is None:
        raise HTTPException(status_code=404, detail="load not found")
    return row


@app.get("/usage")
def usage(tenant: str = Depends(auditor)) -> dict:
    """The tenant's billing record (what to charge) -- see billing.py."""
    from .billing import bill_tenant
    return bill_tenant(_store(), tenant)


@app.post("/signup")
def signup(body: dict) -> dict:
    """Create an account: a new tenant + its first (admin) API key. Open endpoint."""
    email = (body or {}).get("email", "")
    try:
        account = _keystore().create_account(email)
    except InvalidEmail:
        raise HTTPException(status_code=400, detail="invalid email address")
    except DuplicateAccount:
        raise HTTPException(status_code=409, detail="an account with that email already exists")
    logger.info("signup tenant=%s", account["tenant"])
    return account


@app.post("/keys")
def create_key(body: dict, x_api_key: Optional[str] = Header(default=None)) -> dict:
    """Admin issues an additional key for their own tenant with a chosen role."""
    tenant = admin_only(x_api_key)
    role = (body or {}).get("role", "api")
    ttl = (body or {}).get("ttl_days")
    if role not in ("admin", "reviewer", "api"):
        raise HTTPException(status_code=400, detail="invalid role")
    key = _keystore().issue(label=f"{tenant}:{role}", tenant_id=tenant, role=role,
                            ttl_days=ttl)
    return {"api_key": key, "tenant": tenant, "role": role}


@app.post("/keys/rotate")
def rotate_key(x_api_key: Optional[str] = Header(default=None)) -> dict:
    """Rotate the calling key: issue a replacement (same tenant/role) and revoke
    the old one."""
    require_tenant(x_api_key)                 # 401/429 if the key isn't valid
    new_key = _keystore().rotate(x_api_key)
    if new_key is None:
        raise HTTPException(status_code=401, detail="cannot rotate this key")
    return {"api_key": new_key}


@app.post("/billing/webhook")
async def billing_webhook(request: Request) -> dict:
    """Receive Stripe payment events. Verifies the signature with the SDK using
    get_secret('STRIPE_WEBHOOK_SECRET'); 400 on a bad signature."""
    import stripe
    payload = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, get_secret("STRIPE_WEBHOOK_SECRET"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid signature")
    etype = event["type"] if isinstance(event, dict) else getattr(event, "type", None)
    logger.info("stripe webhook event=%s", etype)
    if etype in ("payment_intent.succeeded", "payment_intent.payment_failed"):
        obj = (event.get("data", {}) or {}).get("object", {}) if isinstance(event, dict) else {}
        logger.info("payment %s tenant=%s amount=%s", etype,
                    (obj.get("metadata") or {}).get("tenant"), obj.get("amount"))
    return {"received": True, "type": etype}


# Register the browser routes (landing / signup / demo / review console) on `app`.
from . import web  # noqa: E402,F401
