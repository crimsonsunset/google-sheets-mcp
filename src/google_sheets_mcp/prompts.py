"""MCP prompts — reusable natural-language workflows.

Prompts surface in the client's slash/prompt menu. They don't call tools
themselves; they return a message that primes the model to drive the existing
tools in the right order (describe first, then act).
"""

from __future__ import annotations


def register_prompts(mcp) -> None:
    @mcp.prompt()
    def format_as_report(spreadsheet: str, sheet: str = "") -> str:
        """Turn a sheet into a clean, presentable report."""
        where = f"sheet {sheet!r} of " if sheet else ""
        return (
            f"Format {where}the spreadsheet {spreadsheet} as a clean report. "
            "First call describe_spreadsheet to learn the headers, types, and "
            "extent of the data. Then: apply the 'clean' style preset (or "
            "'financial' if the data is monetary) to the table, freeze the "
            "header row, auto-fit the columns, format numeric columns "
            "appropriately (currency/percent), and add a bold totals row if the "
            "data is additive. Keep changes tasteful and confirm the final range."
        )

    @mcp.prompt()
    def clean_sheet(spreadsheet: str, sheet: str = "") -> str:
        """Tidy up a messy sheet."""
        where = f"sheet {sheet!r} of " if sheet else ""
        return (
            f"Clean up {where}the spreadsheet {spreadsheet}. First call "
            "describe_spreadsheet to see the data. Then trim whitespace, remove "
            "duplicate rows, fix inconsistent number/date formatting, ensure the "
            "header row is present and bold, and auto-fit columns. Report exactly "
            "what you changed."
        )

    @mcp.prompt()
    def build_dashboard(spreadsheet: str, source_range: str) -> str:
        """Build a small dashboard from a data range."""
        return (
            f"Build a dashboard from {source_range} in spreadsheet {spreadsheet}. "
            "First call describe_spreadsheet to understand the columns. Then "
            "create a pivot table summarizing the key metric by the main "
            "category, add a chart visualizing it, and add a slicer for "
            "interactive filtering. Place them neatly on a sheet and summarize "
            "what you built."
        )

    @mcp.prompt()
    def analyze(spreadsheet: str, sheet: str = "") -> str:
        """Analyze a sheet and surface insights."""
        where = f"sheet {sheet!r} of " if sheet else ""
        return (
            f"Analyze {where}the spreadsheet {spreadsheet}. Call "
            "describe_spreadsheet first, then read the relevant ranges. "
            "Summarize the structure, call out totals/averages/outliers and any "
            "data-quality issues (blanks, duplicates, inconsistent types), and "
            "suggest 2–3 useful next steps. Use highlight_where to visually flag "
            "notable cells (e.g. outliers or blanks) only if the user asks."
        )
