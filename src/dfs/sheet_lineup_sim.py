"""The lineup simulator on the Lineups tab: Median, p90, P(cash) and P(GPP) for every built lineup, and one
portfolio line.

Per lineup (the existing Total row under each block, like `Games` / `Min Unique`): the median and
90th-percentile score, the chance the lineup reaches the cash line, the chance it reaches the GPP target.
Portfolio (the first block's `Remaining` row, in the same columns, labelled): the chance at least one lineup
cashes (under `P(cash)`), the expected number that cash (under `p90`) and the chance at least one reaches
the GPP target (under the GPP column).

- **Cash line**: the median of your last three typed `Cash Line` values in Results (`sim_inputs`), read only.
- **GPP target**: `[sim] gpp_target` in config.toml (default 190), shown in the column header, `P(190+)`.
  When the target changes the header text changes with it: every Lineups header repeat is rewritten, and
  anything that looks the column up goes through `find_gpp_column` / `restore_canonical_gpp_header`, never a
  fixed string.
- **Draws**: `sim_inputs.N_SIMS` (20,000) with a fixed seed, so the numbers do not jitter between syncs. All
  the built lineups are simulated in one run: a player in two lineups is one random variable, which is what
  makes the portfolio numbers mean something.
- **Not conditioned on games already played**: a lineup with a locked player is simulated from his pre-game
  distribution, like everything else on the sheet before kickoff. A lineup with a blank slot or a name EdgeRaw
  does not know is left blank.

Columns are found by header name, never by letter (`dfs setup reorder-columns` creates them: they are in
`sheet_columns.LINEUPS_COLUMN_ORDER` right after `Min Unique`).
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
PORTFOLIO_LABEL = "Portfolio"
GPP_HEADER_RE = re.compile(r"^P\((\d+(?:\.\d+)?)\+\)$")


def gpp_header(target: float) -> str:
    """`P(190+)` for a 190 target, `P(187.5+)` for 187.5."""
    return f"P({target:g}+)"


GPP_HEADER_DEFAULT = gpp_header(SIM_GPP_TARGET_DEFAULT)
LINEUP_SIM_HEADERS = [MEDIAN_HEADER, P90_HEADER, P_CASH_HEADER, GPP_HEADER_DEFAULT]


def find_gpp_column(header: list) -> int | None:
    """Index of the `P(<target>+)` header, whatever target it currently carries."""
    for index, name in enumerate(header):
        if GPP_HEADER_RE.match(str(name).strip()):
            return index
    return None


def lineup_sim_notes(target: float, cash_line_weeks: int = 3) -> dict[str, str]:
    """The header notes, keyed by header text (so the GPP note carries the current target)."""
    return {
        MEDIAN_HEADER: (
            "SIMULATED median score of this lineup (half the simulated slates land above it). Outcomes "
            "come from the model's distributions and move together by role (QB with his receivers, the two "
            "sides of a game). Shown on the lineup's Total row."
        ),
        P90_HEADER: (
            "SIMULATED 90th-percentile score: a good night for this lineup (1 slate in 10 is better). On the "
            "Portfolio row (first lineup's Remaining row) this column holds the EXPECTED NUMBER of lineups "
            "that cash."
        ),
        P_CASH_HEADER: (
            f"Chance this lineup reaches the cash line: the median of your last {cash_line_weeks} typed Cash "
            "Line values in Results (read only). On the Portfolio row: the chance at least one lineup cashes."
        ),
        gpp_header(target): (
            f"Chance this lineup scores {target:g} or more, the GPP target (config.toml [sim] gpp_target). "
            f"On the Portfolio row: the chance at least one lineup reaches {target:g}. Simulated from the "
            "model, not conditioned on games already played; 20,000 draws with a fixed seed."
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
    renamed_header: bool

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


def restore_canonical_gpp_header(
    client: SheetsClient, tab: str, *, header_row: int = 1, header_repeats_at: list[int] | None = None
) -> bool:
    """Rename a `P(<other target>+)` header (and every header repeat) back to the canonical
    `GPP_HEADER_DEFAULT` so the column-order code, which compares header names exactly, sees the name it
    expects. The next simulator
    write puts the configured target back. True when something was renamed."""
    return _rename_gpp_header(client, tab, GPP_HEADER_DEFAULT, header_row, header_repeats_at)


def _rename_gpp_header(
    client: SheetsClient,
    tab: str,
    new_name: str,
    header_row: int,
    header_repeats_at: list[int] | None,
    header: list | None = None,
) -> bool:
    """Rename the GPP column's header (and every repeat) to `new_name`; `header` is the header row when the
    caller has already read it (saving a round trip). True when something was renamed."""
    if header is None:
        rows = client.read_range(tab, f"A{header_row}:{header_row}")
        header = list(rows[0]) if rows else []
    index = find_gpp_column(header)
    if index is None or str(header[index]).strip() == new_name:
        return False
    letter = column_letter(index)
    for row in [header_row, *(header_repeats_at or [])]:
        client.update_range(tab, f"{letter}{row}", [[new_name]])
    return True


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
    header_repeats_at: list[int],
    edge,  # noqa: ANN001 - the synced EdgeRaw frame
    depth_rows,  # noqa: ANN001 - nflverse depth rows, or None
    gsis_by_id: dict | None,
    results_tab: str,
    week: int,
    gpp_target: float,
    n_sims: int = N_SIMS,
    seed: int = SEED,
) -> LineupSimReport | str:
    """Simulate every complete lineup on `tab` and write the four columns. Returns the report, or a message
    when
    the tab or its columns are missing (run `dfs setup reorder-columns` first)."""
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
    missing = [n for n in (MEDIAN_HEADER, P90_HEADER, P_CASH_HEADER) if n not in header]
    gpp_index = find_gpp_column(header)
    if missing or gpp_index is None:
        wanted = [*missing, *([] if gpp_index is not None else [GPP_HEADER_DEFAULT])]
        return f"{tab}: column(s) {wanted} not found (run `dfs setup reorder-columns` first) -- skipped"
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

    cols = {
        MEDIAN_HEADER: column_letter(header.index(MEDIAN_HEADER)),
        P90_HEADER: column_letter(header.index(P90_HEADER)),
        P_CASH_HEADER: column_letter(header.index(P_CASH_HEADER)),
        "gpp": column_letter(gpp_index),
    }
    first_letter, last_letter = cols[MEDIAN_HEADER], cols["gpp"]
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
        row = end + 1  # the lineup's Total row
        values = _values_for(by_block[block]) if block in by_block else blank
        updates[f"{first_letter}{row}:{last_letter}{row}"] = [values]

    portfolio = portfolio_summary(result) if result else None
    remaining_row = name_blocks[0][1] + 2  # the first block's Remaining row
    updates[f"{first_letter}{remaining_row}:{last_letter}{remaining_row}"] = [
        [
            PORTFOLIO_LABEL,
            round(portfolio["expected_cashes"], 2) if portfolio else "",
            round(portfolio["p_any_cash"], 4) if portfolio else "",
            round(portfolio["p_any_gpp"], 4) if portfolio else "",
        ]
        if result
        else ["", "", "", ""]
    ]

    with perf.phase("lineup sim: write sheet"):
        renamed = _rename_gpp_header(
            client, tab, gpp_header(gpp_target), header_row, header_repeats_at, header
        )
        client.update_ranges(tab, updates)
        if renamed:  # a new header text: its note, and the formats polish gave the old one, are re-applied
            client.set_note(
                tab, f"{cols['gpp']}{header_row}", lineup_sim_notes(gpp_target)[gpp_header(gpp_target)]
            )
            format_sim_columns(client, tab, header_row=header_row, name_blocks=name_blocks)
            client.set_column_widths(tab, {cols["gpp"]: GPP_COLUMN_PX})
    return LineupSimReport(
        simulated=len(lineups),
        skipped=len(name_blocks) - len(lineups),
        cash_line=cash_line,
        gpp_target=gpp_target,
        portfolio=portfolio,
        renamed_header=renamed,
    )


GPP_COLUMN_PX = 66
POINT_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}
PERCENT_FORMAT = {"numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}
CASHES_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": '0.0" cashes"'}}
LABEL_FORMAT = {"textFormat": {"bold": True}, "horizontalAlignment": "RIGHT"}


def format_sim_columns(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]]
) -> str:
    """Number formats for the four columns across the lineup blocks (points, percentages), and the portfolio
    cells' own (a bold label under `Median`, a count of lineups under `p90`). Found by header name, the GPP
    column by its `P(<target>+)` pattern. Formatting only: no value or rule is touched. Run by `polish` and
    after a
    rename."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = list(rows[0]) if rows else []
    gpp = find_gpp_column(header)
    missing = [n for n in (MEDIAN_HEADER, P90_HEADER, P_CASH_HEADER) if n not in header]
    if missing or gpp is None:
        return f"{tab}: lineup simulator columns not found (run `dfs setup reorder-columns` first) -- skipped"
    median, p90, p_cash = (column_letter(header.index(n)) for n in (MEDIAN_HEADER, P90_HEADER, P_CASH_HEADER))
    gpp_letter = column_letter(gpp)
    first, last = name_blocks[0][0], name_blocks[-1][1] + 2
    remaining_row = name_blocks[0][1] + 2
    with client.batched():
        client.format_range(tab, f"{median}{first}:{p90}{last}", POINT_FORMAT)
        client.format_range(tab, f"{p_cash}{first}:{gpp_letter}{last}", PERCENT_FORMAT)
        client.format_range(tab, f"{median}{remaining_row}", LABEL_FORMAT)
        client.format_range(tab, f"{p90}{remaining_row}", CASHES_FORMAT)
    return f"{tab}: lineup simulator column formats applied"
