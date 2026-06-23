"""
API-key auth: hashed keys with per-tenant scoping, roles, expiry, rotation, and
account creation. Keys are stored hashed (sha256); the raw key is shown once.

Roles: 'admin' (full), 'reviewer' (review actions), 'api' (audit calls). Expiry is
optional (ttl_days); an expired key fails verification. Accounts map an email to a
tenant and own an admin key.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

ROLES = ("admin", "reviewer", "api")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidEmail(ValueError):
    pass


class DuplicateAccount(ValueError):
    pass


def generate_key() -> str:
    """Create a new opaque API key (give to the client; store only its hash)."""
    return "fm_" + secrets.token_urlsafe(24)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KeyStore:
    """Hashed-key store (JSON file) with tenants, roles, expiry, and rotation."""

    def __init__(self, path: str = "api_keys.json"):
        self.path = path
        self.keys: dict[str, dict] = {}
        if os.path.exists(path):
            self.keys = json.load(open(path))

    # -- issuance ----------------------------------------------------------
    def issue(self, label: str, tenant_id: str = "default", role: str = "api",
              ttl_days: "int | None" = None, email: "str | None" = None) -> str:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        expires_at = None
        if ttl_days is not None:
            expires_at = (_now() + timedelta(days=ttl_days)).isoformat(timespec="seconds")
        key = generate_key()
        self.keys[hash_key(key)] = {
            "label": label,
            "tenant_id": tenant_id,
            "role": role,
            "email": email,
            "created_at": _now().isoformat(timespec="seconds"),
            "expires_at": expires_at,
            "active": True,
        }
        self._save()
        return key  # returned ONCE; only the hash is persisted

    # -- lookups -----------------------------------------------------------
    def _record(self, key: str) -> "dict | None":
        rec = self.keys.get(hash_key(key))
        if rec is None:
            hmac.compare_digest(hash_key(key), hash_key("x"))  # constant-time-ish
            return None
        if not rec.get("active"):
            return None
        if self._expired(rec):
            return None
        return rec

    @staticmethod
    def _expired(rec: dict) -> bool:
        exp = rec.get("expires_at")
        if not exp:
            return False
        try:
            return _now() > datetime.fromisoformat(exp)
        except ValueError:
            return False

    def verify(self, key: str) -> bool:
        return self._record(key) is not None

    def tenant_for(self, key: str) -> "str | None":
        rec = self._record(key)
        return rec.get("tenant_id", "default") if rec else None

    def role_for(self, key: str) -> "str | None":
        rec = self._record(key)
        return rec.get("role", "api") if rec else None

    # -- lifecycle ---------------------------------------------------------
    def revoke(self, key: str) -> bool:
        h = hash_key(key)
        if h in self.keys:
            self.keys[h]["active"] = False
            self._save()
            return True
        return False

    def rotate(self, key: str, ttl_days: "int | None" = None) -> "str | None":
        """Issue a replacement key with the same tenant/role/email and revoke the
        old one. Returns the new key, or None if the old key is not valid."""
        rec = self._record(key)
        if rec is None:
            return None
        new_key = self.issue(rec.get("label", "rotated"), rec.get("tenant_id", "default"),
                             role=rec.get("role", "api"), ttl_days=ttl_days,
                             email=rec.get("email"))
        self.revoke(key)
        return new_key

    # -- accounts ----------------------------------------------------------
    def account_exists(self, email: str) -> bool:
        e = email.strip().lower()
        return any(r.get("email", "").lower() == e and r.get("active")
                   for r in self.keys.values())

    def create_account(self, email: str, role: str = "admin",
                       ttl_days: "int | None" = None) -> dict:
        """Create a tenant for `email` and issue its first key. Raises InvalidEmail
        or DuplicateAccount. tenant_id is the (normalized) email -- globally unique."""
        e = email.strip().lower()
        if not _EMAIL_RE.match(e):
            raise InvalidEmail(email)
        if self.account_exists(e):
            raise DuplicateAccount(email)
        key = self.issue(label=e, tenant_id=e, role=role, ttl_days=ttl_days, email=e)
        return {"tenant": e, "api_key": key, "role": role}

    def _save(self):
        json.dump(self.keys, open(self.path, "w"), indent=2)
