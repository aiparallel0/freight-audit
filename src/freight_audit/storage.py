"""
Config-switchable storage: SQLite (default) or Postgres.

get_store() returns the sqlite Store unless DATABASE_URL (read via the secret
provider) is a postgres URL, in which case it returns a PostgresStore with the same
public surface (so reporting/billing/api don't care which backend they got).
psycopg is imported lazily, so the core install never needs it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from .secret_provider import get_secret

_PG_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS loads (
        tenant_id        TEXT NOT NULL DEFAULT 'default',
        load_id          TEXT NOT NULL,
        client           TEXT,
        severity         TEXT,
        auto_approvable  INTEGER,
        net_impact_cents BIGINT,
        findings_json    TEXT,
        processed_at     TEXT NOT NULL,
        PRIMARY KEY (tenant_id, load_id))""",
    """CREATE TABLE IF NOT EXISTS decisions (
        id          BIGSERIAL PRIMARY KEY,
        tenant_id   TEXT NOT NULL DEFAULT 'default',
        load_id     TEXT NOT NULL,
        decision    TEXT NOT NULL,
        actor       TEXT NOT NULL,
        decided_at  TEXT NOT NULL,
        note        TEXT)""",
    """CREATE TABLE IF NOT EXISTS audit_log (
        id        BIGSERIAL PRIMARY KEY,
        tenant_id TEXT NOT NULL DEFAULT 'default',
        ts        TEXT NOT NULL,
        actor     TEXT NOT NULL,
        action    TEXT NOT NULL,
        load_id   TEXT,
        detail    TEXT)""",
    """CREATE TABLE IF NOT EXISTS usage (
        id                BIGSERIAL PRIMARY KEY,
        tenant_id         TEXT NOT NULL DEFAULT 'default',
        load_id           TEXT,
        overpay_cents     BIGINT NOT NULL DEFAULT 0,
        recoverable_cents BIGINT NOT NULL DEFAULT 0,
        ts                TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS bundles (
        tenant_id   TEXT NOT NULL DEFAULT 'default',
        load_id     TEXT NOT NULL,
        bundle_json TEXT NOT NULL,
        received_at TEXT NOT NULL,
        PRIMARY KEY (tenant_id, load_id))""",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PostgresStore:
    """Durable, tenant-scoped store + audit log backed by Postgres (psycopg3).
    Public surface mirrors store.Store so it is a drop-in replacement."""

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self.url = url
        self.conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)
        for stmt in _PG_SCHEMA:
            self.conn.execute(stmt)

    # -- audit -------------------------------------------------------------
    def audit(self, actor, action, load_id=None, detail=None, tenant_id="default"):
        self.conn.execute(
            "INSERT INTO audit_log (tenant_id, ts, actor, action, load_id, detail) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (tenant_id, _now(), actor, action, load_id, detail))

    def audit_trail(self, load_id=None, tenant_id="default"):
        if load_id:
            cur = self.conn.execute(
                "SELECT * FROM audit_log WHERE tenant_id=%s AND load_id=%s ORDER BY id",
                (tenant_id, load_id))
        else:
            cur = self.conn.execute(
                "SELECT * FROM audit_log WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        return [dict(r) for r in cur.fetchall()]

    # -- loads -------------------------------------------------------------
    def save_result(self, result, client="default", actor="system", tenant_id="default"):
        findings = [
            {"type": getattr(f.type, "value", str(f.type)),
             "severity": getattr(f.severity, "value", str(f.severity)),
             "message": f.message, "money_impact_cents": f.money_impact_cents}
            for f in result.findings
        ]
        self.conn.execute(
            """INSERT INTO loads (tenant_id, load_id, client, severity, auto_approvable,
                   net_impact_cents, findings_json, processed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, load_id) DO UPDATE SET
                   client=excluded.client, severity=excluded.severity,
                   auto_approvable=excluded.auto_approvable,
                   net_impact_cents=excluded.net_impact_cents,
                   findings_json=excluded.findings_json,
                   processed_at=excluded.processed_at""",
            (tenant_id, result.load_id, client,
             getattr(result.severity, "value", str(result.severity)),
             int(result.auto_approvable), result.net_money_impact_cents,
             json.dumps(findings), _now()))
        self.audit(actor, "process_load", result.load_id,
                   f"severity={getattr(result.severity,'value',result.severity)} "
                   f"net={result.net_money_impact_cents}", tenant_id=tenant_id)

    def get_load(self, load_id, tenant_id="default"):
        cur = self.conn.execute(
            "SELECT * FROM loads WHERE tenant_id=%s AND load_id=%s", (tenant_id, load_id))
        row = cur.fetchone()
        return dict(row) if row else None

    def all_loads(self, tenant_id="default"):
        cur = self.conn.execute(
            "SELECT * FROM loads WHERE tenant_id=%s ORDER BY processed_at DESC", (tenant_id,))
        return [dict(r) for r in cur.fetchall()]

    # -- decisions ---------------------------------------------------------
    def record_decision(self, load_id, decision, actor, note=None, tenant_id="default"):
        if decision not in ("approved", "disputed"):
            raise ValueError("decision must be 'approved' or 'disputed'")
        self.conn.execute(
            "INSERT INTO decisions (tenant_id, load_id, decision, actor, decided_at, note) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (tenant_id, load_id, decision, actor, _now(), note))
        self.audit(actor, f"decision:{decision}", load_id, note, tenant_id=tenant_id)

    def decisions_for(self, load_id, tenant_id="default"):
        cur = self.conn.execute(
            "SELECT * FROM decisions WHERE tenant_id=%s AND load_id=%s ORDER BY id",
            (tenant_id, load_id))
        return [dict(r) for r in cur.fetchall()]

    # -- usage / metering --------------------------------------------------
    def record_usage(self, tenant_id, load_id, overpay_cents=0, recoverable_cents=0):
        self.conn.execute(
            "INSERT INTO usage (tenant_id, load_id, overpay_cents, recoverable_cents, ts) "
            "VALUES (%s,%s,%s,%s,%s)",
            (tenant_id, load_id, int(overpay_cents), int(recoverable_cents), _now()))

    def usage_rows(self, tenant_id="default"):
        cur = self.conn.execute(
            "SELECT * FROM usage WHERE tenant_id=%s ORDER BY id", (tenant_id,))
        return [dict(r) for r in cur.fetchall()]

    def save_bundle(self, tenant_id, load_id, bundle):
        self.conn.execute(
            """INSERT INTO bundles (tenant_id, load_id, bundle_json, received_at)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (tenant_id, load_id) DO UPDATE SET
                   bundle_json=excluded.bundle_json, received_at=excluded.received_at""",
            (tenant_id, load_id, json.dumps(bundle), _now()))

    def all_bundles(self, tenant_id="default"):
        cur = self.conn.execute(
            "SELECT * FROM bundles WHERE tenant_id=%s ORDER BY received_at", (tenant_id,))
        return [dict(r) for r in cur.fetchall()]

    def summary(self, tenant_id="default"):
        loads = self.all_loads(tenant_id)
        decided = self.conn.execute(
            "SELECT COUNT(DISTINCT load_id) AS c FROM decisions WHERE tenant_id=%s",
            (tenant_id,)).fetchone()["c"]
        return {"loads": len(loads),
                "auto_approvable": sum(1 for l in loads if l["auto_approvable"]),
                "decided": decided,
                "audit_events": len(self.audit_trail(tenant_id=tenant_id))}

    def report(self, tenant_id="default"):
        from .reporting import report_from_store
        return report_from_store(self, tenant_id=tenant_id)

    def close(self):
        self.conn.close()


def get_store(url: Optional[str] = None, sqlite_path: Optional[str] = None):
    """Return a store chosen by config: Postgres when DATABASE_URL is a postgres
    URL, otherwise the sqlite Store."""
    url = url or get_secret("DATABASE_URL")
    if url and url.startswith(("postgres://", "postgresql://")):
        return PostgresStore(url)
    from .store import Store
    return Store(sqlite_path or get_secret("FREIGHT_AUDIT_DB", "freight_audit.db"))
