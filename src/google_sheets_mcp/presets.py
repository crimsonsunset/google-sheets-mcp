"""Pure translations that back the natural-interaction tools.

Nothing here touches the network — everything takes a resolved ``GridRange`` /
A1 string and returns Sheets API request payloads or condition dicts. That keeps
the intent-to-API mapping unit-testable in isolation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .a1 import a1_to_grid_range, col_index_to_letter, split_sheet_and_range
from .colors import parse_color

# -- number formats ----------------------------------------------------------

NUMBER_FORMATS: dict[str, tuple[str, str | None]] = {
    "currency": ("CURRENCY", "$#,##0.00"),
    "percent": ("PERCENT", "0.0%"),
    "date": ("DATE", "yyyy-mm-dd"),
    "datetime": ("DATE_TIME", "yyyy-mm-dd hh:mm"),
    "thousands": ("NUMBER", "#,##0"),
    "number": ("NUMBER", "#,##0.00"),
    "plain": ("NUMBER", None),
}


def number_format(kind: str) -> tuple[str, str | None]:
    key = kind.strip().lower()
    if key not in NUMBER_FORMATS:
        raise ValueError(
            f"Unknown number format {kind!r}. Choose from: "
            f"{', '.join(sorted(NUMBER_FORMATS))}."
        )
    return NUMBER_FORMATS[key]


# -- condition translation ("> 100", "contains foo", "duplicates", "top 10%") --


@dataclass
class Condition:
    """A parsed highlight predicate.

    ``kind == 'boolean'`` -> use ``type`` + ``values`` directly in a booleanRule.
    ``kind == 'formula'`` -> build a CUSTOM_FORMULA from ``special`` (+ ``n``)
    once the concrete range is known (see ``special_formula``).
    """

    kind: str  # "boolean" | "formula"
    type: str | None = None
    values: list[str] = field(default_factory=list)
    special: str | None = None  # "duplicates" | "top" | "bottom"
    n: float | None = None
    percent: bool = False


_NUM_OPS = {
    ">=": "NUMBER_GREATER_THAN_EQ",
    "<=": "NUMBER_LESS_THAN_EQ",
    ">": "NUMBER_GREATER",
    "<": "NUMBER_LESS",
    "==": "NUMBER_EQ",
    "=": "NUMBER_EQ",
    "!=": "NUMBER_NOT_EQ",
    "<>": "NUMBER_NOT_EQ",
}


def _is_number(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except ValueError:
        return False


def translate_condition(condition: str) -> Condition:
    """Parse a natural predicate into a :class:`Condition`."""
    c = condition.strip()
    low = c.lower()

    if low in {"blank", "empty", "is blank", "is empty"}:
        return Condition(kind="boolean", type="BLANK")
    if low in {"not blank", "not empty", "is not blank", "is not empty", "non-empty"}:
        return Condition(kind="boolean", type="NOT_BLANK")
    if low in {"duplicate", "duplicates", "is duplicate"}:
        return Condition(kind="formula", special="duplicates")

    m = re.fullmatch(r"(top|bottom)\s+(\d+(?:\.\d+)?)\s*(%|percent)?", low)
    if m:
        return Condition(
            kind="formula",
            special=m.group(1),
            n=float(m.group(2)),
            percent=bool(m.group(3)),
        )

    m = re.fullmatch(r"between\s+(.+?)\s+and\s+(.+)", low)
    if m:
        return Condition(kind="boolean", type="NUMBER_BETWEEN", values=[m.group(1).strip(), m.group(2).strip()])

    m = re.fullmatch(r"(>=|<=|==|!=|<>|>|<|=)\s*(.+)", c)
    if m:
        op, operand = m.group(1), m.group(2).strip().strip("'\"")
        if _is_number(operand):
            return Condition(kind="boolean", type=_NUM_OPS[op], values=[operand])
        if op in ("=", "=="):
            return Condition(kind="boolean", type="TEXT_EQ", values=[operand])
        if op in ("!=", "<>"):
            return Condition(kind="boolean", type="TEXT_NOT_EQ", values=[operand])
        raise ValueError(f"Operator {op!r} needs a number, got {operand!r}.")

    for prefix, ctype in (
        ("does not contain ", "TEXT_NOT_CONTAINS"),
        ("not contains ", "TEXT_NOT_CONTAINS"),
        ("contains ", "TEXT_CONTAINS"),
        ("starts with ", "TEXT_STARTS_WITH"),
        ("ends with ", "TEXT_ENDS_WITH"),
        ("equals ", "TEXT_EQ"),
        ("is ", "TEXT_EQ"),
    ):
        if low.startswith(prefix):
            operand = c[len(prefix):].strip().strip("'\"")
            return Condition(kind="boolean", type=ctype, values=[operand])

    raise ValueError(
        f"Could not interpret condition {condition!r}. Try things like "
        "'> 100', 'between 10 and 20', 'contains foo', 'blank', 'duplicates', "
        "'top 10%'."
    )


def special_formula(cond: Condition, range_a1: str) -> str:
    """Build the CUSTOM_FORMULA for a 'formula'-kind condition over ``range_a1``."""
    _, cell_part = split_sheet_and_range(range_a1)
    abs_range = _absolutize(cell_part or "A:A")
    top_left = _top_left_cell(cell_part or "A1")

    if cond.special == "duplicates":
        return f"=COUNTIF({abs_range},{top_left})>1"
    if cond.special in ("top", "bottom"):
        if cond.percent:
            pct = (1 - cond.n / 100) if cond.special == "top" else (cond.n / 100)
            op = ">=" if cond.special == "top" else "<="
            return f"={top_left}{op}PERCENTILE({abs_range},{pct})"
        fn = "LARGE" if cond.special == "top" else "SMALL"
        op = ">=" if cond.special == "top" else "<="
        return f"={top_left}{op}{fn}({abs_range},{int(cond.n)})"
    raise ValueError(f"Unsupported special condition {cond.special!r}.")


def _absolutize(cell_range: str) -> str:
    """A1:C10 -> $A$1:$C$10 (leaves already-$ parts alone)."""
    def dollar(token: str) -> str:
        m = re.fullmatch(r"\$?([A-Za-z]+)?\$?(\d+)?", token)
        if not m:
            return token
        col, row = m.group(1), m.group(2)
        out = ""
        if col:
            out += "$" + col
        if row:
            out += "$" + row
        return out or token

    return ":".join(dollar(p) for p in cell_range.split(":"))


def _top_left_cell(cell_range: str) -> str:
    grid = a1_to_grid_range(cell_range)
    col = col_index_to_letter(grid.get("startColumnIndex", 0))
    row = grid.get("startRowIndex", 0) + 1
    return f"{col}{row}"


# -- style presets -----------------------------------------------------------

_PRESETS = {"clean", "financial", "report-header", "input"}


def _header_row(grid: dict) -> dict:
    row = dict(grid)
    start = grid.get("startRowIndex", 0)
    row["startRowIndex"] = start
    row["endRowIndex"] = start + 1
    return row


def _body_rows(grid: dict) -> dict:
    body = dict(grid)
    body["startRowIndex"] = grid.get("startRowIndex", 0) + 1
    return body


def build_preset_requests(preset: str, grid: dict) -> list[dict]:
    """Return the batchUpdate requests for a named style ``preset`` over ``grid``.

    ``grid`` must be a full GridRange (with ``sheetId``).
    """
    key = preset.strip().lower()
    if key not in _PRESETS:
        raise ValueError(f"Unknown preset {preset!r}. Choose from: {', '.join(sorted(_PRESETS))}.")

    header = _header_row(grid)

    if key == "clean":
        return [
            {
                "repeatCell": {
                    "range": header,
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": parse_color("#f0f0f0"),
                        }
                    },
                    "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColor",
                }
            },
            {
                "updateBorders": {
                    "range": header,
                    "bottom": {"style": "SOLID", "color": parse_color("#000000")},
                }
            },
        ]

    if key == "financial":
        return [
            {
                "repeatCell": {
                    "range": header,
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            },
            {
                "repeatCell": {
                    "range": _body_rows(grid),
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"},
                            "horizontalAlignment": "RIGHT",
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
                }
            },
        ]

    if key == "report-header":
        return [
            {"mergeCells": {"range": header, "mergeType": "MERGE_ALL"}},
            {
                "repeatCell": {
                    "range": header,
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True, "fontSize": 14},
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                        }
                    },
                    "fields": "userEnteredFormat.textFormat,userEnteredFormat.horizontalAlignment,userEnteredFormat.verticalAlignment",
                }
            },
        ]

    # input
    return [
        {
            "repeatCell": {
                "range": grid,
                "cell": {"userEnteredFormat": {"backgroundColor": parse_color("#fff2cc")}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        },
        {
            "updateBorders": {
                "range": grid,
                "top": {"style": "SOLID", "color": parse_color("#cccccc")},
                "bottom": {"style": "SOLID", "color": parse_color("#cccccc")},
                "left": {"style": "SOLID", "color": parse_color("#cccccc")},
                "right": {"style": "SOLID", "color": parse_color("#cccccc")},
                "innerHorizontal": {"style": "SOLID", "color": parse_color("#cccccc")},
                "innerVertical": {"style": "SOLID", "color": parse_color("#cccccc")},
            }
        },
    ]


__all__ = [
    "NUMBER_FORMATS",
    "number_format",
    "Condition",
    "translate_condition",
    "special_formula",
    "build_preset_requests",
]
