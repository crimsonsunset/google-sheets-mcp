"""Reference resolution for natural inputs.

Users paste spreadsheet **URLs**, name sheets loosely, and refer to ranges the
way they think ("the Revenue column", "last row", "top 10 rows"). This module
turns those into the concrete IDs and A1 ranges the low-level tools expect.

The pure classifier (`parse_spreadsheet_ref`, `classify_range_ref`) is separated
from the I/O (`resolve_range`) so the parsing logic is unit-testable without a
network client.
"""

from __future__ import annotations

import re
from typing import Any

from .a1 import col_index_to_letter, col_letter_to_index

# https://docs.google.com/spreadsheets/d/<ID>/edit#gid=123
_URL_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")
_GID_RE = re.compile(r"[#&?]gid=(\d+)")

# An A1-ish cell/range: A1, A1:C10, A:C (column range), 2:5 (row range).
_A1_ENDPOINT = r"(?:\$?[A-Za-z]+\$?\d*|\$?\d+)"
_A1_RE = re.compile(rf"^{_A1_ENDPOINT}(:{_A1_ENDPOINT})?$")

_WHOLE_SHEET = {
    "whole sheet",
    "the whole sheet",
    "entire sheet",
    "the entire sheet",
    "all",
    "everything",
}


def parse_spreadsheet_ref(ref: str) -> tuple[str, int | None]:
    """Return ``(spreadsheet_id, gid_or_None)`` from a URL or a bare ID."""
    ref = ref.strip()
    gid_match = _GID_RE.search(ref)
    gid = int(gid_match.group(1)) if gid_match else None
    url_match = _URL_ID_RE.search(ref)
    if url_match:
        return url_match.group(1), gid
    return ref, gid


def resolve_spreadsheet_id(ref: str) -> str:
    """Extract the spreadsheet ID from a URL, or return a bare ID unchanged.

    Validation is left to the caller (``server._sid``) so the existing error
    message and ID regex stay the single source of truth.
    """
    return parse_spreadsheet_ref(ref)[0]


def looks_like_a1(ref: str) -> bool:
    """True if ``ref`` is already an A1 range (possibly ``Sheet!`` qualified)."""
    _, _, cell = ref.rpartition("!")
    return bool(_A1_RE.match(cell.strip()))


def classify_range_ref(ref: str) -> tuple[str, Any]:
    """Classify a natural range reference. Pure — no I/O.

    Returns one of:
      ("a1", ref) — already an A1 range, pass through
      ("whole", None) — the whole sheet
      ("top_n", n) — the first n data rows
      ("last_row", None) — the last row that has data
      ("header", name) — a column identified by its header text
    """
    r = ref.strip()
    low = r.lower()
    if low in _WHOLE_SHEET:
        return ("whole", None)
    m = re.fullmatch(r"top (\d+) rows?", low)
    if m:
        return ("top_n", int(m.group(1)))
    if low in {"last row", "the last row"}:
        return ("last_row", None)
    if "!" in r:
        return ("a1", r)
    # A bare alphabetic token (e.g. "Revenue") is ambiguous with a column letter,
    # but in the natural layer it almost always means a header — so only treat it
    # as A1 when it carries a digit or a ':' (A1, B2, A:C, 2:5).
    if (":" in r or any(ch.isdigit() for ch in r)) and looks_like_a1(r):
        return ("a1", r)
    return ("header", r)


def quote_sheet(title: str) -> str:
    """Quote a sheet title for A1 use if it needs it ('My Sheet' -> \"'My Sheet'\")."""
    if re.fullmatch(r"[A-Za-z0-9_]+", title) and not title[0].isdigit():
        return title
    return "'" + title.replace("'", "''") + "'"


# -- I/O-backed resolution ---------------------------------------------------


def _first_sheet_title(client, spreadsheet_id: str) -> str:
    props = client.sheet_properties(spreadsheet_id)
    if not props:
        raise ValueError(f"Spreadsheet {spreadsheet_id} has no sheets.")
    return props[0]["title"]


def gid_to_sheet(client, spreadsheet_id: str, gid: int) -> str | None:
    """Map a URL ``#gid=`` to its sheet title (sheetId == gid)."""
    for p in client.sheet_properties(spreadsheet_id):
        if p.get("sheetId") == gid:
            return p["title"]
    return None


def _read_row(client, spreadsheet_id: str, a1: str) -> list:
    result = (
        client.spreadsheets.values()
        .get(spreadsheetId=spreadsheet_id, range=a1, valueRenderOption="UNFORMATTED_VALUE")
        .execute()
    )
    values = result.get("values", [])
    return values[0] if values else []


def _column_count(client, spreadsheet_id: str, a1: str) -> int:
    result = (
        client.spreadsheets.values()
        .get(spreadsheetId=spreadsheet_id, range=a1)
        .execute()
    )
    return len(result.get("values", []))


def resolve_range(client, spreadsheet_id: str, ref: str, default_sheet: str | None = None) -> str:
    """Expand a natural range reference to a concrete A1 string.

    ``client`` is a ``SheetsClient``. A1 references pass through unchanged.
    """
    kind, param = classify_range_ref(ref)
    if kind == "a1":
        return ref

    sheet = default_sheet or _first_sheet_title(client, spreadsheet_id)
    qsheet = quote_sheet(sheet)

    if kind == "whole":
        return sheet
    if kind == "top_n":
        return f"{qsheet}!1:{param}"
    if kind == "last_row":
        n = _column_count(client, spreadsheet_id, sheet) or 1
        return f"{qsheet}!{n}:{n}"
    if kind == "header":
        headers = _read_row(client, spreadsheet_id, f"{qsheet}!1:1")
        idx = _match_header(headers, param)
        if idx is None:
            available = ", ".join(repr(str(h)) for h in headers if str(h).strip())
            raise ValueError(
                f"No column with header {param!r} in sheet {sheet!r}. "
                f"Headers: {available or '(none)'}."
            )
        letter = col_index_to_letter(idx)
        return f"{qsheet}!{letter}:{letter}"

    raise ValueError(f"Unrecognized range reference: {ref!r}")


def _match_header(headers: list, name: str) -> int | None:
    """Find the column index of a header by exact then case-insensitive match."""
    target = name.strip().lower()
    for i, h in enumerate(headers):
        if str(h).strip().lower() == target:
            return i
    return None


def header_to_column_index(headers: list, name: str) -> int | None:
    """Public helper: column index for a header name (used by sort_by)."""
    return _match_header(headers, name)


__all__ = [
    "parse_spreadsheet_ref",
    "resolve_spreadsheet_id",
    "looks_like_a1",
    "classify_range_ref",
    "quote_sheet",
    "gid_to_sheet",
    "resolve_range",
    "header_to_column_index",
    "col_letter_to_index",
]
