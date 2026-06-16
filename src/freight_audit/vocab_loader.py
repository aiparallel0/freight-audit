"""
Client-extensible accessorial vocabulary (improves "not ready" item #3).

Before: the category dictionary lived hard-coded in normalize.py, so adding a
client's carrier spellings meant editing code. Now the vocabulary is DATA: a JSON
file (vocab/accessorials.json) that ships with sensible defaults, and an optional
per-client overlay file. Onboarding a client = appending their spellings to a
JSON file, no code change.

It also gives you a "learning loop": run `suggest_unmatched()` over a client's real
invoices and it lists every line description that fell through to 'other', with a
best-guess category, so you can quickly curate their overlay.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_VOCAB = os.path.join(_HERE, "vocab", "accessorials.json")
_FUZZ_THRESHOLD = 86


@dataclass
class Vocabulary:
    """Canonical category -> trigger substrings, plus fuzzy fallback."""
    categories: dict[str, list[str]] = field(default_factory=dict)
    fuzz_threshold: int = _FUZZ_THRESHOLD

    @classmethod
    def load(cls, base_path: str = _DEFAULT_VOCAB,
             client_overlay: Optional[str] = None) -> "Vocabulary":
        with open(base_path) as fh:
            data = json.load(fh)
        cats = {k: [s.lower() for s in v] for k, v in data.get("categories", {}).items()}
        thr = int(data.get("fuzz_threshold", _FUZZ_THRESHOLD))
        if client_overlay and os.path.exists(client_overlay):
            with open(client_overlay) as fh:
                overlay = json.load(fh)
            for cat, words in overlay.get("categories", {}).items():
                cats.setdefault(cat, [])
                for w in words:
                    wl = w.lower()
                    if wl not in cats[cat]:
                        cats[cat].append(wl)
            thr = int(overlay.get("fuzz_threshold", thr))
        return cls(categories=cats, fuzz_threshold=thr)

    def categorize(self, description: str) -> str:
        if not description:
            return "other"
        d = description.lower().strip()
        for category, keywords in self.categories.items():
            for kw in keywords:
                if kw in d:
                    return category
        best_cat, best_score = "other", 0
        for category, keywords in self.categories.items():
            for kw in keywords:
                score = fuzz.partial_ratio(kw, d)
                if score > best_score:
                    best_cat, best_score = category, score
        return best_cat if best_score >= self.fuzz_threshold else "other"

    def categorize_scored(self, description: str) -> tuple[str, int]:
        """Return (category, confidence 0-100). Exact substring = 100."""
        if not description:
            return "other", 0
        d = description.lower().strip()
        for category, keywords in self.categories.items():
            for kw in keywords:
                if kw in d:
                    return category, 100
        best_cat, best_score = "other", 0
        for category, keywords in self.categories.items():
            for kw in keywords:
                score = fuzz.partial_ratio(kw, d)
                if score > best_score:
                    best_cat, best_score = category, score
        return (best_cat, best_score) if best_score >= self.fuzz_threshold else ("other", best_score)

    def suggest_unmatched(self, descriptions: list[str]) -> list[dict]:
        """Given real invoice line descriptions, list the ones that didn't match a
        category, with a best-guess, so you can curate the client overlay quickly."""
        out = []
        for desc in descriptions:
            cat, score = self.categorize_scored(desc)
            if cat == "other":
                # best fuzzy guess even below threshold, to seed the suggestion
                best_cat, best_score = "other", 0
                dl = (desc or "").lower()
                for category, keywords in self.categories.items():
                    for kw in keywords:
                        s = fuzz.partial_ratio(kw, dl)
                        if s > best_score:
                            best_cat, best_score = category, s
                out.append({"description": desc, "guess": best_cat,
                            "confidence": best_score})
        return out
