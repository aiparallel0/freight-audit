# OCR accuracy benchmarks

Two complementary benchmarks measure the OCR pipeline's field-extraction accuracy.
Money is always compared in **integer cents**. The harness lives in
`freight_audit.synth.score` (`score_invoice`, `aggregate_scores`) and
`freight_audit.synth.benchmark` (`benchmark_bundles`), and external datasets are
mapped in via `freight_audit.synth.datasets`.

## 1. Synthetic, in-distribution (runs in CI)

A committed, PII-free corpus (`tests/fixtures/synth_corpus/*.bundle.json`) is rendered
to invoice images, OCR'd with the `freight_invoice` layout, and scored against
integer-cents ground truth.

```bash
freight-audit --benchmark tests/fixtures/synth_corpus
# (identical: freight-audit-synth --benchmark tests/fixtures/synth_corpus)
```

Result (6 docs): **field accuracy 100% · total-exact 100% · line-amount recall 100%.**
`tests/test_benchmark.py` runs this on every suite and guards against regressions.

## 2. Real, out-of-distribution (ad-hoc; data NOT committed)

Tested against **25 real receipts from CORD-v2** (`naver-clova-ix/cord-v2`, CC-BY-4.0)
via the `cord_truth` adapter. Real data is downloaded to a scratch dir, never the repo.

| metric | result |
|---|---|
| OCR legibility — true total present anywhere in the raw OCR text | **40%** |
| Layout extraction — generic layout pulls the total into the right field | **8%** |
| Line-amount recall — generic layout | **4%** |

### Honest read
- Tesseract on raw phone-photo receipts reads the correct total only ~40% of the
  time: real scans are hard (skew, thermal print, locale formatting).
- The shipped **generic** layout extracts the total correctly only ~8% — CORD's
  Indonesian retail format doesn't match the shipped layout profiles, and its locale
  numbers (dot-thousands in the labels vs comma on the image, 3-digit rupiah, no
  cents) don't fit a 2-decimal/cents parser.
- This is expected out-of-distribution behaviour and **validates the architecture**:
  accuracy on a client's documents comes from (a) a per-format `LayoutProfile`
  (config, not code — see `ocr/layouts.py`) and (b) for hard scans, swapping
  Tesseract for a cloud OCR provider via the `TextractProvider` seam — not from
  expecting a generic layout to transfer. The 100% in-distribution number is the
  ceiling available once the format is known.

### Reproduce
```bash
pip install datasets
python - <<'PY'
from datasets import load_dataset
from freight_audit.ocr import extract_receipt, DEFAULT_LAYOUT
from freight_audit.synth import cord_truth, ocr_invoice_fields, score_invoice, aggregate_scores
ds = load_dataset("naver-clova-ix/cord-v2", split="test")
scores = []
for i in range(25):
    ds[i]["image"].convert("RGB").save("r.png")
    rec = extract_receipt("r.png", use_preprocess=True, layout=DEFAULT_LAYOUT)
    scores.append(score_invoice(ocr_invoice_fields(rec), cord_truth(ds[i]["ground_truth"])))
print(aggregate_scores(scores))
PY
```

To benchmark a **real freight** set you hold privately: keep the documents in the
git-ignored `private/` folder, map each record to the scorer's `truth` shape (CORD /
SROIE / FATURA adapters are in `freight_audit.synth.datasets`; add your own the same
way), then run `benchmark_bundles` or `score_invoice` + `aggregate_scores`.
