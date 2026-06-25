"""
Tests for the vocabulary curation tool (PROMPTS #4): `freight-audit-vocab suggest`.
"""
import json

from freight_audit import Vocabulary
from freight_audit.vocab_cli import main


def test_suggest_emits_loadable_overlay(tmp_path):
    inp = tmp_path / "descs.json"
    inp.write_text(json.dumps(["dock fee", "unload service", "FSC", "random widget"]))
    out = tmp_path / "overlay.json"

    assert main(["suggest", str(inp), "-o", str(out)]) == 0
    overlay = json.load(open(out))
    assert "categories" in overlay
    assert "dock fee" in overlay["categories"].get("lumper", [])

    # base can't categorize it; the generated overlay makes it categorize off 'other'
    assert Vocabulary.load().categorize("dock fee") == "other"
    tuned = Vocabulary.load(client_overlay=str(out))
    assert tuned.categorize("dock fee") == "lumper"
    # every (category, trigger) in the overlay round-trips through categorize()
    for cat, triggers in overlay["categories"].items():
        for t in triggers:
            assert tuned.categorize(t) == cat


def test_suggest_reads_load_bundles(tmp_path):
    bundle = {"invoice": {"line_items": [
        {"description": "dock fee", "amount": "$150.00"},
        {"description": "Linehaul", "amount": "$1,000.00"}]}}
    inp = tmp_path / "loads.json"
    inp.write_text(json.dumps([bundle]))
    out = tmp_path / "overlay.json"

    assert main(["suggest", str(inp), "-o", str(out)]) == 0
    overlay = json.load(open(out))
    # "dock fee" pulled from the bundle's invoice line items and filed by guess
    assert any("dock fee" in triggers for triggers in overlay["categories"].values())
    # "Linehaul" already matches the base vocab -> not suggested
    assert all("Linehaul" not in triggers for triggers in overlay["categories"].values())
