"""Read the inputs for, compute, and write the `Stacks` tab (`stacks_tab.build_layout` is the pure middle).

Like the Cockpit, the tab is Python's end to end: nothing on it is typed and it holds no formula, so it is
rewritten whole on every refresh and cannot disturb Suggested or anything Sam types. `dfs stacks` builds it
(and is what `dfs setup stacks` runs once to create and place it right after Suggested); `dfs sync` refreshes
it only where it already exists (`refresh_if_present`), so a structural change to the tab strip is always a
deliberate step (template first, then the live week, `dfs doctor` after).

The ranking needs the fitted field model (`stacks_inputs.FIELD_MODEL_FILE`); a sheet refresh never fits it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from dfs import stacks, stacks_inputs, stacks_tab
from dfs.config import Config
from dfs.log import get_logger
from dfs.sheets import SheetsClient
from dfs.stacks_tab import STACKS_TAB, Layout, StackTabInputs

log = get_logger("sheet_stacks")

LAST_COLUMN = chr(ord("A") + stacks_tab.COLUMN_COUNT - 1)
COLUMN_PX = {
    "A": 64,
    "B": 150,
    "C": 230,
    "D": 90,
    "E": 70,
    "F": 60,
    "G": 70,
    "H": 60,
    "I": 70,
    "J": 70,
    "K": 100,
    "L": 70,
    "M": 330,
}
CUE_COLORS = {stacks_tab.CUE_BUILD: ("#FFFFFF", "#15704A")}  # (text colour, fill)


class StacksUnavailable(Exception):
    """The tab cannot be built (no fitted field model, no standings): the message says what to run."""


def _rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {k: int(h[i : i + 2], 16) / 255 for k, i in (("red", 0), ("green", 2), ("blue", 4))}


# ---------------------------------------------------------------------------------------------
# Computing
# ---------------------------------------------------------------------------------------------


def compute(
    edge: pd.DataFrame,
    *,
    depth_rows=None,
    gsis_by_id=None,
    week: int | None = None,
    n_sims: int = stacks.DEFAULT_SIMS,
    seed: int = 0,
):
    """(StackResult, LiveSlate, FieldModel) for the synced edge frame. Raises `StacksUnavailable` when no
    field model has been fitted."""
    model = stacks_inputs.load_field_model()
    if model is None:
        raise StacksUnavailable(
            "no fitted field model yet: run `dfs stacks --refit` once (about two minutes)"
        )
    live = stacks_inputs.build_live_slate(edge, depth_rows, gsis_by_id, week)
    chalk = stacks_inputs.top_chalk(live.slate, model.herd)
    gpp = stacks.build_gpp_field(live.slate, model.rates, model.herd, chalk=chalk, seed=seed)
    result = stacks.rank_stacks(
        live.slate, live.specs, gpp, n_sims=n_sims, seed=seed, game_info=live.game_info
    )
    return result, live, model


def tab_inputs(
    result: stacks.StackResult,
    live: stacks_inputs.LiveSlate,
    model,
    *,
    sheet_title: str,
    now: datetime | None = None,
) -> StackTabInputs:
    retro = stacks_inputs.load_retro()
    return StackTabInputs(
        result=result,
        sheet_title=sheet_title,
        now=now or datetime.now(UTC),
        week=live.week,
        ownership_published=live.ownership_published,
        field_weeks=tuple(model.weeks),
        retro_line=retro["line"] if retro else "",
        verdict=retro.get("verdict") if retro else None,
    )


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------


def write_stacks(client: SheetsClient, layout: Layout, tab: str = STACKS_TAB) -> str:
    """Write the rows, then style them. The tab is reset first (it is rebuilt from scratch every time)."""
    from dfs.sheet_style import _HEADER_FMT, _PANEL_FMT, _TITLE_FMT, INK_MUTED, WHITE, _num

    last = max(len(layout.rows) + 5, 60)
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
    client.set_column_widths(tab, COLUMN_PX)
    client.format_range(tab, f"A{layout.title_row}", _TITLE_FMT)
    client.format_range(
        tab, f"A{layout.status_row}", {"textFormat": {"fontSize": 9, "foregroundColor": INK_MUTED}}
    )
    for row in layout.section_rows:
        client.format_range(tab, f"A{row}:{LAST_COLUMN}{row}", _PANEL_FMT)
    for row in layout.header_rows:
        client.format_range(tab, f"A{row}:{LAST_COLUMN}{row}", _HEADER_FMT)
    for row in layout.note_rows:
        client.format_range(
            tab, f"B{row}", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for row, cue in layout.cue_rows.items():
        text_color, fill = CUE_COLORS[cue]
        client.format_range(
            tab,
            f"A{row}",
            {
                "textFormat": {"bold": True, "fontSize": 9, "foregroundColor": _rgb(text_color)},
                "horizontalAlignment": "CENTER",
                "backgroundColor": _rgb(fill),
            },
        )
    for rng, pattern, kind in layout.formats:
        client.format_range(tab, rng, {**_num(pattern, kind), "horizontalAlignment": "RIGHT"})
    client.freeze(tab, rows=layout.status_row, cols=0)
    return f"{tab}: built ({len(layout.rows)} rows)"


def place_after_suggested(client: SheetsClient, tab: str = STACKS_TAB) -> str:
    """Put the tab right after Suggested in the strip, coloured with the `build` family. Only `dfs setup
    stacks` does this: a refresh never moves tabs."""
    from dfs.sheet_style import FAMILY_COLORS
    from dfs.sheet_suggested import TAB as SUGGESTED_TAB

    tabs = [t.title for t in client.list_tabs()]
    if SUGGESTED_TAB not in tabs:
        return f"{tab}: left where it was ({SUGGESTED_TAB} not found)"
    index = tabs.index(SUGGESTED_TAB) + 1
    if tab in tabs and tabs.index(tab) < tabs.index(SUGGESTED_TAB):
        index -= 1  # moving it from before Suggested shifts Suggested left by one
    client.set_tab_properties(tab, color=FAMILY_COLORS["build"], index=index, hidden=False)
    return f"{tab}: placed right after {SUGGESTED_TAB}"


def refresh(
    client: SheetsClient,
    cfg: Config,
    *,
    edge: pd.DataFrame,
    depth_rows=None,
    gsis_by_id=None,
    now: datetime | None = None,
    n_sims: int = stacks.DEFAULT_SIMS,
) -> str:
    """Compute and write. Raises on a sheet error or a missing field model: callers decide how soft to be."""
    from dfs.week import parse_week_from_title

    title, _url = client.describe()
    try:
        week = parse_week_from_title(title)
    except ValueError:
        week = None
    result, live, model = compute(
        edge, depth_rows=depth_rows, gsis_by_id=gsis_by_id, week=week, n_sims=n_sims
    )
    layout = stacks_tab.build_layout(tab_inputs(result, live, model, sheet_title=title, now=now))
    return write_stacks(client, layout)


def refresh_if_present(
    client: SheetsClient,
    cfg: Config,
    *,
    edge: pd.DataFrame,
    depth_rows=None,
    gsis_by_id=None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """For `dfs sync`: refresh the Stacks tab only when the sheet has one, and never fail the command.
    Returns (ok, one line to print); (True, "") when there is no Stacks tab to refresh."""
    try:
        if not client.tab_exists(STACKS_TAB):
            return True, ""
        return True, refresh(client, cfg, edge=edge, depth_rows=depth_rows, gsis_by_id=gsis_by_id, now=now)
    except StacksUnavailable as e:
        return False, f"Stacks not refreshed: {e}."
    except Exception as e:  # noqa: BLE001 - a sync must never fail over the Stacks tab
        log.warning("stacks not refreshed: %s", e)
        return False, f"Stacks not refreshed ({e}); `dfs stacks` rebuilds it."
