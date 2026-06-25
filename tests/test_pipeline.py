"""
Tests for the image -> audit bridge (PROMPTS #2).

Render synthetic document images, then run them straight through audit_documents()
to a MatchResult. Needs Pillow + the tesseract binary (skipped otherwise).
"""
import shutil

import pytest

from freight_audit import MatchResult, FindingType
from freight_audit.pipeline import audit_documents
from freight_audit.synth import generate_load, invoice_ground_truth


def _need_ocr():
    pytest.importorskip("PIL")
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract binary not installed")


def test_invoice_image_audits_end_to_end(tmp_path):
    _need_ocr()
    from freight_audit.synth import render_invoice
    b = generate_load(seed=2, with_detention=True)
    img = tmp_path / "invoice.png"
    render_invoice(b).save(str(img))

    r = audit_documents(invoice_img=str(img))
    assert isinstance(r, MatchResult)
    assert r.invoice is not None
    assert r.invoice.billed_total_cents == invoice_ground_truth(b)["billed_total_cents"]
    assert r.load_id == b["load_id"]                 # parsed from "Load L-..."


def test_three_images_map_all_docs(tmp_path):
    _need_ocr()
    from freight_audit.synth import render_invoice, render_rate_confirmation, render_pod
    b = generate_load(seed=3, with_detention=True)
    ri, rr, rp = tmp_path / "i.png", tmp_path / "r.png", tmp_path / "p.png"
    render_invoice(b).save(str(ri))
    render_rate_confirmation(b).save(str(rr))
    render_pod(b).save(str(rp))

    r = audit_documents(str(rr), str(ri), str(rp))
    assert isinstance(r, MatchResult)
    assert r.rate_con is not None and r.invoice is not None and r.pod is not None
    assert r.invoice.billed_total_cents == invoice_ground_truth(b)["billed_total_cents"]
    assert isinstance(r.severity.value, str)


def test_audit_documents_runs_without_images():
    # no images -> no docs -> MISSING_DOC, but the call still returns a MatchResult
    r = audit_documents()
    assert isinstance(r, MatchResult)
    assert any(f.type == FindingType.MISSING_DOC for f in r.findings)
