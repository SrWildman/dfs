"""Read the inputs for, and write, the `Player` tab and its two hidden raw tabs.

`player_card` is the pure part. Two jobs with different rhythms:

- **The raw tabs** (`PlayerCardRaw`, `PlayerCardRows`) are rewritten whole by every `dfs sync` that runs the
  edge step, when the sheet already has the Player tab (`refresh_raw_if_present`; it never creates anything
  and never fails the sync). They hold nothing typed.
- **The card** itself is written only by `dfs setup player-card` (template first, then the live week, `dfs
  doctor` after): rows of formulas, the dropdown in `B1`, the protection with `B1` exempt, the tab placed
  right after Edge Finder. A rebuild keeps the player picked in `B1`.

Every read is fail-soft per part: a missing file blanks that block and the notes on the tab say so.
"""

from __future__ import annotations

import pandas as pd

from dfs import player_card as pc
from dfs import store
from dfs.config import Config
from dfs.log import get_logger
from dfs.sheet_protection import protect_player_card
from dfs.sheets import SheetsClient, SheetsError

log = get_logger("sheet_player_card")

CARD_TAB = pc.PLAYER_CARD_TAB
RAW_TABS = (pc.RAW_TAB, pc.ROWS_TAB)
LABEL_PX, VALUE_PX = 150, 108


# ---------------------------------------------------------------------------------------------
# Reading (disk only: no network, no sheet)
# ---------------------------------------------------------------------------------------------


def _load(name: str) -> pd.DataFrame | None:
    try:
        return store.load_current(name)
    except (FileNotFoundError, OSError, ValueError):
        return None


def _signal_table(season: int) -> tuple[pd.DataFrame | None, str]:
    """Model Check's signal records as last saved by `dfs results update`, and the sentence saying how old."""
    from dfs import results_update

    path = results_update.signal_table_path(season)
    try:
        table = pd.read_csv(path)
    except (OSError, ValueError):
        return None, "No Model Check records saved yet: they appear after the next `dfs results update`."
    as_of = table["AsOf"].iloc[0] if "AsOf" in table.columns and len(table) else "an earlier update"
    return table, f"Records are Model Check's as of {as_of} (`dfs results update`)."


def _props(edge: pd.DataFrame, week: int) -> tuple[list[dict], str]:
    """The newest saved Odds API pull FOR THIS WEEK, reduced to each player's consensus lines. A pull from
    another week is never shown as this week's."""
    from dfs import props, props_edge

    pulls = [(at, p) for at, p in props_edge.load_pulls() if p.get("week") == week]
    if not pulls:
        return [], f"No props pulled for week {week} yet (the weekend pull, or `dfs sync --props`)."
    stamp, payload = pulls[-1]
    quotes = props.parse_event_odds(payload.get("events", []), odds_format="american")
    if quotes.empty:
        return [], f"The week {week} props pull of {stamp:%a %b %d %H:%M} UTC had no player lines."
    consensus = props.consensus_lines(props.book_probabilities(quotes))
    matched, _unmatched = props.attach_players(consensus, edge[["Name", "Team", "Position"]])
    return pc.props_rows(matched), f"Week {week} pull of {stamp:%a %b %d %H:%M} UTC (one pull, not live)."


def gather_raw(edge: pd.DataFrame, week: int, season: int) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """`(PlayerCardRaw frame, PlayerCardRows frame, what could not be read)`."""
    missing: list[str] = []
    sleeper, fantasypros, log_frame = _load("sleeper"), _load("fantasypros"), _load("player_log")
    for label, frame in (("sleeper", sleeper), ("fantasypros", fantasypros), ("player_log", log_frame)):
        if frame is None:
            missing.append(label)
    raw = pc.raw_frame(edge, sleeper=sleeper, fantasypros=fantasypros, log=log_frame)

    try:
        props_rows, props_note = _props(edge, week)
    except Exception as e:  # noqa: BLE001 - one unreadable block must not stop the others
        props_rows, props_note = [], "Props could not be read."
        missing.append(f"props ({e})")
    table, signals_note = _signal_table(season)
    beneficiaries = None
    try:
        from dfs.edge_finder import OUTPUT_DIR

        beneficiaries = pd.read_csv(OUTPUT_DIR / "beneficiaries.csv")
    except (OSError, ValueError):
        pass
    signals = pc.signal_rows(edge, table, beneficiaries)
    rows = pc.rows_frame(props_rows, signals, props_note=props_note, signals_note=signals_note)
    return raw, rows, missing


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------


def _cells(frame: pd.DataFrame) -> list[list]:
    body = frame.astype(object).where(frame.notna(), "").values.tolist()
    return [list(frame.columns), *body]


def write_raw(client: SheetsClient, raw: pd.DataFrame, rows: pd.DataFrame) -> str:
    """Write both hidden tabs whole. They are created hidden on first write; a hidden tab stays writable."""
    for tab, frame in ((pc.RAW_TAB, raw), (pc.ROWS_TAB, rows)):
        existed = client.tab_exists(tab)
        client.write_tab(tab, _cells(frame))
        if not existed:
            from dfs.sheet_style import FAMILY_COLORS

            client.set_tab_properties(tab, color=FAMILY_COLORS["feed"], hidden=True)
    return f"{pc.RAW_TAB}: {len(raw)} players, {pc.ROWS_TAB}: {len(rows)} rows"


def refresh_raw(client: SheetsClient, cfg: Config, edge: pd.DataFrame, week: int, season: int) -> str:
    raw, rows, missing = gather_raw(edge, week, season)
    line = write_raw(client, raw, rows)
    return line + (f" (not on file: {', '.join(missing)})" if missing else "")


def refresh_raw_if_present(
    client: SheetsClient, cfg: Config, edge: pd.DataFrame, week: int, season: int
) -> tuple[bool, str]:
    """For `dfs sync`: refresh the raw tabs only when the sheet has the Player tab, and never fail the sync.
    Returns (ok, one line to print); (True, "") when there is no Player tab."""
    try:
        if not client.tab_exists(CARD_TAB):
            return True, ""
        return True, "Player card data: " + refresh_raw(client, cfg, edge, week, season)
    except Exception as e:  # noqa: BLE001 - a sync must never fail over the Player card
        log.warning("player card data not refreshed: %s", e)
        return False, f"Player card data not refreshed ({e}); `dfs setup player-card` rebuilds it."


def _rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {k: int(h[i : i + 2], 16) / 255 for k, i in (("red", 0), ("green", 2), ("blue", 4))}


def write_card(client: SheetsClient, cfg: Config, layout: pc.Layout, tab: str = CARD_TAB) -> str:
    """Write the formula rows and style them. Protection and the dropdown are separate steps."""
    from dfs.sheet_style import _HEADER_FMT, _PANEL_FMT, _TITLE_FMT, INK_MUTED, WHITE

    last = max(len(layout.rows) + 3, 60)
    client.write_tab(tab, layout.rows)
    client.clear_notes(tab, f"A1:{pc.LAST_COLUMN}{last}")
    client.format_range(
        tab,
        f"A1:{pc.LAST_COLUMN}{last}",
        {
            "backgroundColor": WHITE,
            "textFormat": {"bold": False, "italic": False, "fontSize": 10},
            "horizontalAlignment": "LEFT",
            "wrapStrategy": "OVERFLOW_CELL",
        },
    )
    client.set_column_widths(
        tab, {"A": LABEL_PX, **{chr(ord("B") + i): VALUE_PX for i in range(pc.COLUMN_COUNT - 1)}}
    )
    client.format_range(tab, "A1", _TITLE_FMT)
    client.format_range(
        tab,
        pc.NAME_CELL,
        {
            "textFormat": {"bold": True, "fontSize": 12},
            "backgroundColor": _rgb("#FFF4CC"),
            "borders": {s: {"style": "SOLID"} for s in ("top", "bottom", "left", "right")},
        },
    )
    client.format_range(
        tab,
        f"G1:{pc.LAST_COLUMN}1",
        {"textFormat": {"fontSize": 8, "foregroundColor": INK_MUTED}},
    )
    for row in layout.section_rows:
        client.format_range(tab, f"A{row}:{pc.LAST_COLUMN}{row}", _PANEL_FMT)
    for row in layout.label_rows:
        client.format_range(
            tab, f"A{row}:{pc.LAST_COLUMN}{row}", {**_HEADER_FMT, "horizontalAlignment": "LEFT"}
        )
    for row in layout.note_rows:
        client.format_range(
            tab,
            f"A{row}",
            {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}},
        )
    for rng, pattern in layout.patterns:
        client.format_range(
            tab,
            rng,
            {"numberFormat": {"type": "TEXT" if pattern == "@" else "NUMBER", "pattern": pattern}},
        )
    client.freeze(tab, rows=1, cols=0)
    return f"{tab}: built ({len(layout.rows)} rows)"


def _typed_name(client: SheetsClient, tab: str) -> str:
    """The player picked in `B1` before a rebuild ('' when the tab is new or empty)."""
    if not client.tab_exists(tab):
        return ""
    try:
        grid = client.read_range_unformatted(tab, pc.NAME_CELL)
    except SheetsError:
        return ""
    return str(grid[0][0]).strip() if grid and grid[0] else ""


def build_card(client: SheetsClient, cfg: Config, tab: str = CARD_TAB) -> list[str]:
    """Create or rebuild the Player tab: rows, dropdown over EdgeRaw's names, protection with `B1` exempt."""
    from dfs.derived import edge_sheet_letter
    from dfs.sheet_views import EXPOSURE_TAB, LINEUP_COUNT_CELL

    edge_tab = cfg.google_sheets.tab_mappings.get("edge", "EdgeRaw")
    lineups_tab = cfg.lineups.builder_tab
    exposure = None
    if client.tab_exists(EXPOSURE_TAB):
        header = client.read_range(EXPOSURE_TAB, "A1:G1")
        exposure = pc.exposure_columns(header[0] if header else [])
    layout = pc.build_layout(
        edge_tab=edge_tab,
        lineups_tab=lineups_tab,
        exposure_tab=EXPOSURE_TAB,
        exposure=exposure,
        lineup_count_cell=LINEUP_COUNT_CELL,
        name=_typed_name(client, tab),
    )
    results = [write_card(client, cfg, layout, tab)]
    name_letter = edge_sheet_letter("Name")
    client.set_range_dropdown_validation(
        tab, pc.NAME_CELL, source=f"{edge_tab}!${name_letter}$2:${name_letter}", strict=False
    )
    results.append(f"{tab}: {pc.NAME_CELL} dropdown over {edge_tab} names")
    results.append(protect_player_card(client, tab))
    results.append(place_after_edge_finder(client, tab))
    return results


def place_after_edge_finder(client: SheetsClient, tab: str = CARD_TAB) -> str:
    """Put the tab right after Edge Finder, coloured with the `decide` family. Only `dfs setup player-card`
    moves tabs."""
    from dfs.edge_finder_tab import EDGE_FINDER_TAB
    from dfs.sheet_style import FAMILY_COLORS

    tabs = [t.title for t in client.list_tabs()]
    if EDGE_FINDER_TAB not in tabs:
        return f"{tab}: left where it was ({EDGE_FINDER_TAB} not found)"
    index = tabs.index(EDGE_FINDER_TAB) + 1
    if tab in tabs and tabs.index(tab) < tabs.index(EDGE_FINDER_TAB):
        index -= 1
    client.set_tab_properties(tab, color=FAMILY_COLORS["decide"], index=index, hidden=False)
    return f"{tab}: placed right after {EDGE_FINDER_TAB}"


def setup(client: SheetsClient, cfg: Config, edge: pd.DataFrame, week: int, season: int) -> list[str]:
    """`dfs setup player-card`: the raw tabs first (the card's formulas read them), then the card."""
    return [
        "Player card data: " + refresh_raw(client, cfg, edge, week, season),
        *build_card(client, cfg),
    ]
