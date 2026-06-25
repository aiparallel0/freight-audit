"""
Entry point for the merged freight-audit server (BACKEND.md run command:
`uvicorn api.app:app`).

There is now ONE app. The front-end's frozen contract routes (/findings, /loads,
/decisions, /ocr/confirm, /export/quickbooks, /checkout) are served by the unified
`freight_audit.api` app and backed by the real production services — the
multi-tenant store (a persisted, tenant-scoped queue), the exporters, billing/Stripe,
metering, and the SaaS routes (signup, keys, usage, metrics, billing webhook) — plus
the marketing/demo pages and the finished console at /console.

Run:
    FREIGHT_AUDIT_DB=./freight_audit.db uvicorn api.app:app --reload
"""
from freight_audit.api import app  # noqa: F401  (single source of truth)
