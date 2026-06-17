import pytest

from google_sheets_mcp.colors import parse_color


def test_none():
    assert parse_color(None) is None


def test_hex_full():
    assert parse_color("#ffffff") == {"red": 1.0, "green": 1.0, "blue": 1.0}
    assert parse_color("#000000") == {"red": 0.0, "green": 0.0, "blue": 0.0}


def test_hex_no_hash():
    assert parse_color("ff0000") == {"red": 1.0, "green": 0.0, "blue": 0.0}


def test_hex_short():
    assert parse_color("#fff") == {"red": 1.0, "green": 1.0, "blue": 1.0}


def test_named():
    assert parse_color("red") == {"red": 1.0, "green": 0.0, "blue": 0.0}


def test_dict_passthrough():
    assert parse_color({"red": 0.5, "green": 0.25, "blue": 0.0}) == {
        "red": 0.5,
        "green": 0.25,
        "blue": 0.0,
    }


def test_invalid():
    with pytest.raises(ValueError):
        parse_color("notacolor")
