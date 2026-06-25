# Service Level Agreement (SLA)

This SLA covers the freight-audit hosted API (`freight_audit.api`).

## Availability
- **Target uptime:** 99.9% monthly (≈ 43 minutes of allowed downtime / month),
  measured against the `GET /healthz` endpoint (which checks database connectivity).
- Planned maintenance is announced ≥ 48 hours ahead and excluded from the
  availability calculation.

## Performance (SLO)
- **Latency:** 95th-percentile server processing time for `POST /audit` ≤ 1000 ms,
  excluding OCR on uploaded images (`POST /audit/upload`), which is best-effort.
- **Correctness:** money is computed in integer cents; the matching engine and
  reporting are regression-guarded by the test suite on every push.

## Alerting
Alert rules are checked in at [`config/alerts.json`](../config/alerts.json) and
evaluated against the `/metrics` snapshot:
- `high_error_rate` — server error rate > 5% → **page**.
- `latency_slo_breach` — average latency > 1000 ms → **warn**.
- `traffic_spike` — request count over threshold → **warn**.

Notifications are dispatched by `freight_audit.notify` through the configured
transport (`NOTIFY_TRANSPORT` = `slack` | `webhook` | `email`).

## Support response targets
| Severity | Definition | First response | Target resolution |
|---|---|---|---|
| **P1** | API down / data-affecting | 1 hour (24×7) | 4 hours |
| **P2** | Major feature degraded | 4 business hours | 2 business days |
| **P3** | Minor issue / question | 1 business day | best effort |

## Escalation
P1 incidents page the on-call engineer via the `high_error_rate` rule; if
unacknowledged for 15 minutes they escalate to the engineering lead. Post-incident
reviews are published within 5 business days for any P1.

## Data durability
All processed loads, decisions, and the append-only audit log are persisted
(SQLite for single-node; Postgres via `DATABASE_URL` for production), backed up per
the hosting environment's policy.
