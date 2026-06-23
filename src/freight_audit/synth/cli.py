#!/usr/bin/env python3
"""Generate synthetic, PII-free freight documents for OCR testing/benchmarking.

Installed as the ``freight-audit-synth`` console script:

    freight-audit-synth --count 10 --out synth_out/            # images + ground truth
    freight-audit-synth --count 5 --seed 42 --out synth_out/
    freight-audit-synth --bundles-only --out bundles/          # JSON only, no Pillow

Each load yields: <id>_invoice.png, <id>_rate_confirmation.png, <id>_pod.png,
<id>.gt.json (integer-cents ground truth), <id>.bundle.json (engine-ready), and a
manifest.jsonl. Everything is invented -- no real company/person data.
"""
from __future__ import annotations

import argparse
import json
import os
import random

from .generate import generate_load


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="freight-audit-synth", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=5, help="how many load bundles to make")
    ap.add_argument("--out", default="synth_out", help="output directory")
    ap.add_argument("--seed", type=int, default=0, help="base RNG seed (reproducible)")
    ap.add_argument("--bundles-only", action="store_true",
                    help="write engine-ready bundle JSON only (no images; no Pillow needed)")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    rng = random.Random(args.seed)
    manifest_path = os.path.join(args.out, "manifest.jsonl")

    render_load = None
    if not args.bundles_only:
        try:
            from .render import render_load
        except ImportError:
            ap.error("rendering needs Pillow -- install `freight-audit[synth]`, "
                     "or use --bundles-only")

    written = 0
    with open(manifest_path, "w") as manifest:
        for _ in range(args.count):
            bundle = generate_load(seed=rng.randrange(1_000_000))
            lid = bundle["load_id"]
            if args.bundles_only:
                path = os.path.join(args.out, f"{lid}.bundle.json")
                with open(path, "w") as fh:
                    json.dump(bundle, fh, indent=2)
                manifest.write(json.dumps({"load_id": lid, "bundle": path}) + "\n")
            else:
                paths = render_load(bundle, args.out)
                manifest.write(json.dumps({"load_id": lid, **paths}) + "\n")
            written += 1

    print(f"wrote {written} load(s) to {args.out}/ (manifest: {manifest_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
