# BACKEND.md

Hands-on guide to the **freight-audit backend** — the FastAPI service, its data and
auth layers, and how to run, extend, operate, and deploy it. For the business
overview and full architecture see [`docs/REPORT.md`](docs/REPORT.md).

The backend is a single FastAPI app: **`freight_audit.api:app`**
(`uvicorn freight_audit.api:app`). It is multi-tenant, key-authenticated, persists
to SQLite or Postgres, meters usage for billing, and exposes Prometheus metrics.

---

## Contents
- [Quickstart](#quickstart)
- [Request lifecycle](#request-lifecycle)
- [Modules (backend-relevant)](#modules-backend-relevant)
- [Data & storage](#data--storage)
- [Auth & multi-tenancy](#auth--multi-tenancy)
- [Endpoints](#endpoints)
- [Billing & metering](#billing--metering)
- [Monitoring & alerting](#monitoring--alerting)
- [Secrets & configuration](#secrets--configuration)
- [Extending the backend](#extending-the-backend)
- [Testing](#testing)
- [Deployment](#deployment)
- [Operational runbook](#operational-runbook)
- [Security checklist](#security-checklist)

---

## Quickstart

```bash
# 1. install the backend extras (+ the system `tesseract` binary for OCR uploads)
pip install -e ".[api,ocr,synth,postgres,dev]"

# 2. run it
FREIGHT_AUDIT_KEYS=./keys.json FREIGHT_AUDIT_DB=./freight_audit.db \
  uvicorn freight_audit.api:app --reload
#  -> http://127.0.0.1:8000   (Swagger UI at /docs)

# 3. create a tenant + key, then call an endpoint
curl -s -XPOST localhost:8000/signup -H 'Content-Type: application/json' \
  -d '{"email":"ops@acme.co"}'                       # -> {tenant, api_key, role:"admin"}
KEY=...                                              # paste the api_key
curl -s -XPOST localhost:8000/audit -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d @samples/loads/load_002_overcharge_duplicate.json   # the bundle's rate_confirmation/invoice/pod
```

Run the backend test slice:
```bash
python -m pytest tests/test_api.py tests/test_auth.py tests/test_signup.py \
  tests/test_billing.py tests/test_billing_stripe.py tests/test_monitoring.py \
  tests/test_store.py tests/test_multitenant.py tests/test_web.py -q
```

---

## Request lifecycle

For every HTTP request:

1. **Metrics middleware** (`api._metrics_middleware`) starts a timer.
2. The route's **auth dependency** runs (`auditor` / `reviewer` / `admin_only` /
   `require_tenant`), which calls `_authenticate(x_api_key)`:
   - `KeyStore.tenant_for(key)` → tenant, or **401** if missing/invalid/expired/revoked;
   - `RateLimiter.allow(key)` → **429** if over the per-key limit;
   - role check → **403** if the key's role isn't permitted.
3. The handler runs, scoped to the tenant; persistence goes through
   `_store()` = `storage.get_store()`.
4. The middleware records `(tenant, path, latency_ms, status)` into `METRICS`.

Stores and key stores are created **per request** from config (cheap; SQLite opens a
file, Postgres a pooled connection) so the process holds no shared mutable state
beyond the in-memory rate limiter and metrics counters.

---

## Modules (backend-relevant)

| Module | Responsibility |
|---|---|
| `api.py` | FastAPI app, routes, auth dependencies, metrics middleware |
| `web/pages.py` | Browser pages + `/demo/audit`, `/review` (imported by `api` to register on `app`) |
| `security.py` | `KeyStore`: hashed keys, tenants, roles, expiry, rotation, `create_account` |
| `ratelimit.py` | `InMemoryRateLimiter` (per-key fixed window) |
| `store.py` | `Store`: SQLite persistence + audit log (tenant-scoped) + migrations |
| `storage.py` | `get_store()` factory + `PostgresStore` (psycopg) |
| `billing.py` | metering rollup → `Pricing` → `charge()` (Stripe/generic) + `charge_tenant()` |
| `metrics.py` | in-process counters → Prometheus text |
| `notify.py` | alert evaluation + email/Slack/webhook dispatch |
| `secret_provider.py` | `get_secret()` (env/file/chain) — all credentials |
| `pipeline.py` | `audit_documents()` — OCR images → engine (used by `/audit/upload`) |
| `__init__.py` | `process_load()` — bundle dicts → engine (used by `/audit`) |

---

## Data & storage

`storage.get_store()` returns the backend chosen by config:

- **SQLite** (default): `Store(FREIGHT_AUDIT_DB)` — single file, zero-config.
- **Postgres**: when `DATABASE_URL` is a `postgres(ql)://…` URL → `PostgresStore`.

Both expose the **same surface**, so handlers are backend-agnostic:

```
save_result(result, client, actor, tenant_id)   get_load(load_id, tenant_id)
all_loads(tenant_id)                             record_decision(load_id, decision, actor, note, tenant_id)
decisions_for(load_id, tenant_id)                record_usage(tenant_id, load_id, overpay_cents, recoverable_cents)
usage_rows(tenant_id)                            audit(...) / audit_trail(load_id, tenant_id)
summary(tenant_id)                               report(tenant_id)
```

Tables: `loads` (PK `(tenant_id, load_id)`), `decisions`, `audit_log`, `usage` — all
carry `tenant_id`. **Migrations** run on open: `Store._migrate()` upgrades a legacy
single-tenant SQLite schema in place (recreates `loads` with the composite key, adds
`tenant_id` columns); `PostgresStore` uses `CREATE TABLE IF NOT EXISTS`.

> **Money is integer cents** in every column (`net_impact_cents`, `overpay_cents`, …).

---

## Auth & multi-tenancy

`security.KeyStore` (JSON file at `FREIGHT_AUDIT_KEYS`) stores **hashed** keys; the
raw key is shown once. Each key record has `tenant_id`, `role`
(`admin`/`reviewer`/`api`), optional `expires_at`, and `email`.

- `issue(label, tenant_id, role, ttl_days, email)` → key
- `verify(key)` / `tenant_for(key)` / `role_for(key)` — honor active + expiry
- `rotate(key)` → new key (same tenant/role), revokes the old
- `create_account(email)` → `{tenant, api_key, role:"admin"}` (raises `InvalidEmail`
  / `DuplicateAccount`)

**Roles on endpoints** (`api._role_dep`): `auditor` = api|admin, `reviewer` =
reviewer|admin, `admin_only` = admin. **Rate limit**: per key, `RATE_LIMIT_PER_MIN`
(default 60) → **429**. **Tenant isolation**: every read/write is scoped by the key's
tenant — a key can never see another tenant's data (proved in `test_multitenant.py`).

---

## Endpoints

| Method · Path | Role | Notes |
|---|---|---|
| `POST /signup` | open | `{email}` → tenant + admin key; 400 invalid, 409 duplicate |
| `POST /audit` | api | bundle JSON → findings + severity; persisted + metered |
| `POST /audit/upload` | api | image files → OCR pipeline → audit |
| `GET /loads/{id}` | api | persisted load (tenant-scoped) |
| `GET /usage` | api | metered usage → bill (`billing.bill_tenant`) |
| `GET /review` · `POST /review/{id}` | reviewer | queue; accept/correct/reject → `record_decision` |
| `POST /keys` | admin | issue an extra key |
| `POST /keys/rotate` | any valid | rotate calling key |
| `POST /billing/webhook` | Stripe sig | verified payment events; 400 bad signature |
| `GET /metrics` | open | Prometheus text |
| `GET /healthz` | open | `{status, db, version}` |
| `GET /` `/signup` `/demo` `/app/review` `/docs` | open | pages + Swagger UI |
| `POST /demo/audit` | trial | no key; trial-limited → 429 |

---

## Billing & metering

On each `/audit` and `/audit/upload`, `api._record()` calls
`store.record_usage(tenant, load_id, overpay_cents, recoverable_cents)`.

```python
from freight_audit.billing import bill_tenant, charge_tenant, Pricing
bill = bill_tenant(store, tenant)          # {audits, value_cents, billing_cents, ...}
#  billing_cents = base_fee_cents·audits + ⌊pct_savings · value_cents⌋   (integer cents)
charge_tenant(store, tenant)               # meter -> price -> charge() (Stripe if configured)
```

`charge()` uses the **Stripe SDK** (PaymentIntent) when `STRIPE_API_KEY` is set, else
POSTs to a generic endpoint (`FREIGHT_AUDIT_BILLING_URL`). `POST /billing/webhook`
verifies events with `STRIPE_WEBHOOK_SECRET`. Pricing knobs:
`FREIGHT_AUDIT_BILL_BASE_CENTS`, `FREIGHT_AUDIT_BILL_PCT`.

---

## Monitoring & alerting

- **`GET /metrics`** → `freight_audit_requests_total`, `_errors_total`,
  `_request_latency_ms_avg`, labelled by `tenant` and `path`.
- **Logging** — `logging.getLogger("freight_audit.api")` logs each audit and webhook.
- **Alerts** — rules in `config/alerts.json`; `notify.evaluate_rules(snapshot, rules)`
  fires alerts; `notify.Notifier(transport).dispatch(alert)` sends via
  `NOTIFY_TRANSPORT` (`slack`/`webhook`/`email`, else a `FakeTransport`). SLA in
  [`docs/SLA.md`](docs/SLA.md).

To wire alerting to a metrics scrape loop, evaluate `METRICS.snapshot()` against
`load_alert_rules()` on a schedule and dispatch the result.

---

## Secrets & configuration

Read **only** through `secret_provider.get_secret(name, default)` (env by default;
`FileSecretProvider` / `ChainSecretProvider` available; swap with `set_provider()`).
Backend env checklist:

```
FREIGHT_AUDIT_KEYS, FREIGHT_AUDIT_DB        # key store + sqlite paths
DATABASE_URL                                 # -> Postgres backend
RATE_LIMIT_PER_MIN, DEMO_TRIAL_LIMIT         # limits
STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET        # billing
FREIGHT_AUDIT_BILL_BASE_CENTS, _BILL_PCT     # pricing
NOTIFY_TRANSPORT, SLACK_WEBHOOK_URL, SMTP_*  # alerting
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION   # Textract (uploads)
```

---

## Extending the backend

**Add an endpoint** — decorate on `app` in `api.py` (or a module imported by it),
using an auth dependency:
```python
@app.get("/loads")
def list_loads(tenant: str = Depends(auditor)):
    return {"loads": _store().all_loads(tenant)}
```

**Add an extraction provider** — subclass `ExtractionProvider` in `extract.py` and
register it:
```python
class MyProvider(ExtractionProvider):
    def extract_invoice(self, source): ...
PROVIDERS["mine"] = MyProvider
```

**Add an exporter** — add a `fn(results)->str` and register it:
```python
EXPORTERS["my_format"] = my_export; EXPORTER_EXT["my_format"] = ".txt"
```

**Add a storage backend** — implement the `Store` method surface (see
[Data & storage](#data--storage)) and branch in `storage.get_store()`.

**Onboard a TMS write-back** — supply a `SchemaMapping` field map
(`tms_writeback.SchemaMapping`) — config, not code.

**Tune OCR for a client** — drop `config/layouts/<tenant>.json` (a `LayoutProfile`);
`client_layouts.load_client_layout(tenant)` picks it up.

---

## Testing

```bash
python -m pytest -q                                   # full suite (SQLite) — 126 pass
DATABASE_URL=postgresql://fa:fa@localhost:5432/fa_test \
  python -m pytest tests/test_storage_postgres.py -q  # Postgres backend — 3 pass
```

Backend tests mock external systems (`stripe`, `urllib.request.urlopen`, the IdP) —
no live calls. FastAPI is exercised via `TestClient` (no running server). Each test
isolates state with a temp `FREIGHT_AUDIT_KEYS`/`FREIGHT_AUDIT_DB` and a fresh
`api._limiter`.

---

## Deployment

- **Docker** — `Dockerfile` installs `.[ocr,textract,synth,api,postgres]` + tesseract
  and runs `uvicorn freight_audit.api:app --host 0.0.0.0 --port 8000`; persists
  `/data`. Build/run:
  ```bash
  docker build -t freight-audit . && docker run -p 8000:8000 \
    -e DATABASE_URL=postgresql://... -e STRIPE_API_KEY=sk_... freight-audit
  ```
- **CI** — `.github/workflows/ci.yml`: a SQLite job runs the full suite; a Postgres
  job spins a `postgres:16` service, sets `DATABASE_URL`, and runs the storage tests.
- **Production switches** — set `DATABASE_URL` (managed Postgres), point the secret
  provider at the host's secret manager, set `STRIPE_API_KEY`, `NOTIFY_TRANSPORT`,
  and a real `RATE_LIMIT_PER_MIN`. Put a reverse proxy / TLS in front of uvicorn.

> Note: in restricted networks `docker build` may be blocked from pulling the base
> image; GitHub-hosted runners and normal hosts can pull it.

---

## Operational runbook

- **Health** — `GET /healthz` (checks DB connectivity + version); use it as the
  load-balancer probe and the `high_error_rate` alert source.
- **Metrics** — scrape `GET /metrics`; watch per-tenant `errors_total` and
  `latency_ms_avg` (SLO: avg `/audit` ≤ 1000 ms).
- **Scaling** — the app is stateless except the in-memory rate limiter and metrics;
  to run multiple replicas, move those to a shared backend (Redis) — the
  `RateLimiter` protocol exists for exactly that swap — and use Postgres (not SQLite).
- **Backups** — back up the Postgres database and the `FREIGHT_AUDIT_KEYS` store;
  the `audit_log` table is the append-only "who approved what, when" record.
- **Data retention** — set a retention policy per the DPA; loads/decisions/usage are
  tenant-scoped for per-customer export/delete.

---

## Security checklist

- [x] API keys stored **hashed** (sha256); raw key shown once.
- [x] Per-key **roles**, **expiry**, **rotation**; revoked/expired → 401.
- [x] Per-key **rate limiting** → 429.
- [x] **Tenant isolation** on every read/write (composite-key storage).
- [x] Credentials read via `secret_provider` only — **never hardcoded**.
- [x] Stripe webhook **signature verified**.
- [ ] **TLS** termination (reverse proxy / host) — deployment concern.
- [ ] **SSO** for enterprise (`sso.py` seam built; needs a real IdP).
- [ ] **SOC 2** / formal pen-test — when a customer requires it.

---

*See also: [`docs/REPORT.md`](docs/REPORT.md) (business + full architecture),
[`docs/SLA.md`](docs/SLA.md), [`docs/STATUS.md`](docs/STATUS.md).*
