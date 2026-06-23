"""
SSO (OIDC) integration points + config.

Implements the authorization-code flow seams (authorize URL, code->token exchange)
and real HS256 id_token verification (signature + exp + audience), exercised against
a mock IdP in tests. RS256/JWKS and the live token exchange require a real IdP.

BLOCKED (live): a real IdP tenant (Okta / Azure AD) + its metadata (issuer, client
id/secret, JWKS) is needed to verify against production RS256 tokens and exchange
real auth codes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .secret_provider import get_secret


class InvalidToken(ValueError):
    pass


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def jwt_encode(claims: dict, secret: str) -> str:
    """Encode an HS256 JWT (used by the mock IdP in tests)."""
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64u(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64u(sig)}"


def jwt_decode(token: str, secret: str, audience: Optional[str] = None) -> dict:
    """Verify an HS256 JWT signature, expiry, and audience; return its claims."""
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidToken("malformed token")
    header, payload, sig = parts
    expected = _b64u(hmac.new(secret.encode(), f"{header}.{payload}".encode(),
                              hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise InvalidToken("bad signature")
    claims = json.loads(_b64u_decode(payload))
    if "exp" in claims and time.time() > float(claims["exp"]):
        raise InvalidToken("expired")
    if audience is not None:
        aud = claims.get("aud")
        if aud != audience and not (isinstance(aud, list) and audience in aud):
            raise InvalidToken("audience mismatch")
    return claims


@dataclass
class OIDCConfig:
    issuer: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> "OIDCConfig":
        return cls(
            issuer=get_secret("OIDC_ISSUER", "") or "",
            client_id=get_secret("OIDC_CLIENT_ID", "") or "",
            client_secret=get_secret("OIDC_CLIENT_SECRET", "") or "",
            authorize_url=get_secret("OIDC_AUTHORIZE_URL", "") or "",
            token_url=get_secret("OIDC_TOKEN_URL", "") or "",
            redirect_uri=get_secret("OIDC_REDIRECT_URI", "") or "",
        )


def build_authorize_url(cfg: OIDCConfig, state: str, nonce: str,
                        scope: str = "openid email profile") -> str:
    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri, "scope": scope,
        "state": state, "nonce": nonce})
    return f"{cfg.authorize_url}?{query}"


def exchange_code(cfg: OIDCConfig, code: str, timeout: int = 30) -> dict:
    """Exchange an authorization code for tokens at the IdP token endpoint (live)."""
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": cfg.redirect_uri, "client_id": cfg.client_id,
        "client_secret": cfg.client_secret}).encode("utf-8")
    req = urllib.request.Request(cfg.token_url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_id_token(id_token: str, cfg: Optional[OIDCConfig] = None,
                    secret: Optional[str] = None) -> dict:
    """Verify an id_token (HS256 via the client secret for the mock IdP) and return
    the authenticated principal {sub, email, claims}."""
    cfg = cfg or OIDCConfig.from_env()
    claims = jwt_decode(id_token, secret or cfg.client_secret,
                        audience=cfg.client_id or None)
    return {"sub": claims.get("sub"), "email": claims.get("email"), "claims": claims}
