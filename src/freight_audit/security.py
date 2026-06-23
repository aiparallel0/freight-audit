"""
Minimal API-key gate (scaffolding for "not ready" item #7).

This is intentionally minimal -- a single-tenant API-key check with hashed keys,
enough to put in front of a pilot endpoint so it isn't wide open. It is NOT a real
auth system (no users, roles, sessions, rotation policy, rate limiting). Those are
premature before you have a paying customer; this is the honest floor.

Keys are stored hashed (sha256). Generate one, hand it to the pilot client, verify
on each request.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone


def generate_key() -> str:
    """Create a new opaque API key (give this to the client; store only its hash)."""
    return "fm_" + secrets.token_urlsafe(24)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyStore:
    """Tiny hashed-key store backed by a JSON file."""

    def __init__(self, path: str = "api_keys.json"):
        self.path = path
        self.keys: dict[str, dict] = {}
        if os.path.exists(path):
            self.keys = json.load(open(path))

    def issue(self, label: str, tenant_id: str = "default") -> str:
        key = generate_key()
        self.keys[hash_key(key)] = {
            "label": label,
            "tenant_id": tenant_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "active": True,
        }
        self._save()
        return key  # returned ONCE; only the hash is persisted

    def verify(self, key: str) -> bool:
        rec = self.keys.get(hash_key(key))
        # constant-time-ish: still do a dummy compare if absent
        if rec is None:
            hmac.compare_digest(hash_key(key), hash_key("x"))
            return False
        return bool(rec.get("active"))

    def tenant_for(self, key: str) -> "str | None":
        """Return the tenant a key belongs to, or None if missing/revoked.
        Legacy keys issued without a tenant default to 'default'."""
        rec = self.keys.get(hash_key(key))
        if rec is None:
            hmac.compare_digest(hash_key(key), hash_key("x"))
            return None
        return rec.get("tenant_id", "default") if rec.get("active") else None

    def revoke(self, key: str) -> bool:
        h = hash_key(key)
        if h in self.keys:
            self.keys[h]["active"] = False
            self._save()
            return True
        return False

    def _save(self):
        json.dump(self.keys, open(self.path, "w"), indent=2)
