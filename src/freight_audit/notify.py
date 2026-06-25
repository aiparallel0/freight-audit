"""
Alerting: evaluate alert rules against a metrics snapshot and dispatch notifications
through a configured transport (email / Slack / webhook), with a FakeTransport for
tests. Rules live in config/alerts.json; transport is chosen by NOTIFY_TRANSPORT.
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Optional, Protocol

from .secret_provider import get_secret


class Transport(Protocol):
    def send(self, subject: str, body: str, target: Optional[str] = None) -> None:
        ...


class FakeTransport:
    """Captures dispatched alerts in memory (for tests)."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, subject: str, body: str, target: Optional[str] = None) -> None:
        self.sent.append({"subject": subject, "body": body, "target": target})


class WebhookTransport:
    def __init__(self, url: str):
        self.url = url

    def send(self, subject: str, body: str, target: Optional[str] = None) -> None:
        data = json.dumps({"subject": subject, "text": body}).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).close()


class SlackTransport:
    def __init__(self, url: str):
        self.url = url

    def send(self, subject: str, body: str, target: Optional[str] = None) -> None:
        data = json.dumps({"text": f"*{subject}*\n{body}"}).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).close()


class EmailTransport:
    def __init__(self, host: str, port: int = 25, user: Optional[str] = None,
                 password: Optional[str] = None, sender: str = "alerts@freight-audit"):
        self.host, self.port, self.user, self.password, self.sender = \
            host, int(port), user, password, sender

    def send(self, subject: str, body: str, target: Optional[str] = None) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, self.sender, target or self.sender
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port) as smtp:
            if self.user:
                smtp.login(self.user, self.password or "")
            smtp.send_message(msg)


def default_transport() -> Transport:
    kind = (get_secret("NOTIFY_TRANSPORT", "fake") or "fake").lower()
    if kind == "slack":
        return SlackTransport(get_secret("SLACK_WEBHOOK_URL", ""))
    if kind == "webhook":
        return WebhookTransport(get_secret("NOTIFY_WEBHOOK_URL", ""))
    if kind == "email":
        return EmailTransport(get_secret("SMTP_HOST", "localhost"),
                              get_secret("SMTP_PORT", "25"),
                              get_secret("SMTP_USER"), get_secret("SMTP_PASSWORD"))
    return FakeTransport()


class Notifier:
    def __init__(self, transport: Optional[Transport] = None):
        self.transport = transport or default_transport()

    def dispatch(self, alert: dict) -> None:
        self.transport.send(alert.get("subject", "alert"), alert.get("body", ""),
                            alert.get("target"))


_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b}


def _alerts_path() -> Optional[str]:
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "..", "config", "alerts.json"),
                 os.path.join(here, "..", "config", "alerts.json")):
        if os.path.exists(cand):
            return cand
    return None


def load_alert_rules(path: Optional[str] = None) -> list[dict]:
    path = path or _alerts_path()
    if not path or not os.path.exists(path):
        return []
    return json.load(open(path)).get("rules", [])


def evaluate_rules(snapshot: list[dict], rules: list[dict]) -> list[dict]:
    """Return the alerts firing for a metrics snapshot."""
    firing = []
    for series in snapshot:
        for rule in rules:
            metric, op = rule.get("metric"), _OPS.get(rule.get("op", ">"))
            threshold = rule.get("threshold")
            if metric in series and op and op(series[metric], threshold):
                sev = rule.get("severity", "warn")
                firing.append({
                    "name": rule.get("name", "alert"),
                    "severity": sev,
                    "subject": f"[{sev.upper()}] {rule.get('name')} on tenant {series['tenant']}",
                    "body": (f"{metric}={series[metric]} {rule.get('op')} {threshold} "
                             f"(path {series['path']})"),
                    "series": series,
                })
    return firing
