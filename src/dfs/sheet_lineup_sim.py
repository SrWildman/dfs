"""The lineup simulator on the Lineups tab: Typical, Big night, Cash % and Top 1% for every built lineup.

**Lines (round F).** Cash % and Top 1% are no longer scored against fixed lines: they move with
the slate (`sim_inputs.resolve_lines`, `dfs.sim.field`). Cash % is the chance the lineup beats the double-up
cash line (the field's average score plus 0.6) and Top 1% the chance it reaches the large-field
top-1% line (the field's average plus 63.2); before ownership publishes, or with `dfs sync --fixed-lines`,
they are the old fixed lines (the typed Cash Line median, `[sim] gpp_target`). **The fourth label is always
`Top 1%`** (it was `190+ %`, a header that followed `gpp_target`, until the Week 6 relabel); with the
fixed lines the number under it is the chance of reaching `gpp_target`, and its hover note says so.

**Where they sit** (usability round, slice 5; moved right two columns on 2026-10-17 to make room for the
`EspnPts`/`PropPts` totals). On each lineup's **Total row**, in the four columns that are blank there
(`Val`, `ValAdj`, `Hit3x%`, `Boom%`, found by header name: `SIM_SLOT_HEADERS`), with small
labels on the **Remaining row** under them (`Typical`, `Big night`, `Cash %`, `Top 1%`; they were `Median`,
`p90`, `P(cash)`, `P(190+)` until the Week 6 round), scoreboard style. They are numbers, so they colour:
Cash % goes green at 50% or more and Top 1% at 2% or more (twice what an average entry has). They used to
sit about twenty columns to the right (after `Min Unique`); those columns are retired
(`sheet_reorder.retire_lineup_sim_columns`) and the portfolio line moved to the Board's Pool summary.

**The type marker.** Column A of every Total row is a pale-yellow input dropdown, `Cash` / `GPP` (blank =
both). The number that matches the lineup's type is highlighted, and `dfs lineups late-swap` reads it as
that lineup's default `--goal` (the flag still overrides it, and both deltas are still shown).

- **Cash line**: the median of every typed `Cash Line` in Results this season (`sim_inputs`), read only; the
  Cash % label's note says which ("cash line 141.3 (season median, 4 weeks)").
- **GPP target**: `[sim] gpp_target` in config.toml (default 190), the FIXED top-end line (used with
  `--fixed-lines` and before ownership publishes); it no longer names the label, which is `Top 1%`.
- **Draws**: `sim_inputs.N_SIMS` (20,000) with a fixed seed, so the numbers do not jitter between syncs. All
  the built lineups are simulated in one run: a player in two lineups is one random variable, which is what
  makes the portfolio numbers (on the Board) mean something.
- **Not conditioned on games already played**: a lineup with a locked player is simulated from his pre-game
  distribution, like everything else on the sheet before kickoff. A lineup with a blank slot or a name
  EdgeRaw does not know is left blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dfs import perf
from dfs.sheets import SheetsClient, column_letter

if TYPE_CHECKING:  # the simulator stack (numpy, scipy) is imported only when a simulation actually runs
    from dfs.config import SimLinesConfig
    from dfs.sim_inputs import CashLine, SimLines

N_SIMS = 20000  # mirrored by sim_inputs.N_SIMS (a test pins them equal)
SEED = 0

# What the four numbers are called on the sheet (plain words, Slice 1 of the Week 6 round).
MEDIAN_HEADER = "Typical"
P90_HEADER = "Big night"
P_CASH_HEADER = "Cash %"
# What the retired far-right columns were called, and what the labels were before the rename: both still
# count as "a retired sim header" so an old sheet is cleaned up whichever vintage it is.
OLD_MEDIAN_HEADER = "Median"
OLD_P90_HEADER = "p90"
OLD_P_CASH_HEADER = "P(cash)"
TOP1_HEADER = "Top 1%"  # the fourth label; it was `190+ %` (following `[sim] gpp_target`) until Week 6
# The retired far-right column (`P(190+)`, `P(187.5+)`): matched so an old sheet is cleaned up.
GPP_HEADER_RE = re.compile(r"^P\((\d+(?:\.\d+)?)\+\)$")
# The columns the four numbers sit in: blank on a Total row, the four right after the shadow-projection totals
# (`EspnPts`, `PropPts`; they were `AggPts`..`ValAdj` until those two columns went in on 2026-10-17). Every
# reader finds the run by these names (`sim_slots`), never by position, so moving it is this one line.
SIM_SLOT_HEADERS = ("Val", "ValAdj", "Hit3x%", "Boom%")
# What the run was called before: `write_labels` clears the stale labels the move leaves in those columns.
RETIRED_SLOT_HEADERS = ("AggPts", "CalPts")
MARKER_HEADER = "Name"  # the marker is column A of the Total row: under the Name column
MARKER_OPTIONS = ["Cash", "GPP"]
P_CASH_GREEN = 0.5  # P(cash) at or above this reads green
P_GPP_GREEN = 0.02  # Top 1% at or above this reads green: twice the 1% an average entry has
CASH_WEAK_BELOW = 0.40  # a lineup marked Cash with Cash % under this gets an `Issues` warning


# What the retired far-right columns were called (`sheet_reorder.retire_lineup_sim_columns` deletes them).
RETIRED_SIM_HEADERS = [OLD_MEDIAN_HEADER, OLD_P90_HEADER, OLD_P_CASH_HEADER]


def is_retired_sim_header(name: object) -> bool:
    text = str(name).strip()
    return text in RETIRED_SIM_HEADERS or bool(GPP_HEADER_RE.match(text))


def labels() -> list[str]:
    """The four labels under the numbers, in slot order."""
    return [MEDIAN_HEADER, P90_HEADER, P_CASH_HEADER, TOP1_HEADER]


def sim_slots(header: list) -> list[int] | None:
    """The indices of the four slot columns, or None unless all four exist as one run (by header name)."""
    names = [str(h).strip() for h in header]
    if any(h not in names for h in SIM_SLOT_HEADERS):
        return None
    indices = [names.index(h) for h in SIM_SLOT_HEADERS]
    return indices if indices == list(range(indices[0], indices[0] + len(indices))) else None


def stale_label_cells(header: list, name_blocks: list[tuple[int, int]]) -> dict[str, list[list]]:
    """Blank updates for the Remaining-row cells the scoreboard used to sit in (`RETIRED_SLOT_HEADERS`), so a
    sheet that moved it (2026-10-17) keeps no `Typical` / `Big night` label behind. Found by header name."""
    names = [str(h).strip() for h in header]
    letters = [column_letter(names.index(h)) for h in RETIRED_SLOT_HEADERS if h in names]
    return {f"{letter}{end + 2}": [[""]] for letter in letters for _start, end in name_blocks}


def lineup_sim_notes(
    target: float, cash_basis: str | None = None, lines: SimLines | None = None
) -> dict[str, str]:
    """The notes on the four labels, keyed by label text (so the GPP note carries the current target and the
    Cash % note the basis of the cash line, e.g. "cash line 141.3 (season median, 4 weeks)"). With `lines`
    that move with the slate (`SimLines.relative`) the Cash % and fourth-number notes describe those lines
    instead of the fixed ones."""
    notes = _fixed_notes(target, cash_basis)
    if lines is not None and lines.relative:
        offsets = lines.shown_field
        notes[P_CASH_HEADER] = (
            "CASH %: the chance this lineup beats the double-up cash line. That line moves with the slate: "
            f"it sits about {offsets.cash_offset:g} points above the field's average score, which the "
            "simulator works out in every one of its 20,000 draws from projected ownership (the players "
            "your lineup shares with the field move the line with it). 50% or more is a solid cash build "
            "and turns green. The Cash or GPP marker in column A of the Total row says which number this "
            "lineup is judged on."
        )
        notes[TOP1_HEADER] = (
            "TOP 1%: the chance this lineup finishes in the top 1% of a large-field GPP: the field's average "
            f"score plus {offsets.top_offset:g} points, a line that moves with the slate and is worked out "
            "in every draw from projected ownership. An average entry has about 1%, so 2% or more turns "
            "green and the more above that, the better a GPP build. Not adjusted for games already played."
        )
    return notes


def _fixed_notes(target: float, cash_basis: str | None) -> dict[str, str]:
    return {
        MEDIAN_HEADER: (
            "TYPICAL: this lineup's usual score. Half of the simulated slates land above it, half below."
        ),
        P90_HEADER: ("BIG NIGHT: what this lineup scores on a great slate; only 1 slate in 10 does better."),
        P_CASH_HEADER: (
            "CASH %: the chance this lineup beats a typical cash line, the median of the Cash Lines you "
            f"typed on Results.{f' Today: {cash_basis}.' if cash_basis else ''} 50% or more is a solid cash "
            "build and turns green. The Cash or GPP marker in column A of the Total row says which number "
            "this lineup is judged on."
        ),
        TOP1_HEADER: (
            "TOP 1%: with a fixed line (ownership is not published yet, or --fixed-lines) this is the "
            f"chance the lineup scores {target:g} or more, a typical top-end pace (config.toml [sim] "
            "gpp_target sets the number). Once ownership publishes it is the chance of a top-1% finish "
            "against a line that moves with the slate. 2% or more turns green. Simulated 20,000 times from "
            "the model, not adjusted for games already played."
        ),
    }


@dataclass
class LineupSimReport:
    """What one run did, for the sync's summary line."""

    simulated: int
    skipped: int
    cash_line: CashLine
    gpp_target: float
    portfolio: dict[str, float] | None
    lines: SimLines | None = None

    def line(self) -> str:
        if self.simulated == 0:
            return "Lineup simulator: no complete lineup on Lineups yet -- nothing to simulate."
        p = self.portfolio or {}
        if self.lines is not None and self.lines.mode != "fixed":
            return (
                f"Lineup simulator: {self.simulated} lineup(s) ({self.skipped} unbuilt). {self.lines.note} "
                f"P(>=1 cash) {p.get('p_any_cash', 0):.0%}, {p.get('expected_cashes', 0):.1f} expected "
                f"cashes, P(>=1 {self.lines.top_label}) {p.get('p_any_gpp', 0):.0%}."
            )
        prefix = "--fixed-lines: " if self.lines is not None else ""
        return (
            f"Lineup simulator: {prefix}{self.simulated} lineup(s) ({self.skipped} unbuilt), cash line "
            f"{self.cash_line.value:g} ({self.cash_line.note}), GPP target {self.gpp_target:g}; "
            f"P(>=1 cash) {p.get('p_any_cash', 0):.0%}, {p.get('expected_cashes', 0):.1f} expected cashes, "
            f"P(>=1 {self.gpp_target:g}+) {p.get('p_any_gpp', 0):.0%}."
        )


def _values_for(stats) -> list[float]:  # noqa: ANN001 - dfs.sim.simulate.LineupStats
    """Median, p90, P(cash), P(GPP) for one lineup."""
    return [
        round(stats.quantiles[0.5], 1),
        round(stats.quantiles[0.9], 1),
        round(stats.p_cash, 4),
        round(stats.p_gpp, 4),
    ]


def write_lineup_sim(
    client: SheetsClient,
    tab: str,
    *,
    header_row: int,
    name_blocks: list[tuple[int, int]],
    header_repeats_at: list[int] | None = None,
    edge,  # noqa: ANN001 - the synced EdgeRaw frame
    depth_rows,  # noqa: ANN001 - nflverse depth rows, or None
    gsis_by_id: dict | None,
    results_tab: str,
    week: int,
    gpp_target: float,
    n_sims: int = N_SIMS,
    seed: int = SEED,
    lines_config: SimLinesConfig | None = None,
    fixed_lines: bool = False,
) -> LineupSimReport | str:
    """Simulate every complete lineup on `tab` and write the four numbers on each Total row and their labels
    on the Remaining row below. Returns the report, or a message when the tab or its columns are missing.

    With `lines_config` (`[sim.lines]`) the numbers are scored against lines that move with the slate, unless
    `fixed_lines` or ownership is blank (`sim_inputs.resolve_lines`); without it, against the fixed lines."""
    from dfs.sim.field import has_ownership
    from dfs.sim.simulate import simulate_lineups
    from dfs.sim_inputs import (
        build_specs,
        cash_line_from_results,
        lineup_from_names,
        ownership_by_name,
        portfolio_summary,
        resolve_lines,
    )

    last_row = name_blocks[-1][1] + 2
    with perf.phase("lineup sim: read sheet"):
        try:  # one round trip for the header, the names and Results; a missing tab shows up here
            header_rows, name_rows, results_rows = client.batch_read_ranges(
                [(tab, f"A{header_row}:{header_row}"), (tab, f"A2:A{last_row}"), (results_tab, "A1:J40")]
            )
        except Exception as e:  # noqa: BLE001 - gspread raises its own APIError for an unknown tab
            return f"{tab}: lineup simulator skipped -- could not read {tab} / {results_tab} ({e})"
    header = list(header_rows[0]) if header_rows else []
    slots = sim_slots(header)
    if slots is None:
        return f"{tab}: columns {list(SIM_SLOT_HEADERS)} not found as one run -- lineup simulator skipped"
    if "Name" not in header:
        return f"{tab}: no Name column -- lineup simulator skipped"

    cash_line = cash_line_from_results(
        results_rows[0] if results_rows else [], results_rows[1:] if results_rows else [], before_week=week
    )

    def name_at(row: int) -> str:
        index = row - 2
        return name_rows[index][0].strip() if index < len(name_rows) and name_rows[index] else ""

    typed = {name_at(r) for start, end in name_blocks for r in range(start, end + 1)} - {""}
    first_letter, last_letter = column_letter(slots[0]), column_letter(slots[-1])
    if not typed:
        # No lineup has a player yet (every week until the lineups are built): there is nothing to simulate,
        # and a sync should not make ~40 writes (and a chance to hang) to write blanks. Unless an earlier
        # sync left numbers that are now stale, then they are blanked below.
        with perf.phase("lineup sim: check for stale numbers"):
            cells = client.read_range(tab, f"{first_letter}2:{last_letter}{last_row}")
        if not any(str(cell).strip() for row in cells for cell in row):
            # Nothing to simulate and no stale numbers, but the labels under the numbers may be an older
            # wording (`Median`, `p90`, ...): put the current ones on every Remaining row.
            remaining = name_blocks[0][1] + 2
            shown = client.read_range(tab, f"{first_letter}{remaining}:{last_letter}{remaining}")
            if [str(c).strip() for c in (shown[0] if shown else [])] == labels():
                return f"{tab}: no lineup has players yet -- lineup simulator skipped (nothing to write)"
            client.update_ranges(
                tab,
                {
                    **{
                        f"{first_letter}{end + 2}:{last_letter}{end + 2}": [labels()]
                        for _start, end in name_blocks
                    },
                    **stale_label_cells(header, name_blocks),
                },
            )
            apply_label_notes(client, tab, header_row=header_row, name_blocks=name_blocks, target=gpp_target)
            return f"{tab}: no lineup has players yet -- labels updated, nothing to simulate"
    # Relative lines need every player the field holds, not only the lineups' teams.
    whole_slate = lines_config is not None and not fixed_lines and has_ownership(ownership_by_name(edge))
    specs = build_specs(edge, depth_rows, gsis_by_id, only_names=None if whole_slate else typed)
    lines = (
        resolve_lines(edge, specs, cash_line, gpp_target, lines_config, fixed_lines=fixed_lines)
        if lines_config is not None
        else None
    )

    lineups, owners = [], []
    for block, (start, end) in enumerate(name_blocks):
        built = lineup_from_names([name_at(r) for r in range(start, end + 1)], specs)
        if built is not None:
            lineups.append(built)
            owners.append(block)

    with perf.phase("lineup sim: simulate"):
        result = (
            simulate_lineups(
                lineups,
                n_sims=n_sims,
                seed=seed,
                cash_line=lines.cash_line if lines else cash_line.value,
                gpp_target=lines.gpp_target if lines else gpp_target,
                field=lines.shown_field if lines else None,  # the calibrated display field when switched on
            )
            if lineups
            else None
        )

    updates: dict[str, list[list]] = {}
    blank = ["", "", "", ""]
    by_block = dict(zip(owners, result.lineups if result else [], strict=False))
    for block, (_start, end) in enumerate(name_blocks):
        total_row, remaining_row = end + 1, end + 2
        values = _values_for(by_block[block]) if block in by_block else blank
        updates[f"{first_letter}{total_row}:{last_letter}{total_row}"] = [values]
        updates[f"{first_letter}{remaining_row}:{last_letter}{remaining_row}"] = [labels()]

    updates.update(stale_label_cells(header, name_blocks))
    portfolio = portfolio_summary(result) if result else None
    with perf.phase("lineup sim: write sheet"):
        client.update_ranges(tab, updates)
        # the P(cash) label's note carries the cash line's basis (season median, how many weeks)
        apply_label_notes(
            client,
            tab,
            header_row=header_row,
            name_blocks=name_blocks,
            target=gpp_target,
            cash_basis=lines.cash_basis if lines else cash_line.basis,
            lines=lines,
        )
    return LineupSimReport(
        simulated=len(lineups),
        skipped=len(name_blocks) - len(lineups),
        cash_line=cash_line,
        gpp_target=gpp_target,
        portfolio=portfolio,
        lines=lines,
    )


POINT_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}
PERCENT_FORMAT = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}
LABEL_FORMAT = {
    "textFormat": {"bold": True, "fontSize": 8},
    "horizontalAlignment": "CENTER",
}
MARKER_FORMAT = {"horizontalAlignment": "CENTER", "textFormat": {"bold": True}}
PLAIN_TEXT_FORMAT = {
    "textFormat": {"bold": False, "fontSize": 10, "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}}
}


def format_sim_columns(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]]
) -> str:
    """The look of the scoreboard: number formats on each Total row, small muted labels on each Remaining row,
    green when P(cash) >= 50% or P(GPP) >= 5%, the pale-yellow `Cash` / `GPP` dropdown in column A of every
    Total row, and a highlight on the number that matches the lineup's type. Found by header name;
    formatting only,
    no value changes. Run by `polish`."""
    from dfs.sheet_color_scales import GRAD_MAX
    from dfs.sheet_style import INK_MUTED, INPUT_BG

    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(rows[0]) if rows else []
    slots = sim_slots(header)
    if slots is None or MARKER_HEADER not in header:
        return f"{tab}: simulator columns not found (run `dfs setup reorder-columns` first) -- skipped"
    marker = column_letter(header.index(MARKER_HEADER))
    median, p90, p_cash, p_gpp = (column_letter(i) for i in slots)
    # The matching number is bold in a deep blue (a conditional format cannot underline or outline); its fill
    # stays whatever the green rule says.
    highlight = {"textFormat": {"bold": True, "foregroundColor": {"red": 0.05, "green": 0.2, "blue": 0.6}}}
    green = {"backgroundColor": GRAD_MAX}
    names = [str(h).strip() for h in header]
    retired = [column_letter(names.index(h)) for h in RETIRED_SLOT_HEADERS if h in names]
    with client.batched():
        for _start, end in name_blocks:
            total, remaining = end + 1, end + 2
            # The old scoreboard cells' tiny muted label look goes with the labels (`stale_label_cells`).
            for letter in retired:
                client.format_range(tab, f"{letter}{remaining}", PLAIN_TEXT_FORMAT)
            client.format_range(tab, f"{median}{total}:{p90}{total}", POINT_FORMAT)
            client.format_range(tab, f"{p_cash}{total}:{p_gpp}{total}", PERCENT_FORMAT)
            client.format_range(
                tab,
                f"{median}{remaining}:{p_gpp}{remaining}",
                {**LABEL_FORMAT, "textFormat": {**LABEL_FORMAT["textFormat"], "foregroundColor": INK_MUTED}},
            )
            client.format_range(tab, f"{marker}{total}", {**MARKER_FORMAT, "backgroundColor": INPUT_BG})
            client.set_dropdown_validation(tab, f"{marker}{total}", MARKER_OPTIONS)
            client.add_boolean_rule(
                tab,
                f"{p_cash}{total}",
                condition_type="NUMBER_GREATER_THAN_EQ",
                values=[f"{P_CASH_GREEN:g}"],
                fmt=green,
            )
            client.add_boolean_rule(
                tab,
                f"{p_gpp}{total}",
                condition_type="NUMBER_GREATER_THAN_EQ",
                values=[f"{P_GPP_GREEN:g}"],
                fmt=green,
            )
            # Added last = highest priority: the number that matches the lineup's type is highlighted.
            for cell, kind in ((p_cash, "Cash"), (p_gpp, "GPP")):
                client.add_boolean_rule(
                    tab,
                    f"{cell}{total}",
                    condition_type="CUSTOM_FORMULA",
                    values=[f'=${marker}${total}="{kind}"'],
                    fmt=highlight,
                )
    return f"{tab}: lineup simulator scoreboard formats applied ({len(name_blocks)} lineups)"


def resolve_goal(explicit: str | None, marker: str) -> str:
    """The late-swap goal for one lineup: an explicit `--goal` wins; otherwise its marker (`Cash` -> cash,
    `GPP` -> gpp); a blank marker (both) defaults to cash."""
    if explicit:
        return explicit
    return {"Cash": "cash", "GPP": "gpp"}.get(marker, "cash")


def lineup_types(client: SheetsClient, tab: str, name_blocks: list[tuple[int, int]]) -> list[str]:
    """Each lineup's marker (`Cash`, `GPP`, or "" for both), read from column A of its Total row."""
    ranges = [(tab, f"A{end + 1}") for _start, end in name_blocks]
    values = client.batch_read_ranges(ranges)
    out = []
    for cell in values:
        text = cell[0][0].strip() if cell and cell[0] else ""
        out.append(text if text in MARKER_OPTIONS else "")
    return out


def write_labels(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]]
) -> str:
    """The four labels on every lineup's Remaining row. `dfs sync` writes them along with the numbers; this
    puts them on a sheet that has not synced (the template) or still shows an older wording (`190+ %`).
    Found by header name, values only, safe to repeat. Run by `polish`."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    slots = sim_slots(list(rows[0]) if rows else [])
    if slots is None:
        return f"{tab}: simulator columns not found (run `dfs setup reorder-columns` first) -- skipped"
    first, last = column_letter(slots[0]), column_letter(slots[-1])
    header = list(rows[0])
    client.update_ranges(
        tab,
        {
            **{f"{first}{end + 2}:{last}{end + 2}": [labels()] for _start, end in name_blocks},
            **stale_label_cells(header, name_blocks),
        },
    )
    return f"{tab}: simulator labels written on {len(name_blocks)} Remaining rows"


def apply_label_notes(
    client: SheetsClient,
    tab: str,
    *,
    header_row: int,
    name_blocks: list[tuple[int, int]],
    target: float,
    cash_basis: str | None = None,
    lines: SimLines | None = None,
) -> int:
    """Hover notes on the four labels of the first lineup's Remaining row (the one place that explains them
    all). Returns how many were set."""
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(rows[0]) if rows else []
    slots = sim_slots(header)
    if slots is None:
        return 0
    row = name_blocks[0][1] + 2
    notes = lineup_sim_notes(target, cash_basis, lines)
    texts = [""] * len(header)
    for index, label in zip(slots, labels(), strict=True):
        texts[index] = notes[label]
    # The whole row is replaced, so a note left on the cell where a column used to be is cleared too.
    client.set_row_notes(tab, row, texts)
    return len(slots)
