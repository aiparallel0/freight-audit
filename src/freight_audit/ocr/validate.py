"""
Validation layer for the extracted receipt.

Extraction alone is worthless -- the VALUE is checking the extracted numbers for
internal consistency, exactly like the freight engine checks invoice vs rate con
vs POD. Here we have one document, so we check it against ITSELF:

  - do the line totals sum to the printed TOTAL?
  - does qty x unit price equal each line total?
  - does TOTAL + rounding = TOTAL ROUNDED?
  - does CASH - TOTAL ROUNDED = CHANGE?
  - does the item/qty count match the number of line items?

Every check that fails is either a real receipt error OR an OCR error we should
flag for human review -- which is precisely the "confidence routing" a production
system needs (item #6). The structure (Finding/severity) is reused from the
freight engine so the same review console could render it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .extract import Receipt, ReceiptLine


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


@dataclass
class Check:
    name: str
    severity: Severity
    message: str
    expected: Optional[float] = None
    found: Optional[float] = None


@dataclass
class ValidationResult:
    checks: list[Check] = field(default_factory=list)
    receipt: Optional[Receipt] = None

    @property
    def severity(self) -> Severity:
        order = [Severity.OK, Severity.INFO, Severity.WARN, Severity.BLOCK]
        worst = Severity.OK
        for c in self.checks:
            if order.index(c.severity) > order.index(worst):
                worst = c.severity
        return worst

    @property
    def confident(self) -> bool:
        """True if extraction is internally consistent enough to trust without review."""
        return self.severity in (Severity.OK, Severity.INFO)


def _close(a: Optional[float], b: Optional[float], tol: float = 0.02) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def validate(receipt: Receipt, tol: float = 0.02) -> ValidationResult:
    res = ValidationResult(receipt=receipt)

    # 1) line math: qty * unit == line_total
    for ln in receipt.lines:
        if ln.qty and ln.unit_price is not None and ln.line_total is not None:
            expect = round(ln.qty * ln.unit_price, 2)
            if not _close(expect, ln.line_total, tol):
                res.checks.append(Check(
                    "line_math", Severity.WARN,
                    f"'{ln.description}': {ln.qty} x {ln.unit_price:.2f} = {expect:.2f} "
                    f"but line shows {ln.line_total:.2f}",
                    expected=expect, found=ln.line_total))

    # 2) sum of line totals == printed TOTAL
    line_sum = round(sum(l.line_total for l in receipt.lines if l.line_total is not None), 2)
    if receipt.total is not None and receipt.lines:
        if _close(line_sum, receipt.total, tol):
            res.checks.append(Check(
                "line_sum_vs_total", Severity.OK,
                f"Line items sum to {line_sum:.2f}, matches printed TOTAL {receipt.total:.2f}.",
                expected=line_sum, found=receipt.total))
        else:
            res.checks.append(Check(
                "line_sum_vs_total", Severity.BLOCK,
                f"Line items sum to {line_sum:.2f} but TOTAL says {receipt.total:.2f} "
                f"(off by {abs(line_sum - receipt.total):.2f}) -- extraction or receipt error.",
                expected=line_sum, found=receipt.total))

    # 3) item count == number of parsed lines
    if receipt.declared_item_count is not None:
        n = len(receipt.lines)
        if n == receipt.declared_item_count:
            res.checks.append(Check(
                "item_count", Severity.OK,
                f"Parsed {n} line items, matches declared count {receipt.declared_item_count}.",
                expected=receipt.declared_item_count, found=n))
        else:
            res.checks.append(Check(
                "item_count", Severity.WARN,
                f"Parsed {n} line items but receipt declares {receipt.declared_item_count} "
                f"-- a line may have been missed in OCR.",
                expected=receipt.declared_item_count, found=n))

    # 4) TOTAL + rounding == TOTAL ROUNDED
    #    Malaysian receipts round the TOTAL to the nearest 0.05 (5 sen); the
    #    printed rounding adjustment is signed but OCR often drops the '-'. So we
    #    check the magnitude against the actual rounding delta, and confirm the
    #    rounded value really is TOTAL rounded to nearest 0.05.
    if receipt.total is not None and receipt.total_rounded is not None:
        nearest_5 = round(receipt.total * 20) / 20.0   # nearest 0.05
        delta = round(receipt.total_rounded - receipt.total, 2)
        rounded_ok = _close(receipt.total_rounded, nearest_5, tol)
        if receipt.rounding_adjustment is not None:
            adj_ok = _close(abs(receipt.rounding_adjustment), abs(delta), tol)
        else:
            adj_ok = True
        if rounded_ok and adj_ok:
            res.checks.append(Check(
                "rounding", Severity.OK,
                f"TOTAL {receipt.total:.2f} rounds to nearest 0.05 = {receipt.total_rounded:.2f} "
                f"(adjustment {delta:+.2f}).",
                expected=nearest_5, found=receipt.total_rounded))
        else:
            res.checks.append(Check(
                "rounding", Severity.WARN,
                f"TOTAL {receipt.total:.2f} should round to {nearest_5:.2f} but ROUNDED "
                f"shows {receipt.total_rounded:.2f} (adjustment read as "
                f"{receipt.rounding_adjustment}).",
                expected=nearest_5, found=receipt.total_rounded))

    # 5) CASH - TOTAL ROUNDED == CHANGE
    base = receipt.total_rounded if receipt.total_rounded is not None else receipt.total
    if receipt.cash is not None and base is not None and receipt.change is not None:
        expect = round(receipt.cash - base, 2)
        if _close(expect, receipt.change, tol):
            res.checks.append(Check(
                "change", Severity.OK,
                f"CASH {receipt.cash:.2f} - {base:.2f} = CHANGE {receipt.change:.2f}.",
                expected=expect, found=receipt.change))
        else:
            res.checks.append(Check(
                "change", Severity.WARN,
                f"CASH {receipt.cash:.2f} - {base:.2f} should be {expect:.2f} "
                f"but CHANGE shows {receipt.change:.2f}.",
                expected=expect, found=receipt.change))

    if not res.checks:
        res.checks.append(Check("none", Severity.INFO, "No cross-checkable fields found."))
    return res
