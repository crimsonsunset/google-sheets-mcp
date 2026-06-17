"""CLI entry point.

Usage:
  google-sheets-mcp           Run the MCP server over stdio (default).
  google-sheets-mcp auth      Run the OAuth flow once and cache the token.
"""

from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]

    if args and args[0] == "auth":
        from .auth import config_dir, get_credentials

        try:
            get_credentials(allow_interactive=True)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print("Authenticated. Token cached in the config directory.")
        return

    if args and args[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return

    from .server import main as run_server

    run_server()


if __name__ == "__main__":
    main()
