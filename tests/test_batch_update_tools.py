import pytest

import google_sheets_mcp.server as server


class FakeClient:
    def resolve_sheet_id(self, spreadsheet_id, sheet):
        return 123 if sheet in ("Sheet1", 123, None) else 456

    def resolve_grid_range(self, spreadsheet_id, range_a1, default_sheet=None):
        sheet_id = self.resolve_sheet_id(spreadsheet_id, default_sheet)
        if "!" in range_a1:
            sheet, _, range_a1 = range_a1.partition("!")
            sheet_id = self.resolve_sheet_id(spreadsheet_id, sheet.strip("'"))
        grid = server.a1_to_grid_range(range_a1)
        grid["sheetId"] = sheet_id
        return grid


@pytest.fixture
def captured(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "client", FakeClient())

    def fake_execute(spreadsheet_id, requests, response_ranges=None, include_spreadsheet_in_response=False):
        calls.append(
            {
                "spreadsheet_id": spreadsheet_id,
                "requests": requests,
                "response_ranges": response_ranges,
                "include_spreadsheet_in_response": include_spreadsheet_in_response,
            }
        )
        return {
            "spreadsheetId": spreadsheet_id,
            "replies": [
                {
                    "findReplace": {"occurrencesChanged": 2},
                    "addChart": {"chart": {"chartId": 99}},
                }
            ],
        }

    monkeypatch.setattr(server, "_execute_batch_update", fake_execute)
    return calls


def test_advanced_requests_validate_allowlist():
    assert server._validate_advanced_requests([{"updateCells": {"fields": "*"}}]) == ["updateCells"]


def test_advanced_requests_reject_blocked_data_sources():
    with pytest.raises(ValueError, match="blocked"):
        server._validate_advanced_requests([{"addDataSource": {}}])


def test_advanced_requests_reject_multiple_kinds():
    with pytest.raises(ValueError, match="exactly one"):
        server._validate_advanced_requests([{"updateCells": {}, "sortRange": {}}])


def test_advanced_requests_reject_too_many():
    with pytest.raises(ValueError, match="Too many"):
        server._validate_advanced_requests([{"updateCells": {}}] * 51)


def test_insert_rows_builds_insert_dimension(captured):
    result = server.insert_rows(
        "abcdefghijklmnopqrstuvwxyz",
        "Sheet1",
        start_index=2,
        end_index=5,
        inherit_from_before=True,
    )

    assert result == {"sheetId": 123, "dimension": "ROWS", "startIndex": 2, "endIndex": 5}
    assert captured[0]["requests"] == [
        {
            "insertDimension": {
                "range": {"sheetId": 123, "dimension": "ROWS", "startIndex": 2, "endIndex": 5},
                "inheritFromBefore": True,
            }
        }
    ]


def test_set_data_validation_blocks_network_custom_formula(captured):
    with pytest.raises(ValueError, match="network-fetching"):
        server.set_data_validation(
            "abcdefghijklmnopqrstuvwxyz",
            "Sheet1!A1:A5",
            "CUSTOM_FORMULA",
            ["=IMPORTXML(\"https://example.com\", \"//title\")"],
        )

    assert captured == []


def test_create_pivot_table_builds_update_cells(captured):
    server.create_pivot_table(
        "abcdefghijklmnopqrstuvwxyz",
        source_range="Sheet1!A1:C10",
        destination_cell="Sheet1!E1",
        rows=[{"sourceColumnOffset": 0, "showTotals": True}],
        values=[{"sourceColumnOffset": 2, "summarizeFunction": "SUM", "name": "Revenue"}],
    )

    update_cells = captured[0]["requests"][0]["updateCells"]
    assert update_cells["start"] == {"sheetId": 123, "rowIndex": 0, "columnIndex": 4}
    pivot = update_cells["rows"][0]["values"][0]["pivotTable"]
    assert pivot["source"] == {
        "sheetId": 123,
        "startColumnIndex": 0,
        "endColumnIndex": 3,
        "startRowIndex": 0,
        "endRowIndex": 10,
    }
    assert pivot["valueLayout"] == "HORIZONTAL"
    assert update_cells["fields"] == "pivotTable"


def test_move_chart_builds_position_request(captured):
    server.move_chart(
        "abcdefghijklmnopqrstuvwxyz",
        chart_id=99,
        sheet="Sheet1",
        anchor_cell="G4",
        width_pixels=500,
        height_pixels=300,
    )

    request = captured[0]["requests"][0]["updateEmbeddedObjectPosition"]
    assert request["objectId"] == 99
    assert request["newPosition"]["overlayPosition"]["anchorCell"] == {
        "sheetId": 123,
        "rowIndex": 3,
        "columnIndex": 6,
    }
    assert request["newPosition"]["overlayPosition"]["widthPixels"] == 500
    assert request["fields"] == "anchorCell,offsetXPixels,offsetYPixels,widthPixels,heightPixels"
