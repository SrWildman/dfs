"""`Model Check`: a season-level tab that scores every projection against what actually happened.

Rebuilt from `data/results/` on EVERY run (`dfs results update`), so it needs no carry-forward logic and
no hand-edited cell survives -- by design nothing on it is typed. Contents, top to bottom:

1. a one-line status: weeks scored, players joined, the snapshot rule;
2. **Ceiling** -- the headline: how often did a player beat his published `Ceiling`;
3. **Projection accuracy** -- bias, MAE, calibration slope, R squared, Spearman, and calibration buckets;
4. **ValAdj quintiles** -- do the players it points at beat their salary;
5. **Sources compared** (labelled with the weeks it covers) and the head-to-head;
6. **Salary-multiple hit rates** -- tests the `Val >= 3.0` cash filter;
7. **Flags**, with "thin" shown plainly.

Every table shows n; a row with n < 30 is "thin" and drawn in muted italic. The layout is built by
`build_layout` (pure, no sheet) and written by `write_model_check`, which first resets the whole tab's
format so a thin row from a previous build cannot leak onto a different row today.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

import pandas as pd

from dfs import results_analysis as ra
from dfs.sheet_column_notes import MODEL_CHECK_NOTES, apply_notes_to_values
from dfs.sheets import SheetsClient

MODEL_CHECK_TAB = "Model Check"
LAST_COLUMN = "J"
COLUMN_COUNT = 10  # A..J
# Notes overflow to the right, so they are wrapped into rows short enough to fit A..J (~1,200 px at 9 pt).
NOTE_WRAP_CHARS = 150
FIRST_COLUMN_PX = 190
OTHER_COLUMN_PX = 118
_COUNT_HEADERS = {"n", "Unflagged n", "Weeks"}


@dataclass
class Layout:
    """What `write_model_check` needs, with row numbers (1-based) already resolved."""

    rows: list[list]
    title_row: int = 1
    status_row: int = 2
    section_rows: list[int] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    thin_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)
    signed_ranges: list[tuple[int, int, str]] = field(default_factory=list)  # (first, last, column letter)
    percent_cells: list[str] = field(default_factory=list)  # A1 ranges
    number_cells: list[str] = field(default_factory=list)
    count_cells: list[str] = field(default_factory=list)  # whole numbers (n, weeks)
    fine_cells: list[str] = field(default_factory=list)  # three decimals (rank correlations)


def _pad(values: list) -> list:
    return values + [""] * (COLUMN_COUNT - len(values))


_FORMULA_STARTS = ("=", "+", "-", "@")


def _clean(value):
    """NaN -> blank (never a fabricated 0), numpy scalars -> Python, and text that Sheets would read as a
    formula neutralised. The tab is written USER_ENTERED, so a note line that merely WRAPS to begin with
    "= 0.85 best" would become `#ERROR!`; a leading apostrophe makes it plain text (and is not displayed)."""
    if isinstance(value, float) and value != value:
        return ""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


class _Builder:
    def __init__(self) -> None:
        self.layout = Layout(rows=[])

    @property
    def next_row(self) -> int:
        """The 1-based number of the row the next `add` will write."""
        return len(self.layout.rows) + 1

    def add(self, values: list) -> int:
        """Append one row (padded to the tab width, formula-like text neutralised); returns its row number."""
        self.layout.rows.append(_pad([_clean(v) for v in values]))
        return len(self.layout.rows)

    def blank(self) -> None:
        """Append an empty row."""
        self.add([])

    def section(self, title: str) -> None:
        """A blank row, then a dark section-title row."""
        self.blank()
        self.layout.section_rows.append(self.add([title]))

    def note(self, text: str) -> None:
        """A muted explanatory line, wrapped into as many rows as it needs."""
        for line in textwrap.wrap(text, NOTE_WRAP_CHARS) or [""]:
            self.layout.note_rows.append(self.add([line]))

    def table(
        self,
        frame: pd.DataFrame,
        columns: list[tuple[str, str]],
        *,
        percent: set[str] = frozenset(),
        signed: set[str] = frozenset(),
        three_decimals: set[str] = frozenset(),
    ) -> None:
        """`columns` is `[(header, frame column)]`. Adds a header row, then a row per record, marking
        thin rows (the frame's `Thin` column)."""
        self.layout.header_rows.append(self.add([header for header, _ in columns]))
        first = self.next_row
        for _, record in frame.iterrows():
            row = self.add([record[column] for _, column in columns])
            if bool(record.get("Thin", False)):
                self.layout.thin_rows.append(row)
        last = self.next_row - 1
        if last < first:
            return
        for index, (header, _column) in enumerate(columns):
            letter = chr(ord("A") + index)
            if header in percent:
                self.layout.percent_cells.append(f"{letter}{first}:{letter}{last}")
            elif header in three_decimals:
                self.layout.fine_cells.append(f"{letter}{first}:{letter}{last}")
            elif header in _COUNT_HEADERS:
                self.layout.count_cells.append(f"{letter}{first}:{letter}{last}")
            elif index > 0 and header not in {"Source", "Flag", "Quintile"}:
                self.layout.number_cells.append(f"{letter}{first}:{letter}{last}")
            if header in signed:
                self.layout.signed_ranges.append((first, last, letter))


def _weeks_text(weeks: list[int]) -> str:
    return ", ".join(str(w) for w in weeks) if weeks else "none"


def build_layout(scored: pd.DataFrame, *, weeks: list[int]) -> Layout:
    """The whole tab as rows plus formatting instructions. Pure."""
    b = _Builder()
    pool = scored[scored["RosterablePool"].astype(bool)] if not scored.empty else scored
    counts = ra.dnp_counts(pool) if not pool.empty else {"scored": 0, "dnp": 0, "unmatched": 0}
    total = sum(counts.values())
    joined = (counts["scored"] + counts["dnp"]) / total if total else 0.0
    b.add(["MODEL CHECK  —  projections scored against what actually happened"])
    status = (
        f"Weeks scored: {_weeks_text(weeks)}  |  rosterable pool: {counts['scored']} scored, "
        f"{counts['dnp']} did not play, {counts['unmatched']} not found ({joined:.1%} joined)"
    )
    b.add([status])
    b.note(
        "Each projection is the last TFFB snapshot before that player's own kickoff; ValAdj and flags are "
        "recomputed with today's code."
    )
    if scored.empty or counts["scored"] == 0:
        b.blank()
        b.note(
            "Not enough data yet -- no completed week has been scored. "
            "Run `dfs results update` after a week's games."
        )
        return b.layout

    # ---- Ceiling (the headline) ---------------------------------------------------------------
    ceiling = ra.ceiling_report(scored)
    b.section("CEILING  —  how often did a player beat his published Ceiling? (rosterable pool)")
    if ceiling.empty:
        b.note(ra.NOT_ENOUGH)
    else:
        b.table(
            ceiling,
            [
                ("Position", "Position"),
                ("n", "n"),
                ("Beat Ceiling", "HitRate"),
                ("90% low", "Low90"),
                ("90% high", "High90"),
                ("Implied quantile", "ImpliedQuantile"),
                ("Skill τ=0.80", "Skill80"),
                ("Skill τ=0.85", "Skill85"),
                ("Skill τ=0.90", "Skill90"),
            ],
            percent={"Beat Ceiling", "90% low", "90% high", "Implied quantile"},
        )
        overall = ceiling[ceiling["Position"] == "All"].iloc[0]
        b.note(
            f"Provisional (n={int(overall['n'])}): {overall['HitRate']:.1%} of players beat their Ceiling, "
            f"so it reads like roughly the {overall['ImpliedQuantile']:.0%} quantile; scored as a quantile "
            f"with pinball loss it fits τ = {overall['BestTau']:.2f} best (skill is highest there; skill is "
            "relative to a constant-quantile baseline, since raw pinball loss shrinks as τ rises). "
            "FantasyLabs' own ceiling (top 15%) is the hypothesis, not an assumption."
        )

    # ---- Projection accuracy ------------------------------------------------------------------
    accuracy = ra.accuracy_by_position(scored)
    b.section("PROJECTION ACCURACY  —  actual minus projected (rosterable pool)")
    b.table(
        accuracy,
        [
            ("Position", "Position"),
            ("n", "n"),
            ("Bias", "Bias"),
            ("MAE", "MAE"),
            ("Slope", "Slope"),
            ("R²", "R2"),
            ("Spearman", "Spearman"),
        ],
        signed={"Bias"},
    )
    b.note(
        "Bias below 0 = the projections run high. Slope 1.0 = well calibrated; below 1 = too extreme. "
        "R² is expected to be low (3-23% in public studies) -- that is the sport, not a bug."
    )
    b.blank()
    b.table(
        ra.calibration_buckets(scored),
        [
            ("Projected", "Projected"),
            ("n", "n"),
            ("Mean projected", "MeanProjected"),
            ("Mean actual", "MeanActual"),
        ],
    )
    everyone = ra.accuracy_by_position(scored, "all")
    if not everyone.empty:
        allrow = everyone[everyone["Position"] == "All"].iloc[0]
        b.note(
            f"Everyone with ProjPts > 0 (n={int(allrow['n'])}): bias {allrow['Bias']:+.2f}, "
            f"MAE {allrow['MAE']:.2f}, Spearman {allrow['Spearman']:.2f}."
        )

    # ---- ValAdj quintiles ----------------------------------------------------------------------
    quintiles, rho = ra.valadj_quintiles(scored)
    b.section("VALADJ  —  do the players it points at beat their salary? (actual minus salary-expected)")
    if quintiles.empty:
        b.note(ra.NOT_ENOUGH)
    else:
        b.table(
            quintiles,
            [("Quintile", "Quintile"), ("n", "n"), ("Mean residual", "MeanResidual")],
            signed={"Mean residual"},
        )
        b.note(
            f"Rank correlation of ValAdj with the residual: {rho['All']:+.2f} overall "
            f"(n={rho['n']}); by position "
            + ", ".join(f"{p} {rho[p]:+.2f}" for p in ("QB", "RB", "WR", "TE", "DST") if p in rho)
            + ". Salary-expected is fit on actual points vs salary, per position, over the weeks scored."
        )

    # ---- Sources compared ----------------------------------------------------------------------
    sources, head = ra.source_comparison(scored)
    b.section(
        "SOURCES COMPARED  —  rows where TFFB, Sleeper and FantasyPros all exist "
        f"(week {_weeks_text(head.get('weeks', []))} only)"
    )
    if sources.empty:
        b.note(ra.NOT_ENOUGH)
    else:
        b.table(
            sources,
            [("Source", "Source"), ("n", "n"), ("MAE", "MAE"), ("Bias", "Bias"), ("Spearman", "Spearman")],
            signed={"Bias"},
            three_decimals={"Spearman"},
        )
        b.note(
            "Head-to-head, AggPts vs TFFB (which landed closer): "
            f"AggPts wins {head['agg_wins']} of {head['n']} = "
            f"{head['win_rate']:.1%} (90% interval {head['low']:.1%}-{head['high']:.1%}). "
            + ("Thin. " if head["thin"] else "")
            + "Public study: averaged projections won about 63%."
        )
    consistency = ra.consistency(scored)
    b.note(
        "Consistency over time: " + (consistency if isinstance(consistency, str) else "see table below") + "."
    )
    if isinstance(consistency, pd.DataFrame) and not consistency.empty:
        b.table(
            consistency, [("Source", "Source"), ("Weeks", "Weeks"), ("Mean MAE", "MeanMAE"), ("CV", "CV")]
        )

    # ---- Salary multiple -----------------------------------------------------------------------
    b.section("SALARY MULTIPLE  —  share reaching 3x (cash line) and 4x (GPP line) salary, by PROJECTED Val")
    multiples = ra.salary_multiple_hits(scored)
    if multiples.empty:
        b.note(ra.NOT_ENOUGH)
    else:
        b.table(
            multiples,
            [
                ("Projected Val", "ProjectedVal"),
                ("n", "n"),
                ("Hit 3x", "Hit3x"),
                ("90% low", "Hit3xLow"),
                ("90% high", "Hit3xHigh"),
                ("Hit 4x", "Hit4x"),
            ],
            percent={"Hit 3x", "90% low", "90% high", "Hit 4x"},
        )

    # ---- Flags ---------------------------------------------------------------------------------
    b.section("FLAGS  —  recomputed with today's code; reported, not judged")
    b.table(
        ra.flag_report(scored),
        [
            ("Flag", "Flag"),
            ("n", "n"),
            ("Actual - proj.", "MeanError"),
            ("Unflagged n", "UnflaggedN"),
            ("Unflagged mean", "UnflaggedMean"),
        ],
        signed={"Actual - proj.", "Unflagged mean"},
    )
    b.note(
        "Actual - proj. is the mean of actual minus projected points (negative = ran high). "
        "Muted italic rows are thin (n < 30): too few to say anything yet. "
        "Almost everything here is thin this early."
    )
    return b.layout


def write_model_check(client: SheetsClient, layout: Layout, tab: str = MODEL_CHECK_TAB) -> str:
    """Write the rows, then style them. Resets the tab's format first (the tab is rebuilt from
    scratch every run), then applies title/section/header looks, number formats, signed colour scales
    and the muted-italic thin rows."""
    # Imported here: sheet_style is large and imports sheet_views; this module stays importable
    # without it for the pure layout tests.
    from dfs.sheet_style import (
        _HEADER_FMT,
        _PANEL_FMT,
        _TITLE_FMT,
        GRAD_MAX,
        GRAD_MIN,
        INK_MUTED,
        WHITE,
        _num,
        diverging_anchor_kwargs,
    )

    last_row = max(len(layout.rows), 60)
    client.write_tab(tab, layout.rows)
    client.clear_conditional_formats(tab)
    client.format_range(
        tab,
        f"A1:{LAST_COLUMN}{last_row + 20}",
        {
            "backgroundColor": WHITE,
            "textFormat": {"bold": False, "italic": False, "fontSize": 10},
            "horizontalAlignment": None,
            "numberFormat": None,
            "wrapStrategy": "OVERFLOW_CELL",
        },
    )
    widths = {"A": FIRST_COLUMN_PX, **{chr(ord("B") + i): OTHER_COLUMN_PX for i in range(COLUMN_COUNT - 1)}}
    client.set_column_widths(tab, widths)
    client.format_range(tab, f"A{layout.title_row}", _TITLE_FMT)
    client.format_range(
        tab, f"A{layout.status_row}", {"textFormat": {"fontSize": 9, "foregroundColor": INK_MUTED}}
    )
    for row in layout.section_rows:
        client.format_range(tab, f"A{row}:{LAST_COLUMN}{row}", _PANEL_FMT)
    for row in layout.header_rows:
        client.format_range(tab, f"A{row}:{LAST_COLUMN}{row}", _HEADER_FMT)
        client.format_range(tab, f"B{row}:{LAST_COLUMN}{row}", {"horizontalAlignment": "RIGHT"})
    for row in layout.note_rows:
        client.format_range(
            tab, f"A{row}", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for rng in layout.percent_cells:
        client.format_range(tab, rng, {**_num("0.0%", "PERCENT"), "horizontalAlignment": "RIGHT"})
    for rng in layout.count_cells:
        client.format_range(tab, rng, {**_num("0"), "horizontalAlignment": "RIGHT"})
    for rng in layout.fine_cells:
        client.format_range(tab, rng, {**_num("0.000"), "horizontalAlignment": "RIGHT"})
    for rng in layout.number_cells:
        client.format_range(tab, rng, {**_num("0.00"), "horizontalAlignment": "RIGHT"})
    # Signed columns (bias, residuals, errors): white at zero, symmetric about it, like Movement.
    for first, last, letter in layout.signed_ranges:
        rng = f"{letter}{first}:{letter}{last}"
        client.add_color_scale(
            tab,
            rng,
            min_color=GRAD_MIN,
            mid_color=WHITE,
            max_color=GRAD_MAX,
            mid_type="NUMBER",
            mid_value="0",
            **diverging_anchor_kwargs(rng),
        )
    # Thin rows last, so their muted italic text wins over any scale's text colour.
    for row in layout.thin_rows:
        client.format_range(
            tab, f"A{row}:{LAST_COLUMN}{row}", {"textFormat": {"italic": True, "foregroundColor": INK_MUTED}}
        )
    # Rows only: a frozen column would cut through the section titles that run across A..J.
    client.freeze(tab, rows=layout.status_row, cols=0)
    # One-hover definitions on every metric header (Bias, Slope, Skill tau, ...).
    notes = sum(
        apply_notes_to_values(client, tab, row, layout.rows[row - 1], MODEL_CHECK_NOTES)
        for row in layout.header_rows
    )
    return f"{tab}: built ({len(layout.rows)} rows, {len(layout.thin_rows)} thin, {notes} header note(s))"
