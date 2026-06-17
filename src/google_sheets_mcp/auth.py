"""OAuth 2.0 (user account) credential handling for the Google Sheets MCP.

The first time the server needs credentials it runs a local browser flow against
an OAuth *Desktop app* client (``credentials.json``) and caches the resulting
token (``token.json``). Subsequent runs reuse and silently refresh the token.

File locations (override via env vars):
  * config dir:   ``$GOOGLE_SHEETS_MCP_HOME`` or ``~/.config/google-sheets-mcp``
  * client secret: ``$GOOGLE_SHEETS_CREDENTIALS`` or ``<config>/credentials.json``
  * token cache:   ``$GOOGLE_SHEETS_TOKEN`` or ``<config>/token.json``
"""

from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# spreadsheets: full read/write + formatting on any sheet the user authorizes,
# and creating new spreadsheets.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def config_dir() -> Path:
    base = os.environ.get("GOOGLE_SHEETS_MCP_HOME")
    path = Path(base).expanduser() if base else Path.home() / ".config" / "google-sheets-mcp"
    # 0o700: only the owning user can read/list/enter this directory.
    # mkdir's mode arg is filtered by the umask, so force it with chmod.
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _credentials_path() -> Path:
    override = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    return Path(override).expanduser() if override else config_dir() / "credentials.json"


def _token_path() -> Path:
    override = os.environ.get("GOOGLE_SHEETS_TOKEN")
    return Path(override).expanduser() if override else config_dir() / "token.json"


def _write_token(path: Path, json_str: str) -> None:
    """Write token JSON with mode 0o600 (owner read/write only)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json_str)


def get_credentials(*, allow_interactive: bool = True) -> Credentials:
    """Return valid user credentials, running the OAuth flow if necessary.

    Set ``allow_interactive=False`` to forbid opening a browser — useful in
    headless contexts where you only want to reuse/refresh an existing token.
    """
    token_path = _token_path()
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _write_token(token_path, creds.to_json())
        return creds

    if not allow_interactive:
        raise RuntimeError(
            "No valid cached token and interactive auth disabled. "
            "Run `google-sheets-mcp auth` first."
        )

    creds_path = _credentials_path()
    if not creds_path.exists():
        raise RuntimeError(
            "OAuth client secret not found. "
            "Set $GOOGLE_SHEETS_CREDENTIALS or place credentials.json in the config "
            "directory ($GOOGLE_SHEETS_MCP_HOME or ~/.config/google-sheets-mcp/). "
            "See the README for setup steps."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)
    _write_token(token_path, creds.to_json())
    return creds
