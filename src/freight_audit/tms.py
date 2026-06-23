"""
Live TMS / accounting write-back (PROMPTS #5).

`exporters.tms_json` produces the generic payload most TMS REST endpoints accept;
this is the live counterpart that actually POSTs it. It targets a clearly-named
generic REST endpoint configured by environment variables (so it works against any
TMS by mapping that payload), and reads the endpoint + token FROM THE ENVIRONMENT
ONLY -- never hard-coded. Uses stdlib urllib (no new dependency); mock urlopen to
test without a live call.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

from .exporters import tms_json
from .models import MatchResult

URL_ENV = "FREIGHT_AUDIT_TMS_URL"
TOKEN_ENV = "FREIGHT_AUDIT_TMS_TOKEN"


def post_to_tms(results: list[MatchResult], endpoint: Optional[str] = None,
                token: Optional[str] = None, *, timeout: int = 30) -> dict:
    """POST the tms_json payload for `results` to the configured TMS endpoint.

    endpoint/token default to $FREIGHT_AUDIT_TMS_URL / $FREIGHT_AUDIT_TMS_TOKEN.
    Returns {status, endpoint, loads_sent, response}. Raises ValueError if no
    endpoint is configured."""
    endpoint = endpoint or os.getenv(URL_ENV)
    token = token or os.getenv(TOKEN_ENV)
    if not endpoint:
        raise ValueError(
            f"no TMS endpoint configured: pass endpoint= or set ${URL_ENV}")

    payload = tms_json(results).encode("utf-8")
    req = urllib.request.Request(endpoint, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        status = getattr(resp, "status", None) or resp.getcode()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = raw
    return {"status": status, "endpoint": endpoint,
            "loads_sent": len(results), "response": body}


# the live counterpart to the EXPORTERS registry (exporters return text; this sends)
SENDERS = {"tms_post": post_to_tms}
