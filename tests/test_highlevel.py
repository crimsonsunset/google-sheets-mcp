"""Integration tests for the composite tools via a fake SheetsClient.

We drive the registered tools through ``mcp.call_tool`` in local mode, swapping
the module singleton for a fake that records the requests it receives — so we
assert the tools assemble the right Sheets API payloads without any network.
"""

from __future__ import annotations

import asyncio

import pytest

from google_sheets_mcp.client import SheetsClient
from google_sheets_mcp.highlevel import _infer_type

VALID_ID = "1" + "a" * 43  # passes the _sid regex (20–100 chars)


# -- pure: type inference ----------------------------------------------------


def test_infer_type():
    assert _infer_type([1, 2, 3]) == "number"
    assert _infer_type([True, False]) == "boolean"
    assert _infer_type(["=SUM(A1)", "=A2"]) == "formula"
    assert _infer_type(["x", "y"]) == "text"
    assert _infer_type([None, ""]) == "empty"
    assert _infer_type([1, "x"]) == "text"  # mixed -> text


# -- fake client -------------------------------------------------------------


class _Exec:
    def __init__(self, result):
        self._r = result

    def execute(self):
        return self._r


class _Values:
    def __init__(self, parent):
        self.p = parent

    def get(self, **kw):
        return _Exec({"values": self.p.values_result})

    def batchGet(self, **kw):
        return _Exec({"valueRanges": [{"values": self.p.values_result}]})

    def update(self, **kw):
        self.p.value_updates.append(kw)
        return _Exec({})


class _Service:
    def __init__(self, parent):
        self.p = parent

    def get(self, **kw):
        return _Exec(self.p.metadata)

    def batchUpdate(self, **kw):
        self.p.batch_bodies.append(kw["body"])
        return _Exec({"replies": []})

    def values(self):
        return _Values(self.p)


class FakeClient(SheetsClient):
    def __init__(self):
        super().__init__()
        self.batch_bodies = []
        self.value_updates = []
        self.values_result = [["Name", "Revenue", "Region"], ["Ann", 100, "AU"]]
        self.metadata = {
            "spreadsheetId": VALID_ID,
            "properties": {"title": "T"},
            "spreadsheetUrl": "https://example/u",
            "namedRanges": [],
            "sheets": [
                {
                    "properties": {
                        "title": "Sheet1",
                        "sheetId": 0,
                        "index": 0,
                        "gridProperties": {
                            "rowCount": 100,
                            "columnCount": 10,
                            "frozenRowCount": 0,
                            "frozenColumnCount": 0,
                        },
                    }
                }
            ],
        }

    @property
    def spreadsheets(self):
        return _Service(self)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.delenv("GSHEETS_MCP_MODE", raising=False)
    import google_sheets_mcp.server as server

    f = FakeClient()
    server._local_singleton = f
    yield f
    server._local_singleton = None


def _last_kinds(body):
    return [next(iter(r)) for r in body["requests"]]


def _call(name, args):
    from google_sheets_mcp.server import mcp

    return asyncio.run(mcp.call_tool(name, args))


def test_format_numbers(fake):
    _call("format_numbers", {"spreadsheet": VALID_ID, "range": "Sheet1!A1:B3", "kind": "currency"})
    body = fake.batch_bodies[-1]
    req = body["requests"][0]["repeatCell"]
    assert req["cell"]["userEnteredFormat"]["numberFormat"]["type"] == "CURRENCY"


def test_highlight_where_boolean(fake):
    _call("highlight_where", {"spreadsheet": VALID_ID, "range": "Sheet1!A1:A10", "condition": "> 100", "color": "red"})
    rule = fake.batch_bodies[-1]["requests"][0]["addConditionalFormatRule"]["rule"]
    cond = rule["booleanRule"]["condition"]
    assert cond["type"] == "NUMBER_GREATER"
    assert cond["values"] == [{"userEnteredValue": "100"}]


def test_highlight_where_duplicates_custom_formula(fake):
    _call("highlight_where", {"spreadsheet": VALID_ID, "range": "Sheet1!A2:A100", "condition": "duplicates"})
    cond = fake.batch_bodies[-1]["requests"][0]["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]
    assert cond["type"] == "CUSTOM_FORMULA"
    assert cond["values"][0]["userEnteredValue"].startswith("=COUNTIF(")


def test_apply_style_preset(fake):
    _call("apply_style_preset", {"spreadsheet": VALID_ID, "range": "Sheet1!A1:C10", "preset": "clean"})
    assert _last_kinds(fake.batch_bodies[-1]) == ["repeatCell", "updateBorders"]


def test_build_table_preview_does_not_write(fake):
    _call(
        "build_table",
        {
            "spreadsheet": VALID_ID,
            "anchor": "Sheet1!A1",
            "headers": ["Name", "Revenue"],
            "rows": [["Ann", 100], ["Bo", 200]],
            "preview": True,
        },
    )
    assert fake.batch_bodies == []  # nothing applied
    assert fake.value_updates == []  # nothing written


def test_build_table_applies(fake):
    _call(
        "build_table",
        {
            "spreadsheet": VALID_ID,
            "anchor": "Sheet1!A1",
            "headers": ["Name", "Revenue"],
            "rows": [["Ann", 100]],
            "number_formats": {"Revenue": "currency"},
        },
    )
    assert len(fake.value_updates) == 1  # values written
    kinds = _last_kinds(fake.batch_bodies[-1])
    assert "repeatCell" in kinds and "addBanding" in kinds
    assert "updateSheetProperties" in kinds  # freeze
    assert "autoResizeDimensions" in kinds  # autofit


def test_sort_by_header_name(fake):
    _call("sort_by", {"spreadsheet": VALID_ID, "range": "Sheet1!A1:C10", "column": "Revenue", "order": "desc"})
    spec = fake.batch_bodies[-1]["requests"][0]["sortRange"]
    assert spec["sortSpecs"][0]["dimensionIndex"] == 1  # Revenue is column B
    assert spec["sortSpecs"][0]["sortOrder"] == "DESCENDING"
    assert spec["range"]["startRowIndex"] == 1  # header excluded


def test_url_accepted_end_to_end(fake):
    url = f"https://docs.google.com/spreadsheets/d/{VALID_ID}/edit#gid=0"
    _call("format_numbers", {"spreadsheet": url, "range": "Sheet1!A1:B3", "kind": "percent"})
    nf = fake.batch_bodies[-1]["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]["numberFormat"]
    assert nf["type"] == "PERCENT"
