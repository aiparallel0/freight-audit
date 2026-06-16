"""
Core data models for freight_audit.

Everything in the system flows through these dataclasses. The whole point of the
business is NOT the OCR (that's a commodity API) -- it's turning three messy
documents into these clean, comparable structures and then catching the money
discrepancies between them. So the models are the heart of the moat.

Three source documents per load:
  - RateConfirmation : what the broker AGREED to pay the carrier (the contract)
  - CarrierInvoice   : what the carrier is ACTUALLY billing (the claim)
  - ProofOfDelivery  : evidence the work happened + when (substantiates accessorials)

The MatchResult is what a human reviews and what ultimately gets pushed into the
TMS / accounting system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Money helper -- never use floats for money. Store integer cents.
# Convention: numeric inputs (int/float) and numeric strings are DOLLAR amounts.
#   to_cents(1234) -> 123400  ($1,234.00)
#   to_cents(12.50) -> 1250   ($12.50)
# If you ever need to pass cents directly, pass them already-converted.
# ---------------------------------------------------------------------------
def to_cents(value) -> int:
    """Convert a dollar value (str like '$1,234.56', float, or int) to integer cents.

    Numeric values are always interpreted as dollars, e.g. 1234 -> $1,234.00.
    Returns 0 for None / blank / unparseable input.
    """
    if value is None:
        return 0
    if isinstance(value, bool):  # guard: bool is a subclass of int
        return 0
    if isinstance(value, (int, float)):
        return int(round(value * 100))
    s = str(value).strip().replace("$", "").replace(",", "").replace(" ", "")
    if s == "":
        return 0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        cents = int(round(float(s) * 100))
    except ValueError:
        return 0
    return -cents if neg else cents


def cents_to_str(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


# ---------------------------------------------------------------------------
# Line items (accessorials etc.)
# ---------------------------------------------------------------------------
@dataclass
class LineItem:
    """A single charge line: 'Linehaul', 'Fuel Surcharge', 'Detention', 'Lumper', ..."""
    description: str
    amount_cents: int
    # normalized category, filled by the engine ('linehaul','fuel','detention','lumper',
    # 'liftgate','reweigh','layover','other')
    category: str = "other"
    quantity: Optional[float] = None      # e.g. detention hours
    rate_cents: Optional[int] = None      # e.g. $75.00/hr -> 7500

    def __repr__(self) -> str:
        q = f" [{self.quantity} @ {cents_to_str(self.rate_cents)}]" if self.rate_cents else ""
        return f"<{self.category}:{self.description} {cents_to_str(self.amount_cents)}{q}>"


# ---------------------------------------------------------------------------
# Source documents
# ---------------------------------------------------------------------------
@dataclass
class RateConfirmation:
    load_id: str
    broker_name: str
    carrier_name: str
    origin: str
    destination: str
    agreed_total_cents: int
    line_items: list[LineItem] = field(default_factory=list)
    # accessorials the broker pre-approved on the rate con (category -> max cents, or None=unlimited)
    approved_accessorials: dict[str, Optional[int]] = field(default_factory=dict)
    pickup_date: Optional[datetime] = None
    free_time_hours: float = 2.0          # standard free window before detention applies
    raw_source: str = ""                  # path / id of the source doc

    @property
    def line_total_cents(self) -> int:
        return sum(li.amount_cents for li in self.line_items)


@dataclass
class CarrierInvoice:
    invoice_number: str
    load_id: str
    carrier_name: str
    billed_total_cents: int
    line_items: list[LineItem] = field(default_factory=list)
    invoice_date: Optional[datetime] = None
    raw_source: str = ""

    @property
    def line_total_cents(self) -> int:
        return sum(li.amount_cents for li in self.line_items)


@dataclass
class ProofOfDelivery:
    load_id: str
    delivered: bool
    arrival_time: Optional[datetime] = None
    departure_time: Optional[datetime] = None
    signed_by: Optional[str] = None
    raw_source: str = ""

    @property
    def time_on_site_hours(self) -> Optional[float]:
        if self.arrival_time and self.departure_time:
            return (self.departure_time - self.arrival_time).total_seconds() / 3600.0
        return None


# ---------------------------------------------------------------------------
# Findings / output
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"     # do not auto-pay; human must look


class FindingType(str, Enum):
    TOTAL_MISMATCH = "total_mismatch"
    LINE_OVERCHARGE = "line_overcharge"
    UNAUTHORIZED_ACCESSORIAL = "unauthorized_accessorial"
    DUPLICATE_LINE = "duplicate_line"
    ACCESSORIAL_OVER_CAP = "accessorial_over_cap"
    DETENTION_UNSUPPORTED = "detention_unsupported"
    DETENTION_UNDERBILLED = "detention_underbilled"   # carrier owed MORE than billed
    MISSING_POD = "missing_pod"
    MISSING_DOC = "missing_doc"
    LOAD_ID_MISMATCH = "load_id_mismatch"             # docs reference different loads
    BAD_POD_DATA = "bad_pod_data"                     # timestamps inverted / implausible
    OK = "ok"


@dataclass
class Finding:
    type: FindingType
    severity: Severity
    message: str
    money_impact_cents: int = 0   # +ve = carrier owes broker / broker overpays; -ve = carrier underbilled (revenue they're losing)

    def __repr__(self) -> str:
        return f"[{self.severity.value.upper()}] {self.type.value}: {self.message} ({cents_to_str(self.money_impact_cents)})"


@dataclass
class MatchResult:
    load_id: str
    findings: list[Finding] = field(default_factory=list)
    rate_con: Optional[RateConfirmation] = None
    invoice: Optional[CarrierInvoice] = None
    pod: Optional[ProofOfDelivery] = None

    @property
    def severity(self) -> Severity:
        order = [Severity.OK, Severity.INFO, Severity.WARN, Severity.BLOCK]
        worst = Severity.OK
        for f in self.findings:
            if order.index(f.severity) > order.index(worst):
                worst = f.severity
        return worst

    @property
    def net_money_impact_cents(self) -> int:
        return sum(f.money_impact_cents for f in self.findings)

    @property
    def auto_approvable(self) -> bool:
        return self.severity in (Severity.OK, Severity.INFO)

    def summary(self) -> str:
        lines = [f"Load {self.load_id}  ->  {self.severity.value.upper()}  "
                 f"(net impact {cents_to_str(self.net_money_impact_cents)})"]
        for f in self.findings:
            lines.append("   " + repr(f))
        return "\n".join(lines)
