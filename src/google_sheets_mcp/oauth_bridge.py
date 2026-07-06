"""Stateless OAuth bridge: this MCP server is an OAuth Authorization Server to
the MCP client (Claude) and an OAuth *client* to Google.

The SDK auto-builds ``/authorize``, ``/token``, ``/register``, ``/revoke`` and
the ``.well-known`` metadata, and routes them to the provider methods here. We
add one custom route — ``/oauth/google/callback`` — for Google's redirect.

No per-user tokens are persisted. Every MCP token we issue is a self-contained
Fernet-encrypted blob carrying the underlying Google token, so each ``load_*``
method is decrypt-and-verify rather than a database lookup. The only durable
secrets are app-level: the Google client secret and the Fernet wrap key(s).

In-flight authorization state is likewise carried in the encrypted ``state``
parameter round-tripped through Google, so the callback needs no server memory.
Dynamic client registrations are kept in process memory (they hold only public
redirect URIs, never tokens) — a single-instance limitation noted in the README.
"""

from __future__ import annotations

import base64
import json
import time

import httpx
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .config import (
    GOOGLE_AUTH_URI,
    GOOGLE_REVOKE_URI,
    GOOGLE_TOKEN_URI,
    GroupConfig,
)

# Google scopes we always request: identity (for per-user audit) + Sheets.
_IDENTITY_SCOPES = ["openid", "email"]

# Lifetimes (seconds).
_STATE_TTL = 600
_CODE_TTL = 600
_CLOCK_SKEW = 60


class WrappedAuthCode(AuthorizationCode):
    """AuthorizationCode plus the Google tokens captured during the callback."""

    g_rt: str
    g_at: str
    g_at_exp: int
    email: str | None = None


class WrappedRefreshToken(RefreshToken):
    """RefreshToken plus the Google refresh token it wraps."""

    g_rt: str
    email: str | None = None


def _b64url_json(segment: str) -> dict:
    """Decode a base64url JWT segment to a dict (best-effort, no verification)."""
    try:
        padded = segment + "=" * (-len(segment) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return {}


class TokenCrypto:
    """Fernet wrap/unwrap with key rotation (MultiFernet: encrypt new, decrypt any)."""

    def __init__(self, keys: list[str]) -> None:
        self._mf = MultiFernet([Fernet(k.encode()) for k in keys])

    def wrap(self, kind: str, payload: dict) -> str:
        body = dict(payload)
        body["t"] = kind
        return self._mf.encrypt(json.dumps(body).encode()).decode()

    def unwrap(self, kind: str, token: str) -> dict | None:
        try:
            data = json.loads(self._mf.decrypt(token.encode()))
        except (InvalidToken, ValueError, TypeError):
            return None
        if data.get("t") != kind:  # an access token must not pass as a refresh token, etc.
            return None
        return data


class GoogleBridgeProvider:
    """Implements the SDK's OAuthAuthorizationServerProvider contract (duck-typed)."""

    def __init__(self, config: GroupConfig) -> None:
        self._cfg = config
        self._crypto = TokenCrypto(config.fernet_keys)
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._http = httpx.AsyncClient(timeout=15.0)

    # -- dynamic client registration (in-memory; public metadata only) ------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    # -- authorize: redirect the user to Google -----------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        scopes = params.scopes or list(self._cfg.scopes)
        state_blob = self._crypto.wrap(
            "st",
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "mcp_state": params.state,
                "scopes": scopes,
                "resource": params.resource,
                "exp": int(time.time()) + _STATE_TTL,
            },
        )
        google_scopes = _IDENTITY_SCOPES + list(self._cfg.scopes)
        query = {
            "client_id": self._cfg.google_client_id,
            "redirect_uri": self._cfg.redirect_uri,
            "response_type": "code",
            "scope": " ".join(dict.fromkeys(google_scopes)),  # dedupe, keep order
            "access_type": "offline",  # request a refresh token
            "prompt": "consent",  # ensure a refresh token is returned
            "include_granted_scopes": "true",
            "state": state_blob,
        }
        return construct_redirect_uri(GOOGLE_AUTH_URI, **query)

    # -- Google callback (custom route) -------------------------------------

    async def handle_google_callback(self, request: Request) -> Response:
        if error := request.query_params.get("error"):
            return JSONResponse({"error": error}, status_code=400)

        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return JSONResponse({"error": "missing code/state"}, status_code=400)

        st = self._crypto.unwrap("st", state)
        if not st or st["exp"] < int(time.time()):
            return JSONResponse({"error": "invalid or expired state"}, status_code=400)

        # Exchange Google's authorization code for tokens.
        try:
            resp = await self._http.post(
                GOOGLE_TOKEN_URI,
                data={
                    "code": code,
                    "client_id": self._cfg.google_client_id,
                    "client_secret": self._cfg.google_client_secret,
                    "redirect_uri": self._cfg.redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": f"google token exchange failed: {exc}"}, status_code=502
            )

        tok = resp.json()
        g_rt = tok.get("refresh_token")
        g_at = tok.get("access_token")
        if not g_rt or not g_at:
            # Without a refresh token the grant can't be sustained — usually means
            # the user previously consented; prompt=consent above should avoid it.
            return JSONResponse(
                {"error": "google did not return a refresh token"}, status_code=400
            )

        claims = _b64url_json(tok.get("id_token", "..").split(".")[1]) if tok.get("id_token") else {}
        now = int(time.time())
        our_code = self._crypto.wrap(
            "ac",
            {
                "client_id": st["client_id"],
                "redirect_uri": st["redirect_uri"],
                "redirect_uri_provided_explicitly": st["redirect_uri_provided_explicitly"],
                "code_challenge": st["code_challenge"],
                "scopes": st["scopes"],
                "resource": st["resource"],
                "subject": claims.get("sub"),
                "email": claims.get("email"),
                "g_rt": g_rt,
                "g_at": g_at,
                "g_at_exp": now + int(tok.get("expires_in", 3600)) - _CLOCK_SKEW,
                "exp": now + _CODE_TTL,
            },
        )
        return RedirectResponse(
            url=construct_redirect_uri(
                st["redirect_uri"], code=our_code, state=st.get("mcp_state")
            ),
            status_code=302,
        )

    # -- authorization code exchange ----------------------------------------

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> WrappedAuthCode | None:
        data = self._crypto.unwrap("ac", authorization_code)
        if not data or data["client_id"] != client.client_id:
            return None
        if data["exp"] < int(time.time()):
            return None
        return WrappedAuthCode(
            code=authorization_code,
            scopes=data["scopes"],
            expires_at=float(data["exp"]),
            client_id=data["client_id"],
            code_challenge=data["code_challenge"],
            redirect_uri=data["redirect_uri"],
            redirect_uri_provided_explicitly=data["redirect_uri_provided_explicitly"],
            resource=data.get("resource"),
            subject=data.get("subject"),
            g_rt=data["g_rt"],
            g_at=data["g_at"],
            g_at_exp=data["g_at_exp"],
            email=data.get("email"),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: WrappedAuthCode
    ) -> OAuthToken:
        return self._issue_tokens(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            subject=authorization_code.subject,
            email=authorization_code.email,
            resource=authorization_code.resource,
            g_rt=authorization_code.g_rt,
            g_at=authorization_code.g_at,
            g_at_exp=authorization_code.g_at_exp,
        )

    # -- refresh token exchange ---------------------------------------------

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> WrappedRefreshToken | None:
        data = self._crypto.unwrap("rt", refresh_token)
        if not data or data["client_id"] != client.client_id:
            return None
        return WrappedRefreshToken(
            token=refresh_token,
            client_id=data["client_id"],
            scopes=data["scopes"],
            subject=data.get("subject"),
            g_rt=data["g_rt"],
            email=data.get("email"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: WrappedRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        try:
            resp = await self._http.post(
                GOOGLE_TOKEN_URI,
                data={
                    "client_id": self._cfg.google_client_id,
                    "client_secret": self._cfg.google_client_secret,
                    "refresh_token": refresh_token.g_rt,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TokenError("invalid_grant", f"google refresh failed: {exc}") from None

        tok = resp.json()
        now = int(time.time())
        requested = scopes or refresh_token.scopes
        return self._issue_tokens(
            client_id=client.client_id,
            scopes=requested,
            subject=refresh_token.subject,
            email=refresh_token.email,
            resource=None,
            # Google usually omits a new refresh token on refresh — reuse the old.
            g_rt=tok.get("refresh_token") or refresh_token.g_rt,
            g_at=tok["access_token"],
            g_at_exp=now + int(tok.get("expires_in", 3600)) - _CLOCK_SKEW,
        )

    # -- access token verification (called on every MCP request) ------------

    async def load_access_token(self, token: str) -> AccessToken | None:
        data = self._crypto.unwrap("at", token)
        if not data:
            return None
        if data["expires_at"] < int(time.time()):
            return None
        return AccessToken(
            token=token,
            client_id=data["client_id"],
            scopes=data["scopes"],
            expires_at=data["expires_at"],
            resource=data.get("resource"),
            subject=data.get("subject"),
            # The Google access token rides in claims for per-request Sheets calls.
            claims={"g_at": data["g_at"], "email": data.get("email")},
        )

    async def revoke_token(self, token) -> None:
        # Best-effort: revoke the underlying Google grant via its refresh token.
        g_rt = getattr(token, "g_rt", None)
        if not g_rt:
            return
        try:
            await self._http.post(GOOGLE_REVOKE_URI, data={"token": g_rt})
        except httpx.HTTPError:
            pass

    # -- helpers ------------------------------------------------------------

    def _issue_tokens(
        self,
        *,
        client_id: str,
        scopes: list[str],
        subject: str | None,
        email: str | None,
        resource: str | None,
        g_rt: str,
        g_at: str,
        g_at_exp: int,
    ) -> OAuthToken:
        access = self._crypto.wrap(
            "at",
            {
                "client_id": client_id,
                "scopes": scopes,
                "subject": subject,
                "email": email,
                "resource": resource,
                "g_at": g_at,
                "expires_at": g_at_exp,
            },
        )
        refresh = self._crypto.wrap(
            "rt",
            {
                "client_id": client_id,
                "scopes": scopes,
                "subject": subject,
                "email": email,
                "g_rt": g_rt,
            },
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=max(1, g_at_exp - int(time.time())),
            scope=" ".join(scopes),
            refresh_token=refresh,
        )


def register_callback(mcp, provider: GoogleBridgeProvider) -> None:
    """Register the Google OAuth callback route on the FastMCP server."""

    @mcp.custom_route("/oauth/google/callback", methods=["GET"])
    async def google_callback(request: Request) -> Response:  # noqa: ANN001
        return await provider.handle_google_callback(request)
