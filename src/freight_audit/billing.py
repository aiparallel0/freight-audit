"""
Usage metering + billing records (the monetization layer -- scaffold, like security.py).

This turns recorded usage into the BILLING RECORD (what to charge a tenant); it does
NOT move money. Actual payment processing is left as an env-gated seam (`charge`)
that POSTs to a clearly-named generic payment endpoint -- the same pattern as the
Textract / TMS adapters -- so wiring a real processor (Stripe etc.) is configuration,
not hard-coded credentials.

Pricing model (integer cents). Over a tenant t's recorded usage events U(t), where
each event e surfaced overpay(e) >= 0 and recover(e) >= 0 cents:

    audits             = |U(t)|
    overpay_cents      = Σ_{e in U(t)} overpay(e)
    recoverable_cents  = Σ_{e in U(t)} recover(e)
    value_cents        = overpay_cents + recoverable_cents
    bill_cents         = base_fee_cents · audits + ⌊ pct_savings · value_cents ⌋

with base_fee_cents ∈ ℤ≥0 and pct_savings ∈ [0, 1] (per-audit fee + optional share
of the value delivered). Both are configurable; the defaults are placeholders.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .models import cents_to_str

BASE_FEE_ENV = "FREIGHT_AUDIT_BILL_BASE_CENTS"
PCT_SAVINGS_ENV = "FREIGHT_AUDIT_BILL_PCT"
BILLING_URL_ENV = "FREIGHT_AUDIT_BILLING_URL"
BILLING_TOKEN_ENV = "FREIGHT_AUDIT_BILLING_TOKEN"


@dataclass
class Pricing:
    base_fee_cents: int = 50          # $0.50 per audit (placeholder)
    pct_savings: float = 0.0          # share of value delivered (0..1)

    @classmethod
    def from_env(cls) -> "Pricing":
        return cls(
            base_fee_cents=int(os.getenv(BASE_FEE_ENV, "50")),
            pct_savings=float(os.getenv(PCT_SAVINGS_ENV, "0")),
        )

    def bill_cents(self, audits: int, value_cents: int) -> int:
        pct = min(max(self.pct_savings, 0.0), 1.0)
        return int(self.base_fee_cents) * int(audits) + int(pct * int(value_cents))


def bill_tenant(store, tenant_id: str = "default",
                pricing: Optional[Pricing] = None) -> dict:
    """Aggregate a tenant's recorded usage into a billing record (integer cents)."""
    pricing = pricing or Pricing.from_env()
    rows = store.usage_rows(tenant_id=tenant_id)
    audits = len(rows)
    overpay = sum(int(r.get("overpay_cents") or 0) for r in rows)
    recoverable = sum(int(r.get("recoverable_cents") or 0) for r in rows)
    value = overpay + recoverable
    amount = pricing.bill_cents(audits, value)
    return {
        "tenant_id": tenant_id,
        "audits": audits,
        "overpayment_caught_cents": overpay,
        "recoverable_revenue_cents": recoverable,
        "value_cents": value,
        "pricing": {"base_fee_cents": pricing.base_fee_cents,
                    "pct_savings": pricing.pct_savings},
        "billing_cents": amount,
        "billing_str": cents_to_str(amount),
    }


def charge(amount_cents: int, tenant: str, endpoint: Optional[str] = None,
           token: Optional[str] = None, *, currency: str = "usd",
           timeout: int = 30) -> dict:
    """Submit a charge to a generic payment endpoint (env-configured). This is the
    seam where a real processor plugs in -- credentials come from the environment
    only ($FREIGHT_AUDIT_BILLING_URL / _TOKEN), never source. Mock urlopen to test."""
    endpoint = endpoint or os.getenv(BILLING_URL_ENV)
    token = token or os.getenv(BILLING_TOKEN_ENV)
    if not endpoint:
        raise ValueError(
            f"no billing endpoint configured: pass endpoint= or set ${BILLING_URL_ENV}")

    body = json.dumps({"tenant": tenant, "amount_cents": int(amount_cents),
                       "currency": currency}).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        status = getattr(resp, "status", None) or resp.getcode()
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = raw
    return {"status": status, "tenant": tenant,
            "amount_cents": int(amount_cents), "response": parsed}
