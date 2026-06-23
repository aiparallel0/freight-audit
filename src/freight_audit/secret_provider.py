"""
Secret provider interface -- credentials are read through this, never hardcoded.

Default is the process environment; a JSON file provider is available, and other
backends (cloud secret managers) implement the same SecretProvider protocol and are
installed via set_provider(). Code calls get_secret("STRIPE_API_KEY") and stays
agnostic to where the value lives.
"""
from __future__ import annotations

import json
import os
from typing import Optional, Protocol


class SecretProvider(Protocol):
    def get(self, name: str) -> Optional[str]:
        ...


class EnvSecretProvider:
    """Reads secrets from environment variables (the default)."""

    def get(self, name: str) -> Optional[str]:
        return os.environ.get(name)


class FileSecretProvider:
    """Reads secrets from a flat JSON file {name: value}."""

    def __init__(self, path: str):
        self.path = path
        self._cache: Optional[dict] = None

    def get(self, name: str) -> Optional[str]:
        if self._cache is None:
            try:
                with open(self.path) as fh:
                    self._cache = json.load(fh)
            except FileNotFoundError:
                self._cache = {}
        val = self._cache.get(name)
        return str(val) if val is not None else None


class ChainSecretProvider:
    """Tries each provider in order; first hit wins."""

    def __init__(self, *providers: SecretProvider):
        self.providers = providers

    def get(self, name: str) -> Optional[str]:
        for p in self.providers:
            val = p.get(name)
            if val is not None:
                return val
        return None


_provider: SecretProvider = EnvSecretProvider()


def set_provider(provider: SecretProvider) -> None:
    global _provider
    _provider = provider


def get_provider() -> SecretProvider:
    return _provider


def get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    val = _provider.get(name)
    return val if val is not None else default
