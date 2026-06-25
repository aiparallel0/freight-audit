"""
The committed synthetic corpus + an aggregate OCR-accuracy benchmark in the suite.

The corpus is a fixed, PII-free set of bundle JSONs under tests/fixtures/synth_corpus/.
The benchmark renders each invoice, OCRs it with the freight_invoice layout, and
scores against integer-cents ground truth -- a reproducible accuracy number that
guards the OCR pipeline against regressions. Needs Pillow + the tesseract binary.
"""
import glob
import json
import os
import shutil

import pytest

from freight_audit.synth import invoice_ground_truth

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(REPO, "tests", "fixtures", "synth_corpus")


def _bundles():
    return [json.load(open(p)) for p in sorted(glob.glob(os.path.join(CORPUS, "*.bundle.json")))]


def test_corpus_present_and_internally_consistent():
    bundles = _bundles()
    assert len(bundles) >= 4
    for b in bundles:
        gt = invoice_ground_truth(b)
        # the labelled total equals the sum of the labelled line items, in cents
        assert gt["billed_total_cents"] == sum(gt["line_amounts_cents"])


def test_corpus_ocr_accuracy_benchmark(capsys):
    pytest.importorskip("PIL")
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not installed")
    from freight_audit.synth import benchmark_bundles, format_benchmark

    bundles = _bundles()
    agg = benchmark_bundles(bundles)
    with capsys.disabled():
        print("\n" + format_benchmark(agg))

    assert agg["documents"] == len(bundles)
    # clean rendered text should OCR to high accuracy via the freight_invoice layout
    assert agg["total_exact_rate"] >= 0.8, agg
    assert agg["field_accuracy"] >= 0.8, agg
    assert agg["line_amount_recall"] >= 0.8, agg


def test_main_cli_benchmark_requires_bundles(tmp_path):
    from freight_audit.cli import main
    with pytest.raises(SystemExit):                 # empty dir -> argparse error
        main(["--benchmark", str(tmp_path)])


def test_main_cli_benchmark_runs(tmp_path, capsys):
    pytest.importorskip("PIL")
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not installed")
    from freight_audit.cli import main
    from freight_audit.synth import generate_load
    b = generate_load(seed=99)
    (tmp_path / f"{b['load_id']}.bundle.json").write_text(json.dumps(b))
    assert main(["--benchmark", str(tmp_path)]) == 0
    assert "OCR accuracy benchmark" in capsys.readouterr().out
