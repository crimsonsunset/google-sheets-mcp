import pytest

from google_sheets_mcp.server import (
    _build_add_chart_request,
    _validate_chart_source_grid,
)


def test_build_basic_chart_request():
    req = _build_add_chart_request(
        chart_type="column",
        domain_grid={
            "sheetId": 123,
            "startRowIndex": 1,
            "endRowIndex": 5,
            "startColumnIndex": 0,
            "endColumnIndex": 1,
        },
        series_grids=[
            {
                "sheetId": 123,
                "startRowIndex": 1,
                "endRowIndex": 5,
                "startColumnIndex": 1,
                "endColumnIndex": 2,
            }
        ],
        position_sheet_id=123,
        anchor_cell="E2",
        title="Revenue",
        x_axis_title="Month",
        y_axis_title="Amount",
        header_count=1,
        stacked_type="not_stacked",
    )

    chart = req["addChart"]["chart"]
    spec = chart["spec"]
    basic = spec["basicChart"]

    assert spec["title"] == "Revenue"
    assert basic["chartType"] == "COLUMN"
    assert basic["headerCount"] == 1
    assert basic["stackedType"] == "NOT_STACKED"
    assert basic["domains"][0]["domain"]["sourceRange"]["sources"][0]["startColumnIndex"] == 0
    assert basic["series"][0]["series"]["sourceRange"]["sources"][0]["startColumnIndex"] == 1
    assert basic["series"][0]["targetAxis"] == "LEFT_AXIS"
    assert basic["axis"] == [
        {"position": "BOTTOM_AXIS", "title": "Month"},
        {"position": "LEFT_AXIS", "title": "Amount"},
    ]
    assert chart["position"]["overlayPosition"]["anchorCell"] == {
        "sheetId": 123,
        "rowIndex": 1,
        "columnIndex": 4,
    }


def test_build_donut_chart_request():
    req = _build_add_chart_request(
        chart_type="donut",
        domain_grid={
            "sheetId": 123,
            "startRowIndex": 1,
            "endRowIndex": 5,
            "startColumnIndex": 0,
            "endColumnIndex": 1,
        },
        series_grids=[
            {
                "sheetId": 123,
                "startRowIndex": 1,
                "endRowIndex": 5,
                "startColumnIndex": 1,
                "endColumnIndex": 2,
            }
        ],
        position_sheet_id=123,
        anchor_cell="G4",
        legend_position="labeled_legend",
    )

    pie = req["addChart"]["chart"]["spec"]["pieChart"]
    assert pie["legendPosition"] == "LABELED_LEGEND"
    assert pie["pieHole"] == 0.4
    assert req["addChart"]["chart"]["position"]["overlayPosition"]["anchorCell"] == {
        "sheetId": 123,
        "rowIndex": 3,
        "columnIndex": 6,
    }


def test_chart_source_must_be_single_row_or_column():
    with pytest.raises(ValueError, match="single row or single column"):
        _validate_chart_source_grid(
            "Sheet1!A1:B10",
            {
                "sheetId": 123,
                "startRowIndex": 0,
                "endRowIndex": 10,
                "startColumnIndex": 0,
                "endColumnIndex": 2,
            },
        )


def test_pie_chart_requires_one_series():
    with pytest.raises(ValueError, match="exactly one series"):
        _build_add_chart_request(
            chart_type="pie",
            domain_grid={
                "sheetId": 123,
                "startRowIndex": 1,
                "endRowIndex": 5,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            },
            series_grids=[
                {
                    "sheetId": 123,
                    "startRowIndex": 1,
                    "endRowIndex": 5,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
                },
                {
                    "sheetId": 123,
                    "startRowIndex": 1,
                    "endRowIndex": 5,
                    "startColumnIndex": 2,
                    "endColumnIndex": 3,
                },
            ],
            position_sheet_id=123,
            anchor_cell="E2",
        )
