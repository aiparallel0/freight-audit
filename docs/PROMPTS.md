# Claude Code prompts

Ready-to-paste tasks for extending `freight-audit`, ordered by value. Each is
self-contained: paste it into a Claude Code session running in this repo. Each has
**acceptance criteria** so you (and Claude) know when it's done. Keep the
conventions in `CLAUDE.md` — money is integer cents, OCR stays decoupled and
optional, vocabulary/rules/layouts are data not code, and no real data in the repo.

Run `pytest` after every task; all existing tests must stay green.

---

## 1. Wire a real cloud-OCR provider (highest value)

> In `src/freight_audit/extract.py` there are stub adapters for cloud OCR
> (`TextractProvider`, `GoogleDocAIProvider`, `VeryfiProvider`) whose `_raw()`
> raises `NotImplementedError`. Implement **one** of them for real — pick AWS
> Textract. Read credentials from environment variables only (never hard-code).
> Map Textract's response into the same `RateConfirmation` / `CarrierInvoice` /
> `ProofOfDelivery` dataclasses the offline `JsonFixtureProvider` produces, so the
> rest of the engine is unchanged. Add an optional dependency extra
> `textract = ["boto3"]` in `pyproject.toml`. Add a unit test that mocks the boto3
> client (no live AWS calls) and asserts the mapping produces the right fields.
>
> Acceptance: `pip install -e ".[textract]"` works; a mocked test passes; the
> offline path and all 37 existing tests still pass; no credentials in source.

## 2. Add an end-to-end "image → audit" path

> Today OCR (`freight_audit.ocr`) and the audit engine are separate. Add a thin
> bridge so a set of document images can be run straight through to an audit
> result: OCR each image → map the extracted fields into the engine's document
> dataclasses → run `MatchEngine`. Put it in a new module
> `src/freight_audit/pipeline.py` with a function
> `audit_documents(rate_con_img, invoice_img, pod_img, profile=None)`. Keep OCR
> imports lazy so the core install still works without the `ocr` extra. Add a CLI
> subcommand or a new `freight-audit-pipeline` console script.
>
> Acceptance: running the new entry point on three images returns a `MatchResult`;
> a test using the synthetic fixture (regenerate via
> `tests/fixtures/make_sample_receipt.py`) passes; existing tests stay green.

## 3. Build a small REST API

> Add a FastAPI app in `src/freight_audit/api.py` exposing:
> `POST /audit` (accepts a load bundle JSON, returns findings + severity),
> `POST /audit/upload` (accepts document images, runs the OCR→audit pipeline),
> and `GET /healthz`. Gate write endpoints behind the existing API-key check in
> `security.py` (`KeyStore.verify`). Persist each processed load and decision via
> `store.py`. Add `api = ["fastapi", "uvicorn", "python-multipart"]` as an extra.
> Write tests with FastAPI's `TestClient` covering auth-required, a successful
> audit, and a persisted-then-retrieved load.
>
> Acceptance: `uvicorn freight_audit.api:app` serves the endpoints; tests pass;
> unauthenticated writes are rejected; audit results are persisted.

## 4. Curate a client's accessorial vocabulary from real invoices

> Add a CLI command `freight-audit-vocab suggest <invoices.json>` that loads a
> client's real invoice line descriptions, runs `Vocabulary.suggest_unmatched()`,
> and writes a starter overlay JSON (grouping descriptions under their best-guess
> category) for a human to review. This operationalizes onboarding a client's
> carrier spellings as data. Do not modify the shipped `vocab/accessorials.json`.
>
> Acceptance: given a JSON list of descriptions, the command emits a valid overlay
> file that `Vocabulary.load(client_overlay=...)` accepts; a test covers it.

## 5. Add a real TMS write-back adapter

> `exporters.py` produces a generic `tms_json` payload. Pick one real TMS with a
> public API (or stub a clearly-named generic REST target) and add an adapter that
> POSTs the audit results to it, mapping `tms_json` fields to that API's schema.
> Put it behind the exporter registry pattern and read the endpoint/token from env
> vars. Add a mocked test (no live calls).
>
> Acceptance: a mocked test shows the correct payload is POSTed; the generic
> `tms_json` exporter is unchanged; existing tests stay green.

## 6. Harden detention & accessorial rules with more real edge cases

> Strengthen `match.py` against messy real data. Add handling and tests for:
> partial POD timestamps (date but no time), multiple detention line items on one
> invoice, accessorials present on the invoice but with a zero/blank amount, and
> rate cons that express detention as a flat fee rather than per-hour. Keep money
> as integer cents and keep every new behavior covered by a test.
>
> Acceptance: new tests cover each case; no regression in the existing 37 tests;
> no float money introduced.

## 7. Multi-tenant auth (only once there are real users)

> Replace the single-tenant API-key scaffold in `security.py` with per-tenant keys:
> each key maps to a tenant id; `store.py` rows are scoped by tenant; the API
> filters all reads/writes by the authenticated tenant. Add migration logic for the
> existing single-tenant SQLite schema. Add tests proving tenant isolation (tenant
> A cannot read tenant B's loads).
>
> Acceptance: cross-tenant access is impossible in tests; existing data migrates;
> all tests pass. (Do this only when the product actually has multiple customers.)

---

## How to drive a task with Claude Code

1. Open this repo in Claude Code (it auto-reads `CLAUDE.md`).
2. Paste one task block above.
3. Let it propose a plan, then implement.
4. Run `pytest` — require all tests green before accepting.
5. Review the diff for the conventions in `CLAUDE.md` (integer-cents money, OCR
   decoupled and optional, data-driven vocab/rules/layouts, no secrets, no real
   data committed).
