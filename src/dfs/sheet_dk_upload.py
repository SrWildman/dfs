"""The DK Upload tab as `dfs entries fill` uses it: one row per Classic main-slate entry.

**Layout** (row 1 = `lineups.DK_UPLOAD_SHEET_HEADER`). DK's own columns stay first and in DK's order (`Entry
ID, Contest Name, Contest ID, Entry Fee`, the nine slots) so `dfs export` (which reads them by position) and
`dfs doctor`'s header check are untouched; four columns follow: `Format` (the contest's series format),
`Lineup` (a typed dropdown, `L1`..`L20`, filled **only by Sam**), `Suggested` (the tool's read-only guess,
grey, never copied into the dropdown) and `Why`. Every position here is derived from the header tuple
(`column_index`/`column_letter_of`), never typed as a letter.

The slots fill from the chosen lineup on the next `entries fill` run (a formula would need DK's ids, which
only the entries file carries). Matching is by Entry ID, so re-running never loses a dropdown Sam set.

The Lineup column is the one typed cell on the tab: pale-yellow (`INPUT_BG`, the workbook's single "type here"
cue), a dropdown, and carved out of the tab's warning-only protection (`unprotected_ranges`). `dfs doctor`
checks all three.

The Lineups tab is read here too (`read_lineups`): each block's nine typed names, its Cash / GPP marker and
the Total row's Cash %, Top 1%, CalPts, ProjPts (`Pts`) and `Own%`, all found by header name.
"""

from __future__ import annotations

from dfs.entries import SheetLineup, TabRow, lineup_choice
from dfs.lineups import DK_UPLOAD_EXTRA_HEADERS, DK_UPLOAD_SHEET_HEADER
from dfs.models import ROSTER_SLOTS
from dfs.sheet_lineup_sim import MARKER_OPTIONS, sim_slots
from dfs.sheets import SheetsClient, column_letter
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS

FIRST_ROW = 2
LAST_ROW = 500  # entries beyond this do not fit; `fill` says so rather than dropping them silently
LINEUP_CHOICES = [f"L{i}" for i in range(1, len(LINEUPS_NAME_BLOCKS) + 1)]
LINEUP_HEADER, SUGGESTED_HEADER, WHY_HEADER, FORMAT_HEADER = "Lineup", "Suggested", "Why", "Format"
SLOT_FIRST = DK_UPLOAD_SHEET_HEADER.index(ROSTER_SLOTS[0], 4)  # the first of DK's nine slot columns


def column_index(name: str) -> int:
    return DK_UPLOAD_SHEET_HEADER.index(name)


def column_letter_of(name: str) -> str:
    return column_letter(column_index(name))


def lineup_range(last_row: int = LAST_ROW) -> str:
    """The dropdown's cells (`O2:O500` on today's layout)."""
    letter = column_letter_of(LINEUP_HEADER)
    return f"{letter}{FIRST_ROW}:{letter}{last_row}"


def unprotected_ranges() -> list[str]:
    """What Sam types on DK Upload: the Lineup dropdown, and nothing else."""
    return [lineup_range()]


LINEUP_NOTE = (
    "LINEUP: you choose which lineup goes in this entry (L1..L20 are the lineups on the Lineups tab). "
    "The tool never fills this column. Then run `dfs entries fill` again: it writes that lineup's nine "
    "players into the slots to the left, and `--write` makes the upload file."
)
SUGGESTED_NOTE = (
    "SUGGESTED: the tool's guess, by contest format (the Cash-marked lineup with the best Cash % for H2H, "
    "50/50 and Double Up; the GPP-marked one with the best Top 1% for GPPs; never the same lineup twice "
    "in one contest). It is only a hint: it is never copied into Lineup and the export never reads it."
)


def read_tab_rows(client: SheetsClient, tab: str, last_row: int = LAST_ROW) -> dict[str, TabRow]:
    """The tab's entries by Entry ID: their Lineup choice and the slots now on the tab."""
    width = len(DK_UPLOAD_SHEET_HEADER)
    grid = client.read_range(tab, f"A{FIRST_ROW}:{column_letter(width - 1)}{last_row}")
    lineup_at = column_index(LINEUP_HEADER)
    out: dict[str, TabRow] = {}
    for offset, row in enumerate(grid):
        cells = [str(c).strip() for c in row] + [""] * (width - len(row))
        entry_id = cells[0]
        if not entry_id:
            continue
        out[entry_id] = TabRow(
            entry_id=entry_id,
            lineup=cells[lineup_at],
            cells=tuple(cells[SLOT_FIRST : SLOT_FIRST + len(ROSTER_SLOTS)]),
            row=FIRST_ROW + offset,
            raw=tuple(cells),
        )
    return out


def tab_grid(rows, kept: list[TabRow]) -> list[list[str]]:
    """The tab's data rows: one per planned entry (file order; `rows` are `entries.RowPlan`), then the rows
    the file no longer lists, written back as they were (a Lineup Sam set is never dropped)."""
    grid = []
    for r in rows:
        file = r.entry.file
        row = [""] * len(DK_UPLOAD_SHEET_HEADER)
        row[0:4] = [file.entry_id, file.contest_name, file.contest_id, file.entry_fee]
        row[SLOT_FIRST : SLOT_FIRST + len(ROSTER_SLOTS)] = r.cells
        row[column_index(FORMAT_HEADER)] = r.entry.format
        row[column_index(LINEUP_HEADER)] = f"L{r.lineup}" if r.lineup else ""
        row[column_index(SUGGESTED_HEADER)] = r.suggested
        row[column_index(WHY_HEADER)] = r.why
        grid.append(row)
    grid.extend(list(t.raw) for t in kept)
    return grid


def write_tab_rows(client: SheetsClient, tab: str, grid: list[list[str]], previous_rows: int) -> str:
    """One `update_range` over the rows now and any the tab used to have (padded blank), so there is no moment
    between a clear and a write in which a Lineup Sam set is missing. `fill` raises before this when the
    entries do not fit."""
    if len(grid) + FIRST_ROW - 1 > LAST_ROW:
        raise ValueError(f"{len(grid)} rows do not fit DK Upload's {LAST_ROW - FIRST_ROW + 1} rows")
    width = len(DK_UPLOAD_SHEET_HEADER)
    padded = grid + [[""] * width for _ in range(max(0, previous_rows - len(grid)))]
    if not padded:
        return f"{tab}: no entries to write"
    last = FIRST_ROW + len(padded) - 1
    client.update_range(tab, f"A{FIRST_ROW}:{column_letter(width - 1)}{last}", padded)
    return f"{tab}: {len(grid)} entry row(s) written"


def write_changed_slots(client: SheetsClient, tab: str, changed, tab_rows: dict[str, TabRow]) -> str:
    """`--again`: rewrite the nine slots of the rows in `changed` (`entries.RowPlan`) and nothing else: the
    Lineup dropdown, the Suggested guess and every other row stay exactly as they are."""
    first = column_letter(SLOT_FIRST)
    last = column_letter(SLOT_FIRST + len(ROSTER_SLOTS) - 1)
    updates = {
        f"{first}{tab_rows[r.entry.entry_id].row}:{last}{tab_rows[r.entry.entry_id].row}": [list(r.cells)]
        for r in changed
        if r.entry.entry_id in tab_rows
    }
    client.update_ranges(tab, updates)
    return f"{tab}: slots rewritten on {len(updates)} entr{'y' if len(updates) == 1 else 'ies'}"


def build_dk_upload(client: SheetsClient, tab: str, *, last_row: int = LAST_ROW) -> list[str]:
    """`dfs setup dk-upload`: put the entries layout on `tab`. Idempotent: the header row (DK's nine plus
    Format / Lineup / Suggested / Why), widths and the freeze, the Lineup dropdown with its pale-yellow cue,
    the grey Suggested / Why columns and their notes. Never touches an entry row's values. Protection is
    `sheet_protection.protect_dk_upload` (the command runs it right after)."""
    from dfs.sheet_style import BAND_BG, INK_MUTED, INPUT_BG, style_flat_tab

    if not client.tab_exists(tab):
        return [f"{tab}: not present -- skipped"]
    width = len(DK_UPLOAD_SHEET_HEADER)
    client.ensure_column_capacity(tab, width)
    client.ensure_row_capacity(tab, last_row)
    client.update_range(tab, f"A1:{column_letter(width - 1)}1", [list(DK_UPLOAD_SHEET_HEADER)])
    results = [f"{tab}: header row written ({', '.join(DK_UPLOAD_EXTRA_HEADERS)} after DK's columns)"]
    results.append(style_flat_tab(client, tab, last_row=last_row))
    lineup, suggested, why = (column_letter_of(h) for h in (LINEUP_HEADER, SUGGESTED_HEADER, WHY_HEADER))
    client.format_range(
        tab,
        lineup_range(last_row),
        {"backgroundColor": INPUT_BG, "horizontalAlignment": "CENTER", "textFormat": {"bold": True}},
    )
    client.set_dropdown_validation(tab, lineup_range(last_row), LINEUP_CHOICES)
    client.format_range(
        tab,
        f"{suggested}{FIRST_ROW}:{why}{last_row}",
        {"backgroundColor": BAND_BG, "textFormat": {"foregroundColor": INK_MUTED}},
    )
    client.set_column_widths(
        tab,
        {
            column_letter_of(FORMAT_HEADER): 110,
            lineup: 70,
            suggested: 90,
            why: 330,
            column_letter(column_index("Contest Name")): 280,
        },
    )
    client.set_cell_notes(tab, {f"{lineup}1": LINEUP_NOTE, f"{suggested}1": SUGGESTED_NOTE})
    results.append(
        f"{tab}: {lineup_range(last_row)} is the Lineup dropdown ({LINEUP_CHOICES[0]}..{LINEUP_CHOICES[-1]}, "
        f"pale-yellow); {suggested}:{why} are grey and read-only"
    )
    return results


# -- the Lineups tab -----------------------------------------------------------------------------------------


def _number(value) -> float | None:
    if isinstance(value, bool) or value in ("", None):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace("%", "").replace(",", ""))
    except ValueError:
        return None


def read_lineups(client: SheetsClient, tab: str) -> list[SheetLineup]:
    """Every Lineups block (a lineup per `LINEUPS_NAME_BLOCKS` entry, `L1`..), with its Cash / GPP marker
    (column A of its Total row) and the Total row's Cash %, Top 1%, CalPts, ProjPts (`Pts`) and `Own%`, found
    by header name. A column the sheet does not have reads as None."""
    header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
    top = client.read_range(tab, f"A{header_row}:{header_row}")
    header = [str(h).strip() for h in (top[0] if top else [])]
    index = {name: header.index(name) for name in ("Pts", "CalPts", "Own%") if name in header}
    sim = sim_slots(header)
    if sim:
        index["Cash %"], index["Top 1%"] = sim[2], sim[3]
    last_col = max([0, *index.values()])
    last_total = LINEUPS_NAME_BLOCKS[-1][1] + 1
    grid = client.read_range_unformatted(tab, f"A1:{column_letter(last_col)}{last_total}")

    def cell(row: int, col: int):
        line = grid[row - 1] if row - 1 < len(grid) else []
        return line[col] if col < len(line) else ""

    out = []
    for number, (start, end) in enumerate(LINEUPS_NAME_BLOCKS, start=1):
        names = tuple(str(cell(r, 0)).strip() for r in range(start, end + 1))
        marker = str(cell(end + 1, 0)).strip()
        values = {name: _number(cell(end + 1, col)) for name, col in index.items()}
        out.append(
            SheetLineup(
                number=number,
                names=names,
                marker=marker if marker in MARKER_OPTIONS else "",
                cash_pct=values.get("Cash %"),
                top1=values.get("Top 1%"),
                cal_pts=values.get("CalPts"),
                proj_pts=values.get("Pts"),
                own=values.get("Own%"),
            )
        )
    return out


__all__ = [
    "LINEUP_CHOICES",
    "build_dk_upload",
    "lineup_choice",
    "read_lineups",
    "read_tab_rows",
    "tab_grid",
    "unprotected_ranges",
    "write_tab_rows",
]
