"""The lineup simulator on the Lineups tab: Median, p90, P(cash) and P(GPP) for every built lineup, up front.

**Where they sit** (usability round, slice 5). On each lineup's **Total row**, in the four columns right
after `Pts` that are blank there (`AggPts`, `CalPts`, `Val`, `ValAdj`, found by header name), with small
labels on the
**Remaining row** under them (`Median`, `p90`, `P(cash)`, `P(190+)`), scoreboard style. They are numbers, so
they colour: P(cash) goes green at 50% or more and P(GPP) at 5% or more. They used to sit about twenty
columns to the right (`Median` .. `P(190+)` after `Min Unique`); those columns are retired
(`sheet_reorder.retire_lineup_sim_columns`) and the portfolio line moved to the Board's Pool summary.

**The type marker.** Column A of every Total row is a pale-yellow input dropdown, `Cash` / `GPP` (blank =
both). The number that matches the lineup's type is highlighted, and `dfs lineups late-swap` reads it as
that lineup's default `--goal` (the flag still overrides it, and both deltas are still shown).

- **Cash line**: the median of your last three typed `Cash Line` values in Results (`sim_inputs`), read only.
- **GPP target**: `[sim] gpp_target` in config.toml (default 190), in the label under the number (`P(190+)`).
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
from dfs.config import SIM_GPP_TARGET_DEFAULT
from dfs.sheets import SheetsClient, column_letter

if TYPE_CHECKING:  # the simulator stack (numpy, scipy) is imported only when a simulation actually runs
    from dfs.sim_inputs import CashLine

N_SIMS = 20000  # mirrored by sim_inputs.N_SIMS (a test pins them equal)
SEED = 0

MEDIAN_HEADER = "Median"
P90_HEADER = "p90"
P_CASH_HEADER = "P(cash)"
GPP_HEADER_RE = re.compile(r"^P\((\d+(?:\.\d+)?)\+\)$")
# The columns the four numbers sit in: blank on a Total row, right after `Pts`.
SIM_SLOT_HEADERS = ("AggPts", "CalPts", "Val", "ValAdj")
MARKER_HEADER = "Name"  # the marker is column A of the Total row: under the Name column
MARKER_OPTIONS = ["Cash", "GPP"]
P_CASH_GREEN = 0.5  # P(cash) at or above this reads green
P_GPP_GREEN = 0.05  # P(GPP target) at or above this reads green


def gpp_header(target: float) -> str:
    """`P(190+)` for a 190 target, `P(187.5+)` for 187.5."""
    return f"P({target:g}+)"


GPP_HEADER_DEFAULT = gpp_header(SIM_GPP_TARGET_DEFAULT)
# What the retired far-right columns were called (`sheet_reorder.retire_lineup_sim_columns` deletes them).
RETIRED_SIM_HEADERS = [MEDIAN_HEADER, P90_HEADER, P_CASH_HEADER]


def is_retired_sim_header(name: object) -> bool:
    text = str(name).strip()
    return text in RETIRED_SIM_HEADERS or bool(GPP_HEADER_RE.match(text))


def labels(target: float) -> list[str]:
    """The four labels under the numbers, in slot order."""
    return [MEDIAN_HEADER, P90_HEADER, P_CASH_HEADER, gpp_header(target)]


def sim_slots(header: list) -> list[int] | None:
    """The indices of the four slot columns, or None unless all four exist as one run (by header name)."""
    names = [str(h).strip() for h in header]
    if any(h not in names for h in SIM_SLOT_HEADERS):
        return None
    indices = [names.index(h) for h in SIM_SLOT_HEADERS]
    return indices if indices == list(range(indices[0], indices[0] + len(indices))) else None


def lineup_sim_notes(target: float, cash_line_weeks: int = 3) -> dict[str, str]:
    """The notes on the four labels, keyed by label text (so the GPP note carries the current target)."""
    return {
        MEDIAN_HEADER: (
            "SIMULATED median score of this lineup (half the simulated slates land above it). Outcomes come "
            "from the model's distributions and move together by role (QB with his receivers, the two sides "
            "of a game)."
        ),
        P90_HEADER: (
            "SIMULATED 90th-percentile score: a good night for this lineup (1 slate in 10 is better)."
        ),
        P_CASH_HEADER: (
            f"Chance this lineup reaches the cash line: the median of your last {cash_line_weeks} typed "
            "Cash Line values in Results (read only). Green at 50% or more. Column A of the Total row says "
            "whether this lineup is a Cash or a GPP lineup; the matching number is highlighted."
        ),
        gpp_header(target): (
            f"Chance this lineup scores {target:g} or more, the GPP target (config.toml [sim] gpp_target). "
            "Green at 5% or more. Simulated from the model, not conditioned on games already played; "
            "20,000 draws with a fixed seed."
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
    renamed_header: bool = False

    def line(self) -> str:
        if self.simulated == 0:
            return "Lineup simulator: no complete lineup on Lineups yet -- nothing to simulate."
        p = self.portfolio or {}
        return (
            f"Lineup simulator: {self.simulated} lineup(s) ({self.skipped} unbuilt), cash line "
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
) -> LineupSimReport | str:
    """Simulate every complete lineup on `tab` and write the four numbers on each Total row and their labels
    on the
    Remaining row below. Returns the report, or a message when the tab or its columns are missing."""
    from dfs.sim.simulate import simulate_lineups
    from dfs.sim_inputs import build_specs, cash_line_from_results, lineup_from_names, portfolio_summary

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
    specs = build_specs(edge, depth_rows, gsis_by_id, only_names=typed) if typed else {}

    lineups, owners = [], []
    for block, (start, end) in enumerate(name_blocks):
        built = lineup_from_names([name_at(r) for r in range(start, end + 1)], specs)
        if built is not None:
            lineups.append(built)
            owners.append(block)

    first_letter, last_letter = column_letter(slots[0]), column_letter(slots[-1])
    with perf.phase("lineup sim: simulate"):
        result = (
            simulate_lineups(
                lineups, n_sims=n_sims, seed=seed, cash_line=cash_line.value, gpp_target=gpp_target
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
        updates[f"{first_letter}{remaining_row}:{last_letter}{remaining_row}"] = [labels(gpp_target)]

    portfolio = portfolio_summary(result) if result else None
    with perf.phase("lineup sim: write sheet"):
        client.update_ranges(tab, updates)
    return LineupSimReport(
        simulated=len(lineups),
        skipped=len(name_blocks) - len(lineups),
        cash_line=cash_line,
        gpp_target=gpp_target,
        portfolio=portfolio,
    )


POINT_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}
PERCENT_FORMAT = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}
LABEL_FORMAT = {
    "textFormat": {"bold": True, "fontSize": 8},
    "horizontalAlignment": "CENTER",
}
MARKER_FORMAT = {"horizontalAlignment": "CENTER", "textFormat": {"bold": True}}


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
    with client.batched():
        for _start, end in name_blocks:
            total, remaining = end + 1, end + 2
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


def apply_label_notes(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]], target: float
) -> int:
    """Hover notes on the four labels of the first lineup's Remaining row (the one place that explains them
    all). Returns how many were set."""
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    slots = sim_slots(list(rows[0]) if rows else [])
    if slots is None:
        return 0
    row = name_blocks[0][1] + 2
    notes = lineup_sim_notes(target)
    for index, label in zip(slots, labels(target), strict=True):
        client.set_note(tab, f"{column_letter(index)}{row}", notes[label])
    return len(slots)
