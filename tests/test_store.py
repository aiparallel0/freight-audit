"""
Tests for the minimum-viable hardening (item #7): persistence, audit log, API keys.

Run:  python tests/test_store.py
"""
import glob
import json
import os
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from freight_audit import MatchEngine, JsonFixtureProvider  # noqa: E402
from freight_audit.store import Store  # noqa: E402
from freight_audit.security import KeyStore, generate_key, hash_key  # noqa: E402


def _results():
    prov, eng = JsonFixtureProvider(), MatchEngine()
    out = []
    for path in sorted(glob.glob(os.path.join(REPO, "samples", "loads", "*.json"))):
        b = json.load(open(path))
        rc = prov.extract_rate_confirmation(b["rate_confirmation"]) if b.get("rate_confirmation") else None
        inv = prov.extract_invoice(b["invoice"]) if b.get("invoice") else None
        pod = prov.extract_pod(b["pod"]) if b.get("pod") else None
        out.append(eng.match(rc, inv, pod))
    return out


def test_store_persists_and_reopens():
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "t.db")
        s = Store(db)
        for r in _results():
            s.save_result(r, client="Acme", actor="tester")
        assert s.summary()["loads"] == 5
        s.close()
        # reopen -> data survives
        s2 = Store(db)
        assert s2.summary()["loads"] == 5
        assert s2.get_load("L-100482")["severity"] == "warn"
        s2.close()


def test_audit_log_records_actions():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "t.db"))
        for r in _results():
            s.save_result(r, actor="tester")
        s.record_decision("L-100481", "approved", actor="jane", note="ok")
        trail = s.audit_trail("L-100481")
        actions = [e["action"] for e in trail]
        assert "process_load" in actions
        assert "decision:approved" in actions
        # actor + timestamp captured
        assert all(e["actor"] and e["ts"] for e in trail)
        s.close()


def test_decision_validation():
    with tempfile.TemporaryDirectory() as d:
        s = Store(os.path.join(d, "t.db"))
        try:
            s.record_decision("L1", "maybe", actor="x")
            assert False, "should reject invalid decision"
        except ValueError:
            pass
        s.close()


def test_api_keys_issue_verify_revoke():
    with tempfile.TemporaryDirectory() as d:
        ks = KeyStore(os.path.join(d, "k.json"))
        key = ks.issue("pilot")
        assert key.startswith("fm_")
        assert ks.verify(key) is True
        assert ks.verify("fm_nope") is False
        assert ks.revoke(key) is True
        assert ks.verify(key) is False
        # only the hash is persisted, never the raw key
        stored = json.load(open(os.path.join(d, "k.json")))
        assert hash_key(key) in stored
        assert key not in json.dumps(stored)
