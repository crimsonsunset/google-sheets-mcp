import pytest

from google_sheets_mcp.refs import (
    classify_range_ref,
    header_to_column_index,
    parse_spreadsheet_ref,
    quote_sheet,
    resolve_range,
    resolve_spreadsheet_id,
)

URL = "https://docs.google.com/spreadsheets/d/1AbC_dEfG-hIjKlMnOpQrStUvWxYz012345678/edit#gid=42"
ID = "1AbC_dEfG-hIjKlMnOpQrStUvWxYz012345678"


def test_parse_url():
    assert parse_spreadsheet_ref(URL) == (ID, 42)


def test_parse_bare_id():
    assert parse_spreadsheet_ref(ID) == (ID, None)


def test_resolve_spreadsheet_id():
    assert resolve_spreadsheet_id(URL) == ID
    assert resolve_spreadsheet_id(ID) == ID
    assert resolve_spreadsheet_id(f"  {URL}  ") == ID


def test_classify():
    assert classify_range_ref("A1:C10") == ("a1", "A1:C10")
    assert classify_range_ref("Sheet1!B2") == ("a1", "Sheet1!B2")
    assert classify_range_ref("A:A") == ("a1", "A:A")
    assert classify_range_ref("2:5") == ("a1", "2:5")
    assert classify_range_ref("top 10 rows") == ("top_n", 10)
    assert classify_range_ref("top 3 row") == ("top_n", 3)
    assert classify_range_ref("last row") == ("last_row", None)
    assert classify_range_ref("whole sheet") == ("whole", None)
    assert classify_range_ref("Revenue") == ("header", "Revenue")
    assert classify_range_ref("Total Sales") == ("header", "Total Sales")


def test_quote_sheet():
    assert quote_sheet("Sales") == "Sales"
    assert quote_sheet("My Sheet") == "'My Sheet'"
    assert quote_sheet("O'Brien") == "'O''Brien'"


def test_header_to_column_index():
    headers = ["Name", "Revenue", "Region"]
    assert header_to_column_index(headers, "Revenue") == 1
    assert header_to_column_index(headers, "revenue") == 1  # case-insensitive
    assert header_to_column_index(headers, "Missing") is None


# -- resolve_range with a minimal fake client --------------------------------


class _FakeExec:
    def __init__(self, result):
        self._r = result

    def execute(self):
        return self._r


class _FakeValues:
    def __init__(self, values):
        self._values = values

    def get(self, **kwargs):
        return _FakeExec({"values": self._values})


class _FakeSpreadsheets:
    def __init__(self, values):
        self._values = values

    def values(self):
        return _FakeValues(self._values)


class _FakeClient:
    def __init__(self, values):
        self._values = values

    def sheet_properties(self, sid):
        return [{"title": "Sheet1", "sheetId": 0}]

    @property
    def spreadsheets(self):
        return _FakeSpreadsheets(self._values)


def test_resolve_range_passthrough_a1():
    c = _FakeClient([["Name", "Revenue"]])
    assert resolve_range(c, "sid", "Sheet1!A1:C10") == "Sheet1!A1:C10"


def test_resolve_range_header():
    c = _FakeClient([["Name", "Revenue", "Region"]])
    assert resolve_range(c, "sid", "Revenue") == "Sheet1!B:B"


def test_resolve_range_header_missing():
    c = _FakeClient([["Name", "Region"]])
    with pytest.raises(ValueError):
        resolve_range(c, "sid", "Revenue")


def test_resolve_range_top_n_and_whole():
    c = _FakeClient([["Name"]])
    assert resolve_range(c, "sid", "top 5 rows") == "Sheet1!1:5"
    assert resolve_range(c, "sid", "whole sheet") == "Sheet1"


def test_resolve_range_last_row():
    c = _FakeClient([["h"], [1], [2], [3]])  # 4 rows of data
    assert resolve_range(c, "sid", "last row") == "Sheet1!4:4"
