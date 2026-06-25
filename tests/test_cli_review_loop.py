"""
Tests for the CLI review-loop wiring (Phase C stage 10).

Verifies the backend-free loop end to end:
  engine run --db  -> results persisted to the store + audit log
  engine run --json -> findings.json the review console renders
  --import-decisions -> the console's exported CSV recorded as decisions + audit

No web server involved; everything goes through the existing store.py.
"""
import csv
import json
import os

import pytest

from freight_audit.cli import main
from freight_audit.store import Store

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(REPO, "samples", "loads", "load_002_overcharge_duplicate.json")


def test_cli_persists_results_and_audit_log(tmp_path):
    db = str(tmp_path / "pilot.db")
    rc = main([SAMPLE, "--db", db, "--actor", "auditbot", "--no-color"])
    assert rc == 0                                  # overcharge is WARN, not blocking

    s = Store(db)
    try:
        assert any(l["load_id"] == "L-100482" for l in s.all_loads())
        trail = s.audit_trail("L-100482")
        assert any(e["action"] == "process_load" and e["actor"] == "auditbot"
                   for e in trail)
    finally:
        s.close()


def test_cli_writes_review_findings_json(tmp_path):
    out = str(tmp_path / "findings.json")
    main([SAMPLE, "--json", out, "--no-color"])
    data = json.loads(open(out).read())
    # exactly the shape the review console's load() expects
    assert isinstance(data, list) and data
    row = data[0]
    assert row["load_id"] == "L-100482"
    assert "severity" in row and "auto_approvable" in row
    assert isinstance(row["findings"], list)
    assert {"type", "severity", "message", "money_impact_cents"} <= set(row["findings"][0])


def test_cli_imports_console_decisions(tmp_path):
    db = str(tmp_path / "pilot.db")
    main([SAMPLE, "--db", db, "--no-color"])        # persist the load first

    # a decisions CSV exactly as the review console exports it
    dec = tmp_path / "freight-audit-decisions.csv"
    with open(dec, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["load_id", "severity", "overpayment", "recoverable", "decision"])
        w.writerow(["L-100482", "warn", "$400.00", "$0.00", "disputed"])
        w.writerow(["L-99999", "ok", "$0.00", "$0.00", "undecided"])  # must be skipped

    rc = main(["--import-decisions", str(dec), "--db", db,
               "--actor", "jane", "--no-color"])
    assert rc == 0

    s = Store(db)
    try:
        decs = s.decisions_for("L-100482")
        assert len(decs) == 1
        assert decs[0]["decision"] == "disputed" and decs[0]["actor"] == "jane"
        assert s.decisions_for("L-99999") == []     # undecided row skipped
        assert any(e["action"] == "decision:disputed"
                   for e in s.audit_trail("L-100482"))
    finally:
        s.close()


def test_import_decisions_requires_db(tmp_path):
    dec = tmp_path / "d.csv"
    dec.write_text("load_id,decision\nL-1,approved\n")
    with pytest.raises(SystemExit):                 # argparse error -> SystemExit
        main(["--import-decisions", str(dec)])
