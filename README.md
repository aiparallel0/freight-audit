# freight-audit — Intelligent invoice verification for shipping

Catch billing mistakes before you pay. `freight-audit` is an automated auditing system that checks freight carrier invoices against your shipping records to find overcharges, hidden fees, billing errors, and missed revenue opportunities.

## The problem it solves

Every time you ship a package, the carrier sends an invoice. But:
- **Carriers overcharge.** Wrong weight class, unauthorized fees, duplicate line items.
- **You don't notice.** Invoices are dense, fees are confusing, volume is high.
- **Money disappears.** Even small errors compound across thousands of shipments.
- **You miss revenue.** Sometimes the proof of delivery proves detention or damage you should bill for—but didn't.

## How it works

`freight-audit` takes three documents and finds the problems:

1. **Rate Confirmation** — your negotiated contract (what should you pay?)
2. **Proof of Delivery (POD)** — what actually happened (delivery location, delays, damage)
3. **Carrier Invoice** — what they billed (charges, fees, totals)

The engine compares all three, applies your business rules, and produces a structured report of:
- ✓ Valid charges (passed all checks)
- ✗ **Overcharges** (billed above contract rate)
- ⚠ **Unauthorized accessorials** (fees not in your agreement or outside negotiated caps)
- ⚠ **Duplicate charges** (same line billed twice)
- ⚠ **Detention mismatches** (billed detention the POD doesn't support, or unbilled detention the POD proves)
- 💰 **Uncollected revenue** (you should have charged for detention/damage but didn't)

Each finding includes the evidence, severity, and recommended action.

## Quick start

### Install

```bash
pip install -e ".[ocr,dev]"   # includes optional OCR pipeline + testing tools
```

### Run the audit engine

```bash
# Audit bundled sample shipments
freight-audit

# Audit your own loads (JSON format; see samples/)
freight-audit path/to/your/loads.json --json output.json
```

### Run the web console

```bash
cd review_ui && python -m http.server 8080
# Open http://localhost:8080/console.dc.html
```

The console works **completely offline**—no backend needed. It runs the audit engine in your browser on sample data, lets you explore findings, and shows you how the rules work.

### Run the backend (live auditing)

```bash
pip install fastapi uvicorn
uvicorn api.app:app --reload
# Console at http://localhost:8080 now connects to your live API on :8000
```

### Run tests

```bash
pytest   # 37 tests covering the matching engine and OCR pipeline
```

## What's inside

```
src/freight_audit/
  match.py           ← the audit engine: applies rules, finds discrepancies
  models.py          ← invoice, rate, POD data structures
  normalize.py       ← converts carrier fees to standard categories
  profiles.py        ← per-customer business rules (in JSON config)
  extract.py         ← reads documents; OCR pipeline is optional
  exporters.py       ← outputs to CSV, QuickBooks, or TMS systems
  
  ocr/               ← optional: turns invoice photos into structured data
    extract.py       ← runs Tesseract OCR + intelligent parsing
    validate.py      ← checks OCR output for confidence
    cli.py           ← `freight-audit-ocr IMG.jpg`

  review_ui/         ← static web console (offline-first)
    console.dc.html  ← the app
    engine.js        ← JS port of the audit engine (runs in browser)
    
api/
  app.py             ← FastAPI wrapper; connects UI to the engine

samples/
  loads/             ← 5 realistic sample shipments (synthetic)
  profiles/          ← example customer config
  
tests/               ← pytest suite (all 37 tests)
docs/
  STATUS.md          ← what's built, what's next
  PROMPTS.md         ← concrete tasks in priority order
  BACKEND.md         ← architecture for scaling to production
```

## Key design choices

- **Money is integer cents, never floats.** Prevents rounding errors in billing logic.
- **Engine is OCR-agnostic.** Invoices come from any source (file, photo, API). OCR is optional.
- **Rules are config, not code.** Customer-specific thresholds live in JSON `ClientProfile` files—no code changes needed per customer.
- **Vocabulary is data-driven.** Carrier spelling variations live in `vocab/*.json` files, not hardcoded lists.
- **New document layouts are profiles, not parser rewrites.** Add a `LayoutProfile` in `ocr/layouts.py`; the generic parser handles it.

## Architecture: offline → API → full backend

### Level 1: Offline console (works today)

Browser runs the ported engine locally. No server, no dependencies. Perfect for learning or light use.

```bash
cd review_ui && python -m http.server 8080
# Open console.dc.html → runs engine.js on sample data
```

### Level 2: Live backend (scaffold included)

FastAPI wrapper around the Python engine. Routes to `/findings`, `/queue`, `/export`. Connects the console to the real engine.

```bash
uvicorn api.app:app --reload
# Console now submits requests to http://localhost:8000
```

### Level 3: Full production (roadmap in BACKEND.md)

Adds database persistence, real OCR integration, QuickBooks/TMS connectors, Stripe billing, and user authentication. Phases 1–6 map to `# TODO` comments in `api/app.py`.

## For non-technical users

**You need this if you:**
- Ship dozens or more packages per month to carriers you pay.
- Want to catch billing errors before they cost you money.
- Receive invoices that are hard to verify against your agreements.
- Manage shipping costs and want to recover money that's owed to you.

**What you do:**
1. Upload or point to your invoice, rate contract, and delivery proof.
2. Configure your business rules once (maximum fee caps, allowed charges, detention policy).
3. Get a report of findings with recommended actions—or let it auto-reject overage.

**What freight-audit does:**
1. Reads the documents (PDF, image, or structured data).
2. Matches every charge against your contract and delivery proof.
3. Flags anything that doesn't match your rules.
4. Exports decisions to your accounting system or TMS.

## For developers

See `docs/PROMPTS.md` for concrete, prioritized tasks. Highest-impact first: wire a real cloud-OCR provider into `extract.py` and test on live documents.

## Privacy & safety

- **No real data in the repo.** All samples are synthetic.
- **Credentials are environment variables only.** Never commit API keys or client info.
- `.gitignore` excludes `private/`, `prospects/`, `clients/` directories.

## License

See LICENSE file.

---

**Ready to audit your first invoice?** Start with the offline console above, or jump to `docs/STATUS.md` to see what's ready to use.
