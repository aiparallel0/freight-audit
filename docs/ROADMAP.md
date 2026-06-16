# Roadmap

This is the **technical** direction for `freight-audit`. It maps to the tasks in
`PROMPTS.md`. (Business / go-to-market planning is intentionally kept out of the
code repo.)

## Now — works and tested (37 tests)

- Core audit engine: total/line/accessorial/duplicate/detention checks + integrity
  guards (load-ID mismatch, bad POD timing, capped detention).
- Data-driven accessorial vocabulary with per-client overlays + a learning loop.
- Per-client business-rule profiles (fuel formula, caps, tolerances) as JSON.
- Output exporters: CSV, exception worklist CSV, QuickBooks IIF, generic TMS JSON.
- OCR pipeline: preprocess → Tesseract → layout-driven parse → self-validation →
  CONFIDENT/NEEDS-REVIEW verdict, with config-driven layout profiles.
- Optional hardening: SQLite persistence, append-only audit log, API-key scaffold.
- Installable package with two console scripts and a pytest suite.

## Next — close the gap to a real deployment

Ordered by value (see `PROMPTS.md` for the runnable task specs):

1. **Real cloud OCR.** Implement one provider adapter (Textract) behind the
   existing seam in `extract.py`. This is the single biggest gap between "runs on
   samples" and "runs on a client".
2. **Image → audit pipeline.** A thin bridge from OCR output to the engine's
   document dataclasses, exposed as an entry point.
3. **REST API.** FastAPI app with auth + persistence, for integration.
4. **Vocabulary curation tool.** CLI to generate a client overlay from their real
   invoice descriptions.
5. **Real TMS write-back.** Map the generic `tms_json` payload to a specific TMS.
6. **Rule hardening.** More real-world detention/accessorial edge cases, each
   test-covered.

## Later — only once there are real customers

7. **Multi-tenant auth & data isolation.** Per-tenant keys and tenant-scoped
   storage. Premature before multiple paying customers exist; don't build it early.

## Principles

- Don't build ahead of demand. The architecture is shaped so per-client work is
  configuration or a one-method swap; resist turning those into speculative
  platform features before a customer needs them.
- Keep money in integer cents, keep OCR decoupled and optional, keep
  vocabulary/rules/layouts as data, and keep real data out of the repo.
