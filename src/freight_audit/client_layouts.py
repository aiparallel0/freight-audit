"""
Per-client OCR layout tuning: load a tenant's LayoutProfile overrides from a config
directory (config/layouts/<tenant>.json), falling back to the shipped default. The
mechanism is complete; only the tuned values per client are data.

BLOCKED (tuning values): real client documents are needed to set the per-format
patterns/labels accurately for a given carrier's layout.
"""
from __future__ import annotations

import os
from typing import Optional

from .ocr.layouts import LayoutProfile, DEFAULT_LAYOUT


def layouts_dir(base: Optional[str] = None) -> str:
    if base:
        return base
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "config", "layouts"))


def load_client_layout(tenant: str, config_dir: Optional[str] = None) -> LayoutProfile:
    """Return the tenant's tuned LayoutProfile if config/layouts/<tenant>.json exists,
    else the shipped DEFAULT_LAYOUT."""
    path = os.path.join(layouts_dir(config_dir), f"{tenant}.json")
    if os.path.exists(path):
        return LayoutProfile.load(path)
    return DEFAULT_LAYOUT
