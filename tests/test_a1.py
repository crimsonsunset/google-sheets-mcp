from google_sheets_mcp.a1 import (
    a1_to_grid_range,
    col_index_to_letter,
    col_letter_to_index,
    split_sheet_and_range,
)


def test_col_letter_roundtrip():
    for i in (0, 1, 25, 26, 27, 51, 52, 701, 702):
        assert col_letter_to_index(col_index_to_letter(i)) == i


def test_col_letters():
    assert col_letter_to_index("A") == 0
    assert col_letter_to_index("Z") == 25
    assert col_letter_to_index("AA") == 26
    assert col_index_to_letter(0) == "A"
    assert col_index_to_letter(26) == "AA"


def test_split_sheet_and_range():
    assert split_sheet_and_range("Sheet1!A1:C10") == ("Sheet1", "A1:C10")
    assert split_sheet_and_range("A1:C10") == (None, "A1:C10")
    assert split_sheet_and_range("'My Sheet'!B2") == ("My Sheet", "B2")
    assert split_sheet_and_range("'O''Brien'!A1") == ("O'Brien", "A1")


def test_grid_full_range():
    assert a1_to_grid_range("A1:C10") == {
        "startColumnIndex": 0,
        "endColumnIndex": 3,
        "startRowIndex": 0,
        "endRowIndex": 10,
    }


def test_grid_single_cell():
    assert a1_to_grid_range("B2") == {
        "startColumnIndex": 1,
        "endColumnIndex": 2,
        "startRowIndex": 1,
        "endRowIndex": 2,
    }


def test_grid_full_columns():
    assert a1_to_grid_range("A:C") == {"startColumnIndex": 0, "endColumnIndex": 3}


def test_grid_full_rows():
    assert a1_to_grid_range("2:5") == {"startRowIndex": 1, "endRowIndex": 5}


def test_grid_empty_is_whole_sheet():
    assert a1_to_grid_range("") == {}
    assert a1_to_grid_range(None) == {}


def test_grid_reversed_normalizes():
    assert a1_to_grid_range("C10:A1") == a1_to_grid_range("A1:C10")
