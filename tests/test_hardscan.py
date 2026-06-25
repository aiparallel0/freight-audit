"""
Hard-scan OCR harness (#8, scaffold): degrade synthetic invoices and measure
extraction accuracy under degradation. Needs Pillow + tesseract.

BLOCKED (real accuracy): a corpus of real hard scans is required for true numbers;
this exercises the harness on synthetic degraded fixtures.
"""
import shutil

import pytest


def test_hardscan_harness_runs():
    pytest.importorskip("PIL")
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract not installed")
    from freight_audit.synth import generate_load
    from freight_audit.hardscan import benchmark_hardscan

    bundles = [generate_load(seed=i, with_detention=False) for i in (1, 2)]
    agg = benchmark_hardscan(bundles, rotate=1.0, blur=0.3)
    assert agg["documents"] == 2
    assert 0.0 <= agg["field_accuracy"] <= 1.0
    assert agg["degradation"]["rotate"] == 1.0
