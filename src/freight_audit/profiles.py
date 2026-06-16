"""
Per-client business rules as CONFIG (improves "not ready" item #4).

Before: client-specific rules (fuel formulas, tolerances, accessorial caps) would
mean editing match.py per client. Now they live in a ClientProfile that can be
loaded from JSON, so onboarding a client is writing a profile, not surgery on the
engine. The engine reads the profile; the profile carries the client's policy.

What a profile can express today:
  - tolerance (how many cents of mismatch to ignore)
  - default detention rate + free time + max detention hours
  - a fuel-surcharge formula to validate the carrier's fuel charge against
    (percentage-of-linehaul, or per-mile), with a tolerance
  - global approved-accessorial caps (applied on top of per-load rate-con approvals)
  - which accessorial categories are simply not allowed for this client
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .match import EngineConfig
from .models import (
    CarrierInvoice, Finding, FindingType, RateConfirmation, Severity,
    cents_to_str, to_cents,
)


@dataclass
class FuelRule:
    """Validate the carrier's fuel charge against an agreed formula."""
    kind: str = "none"                 # "pct_of_linehaul" | "per_mile" | "none"
    pct: float = 0.0                   # e.g. 0.25 for 25% of linehaul
    per_mile_cents: int = 0            # e.g. 5500 for $0.55/mi (rate con must carry miles)
    tolerance_cents: int = 200

    def expected_cents(self, linehaul_cents: int, miles: Optional[float]) -> Optional[int]:
        if self.kind == "pct_of_linehaul":
            return int(round(linehaul_cents * self.pct))
        if self.kind == "per_mile" and miles:
            return int(round(miles * self.per_mile_cents))
        return None


@dataclass
class ClientProfile:
    name: str = "default"
    tolerance_cents: int = 100
    default_detention_rate_cents: int = 7500
    free_time_hours: float = 2.0
    max_detention_hours: float = 8.0
    fuel_rule: FuelRule = field(default_factory=FuelRule)
    global_accessorial_caps: dict[str, int] = field(default_factory=dict)  # category -> max cents
    disallowed_accessorials: list[str] = field(default_factory=list)
    vocab_overlay_path: Optional[str] = None

    @classmethod
    def load(cls, path: str) -> "ClientProfile":
        with open(path) as fh:
            d = json.load(fh)
        fr = d.get("fuel_rule", {})
        return cls(
            name=d.get("name", "client"),
            tolerance_cents=to_cents(d["tolerance"]) if "tolerance" in d else 100,
            default_detention_rate_cents=to_cents(d.get("default_detention_rate", 75.0)),
            free_time_hours=float(d.get("free_time_hours", 2.0)),
            max_detention_hours=float(d.get("max_detention_hours", 8.0)),
            fuel_rule=FuelRule(
                kind=fr.get("kind", "none"),
                pct=float(fr.get("pct", 0.0)),
                per_mile_cents=to_cents(fr["per_mile"]) if "per_mile" in fr else 0,
                tolerance_cents=to_cents(fr.get("tolerance", 2.0)),
            ),
            global_accessorial_caps={k: to_cents(v)
                                     for k, v in d.get("global_accessorial_caps", {}).items()},
            disallowed_accessorials=[s.lower() for s in d.get("disallowed_accessorials", [])],
            vocab_overlay_path=d.get("vocab_overlay_path"),
        )

    def to_engine_config(self) -> EngineConfig:
        return EngineConfig(
            total_tolerance_cents=self.tolerance_cents,
            default_detention_rate_cents=self.default_detention_rate_cents,
            max_detention_hours=self.max_detention_hours,
        )


def apply_profile_rules(profile: ClientProfile,
                        rc: Optional[RateConfirmation],
                        inv: CarrierInvoice,
                        miles: Optional[float] = None) -> list[Finding]:
    """Run the profile-specific checks and return extra findings. Designed to be
    appended to a MatchResult from the core engine."""
    findings: list[Finding] = []

    # bucket invoice charges by category (categories already normalized by the engine)
    by_cat: dict[str, int] = {}
    for li in inv.line_items:
        by_cat[li.category] = by_cat.get(li.category, 0) + li.amount_cents

    # 1) disallowed accessorials for this client
    for cat in profile.disallowed_accessorials:
        if by_cat.get(cat, 0) > 0:
            findings.append(Finding(
                FindingType.UNAUTHORIZED_ACCESSORIAL, Severity.WARN,
                f"[{profile.name}] '{cat}' is disallowed under this client's policy "
                f"but was billed {cents_to_str(by_cat[cat])}.",
                money_impact_cents=by_cat[cat]))

    # 2) global accessorial caps (on top of per-load rate-con approvals)
    for cat, cap in profile.global_accessorial_caps.items():
        billed = by_cat.get(cat, 0)
        if billed > cap + profile.tolerance_cents:
            findings.append(Finding(
                FindingType.ACCESSORIAL_OVER_CAP, Severity.WARN,
                f"[{profile.name}] {cat} billed {cents_to_str(billed)} exceeds the "
                f"client's global cap {cents_to_str(cap)} (+{cents_to_str(billed - cap)}).",
                money_impact_cents=billed - cap))

    # 3) fuel formula validation
    if profile.fuel_rule.kind != "none":
        linehaul = by_cat.get("linehaul", 0)
        billed_fuel = by_cat.get("fuel", 0)
        expected = profile.fuel_rule.expected_cents(linehaul, miles)
        if expected is not None and billed_fuel > 0:
            diff = billed_fuel - expected
            if abs(diff) > profile.fuel_rule.tolerance_cents:
                sev = Severity.WARN if diff > 0 else Severity.INFO
                findings.append(Finding(
                    FindingType.LINE_OVERCHARGE, sev,
                    f"[{profile.name}] fuel billed {cents_to_str(billed_fuel)} vs formula "
                    f"expectation {cents_to_str(expected)} "
                    f"({'over' if diff>0 else 'under'} by {cents_to_str(abs(diff))}).",
                    money_impact_cents=max(0, diff)))

    return findings
