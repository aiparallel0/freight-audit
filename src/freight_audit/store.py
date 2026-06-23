"""
Persistence + audit log + multi-tenant scoping (item #7).

Durable storage of every processed load and its findings, plus an append-only
AUDIT LOG of who did what -- and rows are scoped by tenant so one customer can
never read another's data. Backed by SQLite (zero-config, single file); the schema
maps cleanly to Postgres later.

Tenancy: every row carries a tenant_id (default "default" for single-tenant use).
A legacy single-tenant database is migrated in place on open -- the loads table is
recreated with a composite (tenant_id, load_id) primary key and existing rows are
assigned tenant_id "default"; decisions/audit_log gain a tenant_id column.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS loads (
    tenant_id      TEXT NOT NULL DEFAULT 'default',
    load_id        TEXT NOT NULL,
    client         TEXT,
    severity       TEXT,
    auto_approvable INTEGER,
    net_impact_cents INTEGER,
    findings_json  TEXT,
    processed_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, load_id)
);
CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    load_id      TEXT NOT NULL,
    decision     TEXT NOT NULL,          -- 'approved' | 'disputed'
    actor        TEXT NOT NULL,
    decided_at   TEXT NOT NULL,
    note         TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    ts        TEXT NOT NULL,
    actor     TEXT NOT NULL,
    action    TEXT NOT NULL,
    load_id   TEXT,
    detail    TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    """Durable, tenant-scoped store + audit log over SQLite."""

    def __init__(self, path: str = "freight_audit.db"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    # -- migration ---------------------------------------------------------
    def _has_column(self, table: str, column: str) -> bool:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == column for r in rows)

    def _migrate(self) -> None:
        """Bring a legacy single-tenant schema up to the tenant-scoped one."""
        if not self._has_column("loads", "tenant_id"):
            # recreate loads with a composite (tenant_id, load_id) primary key
            self.conn.executescript(
                """
                CREATE TABLE loads_new (
                    tenant_id      TEXT NOT NULL DEFAULT 'default',
                    load_id        TEXT NOT NULL,
                    client         TEXT,
                    severity       TEXT,
                    auto_approvable INTEGER,
                    net_impact_cents INTEGER,
                    findings_json  TEXT,
                    processed_at   TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, load_id)
                );
                INSERT INTO loads_new (tenant_id, load_id, client, severity,
                    auto_approvable, net_impact_cents, findings_json, processed_at)
                  SELECT 'default', load_id, client, severity, auto_approvable,
                    net_impact_cents, findings_json, processed_at FROM loads;
                DROP TABLE loads;
                ALTER TABLE loads_new RENAME TO loads;
                """)
        for table in ("decisions", "audit_log"):
            if not self._has_column(table, "tenant_id"):
                self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'")
        self.conn.commit()

    # -- audit -------------------------------------------------------------
    def audit(self, actor: str, action: str, load_id: Optional[str] = None,
              detail: Optional[str] = None, tenant_id: str = "default") -> None:
        self.conn.execute(
            "INSERT INTO audit_log (tenant_id, ts, actor, action, load_id, detail) "
            "VALUES (?,?,?,?,?,?)",
            (tenant_id, _now(), actor, action, load_id, detail))
        self.conn.commit()

    def audit_trail(self, load_id: Optional[str] = None,
                    tenant_id: str = "default") -> list[dict]:
        if load_id:
            cur = self.conn.execute(
                "SELECT * FROM audit_log WHERE tenant_id=? AND load_id=? ORDER BY id",
                (tenant_id, load_id))
        else:
            cur = self.conn.execute(
                "SELECT * FROM audit_log WHERE tenant_id=? ORDER BY id", (tenant_id,))
        return [dict(r) for r in cur.fetchall()]

    # -- loads -------------------------------------------------------------
    def save_result(self, result, client: str = "default", actor: str = "system",
                    tenant_id: str = "default") -> None:
        """Persist a MatchResult under a tenant (duck-typed: needs load_id, severity,
        findings, auto_approvable, net_money_impact_cents)."""
        findings = [
            {"type": getattr(f.type, "value", str(f.type)),
             "severity": getattr(f.severity, "value", str(f.severity)),
             "message": f.message, "money_impact_cents": f.money_impact_cents}
            for f in result.findings
        ]
        self.conn.execute(
            """INSERT INTO loads (tenant_id, load_id, client, severity, auto_approvable,
                   net_impact_cents, findings_json, processed_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(tenant_id, load_id) DO UPDATE SET
                   client=excluded.client, severity=excluded.severity,
                   auto_approvable=excluded.auto_approvable,
                   net_impact_cents=excluded.net_impact_cents,
                   findings_json=excluded.findings_json,
                   processed_at=excluded.processed_at""",
            (tenant_id, result.load_id, client,
             getattr(result.severity, "value", str(result.severity)),
             int(result.auto_approvable), result.net_money_impact_cents,
             json.dumps(findings), _now()))
        self.conn.commit()
        self.audit(actor, "process_load", result.load_id,
                   f"severity={getattr(result.severity,'value',result.severity)} "
                   f"net={result.net_money_impact_cents}", tenant_id=tenant_id)

    def get_load(self, load_id: str, tenant_id: str = "default") -> Optional[dict]:
        cur = self.conn.execute(
            "SELECT * FROM loads WHERE tenant_id=? AND load_id=?", (tenant_id, load_id))
        row = cur.fetchone()
        return dict(row) if row else None

    def all_loads(self, tenant_id: str = "default") -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM loads WHERE tenant_id=? ORDER BY processed_at DESC", (tenant_id,))
        return [dict(r) for r in cur.fetchall()]

    # -- decisions ---------------------------------------------------------
    def record_decision(self, load_id: str, decision: str, actor: str,
                        note: Optional[str] = None, tenant_id: str = "default") -> None:
        if decision not in ("approved", "disputed"):
            raise ValueError("decision must be 'approved' or 'disputed'")
        self.conn.execute(
            "INSERT INTO decisions (tenant_id, load_id, decision, actor, decided_at, note) "
            "VALUES (?,?,?,?,?,?)",
            (tenant_id, load_id, decision, actor, _now(), note))
        self.conn.commit()
        self.audit(actor, f"decision:{decision}", load_id, note, tenant_id=tenant_id)

    def decisions_for(self, load_id: str, tenant_id: str = "default") -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM decisions WHERE tenant_id=? AND load_id=? ORDER BY id",
            (tenant_id, load_id))
        return [dict(r) for r in cur.fetchall()]

    def summary(self, tenant_id: str = "default") -> dict:
        loads = self.all_loads(tenant_id)
        decided = self.conn.execute(
            "SELECT COUNT(DISTINCT load_id) c FROM decisions WHERE tenant_id=?",
            (tenant_id,)).fetchone()["c"]
        return {
            "loads": len(loads),
            "auto_approvable": sum(1 for l in loads if l["auto_approvable"]),
            "decided": decided,
            "audit_events": len(self.audit_trail(tenant_id=tenant_id)),
        }

    def report(self, tenant_id: str = "default") -> dict:
        """Aggregate savings/ROI report over a tenant's persisted loads (reporting.py)."""
        from .reporting import report_from_store      # lazy import avoids a cycle
        return report_from_store(self, tenant_id=tenant_id)

    def close(self):
        self.conn.close()
