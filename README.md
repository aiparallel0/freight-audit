# freight-audit

Audit freight **carrier invoices** against the **rate confirmation** and **proof
of delivery**, and catch the money problems before you pay:

- overcharges vs the agreed rate
- unauthorized or over-cap accessorials
- duplicate line items
- detention that's billed but the POD can't substantiate
- **the reverse** — detention the POD *proves* but the carrier never billed
  (revenue that's owed and silently left uncollected)

Plus an optional **OCR pipeline** that turns a document photo into structured,
self-validated data, so the audit can run on scanned paperwork rather than
hand-keyed JSON.

> Status: the full technical roadmap is implemented and tested (97 passing tests):
> real OCR (Tesseract + AWS Textract), an image→audit pipeline, a FastAPI REST API
> with multi-tenant auth + persistence, vocabulary curation, TMS write-back, a
> synthetic-document generator + OCR accuracy benchmark, a usage/billing scaffold,
> Docker, and CI. What's left is non-code (go-to-market and live payment
> processing). See [`docs/STATUS.md`](docs/STATUS.md).

---

## Install

```bash
# core engine only
pip install -e .

# with the OCR pipeline (also needs the system `tesseract` binary, v5+)
pip install -e ".[ocr]"

# for development (adds pytest)
pip install -e ".[ocr,dev]"
```

Python 3.10+. The only hard dependency is `rapidfuzz`; OCR pulls in
`pytesseract`, `opencv-python-headless`, `pillow`, and `numpy`.

On Debian/Ubuntu the Tesseract binary is: `sudo apt-get install tesseract-ocr`.

---

## Quick start

### Audit the bundled sample loads

```bash
freight-audit
```

You'll see it flag overcharges, an unauthorized accessorial, unprovable detention,
and a load where the POD proves a long wait but **no detention was billed**.

### Audit your own load bundles

A "load bundle" is a JSON file with optional `rate_confirmation`, `invoice`, and
`pod` keys (see [`samples/loads/`](samples/loads/) for the shape):

```bash
freight-audit path/to/load_*.json
freight-audit path/to/loads/ --export quickbooks_iif --export-out bills.iif
freight-audit path/to/loads/ --json results.json
```

### Apply a client's business rules

```bash
freight-audit samples/loads/*.json --profile samples/profiles/client_example.json
```

A profile expresses tolerances, a fuel-surcharge formula, accessorial caps, and
disallowed accessorials as JSON — onboarding a client is writing a profile, not
editing the engine.

### Run the OCR pipeline on a document image

```bash
# bring your own image — none is bundled
freight-audit-ocr path/to/receipt.jpg
freight-audit-ocr path/to/invoice.png --layout tabular_invoice --json out.json
```

---

## Use it as a library

```python
from freight_audit import process_load, MatchEngine, ClientProfile

# from JSON dicts (the default JsonFixtureProvider extracts them)
result = process_load(rate_con_dict, invoice_dict, pod_dict)
print(result.severity, result.net_money_impact_cents)
for f in result.findings:
    print(f.type.value, f.message, f.money_impact_cents)

# with a client profile
profile = ClientProfile.load("samples/profiles/client_example.json")
result = process_load(rc, inv, pod, profile=profile)
```

OCR:

```python
from freight_audit.ocr import extract_receipt, validate

receipt = extract_receipt("receipt.jpg")          # photo -> structured Receipt
result = validate(receipt)                          # self-consistency checks
print(result.confident, result.severity)            # CONFIDENT vs NEEDS-REVIEW
```

---

## Architecture

```
photo ─▶ OCR ─▶ noisy text ─▶ layout-driven field mapping ─▶ structured docs
                                                                    │
rate confirmation ┐                                                 ▼
carrier invoice   ├─▶ normalize accessorials ─▶ matching rules ─▶ findings
proof of delivery ┘        (data-driven vocab)   (+ client profile)   │
                                                                      ▼
                                          exporters: CSV · QuickBooks IIF · TMS JSON
```

| Module | Responsibility |
|---|---|
| `models.py` | data structures; money stored as **integer cents** (never floats) |
| `match.py` | the audit rules + integrity guards (load-ID mismatch, bad POD timing) |
| `normalize.py` + `vocab_loader.py` | map free-text charge descriptions to canonical accessorial categories (data-driven, client-extensible) |
| `profiles.py` | per-client business rules as JSON config (fuel formula, caps, tolerances) |
| `extract.py` | the OCR/extraction **provider seam** — offline JSON works now; Textract / Google Document AI / Veryfi are stubbed adapters |
| `exporters.py` | output adapters behind a registry |
| `store.py` | optional SQLite persistence + append-only audit log |
| `security.py` | optional API-key gate scaffold |
| `ocr/` | the OCR pipeline: preprocess → Tesseract → parse → validate → verdict |

The OCR step is isolated behind `ocr.extract.ocr_image`, so swapping Tesseract for
a cloud OCR API is one method body — the parser, validator, and tests don't change.

---

## Develop

```bash
pip install -e ".[ocr,dev]"
pytest                      # 97 tests
```

Working on this with **Claude Code**? Start with [`CLAUDE.md`](CLAUDE.md) (project
context) and [`docs/PROMPTS.md`](docs/PROMPTS.md) (ready-to-paste tasks for
extending it — wiring real OCR, adding a client profile, building an API, etc.).

---

## A note on the sample data

Everything bundled is **synthetic**. The sample loads are invented; the OCR test
fixture is a generated receipt image with made-up merchant and line items
([`tests/fixtures/make_sample_receipt.py`](tests/fixtures/make_sample_receipt.py)).
No real company, person, invoice, or contact information ships in this repo.

Real client documents and any prospect/contact data should live in a local,
git-ignored `private/` folder — never in the repo. See `.gitignore`.

---

## License

MIT — see [`LICENSE`](LICENSE).
