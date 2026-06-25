# Backend services — preparation handoff

The front-end (`Freight Audit Web Pages.dc.html` + `deploy.config.js`) is finished and
contract-frozen. Each external is a variable that, when set, makes the app call a real
service. This doc is how to **build those services** so that filling `deploy.config.js`
actually works. Build to these contracts and the front-end needs zero changes.

Reuse what already exists in the `freight-audit` repo — do not rewrite the engine:
`match.py` (rules), `models.py` (`process_load`, money cents), `normalize.py`,
`profiles.py`, `exporters.py`, `store.py`, `security.py`, `extract.py` (provider seam).

---

## 0. The frozen contract (what the front-end calls)

The app's `loadEngine()` / seams expect these. Match them exactly.

```
GET  /findings                  -> MatchResult[]   (the audit queue)
POST /decisions                 {id, kind, at}     (approve/flag, append-only)
POST /export/quickbooks                            (push bills via QBO)
POST /ocr/confirm               {fields...}        (confirmed OCR -> audit)
POST /checkout                  {plan}             (Stripe session)
   + the client's own TMS endpoint (TMS_API_URL) receives the tms.json payload
```

`MatchResult` JSON (already produced by the in-browser port in `findings.json` — keep identical):

```json
{
  "load_id": "L-100482",
  "severity": "warn",
  "net_money_impact_cents": 110000,
  "auto_approvable": false,
  "findings": [{"type":"line_overcharge","severity":"warn","message":"…","money_impact_cents":18000}],
  "rate_con": { … }, "invoice": { … }, "pod": { … }
}
```

Money is **integer cents** everywhere (never floats) — use `to_cents` / `cents_to_str`.

---

## 1. Audit API  (variable: `API_BASE_URL`)  — build first

Wrap the existing engine in HTTP. Nothing about the rules changes.

- Stack: **FastAPI + uvicorn**, pydantic models mirroring `models.py`.
- `GET /findings`: load each bundle, call `process_load(rc, inv, pod, profile)`, serialize
  the `MatchResult` to the JSON above (add a `to_dict()` on the dataclass).
- `POST /loads`: accept a load bundle, run the engine, persist, return its `MatchResult`.
- CORS: allow the front-end origin. Serve over HTTPS.
- Exit gate: the site with `API_BASE_URL` set renders the **same** results it shows offline.

## 2. Persistence  (variable: `DATABASE_URL`)

- Promote `store.py` from the SQLite scaffold to a real schema:
  `loads`, `findings`, `decisions`, `audit_log` (append-only).
- Add migrations (Alembic). Point at managed Postgres.
- `POST /decisions` writes the decision + an `audit_log` row; `GET /findings` reads persisted state.
- Exit gate: a decision survives a different browser/session.

## 3. Auth + payments  (variables: `AUTH_MODE`, `STRIPE_KEY`, `EMAIL_API_KEY`)

- Start from `security.py`'s API-key gate; add sessions/JWT (or integrate Clerk/Auth0).
  Protect every endpoint above.
- `POST /checkout`: create a Stripe Checkout session; handle the webhook to mark the
  account paid. Configure products/prices in Stripe first.
- Wire the email provider for invites/resets.
- Exit gate: a stranger cannot read `/findings`.

## 4. Exporters as endpoints  (variables: `QBO_CLIENT_ID`, `TMS_API_URL`)

- The formats already exist in `exporters.py` (IIF, TMS JSON, exception CSV). Wrap them:
  - `POST /export/quickbooks`: run the IIF exporter, then push via a real **QuickBooks OAuth**
    flow (consent → token exchange → refresh). A client id alone is not enough.
  - TMS: map the `tms_json` payload to the client's API and POST to `TMS_API_URL`.
- Exit gate: an approved batch appears in QuickBooks / the TMS.

## 5. Real OCR  (variables: `OCR_PROVIDER`, `OCR_API_KEY`, `UPLOADS_BUCKET`)

- Implement one `ExtractionProvider` in `extract.py` (Veryfi or Textract) behind the seam —
  the parser, validator, and tests do not change.
- Upload endpoint stores the image in object storage, runs the provider, returns fields +
  document confidence; `POST /ocr/confirm` persists corrections and triggers the audit.
- Exit gate: a photographed invoice audits end-to-end above the client's accuracy bar.

## 6. Scale  (variables: `QUEUE_URL`, `SSO_METADATA_URL`, `MONITORING_DSN`)

Only once there are paying customers: a job queue for batch processing, enterprise SSO,
error monitoring, multi-tenant data isolation. Do not pull forward.

---

## Cross-cutting prep

- **Containerize** (Dockerfile) and deploy to Render / Fly / a VM; one service, then split if needed.
- The API reads its **own** env config (same variable names) for `DATABASE_URL`, OCR keys, Stripe, etc.
- Keep the repo's **37 tests** green; add API + integration tests.
- Build order: **1 → 2 → 3**, then **4 and 5** in parallel, then **6**.
- Golden rule: never change the contract in section 0. The front-end is done; the server conforms to it.
