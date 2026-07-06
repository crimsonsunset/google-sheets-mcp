"""CLI entry point.

Usage:
  google-sheets-mcp                  Run the server (mode from $GSHEETS_MCP_MODE,
                                     default: local stdio).
  google-sheets-mcp serve [--mode local|group]
                                     Run the server, optionally forcing the mode.
  google-sheets-mcp auth             (local) Run the OAuth flow once and cache the token.
  google-sheets-mcp genkey           Print a fresh Fernet wrap key for group mode.
"""

from __future__ import annotations

import os
import sys


def _run_server() -> None:
    # server reads the mode at import time, so $GSHEETS_MCP_MODE must be set first.
    from .server import main as run_server

    run_server()


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else None

    if cmd in {"-h", "--help", "help"}:
        print(__doc__)
        return

    if cmd == "genkey":
        from cryptography.fernet import Fernet

        print(Fernet.generate_key().decode())
        return

    if cmd == "auth":
        from .auth import config_dir, get_credentials

        try:
            get_credentials(allow_interactive=True)
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1)
        print(f"Authenticated. Token cached under {config_dir()}.")
        return

    if cmd == "serve":
        if "--mode" in args:
            i = args.index("--mode")
            try:
                requested = args[i + 1]
            except IndexError:
                print("Error: --mode needs a value (local|group)", file=sys.stderr)
                raise SystemExit(1)
            if requested not in {"local", "group"}:
                print("Error: --mode must be 'local' or 'group'", file=sys.stderr)
                raise SystemExit(1)
            os.environ["GSHEETS_MCP_MODE"] = requested
        _run_server()
        return

    if cmd is not None:
        print(f"Unknown command: {cmd!r}\n", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        raise SystemExit(1)

    _run_server()


if __name__ == "__main__":
    main()
