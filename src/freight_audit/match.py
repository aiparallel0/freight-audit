"""
The matching engine -- the actual product.

Given a RateConfirmation (the agreement), a CarrierInvoice (the claim), and a
ProofOfDelivery (the evidence), it produces a MatchResult full of Findings.

Two directions of money matter, and most tools only look at one:
  1. Broker OVERPAYS  -> invoice > agreement, unauthorized/over-cap accessorials,
                         duplicates. (Classic freight audit.)
  2. Carrier UNDERBILLS -> they're owed detention the POD proves, but they didn't
                         bill it. This is the ATRI "~half of detention invoices
                         never get collected" money. Catching it = "we recover
                         revenue you're already owed," which is far easier to sell
                         than "we save you money."

Tunable thresholds live in EngineConfig so each client can be calibrated.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import (
    CarrierInvoice, Finding, FindingType, LineItem, MatchResult,
    ProofOfDelivery, RateConfirmation, Severity, cents_to_str,
)
from .normalize import normalize_category, is_same_load


@dataclass
class EngineConfig:
    # how many cents of total mismatch to ignore (rounding / pennies)
    total_tolerance_cents: int = 100
    # default detention rate to value an underbilled detention claim, if rate con is silent
    default_detention_rate_cents: int = 7500   # $75.00/hr
    # round detention to nearest fraction of an hour when billing
    detention_round_hours: float = 0.25
    # accessorial categories that ALWAYS require POD support to be payable
    pod_required_categories: tuple[str, ...] = ("detention", "layover")
    # cap billable detention at this many hours; beyond it, treat as layover/data
    # error and flag for a human rather than auto-computing a big number
    max_detention_hours: float = 8.0
    # time on site beyond this is implausible -> likely a date/OCR error, flag it
    implausible_time_on_site_hours: float = 14.0


class MatchEngine:
    def __init__(self, config: EngineConfig | None = None):
        self.cfg = config or EngineConfig()

    # -- public entry point -------------------------------------------------
    def match(
        self,
        rate_con: RateConfirmation | None,
        invoice: CarrierInvoice | None,
        pod: ProofOfDelivery | None,
    ) -> MatchResult:
        load_id = (
            (rate_con.load_id if rate_con else None)
            or (invoice.load_id if invoice else None)
            or "UNKNOWN"
        )
        result = MatchResult(load_id=load_id, rate_con=rate_con, invoice=invoice, pod=pod)

        # 0) presence checks
        if invoice is None:
            result.findings.append(Finding(
                FindingType.MISSING_DOC, Severity.BLOCK,
                "No carrier invoice present; nothing to audit."))
            return result
        if rate_con is None:
            result.findings.append(Finding(
                FindingType.MISSING_DOC, Severity.BLOCK,
                "No rate confirmation present; cannot validate charges against agreement."))
            # we can still run POD/detention checks below, so don't return

        # 0b) integrity: are these documents even for the same load?
        self._check_load_ids(rate_con, invoice, pod, result)
        # 0c) integrity: is the POD timing data sane?
        self._check_pod_data(pod, result)

        # normalize all line categories up front
        for li in invoice.line_items:
            li.category = normalize_category(li.description)
        if rate_con:
            for li in rate_con.line_items:
                li.category = normalize_category(li.description)

        # run rule set
        if rate_con:
            self._check_total(rate_con, invoice, result)
            self._check_line_overcharges(rate_con, invoice, result)
            self._check_unauthorized_accessorials(rate_con, invoice, result)
            self._check_accessorial_caps(rate_con, invoice, result)
        self._check_duplicates(invoice, result)
        self._check_detention(rate_con, invoice, pod, result)
        self._check_pod_presence(invoice, pod, result)

        if not result.findings:
            result.findings.append(Finding(
                FindingType.OK, Severity.OK,
                "Invoice matches agreement and evidence; safe to auto-approve."))
        return result

    # -- individual rules ---------------------------------------------------
    def _check_total(self, rc: RateConfirmation, inv: CarrierInvoice, res: MatchResult):
        diff = inv.billed_total_cents - rc.agreed_total_cents
        if abs(diff) <= self.cfg.total_tolerance_cents:
            return
        if diff > 0:
            res.findings.append(Finding(
                FindingType.TOTAL_MISMATCH, Severity.WARN,
                f"Invoice total {cents_to_str(inv.billed_total_cents)} exceeds agreed "
                f"{cents_to_str(rc.agreed_total_cents)} by {cents_to_str(diff)}.",
                money_impact_cents=diff))
        else:
            res.findings.append(Finding(
                FindingType.TOTAL_MISMATCH, Severity.INFO,
                f"Invoice total {cents_to_str(inv.billed_total_cents)} is "
                f"{cents_to_str(-diff)} BELOW agreed {cents_to_str(rc.agreed_total_cents)}.",
                money_impact_cents=diff))

    def _check_line_overcharges(self, rc: RateConfirmation, inv: CarrierInvoice, res: MatchResult):
        """Compare same-category charges that exist on BOTH docs (e.g. linehaul, fuel)."""
        rc_by_cat: dict[str, int] = {}
        for li in rc.line_items:
            rc_by_cat[li.category] = rc_by_cat.get(li.category, 0) + li.amount_cents
        inv_by_cat: dict[str, int] = {}
        for li in inv.line_items:
            inv_by_cat[li.category] = inv_by_cat.get(li.category, 0) + li.amount_cents

        for cat in ("linehaul", "fuel"):
            if cat in rc_by_cat and cat in inv_by_cat:
                over = inv_by_cat[cat] - rc_by_cat[cat]
                if over > self.cfg.total_tolerance_cents:
                    res.findings.append(Finding(
                        FindingType.LINE_OVERCHARGE, Severity.WARN,
                        f"{cat.title()} billed {cents_to_str(inv_by_cat[cat])} vs agreed "
                        f"{cents_to_str(rc_by_cat[cat])} (+{cents_to_str(over)}).",
                        money_impact_cents=over))

    def _check_unauthorized_accessorials(self, rc: RateConfirmation, inv: CarrierInvoice, res: MatchResult):
        """Accessorial on the invoice that is neither on the rate con nor pre-approved.
        A zero/blank-amount accessorial is not an overcharge -- don't cry wolf on it;
        note it once as INFO so it's transparent, not silently dropped."""
        rc_categories = {li.category for li in rc.line_items}
        approved = set(rc.approved_accessorials.keys())
        baseline = {"linehaul", "fuel", "other"} | rc_categories | approved
        zero_amount = []
        for li in inv.line_items:
            if li.category in baseline:
                continue
            if li.amount_cents <= 0:           # $0 / blank: no money at stake
                if li.amount_cents == 0:
                    zero_amount.append(li)
                continue
            res.findings.append(Finding(
                FindingType.UNAUTHORIZED_ACCESSORIAL, Severity.WARN,
                f"Unauthorized accessorial '{li.description}' ({li.category}) "
                f"billed {cents_to_str(li.amount_cents)} -- not on rate con or pre-approved.",
                money_impact_cents=li.amount_cents))
        if zero_amount:
            names = ", ".join(f"'{li.description}'" for li in zero_amount)
            res.findings.append(Finding(
                FindingType.ZERO_AMOUNT_ACCESSORIAL, Severity.INFO,
                f"Zero/blank-amount accessorial line(s) ignored (no charge): {names}.",
                money_impact_cents=0))

    def _check_accessorial_caps(self, rc: RateConfirmation, inv: CarrierInvoice, res: MatchResult):
        """Accessorial that IS approved but exceeds the approved cap."""
        inv_by_cat: dict[str, int] = {}
        for li in inv.line_items:
            inv_by_cat[li.category] = inv_by_cat.get(li.category, 0) + li.amount_cents
        for cat, cap in rc.approved_accessorials.items():
            if cap is None:
                continue
            billed = inv_by_cat.get(cat, 0)
            if billed > cap + self.cfg.total_tolerance_cents:
                res.findings.append(Finding(
                    FindingType.ACCESSORIAL_OVER_CAP, Severity.WARN,
                    f"{cat.title()} billed {cents_to_str(billed)} exceeds approved cap "
                    f"{cents_to_str(cap)} (+{cents_to_str(billed - cap)}).",
                    money_impact_cents=billed - cap))

    def _check_duplicates(self, inv: CarrierInvoice, res: MatchResult):
        """Same category + same amount appearing more than once = likely double billing."""
        seen: dict[tuple[str, int], int] = {}
        for li in inv.line_items:
            key = (li.category, li.amount_cents)
            seen[key] = seen.get(key, 0) + 1
        for (cat, amt), count in seen.items():
            if count > 1 and amt > 0:
                dup_amt = amt * (count - 1)
                res.findings.append(Finding(
                    FindingType.DUPLICATE_LINE, Severity.WARN,
                    f"'{cat}' charge of {cents_to_str(amt)} appears {count}x "
                    f"-- {count - 1} likely duplicate(s).",
                    money_impact_cents=dup_amt))

    def _check_detention(
        self, rc: RateConfirmation | None, inv: CarrierInvoice,
        pod: ProofOfDelivery | None, res: MatchResult,
    ):
        """The revenue-recovery rule. Two cases:
        (a) carrier BILLED detention -> POD must support it, else it's deniable.
        (b) carrier did NOT bill detention but POD proves a long wait -> they're
            leaving money on the table (the ATRI money)."""
        # only count detention lines that actually carry a charge (a $0/blank
        # detention line means "not billed", handled by the underbilled case below)
        detention_lines = [li for li in inv.line_items
                           if li.category == "detention" and li.amount_cents > 0]
        billed_detention = sum(li.amount_cents for li in detention_lines)
        free_hours = rc.free_time_hours if rc else 2.0
        # only use POD time if it passed the sanity checks
        time_on_site = pod.time_on_site_hours if self._pod_time_usable(pod) else None

        # multiple detention lines on one invoice -> substantiated against their SUM
        # above, but two detention charges for a single POD wait is a double-bill risk.
        if len(detention_lines) > 1:
            res.findings.append(Finding(
                FindingType.MULTIPLE_DETENTION_LINES, Severity.WARN,
                f"{len(detention_lines)} separate detention lines totaling "
                f"{cents_to_str(billed_detention)} on one invoice -- verify the same "
                f"wait isn't billed twice.",
                money_impact_cents=0))

        # case (a): billed but unprovable
        if billed_detention > 0:
            if time_on_site is None:
                res.findings.append(Finding(
                    FindingType.DETENTION_UNSUPPORTED, Severity.BLOCK,
                    f"Detention of {cents_to_str(billed_detention)} billed but "
                    f"{self._pod_time_reason(pod)} -- deniable as-is.",
                    money_impact_cents=0))
            else:
                billable_hours = max(0.0, time_on_site - free_hours)
                if billable_hours <= 0:
                    res.findings.append(Finding(
                        FindingType.DETENTION_UNSUPPORTED, Severity.BLOCK,
                        f"Detention billed but POD shows only {time_on_site:.2f}h on site "
                        f"(<= {free_hours:.1f}h free) -- not supported.",
                        money_impact_cents=0))

        # case (b): not billed but earned
        if billed_detention == 0 and time_on_site is not None:
            billable_hours = max(0.0, time_on_site - free_hours)
            if billable_hours >= self.cfg.detention_round_hours:
                owed, note = self._value_underbilled_detention(rc, billable_hours)
                res.findings.append(Finding(
                    FindingType.DETENTION_UNDERBILLED, Severity.WARN,
                    f"POD shows {time_on_site:.2f}h on site "
                    f"({billable_hours:.2f}h past free time) but NO detention billed. {note}",
                    money_impact_cents=-owed))

    def _check_pod_presence(self, inv: CarrierInvoice, pod: ProofOfDelivery | None, res: MatchResult):
        needs_pod = any(
            li.category in self.cfg.pod_required_categories for li in inv.line_items)
        if pod is None and needs_pod:
            res.findings.append(Finding(
                FindingType.MISSING_POD, Severity.BLOCK,
                "Invoice includes detention/layover but no POD attached to prove it.",
                money_impact_cents=0))
        elif pod is None:
            res.findings.append(Finding(
                FindingType.MISSING_POD, Severity.INFO,
                "No POD attached (not strictly required for these charges)."))
        elif not pod.delivered:
            res.findings.append(Finding(
                FindingType.MISSING_POD, Severity.BLOCK,
                "POD present but marked NOT delivered -- do not pay."))

    def _check_load_ids(self, rc, inv, pod, res: MatchResult):
        """All three docs should reference the same load. If not, they may have been
        mis-paired -- a dangerous silent error -- so flag it loudly."""
        ids = []
        if rc:
            ids.append(("rate con", rc.load_id))
        if inv:
            ids.append(("invoice", inv.load_id))
        if pod:
            ids.append(("POD", pod.load_id))
        # compare every pair; if any pair fails is_same_load, flag once
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                (na, a), (nb, b) = ids[i], ids[j]
                if not is_same_load(a, b):
                    res.findings.append(Finding(
                        FindingType.LOAD_ID_MISMATCH, Severity.BLOCK,
                        f"{na} references load '{a}' but {nb} references '{b}' -- "
                        f"documents may be mis-matched. Do not pay until resolved.",
                        money_impact_cents=0))
                    return

    def _check_pod_data(self, pod: ProofOfDelivery | None, res: MatchResult):
        """Catch implausible POD timestamps (inverted or absurdly long) so we don't
        silently compute detention off bad data."""
        if pod is None:
            return
        tos = pod.time_on_site_hours
        if tos is None:
            return
        if tos < 0:
            res.findings.append(Finding(
                FindingType.BAD_POD_DATA, Severity.BLOCK,
                f"POD departure time is before arrival time ({tos:.2f}h) -- bad data; "
                f"detention cannot be computed. Verify the POD.",
                money_impact_cents=0))
        elif tos > self.cfg.implausible_time_on_site_hours:
            res.findings.append(Finding(
                FindingType.BAD_POD_DATA, Severity.WARN,
                f"POD shows {tos:.1f}h on site -- implausibly long (likely a date or "
                f"OCR error, or a layover rather than detention). Verify before billing.",
                money_impact_cents=0))

    @staticmethod
    def _has_clock_time(dt) -> bool:
        """A timestamp that parsed to exactly 00:00:00 almost always means the source
        had a DATE but no clock time (the time defaulted to midnight). Treat that as
        'no usable time of day' so we never compute detention off a fabricated midnight.
        A genuine 00:00 arrival is rare; the cost of being wrong is a human review."""
        return dt is not None and (dt.hour, dt.minute, dt.second) != (0, 0, 0)

    def _pod_time_usable(self, pod: ProofOfDelivery | None) -> bool:
        """True only if POD has timestamps we trust enough to compute detention from:
        both present, both carrying a clock time, and the span sane."""
        if pod is None:
            return False
        if not (self._has_clock_time(pod.arrival_time)
                and self._has_clock_time(pod.departure_time)):
            return False
        tos = pod.time_on_site_hours
        return (tos is not None
                and tos >= 0
                and tos <= self.cfg.implausible_time_on_site_hours)

    def _pod_time_reason(self, pod: ProofOfDelivery | None) -> str:
        """Human-readable reason a POD's timing can't substantiate billed detention."""
        if (pod is not None and pod.arrival_time is not None
                and pod.departure_time is not None
                and not (self._has_clock_time(pod.arrival_time)
                         and self._has_clock_time(pod.departure_time))):
            return "the POD shows a delivery date but no arrival/departure clock time"
        return "POD has no usable arrival/departure timestamps to substantiate it"

    def _detention_rate(self, rc: RateConfirmation | None) -> int:
        if rc:
            for li in rc.line_items:
                if li.category == "detention" and li.rate_cents:
                    return li.rate_cents
            cap = rc.approved_accessorials.get("detention")
            if isinstance(cap, int) and cap > 0:
                return cap  # treat a per-hour approval as the rate if that's all we have
        return self.cfg.default_detention_rate_cents

    def _value_underbilled_detention(self, rc: RateConfirmation | None,
                                     billable_hours: float) -> tuple[int, str]:
        """Value an underbilled-detention claim, in integer cents, with a human note.
        Honors a flat detention fee on the rate con; otherwise prices it per-hour at
        the rate-con (or default) rate, capped at max_detention_hours."""
        if rc is not None and rc.detention_flat_fee_cents is not None:
            owed = rc.detention_flat_fee_cents
            return owed, (f"A flat detention fee of {cents_to_str(owed)} is owed "
                          f"-- recoverable revenue.")
        rate = self._detention_rate(rc)
        # cap at max_detention_hours: beyond that it's more likely layover/data error
        capped = min(billable_hours, self.cfg.max_detention_hours)
        rounded = round(capped / self.cfg.detention_round_hours) \
            * self.cfg.detention_round_hours
        owed = int(round(rounded * rate))
        note = (f"Carrier is owed ~{cents_to_str(owed)} @ {cents_to_str(rate)}/hr "
                f"({rounded:.2f}h) -- recoverable revenue")
        if billable_hours > self.cfg.max_detention_hours:
            note += (f" (capped at {self.cfg.max_detention_hours:.0f}h; "
                     f"verify whether this was layover)")
        return owed, note + "."
