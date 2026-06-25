# STATUS — what is and isn't ready

**Update:** the **entire technical roadmap (`docs/PROMPTS.md` #1–#7) is now
implemented and tested** — real OCR (Tesseract + AWS Textract), the image→audit
pipeline, a FastAPI REST API with multi-tenant auth + persistence, vocabulary
curation, TMS write-back, hardened detention/accessorial rules, plus a synthetic
document generator + OCR accuracy benchmark, a usage/billing scaffold, Docker, and
CI (97 tests). What remains is **not code**: lead generation, a marketing site, a
self-serve signup funnel, live payment processing, and production hosting/secrets —
each needs an external account or a business decision, not a commit. The original
assessment below is kept for history.

Straight answer to "is it 100% ready?": **No — and no honest version of this could
be, because the missing 20% is work that only exists once you have a real client.**
Here is exactly where the line is, so you're never surprised.

## ✅ Ready now (works, tested, correct) — 37 tests passing

- **The matching logic.** The rules in `match.py` cover the real money
  cases: total mismatch, line overcharge, unauthorized accessorial, over-cap
  accessorial, duplicate lines, unprovable detention, and the recoverable-detention
  (revenue-recovery) case.
- **Integrity guards**: correct money parsing; mismatched load IDs blocked;
  inverted/implausible POD timestamps blocked; over-long detention capped.
- **Data-driven accessorial vocabulary** (`vocab/`) with per-client overlays and a
  "learning loop" that suggests categories for unmatched descriptions.
- **Per-client business-rule profiles** (`samples/profiles/` + `profiles.py`): fuel formula validation,
  configurable tolerances, global accessorial caps, disallowed accessorials — all
  as JSON config, no code change per client.
- **Multiple real exporters** (`exporters.py`): QuickBooks IIF, generic TMS JSON,
  exception worklist CSV, flat CSV — behind a registry.
- **Real OCR on a synthetic receipt fixture** (the `ocr/` subpackage): preprocessing + Tesseract +
  noise-tolerant parser + self-validation + a CONFIDENT/NEEDS-REVIEW verdict.
- **Config-driven layout profiles** (`ocr/layouts.py`): a new document layout
  is a profile, not code — proven on two different layouts.
- **Document-level OCR confidence** scoring for routing to human review.
- **Minimum-viable hardening** (`store.py`, `security.py`): durable SQLite
  persistence, an append-only audit log (who approved what, when), and an API-key
  gate scaffold.
- **The CLI** (`freight-audit` CLI, with `--profile` and `--export`), the **review console**,
  and the **provider seam** (OCR fully decoupled) all work.

## 🔶 Still the pilot's job — but the *mechanisms* are now built

The remaining work genuinely needs a real client's documents. The difference from
before: each item is now **configuration or a one-method swap**, not engine surgery.

1. **Real OCR extraction** — DEMONSTRATED end-to-end in the `ocr/` subpackage on a synthetic receipt fixture. The freight cloud-API adapters (`extract.py`) are still stubs, but
   the working pattern exists; wiring Textract/Google/Veryfi is one method body.
2. **Field mapping for their layouts** — now CONFIG: `ocr/layouts.py` defines
   layout profiles; a new carrier's format is a profile, proven on two different
   layouts. Per-client tuning against real samples remains, but as data not code.
3. **Their accessorial vocabulary** — now DATA: `vocab/accessorials.json`
   plus per-client overlays; `suggest_unmatched()` helps you curate from their real
   invoices. Adding spellings is editing JSON.
4. **Their business rules** — now CONFIG: a `ClientProfile` JSON expresses fuel
   formula, tolerances, caps, and disallowed accessorials. Onboarding = a profile.
   (Genuinely novel rules can still need a new check, but the common ones are config.)
5. **Their output target** — now MULTIPLE real exporters (QuickBooks IIF, TMS JSON,
   exception CSV). Pushing to a *specific* live API is mapping these payloads to it.
6. **Accuracy on messy real docs** — preprocessing + confidence scoring + a
   self-validation gate are built; reaching a client's exact accuracy bar on their
   worst scans is still iteration against their samples (and likely a cloud OCR
   swap), but the routing-to-review machinery exists.
7. **Production hardening** — minimum-viable pieces now built (SQLite persistence,
   append-only audit log, API-key scaffold). Full multi-tenant auth, a web server,
   roles, queues, and scale remain — and remain premature before paying customers.

## The honest summary

This is a **correct, tested product seed and a complete go-to-market kit** — not a
deployed SaaS. That is the right thing to have at Stage 0–1: enough to demo and to
land a pilot, with the architecture already shaped so the pilot work drops in
cleanly. The 20% that's missing is the 20% you get *paid* to build, on real
documents, once someone has said yes.

If you want it closer to "client-deployable" before you've talked to anyone, the
highest-value next step is wiring one real OCR provider (Veryfi or Textract) into
`extract.py` and running it against 5–10 real freight PDFs. But per the plan, the
even-higher-value step is sending the outreach email first — because their
documents are what tells you which fields and rules actually matter.
