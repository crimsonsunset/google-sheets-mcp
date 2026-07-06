"""Runtime configuration for the two deployment modes.

* ``local``  (default) — single user, stdio, on-disk token. See ``auth.py``.
* ``group``  — centrally hosted, Streamable HTTP, per-user Google OAuth via the
  stateless bridge in ``oauth_bridge.py``. The server persists no user tokens;
  it holds only two app-level secrets (Google client secret + Fernet wrap key).

Group-mode settings are read from environment variables:

  GSHEETS_MCP_MODE           ``local`` (default) or ``group``
  GSHEETS_PUBLIC_URL         public https base URL, e.g. https://sheets.example.com
  GSHEETS_GOOGLE_CLIENT_SECRETS  path to a Google OAuth *Web* client JSON, OR
  GSHEETS_GOOGLE_CLIENT_ID / GSHEETS_GOOGLE_CLIENT_SECRET
  GSHEETS_FERNET_KEYS        comma-separated Fernet keys; first = primary (encrypt),
                             rest accepted for decrypt (key rotation). Or
  GSHEETS_FERNET_KEY         a single key.
  GSHEETS_HOST               bind host (default 0.0.0.0)
  GSHEETS_PORT               bind port (default 8000)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .auth import SCOPES

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URI = "https://oauth2.googleapis.com/revoke"
GOOGLE_TOKENINFO_URI = "https://oauth2.googleapis.com/tokeninfo"


def mode() -> str:
    """Return the active deployment mode: ``local`` or ``group``."""
    return os.environ.get("GSHEETS_MCP_MODE", "local").strip().lower()


@dataclass(frozen=True)
class GroupConfig:
    public_url: str
    google_client_id: str
    google_client_secret: str
    fernet_keys: list[str]
    host: str
    port: int
    scopes: list[str]

    @property
    def issuer_url(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def redirect_uri(self) -> str:
        """The Google callback URL — must be registered on the Google client."""
        return self.issuer_url + "/oauth/google/callback"


def _load_google_client() -> tuple[str, str]:
    """Resolve the Google OAuth client id/secret from env or a secrets JSON file."""
    secrets_path = os.environ.get("GSHEETS_GOOGLE_CLIENT_SECRETS")
    if secrets_path:
        data = json.loads(Path(secrets_path).expanduser().read_text())
        # Google downloads wrap the client under a "web" (or "installed") key.
        node = data.get("web") or data.get("installed") or data
        cid = node.get("client_id")
        secret = node.get("client_secret")
        if not cid or not secret:
            raise RuntimeError(
                f"{secrets_path} is missing client_id/client_secret. "
                "Use a Google OAuth 'Web application' client JSON."
            )
        return cid, secret

    cid = os.environ.get("GSHEETS_GOOGLE_CLIENT_ID")
    secret = os.environ.get("GSHEETS_GOOGLE_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError(
            "Group mode needs Google OAuth client credentials. Set "
            "GSHEETS_GOOGLE_CLIENT_SECRETS to a Web-client JSON path, or set "
            "GSHEETS_GOOGLE_CLIENT_ID and GSHEETS_GOOGLE_CLIENT_SECRET."
        )
    return cid, secret


def _load_fernet_keys() -> list[str]:
    raw = os.environ.get("GSHEETS_FERNET_KEYS") or os.environ.get("GSHEETS_FERNET_KEY")
    if not raw:
        raise RuntimeError(
            "Group mode needs a Fernet wrap key. Generate one with "
            "`google-sheets-mcp genkey` and set GSHEETS_FERNET_KEY (or "
            "GSHEETS_FERNET_KEYS=new,old for rotation)."
        )
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("GSHEETS_FERNET_KEYS is empty.")
    return keys


def load_group_config() -> GroupConfig:
    """Build the group-mode config from the environment, or raise RuntimeError."""
    public_url = os.environ.get("GSHEETS_PUBLIC_URL")
    if not public_url:
        raise RuntimeError(
            "Group mode needs GSHEETS_PUBLIC_URL (the public https base URL of "
            "this server, e.g. https://sheets.example.com)."
        )
    if not public_url.startswith("https://") and "localhost" not in public_url:
        raise RuntimeError(
            "GSHEETS_PUBLIC_URL must be https:// (bearer tokens travel in headers). "
            "Use localhost only for local testing."
        )

    cid, secret = _load_google_client()
    return GroupConfig(
        public_url=public_url,
        google_client_id=cid,
        google_client_secret=secret,
        fernet_keys=_load_fernet_keys(),
        host=os.environ.get("GSHEETS_HOST", "0.0.0.0"),
        port=int(os.environ.get("GSHEETS_PORT", "8000")),
        scopes=list(SCOPES),
    )
