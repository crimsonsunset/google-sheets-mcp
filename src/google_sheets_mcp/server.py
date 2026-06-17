"""FastMCP server exposing cell-level and formatting-level Google Sheets tools.

Run locally over stdio (the default transport) — e.g. configured as an MCP
server in Claude Desktop / Claude Code, or `google-sheets-mcp` from a shell.
"""

from __future__ import annotations

import re
from typing import Any

from googleapiclient.errors import HttpError
from mcp.server.fastmcp import FastMCP

from .a1 import a1_to_grid_range, split_sheet_and_range
from .client import SheetsClient
from .colors import parse_color

mcp = FastMCP("google-sheets")
client = SheetsClient()

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_SPREADSHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
_RENDER_OPTIONS = {"FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"}
_INPUT_OPTIONS = {"USER_ENTERED", "RAW"}


def _sid(spreadsheet_id: str) -> str:
    """Validate and return a spreadsheet ID."""
    if not _SPREADSHEET_ID_RE.fullmatch(spreadsheet_id):
        raise ValueError(
            "Invalid spreadsheet_id — expected an alphanumeric Google Sheets ID "
            "(20–100 characters, letters/digits/hyphens/underscores)."
        )
    return spreadsheet_id


def _render_option(opt: str) -> str:
    upper = opt.upper()
    if upper not in _RENDER_OPTIONS:
        raise ValueError(
            f"Invalid value_render_option {opt!r}. "
            f"Choose from: {', '.join(sorted(_RENDER_OPTIONS))}."
        )
    return upper


def _input_option(opt: str) -> str:
    upper = opt.upper()
    if upper not in _INPUT_OPTIONS:
        raise ValueError(
            f"Invalid value_input_option {opt!r}. "
            f"Choose from: {', '.join(sorted(_INPUT_OPTIONS))}."
        )
    return upper


def _api(fn, *args, **kwargs):
    """Execute a Google API call, stripping URL details from errors."""
    try:
        return fn(*args, **kwargs).execute()
    except HttpError as exc:
        raise RuntimeError(
            f"Google Sheets API error {exc.status_code}: {exc.reason}"
        ) from None


# ---------------------------------------------------------------------------
# Spreadsheet / sheet structure
# ---------------------------------------------------------------------------


@mcp.tool()
def get_spreadsheet_info(spreadsheet_id: str) -> dict:
    """Get a spreadsheet's title, URL, and the list of its sheets.

    Returns each sheet's title, numeric ``sheetId``, index, dimensions, and any
    frozen rows/columns. Use this first to discover sheet names and sizes.
    """
    meta = client.get_metadata(_sid(spreadsheet_id))
    sheets = []
    for s in meta.get("sheets", []):
        p = s["properties"]
        grid = p.get("gridProperties", {})
        sheets.append(
            {
                "title": p["title"],
                "sheetId": p["sheetId"],
                "index": p.get("index"),
                "rowCount": grid.get("rowCount"),
                "columnCount": grid.get("columnCount"),
                "frozenRowCount": grid.get("frozenRowCount", 0),
                "frozenColumnCount": grid.get("frozenColumnCount", 0),
            }
        )
    return {
        "spreadsheetId": meta.get("spreadsheetId"),
        "title": meta.get("properties", {}).get("title"),
        "url": meta.get("spreadsheetUrl"),
        "sheets": sheets,
    }


@mcp.tool()
def create_spreadsheet(title: str, sheet_titles: list[str] | None = None) -> dict:
    """Create a new spreadsheet and return its id and URL.

    Optionally provide ``sheet_titles`` to seed multiple named sheets/tabs.
    """
    body: dict[str, Any] = {"properties": {"title": title}}
    if sheet_titles:
        body["sheets"] = [{"properties": {"title": t}} for t in sheet_titles]
    result = _api(
        client.spreadsheets.create,
        body=body,
        fields="spreadsheetId,spreadsheetUrl,properties.title",
    )
    return {
        "spreadsheetId": result["spreadsheetId"],
        "url": result["spreadsheetUrl"],
        "title": result["properties"]["title"],
    }


@mcp.tool()
def add_sheet(
    spreadsheet_id: str,
    title: str,
    row_count: int = 1000,
    column_count: int = 26,
) -> dict:
    """Add a new sheet/tab to an existing spreadsheet."""
    req = {
        "addSheet": {
            "properties": {
                "title": title,
                "gridProperties": {"rowCount": row_count, "columnCount": column_count},
            }
        }
    }
    result = client.batch_update(_sid(spreadsheet_id), [req])
    props = result["replies"][0]["addSheet"]["properties"]
    return {"title": props["title"], "sheetId": props["sheetId"]}


@mcp.tool()
def rename_sheet(spreadsheet_id: str, sheet: str, new_title: str) -> dict:
    """Rename a sheet/tab."""
    sheet_id = client.resolve_sheet_id(_sid(spreadsheet_id), sheet)
    req = {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "title": new_title},
            "fields": "title",
        }
    }
    client.batch_update(spreadsheet_id, [req])
    return {"sheetId": sheet_id, "title": new_title}


# ---------------------------------------------------------------------------
# Cell values
# ---------------------------------------------------------------------------


@mcp.tool()
def read_range(
    spreadsheet_id: str,
    range: str,
    value_render_option: str = "FORMATTED_VALUE",
) -> dict:
    """Read cell values from an A1 range (e.g. ``Sheet1!A1:C10``).

    ``value_render_option``: ``FORMATTED_VALUE`` (as shown, default),
    ``UNFORMATTED_VALUE`` (typed values), or ``FORMULA`` (underlying formulas).
    Returns ``{"range", "values"}`` where ``values`` is a list of rows.
    """
    result = _api(
        client.spreadsheets.values().get,
        spreadsheetId=_sid(spreadsheet_id),
        range=range,
        valueRenderOption=_render_option(value_render_option),
    )
    return {"range": result.get("range"), "values": result.get("values", [])}


@mcp.tool()
def batch_read(
    spreadsheet_id: str,
    ranges: list[str],
    value_render_option: str = "FORMATTED_VALUE",
) -> dict:
    """Read several A1 ranges at once. Returns ``{"valueRanges": [...]}``."""
    result = _api(
        client.spreadsheets.values().batchGet,
        spreadsheetId=_sid(spreadsheet_id),
        ranges=ranges,
        valueRenderOption=_render_option(value_render_option),
    )
    return {
        "valueRanges": [
            {"range": vr.get("range"), "values": vr.get("values", [])}
            for vr in result.get("valueRanges", [])
        ]
    }


@mcp.tool()
def write_range(
    spreadsheet_id: str,
    range: str,
    values: list[list[Any]],
    value_input_option: str = "USER_ENTERED",
) -> dict:
    """Write a 2D array of values to an A1 range.

    ``value_input_option``: ``USER_ENTERED`` (parse like a user typing — formulas,
    dates, and numbers are interpreted; default) or ``RAW`` (store verbatim as
    strings). The range's top-left anchors the write.

    **Note:** ``USER_ENTERED`` evaluates formulas, including those that make
    external network requests (e.g. ``=IMPORTDATA(...)``). Use ``RAW`` if you
    are writing untrusted user-supplied content.
    """
    result = _api(
        client.spreadsheets.values().update,
        spreadsheetId=_sid(spreadsheet_id),
        range=range,
        valueInputOption=_input_option(value_input_option),
        body={"values": values},
    )
    return {
        "updatedRange": result.get("updatedRange"),
        "updatedRows": result.get("updatedRows"),
        "updatedColumns": result.get("updatedColumns"),
        "updatedCells": result.get("updatedCells"),
    }


@mcp.tool()
def append_rows(
    spreadsheet_id: str,
    range: str,
    values: list[list[Any]],
    value_input_option: str = "USER_ENTERED",
) -> dict:
    """Append rows after the last row of data within/near the given A1 range.

    The ``range`` selects the table to append to (e.g. ``Sheet1!A1`` or
    ``Sheet1!A:D``); new rows are added below the existing data.

    **Note:** ``USER_ENTERED`` evaluates formulas, including those that make
    external network requests (e.g. ``=IMPORTDATA(...)``). Use ``RAW`` if you
    are writing untrusted user-supplied content.
    """
    result = _api(
        client.spreadsheets.values().append,
        spreadsheetId=_sid(spreadsheet_id),
        range=range,
        valueInputOption=_input_option(value_input_option),
        insertDataOption="INSERT_ROWS",
        body={"values": values},
    )
    updates = result.get("updates", {})
    return {
        "updatedRange": updates.get("updatedRange"),
        "updatedRows": updates.get("updatedRows"),
        "updatedCells": updates.get("updatedCells"),
    }


@mcp.tool()
def clear_range(spreadsheet_id: str, range: str) -> dict:
    """Clear the *values* in an A1 range (formatting is preserved)."""
    result = _api(
        client.spreadsheets.values().clear,
        spreadsheetId=_sid(spreadsheet_id),
        range=range,
        body={},
    )
    return {"clearedRange": result.get("clearedRange")}


# ---------------------------------------------------------------------------
# Cell formatting
# ---------------------------------------------------------------------------


@mcp.tool()
def format_cells(
    spreadsheet_id: str,
    range: str,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    strikethrough: bool | None = None,
    font_size: int | None = None,
    font_family: str | None = None,
    text_color: str | None = None,
    background_color: str | None = None,
    horizontal_alignment: str | None = None,
    vertical_alignment: str | None = None,
    wrap_strategy: str | None = None,
    number_format_type: str | None = None,
    number_format_pattern: str | None = None,
) -> dict:
    """Apply cell formatting to an A1 range. Only the arguments you pass change.

    * ``text_color`` / ``background_color``: ``"#RRGGBB"``, a named color
      (red, blue, lightgray, ...), or omit to leave unchanged.
    * ``horizontal_alignment``: ``LEFT`` | ``CENTER`` | ``RIGHT``.
    * ``vertical_alignment``: ``TOP`` | ``MIDDLE`` | ``BOTTOM``.
    * ``wrap_strategy``: ``OVERFLOW_CELL`` | ``CLIP`` | ``WRAP``.
    * ``number_format_type``: ``TEXT`` | ``NUMBER`` | ``PERCENT`` | ``CURRENCY``
      | ``DATE`` | ``TIME`` | ``DATE_TIME`` | ``SCIENTIFIC``; pair with an
      optional ``number_format_pattern`` (e.g. ``"$#,##0.00"``, ``"0.0%"``).
    """
    cell_format: dict[str, Any] = {}
    fields: list[str] = []
    text_format: dict[str, Any] = {}

    if bold is not None:
        text_format["bold"] = bold
        fields.append("userEnteredFormat.textFormat.bold")
    if italic is not None:
        text_format["italic"] = italic
        fields.append("userEnteredFormat.textFormat.italic")
    if underline is not None:
        text_format["underline"] = underline
        fields.append("userEnteredFormat.textFormat.underline")
    if strikethrough is not None:
        text_format["strikethrough"] = strikethrough
        fields.append("userEnteredFormat.textFormat.strikethrough")
    if font_size is not None:
        text_format["fontSize"] = font_size
        fields.append("userEnteredFormat.textFormat.fontSize")
    if font_family is not None:
        text_format["fontFamily"] = font_family
        fields.append("userEnteredFormat.textFormat.fontFamily")
    if text_color is not None:
        text_format["foregroundColor"] = parse_color(text_color)
        fields.append("userEnteredFormat.textFormat.foregroundColor")

    if text_format:
        cell_format["textFormat"] = text_format

    if background_color is not None:
        cell_format["backgroundColor"] = parse_color(background_color)
        fields.append("userEnteredFormat.backgroundColor")
    if horizontal_alignment is not None:
        cell_format["horizontalAlignment"] = horizontal_alignment.upper()
        fields.append("userEnteredFormat.horizontalAlignment")
    if vertical_alignment is not None:
        cell_format["verticalAlignment"] = vertical_alignment.upper()
        fields.append("userEnteredFormat.verticalAlignment")
    if wrap_strategy is not None:
        cell_format["wrapStrategy"] = wrap_strategy.upper()
        fields.append("userEnteredFormat.wrapStrategy")
    if number_format_type is not None:
        nf: dict[str, Any] = {"type": number_format_type.upper()}
        if number_format_pattern is not None:
            nf["pattern"] = number_format_pattern
        cell_format["numberFormat"] = nf
        fields.append("userEnteredFormat.numberFormat")

    if not fields:
        raise ValueError("No formatting options provided — pass at least one.")

    grid = client.resolve_grid_range(_sid(spreadsheet_id), range)
    req = {
        "repeatCell": {
            "range": grid,
            "cell": {"userEnteredFormat": cell_format},
            "fields": ",".join(fields),
        }
    }
    client.batch_update(spreadsheet_id, [req])
    return {"range": range, "appliedFields": fields}


@mcp.tool()
def set_borders(
    spreadsheet_id: str,
    range: str,
    style: str = "SOLID",
    color: str = "#000000",
    top: bool = True,
    bottom: bool = True,
    left: bool = True,
    right: bool = True,
    inner_horizontal: bool = False,
    inner_vertical: bool = False,
) -> dict:
    """Set borders on an A1 range.

    ``style``: ``SOLID`` | ``SOLID_MEDIUM`` | ``SOLID_THICK`` | ``DASHED`` |
    ``DOTTED`` | ``DOUBLE`` | ``NONE`` (use NONE to clear). ``top``/``bottom``/
    ``left``/``right`` are the outer edges; ``inner_*`` set borders between cells.
    """
    border = {"style": style.upper(), "color": parse_color(color)}
    body: dict[str, Any] = {"range": client.resolve_grid_range(_sid(spreadsheet_id), range)}
    for edge, enabled in (
        ("top", top),
        ("bottom", bottom),
        ("left", left),
        ("right", right),
        ("innerHorizontal", inner_horizontal),
        ("innerVertical", inner_vertical),
    ):
        if enabled:
            body[edge] = dict(border)

    client.batch_update(spreadsheet_id, [{"updateBorders": body}])
    return {"range": range, "style": style.upper()}


@mcp.tool()
def merge_cells(spreadsheet_id: str, range: str, merge_type: str = "MERGE_ALL") -> dict:
    """Merge cells in an A1 range.

    ``merge_type``: ``MERGE_ALL`` (one cell), ``MERGE_COLUMNS`` (merge each column
    vertically), or ``MERGE_ROWS`` (merge each row horizontally).
    """
    grid = client.resolve_grid_range(_sid(spreadsheet_id), range)
    client.batch_update(
        spreadsheet_id, [{"mergeCells": {"range": grid, "mergeType": merge_type.upper()}}]
    )
    return {"range": range, "mergeType": merge_type.upper()}


@mcp.tool()
def unmerge_cells(spreadsheet_id: str, range: str) -> dict:
    """Unmerge any merged cells overlapping an A1 range."""
    grid = client.resolve_grid_range(_sid(spreadsheet_id), range)
    client.batch_update(spreadsheet_id, [{"unmergeCells": {"range": grid}}])
    return {"range": range, "unmerged": True}


# ---------------------------------------------------------------------------
# Dimensions: column widths, row heights, freezing
# ---------------------------------------------------------------------------


@mcp.tool()
def set_dimension_size(
    spreadsheet_id: str,
    sheet: str,
    dimension: str,
    start_index: int,
    end_index: int,
    pixel_size: int,
) -> dict:
    """Set a fixed pixel size for a band of columns or rows.

    ``dimension``: ``COLUMNS`` or ``ROWS``. Indices are zero-based and half-open
    (``start_index=0, end_index=3`` covers the first three columns/rows).
    """
    sheet_id = client.resolve_sheet_id(_sid(spreadsheet_id), sheet)
    req = {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": dimension.upper(),
                "startIndex": start_index,
                "endIndex": end_index,
            },
            "properties": {"pixelSize": pixel_size},
            "fields": "pixelSize",
        }
    }
    client.batch_update(spreadsheet_id, [req])
    return {"sheetId": sheet_id, "dimension": dimension.upper(), "pixelSize": pixel_size}


@mcp.tool()
def auto_resize_dimensions(
    spreadsheet_id: str,
    sheet: str,
    dimension: str,
    start_index: int,
    end_index: int,
) -> dict:
    """Auto-fit columns or rows to their contents (zero-based, half-open range)."""
    sheet_id = client.resolve_sheet_id(_sid(spreadsheet_id), sheet)
    req = {
        "autoResizeDimensions": {
            "dimensions": {
                "sheetId": sheet_id,
                "dimension": dimension.upper(),
                "startIndex": start_index,
                "endIndex": end_index,
            }
        }
    }
    client.batch_update(spreadsheet_id, [req])
    return {"sheetId": sheet_id, "dimension": dimension.upper(), "autoResized": True}


@mcp.tool()
def freeze(
    spreadsheet_id: str,
    sheet: str,
    rows: int | None = None,
    columns: int | None = None,
) -> dict:
    """Freeze the first ``rows`` rows and/or ``columns`` columns of a sheet.

    Pass ``0`` to unfreeze. Leaving an argument as ``None`` keeps it unchanged.
    """
    sheet_id = client.resolve_sheet_id(_sid(spreadsheet_id), sheet)
    grid_props: dict[str, int] = {}
    fields: list[str] = []
    if rows is not None:
        grid_props["frozenRowCount"] = rows
        fields.append("gridProperties.frozenRowCount")
    if columns is not None:
        grid_props["frozenColumnCount"] = columns
        fields.append("gridProperties.frozenColumnCount")
    if not fields:
        raise ValueError("Pass rows and/or columns to freeze.")

    req = {
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": grid_props},
            "fields": ",".join(fields),
        }
    }
    client.batch_update(spreadsheet_id, [req])
    return {"sheetId": sheet_id, "frozenRows": rows, "frozenColumns": columns}


# ---------------------------------------------------------------------------
# Conditional formatting
# ---------------------------------------------------------------------------


@mcp.tool()
def add_conditional_format(
    spreadsheet_id: str,
    range: str,
    condition_type: str,
    values: list[str] | None = None,
    background_color: str | None = None,
    text_color: str | None = None,
    bold: bool | None = None,
) -> dict:
    """Add a boolean conditional-format rule to an A1 range.

    ``condition_type`` is a Sheets ``ConditionType`` such as ``NUMBER_GREATER``,
    ``NUMBER_LESS``, ``NUMBER_BETWEEN``, ``TEXT_CONTAINS``, ``TEXT_EQ``,
    ``CUSTOM_FORMULA``, ``BLANK``, ``NOT_BLANK``. ``values`` supplies the
    comparison operands (e.g. ``["100"]`` or ``["10","20"]``; for
    ``CUSTOM_FORMULA`` a single ``["=$A1>5"]``). The format applies when the
    condition is true.
    """
    grid = client.resolve_grid_range(_sid(spreadsheet_id), range)

    fmt: dict[str, Any] = {}
    if background_color is not None:
        fmt["backgroundColor"] = parse_color(background_color)
    text_fmt: dict[str, Any] = {}
    if text_color is not None:
        text_fmt["foregroundColor"] = parse_color(text_color)
    if bold is not None:
        text_fmt["bold"] = bold
    if text_fmt:
        fmt["textFormat"] = text_fmt
    if not fmt:
        raise ValueError("Provide at least one of background_color, text_color, bold.")

    condition: dict[str, Any] = {"type": condition_type.upper()}
    if values:
        condition["values"] = [{"userEnteredValue": v} for v in values]

    req = {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [grid],
                "booleanRule": {"condition": condition, "format": fmt},
            },
            "index": 0,
        }
    }
    client.batch_update(spreadsheet_id, [req])
    return {"range": range, "conditionType": condition_type.upper()}


def main() -> None:
    """Entry point — run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
