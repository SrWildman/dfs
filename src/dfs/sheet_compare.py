"""Write the `Compare` tab: the Player card's measures for up to four players side by side.

`compare_card` is the pure part. The tab is all formulas over EdgeRaw (no hidden raw tab of its own, so a
`dfs sync` has nothing to refresh), written only by `dfs setup compare` (template first, then the live
week, `dfs doctor` after) and rebuilt by a sync that WIDENS EdgeRaw (`rebuild_if_widened`): Sheets shifts a
formula's references when the tab it points at grows, so a tab built before the widening reads the wrong
columns with no error. A rebuild keeps the four players picked in `B1:E1`.
"""

from __future__ import annotations

from dfs import compare_card as cc
from dfs.config import Config
from dfs.log import get_logger
from dfs.sheets import SheetsClient, SheetsError

log = get_logger("sheet_compare")

COMPARE_TAB = cc.COMPARE_TAB


def _rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {k: int(h[i : i + 2], 16) / 255 for k, i in (("red", 0), ("green", 2), ("blue", 4))}


def write_card(client: SheetsClient, cfg: Config, layout: cc.Layout, tab: str = COMPARE_TAB) -> str:
    """Write the formula rows, style them and add the highlight rules. Dropdowns, protection and placement are
    separate steps."""
    from dfs.sheet_style import _PANEL_FMT, _TITLE_FMT, INK_MUTED, WHITE

    last = max(len(layout.rows) + 3, 60)
    client.write_tab(tab, layout.rows)
    client.clear_notes(tab, f"A1:{cc.LAST_COLUMN}{last}")
    client.format_range(
        tab,
        f"A1:{cc.LAST_COLUMN}{last}",
        {
            "backgroundColor": WHITE,
            "textFormat": {"bold": False, "italic": False, "fontSize": 10},
            "horizontalAlignment": "LEFT",
            "wrapStrategy": "OVERFLOW_CELL",
        },
    )
    client.set_column_widths(tab, {"A": cc.LABEL_PX, **{c: cc.VALUE_PX for c in cc.PICKER_COLUMNS}})
    client.format_range(tab, "A1", _TITLE_FMT)
    client.format_range(
        tab,
        cc.PICKER_RANGE,
        {
            "textFormat": {"bold": True, "fontSize": 12},
            "backgroundColor": _rgb("#FFF4CC"),
            "borders": {s: {"style": "SOLID"} for s in ("top", "bottom", "left", "right")},
            "wrapStrategy": "WRAP",
        },
    )
    helper = f"A{cc.HELPER_ROW}:{cc.LAST_COLUMN}{cc.HELPER_ROW}"
    client.format_range(tab, helper, {"textFormat": {"fontSize": 8, "foregroundColor": INK_MUTED}})
    for row in layout.section_rows:
        client.format_range(tab, f"A{row}:{cc.LAST_COLUMN}{row}", _PANEL_FMT)
    for row in layout.label_rows:
        client.format_range(tab, f"A{row}", {"textFormat": {"bold": True}})
    for row in layout.note_rows:
        client.format_range(
            tab, f"A{row}", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for rng, pattern in layout.patterns:
        client.format_range(
            tab,
            rng,
            {
                "numberFormat": {"type": "TEXT" if pattern == "@" else "NUMBER", "pattern": pattern},
                "horizontalAlignment": "RIGHT" if pattern != "@" else "LEFT",
            },
        )
    client.clear_conditional_formats(tab)  # the rules are rebuilt whole: never stack a second set
    client.add_boolean_rules(tab, cc.highlight_specs(layout))
    client.freeze(tab, rows=cc.PICKER_ROW, cols=1)
    return f"{tab}: built ({len(layout.rows)} rows, {len(layout.highlights)} highlighted measures)"


def _typed_names(client: SheetsClient, tab: str) -> list[str]:
    """The players picked in `B1:E1` before a rebuild ([] when the tab is new or empty)."""
    if not client.tab_exists(tab):
        return []
    try:
        grid = client.read_range_unformatted(tab, cc.PICKER_RANGE)
    except SheetsError:
        return []
    cells = list(grid[0]) if grid else []
    return [str(c).strip() for c in cells] + [""] * (len(cc.PICKER_COLUMNS) - len(cells))


def build_card(client: SheetsClient, cfg: Config, tab: str = COMPARE_TAB) -> list[str]:
    """Create or rebuild the Compare tab: rows, dropdowns over EdgeRaw's names, protection with `B1:E1`
    exempt, placed right after the Player tab."""
    from dfs.derived import edge_sheet_letter
    from dfs.sheet_protection import protect_compare
    from dfs.sheet_views import EXPOSURE_TAB, LINEUP_COUNT_CELL

    edge_tab = cfg.google_sheets.tab_mappings.get("edge", "EdgeRaw")
    exposure = None
    if client.tab_exists(EXPOSURE_TAB):
        from dfs import player_card as pc

        header = client.read_range(EXPOSURE_TAB, "A1:G1")
        exposure = pc.exposure_columns(header[0] if header else [])
    layout = cc.build_layout(
        edge_tab=edge_tab,
        lineups_tab=cfg.lineups.builder_tab,
        exposure_tab=EXPOSURE_TAB,
        exposure=exposure,
        lineup_count_cell=LINEUP_COUNT_CELL,
        names=_typed_names(client, tab),
    )
    results = [write_card(client, cfg, layout, tab)]
    name_letter = edge_sheet_letter("Name")
    client.set_range_dropdown_validation(
        tab, cc.PICKER_RANGE, source=f"{edge_tab}!${name_letter}$2:${name_letter}", strict=False
    )
    results.append(f"{tab}: {cc.PICKER_RANGE} dropdowns over {edge_tab} names")
    results.append(protect_compare(client, tab))
    results.append(place_after_player(client, tab))
    return results


def place_after_player(client: SheetsClient, tab: str = COMPARE_TAB) -> str:
    """Put the tab right after the Player tab, coloured with the `decide` family. Only `dfs setup compare`
    moves tabs."""
    from dfs import player_card
    from dfs.sheet_style import FAMILY_COLORS

    anchor = player_card.PLAYER_CARD_TAB
    tabs = [t.title for t in client.list_tabs()]
    if anchor not in tabs:
        return f"{tab}: left where it was ({anchor} not found; `dfs setup player-card` first)"
    index = tabs.index(anchor) + 1
    if tab in tabs and tabs.index(tab) < tabs.index(anchor):
        index -= 1
    client.set_tab_properties(tab, color=FAMILY_COLORS["decide"], index=index, hidden=False)
    return f"{tab}: placed right after {anchor}"


def rebuild_if_widened(client: SheetsClient, cfg: Config, before: int | None, after: int) -> tuple[bool, str]:
    """For `dfs sync`: when EdgeRaw's width changed this run (`before` -> `after`), rewrite the tab itself,
    because Sheets shifts a reference that sat at or past the old edge when the tab grows (the Player card's
    2026-10-22 incident). Only on a sheet that already has the Compare tab; never fails the sync.
    Returns (ok, one line to print); (True, "") when nothing needed doing."""
    if before is None or before == after:
        return True, ""
    try:
        if not client.tab_exists(COMPARE_TAB):
            return True, ""
        build_card(client, cfg)
        return True, f"Compare tab rebuilt (EdgeRaw grew from {before} to {after} columns)"
    except Exception as e:  # noqa: BLE001 - a sync must never fail over the Compare tab
        log.warning("compare tab not rebuilt: %s", e)
        return False, f"Compare tab not rebuilt ({e}); `dfs setup compare` rebuilds it."


def setup(client: SheetsClient, cfg: Config) -> list[str]:
    """`dfs setup compare`."""
    return build_card(client, cfg)
