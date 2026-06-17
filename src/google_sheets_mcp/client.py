"""Thin wrapper around the Google Sheets API v4 service.

Holds the lazily-built service object, resolves sheet names to ``sheetId``s, and
turns A1 ranges into fully-qualified ``GridRange`` objects for formatting calls.
"""

from __future__ import annotations

from googleapiclient.discovery import build

from .a1 import a1_to_grid_range, split_sheet_and_range
from .auth import get_credentials


class SheetsClient:
    def __init__(self) -> None:
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build(
                "sheets",
                "v4",
                credentials=get_credentials(),
                cache_discovery=False,
            )
        return self._service

    @property
    def spreadsheets(self):
        return self.service.spreadsheets()

    # -- metadata -----------------------------------------------------------

    def get_metadata(self, spreadsheet_id: str) -> dict:
        """Return spreadsheet metadata (properties + per-sheet properties)."""
        return self.spreadsheets.get(
            spreadsheetId=spreadsheet_id,
            fields=(
                "spreadsheetId,properties.title,spreadsheetUrl,"
                "sheets.properties(sheetId,title,index,sheetType,"
                "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount))"
            ),
        ).execute()

    def sheet_properties(self, spreadsheet_id: str) -> list[dict]:
        return [s["properties"] for s in self.get_metadata(spreadsheet_id).get("sheets", [])]

    def resolve_sheet_id(self, spreadsheet_id: str, sheet: str | int | None) -> int:
        """Resolve a sheet name (or numeric id, or None=first sheet) to a ``sheetId``."""
        props = self.sheet_properties(spreadsheet_id)
        if not props:
            raise ValueError(f"Spreadsheet {spreadsheet_id} has no sheets.")

        if sheet is None:
            return props[0]["sheetId"]

        # Numeric sheetId passed directly.
        if isinstance(sheet, int) or (isinstance(sheet, str) and sheet.lstrip("-").isdigit()):
            sid = int(sheet)
            if any(p["sheetId"] == sid for p in props):
                return sid
            # fall through to name match in case a sheet is literally named "0"

        for p in props:
            if p["title"] == sheet:
                return p["sheetId"]

        available = ", ".join(repr(p["title"]) for p in props)
        raise ValueError(f"Sheet {sheet!r} not found. Available sheets: {available}.")

    def resolve_grid_range(
        self,
        spreadsheet_id: str,
        range_a1: str,
        default_sheet: str | int | None = None,
    ) -> dict:
        """Convert an A1 range (optionally ``Sheet!`` prefixed) to a full ``GridRange``."""
        sheet_name, cell_range = split_sheet_and_range(range_a1)
        sheet_ref = sheet_name if sheet_name is not None else default_sheet
        sheet_id = self.resolve_sheet_id(spreadsheet_id, sheet_ref)
        grid = a1_to_grid_range(cell_range)
        grid["sheetId"] = sheet_id
        return grid

    # -- low-level passthroughs --------------------------------------------

    def batch_update(self, spreadsheet_id: str, requests: list[dict]) -> dict:
        return self.spreadsheets.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()
