"""Read the inputs for, and write, the `Cockpit` tab (`cockpit.build_layout` is the pure middle).

The tab is Python's end to end: nothing on it is typed, and it holds no formula, so it is rewritten whole on
every refresh and cannot disturb Board, Exposure or anything Sam types. It is only ever REFRESHED by
`dfs sync --live` and `dfs lineups late-swap` when it already exists (`refresh_if_present`); creating it, and
placing it right after Board, is `dfs setup cockpit` (template first, then the live week, `dfs doctor` after),
so a structural change to the tab strip is always a deliberate step.

Every read is fail-soft per block: a tab that cannot be read becomes a line saying so on the Cockpit
(`CockpitInputs.unavailable`), never a failed sync.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from dfs import cockpit, late_swap_store, stakes
from dfs import sheet_dk_upload as dku
from dfs.cockpit import COCKPIT_TAB, DkEntry, Layout, PlanNumbers
from dfs.config import Config
from dfs.contests import series
from dfs.log import get_logger
from dfs.sheets import SheetsClient, SheetsError

log = get_logger("sheet_cockpit")

LAST_COLUMN = chr(ord("A") + cockpit.COLUMN_COUNT - 1)
CUE_PX, TEXT_PX = (
    64,
    170,
)  # column A, then the rest (text lines overflow across the empty cells to their right)
CUE_COLORS = {  # (text colour, fill) as hex
    cockpit.CUE_SWAP: ("#FFFFFF", "#A93B30"),
    cockpit.CUE_LOOK: ("#5C4400", "#F6E3A1"),
    cockpit.CUE_FILL: ("#5C4400", "#F6E3A1"),
    cockpit.CUE_OK: ("#798494", None),
}


# ---------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------


def _fee(text: object) -> float | None:
    cleaned = str(text).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def dk_entries(client: SheetsClient, tab: str) -> list[DkEntry]:
    """DK Upload's rows reduced to fee, format and picked lineup (header positions, never typed letters)."""
    rows = dku.read_tab_rows(client, tab)
    fee_at, format_at = dku.column_index("Entry Fee"), dku.column_index("Format")
    out = []
    for entry in rows.values():
        raw = entry.raw
        fmt = raw[format_at] if format_at < len(raw) else ""
        fee = _fee(raw[fee_at]) if fee_at < len(raw) else None
        out.append(DkEntry(entry.entry_id, fee, fmt, fmt in series.CASH_FORMATS, entry.lineup))
    return out


def plan_numbers(client: SheetsClient, cfg: Config, season: int) -> PlanNumbers:
    """The stakes plan the Bankroll tab's STAKES block shows, reduced to what the Cockpit needs."""
    from dfs.sheet_stakes import load_typed, read_inputs

    inputs = read_inputs(client, cfg.bankroll)
    typed = load_typed()
    plan = stakes.make_plan(
        cfg.stakes,
        budget=inputs.budget,
        bankroll=inputs.bankroll,
        stats=stakes.bucket_stats(typed),
        spent_so_far=inputs.spent,
        largest_entry_so_far=inputs.largest_entry,
    )
    return PlanNumbers(
        budget=plan.budget,
        spend=plan.spend,
        cash=plan.amounts.get(stakes.CASH),
        gpp=plan.amounts.get(stakes.GPP),
        entry_cap=plan.entry_cap,
        warnings=tuple(plan.warnings),
    )


def exposure_targets(client: SheetsClient, tab: str, last_row: int = 80) -> dict[str, float]:
    """Sam's typed Target per player on the Exposure tab, as fractions (`60%` and `0.6` both read 0.6)."""
    grid = client.read_range_unformatted(tab, f"A1:G{last_row}")
    if not grid:
        return {}
    header = [str(h).strip() for h in grid[0]]
    if "Name" not in header or "Target" not in header:
        return {}
    name_at, target_at = header.index("Name"), header.index("Target")
    out: dict[str, float] = {}
    for row in grid[1:]:
        if len(row) <= max(name_at, target_at):
            continue
        name, raw = str(row[name_at]).strip(), row[target_at]
        if not name or raw in ("", None):
            continue
        text = str(raw).replace("%", "").replace(",", "").strip()
        try:
            value = float(text)
        except ValueError:
            continue
        if "%" in str(raw) or value > 1:
            value /= 100
        out[name] = value
    return out


def gather(
    client: SheetsClient,
    cfg: Config,
    *,
    edge: pd.DataFrame,
    previous_edge: pd.DataFrame | None = None,
    now: datetime | None = None,
    season: int | None = None,
) -> cockpit.CockpitInputs:
    """Everything the tab needs. A part that cannot be read is left out and named in `unavailable`."""
    from dfs.nfl_calendar import current_season
    from dfs.week import parse_week_from_title

    now = now or datetime.now(UTC)
    title, _url = client.describe()
    try:
        week = parse_week_from_title(title)
    except ValueError:
        week = None
    unavailable: dict[str, str] = {}
    lineups = dku.read_lineups(client, cfg.lineups.builder_tab)  # without Lineups there is no cockpit: raise
    entries = None
    try:
        entries = dk_entries(client, cfg.lineups.upload_tab)
    except (SheetsError, OSError, ValueError, KeyError, IndexError) as e:
        unavailable["dk"] = str(e)
    plan = None
    try:
        plan = plan_numbers(client, cfg, season or current_season())
    except Exception as e:  # noqa: BLE001 - one unreadable block must not stop the others
        unavailable["plan"] = str(e)
    targets: dict[str, float] = {}
    try:
        from dfs.sheet_views import EXPOSURE_TAB

        targets = exposure_targets(client, EXPOSURE_TAB)
    except (SheetsError, OSError, ValueError, KeyError) as e:
        log.warning("cockpit: Exposure targets unreadable: %s", e)
    return cockpit.CockpitInputs(
        now=now,
        sheet_title=title,
        edge=edge,
        lineups=lineups,
        week=week,
        previous_edge=previous_edge,
        saved_swap=late_swap_store.load(),
        dk_entries=entries,
        plan=plan,
        targets=targets,
        unavailable=unavailable,
    )


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------


def _rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {k: int(h[i : i + 2], 16) / 255 for k, i in (("red", 0), ("green", 2), ("blue", 4))}


def write_cockpit(client: SheetsClient, layout: Layout, tab: str = COCKPIT_TAB) -> str:
    """Write the rows, then style them. The tab is reset first (it is rebuilt from scratch every time)."""
    from dfs.sheet_style import _HEADER_FMT, _PANEL_FMT, _TITLE_FMT, INK_MUTED, WHITE, _num

    last = max(len(layout.rows) + 5, 80)
    client.write_tab(tab, layout.rows)
    client.clear_notes(tab, f"A1:{LAST_COLUMN}{last}")
    client.format_range(
        tab,
        f"A1:{LAST_COLUMN}{last}",
        {
            "backgroundColor": WHITE,
            "textFormat": {"bold": False, "italic": False, "fontSize": 10},
            "horizontalAlignment": None,
            "numberFormat": None,
            "wrapStrategy": "OVERFLOW_CELL",
        },
    )
    client.set_column_widths(
        tab, {"A": CUE_PX, **{chr(ord("B") + i): TEXT_PX for i in range(cockpit.COLUMN_COUNT - 1)}}
    )
    client.format_range(tab, f"A{layout.title_row}", _TITLE_FMT)
    client.format_range(
        tab, f"A{layout.status_row}", {"textFormat": {"fontSize": 9, "foregroundColor": INK_MUTED}}
    )
    for row in layout.section_rows:
        client.format_range(tab, f"A{row}:{LAST_COLUMN}{row}", _PANEL_FMT)
    for row in layout.header_rows:
        client.format_range(tab, f"A{row}:{LAST_COLUMN}{row}", _HEADER_FMT)
    for row in layout.headline_rows:
        client.format_range(tab, f"B{row}", {"textFormat": {"bold": True, "fontSize": 11}})
    for row in layout.note_rows:
        client.format_range(
            tab, f"B{row}", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for row, cue in layout.cue_rows.items():
        text_color, fill = CUE_COLORS[cue]
        fmt: dict = {
            "textFormat": {"bold": cue != cockpit.CUE_OK, "fontSize": 9, "foregroundColor": _rgb(text_color)},
            "horizontalAlignment": "CENTER",
        }
        if fill:
            fmt["backgroundColor"] = _rgb(fill)
        client.format_range(tab, f"A{row}", fmt)
    for rng in layout.percent_cells:
        client.format_range(tab, rng, {**_num("0%", "PERCENT"), "horizontalAlignment": "RIGHT"})
    for rng in layout.number_cells:
        client.format_range(tab, rng, {**_num("0.0"), "horizontalAlignment": "RIGHT"})
    client.freeze(tab, rows=layout.status_row, cols=0)
    return f"{tab}: built ({len(layout.rows)} rows)"


def place_after_board(client: SheetsClient, tab: str = COCKPIT_TAB) -> str:
    """Put the tab right after Board in the strip, coloured with the `decide` family. Only `dfs setup cockpit`
    does this: a refresh never moves tabs."""
    from dfs.sheet_style import FAMILY_COLORS
    from dfs.sheet_views import BOARD_TAB

    tabs = [t.title for t in client.list_tabs()]
    if BOARD_TAB not in tabs:
        return f"{tab}: left where it was ({BOARD_TAB} not found)"
    index = tabs.index(BOARD_TAB) + 1
    if tab in tabs and tabs.index(tab) < tabs.index(BOARD_TAB):
        index -= 1  # moving it from before Board shifts Board left by one
    client.set_tab_properties(tab, color=FAMILY_COLORS["decide"], index=index, hidden=False)
    return f"{tab}: placed right after {BOARD_TAB}"


def refresh(
    client: SheetsClient,
    cfg: Config,
    *,
    edge: pd.DataFrame,
    previous_edge: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> str:
    """Gather and write. Raises on a sheet error: callers decide how soft to be."""
    inputs = gather(client, cfg, edge=edge, previous_edge=previous_edge, now=now)
    return write_cockpit(client, cockpit.build_layout(inputs))


def refresh_if_present(
    client: SheetsClient,
    cfg: Config,
    *,
    edge: pd.DataFrame,
    previous_edge: pd.DataFrame | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """For `sync --live` and `late-swap`: refresh the Cockpit only when the sheet has one, and never fail the
    command. Returns (ok, one line to print); (True, "") when there is no Cockpit tab to refresh."""
    try:
        if not client.tab_exists(COCKPIT_TAB):
            return True, ""
        return True, refresh(client, cfg, edge=edge, previous_edge=previous_edge, now=now)
    except Exception as e:  # noqa: BLE001 - a sync or late-swap run must never fail over the Cockpit
        log.warning("cockpit not refreshed: %s", e)
        return False, f"Cockpit not refreshed ({e}); `dfs setup cockpit` rebuilds it."
