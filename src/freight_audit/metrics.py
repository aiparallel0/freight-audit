"""
In-process request metrics, exported in Prometheus text format.

Tracks per (tenant, path): request count, server-error count, and total latency, so
/metrics exposes per-tenant request rate, error rate, and average latency. A single
process-wide Metrics instance is used by the API middleware.
"""
from __future__ import annotations

import threading


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._series: dict[tuple[str, str], dict] = {}

    def record(self, tenant: str, path: str, latency_ms: float, status: int) -> None:
        with self._lock:
            d = self._series.setdefault((tenant, path),
                                        {"count": 0, "errors": 0, "latency_ms": 0.0})
            d["count"] += 1
            d["latency_ms"] += latency_ms
            if status >= 500:
                d["errors"] += 1

    def snapshot(self) -> list[dict]:
        with self._lock:
            out = []
            for (tenant, path), d in self._series.items():
                count = d["count"]
                out.append({
                    "tenant": tenant, "path": path, "count": count,
                    "errors": d["errors"],
                    "error_rate": (d["errors"] / count) if count else 0.0,
                    "latency_avg_ms": (d["latency_ms"] / count) if count else 0.0,
                })
            return out

    def reset(self) -> None:
        with self._lock:
            self._series.clear()

    def render_prometheus(self) -> str:
        lines = [
            "# HELP freight_audit_requests_total Total requests by tenant and path.",
            "# TYPE freight_audit_requests_total counter",
            "# HELP freight_audit_errors_total Server errors by tenant and path.",
            "# TYPE freight_audit_errors_total counter",
            "# HELP freight_audit_request_latency_ms_avg Average request latency (ms).",
            "# TYPE freight_audit_request_latency_ms_avg gauge",
        ]
        for s in self.snapshot():
            lab = f'tenant="{s["tenant"]}",path="{s["path"]}"'
            lines.append(f'freight_audit_requests_total{{{lab}}} {s["count"]}')
            lines.append(f'freight_audit_errors_total{{{lab}}} {s["errors"]}')
            lines.append(f'freight_audit_request_latency_ms_avg{{{lab}}} {s["latency_avg_ms"]:.3f}')
        return "\n".join(lines) + "\n"


METRICS = Metrics()
