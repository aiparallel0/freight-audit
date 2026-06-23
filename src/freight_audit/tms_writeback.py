"""
Live-TMS write-back against a documented schema interface.

A SchemaMapping maps our canonical audit fields (from exporters.tms_json) onto a
specific TMS's field names; map_payload() produces the TMS-shaped body and
write_back() POSTs it (endpoint/token from the secret provider). The mapping is
config, so onboarding a TMS is editing a field map -- not code.

BLOCKED (live): the specific TMS's field schema + sandbox credentials/endpoint are
needed to validate against the real system.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .exporters import tms_json
from .secret_provider import get_secret


@dataclass
class SchemaMapping:
    """canonical field name -> target TMS field name."""
    field_map: dict

    @classmethod
    def from_file(cls, path: str) -> "SchemaMapping":
        return cls(field_map=json.load(open(path)).get("field_map", {}))


def map_payload(results, mapping: SchemaMapping) -> dict:
    """Translate the generic tms_json payload into the target TMS's schema."""
    generic = json.loads(tms_json(results))
    records = []
    for load in generic["loads"]:
        records.append({mapping.field_map.get(k, k): v for k, v in load.items()})
    return {"records": records}


def write_back(results, mapping: SchemaMapping, endpoint: Optional[str] = None,
               token: Optional[str] = None, timeout: int = 30) -> dict:
    """POST the mapped payload to the TMS write-back endpoint (live)."""
    endpoint = endpoint or get_secret("TMS_WRITEBACK_URL")
    token = token or get_secret("TMS_WRITEBACK_TOKEN")
    if not endpoint:
        raise ValueError("no TMS_WRITEBACK_URL configured")
    payload = map_payload(results, mapping)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
    return {"status": status, "records": len(payload["records"])}
