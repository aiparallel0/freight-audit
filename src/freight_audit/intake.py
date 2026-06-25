"""
Document intake channels -> engine load bundles.

  - parse_eml(raw)        : pull load-bundle JSON attachments out of an email.
  - parse_edi_210(text)   : parse an X12 EDI 210 freight invoice into an invoice bundle.
  - tms_pull(endpoint)    : GET pending loads from a TMS REST endpoint and map them.

The parsers run against fixtures; the network adapters read endpoint/creds from the
secret provider.

BLOCKED (live): a real mailbox (IMAP), an EDI VAN connection, and TMS-pull
credentials/endpoint are needed to ingest production traffic.
"""
from __future__ import annotations

import email
import json
import urllib.request
from email import policy
from typing import Optional

from .secret_provider import get_secret


def parse_eml(raw: bytes) -> list[dict]:
    """Return every load-bundle JSON attachment found in an email."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    bundles = []
    for part in msg.walk():
        is_json = (part.get_content_type() == "application/json"
                   or (part.get_filename() or "").lower().endswith(".json"))
        if not is_json:
            continue
        try:
            content = part.get_content()
            bundles.append(json.loads(content if isinstance(content, str) else content.decode()))
        except Exception:
            continue
    return bundles


def _cents_to_str(cents_str: str) -> str:
    return f"${int(cents_str) / 100:.2f}"


def parse_edi_210(text: str) -> dict:
    """Parse a (subset of) X12 EDI 210 freight invoice into an invoice bundle.
    Segments are separated by '~', elements by '*'. Reads B3 (invoice id / amount),
    L1 (charge lines), and N9 (reference / load number)."""
    invoice: dict = {"invoice_number": "", "line_items": []}
    load_id = ""
    for seg in (s.strip() for s in text.replace("\n", "").split("~")):
        if not seg:
            continue
        el = seg.split("*")
        tag = el[0]
        if tag == "B3":
            if len(el) > 2:
                invoice["invoice_number"] = el[2]
            if len(el) > 7 and el[7].isdigit():
                invoice["billed_total"] = _cents_to_str(el[7])
        elif tag == "L1":
            nums = [int(e) for e in el[1:] if e.isdigit()]
            if nums:
                desc = el[-1] if el and not el[-1].isdigit() else "Charge"
                # the monetary amount is the largest numeric element (cents), not the
                # small line/sequence number
                invoice["line_items"].append(
                    {"description": desc, "amount": _cents_to_str(str(max(nums)))})
        elif tag == "N9" and len(el) > 2 and el[1] in ("LO", "BM", "CN"):
            load_id = el[2]
    invoice["load_id"] = load_id
    return {"load_id": load_id, "invoice": invoice}


def _map_tms_load(row: dict) -> dict:
    return {"load_id": row.get("load_id") or row.get("id", ""),
            "rate_confirmation": row.get("rate_confirmation"),
            "invoice": row.get("invoice"),
            "pod": row.get("pod")}


def tms_pull(endpoint: Optional[str] = None, token: Optional[str] = None,
             timeout: int = 30) -> list[dict]:
    """Pull pending loads from a TMS REST endpoint and map them to bundles (live)."""
    endpoint = endpoint or get_secret("TMS_PULL_URL")
    token = token or get_secret("TMS_PULL_TOKEN")
    if not endpoint:
        raise ValueError("no TMS_PULL_URL configured")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(endpoint, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    rows = data.get("loads", []) if isinstance(data, dict) else data
    return [_map_tms_load(r) for r in rows]
