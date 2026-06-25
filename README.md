# freight-audit — UI + backend handoff

Everything built for the web console and the path to a live deployment. Drop these
into the `freight-audit` repo at the locations below.

## What's here

```
review_ui/                 → the audit console (static front-end)
  console.dc.html          the app (queue, load detail, OCR review, rules, admin,
                           build-status, deployment-config; funnel: landing → pricing
                           → register → app)
  support.js               front-end runtime (required by console.dc.html)
  engine.js                faithful JS port of match.py + normalize.py (runs offline)
  freight_samples.js       the repo's 5 sample loads, embedded for the in-browser run
  findings.json            engine output on those samples (what `freight-audit --json` emits)
  deploy.config.js         THE single switchboard: every external is one variable
api/
  app.py                   FastAPI scaffold wrapping process_load() in the UI's contract
BACKEND.md                 full plan to build the backend services (Phases 1–6)
```

## Where it goes in the repo

- `review_ui/*`  → replaces/extends `src/freight_audit/review_ui/` (the richer console).
- `api/app.py`   → new `api/` package at repo root.
- `BACKEND.md`   → repo root (alongside README.md / STATUS.md).

## Run the console (offline, 100% functional today)

It's static — serve the folder and open it:

```
cd review_ui && python -m http.server 8080   # then open http://localhost:8080/console.dc.html
```

With everything blank in `deploy.config.js`, it runs the **ported engine in-browser on
the repo's 5 real sample loads**. No backend needed.

## Connect it to the real engine (three levels, one variable each)

In `review_ui/deploy.config.js`:

1. **Real Python output (no server):** from the repo run
   `freight-audit samples/loads/*.json --json review_ui/findings.json`, then set
   `FINDINGS_URL: "findings.json"`. The UI now shows the actual CLI engine output.
2. **Live API:** run the backend (below) and set `API_BASE_URL: "http://localhost:8000"`.
   The queue, decisions, and exports hit the server.
3. Each remaining capability (DB, OCR, QuickBooks, TMS, Stripe, auth) switches on by
   setting its variable once the matching service exists — see BACKEND.md.

Precedence the UI follows: `API_BASE_URL` → `FINDINGS_URL` → in-browser ported engine.

## Run the backend scaffold

```
pip install -e ".[ocr,dev]" fastapi uvicorn
uvicorn api.app:app --reload     # serves GET /findings etc. on :8000
```

`api/app.py` wraps the existing `process_load()` and returns the exact `findings.json`
shape the front-end consumes — so pointing `API_BASE_URL` at it requires no UI change.
The `# TODO` markers map 1:1 to BACKEND.md phases (persistence, OCR, exporters, auth).

## The one rule

Never change the contract in BACKEND.md §0 (the routes + the `findings.json` shape).
The front-end is done and conforms to it; each backend service is built to match it.

## Note on engine.js

`engine.js` is a behaviour-faithful JS port of `match.py` + `normalize.py`, used so the
console works fully offline. It is validated against the five sample scenarios. Once the
API is live it becomes optional (the server runs the real Python); keep them in sync, or
delete `engine.js` and always read from the API/`findings.json`.
