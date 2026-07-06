"""Thin wrapper around the Google Sheets API v4 service.

A ``SheetsClient`` is bound to one set of credentials. In local mode a single
instance is reused; in group mode one is built per request from the caller's
own Google access token. Per-request construction is cheap because the client
library ships a *static* Sheets v4 discovery document, so ``build`` reads it
from disk with no network round-trip.
"""

from __future__ import annotations

from googleapiclient.discovery import build

from .a1 import a1_to_grid_range, split_sheet_and_range
from .auth import get_credentials


class SheetsClient:
    def __init__(self, credentials=None) -> None:
        """Bind to ``credentials``; if None, fall back to local on-disk creds."""
        self._credentials = credentials
        self._service = None

    @property
    def service(self):
        if self._service is None:
            creds = self._credentials or get_credentials()
            # static_discovery=True uses the bundled sheets.v4.json (no network).
            self._service = build(
                "sheets",
                "v4",
                credentials=creds,
                cache_discovery=False,
                static_discovery=True,
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
                "namedRanges(namedRangeId,name,range),"
                "sheets(properties(sheetId,title,index,sheetType,"
                "gridProperties(rowCount,columnCount,frozenRowCount,frozenColumnCount)),"
                "charts(chartId,spec.title,position),"
                "filterViews(filterViewId,title,range),"
                "protectedRanges(protectedRangeId,description,range,warningOnly),"
                "bandedRanges(bandedRangeId,range),"
                "slicers(slicerId,spec.title,position),"
                "tables(tableId,name,range))"
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

        titles = [p["title"] for p in props]
        available = ", ".join(repr(t) for t in titles)
        import difflib

        close = difflib.get_close_matches(str(sheet), titles, n=1, cutoff=0.6)
        hint = f" Did you mean {close[0]!r}?" if close else ""
        raise ValueError(f"Sheet {sheet!r} not found.{hint} Available sheets: {available}.")

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
