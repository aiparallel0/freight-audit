#!/usr/bin/env python3
"""Command-line interface for the freight_audit engine.

Installed as the ``freight-audit`` console script:

    freight-audit                         # audit the bundled sample loads
    freight-audit path/to/*.json          # audit your own load bundles
    freight-audit --profile client.json   # apply a client business-rule profile
    freight-audit --export quickbooks_iif --export-out bills.iif
    freight-audit --json out.json         # write structured JSON

A "load bundle" is a JSON file with optional ``rate_confirmation``, ``invoice``,
and ``pod`` keys. See the bundled samples for the shape.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys

from . import (
    MatchEngine, JsonFixtureProvider, cents_to_str, ClientProfile,
    export, EXPORTERS, EXPORTER_EXT, Vocabulary, set_vocabulary,
)
from .profiles import apply_profile_rules
from .models import FindingType
from .reporting import report_from_results, format_report

C = {"ok": "\033[92m", "info": "\033[96m", "warn": "\033[93m",
     "block": "\033[91m", "end": "\033[0m"}


def _bundled_samples() -> list[str]:
    """Locate sample load bundles shipped with the repo (../samples/loads)."""
    here = os.path.dirname(os.path.abspath(__file__))
    # repo layout: src/freight_audit/cli.py -> ../../samples/loads
    candidate = os.path.normpath(os.path.join(here, "..", "..", "samples", "loads"))
    if os.path.isdir(candidate):
        return sorted(glob.glob(os.path.join(candidate, "*.json")))
    return []


def _process(path, provider, engine, profile):
    bundle = json.load(open(path))
    rc = provider.extract_rate_confirmation(bundle["rate_confirmation"]) if bundle.get("rate_confirmation") else None
    inv = provider.extract_invoice(bundle["invoice"]) if bundle.get("invoice") else None
    pod = provider.extract_pod(bundle["pod"]) if bundle.get("pod") else None
    result = engine.match(rc, inv, pod)
    if profile is not None and inv is not None:
        extra = apply_profile_rules(profile, rc, inv)
        if extra and len(result.findings) == 1 and result.findings[0].type == FindingType.OK:
            result.findings = []
        result.findings.extend(extra)
    return bundle, result


def main(argv=None):
    ap = argparse.ArgumentParser(prog="freight-audit", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="load-bundle JSON files (default: bundled samples)")
    ap.add_argument("--profile", help="path to a client ClientProfile JSON")
    ap.add_argument("--export", choices=list(EXPORTERS),
                    help="write a formatted export (csv/exceptions_csv/quickbooks_iif/tms_json)")
    ap.add_argument("--export-out", help="path for --export output (default auto-named)")
    ap.add_argument("--csv", help="write a flat CSV summary to this path")
    ap.add_argument("--json", help="write structured JSON to this path")
    ap.add_argument("--report", action="store_true",
                    help="print a savings/ROI report for this run")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)

    def col(sev):
        return sev.upper() if args.no_color else f"{C.get(sev,'')}{sev.upper()}{C['end']}"

    files = args.files or _bundled_samples()
    if not files:
        ap.error("no input files given and no bundled samples found; pass load-bundle JSON paths")

    provider = JsonFixtureProvider()
    profile = None
    if args.profile:
        profile = ClientProfile.load(args.profile)
        if profile.vocab_overlay_path and os.path.exists(profile.vocab_overlay_path):
            set_vocabulary(Vocabulary.load(client_overlay=profile.vocab_overlay_path))
        print(f"(using client profile: {profile.name})")
    engine = MatchEngine(profile.to_engine_config() if profile else None)

    rows, json_out, results = [], [], []
    print("=" * 78)
    print("FREIGHT-AUDIT  --  invoice / rate-con / POD audit")
    print("=" * 78)

    total_over = total_rec = 0
    auto = 0
    for path in files:
        _, result = _process(path, provider, engine, profile)
        results.append(result)
        sev = result.severity.value
        over = sum(f.money_impact_cents for f in result.findings if f.money_impact_cents > 0)
        rec = -sum(f.money_impact_cents for f in result.findings if f.money_impact_cents < 0)
        total_over += over
        total_rec += rec
        if result.auto_approvable:
            auto += 1
        label = "AUTO-APPROVE" if result.auto_approvable else "REVIEW"
        print(f"\nLoad {result.load_id}   [{col(sev)}]   {label}")
        for f in result.findings:
            money = f"  [{cents_to_str(f.money_impact_cents)}]" if f.money_impact_cents else ""
            print(f"    - ({f.severity.value.upper()}) {f.message}{money}")
        rows.append({
            "load_id": result.load_id, "severity": sev,
            "auto_approvable": result.auto_approvable,
            "overpayment": cents_to_str(over), "recoverable": cents_to_str(rec),
            "net_impact": cents_to_str(result.net_money_impact_cents),
        })
        json_out.append({
            "load_id": result.load_id, "severity": sev,
            "auto_approvable": result.auto_approvable,
            "net_impact_cents": result.net_money_impact_cents,
            "findings": [{"type": f.type.value, "severity": f.severity.value,
                          "message": f.message, "money_impact_cents": f.money_impact_cents}
                         for f in result.findings],
        })

    blocking = sum(1 for r in results if r.severity.value == "block")
    print("\n" + "=" * 78)
    print("SUMMARY")
    print(f"  loads processed     : {len(results)}")
    print(f"  auto-approvable     : {auto}")
    print(f"  need human review   : {len(results) - auto}  (blocking: {blocking})")
    print(f"  overpayment flagged : {cents_to_str(total_over)}")
    print(f"  recoverable revenue : {cents_to_str(total_rec)}")
    print(f"  TOTAL $ TOUCHED     : {cents_to_str(total_over + total_rec)}")
    print("=" * 78)

    if args.report:
        print("\n" + format_report(report_from_results(results)))

    if args.csv and rows:
        with open(args.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print(f"CSV written -> {args.csv}")
    if args.json:
        open(args.json, "w").write(json.dumps(json_out, indent=2))
        print(f"JSON written -> {args.json}")
    if args.export:
        out_path = args.export_out or f"export_{args.export}{EXPORTER_EXT.get(args.export, '.txt')}"
        open(out_path, "w").write(export(results, args.export))
        print(f"{args.export} export written -> {out_path}")

    return 0 if blocking == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
