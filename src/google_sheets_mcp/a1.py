"""A1-notation helpers and conversion to/from the Sheets API ``GridRange`` shape.

A ``GridRange`` uses zero-based, half-open indices::

    {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 10,
     "startColumnIndex": 0, "endColumnIndex": 3}

Cell references here are *sheet-relative* (no ``Sheet1!`` prefix) — the sheet is
resolved separately and the resulting ``sheetId`` merged in by the caller.
"""

from __future__ import annotations

import re

_CELL_RE = re.compile(r"^\$?([A-Za-z]+)?\$?(\d+)?$")


def col_letter_to_index(letters: str) -> int:
    """``A`` -> 0, ``Z`` -> 25, ``AA`` -> 26."""
    if len(letters) > 10:
        raise ValueError(f"Column reference too long: {letters!r}")
    result = 0
    for ch in letters.upper():
        if not ch.isalpha():
            raise ValueError(f"Invalid column letters: {letters!r}")
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result - 1


def col_index_to_letter(index: int) -> str:
    """``0`` -> ``A``, ``25`` -> ``Z``, ``26`` -> ``AA``."""
    if index < 0:
        raise ValueError("Column index must be >= 0")
    letters = ""
    n = index + 1
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def split_sheet_and_range(a1: str) -> tuple[str | None, str | None]:
    """Split ``Sheet1!A1:C10`` into ``("Sheet1", "A1:C10")``.

    Handles quoted sheet names like ``'My Sheet'!A1``. When there is no ``!``
    the whole string is treated as the cell range and the sheet is ``None``.
    """
    a1 = a1.strip()
    if "!" in a1:
        sheet, _, rng = a1.rpartition("!")
        sheet = sheet.strip()
        if len(sheet) >= 2 and sheet.startswith("'") and sheet.endswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
        return (sheet or None), (rng.strip() or None)
    return None, (a1 or None)


def _parse_cell(cell: str) -> tuple[int | None, int | None]:
    """Parse a single cell ref into ``(col_index, row_index)``; either may be None."""
    m = _CELL_RE.match(cell.strip())
    if not m or (m.group(1) is None and m.group(2) is None):
        raise ValueError(f"Invalid cell reference: {cell!r}")
    col = col_letter_to_index(m.group(1)) if m.group(1) else None
    row = int(m.group(2)) - 1 if m.group(2) else None
    return col, row


def a1_to_grid_range(cell_range: str | None) -> dict:
    """Convert a sheet-relative range to a partial ``GridRange`` (no ``sheetId``).

    Supports full ranges (``A1:C10``), single cells (``B2``), full columns
    (``A:C``), and full rows (``2:5``). An empty/``None`` range returns ``{}``,
    which the API interprets as the entire sheet.
    """
    if cell_range is None:
        return {}
    cell_range = cell_range.strip()
    if not cell_range:
        return {}

    if ":" in cell_range:
        start_s, end_s = cell_range.split(":", 1)
    else:
        start_s = end_s = cell_range

    start_col, start_row = _parse_cell(start_s)
    end_col, end_row = _parse_cell(end_s)

    grid: dict[str, int] = {}

    cols = [c for c in (start_col, end_col) if c is not None]
    if cols:
        grid["startColumnIndex"] = min(cols)
        grid["endColumnIndex"] = max(cols) + 1

    rows = [r for r in (start_row, end_row) if r is not None]
    if rows:
        grid["startRowIndex"] = min(rows)
        grid["endRowIndex"] = max(rows) + 1

    return grid
