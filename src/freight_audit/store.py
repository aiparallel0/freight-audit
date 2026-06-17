"""
Persistence + audit log (improves "not ready" item #7).

This is the *minimum viable* hardening a real pilot actually needs -- not a full
production platform (that's still premature), but the two pieces you can't run a
money-touching pilot without:

  1. Durable storage of every processed load and its findings (so results survive
     a restart and you can report on them).
  2. An append-only AUDIT LOG of who did what (processed a load, approved/disputed
     an invoice) -- because you're making pay/don't-pay decisions on someone's
     money, and "who approved this and when" must be answerable.

Backed by SQLite (zero-config, single file, ships with Python). Same schema maps
cleanly to Postgres later. No server, no ORM, no dependencies.

Deliberately NOT included (still premature pre-revenue, per STATUS.md): multi-tenant
auth, role management, web server, queues, horizontal scale. A simple API-key gate
is provided in security.py as scaffolding only.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS loads (
    load_id        TEXT PRIMARY KEY,
    client         TEXT,
    severity       TEXT,
    auto_approvable INTEGER,
    net_impact_cents INTEGER,
    findings_json  TEXT,
    processed_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    load_id      TEXT NOT NULL,
    decision     TEXT NOT NULL,          -- 'approved' | 'disputed'
    actor        TEXT NOT NULL,
    decided_at   TEXT NOT NULL,
    note         TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
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
    """Thin durable store + audit log over SQLite."""

    def __init__(self, path: str = "freight_audit.db"):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # -- audit -------------------------------------------------------------
    def audit(self, actor: str, action: str, load_id: Optional[str] = None,
              detail: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (ts, actor, action, load_id, detail) VALUES (?,?,?,?,?)",
            (_now(), actor, action, load_id, detail))
        self.conn.commit()

    def audit_trail(self, load_id: Optional[str] = None) -> list[dict]:
        if load_id:
            cur = self.conn.execute(
                "SELECT * FROM audit_log WHERE load_id=? ORDER BY id", (load_id,))
        else:
            cur = self.conn.execute("SELECT * FROM audit_log ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    # -- loads -------------------------------------------------------------
    def save_result(self, result, client: str = "default", actor: str = "system") -> None:
        """Persist a MatchResult (duck-typed: needs load_id, severity, findings,
        auto_approvable, net_money_impact_cents)."""
        findings = [
            {"type": getattr(f.type, "value", str(f.type)),
             "severity": getattr(f.severity, "value", str(f.severity)),
             "message": f.message, "money_impact_cents": f.money_impact_cents}
            for f in result.findings
        ]
        self.conn.execute(
            """INSERT INTO loads (load_id, client, severity, auto_approvable,
                   net_impact_cents, findings_json, processed_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(load_id) DO UPDATE SET
                   client=excluded.client, severity=excluded.severity,
                   auto_approvable=excluded.auto_approvable,
                   net_impact_cents=excluded.net_impact_cents,
                   findings_json=excluded.findings_json,
                   processed_at=excluded.processed_at""",
            (result.load_id, client, getattr(result.severity, "value", str(result.severity)),
             int(result.auto_approvable), result.net_money_impact_cents,
             json.dumps(findings), _now()))
        self.conn.commit()
        self.audit(actor, "process_load", result.load_id,
                   f"severity={getattr(result.severity,'value',result.severity)} "
                   f"net={result.net_money_impact_cents}")

    def get_load(self, load_id: str) -> Optional[dict]:
        cur = self.conn.execute("SELECT * FROM loads WHERE load_id=?", (load_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def all_loads(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM loads ORDER BY processed_at DESC")
        return [dict(r) for r in cur.fetchall()]

    # -- decisions ---------------------------------------------------------
    def record_decision(self, load_id: str, decision: str, actor: str,
                         note: Optional[str] = None) -> None:
        if decision not in ("approved", "disputed"):
            raise ValueError("decision must be 'approved' or 'disputed'")
        self.conn.execute(
            "INSERT INTO decisions (load_id, decision, actor, decided_at, note) VALUES (?,?,?,?,?)",
            (load_id, decision, actor, _now(), note))
        self.conn.commit()
        self.audit(actor, f"decision:{decision}", load_id, note)

    def decisions_for(self, load_id: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT * FROM decisions WHERE load_id=? ORDER BY id", (load_id,))
        return [dict(r) for r in cur.fetchall()]

    def summary(self) -> dict:
        loads = self.all_loads()
        decided = self.conn.execute("SELECT COUNT(DISTINCT load_id) c FROM decisions").fetchone()["c"]
        return {
            "loads": len(loads),
            "auto_approvable": sum(1 for l in loads if l["auto_approvable"]),
            "decided": decided,
            "audit_events": len(self.audit_trail()),
        }

    def report(self) -> dict:
        """Aggregate savings/ROI report over every persisted load (see reporting.py)."""
        from .reporting import report_from_store      # lazy import avoids a cycle
        return report_from_store(self)

    def close(self):
        self.conn.close()
