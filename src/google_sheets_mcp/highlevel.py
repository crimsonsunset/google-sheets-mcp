"""Intent-level tools: the natural-interaction "front door".

Each tool bundles what would otherwise be several low-level calls into one
operation phrased the way a user thinks, and accepts URL-or-ID plus natural
ranges (via :mod:`refs`). They compose the existing helpers in ``server.py`` —
nothing here talks to Google directly except through ``current_client()``.

Registered by ``register_highlevel(mcp)``, called from ``server.py`` after the
FastMCP instance exists (avoids an import cycle).
"""

from __future__ import annotations

from typing import Any

from .a1 import col_index_to_letter, col_letter_to_index, split_sheet_and_range
from .colors import parse_color
from . import presets
from . import refs


def _infer_type(samples: list) -> str:
    vals = [v for v in samples if v not in (None, "")]
    if not vals:
        return "empty"
    if all(isinstance(v, bool) for v in vals):
        return "boolean"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "number"
    if all(isinstance(v, str) and v.startswith("=") for v in vals):
        return "formula"
    return "text"


def register_highlevel(mcp) -> None:
    # Imported here (not at module top) so this module can be imported while
    # server.py is still being defined.
    from .server import _api, _execute_batch_update, _sid, _condition, current_client

    def _props(sid: str) -> list[dict]:
        return current_client().sheet_properties(sid)

    def _title_for(props: list[dict], sheet_id: int) -> str:
        return next(p["title"] for p in props if p["sheetId"] == sheet_id)

    def _read_values(sid: str, a1: str, render: str = "UNFORMATTED_VALUE") -> list[list]:
        result = _api(
            current_client().spreadsheets.values().get,
            spreadsheetId=sid,
            range=a1,
            valueRenderOption=render,
        )
        return result.get("values", [])

    # -- context ------------------------------------------------------------

    @mcp.tool()
    def describe_spreadsheet(
        spreadsheet: str, sheets: list[str] | None = None, sample_rows: int = 5
    ) -> dict:
        """Compact, model-friendly snapshot of a spreadsheet in one call.

        Accepts a spreadsheet ID *or* a full Google Sheets URL. Returns, per
        sheet: dimensions, frozen rows/cols, column headers with inferred types
        (number/date-ish/boolean/text/formula), a few sample rows, and any
        tables/charts. Use this first to ground natural requests like "the
        Revenue column" before calling other tools.
        """
        sid = _sid(spreadsheet)
        meta = current_client().get_metadata(sid)
        all_sheets = meta.get("sheets", [])
        target = [
            s for s in all_sheets
            if sheets is None or s["properties"]["title"] in sheets
        ]

        ranges = [
            f"{refs.quote_sheet(s['properties']['title'])}!1:{max(1, sample_rows) + 1}"
            for s in target
        ]
        value_ranges: list[dict] = []
        if ranges:
            batch = _api(
                current_client().spreadsheets.values().batchGet,
                spreadsheetId=sid,
                ranges=ranges,
                valueRenderOption="UNFORMATTED_VALUE",
            )
            value_ranges = batch.get("valueRanges", [])

        out = []
        for s, vr in zip(target, value_ranges):
            p = s["properties"]
            grid = p.get("gridProperties", {})
            values = vr.get("values", [])
            headers = values[0] if values else []
            samples = values[1 : sample_rows + 1] if len(values) > 1 else []
            columns = []
            for i, h in enumerate(headers):
                col_samples = [row[i] for row in samples if i < len(row)]
                columns.append(
                    {"name": h, "column": col_index_to_letter(i), "type": _infer_type(col_samples)}
                )
            out.append(
                {
                    "title": p["title"],
                    "rowCount": grid.get("rowCount"),
                    "columnCount": grid.get("columnCount"),
                    "frozenRowCount": grid.get("frozenRowCount", 0),
                    "frozenColumnCount": grid.get("frozenColumnCount", 0),
                    "headers": columns,
                    "sampleRows": samples,
                    "tables": [
                        {"tableId": t.get("tableId"), "name": t.get("name"), "range": t.get("range")}
                        for t in s.get("tables", [])
                    ],
                    "charts": [
                        {"chartId": c.get("chartId"), "title": c.get("spec", {}).get("title")}
                        for c in s.get("charts", [])
                    ],
                }
            )
        return {
            "spreadsheetId": meta.get("spreadsheetId"),
            "title": meta.get("properties", {}).get("title"),
            "url": meta.get("spreadsheetUrl"),
            "namedRanges": [
                {"name": nr.get("name"), "range": nr.get("range")}
                for nr in meta.get("namedRanges", [])
            ],
            "sheets": out,
        }

    # -- build a formatted table in one call --------------------------------

    @mcp.tool()
    def build_table(
        spreadsheet: str,
        anchor: str,
        headers: list[str],
        rows: list[list[Any]],
        style: str = "clean",
        banding: bool = True,
        freeze_header: bool = True,
        autofit: bool = True,
        number_formats: dict[str, str] | None = None,
        preview: bool = False,
    ) -> dict:
        """Write a titled table and format it in one shot.

        ``anchor`` is where the top-left header goes: ``Sheet1!A1``, a bare cell
        (first sheet), or a sheet name (its A1). Applies a style ``preset``
        (clean/financial/report-header/input) to the header, optionally freezes
        the header row, adds banding, auto-fits columns, and applies per-column
        ``number_formats`` (``{"Revenue": "currency"}``). Set ``preview=True`` to
        get the planned ranges/requests without changing anything.
        """
        sid = _sid(spreadsheet)
        client = current_client()
        sheet_name, cell = split_sheet_and_range(anchor)
        props = _props(sid)
        sheet_id = client.resolve_sheet_id(sid, sheet_name)
        title = _title_for(props, sheet_id)

        from .a1 import a1_to_grid_range

        start = a1_to_grid_range(cell or "A1")
        start_row = start.get("startRowIndex", 0)
        start_col = start.get("startColumnIndex", 0)
        n_cols = max(len(headers), max((len(r) for r in rows), default=0))
        n_rows = 1 + len(rows)
        data_grid = {
            "sheetId": sheet_id,
            "startRowIndex": start_row,
            "endRowIndex": start_row + n_rows,
            "startColumnIndex": start_col,
            "endColumnIndex": start_col + n_cols,
        }
        end_cell = f"{col_index_to_letter(start_col + n_cols - 1)}{start_row + n_rows}"
        write_a1 = (
            f"{refs.quote_sheet(title)}!"
            f"{col_index_to_letter(start_col)}{start_row + 1}:{end_cell}"
        )
        values = [list(headers)] + [list(r) for r in rows]

        requests: list[dict] = presets.build_preset_requests(style, data_grid)
        if banding:
            requests.append(
                {
                    "addBanding": {
                        "bandedRange": {
                            "range": data_grid,
                            "rowProperties": {
                                "headerColor": parse_color("#d9ead3"),
                                "firstBandColor": parse_color("#ffffff"),
                                "secondBandColor": parse_color("#f3f3f3"),
                            },
                        }
                    }
                }
            )
        if freeze_header:
            requests.append(
                {
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {"frozenRowCount": start_row + 1},
                        },
                        "fields": "gridProperties.frozenRowCount",
                    }
                }
            )
        if number_formats:
            for name, kind in number_formats.items():
                if name in headers:
                    ci = start_col + list(headers).index(name)
                elif name.isalpha():
                    ci = col_letter_to_index(name)
                else:
                    raise ValueError(f"number_formats key {name!r} is not a header or column letter.")
                ftype, pattern = presets.number_format(kind)
                nf: dict[str, Any] = {"type": ftype}
                if pattern:
                    nf["pattern"] = pattern
                requests.append(
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": start_row + 1,
                                "endRowIndex": start_row + n_rows,
                                "startColumnIndex": ci,
                                "endColumnIndex": ci + 1,
                            },
                            "cell": {"userEnteredFormat": {"numberFormat": nf}},
                            "fields": "userEnteredFormat.numberFormat",
                        }
                    }
                )
        if autofit:
            requests.append(
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_col,
                            "endIndex": start_col + n_cols,
                        }
                    }
                }
            )

        if preview:
            return {
                "preview": True,
                "writeRange": write_a1,
                "rows": n_rows,
                "columns": n_cols,
                "requestKinds": [next(iter(r)) for r in requests],
            }

        _api(
            client.spreadsheets.values().update,
            spreadsheetId=sid,
            range=write_a1,
            valueInputOption="RAW",
            body={"values": values},
        )
        _execute_batch_update(sid, requests)
        return {"writeRange": write_a1, "rows": n_rows, "columns": n_cols, "applied": len(requests)}

    # -- one-liner styling --------------------------------------------------

    @mcp.tool()
    def apply_style_preset(spreadsheet: str, range: str, preset: str) -> dict:
        """Apply a named style preset to a range.

        Presets: ``clean`` (bold shaded header + underline), ``financial``
        (currency, right-aligned body + bold header), ``report-header`` (merged
        centered title), ``input`` (highlighted bordered input cells). ``range``
        accepts A1 or natural refs like "whole sheet".
        """
        sid = _sid(spreadsheet)
        client = current_client()
        resolved = refs.resolve_range(client, sid, range)
        grid = client.resolve_grid_range(sid, resolved)
        _execute_batch_update(sid, presets.build_preset_requests(preset, grid))
        return {"range": resolved, "preset": preset.lower()}

    @mcp.tool()
    def highlight_where(
        spreadsheet: str,
        range: str,
        condition: str,
        color: str | None = None,
        text_color: str | None = None,
        bold: bool | None = None,
    ) -> dict:
        """Conditionally highlight cells from a natural predicate.

        ``condition`` examples: ``"> 100"``, ``"between 10 and 20"``,
        ``"contains overdue"``, ``"blank"``, ``"duplicates"``, ``"top 10%"``.
        Applies the given background ``color`` (default light amber), and/or
        ``text_color``/``bold``. ``range`` accepts A1 or natural refs.
        """
        sid = _sid(spreadsheet)
        client = current_client()
        resolved = refs.resolve_range(client, sid, range)
        grid = client.resolve_grid_range(sid, resolved)

        cond = presets.translate_condition(condition)
        if cond.kind == "formula":
            condition_dict = _condition("CUSTOM_FORMULA", [presets.special_formula(cond, resolved)])
        else:
            condition_dict = _condition(cond.type, cond.values or None)

        fmt: dict[str, Any] = {}
        if color:
            fmt["backgroundColor"] = parse_color(color)
        text_fmt: dict[str, Any] = {}
        if text_color:
            text_fmt["foregroundColor"] = parse_color(text_color)
        if bold is not None:
            text_fmt["bold"] = bold
        if text_fmt:
            fmt["textFormat"] = text_fmt
        if not fmt:
            fmt["backgroundColor"] = parse_color("#fff2cc")

        req = {
            "addConditionalFormatRule": {
                "rule": {"ranges": [grid], "booleanRule": {"condition": condition_dict, "format": fmt}},
                "index": 0,
            }
        }
        _execute_batch_update(sid, [req])
        return {"range": resolved, "condition": condition}

    @mcp.tool()
    def format_numbers(spreadsheet: str, range: str, kind: str) -> dict:
        """Apply a number format to a range.

        ``kind``: ``currency`` | ``percent`` | ``date`` | ``datetime`` |
        ``thousands`` | ``number`` | ``plain``. ``range`` accepts A1 or natural
        refs (e.g. a header name resolves to its whole column).
        """
        sid = _sid(spreadsheet)
        client = current_client()
        resolved = refs.resolve_range(client, sid, range)
        grid = client.resolve_grid_range(sid, resolved)
        ftype, pattern = presets.number_format(kind)
        nf: dict[str, Any] = {"type": ftype}
        if pattern:
            nf["pattern"] = pattern
        req = {
            "repeatCell": {
                "range": grid,
                "cell": {"userEnteredFormat": {"numberFormat": nf}},
                "fields": "userEnteredFormat.numberFormat",
            }
        }
        _execute_batch_update(sid, [req])
        return {"range": resolved, "kind": kind.lower()}

    @mcp.tool()
    def add_totals_row(
        spreadsheet: str, table_range: str, functions: dict[str, str] | None = None
    ) -> dict:
        """Append a bold totals row beneath a table.

        By default sums every column except the first (which gets a "Total"
        label). Override per column with ``functions`` keyed by header name,
        e.g. ``{"Margin": "AVERAGE", "Count": "COUNT"}``.
        """
        sid = _sid(spreadsheet)
        client = current_client()
        resolved = refs.resolve_range(client, sid, table_range)
        grid = client.resolve_grid_range(sid, resolved)
        sheet_id = grid["sheetId"]
        props = _props(sid)
        title = _title_for(props, sheet_id)

        start_col = grid.get("startColumnIndex", 0)
        end_col = grid["endColumnIndex"]
        start_row = grid.get("startRowIndex", 0)
        end_row = grid["endRowIndex"]  # exclusive, 0-based
        header_row = _read_values(sid, resolved)[:1]
        header_names = header_row[0] if header_row else []

        totals_a1_row = end_row + 1  # A1 row directly below the table
        body_first = start_row + 2  # A1 row of first data row (skip header)
        body_last = end_row  # A1 row of last data row

        row_values: list[Any] = []
        for offset, ci in enumerate(range(start_col, end_col)):
            col = col_index_to_letter(ci)
            if offset == 0:
                row_values.append("Total")
                continue
            name = header_names[offset] if offset < len(header_names) else None
            fn = (functions or {}).get(name, "SUM").upper()
            row_values.append(f"={fn}({col}{body_first}:{col}{body_last})")

        write_a1 = (
            f"{refs.quote_sheet(title)}!"
            f"{col_index_to_letter(start_col)}{totals_a1_row}:"
            f"{col_index_to_letter(end_col - 1)}{totals_a1_row}"
        )
        _api(
            client.spreadsheets.values().update,
            spreadsheetId=sid,
            range=write_a1,
            valueInputOption="USER_ENTERED",
            body={"values": [row_values]},
        )
        _execute_batch_update(
            sid,
            [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": end_row,
                            "endRowIndex": end_row + 1,
                            "startColumnIndex": start_col,
                            "endColumnIndex": end_col,
                        },
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                }
            ],
        )
        return {"totalsRow": write_a1}

    @mcp.tool()
    def autofit(spreadsheet: str, target: str) -> dict:
        """Auto-fit column widths to their contents.

        ``target`` may be a sheet name (fits all columns) or an A1 range like
        ``Sheet1!A:D`` (fits just those columns).
        """
        sid = _sid(spreadsheet)
        client = current_client()
        titles = {p["title"]: p for p in _props(sid)}

        if target in titles:
            p = titles[target]
            sheet_id = p["sheetId"]
            start_index, end_index = 0, p.get("gridProperties", {}).get("columnCount", 26)
        else:
            grid = client.resolve_grid_range(sid, target)
            sheet_id = grid["sheetId"]
            start_index = grid.get("startColumnIndex", 0)
            end_index = grid.get("endColumnIndex")
            if end_index is None:
                sheet_id_props = next(p for p in _props(sid) if p["sheetId"] == sheet_id)
                end_index = sheet_id_props.get("gridProperties", {}).get("columnCount", 26)

        _execute_batch_update(
            sid,
            [
                {
                    "autoResizeDimensions": {
                        "dimensions": {
                            "sheetId": sheet_id,
                            "dimension": "COLUMNS",
                            "startIndex": start_index,
                            "endIndex": end_index,
                        }
                    }
                }
            ],
        )
        return {"target": target, "columns": end_index - start_index}

    @mcp.tool()
    def sort_by(spreadsheet: str, range: str, column: str, order: str = "asc") -> dict:
        """Sort a range by a column identified by its **header name** (or letter).

        ``order`` is ``asc`` or ``desc``. Assumes the first row of ``range`` is a
        header (its cells are not sorted into the body).
        """
        sid = _sid(spreadsheet)
        client = current_client()
        resolved = refs.resolve_range(client, sid, range)
        grid = client.resolve_grid_range(sid, resolved)

        header_row = _read_values(sid, resolved)[:1]
        headers = header_row[0] if header_row else []
        rel = refs.header_to_column_index(headers, column)
        if rel is None:
            if column.isalpha():
                dimension_index = col_letter_to_index(column)
            else:
                available = ", ".join(repr(str(h)) for h in headers if str(h).strip())
                raise ValueError(f"No column {column!r} in the range. Headers: {available or '(none)'}.")
        else:
            dimension_index = grid.get("startColumnIndex", 0) + rel

        sort_order = "ASCENDING" if order.strip().lower().startswith("a") else "DESCENDING"
        # Exclude the header row from the sort body.
        body = dict(grid)
        body["startRowIndex"] = grid.get("startRowIndex", 0) + 1
        req = {
            "sortRange": {
                "range": body,
                "sortSpecs": [{"dimensionIndex": dimension_index, "sortOrder": sort_order}],
            }
        }
        _execute_batch_update(sid, [req])
        return {"range": resolved, "sortedBy": column, "order": sort_order}
