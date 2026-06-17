"""Color parsing into the Sheets API ``Color`` shape (channels in 0.0–1.0)."""

from __future__ import annotations

_NAMED = {
    "black": "#000000",
    "white": "#ffffff",
    "red": "#ff0000",
    "green": "#00ff00",
    "darkgreen": "#006400",
    "blue": "#0000ff",
    "yellow": "#ffff00",
    "orange": "#ff9900",
    "purple": "#9900ff",
    "gray": "#808080",
    "grey": "#808080",
    "lightgray": "#d9d9d9",
    "lightgrey": "#d9d9d9",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
}


def parse_color(value: str | dict | None) -> dict | None:
    """Convert a color spec into a Sheets ``Color`` dict, or ``None``.

    Accepts:
      * ``None`` -> ``None`` (no change)
      * a hex string ``"#RRGGBB"`` / ``"RRGGBB"`` / ``"#RGB"``
      * a named color (see ``_NAMED``)
      * a dict already shaped like ``{"red": .., "green": .., "blue": ..}``
    """
    if value is None:
        return None

    if isinstance(value, dict):
        # Assume already a Sheets Color dict; pass through known keys.
        out = {k: float(value[k]) for k in ("red", "green", "blue", "alpha") if k in value}
        if not out:
            raise ValueError(f"Color dict missing red/green/blue keys: {value!r}")
        return out

    text = str(value).strip().lower()
    if text in _NAMED:
        text = _NAMED[text]

    hex_str = text.lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(ch * 2 for ch in hex_str)
    if len(hex_str) != 6 or any(c not in "0123456789abcdef" for c in hex_str):
        raise ValueError(
            f"Unrecognized color {value!r}. Use '#RRGGBB', a named color, "
            "or a {red,green,blue} dict."
        )

    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    return {"red": r, "green": g, "blue": b}
