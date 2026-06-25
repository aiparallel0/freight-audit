# freight-audit — Project Report

> A complete reference for **freight-audit**: the business idea, the product, the
> codebase, and what it takes to turn it into a real company.
>
> **Status:** the engine + SaaS platform are built and tested (**129 tests** — 126 on
> SQLite + 3 against Postgres). Going live needs only external credentials/accounts;
> the remaining work is the *business* (customers, entity, contracts, hosting).

---

## Contents
1. [Executive summary](#1-executive-summary)
2. [The business idea](#2-the-business-idea)
3. [How the product works](#3-how-the-product-works)
4. [Technical architecture](#4-technical-architecture)
5. [API surface](#5-api-surface)
6. [Command-line tools](#6-command-line-tools)
7. [Configuration & secrets](#7-configuration--secrets)
8. [Testing](#8-testing)
9. [Running & deploying it](#9-running--deploying-it)
10. [Status, scoreboard & what's blocked](#10-status-scoreboard--whats-blocked)
11. [Roadmap to a real business](#11-roadmap-to-a-real-business)
12. [Repository map](#12-repository-map)
13. [Conventions & guardrails](#13-conventions--guardrails)

---

## 1. Executive summary

**freight-audit** audits freight **carrier invoices** against the **rate
confirmation** (what the broker agreed to pay) and the **proof of delivery** (POD,
the evidence the work happened) — and flags the money problems *before* payment.

It catches money in **two directions**, which is the core insight most tools miss:

- **Broker overpays** → invoice exceeds the agreement, unauthorized or over-cap
  accessorials, duplicate charges, detention billed but the POD can't substantiate
  it. *(Classic freight audit — "we save you money.")*
- **Carrier underbills** → the POD *proves* a long wait the carrier never billed as
  detention. This is **uncollected revenue** — industry framing puts roughly half of
  owed detention as never collected. *(Revenue recovery — "we recover money you're
  already owed," which is far easier to sell.)*

The codebase is a correct, tested core delivered as: a Python package, a CLI, a real
OCR pipeline (Tesseract + AWS Textract), an **image→audit** bridge, and a
**multi-tenant FastAPI SaaS** with a marketing site, self-serve demo, signup, an
API-served review console, usage metering + Stripe billing, Prometheus metrics +
alerting, and a config-switchable **SQLite⇄Postgres** backend.

**Money is always integer cents.** Nothing here uses float dollars in engine logic
or storage.

---

## 2. The business idea

### 2.1 The problem
Freight brokers and 3PLs pay thousands of carrier invoices a month. A meaningful
share contain errors — wrong linehaul, fuel mis-applied, accessorials that were
never authorized, the same charge billed twice, detention with no evidence. At the
same time, carriers routinely *under*-bill detention they're owed because chasing it
is administrative friction. Manual audit by an AP clerk doesn't scale and misses the
underbilled (recovery) side entirely. The documents arrive as PDFs, scans, EDI, and
email attachments in inconsistent formats.

### 2.2 The product / value proposition
- **Catch overbilling** — total mismatches, unauthorized/over-cap accessorials,
  duplicates, unprovable detention; each flagged with the **exact dollar impact**.
- **Recover detention** — surface the recoverable revenue the POD proves but the
  carrier didn't bill. *(The differentiated wedge.)*
- **Auto-approve the clean ones** — loads that match are marked safe to pay; only
  exceptions reach a human, with a worklist and an append-only audit trail.

### 2.3 Who it's for (ICP)
**Mid-size freight brokers / 3PLs, ~50–500 loads/week.** Enough invoice volume to
have real leakage, but too small to staff a dedicated audit team or buy an
enterprise freight-audit-and-payment (FAP) platform.

### 2.4 Pricing
Recommended: **contingency — keep 20–30% of money recovered or avoided**
("we only get paid when we save you money"), optionally plus a small per-audit fee.
Contingency removes buyer risk and aligns incentives; the per-audit fee (already
implemented in `billing.Pricing`) covers baseline cost. Both are configurable:
`bill_cents = base_fee_cents · audits + ⌊pct_savings · value_cents⌋`.

### 2.5 Market & competition
Established FAP vendors (Cass, Trax, AFS, nVision, Intelligent Audit) are strong in
**enterprise parcel/LTL**. The opening here is **SMB truckload brokers**, a
**detention-recovery** angle (revenue, not just savings), modern **self-serve**
onboarding, and a **data-driven** config model (per-client vocab/rules/layouts as
JSON, not bespoke engineering).

### 2.6 Go-to-market
Founder-led, design-partner-first: land one friendly broker, prove value on their
real documents, convert to a paid contract, then grow by referral. The built
**marketing site + `/demo`** double as a top-of-funnel once there's traction.

### 2.7 Unit economics (illustrative)
A broker doing 1,000 loads/month with ~3–5% billing leakage and unbilled detention
might surface a few thousand dollars/month of recoverable+avoided spend; at a 25%
take that is recurring revenue per account at near-software gross margins. Variable
cost is dominated by **cloud OCR per page** (AWS Textract) plus hosting; the engine,
storage, and review add negligible marginal cost. *(Numbers are illustrative — the
pilot exists to measure the real recovery rate and false-positive rate.)*

### 2.8 Risks (attack in this order)
1. **Generalization** — does the engine find real money on messy real docs with a
   low false-positive rate? (The CORD real-data test showed a generic OCR layout
   gets ~8% field extraction → per-format layout tuning is the real work. The
   architecture is built for exactly that, but it must be proven per client.)
2. **Trust** — will brokers hand financial documents to a new vendor? (Mitigate with
   a DPA, a clear data-handling posture, and a warm referral.)
3. **ROI** — is the recovered dollar amount big enough to justify the fee?
   (Contingency pricing sidesteps the objection; the pilot quantifies it.)

---

## 3. How the product works

```
photo/PDF ─▶ OCR ─▶ noisy text ─▶ layout-driven field mapping ─▶ structured docs
                                                                       │
rate confirmation ┐                                                    ▼
carrier invoice   ├─▶ normalize accessorials ─▶ matching rules ─▶ findings + $ impact
proof of delivery ┘        (data-driven vocab)   (+ client profile)        │
                                                                           ▼
                       review / auto-approve ─▶ write-back (TMS/QuickBooks) + ROI report ─▶ bill
```

Each **load** has up to three source documents. They are extracted into clean
dataclasses, charge descriptions are normalized to canonical accessorial categories,
the engine compares them, and it emits `Finding`s with a severity and a money impact.
Clean loads are auto-approvable; exceptions go to the review console; results are
exported/written back and rolled up into a savings/ROI report; usage is metered for
billing.

---

## 4. Technical architecture

Layered: **Browser pages → FastAPI app → core engine → data/ops**, with a secret
provider underneath everything. See `docs/` and the architecture diagram.

### 4.1 Core data model — `models.py`
- **Money is integer cents.** `to_cents()` / `cents_to_str()` are the only money I/O.
- Dataclasses: `LineItem`, `RateConfirmation`, `CarrierInvoice`, `ProofOfDelivery`,
  `Finding`, `MatchResult`. Enums: `Severity` (ok/info/warn/block), `FindingType`.
- `MatchResult` exposes `severity`, `net_money_impact_cents`, `auto_approvable`.

### 4.2 Matching engine — `match.py`
`MatchEngine.match(rate_con, invoice, pod) -> MatchResult` runs the rule set with a
tunable `EngineConfig`. Rules: total mismatch, line overcharge, unauthorized
accessorial, accessorial over-cap, duplicate line, **detention both directions**
(unprovable → block; underbilled → recoverable revenue), missing-doc/POD checks.
Integrity guards: load-ID mismatch, inverted/implausible POD timestamps, capped
detention, partial (date-only) POD timestamps, multiple detention lines, zero-amount
accessorials, flat-fee detention.

### 4.3 Extraction & OCR
- **`extract.py`** — provider seam. `JsonFixtureProvider` (offline JSON),
  **`TextractProvider`** (real AWS Textract: AnalyzeExpense + AnalyzeDocument
  QUERIES), `GoogleDocAIProvider`/`VeryfiProvider` (stubs).
- **`ocr/`** — real OCR pipeline: `preprocess.py` (OpenCV), `extract.py`
  (Tesseract + noise-tolerant parser), `layouts.py` (config-driven `LayoutProfile`s,
  incl. `freight_invoice`), `validate.py` (self-consistency → CONFIDENT/NEEDS-REVIEW).
- **`pipeline.py`** — `audit_documents(rate_con_img, invoice_img, pod_img)` OCRs
  images → maps fields → runs the engine (the image→audit bridge).
- **`synth/`** — PII-free synthetic freight-document generator (render invoice/
  rate-con/POD + integer-cents ground truth), a scorer, and an OCR accuracy
  benchmark; `hardscan.py` degrades images to test hard scans.

### 4.4 Normalization & client config
- **`normalize.py` + `vocab_loader.py`** — data-driven accessorial vocabulary with
  per-client overlays and a `suggest_unmatched()` learning loop.
- **`profiles.py`** — per-client business rules as JSON (fuel formula, caps,
  tolerances, disallowed accessorials).
- **`client_layouts.py`** — per-tenant `LayoutProfile` loader.
- **`vocab_cli.py`** — `freight-audit-vocab suggest` builds a starter overlay from a
  client's real invoice descriptions.

### 4.5 Output & write-back
- **`exporters.py`** — CSV, exception CSV, QuickBooks IIF, generic TMS JSON, ROI
  report, behind a registry.
- **`reporting.py`** — savings/ROI aggregation from a live batch *or* persisted
  history; `Store.report()`.
- **`tms.py`** — live POST of the generic TMS payload (env creds).
- **`tms_writeback.py`** — `SchemaMapping` maps fields to a specific TMS's schema,
  then POSTs.

### 4.6 SaaS platform
- **`api.py`** — FastAPI app (`uvicorn freight_audit.api:app`): audit, upload,
  signup, keys, review, usage, billing webhook, metrics, health; metrics middleware.
- **`security.py`** — `KeyStore`: hashed keys, **tenants**, **roles**
  (admin/reviewer/api), **expiry**, **rotation**, `create_account(email)`.
- **`ratelimit.py`** — per-key fixed-window limiter → 429.
- **`store.py` / `storage.py`** — tenant-scoped persistence + audit log; `get_store()`
  picks **SQLite** (default) or **Postgres** (`DATABASE_URL`, via `PostgresStore`),
  with migrations.
- **`billing.py`** — usage metering rollup → `Pricing` → `charge()` (Stripe SDK when
  configured, else generic endpoint); `charge_tenant()`.
- **`metrics.py` + `notify.py`** — Prometheus `/metrics`; alert rules
  (`config/alerts.json`) dispatched via email/Slack/webhook (fake transport for
  tests); SLA in `docs/SLA.md`.
- **`secret_provider.py`** — every external credential is read via `get_secret()`
  (env/file/chain), never hardcoded.
- **`web/`** — landing, signup, demo, and review-console pages (real copy,
  responsive) + a committed synthetic sample invoice.

### 4.7 Intake & integrations
- **`intake.py`** — email (`.eml`) attachment parser, EDI 210 parser, TMS-pull
  adapter (fixtures/mocked; live needs creds).
- **`sso.py`** — OIDC seams + HS256 id_token verification (mock-IdP tested).

---

## 5. API surface

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/` `/signup` `/demo` `/app/review` | open | Browser pages (landing, signup, demo, review console) |
| POST | `/signup` | open | Create a tenant + admin API key (400 invalid, 409 duplicate) |
| POST | `/demo/audit` | trial | Run a demo audit (file→OCR→match, or sample bundle); trial-limited → 429 |
| POST | `/audit` | api | Audit a load-bundle JSON → findings + severity |
| POST | `/audit/upload` | api | Audit document images via the OCR pipeline |
| GET | `/loads/{id}` | api | Retrieve a persisted load (tenant-scoped) |
| GET | `/usage` | api | The tenant's billing record (metered usage → bill) |
| GET | `/review` · POST `/review/{id}` | reviewer | List items needing review; accept/correct/reject |
| POST | `/keys` | admin | Issue an additional key for the tenant |
| POST | `/keys/rotate` | any valid | Rotate the calling key (old revoked) |
| POST | `/billing/webhook` | signature | Stripe payment events (verified) |
| GET | `/metrics` | open | Prometheus metrics (per-tenant count/errors/latency) |
| GET | `/healthz` | open | Liveness + DB check + version |
| GET | `/docs` | open | FastAPI auto Swagger UI |

Auth is via the `X-API-Key` header; roles are enforced per endpoint; every key maps
to a tenant and all reads/writes are tenant-scoped.

---

## 6. Command-line tools

| Script | What it does |
|---|---|
| `freight-audit` | Audit load bundles; `--profile`, `--export`, `--report`, `--db`, `--import-decisions`, `--benchmark` |
| `freight-audit-ocr` | Run the OCR pipeline on a document image (`--layout`) |
| `freight-audit-pipeline` | OCR rate-con/invoice/POD images → audit (image→audit bridge) |
| `freight-audit-synth` | Generate synthetic PII-free freight docs + ground truth; `--benchmark` |
| `freight-audit-vocab` | `suggest` — build a client vocabulary overlay from real descriptions |

---

## 7. Configuration & secrets

All read through `secret_provider.get_secret()` (env by default).

| Variable | Used by |
|---|---|
| `FREIGHT_AUDIT_KEYS`, `FREIGHT_AUDIT_DB` | API key store path, SQLite path |
| `DATABASE_URL` | Switches storage to Postgres |
| `RATE_LIMIT_PER_MIN`, `DEMO_TRIAL_LIMIT` | Rate limiting, demo trial cap |
| `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET` | Stripe billing + webhook |
| `FREIGHT_AUDIT_BILL_BASE_CENTS`, `FREIGHT_AUDIT_BILL_PCT` | Pricing model |
| `FREIGHT_AUDIT_BILLING_URL` / `_TOKEN` | Generic payment endpoint (non-Stripe) |
| `FREIGHT_AUDIT_TMS_URL` / `_TOKEN`, `TMS_WRITEBACK_URL` / `_TOKEN` | TMS write-back |
| `TMS_PULL_URL` / `_TOKEN` | TMS-pull intake |
| `NOTIFY_TRANSPORT`, `SLACK_WEBHOOK_URL`, `NOTIFY_WEBHOOK_URL`, `SMTP_*` | Alerting |
| `OIDC_ISSUER`/`CLIENT_ID`/`CLIENT_SECRET`/`AUTHORIZE_URL`/`TOKEN_URL`/`REDIRECT_URI` | SSO |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` | AWS Textract |

No credential is ever hardcoded in source.

---

## 8. Testing

**129 tests** — 126 on SQLite + 3 against Postgres. Run with `python -m pytest`.

| Area | Test files |
|---|---|
| Engine & rules | `test_engine`, `test_extensibility` |
| Extraction / OCR | `test_textract`, `test_ocr`, `test_pipeline`, `test_synth`, `test_datasets`, `test_benchmark`, `test_hardscan` |
| Reporting / exporters | `test_reporting` |
| Persistence / multi-tenant | `test_store`, `test_multitenant`, `test_storage_postgres` |
| Auth / signup | `test_auth`, `test_signup` |
| Web / demo / review | `test_web`, `test_cli_review_loop` |
| Billing | `test_billing`, `test_billing_stripe` |
| Monitoring | `test_monitoring` |
| Write-back / vocab | `test_tms`, `test_vocab_cli` |
| Scaffolds (SSO/intake/layouts/TMS) | `test_scaffolds` |

The matching engine (#9) and reporting/ROI (#12) are at 100% and regression-guarded.

---

## 9. Running & deploying it

```bash
# install (core engine only needs rapidfuzz)
pip install -e ".[ocr,api,synth,dev]"      # + the system `tesseract` binary for OCR
python -m pytest -q                         # 129 tests

# the SaaS
uvicorn freight_audit.api:app --reload      # http://127.0.0.1:8000  (+ /docs, /demo)

# the CLI
freight-audit                               # audit bundled samples
freight-audit --report --benchmark tests/fixtures/synth_corpus

# Postgres backend
DATABASE_URL=postgresql://user:pw@host/db uvicorn freight_audit.api:app
```

**Extras:** `ocr`, `textract`, `synth`, `api`, `postgres`, `dev`.
**Deploy:** `Dockerfile` builds the API image (tesseract + extras + uvicorn);
`.github/workflows/ci.yml` runs the full suite on SQLite and the storage tests
against a Postgres service.

---

## 10. Status, scoreboard & what's blocked

### Readiness (15-stage funnel, contact → paying customer)

| # | Stage | Now |
|---|---|---|
| 1 | Lead gen / prospecting | 0% (out of scope — sales/CRM) |
| 2 | Marketing landing (code) | ~85% |
| 3 | Demo / trial | ~95% |
| 4 | Signup / account | ~90% |
| 5 | Tenant auth + hardening | ~90% (SSO live blocked) |
| 6 | Client config / layout tuning | ~95% (tuning values need docs) |
| 7 | Document intake | ~80% (live channels blocked) |
| 8 | OCR / extraction | ~92% (hard-scan corpus blocked) |
| 9 | Matching engine | 100% |
| 10 | Review loop (API) | 100% |
| 11 | Write-back | ~90% (specific TMS schema blocked) |
| 12 | Reporting / ROI | 100% |
| 13 | Billing | ~90% (live charge blocked) |
| 14 | Monitoring / alerts | ~90% |
| 15 | Deploy / CI / storage | ~85% (live host; docker-build env-blocked) |

### BLOCKED — each needs a human to supply one thing
- **Stripe live charge** — a Stripe **test secret key** (`sk_test_…`), then live keys after KYC.
- **`docker build` here** — registry egress to docker.io (blocked by this sandbox's proxy; CI runners can pull).
- **SSO** — a real **IdP tenant + metadata** (Okta/Azure AD).
- **Per-client layout tuning** — **real client documents**.
- **Live intake** — a **mailbox / EDI VAN / TMS** credential + endpoint.
- **Hard-scan accuracy** — a **corpus of real hard scans**.
- **Live TMS write-back** — the **specific TMS field schema + sandbox creds**.

### Out of scope (human action, not code)
Lead gen/CRM, content/SEO/campaigns, legal contracts, payment-processor account &
KYC, and paid infrastructure/host provisioning.

---

## 11. Roadmap to a real business

**Phase 0 — set up & flip the code live (~1–2 weeks):** pick the ICP, decide
contingency pricing, **deploy** the Dockerfile (host + managed Postgres + secrets +
domain/TLS), open Stripe (test→live), wire alerts to a real channel.

**Phase 1 — land ONE design partner & prove value on REAL docs (the milestone):**
outreach to 20–50 brokers; get one to send ~50 real rate-con/invoice/POD sets under
an NDA/DPA; tune a layout profile (#6) and wire their intake (#7); run the audit;
measure real $ found and false-positive rate; iterate.

**Phase 2 — convert to paying & operationalize:** turn the pilot into a contract
(first revenue), go live on Stripe, wire write-back to their TMS (#11), sign MSA +
DPA, get E&O/cyber insurance, set retention/backups.

**Phase 3 — make it repeatable & scale:** referrals + outreach to 3–5 customers,
self-serve `/demo` funnel, SSO when an enterprise asks (#5), SOC 2 when required,
first hire.

**The single next action:** get one design partner to send 50 real document sets.
The built pipeline (tune layout → intake → audit → findings report) runs end-to-end
on them the same day.

---

## 12. Repository map

```
src/freight_audit/
  __init__.py        public API + process_load()
  models.py          dataclasses; MONEY = INTEGER CENTS
  match.py           MatchEngine: audit rules + integrity guards
  normalize.py       free-text charge -> canonical accessorial
  vocab_loader.py    data-driven vocabulary + client overlays
  vocab_cli.py       freight-audit-vocab (overlay curation)
  profiles.py        per-client business rules (JSON)
  client_layouts.py  per-tenant OCR layout loader
  extract.py         provider seam (JsonFixture, Textract, ...)
  pipeline.py        image -> audit bridge
  exporters.py       CSV / QuickBooks IIF / TMS JSON / ROI
  reporting.py       savings / ROI aggregation
  tms.py             live TMS POST (generic)
  tms_writeback.py   schema-mapped TMS write-back
  store.py           SQLite persistence + audit log (tenant-scoped)
  storage.py         get_store(): SQLite | Postgres factory + PostgresStore
  security.py        KeyStore: tenants, roles, expiry, rotation, accounts
  ratelimit.py       per-key rate limiter
  secret_provider.py credential interface (env/file/chain)
  billing.py         metering -> pricing -> Stripe/generic charge
  metrics.py         in-process metrics -> Prometheus
  notify.py          alert evaluation + email/Slack/webhook dispatch
  api.py             FastAPI app (uvicorn freight_audit.api:app)
  cli.py             freight-audit console script
  intake.py          email / EDI 210 / TMS-pull parsers
  sso.py             OIDC seams + id_token verification
  hardscan.py        degrade + OCR accuracy harness
  ocr/               preprocess, Tesseract extract, layouts, validate, cli
  synth/             synthetic doc generator + scorer + benchmark + cli
  web/               landing / signup / demo / review pages + static sample
  vocab/*.json       shipped accessorial vocabulary (+ example overlay)
  review_ui/index.html   static review console (file-based)
config/alerts.json   alert rules
docs/                STATUS, ROADMAP, PROMPTS, BENCHMARKS, SLA, REPORT (this file)
samples/             synthetic load bundles + example client profile
tests/               pytest suite (129 tests)
Dockerfile, .github/workflows/ci.yml   deploy + CI
```

---

## 13. Conventions & guardrails

- **Money is integer cents, everywhere.** Use `to_cents()` / `cents_to_str()`.
- **The engine is decoupled from OCR.** Extraction goes through `ExtractionProvider`;
  OCR deps are optional and import-guarded.
- **Vocabulary, client rules, and layouts are data, not code** (JSON overlays /
  profiles / layout profiles).
- **Credentials come from `secret_provider` (env) only** — never hardcoded.
- **No real or PII data in the repo.** All samples are synthetic; real client
  documents belong in a git-ignored `private/` folder.
- **Tests prove behavior.** `mock`/`fake` live only in test files; the matching
  engine and reporting are regression-guarded.

---

*Generated as the consolidated business + technical reference for freight-audit.*
