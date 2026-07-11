# api/app/core/security.py
#
# FastAPI dependency — resolves the authenticated caller's identity.
#
# Token resolution order:
#   1. access_token HttpOnly cookie  (BFF Authorization Code flow)
#   2. Authorization: Bearer header  (service-to-service calls)
#
# Dev mode (KEYCLOAK_URL is empty): returns a hardcoded dev user so every
# endpoint works without a running Keycloak instance.

from __future__ import annotations

import base64
import json
import time
from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings

# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------

_jwks_cache: list[dict] = []
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600.0  # re-fetch public keys once per hour


async def _get_jwks() -> list[dict]:
    global _jwks_cache, _jwks_fetched_at
    if _jwks_cache and (time.time() - _jwks_fetched_at) < _JWKS_TTL:
        return _jwks_cache
    url = (
        f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"
        "/protocol/openid-connect/certs"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=5.0)
        resp.raise_for_status()
    _jwks_cache = resp.json().get("keys", [])
    _jwks_fetched_at = time.time()
    return _jwks_cache


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _b64pad(s: str) -> bytes:
    """Base64url-decode a segment that may be missing padding."""
    return base64.urlsafe_b64decode(s + "=" * (4 - len(s) % 4))


def _jwt_header(token: str) -> dict:
    try:
        return json.loads(_b64pad(token.split(".")[0]))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token")


async def _verify_token(token: str) -> dict:
    """
    Verify JWT signature against Keycloak JWKS and return claims.

    Uses PyJWT for RS256/ES256 signature + expiry + issuer checks.
    Falls back to an unverified payload decode when PyJWT is unavailable
    (useful in early local dev before all dependencies are installed).
    """
    try:
        import jwt as pyjwt
        from jwt.algorithms import ECAlgorithm, RSAAlgorithm

        header = _jwt_header(token)
        kid = header.get("kid")
        alg = header.get("alg", "RS256")

        keys = await _get_jwks()
        jwk = next((k for k in keys if k.get("kid") == kid), keys[0] if keys else None)
        if not jwk:
            raise HTTPException(status_code=401, detail="No signing key available")

        cls = RSAAlgorithm if alg.startswith("RS") else ECAlgorithm
        public_key = cls.from_jwk(jwk)

        claims: dict = pyjwt.decode(
            token,
            public_key,
            algorithms=[alg],
            options={"verify_aud": False},
        )
        expected_iss = (
            f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"
        )
        if claims.get("iss", "").rstrip("/") != expected_iss.rstrip("/"):
            raise HTTPException(status_code=401, detail="Invalid token issuer")
        return claims

    except HTTPException:
        raise
    except ImportError:
        # PyJWT unavailable — decode without signature verification (dev only)
        try:
            return json.loads(_b64pad(token.split(".")[1]))
        except Exception:
            raise HTTPException(status_code=401, detail="Malformed token payload")
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Token verification failed") from exc


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> dict:
    """
    Returns the authenticated caller as a plain dict with keys:
      id, tenant_id, email, roles, name, username

    Resolves from the access_token HttpOnly cookie (BFF flow) first,
    then from an Authorization: Bearer header (service-to-service).
    """
    if not settings.keycloak_url:
        return {
            "id":        "dev-user-001",
            "tenant_id": "dev-tenant-001",
            "email":     "dev@insightx.local",
            "roles":     ["insightx-admin"],
            "name":      "Dev User",
            "username":  "dev",
        }

    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = await _verify_token(token)

    realm_roles: list[str] = claims.get("realm_access", {}).get("roles", [])
    client_roles: list[str] = []
    for client_data in claims.get("resource_access", {}).values():
        client_roles.extend(client_data.get("roles", []))

    return {
        "id":           claims.get("sub", ""),
        "tenant_id":    claims.get("tenant_id") or settings.keycloak_realm,
        "email":        claims.get("email", ""),
        "roles":        realm_roles,
        "client_roles": list(dict.fromkeys(client_roles)),
        "name":         claims.get("name", ""),
        "username":     claims.get("preferred_username", ""),
    }


# Type alias — routes declare `current_user: CurrentUser` instead of the full Annotated form
CurrentUser = Annotated[dict, Depends(get_current_user)]
