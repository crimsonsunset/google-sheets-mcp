# google-sheets-mcp

A locally running [MCP](https://modelcontextprotocol.io) server that gives an LLM
**cell-level** and **formatting-level** control over Google Sheets via the Sheets
API v4. Runs over stdio, authenticates with your own Google account via OAuth.

## Capabilities

**Structure**
- `get_spreadsheet_info` — list sheets with ids, dimensions, frozen rows/cols
- `create_spreadsheet`, `add_sheet`, `rename_sheet`

**Cell values**
- `read_range`, `batch_read` — read A1 ranges (formatted, unformatted, or formulas)
- `write_range`, `append_rows`, `clear_range`

**Formatting**
- `format_cells` — bold/italic/underline/strikethrough, font size & family,
  text & background color, horizontal/vertical alignment, wrap strategy, number formats
- `set_borders` — outer + inner borders, styles, colors
- `merge_cells`, `unmerge_cells`
- `set_dimension_size` (column width / row height), `auto_resize_dimensions`
- `freeze` rows/columns
- `add_conditional_format` — boolean rules (number/text/custom-formula conditions)
- `batch_update` — raw Sheets API `batchUpdate` escape hatch for anything else

## Setup

### 1. Get an OAuth client secret

1. In the [Google Cloud Console](https://console.cloud.google.com/), create (or pick)
   a project and **enable the Google Sheets API** (APIs & Services → Library).
2. Configure the OAuth consent screen (External is fine for personal use; add your
   own Google account as a *Test user*).
3. APIs & Services → Credentials → **Create Credentials → OAuth client ID** →
   application type **Desktop app**. Download the JSON.
4. Save it as `credentials.json` under the config directory:

   ```
   ~/.config/google-sheets-mcp/credentials.json
   ```

   (Or set `$GOOGLE_SHEETS_CREDENTIALS` to its path.)

### 2. Install

```bash
uv sync
```

### 3. Authenticate once

```bash
uv run google-sheets-mcp auth
```

This opens a browser, you log in, and the token is cached at
`~/.config/google-sheets-mcp/token.json`. After this the server runs without
prompting (the token auto-refreshes).

## Running

The server speaks MCP over **stdio** — point your MCP client at it.

### Claude Code

```bash
claude mcp add google-sheets -- uv run --directory /path/to/google-sheets-mcp google-sheets-mcp
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "google-sheets": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/google-sheets-mcp", "google-sheets-mcp"]
    }
  }
}
```

## Notes on ranges

- **A1 notation** (`Sheet1!A1:C10`, `A:C`, `2:5`, `B2`) is used by all
  value/formatting tools. If you omit the `Sheet!` prefix the first sheet is used.
- **Dimension tools** (`set_dimension_size`, `auto_resize_dimensions`) use
  zero-based, half-open indices: `start_index=0, end_index=3` = first three.
- Colors accept `#RRGGBB`, short `#RGB`, named colors (`red`, `lightgray`, …),
  or a `{"red":..,"green":..,"blue":..}` dict (0.0–1.0).

## Config / environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_SHEETS_MCP_HOME` | `~/.config/google-sheets-mcp` | Config directory |
| `GOOGLE_SHEETS_CREDENTIALS` | `<home>/credentials.json` | OAuth client secret |
| `GOOGLE_SHEETS_TOKEN` | `<home>/token.json` | Cached user token |

## Development

```bash
uv run pytest
```

Secrets (`credentials.json`, `token.json`) are git-ignored — never commit them.
