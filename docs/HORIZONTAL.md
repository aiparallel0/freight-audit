# HORIZONTAL — applying the audit engine beyond freight

`freight-audit` looks like a trucking tool, but its core is sector-neutral. Strip
the freight vocabulary and what remains is a **three-document reconciliation
engine**:

```
AGREEMENT   (what was promised / priced)   ─┐
CLAIM       (what is being billed)         ─┼─►  MATCH  ─►  FINDINGS
EVIDENCE    (what was actually delivered)  ─┘
```

In freight those three documents are the **rate confirmation**, the **carrier
invoice**, and the **proof of delivery**. But that shape — *a priced agreement, a
claim against it, and independent proof of fulfilment* — recurs in almost every
sector that pays vendors against contracts. The engine already separates the
domain-specific parts from the reusable core along clean seams, so porting to a
new sector is mostly **configuration and data**, not a rewrite.

## Why it ports cleanly

The architecture was built so the sector-specific knowledge lives in swappable
layers, not in the matching logic:

| Layer (file) | What it holds | What changes per sector |
| --- | --- | --- |
| `match.py` | The audit rules: over-claim, unauthorized line, over-cap line, duplicate, unprovable claim, **under-billing / uncollected revenue** | Mostly reused — the rule *shapes* are universal |
| `models.py` | Agreement / Claim / Evidence dataclasses, **integer-cents money** | Rename fields; money handling is identical |
| `normalize.py` + `vocab/*.json` | Free-text line item → canonical category | New vocabulary file per sector (data, not code) |
| `profiles.py` | Per-client rules: tolerances, caps, disallowed items, formula checks | New profile JSON per client |
| `extract.py` / `ocr/` | Turn a document/photo into structured data | New layout profile or provider adapter |
| `exporters.py` | Push results to the system of record | New payload mapping per target system |

The two ideas that make the engine valuable in freight are exactly the ideas that
travel:

1. **Both directions of money.** Most tools only catch overbilling. This engine
   also catches **under-billing** — money you are owed but nobody claimed
   (in freight: uncollected detention). Every sector below has an equivalent
   "revenue you're already owed" case, and it is always the easier sell.
2. **Evidence-gated claims.** A charge is only allowed if the *evidence* document
   proves it. That single guard generalizes to any domain with a proof artifact.

## Candidate sectors

Each row is the same engine with a different vocabulary and profile.

| Sector | Agreement | Claim | Evidence | Signature finding |
| --- | --- | --- | --- | --- |
| **Healthcare claims** | Payer contract / fee schedule | Provider claim (837/UB-04) | Chart notes, EOB, auth | Upcoding, unbundling, non-covered code, **under-coded** covered work |
| **Utility & telecom** | Tariff / service contract | Monthly invoice | Meter reads, CDRs, usage logs | Rate misapplication, phantom lines, cap overrun, **un-credited SLA breach** |
| **Insurance / subrogation** | Policy + coverage limits | Repair/medical bill | Adjuster report, photos | Over-cap line, non-covered item, duplicate, **recoverable subrogation** |
| **Construction pay apps** | Schedule of values / contract | AIA G702/G703 pay app | Site photos, inspection sign-off | Over-billing % complete, unapproved change order, retention error |
| **Cloud / SaaS spend** | Committed-use / order form | Monthly usage bill | Metering / provisioning logs | Untagged spend, rate not honored, unused-but-billed, **missed commit credit** |
| **Legal e-billing** | Outside-counsel guidelines + rate card | LEDES invoice | Matter timekeeping records | Off-guidelines task, rate above card, block-billing, duplicate entry |
| **Media / royalties** | Licensing / distribution deal | Royalty statement | Play/sales/stream logs | Under-reported units, wrong rate tier, missing territory |
| **T&E / procurement** | Travel policy / PO + catalog | Expense report / vendor invoice | Receipts, GRN, delivery note | Out-of-policy item, over-cap, no-receipt, PO/receipt 3-way mismatch |

The classic **3-way match** (PO ↔ invoice ↔ goods-receipt) that every AP department
already understands is just the freight pattern with different labels — which makes
procurement the lowest-friction adjacent market.

## What a port actually takes

Because the seams already exist, standing up a new sector is a bounded checklist,
not an engine rewrite:

1. **Rename the three document models** in `models.py` to the sector's terms
   (agreement/claim/evidence). Keep integer-cents money exactly as is.
2. **Author a vocabulary file** (`vocab/<sector>.json`) mapping that sector's
   free-text line descriptions to canonical categories. Use the existing
   `suggest_unmatched()` learning loop to bootstrap it from real documents.
3. **Write a client profile** (JSON) for tolerances, caps, disallowed items, and
   any formula check (the freight "fuel formula" slot generalizes to any
   computed line).
4. **Add a layout profile** in `ocr/layouts.py` (or wire the relevant cloud
   extractor in `extract.py`) so the sector's documents parse.
5. **Map one exporter** payload to the sector's system of record.
6. **Add sector edge-case rules only if genuinely novel** — most map onto the
   existing over-claim / unauthorized / over-cap / duplicate / unprovable /
   under-billed finding types.

Steps 1–5 are configuration and data. Step 6 is the only place new code is
expected, and the finding taxonomy is designed to absorb most cases without it.

## What does *not* port for free

Honesty, same as `docs/STATUS.md`:

- **Regulatory surface.** Healthcare (HIPAA), insurance, and finance carry
  compliance obligations the freight core never had to model. That is real work,
  external to the matching logic.
- **Document supply.** Each sector needs its own real documents to tune vocabulary
  and layouts — the same "needs a real pilot" gap freight has.
- **Systems of record.** Each target (EHR, billing platform, ERP, e-billing hub)
  is a distinct integration, even though the exporter *seam* is reused.

None of these touch `match.py`. The audit brain is portable; the plumbing and the
compliance envelope are per-sector.

## Bottom line

The engine is a **general contract-vs-claim-vs-evidence auditor** that happens to
ship with a freight vocabulary. Every sector that pays vendors against a priced
agreement with a proof artifact is a candidate, and the two differentiators —
**catching under-billing**, and **gating every charge on evidence** — carry into
all of them. Horizontal expansion is a series of scoped configuration efforts, not
a new product each time.
