"""freight_audit -- freight document matching & accessorial-recovery engine.

Audits a carrier invoice against the rate confirmation and proof-of-delivery to
catch overcharges, unauthorized accessorials, duplicate lines, and unprovable
detention -- and the reverse: detention that's owed but was never billed.

Public API:
    from freight_audit import process_load, MatchEngine, JsonFixtureProvider

Optional OCR pipeline (requires the ``ocr`` extra: pytesseract, opencv, pillow):
    from freight_audit.ocr import extract_receipt, validate
"""
from .models import (
    CarrierInvoice, Finding, FindingType, LineItem, MatchResult,
    ProofOfDelivery, RateConfirmation, Severity, cents_to_str, to_cents,
)
from .extract import (
    ExtractionProvider, JsonFixtureProvider, TextractProvider,
    GoogleDocAIProvider, VeryfiProvider, PROVIDERS,
)
from .match import MatchEngine, EngineConfig
from .normalize import normalize_category, set_vocabulary
from .vocab_loader import Vocabulary
from .profiles import ClientProfile, FuelRule, apply_profile_rules
from .exporters import EXPORTERS, EXPORTER_EXT, export

__version__ = "0.1.0"


def process_load(rate_con_src, invoice_src, pod_src,
                 provider: ExtractionProvider | None = None,
                 engine: MatchEngine | None = None,
                 profile: "ClientProfile | None" = None,
                 miles: float | None = None) -> MatchResult:
    """Convenience: extract three docs with `provider` and match them with `engine`.
    Any source may be None (missing doc). If `profile` is given, the client's
    business rules are applied on top of the core findings."""
    provider = provider or JsonFixtureProvider()
    if engine is None:
        engine = MatchEngine(profile.to_engine_config()) if profile else MatchEngine()
    rc = provider.extract_rate_confirmation(rate_con_src) if rate_con_src else None
    inv = provider.extract_invoice(invoice_src) if invoice_src else None
    pod = provider.extract_pod(pod_src) if pod_src else None
    result = engine.match(rc, inv, pod)
    if profile is not None and inv is not None:
        # categories were normalized inside match(); apply client rules now
        extra = apply_profile_rules(profile, rc, inv, miles=miles)
        # drop the synthetic "OK" finding if real findings now exist
        if extra and len(result.findings) == 1 and result.findings[0].type == FindingType.OK:
            result.findings = []
        result.findings.extend(extra)
    return result


__all__ = [
    "process_load", "MatchEngine", "EngineConfig",
    "ExtractionProvider", "JsonFixtureProvider", "TextractProvider",
    "GoogleDocAIProvider", "VeryfiProvider", "PROVIDERS",
    "RateConfirmation", "CarrierInvoice", "ProofOfDelivery", "LineItem",
    "MatchResult", "Finding", "FindingType", "Severity",
    "cents_to_str", "to_cents",
    "normalize_category", "set_vocabulary", "Vocabulary",
    "ClientProfile", "FuelRule", "apply_profile_rules",
    "EXPORTERS", "EXPORTER_EXT", "export",
]
