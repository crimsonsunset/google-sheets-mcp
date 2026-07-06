import pytest

from google_sheets_mcp.presets import (
    build_preset_requests,
    number_format,
    special_formula,
    translate_condition,
)

GRID = {
    "sheetId": 0,
    "startRowIndex": 0,
    "endRowIndex": 10,
    "startColumnIndex": 0,
    "endColumnIndex": 3,
}


def test_translate_numeric_ops():
    assert translate_condition("> 100").type == "NUMBER_GREATER"
    assert translate_condition(">=50").type == "NUMBER_GREATER_THAN_EQ"
    assert translate_condition("< 5").type == "NUMBER_LESS"
    assert translate_condition("<= 5").type == "NUMBER_LESS_THAN_EQ"
    assert translate_condition("= 7").type == "NUMBER_EQ"
    assert translate_condition("!= 0").type == "NUMBER_NOT_EQ"
    c = translate_condition("> 100")
    assert c.kind == "boolean" and c.values == ["100"]


def test_translate_between():
    c = translate_condition("between 10 and 20")
    assert c.type == "NUMBER_BETWEEN" and c.values == ["10", "20"]


def test_translate_text():
    assert translate_condition("contains foo").type == "TEXT_CONTAINS"
    assert translate_condition("does not contain foo").type == "TEXT_NOT_CONTAINS"
    assert translate_condition("starts with A").type == "TEXT_STARTS_WITH"
    assert translate_condition("ends with Z").type == "TEXT_ENDS_WITH"
    assert translate_condition("= Done").type == "TEXT_EQ"
    assert translate_condition("is Done").values == ["Done"]


def test_translate_blank():
    assert translate_condition("blank").type == "BLANK"
    assert translate_condition("not empty").type == "NOT_BLANK"


def test_translate_specials():
    assert translate_condition("duplicates").special == "duplicates"
    top = translate_condition("top 10%")
    assert top.special == "top" and top.n == 10.0 and top.percent
    bot = translate_condition("bottom 5")
    assert bot.special == "bottom" and bot.n == 5.0 and not bot.percent


def test_translate_unknown():
    with pytest.raises(ValueError):
        translate_condition("somewhere over the rainbow")


def test_special_formula():
    assert special_formula(translate_condition("duplicates"), "Sheet1!A2:A100") == "=COUNTIF($A$2:$A$100,A2)>1"
    assert special_formula(translate_condition("top 5"), "B2:B50") == "=B2>=LARGE($B$2:$B$50,5)"
    assert special_formula(translate_condition("top 10%"), "B2:B50") == "=B2>=PERCENTILE($B$2:$B$50,0.9)"
    assert special_formula(translate_condition("bottom 3"), "B2:B50") == "=B2<=SMALL($B$2:$B$50,3)"


def test_number_format():
    assert number_format("currency") == ("CURRENCY", "$#,##0.00")
    assert number_format("percent") == ("PERCENT", "0.0%")
    assert number_format("plain") == ("NUMBER", None)
    with pytest.raises(ValueError):
        number_format("bogus")


def test_build_preset_requests_kinds():
    assert [next(iter(r)) for r in build_preset_requests("clean", GRID)] == ["repeatCell", "updateBorders"]
    assert [next(iter(r)) for r in build_preset_requests("financial", GRID)] == ["repeatCell", "repeatCell"]
    assert [next(iter(r)) for r in build_preset_requests("report-header", GRID)] == ["mergeCells", "repeatCell"]
    assert [next(iter(r)) for r in build_preset_requests("input", GRID)] == ["repeatCell", "updateBorders"]


def test_build_preset_header_is_first_row_only():
    req = build_preset_requests("clean", GRID)[0]["repeatCell"]["range"]
    assert req["startRowIndex"] == 0 and req["endRowIndex"] == 1


def test_build_preset_unknown():
    with pytest.raises(ValueError):
        build_preset_requests("fancy", GRID)
