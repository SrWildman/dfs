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

from dfs import calibration, results_signals
from dfs import results_analysis as ra
from dfs.sheet_column_notes import MODEL_CHECK_NOTES, apply_notes_to_values
from dfs.sheets import SheetsClient

MODEL_CHECK_TAB = "Model Check"
# Plain-English column labels (usability round, slice 6; they used to read "Implied quantile" and "Skill
# τ=0.80").
CEILING_REAL = "Ceiling is really the Nth percentile"
SKILL_80, SKILL_85, SKILL_90 = (f"Ceiling skill ({n}th pct)" for n in (80, 85, 90))
RACE_CLEAR_GAP = 0.15  # MAE points between the best projection and the runner-up for "clearly best"
RACE_CLEAR_MIN_N = 50
VERDICT_MIN_N = 30  # a signal needs this many players before its verdict is worded
RELIABILITY_ADJUST_WEEK = 8  # "no adjustment until ~Week 8" (docs: reliability recalibration)
LAST_COLUMN = "J"
COLUMN_COUNT = 10  # A..J
# Notes overflow to the right, so they are wrapped into rows short enough to fit A..J (~1,200 px at 9 pt).
NOTE_WRAP_CHARS = 150
FIRST_COLUMN_PX = 190
OTHER_COLUMN_PX = 118
_COUNT_HEADERS = {"n", "Unflagged n", "Weeks", "Week", "Decile"}
_TEXT_HEADERS = {"Source", "Flag", "Quintile", "Projection", "Signal", "Measure", "Off?"}


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
    verdict_rows: list[int] = field(default_factory=list)  # one computed sentence opening each block
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

    def verdict(self, text: str) -> None:
        """The block's one computed line, in words (bold), wrapped like a note."""
        for line in textwrap.wrap(text, NOTE_WRAP_CHARS) or [""]:
            self.layout.verdict_rows.append(self.add([line]))

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
            elif index > 0 and header not in _TEXT_HEADERS:
                self.layout.number_cells.append(f"{letter}{first}:{letter}{last}")
            if header in signed:
                self.layout.signed_ranges.append((first, last, letter))


def race_verdict(race: pd.DataFrame) -> str:
    """Per position, which projection has the lowest MAE and by how much against the runner-up: "clearly best"
    when the gap is at least `RACE_CLEAR_GAP` with n >= `RACE_CLEAR_MIN_N`, else "too close to call"."""
    parts = []
    for position in calibration.POSITIONS:
        part = race[race["Position"] == position].sort_values("MAE")
        if len(part) < 2:
            continue
        best, second = part.iloc[0], part.iloc[1]
        if second["MAE"] - best["MAE"] >= RACE_CLEAR_GAP and best["n"] >= RACE_CLEAR_MIN_N:
            parts.append(
                f"{position}: {best['Source']} clearly best (MAE {best['MAE']:.2f} vs "
                f"{second['Source']} {second['MAE']:.2f})."
            )
        else:
            parts.append(f"{position}: too close to call.")
    return " ".join(parts) or "Not enough rows to compare the projections yet."


def reliability_verdict(reliability: pd.DataFrame) -> str:
    """How far each probability column runs from what happened, in points (n-weighted over the deciles)."""
    parts = []
    for measure, part in reliability.groupby("Measure", sort=False):
        weight = part["n"].sum()
        if weight <= 0:
            continue
        gap = float((part["Gap"] * part["n"]).sum() / weight)  # realized minus predicted, in points
        if abs(gap) < 0.5:
            parts.append(f"{measure} is about right")
        else:
            points = round(abs(gap))
            parts.append(
                f"{measure} runs ~{points} point{'' if points == 1 else 's'} {'high' if gap < 0 else 'low'}"
            )
    if not parts:
        return "Not enough scored players to judge the probabilities yet."
    return "; ".join(parts) + f"; no adjustment until ~Week {RELIABILITY_ADJUST_WEEK}."


def _signal_position(name: str) -> str | None:
    """The position whose unflagged baseline a signal is judged against (None: it spans positions)."""
    if name.startswith("R6 "):
        return name.split()[1]
    return {
        "FADE↓": "TE",
        "USAGE↑": "RB",
        "USAGE↓": "RB",
        "INJ+ confirmed": "RB",
        "INJ+ questionable": "RB",
    }.get(name)


def signals_verdict(table: pd.DataFrame) -> str:
    """Each signal with enough players, against its position's unflagged baseline, in words."""
    rows = table.set_index("Signal")
    parts = []
    for name in rows.index:
        position = _signal_position(name)
        base_name = f"{results_signals.BASELINE_PREFIX}{position}"
        row = rows.loc[name]
        if position is None or base_name not in rows.index or int(row["n"]) < VERDICT_MIN_N:
            continue
        value, base = row["VsCal"], rows.loc[base_name, "VsCal"]
        if pd.isna(value) or pd.isna(base):
            continue
        gap = float(value - base)
        way = "below" if gap < 0 else "above"
        parts.append(
            f"{name} (n={int(row['n'])}): {value:+.1f} vs CalPts, {abs(gap):.1f} {way} the unflagged "
            f"{position} average ({base:+.1f})."
        )
    return " ".join(parts) or "No signal has 30 players yet; read the n column as 'not enough data'."


def _weeks_text(weeks: list[int]) -> str:
    return ", ".join(str(w) for w in weeks) if weeks else "none"


def build_layout(
    scored: pd.DataFrame,
    *,
    weeks: list[int],
    signal_table: pd.DataFrame | None = None,
    reliability: pd.DataFrame | None = None,
) -> Layout:
    """The whole tab as rows plus formatting instructions. Pure. `signal_table` is
    `results_signals.signal_report` (None when no signals archive exists yet)."""
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

    _add_projection_race(b, scored)
    _add_reliability(b, reliability)

    # ---- Ceiling (the headline) ---------------------------------------------------------------
    ceiling = ra.ceiling_report(scored)
    b.section("CEILING  —  how often did a player beat his published Ceiling? (rosterable pool)")
    if ceiling.empty:
        b.note(ra.NOT_ENOUGH)
    else:
        overall_row = ceiling[ceiling["Position"] == "All"].iloc[0]
        b.verdict(
            f"Ceiling is really the {overall_row['ImpliedQuantile'] * 100:.0f}th percentile: "
            f"{overall_row['HitRate']:.0%} of players beat it (n={int(overall_row['n'])})."
        )
        b.table(
            ceiling,
            [
                ("Position", "Position"),
                ("n", "n"),
                ("Beat Ceiling", "HitRate"),
                ("90% low", "Low90"),
                ("90% high", "High90"),
                (CEILING_REAL, "ImpliedQuantile"),
                (SKILL_80, "Skill80"),
                (SKILL_85, "Skill85"),
                (SKILL_90, "Skill90"),
            ],
            percent={"Beat Ceiling", "90% low", "90% high", CEILING_REAL},
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
    overall_acc = accuracy[accuracy["Position"] == "All"]
    if not overall_acc.empty:
        a = overall_acc.iloc[0]
        b.verdict(
            f"TFFB's projections {'run high' if a['Bias'] < 0 else 'run low'} by {abs(a['Bias']):.1f} "
            f"points on average and miss by {a['MAE']:.1f} (n={int(a['n'])})."
        )
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
        b.verdict(
            f"ValAdj {'does' if rho['All'] > 0.05 else 'does not yet'} point at players who beat salary: "
            f"rank correlation {rho['All']:+.2f} (n={rho['n']})."
        )
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
        b.verdict(
            f"AggPts landed closer than TFFB {head['win_rate']:.0%} of the time ({head['agg_wins']} of "
            f"{head['n']})" + (", still thin." if head["thin"] else ".")
        )
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
        low_row, high_row = multiples.iloc[0], multiples.iloc[-1]
        b.verdict(
            f"Players projected at Val {high_row['ProjectedVal']} reached 3x salary "
            f"{high_row['Hit3x']:.0%} of the time, against {low_row['Hit3x']:.0%} at "
            f"{low_row['ProjectedVal']}."
        )
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
    flags = ra.flag_report(scored)
    settled = flags[flags["n"] >= VERDICT_MIN_N] if not flags.empty else flags
    b.verdict(
        f"{len(settled)} of {len(flags)} flags have {VERDICT_MIN_N}+ players; "
        + (
            ", ".join(f"{r.Flag} {r.MeanError:+.1f}" for r in settled.itertuples())
            + " points against projection."
            if len(settled)
            else "the rest are too thin to read yet."
        )
    )
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
    _add_signals(b, signal_table)
    return b.layout


def _race_block(b: _Builder, scored: pd.DataFrame, *, with_um: bool) -> bool:
    """One projection-race table (by position) and its per-week trend. Returns False when the race cannot
    start yet. `with_um` adds UM as a row, judged with the others on the players UM rates."""
    race = calibration.backtest(scored, with_um=with_um)
    if race.empty:
        return False
    labels = [label for label, _ in (calibration.PROJECTIONS_WITH_UM if with_um else calibration.PROJECTIONS)]
    order = {name: i for i, name in enumerate(labels)}
    positions = ["All", *calibration.POSITIONS]
    race = race.assign(_p=race["Position"].map(positions.index), _s=race["Source"].map(order))
    race = race.sort_values(["_p", "_s"]).drop(columns=["_p", "_s"])
    race = race.assign(Thin=race["n"].map(ra.is_thin)).rename(columns={"Source": "Projection"})
    b.table(
        race,
        [
            ("Position", "Position"),
            ("Projection", "Projection"),
            ("n", "n"),
            ("ρ (rank)", "Rho"),
            ("MAE", "MAE"),
            ("Bias", "Bias"),
        ],
        signed={"Bias"},
        three_decimals={"ρ (rank)"},
    )
    trend = calibration.backtest_by_week(scored, with_um=with_um)
    if not trend.empty:
        wide = trend.pivot(index="Week", columns="Source", values=["MAE", "Rho"])
        counts = trend[trend["Source"] == "TFFB"].set_index("Week")["n"]
        table = pd.DataFrame({"Week": wide.index, "n": counts.reindex(wide.index).to_numpy()})
        columns = [("Week", "Week"), ("n", "n")]
        three = set()
        for name in labels:
            table[f"{name} MAE"] = wide[("MAE", name)].to_numpy()
            columns.append((f"{name} MAE", f"{name} MAE"))
        if not with_um:  # twelve columns would not fit A..J; the with-UM trend shows MAE only
            for name in labels:
                table[f"{name} ρ"] = wide[("Rho", name)].to_numpy()
                columns.append((f"{name} ρ", f"{name} ρ"))
                three.add(f"{name} ρ")
        table["Thin"] = table["n"].map(ra.is_thin)
        b.blank()
        b.table(table, columns, three_decimals=three)
    return True


def _add_projection_race(b: _Builder, scored: pd.DataFrame) -> None:
    """The table Sam uses to choose his default projection: TFFB, AggPts and CalPts on weeks none of them
    trained on (CalPts is fitted on earlier weeks only; the 2nd scored week is the first it can be judged),
    first without UM, then with UM on the rows UM rates."""
    b.section("PROJECTION RACE  —  TFFB vs AggPts vs CalPts, each judged on weeks it never trained on")
    plain = calibration.backtest(scored, with_um=False)
    if not plain.empty:
        b.verdict(race_verdict(plain))
    if not _race_block(b, scored, with_um=False):
        b.note(
            "Not enough data yet -- CalPts needs one earlier scored week to learn from, so the race starts "
            "with the 2nd scored week."
        )
        return
    b.blank()
    b.note("WITH UM, on the players the UM model rates (blank outside its training population):")
    if not _race_block(b, scored, with_um=True):
        b.note("UM is not on the scored weeks yet -- run `dfs model fetch`, then `dfs results update`.")
    weeks_trained = sorted(int(w) for w in scored["week"].unique())
    b.note(
        "CalPts is NOT the default projection: it is tracked here every week and Sam decides. It is each "
        "source's level bias corrected per position and salary tier (shrunk toward 0), then weighted; "
        "UM is not part of CalPts (it added nothing in 12 seasons of back-tests); it is its own row. "
        f"Scored weeks so far: {_weeks_text(weeks_trained)}. rho = rank correlation within position and week "
        "(TFFB is hard to beat at ranking; the gain from CalPts is expected in the level, MAE and bias). "
        "Rosterable pool, scored players only."
    )


def _add_reliability(b: _Builder, reliability: pd.DataFrame | None) -> None:
    """Predicted decile against realized for Hit3x%, Boom% and Bust%."""
    b.section("RELIABILITY  —  do Hit3x%, Boom% and Bust% come true? (predicted decile vs realized)")
    if reliability is None or reliability.empty:
        b.note("Not enough data yet -- no scored week has probabilities. Run `dfs results update`.")
        return
    b.verdict(reliability_verdict(reliability))
    table = reliability.assign(
        Predicted=reliability["Predicted"] / 100, Realized=reliability["Realized"] / 100
    )
    b.table(
        table,
        [
            ("Measure", "Measure"),
            ("Decile", "Decile"),
            ("n", "n"),
            ("Predicted", "Predicted"),
            ("Realized", "Realized"),
            ("Gap (pts)", "Gap"),
            ("Off?", "Flag"),
        ],
        percent={"Predicted", "Realized"},
        signed={"Gap (pts)"},
    )
    flagged = reliability[reliability["Flag"] == "off"]
    b.note(
        "Decile 1 = the lowest predicted probability. Gap = realized minus predicted, in percentage points "
        f"(flagged 'off' when beyond {results_signals.RELIABILITY_FLAG_POINTS:g} with n >= "
        f"{results_signals.RELIABILITY_MIN_N}; nothing is adjusted). "
        + (
            f"{len(flagged)} decile(s) are off: "
            + ", ".join(f"{r.Measure} decile {r.Decile} ({r.Gap:+.1f})" for r in flagged.itertuples())
            + ". "
            if len(flagged)
            else "No decile is off yet. "
        )
        + "The probability tables were learned from UM's errors and are applied to CalPts, so Boom% and "
        "Bust% may run slightly too wide. Few weeks scored: deciles are thin, read n."
    )


def _add_signals(b: _Builder, signal_table: pd.DataFrame | None) -> None:
    """Every context signal scored: n, mean miss against ProjPts and CalPts, and the hit rate."""
    b.section("SIGNALS  —  context, not proven to beat projections; scored here every week")
    if signal_table is None or signal_table.empty:
        b.note(
            "Not enough data yet -- no signals archive exists. Run `dfs results update` after a week's games."
        )
        return
    b.verdict(signals_verdict(signal_table))
    table = signal_table.rename(
        columns={
            "VsProj": "Actual - ProjPts",
            "VsCal": "Actual - CalPts",
            "HitProj": "Hit (vs ProjPts)",
            "HitCal": "Hit (vs CalPts)",
        }
    )
    b.table(
        table,
        [
            ("Signal", "Signal"),
            ("n", "n"),
            ("Actual - ProjPts", "Actual - ProjPts"),
            ("Actual - CalPts", "Actual - CalPts"),
            ("Hit (vs ProjPts)", "Hit (vs ProjPts)"),
            ("Hit (vs CalPts)", "Hit (vs CalPts)"),
        ],
        percent={"Hit (vs ProjPts)", "Hit (vs CalPts)"},
        signed={"Actual - ProjPts", "Actual - CalPts"},
    )
    for line in results_signals.r6_verdict_lines(signal_table):
        b.note(line)
    b.note(
        "Each row is a group of players the signal picked, as it stood before kickoff (weeks backfilled "
        "without lookahead). Hit = on the right side of the projection: above it for USAGE up, "
        "INJ+ and the top-8 matchups; below it for FADE, USAGE down and the bottom-4 matchups. "
        "Matchup rows are each team's top two players at the position by CalPts. The "
        "'Unflagged' rows are every pool player at that position with no signal: a signal means something "
        "only if its row differs from them (they have no direction, so no hit rate). BUY↑ was removed "
        "(wrong-signed in 12 seasons of back-tests). Shown as context. A signal with n of 0 has not "
        "fired yet."
    )


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
    for row in layout.verdict_rows:
        client.format_range(tab, f"A{row}", {"textFormat": {"bold": True, "fontSize": 10}})
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
