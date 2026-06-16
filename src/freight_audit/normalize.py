"""
Accessorial normalization.

Real carrier invoices call the same charge a dozen ways: "DETENTION", "Det.",
"Driver Wait Time", "Layover/Detention", "WAIT". The matching logic can only
compare apples to apples if every line is first mapped to a canonical category.

The vocabulary now lives in DATA (engine/vocab/accessorials.json) and can be
extended per client via an overlay file -- see vocab_loader.Vocabulary. This module
keeps a hardcoded fallback so it still works if the JSON is missing, and exposes
the same normalize_category() function the rest of the engine already calls.
"""
from __future__ import annotations

from rapidfuzz import fuzz

# hardcoded fallback (used only if the JSON vocab can't be loaded)
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "linehaul":  ["linehaul", "line haul", "freight charge", "base rate", "transportation", "flat rate"],
    "fuel":      ["fuel", "fsc", "fuel surcharge"],
    "detention": ["detention", "det ", "det.", "driver wait", "wait time", "waiting", "demurrage"],
    "layover":   ["layover", "lay over", "overnight"],
    "lumper":    ["lumper", "unloading", "loading fee", "load/unload", "handling"],
    "liftgate":  ["liftgate", "lift gate", "lift-gate"],
    "reweigh":   ["reweigh", "re-weigh", "reweighing", "scale"],
    "tonu":      ["tonu", "truck order not used", "dry run", "dead head", "deadhead"],
    "stopoff":   ["stop off", "stop-off", "extra stop", "multi-stop", "additional stop"],
    "residential": ["residential", "resi "],
}

_FUZZ_THRESHOLD = 86

# lazily-loaded shared vocabulary (data-driven). Set via set_vocabulary() to use a
# client overlay; defaults to the shipped JSON, falling back to the dict above.
_VOCAB = None


def _get_vocab():
    global _VOCAB
    if _VOCAB is None:
        try:
            from .vocab_loader import Vocabulary
            _VOCAB = Vocabulary.load()
        except Exception:
            from .vocab_loader import Vocabulary  # build from the fallback dict
            _VOCAB = Vocabulary(categories={k: [s.lower() for s in v]
                                            for k, v in CATEGORY_KEYWORDS.items()})
    return _VOCAB


def set_vocabulary(vocab) -> None:
    """Install a client-specific Vocabulary (from vocab_loader.Vocabulary.load(...))."""
    global _VOCAB
    _VOCAB = vocab


def normalize_category(description: str) -> str:
    """Map a free-text line description to a canonical accessorial category."""
    return _get_vocab().categorize(description)


def is_same_load(id_a: str, id_b: str) -> bool:
    """Loads referenced across docs may be formatted differently (PRO#, load#, BOL#).
    Normalize and compare leniently."""
    def clean(x: str) -> str:
        return "".join(ch for ch in str(x).upper() if ch.isalnum()).lstrip("0") or "0"
    a, b = clean(id_a), clean(id_b)
    if a == b:
        return True
    # one is a suffix/prefix of the other (e.g. "L-100482" vs "100482")
    if a.endswith(b) or b.endswith(a) or a in b or b in a:
        return True
    return fuzz.ratio(a, b) >= 92
