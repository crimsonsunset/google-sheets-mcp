"""Tests for the stateless group-mode OAuth bridge.

These exercise the token crypto, the provider's token lifecycle, and the
per-request client resolution — all without touching the network (Google calls
are monkeypatched).
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet

from google_sheets_mcp.config import GroupConfig
from google_sheets_mcp.oauth_bridge import GoogleBridgeProvider, TokenCrypto

from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _key() -> str:
    return Fernet.generate_key().decode()


def _config(keys=None) -> GroupConfig:
    return GroupConfig(
        public_url="http://localhost:8000",
        google_client_id="cid.apps.googleusercontent.com",
        google_client_secret="sec",
        fernet_keys=keys or [_key()],
        host="0.0.0.0",
        port=8000,
        scopes=list(SCOPES),
    )


def _client() -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id="c1", redirect_uris=["https://claude.ai/api/mcp/auth_callback"]
    )


# -- TokenCrypto -------------------------------------------------------------


def test_crypto_roundtrip_and_tag():
    c = TokenCrypto([_key()])
    blob = c.wrap("at", {"g_at": "tok", "x": 1})
    assert c.unwrap("at", blob) == {"g_at": "tok", "x": 1, "t": "at"}


def test_crypto_tag_isolation():
    c = TokenCrypto([_key()])
    blob = c.wrap("at", {"g_at": "tok"})
    assert c.unwrap("rt", blob) is None  # access token must not pass as refresh


def test_crypto_tamper_rejected():
    c = TokenCrypto([_key()])
    blob = c.wrap("at", {"g_at": "tok"})
    assert c.unwrap("at", blob[:-3] + "AAA") is None


def test_crypto_key_rotation():
    old = _key()
    new = _key()
    old_blob = TokenCrypto([old]).wrap("rt", {"g_rt": "r"})
    # New primary, old still accepted for decrypt.
    rotated = TokenCrypto([new, old])
    assert rotated.unwrap("rt", old_blob) == {"g_rt": "r", "t": "rt"}
    # A token from a totally unknown key fails.
    assert rotated.unwrap("rt", TokenCrypto([_key()]).wrap("rt", {"g_rt": "r"})) is None


# -- authorize ---------------------------------------------------------------


def test_authorize_builds_google_url_with_encrypted_state():
    p = GoogleBridgeProvider(_config())
    params = AuthorizationParams(
        state="mcp-state",
        scopes=SCOPES,
        code_challenge="challenge123",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        redirect_uri_provided_explicitly=True,
        resource=None,
    )
    url = asyncio.run(p.authorize(_client(), params))
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    assert parsed.netloc == "accounts.google.com"
    assert q["client_id"] == "cid.apps.googleusercontent.com"
    assert q["redirect_uri"] == "http://localhost:8000/oauth/google/callback"
    assert q["access_type"] == "offline"
    assert "spreadsheets" in q["scope"]

    st = p._crypto.unwrap("st", q["state"])
    assert st["client_id"] == "c1"
    assert st["code_challenge"] == "challenge123"
    assert st["mcp_state"] == "mcp-state"


# -- token issuance + verification ------------------------------------------


def test_issue_then_load_access_token():
    p = GoogleBridgeProvider(_config())
    now = int(time.time())
    tok = p._issue_tokens(
        client_id="c1",
        scopes=SCOPES,
        subject="user-sub",
        email="u@example.com",
        resource=None,
        g_rt="google-refresh",
        g_at="google-access",
        g_at_exp=now + 3000,
    )
    assert tok.token_type == "Bearer"
    assert tok.refresh_token

    at = asyncio.run(p.load_access_token(tok.access_token))
    assert at is not None
    assert at.subject == "user-sub"
    assert at.claims["g_at"] == "google-access"  # rides into the per-request client
    assert at.claims["email"] == "u@example.com"

    rt = asyncio.run(p.load_refresh_token(_client(), tok.refresh_token))
    assert rt is not None
    assert rt.g_rt == "google-refresh"


def test_load_access_token_expired():
    p = GoogleBridgeProvider(_config())
    tok = p._issue_tokens(
        client_id="c1", scopes=SCOPES, subject="s", email=None, resource=None,
        g_rt="r", g_at="a", g_at_exp=int(time.time()) - 5,
    )
    assert asyncio.run(p.load_access_token(tok.access_token)) is None


def test_load_refresh_token_wrong_client():
    p = GoogleBridgeProvider(_config())
    tok = p._issue_tokens(
        client_id="c1", scopes=SCOPES, subject="s", email=None, resource=None,
        g_rt="r", g_at="a", g_at_exp=int(time.time()) + 100,
    )
    other = OAuthClientInformationFull(client_id="other", redirect_uris=["https://x/cb"])
    assert asyncio.run(p.load_refresh_token(other, tok.refresh_token)) is None


# -- authorization code ------------------------------------------------------


def test_load_authorization_code_roundtrip_and_guards():
    p = GoogleBridgeProvider(_config())
    now = int(time.time())
    code = p._crypto.wrap(
        "ac",
        {
            "client_id": "c1",
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "redirect_uri_provided_explicitly": True,
            "code_challenge": "chal",
            "scopes": SCOPES,
            "resource": None,
            "subject": "sub",
            "email": "u@example.com",
            "g_rt": "r",
            "g_at": "a",
            "g_at_exp": now + 1000,
            "exp": now + 300,
        },
    )
    ac = asyncio.run(p.load_authorization_code(_client(), code))
    assert ac is not None
    assert ac.code_challenge == "chal"
    assert ac.g_rt == "r"

    # wrong client rejected
    other = OAuthClientInformationFull(client_id="other", redirect_uris=["https://x/cb"])
    assert asyncio.run(p.load_authorization_code(other, code)) is None


def test_load_authorization_code_expired():
    p = GoogleBridgeProvider(_config())
    now = int(time.time())
    code = p._crypto.wrap(
        "ac",
        {
            "client_id": "c1", "redirect_uri": "https://claude.ai/cb",
            "redirect_uri_provided_explicitly": True, "code_challenge": "chal",
            "scopes": SCOPES, "resource": None, "subject": "sub", "email": None,
            "g_rt": "r", "g_at": "a", "g_at_exp": now + 1000, "exp": now - 1,
        },
    )
    assert asyncio.run(p.load_authorization_code(_client(), code)) is None


# -- refresh exchange (Google mocked) ---------------------------------------


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeHTTP:
    def __init__(self, data):
        self._data = data
        self.calls = []

    async def post(self, url, data=None):
        self.calls.append((url, data))
        return _FakeResp(self._data)


def test_exchange_refresh_token_mints_new_access(monkeypatch):
    p = GoogleBridgeProvider(_config())
    p._http = _FakeHTTP({"access_token": "new-google-access", "expires_in": 3600})

    rt = asyncio.run(
        p.load_refresh_token(
            _client(),
            p._crypto.wrap(
                "rt",
                {"client_id": "c1", "scopes": SCOPES, "subject": "s",
                 "email": "u@example.com", "g_rt": "google-refresh"},
            ),
        )
    )
    new_tok = asyncio.run(p.exchange_refresh_token(_client(), rt, SCOPES))
    # the Google refresh call happened with our refresh token
    assert p._http.calls[0][1]["grant_type"] == "refresh_token"
    assert p._http.calls[0][1]["refresh_token"] == "google-refresh"
    # new access token decodes to the fresh Google access token
    at = asyncio.run(p.load_access_token(new_tok.access_token))
    assert at.claims["g_at"] == "new-google-access"


# -- per-request client resolution ------------------------------------------


def _set_group_env(monkeypatch):
    """Full group-mode env so importing the server builds (no network needed)."""
    monkeypatch.setenv("GSHEETS_MCP_MODE", "group")
    monkeypatch.setenv("GSHEETS_PUBLIC_URL", "http://localhost:8000")
    monkeypatch.setenv("GSHEETS_GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GSHEETS_GOOGLE_CLIENT_SECRET", "sec")
    monkeypatch.setenv("GSHEETS_FERNET_KEY", _key())


def test_current_client_group_reads_token(monkeypatch):
    _set_group_env(monkeypatch)
    from mcp.server.auth.middleware.auth_context import (
        AuthenticatedUser,
        auth_context_var,
    )
    from mcp.server.auth.provider import AccessToken

    import google_sheets_mcp.server as server

    at = AccessToken(
        token="mcp-at", client_id="c1", scopes=SCOPES,
        expires_at=int(time.time()) + 100, claims={"g_at": "GOOGLE_ACCESS", "email": None},
    )
    token = auth_context_var.set(AuthenticatedUser(at))
    try:
        client = server.current_client()
        assert client._credentials.token == "GOOGLE_ACCESS"
    finally:
        auth_context_var.reset(token)


def test_current_client_group_requires_token(monkeypatch):
    _set_group_env(monkeypatch)
    import google_sheets_mcp.server as server

    with pytest.raises(RuntimeError):
        server.current_client()
