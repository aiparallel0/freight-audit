# CLAUDE.md

Project context for Claude Code. Read this first.

## What this project is

`freight-audit` is a Python package that audits freight **carrier invoices**
against the **rate confirmation** and **proof of delivery (POD)** to catch billing
problems before payment — overcharges, unauthorized/over-cap accessorials,
duplicate lines, unprovable detention — and the reverse case: detention the POD
proves but the carrier never billed (uncollected revenue). It also has an optional
OCR pipeline (`freight_audit.ocr`) that turns a document photo into structured,
self-validated data.

It is a **correct, tested seed**, not a finished SaaS. The matching engine and OCR
are real and covered by 37 tests. The remaining work is deliberately scoped as
configuration or single-method swaps (see `docs/STATUS.md`).

## Layout

```
src/freight_audit/
  __init__.py        # public API + process_load()
  models.py          # dataclasses; MONEY IS INTEGER CENTS (never float)
  match.py           # MatchEngine: the audit rules + integrity guards
  normalize.py       # free-text charge -> canonical accessorial category
  vocab_loader.py    # data-driven vocabulary (vocab/*.json) + client overlays
  profiles.py        # ClientProfile: per-client business rules as JSON
  extract.py         # provider seam: JsonFixtureProvider works; cloud OCR = stubs
  exporters.py       # CSV / QuickBooks IIF / TMS JSON behind a registry
  store.py           # optional SQLite persistence + append-only audit log
  security.py        # optional API-key gate scaffold
  cli.py             # `freight-audit` console script
  vocab/*.json       # shipped accessorial vocabulary (+ example client overlay)
  review_ui/index.html  # static human-in-the-loop review console
  ocr/
    preprocess.py    # OpenCV cleanup (optional dep)
    extract.py       # Tesseract OCR + noise-tolerant parser (the real provider)
    layouts.py       # config-driven layout profiles
    validate.py      # self-consistency checks -> CONFIDENT / NEEDS-REVIEW
    cli.py           # `freight-audit-ocr` console script
samples/loads/*.json     # synthetic load bundles
samples/profiles/*.json  # example client profile
tests/                   # pytest suite (37 tests)
tests/fixtures/make_sample_receipt.py  # generates the synthetic OCR fixture
docs/                    # STATUS, ROADMAP, PROMPTS
```

## Commands

```bash
pip install -e ".[ocr,dev]"   # install with OCR + pytest
pytest                        # run all 37 tests
freight-audit                 # audit bundled samples
freight-audit-ocr IMG.jpg     # run OCR on an image
```

## Conventions (follow these)

- **Money is integer cents, everywhere.** Use `to_cents()` / `cents_to_str()` from
  `models.py`. Never introduce float dollars into engine logic or storage.
- **The engine is decoupled from OCR.** Extraction goes through `ExtractionProvider`
  in `extract.py`. Don't call OCR from inside `match.py`.
- **Accessorial vocabulary is data, not code.** Add carrier spellings to
  `vocab/*.json` or a client overlay — don't hard-code new keyword lists in
  `normalize.py` (the dict there is only a fallback).
- **Client-specific rules are config, not code.** Express them in a `ClientProfile`
  JSON; only add a new `apply_profile_rules` branch for genuinely novel rule types.
- **New document layouts are profiles, not parser edits.** Add a `LayoutProfile` in
  `ocr/layouts.py`; the generic parser consumes it.
- **OCR deps are optional.** `cv2` / `pytesseract` must stay import-guarded so the
  core engine installs and runs without the `ocr` extra.
- **Tests are pytest, with `pythonpath = ["src"]`** set in `pyproject.toml`. Sample
  paths resolve to `samples/loads` and `samples/profiles`; the OCR fixture is
  generated into `tests/fixtures/`.
- **No personal or client data in the repo.** All samples are synthetic. Real
  documents/contacts belong in a git-ignored `private/` folder.

## Privacy / safety guardrails

- Do not commit real company names, contact details, prospect lists, or client
  documents. `.gitignore` already excludes `private/`, `prospects*`, `clients/`.
- The cloud-OCR adapters in `extract.py` are stubs by design; when implementing
  one, keep credentials out of source (env vars only).

## Where to start

`docs/PROMPTS.md` has concrete, ready-to-run tasks (each with acceptance criteria),
ordered by value. The highest-impact first task is wiring one real cloud-OCR
provider into `extract.py` and proving it on real documents.
