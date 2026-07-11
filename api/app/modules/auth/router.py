# api/app/modules/auth/router.py
#
# BFF (Backend-for-Frontend) Authorization Code + PKCE flow with Keycloak.
#
# The backend drives the entire OAuth dance:
#   GET  /auth/login     → redirect browser to Keycloak login page (PKCE S256)
#   GET  /auth/callback  → exchange code + PKCE verifier for tokens, set HttpOnly cookies
#   GET  /auth/me        → return caller identity decoded from cookies
#   POST /auth/refresh   → exchange refresh_token cookie for a fresh access_token cookie
#   GET  /auth/logout    → revoke session in Keycloak, clear all auth cookies
#
# All tokens live exclusively in HttpOnly, SameSite=Lax cookies.
# JavaScript never touches the raw token strings.

import base64
import hashlib
import json
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import settings

router = APIRouter()

# ---------------------------------------------------------------------------
# Cookie name constants
# ---------------------------------------------------------------------------

_STATE_COOKIE   = "oauth_state"
_PKCE_COOKIE    = "pkce_verifier"
_ACCESS_COOKIE  = "access_token"
_ID_COOKIE      = "id_token"
_REFRESH_COOKIE = "refresh_token"
_STATE_TTL      = 300   # state + PKCE cookies live 5 min — long enough for a login


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _kc_oidc_base() -> str:
    return (
        f"{settings.keycloak_url.rstrip('/')}/realms/{settings.keycloak_realm}"
        "/protocol/openid-connect"
    )


def _auth_cookie_kwargs(max_age: int | None = None) -> dict:
    """Common attributes shared by all long-lived auth cookies."""
    kw: dict = {
        "httponly": True,
        "secure":   settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "path":     "/",
    }
    if max_age is not None:
        kw["max_age"] = max_age
    return kw


def _set_token_cookies(response: Response, tokens: dict) -> None:
    """Write access_token, id_token, and refresh_token into HttpOnly cookies."""
    response.set_cookie(
        _ACCESS_COOKIE,
        tokens["access_token"],
        max_age=tokens.get("expires_in", 300),
        **_auth_cookie_kwargs(),
    )
    if "id_token" in tokens:
        response.set_cookie(
            _ID_COOKIE,
            tokens["id_token"],
            max_age=tokens.get("expires_in", 300),
            **_auth_cookie_kwargs(),
        )
    if "refresh_token" in tokens:
        response.set_cookie(
            _REFRESH_COOKIE,
            tokens["refresh_token"],
            max_age=tokens.get("refresh_expires_in", 1800),
            **_auth_cookie_kwargs(),
        )


def _clear_token_cookies(response: Response) -> None:
    for name in (_ACCESS_COOKIE, _ID_COOKIE, _REFRESH_COOKIE):
        response.delete_cookie(
            name,
            path="/",
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
        )


def _decode_jwt_payload(token: str) -> dict:
    """Decode the payload segment of a JWT without verifying the signature.

    Used only for reading identity claims from tokens that were already
    verified server-side by Keycloak during the code exchange.
    """
    try:
        raw = token.split(".")[1]
        raw += "=" * (4 - len(raw) % 4)   # restore base64 padding
        return json.loads(base64.b64decode(raw))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or malformed token")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/login", summary="Redirect browser to Keycloak login page (PKCE S256)")
async def login() -> RedirectResponse:
    """
    Step 1 of the Authorization Code + PKCE flow.

    Generates:
      - state  : random token stored in a short-lived HttpOnly cookie (CSRF protection)
      - code_verifier  : random secret stored in a short-lived HttpOnly cookie
      - code_challenge : SHA-256(code_verifier) sent to Keycloak

    Keycloak will echo `state` back in the callback URL so we can verify it.
    Keycloak will verify the `code_challenge` when we submit the `code_verifier`
    in the token exchange — proving both requests came from the same client.

    Dev mode (KEYCLOAK_URL empty): skips Keycloak and redirects straight to
    the frontend with a placeholder cookie so local dev works without SSO.
    """
    if not settings.keycloak_url:
        response = RedirectResponse(settings.frontend_url, status_code=302)
        response.set_cookie(_ACCESS_COOKIE, "dev-bypass", max_age=86400, **_auth_cookie_kwargs())
        print("⚠️  Dev mode: skipping Keycloak login, setting dev-bypass cookie")
        return response

    state = secrets.token_urlsafe(32)

    # PKCE S256: verifier is random bytes (base64url-encoded); challenge is its SHA-256 hash
    code_verifier  = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")

    params = {
        "response_type":         "code",
        "client_id":             settings.keycloak_client_id,
        "redirect_uri":          settings.redirect_uri,
        "scope":                 "openid profile email",
        "state":                 state,
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{_kc_oidc_base()}/auth?{urlencode(params)}"
    response = RedirectResponse(auth_url, status_code=302)

    # Scope temporary cookies to /api/auth so they're not sent on every API request.
    # The router is mounted at /api/auth, so path must start with /api/auth
    # for the browser to include these cookies on the /api/auth/callback request.
    response.set_cookie(
        _STATE_COOKIE, state, max_age=_STATE_TTL,
        httponly=True, secure=settings.cookie_secure,
        samesite=settings.cookie_samesite, path="/api/auth",
    )
    response.set_cookie(
        _PKCE_COOKIE, code_verifier, max_age=_STATE_TTL,
        httponly=True, secure=settings.cookie_secure,
        samesite=settings.cookie_samesite, path="/api/auth",
    )

    print(f"Redirecting to Keycloak login page: {auth_url}")
    return response


@router.get("/callback", summary="OAuth2 callback — exchange code + PKCE verifier for tokens")
async def callback(
    code:          str       = Query(..., description="Authorization code from Keycloak"),
    state:         str       = Query(..., description="State parameter for CSRF validation"),
    error:         str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    stored_state:  str | None = Cookie(default=None, alias="oauth_state"),
    code_verifier: str | None = Cookie(default=None, alias="pkce_verifier"),
) -> RedirectResponse:
    """
    Step 2 of the Authorization Code + PKCE flow.

    Keycloak redirects the browser here with ?code=<code>&state=<state>.
    1. Validates the state cookie (CSRF check).
    2. Validates the PKCE verifier cookie is present.
    3. Exchanges the code + verifier for tokens (server-to-server call).
    4. Stores access_token, id_token, and refresh_token in HttpOnly cookies.
    5. Clears the temporary state and PKCE cookies.
    6. Redirects to the frontend application.
    """
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Keycloak error: {error} — {error_description}",
        )

    if not stored_state or stored_state != state:
        raise HTTPException(
            status_code=400,
            detail="Invalid or missing OAuth state — possible CSRF attempt",
        )

    if not code_verifier:
        raise HTTPException(
            status_code=400,
            detail="PKCE verifier missing — please try logging in again",
        )
    
    print(f"Exchanging code for tokens with Keycloak (code={code}, state={state})")

    data = {
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  settings.redirect_uri,
        "client_id":     settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
        "code_verifier": code_verifier,   # proves this is the same client that started the flow
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_kc_oidc_base()}/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0,
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Keycloak token exchange failed: {resp.text}",
        )

    tokens   = resp.json()
    response = RedirectResponse(settings.frontend_url, status_code=302)
    response.delete_cookie(_STATE_COOKIE, path="/api/auth")
    response.delete_cookie(_PKCE_COOKIE,  path="/api/auth")
    _set_token_cookies(response, tokens)
    return response


@router.get("/me", summary="Return the current user's identity from token cookies")
async def get_me(request: Request) -> JSONResponse:
    """
    Decodes the id_token cookie for identity claims (sub, email, name) and
    reads roles from the access_token cookie (both realm and client roles).

    No Bearer header required — tokens are read from HttpOnly cookies set
    by /callback. This endpoint is for the frontend to populate its user
    context immediately after login without an extra Keycloak call.

    Dev mode (KEYCLOAK_URL empty): returns a hardcoded dev identity so
    local development works without a running Keycloak instance.
    """
    if not settings.keycloak_url:
        return JSONResponse({
            "sub":        "dev-user-001",
            "username":   "dev",
            "email":      "dev@insightx.local",
            "name":       "Dev User",
            "given_name": "Dev",
            "roles":      ["insightx-admin"],
            "tenant_id":  "dev-tenant-001",
        })

    id_token     = request.cookies.get(_ID_COOKIE)
    access_token = request.cookies.get(_ACCESS_COOKIE)

    if not id_token or not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    id_payload     = _decode_jwt_payload(id_token)
    access_payload = _decode_jwt_payload(access_token)

    # Collect roles from both realm_access (realm-level) and resource_access (client-level).
    # Keycloak places roles differently depending on admin configuration; reading both
    # ensures the app works regardless.
    # Realm-level roles (e.g. insightx-admin)
    realm_roles = access_payload.get("realm_access", {}).get("roles", [])

    # Client-level roles — collect from ALL clients in resource_access so that
    # feat:* roles defined on the frontend client (e.g. "InsightX") are included
    # even if keycloak_client_id points to a different backend client.
    client_roles: list[str] = []
    for client_data in access_payload.get("resource_access", {}).values():
        client_roles.extend(client_data.get("roles", []))

    all_roles = list(dict.fromkeys(realm_roles + client_roles))  # dedup, preserve order

    return JSONResponse({
        "sub":        id_payload.get("sub"),
        "username":   id_payload.get("preferred_username"),
        "email":      id_payload.get("email"),
        "name":       id_payload.get("name"),
        "given_name": id_payload.get("given_name"),
        "roles":      all_roles,
        "tenant_id":  access_payload.get("tenant_id") or settings.keycloak_realm,
        "exp":        id_payload.get("exp"),
    })


@router.post("/refresh", summary="Rotate access_token using refresh_token cookie")
async def refresh_token(
    response: Response,
    token:    str | None = Cookie(default=None, alias="refresh_token"),
) -> dict:
    """
    Exchanges the refresh_token cookie for a fresh set of tokens and
    rotates all three auth cookies in-place.

    Returns 401 if the refresh token is missing, expired, or revoked.
    The frontend should redirect to /auth/login on 401.
    """
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token")

    data = {
        "grant_type":    "refresh_token",
        "refresh_token": token,
        "client_id":     settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_kc_oidc_base()}/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0,
        )

    if resp.status_code != 200:
        _clear_token_cookies(response)
        raise HTTPException(status_code=401, detail="Refresh token expired or revoked")

    _set_token_cookies(response, resp.json())
    return {"ok": True}


@router.get("/logout", summary="Terminate the Keycloak session and clear all auth cookies")
async def logout(request: Request) -> RedirectResponse:
    """
    1. Calls Keycloak's RP-initiated logout endpoint to invalidate the session.
    2. Clears access_token, id_token, and refresh_token cookies.
    3. Redirects the browser to the frontend login page.

    id_token_hint tells Keycloak exactly which SSO session to end — without
    it, the user could immediately re-authenticate via the still-active session
    without being prompted to log in again.

    Keycloak errors are intentionally swallowed: cookies are always cleared
    even if the server-side revocation fails.
    """
    refresh_token_val = request.cookies.get(_REFRESH_COOKIE)
    id_token_val      = request.cookies.get(_ID_COOKIE)

    if refresh_token_val and settings.keycloak_url:
        logout_data: dict = {
            "client_id":     settings.keycloak_client_id,
            "client_secret": settings.keycloak_client_secret,
            "refresh_token": refresh_token_val,
        }
        if id_token_val:
            logout_data["id_token_hint"] = id_token_val

        async with httpx.AsyncClient() as client:
            await client.post(
                f"{_kc_oidc_base()}/logout",
                data=logout_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=5.0,
            )

    response = RedirectResponse(url=f"{settings.frontend_url}/login", status_code=302)
    _clear_token_cookies(response)
    return response
