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

from dfs import calibration, props_edge, results_signals
from dfs import results_analysis as ra
from dfs.props_edge import Shadow
from dfs.sheet_clipping import fit_tab_widths
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
_COUNT_HEADERS = {"n", "Unflagged n", "Weeks", "Weeks up", "Week", "Decile"}
_TEXT_HEADERS = {
    "Format",
    "Model",
    "Source",
    "Flag",
    "Quintile",
    "Projection",
    "Signal",
    "Measure",
    "Off?",
    "Players rated by",
    "ESPN vs market",
    "Verdict",
}


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
    """Sam's question is whether CalPts beats TFFB, so each position leads with exactly that: "RB: CalPts
    beats TFFB clearly, MAE 4.69 vs 5.23." ("clearly" needs a gap of at least `RACE_CLEAR_GAP` with n >=
    `RACE_CLEAR_MIN_N`; a smaller gap is "too close to call", fewer players "too early to call"). A second
    sentence names the lowest-MAE source overall only when it is neither of the two (AggPts, say)."""
    parts = []
    for position in calibration.POSITIONS:
        part = race[race["Position"] == position]
        by_source = part.set_index("Source")
        if "CalPts" not in by_source.index or "TFFB" not in by_source.index:
            continue
        cal, tffb = by_source.loc["CalPts"], by_source.loc["TFFB"]
        n = min(cal["n"], tffb["n"])
        gap = tffb["MAE"] - cal["MAE"]  # positive: CalPts is closer to what happened
        mae = f"MAE {cal['MAE']:.2f} vs {tffb['MAE']:.2f}"
        if n < RACE_CLEAR_MIN_N:
            line = f"{position}: too early to call ({int(n)} players), {mae}."
        elif gap >= RACE_CLEAR_GAP:
            line = f"{position}: CalPts beats TFFB clearly, {mae}."
        elif -gap >= RACE_CLEAR_GAP:
            line = f"{position}: TFFB beats CalPts clearly, MAE {tffb['MAE']:.2f} vs {cal['MAE']:.2f}."
        else:
            line = f"{position}: CalPts and TFFB are too close to call, {mae}."
        best = part.sort_values("MAE").iloc[0]
        if best["Source"] not in ("CalPts", "TFFB") and best["n"] >= RACE_CLEAR_MIN_N:
            line += (
                f" Lowest of all: {best['Source']} (MAE {best['MAE']:.2f}, "
                f"{cal['MAE'] - best['MAE']:.2f} better than CalPts)."
            )
        parts.append(line)
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
    ownership: pd.DataFrame | None = None,
    ownership_gaps: pd.DataFrame | None = None,
    props_shadow: Shadow | None = None,
    teammate_race: pd.DataFrame | None = None,
    opportunity: pd.DataFrame | None = None,
    our_dst_race: pd.DataFrame | None = None,
) -> Layout:
    """The whole tab as rows plus formatting instructions. Pure. `signal_table` is
    `results_signals.signal_report` (None when no signals archive exists yet); `ownership` is
    `field_own.score_summary` and `ownership_gaps` `field_own.disagreement` (None leaves the sections out);
    `props_shadow` is `props_edge.Shadow` (None leaves PROPS SHADOW out); `teammate_race` is
    `calibration.teammate_bump_race` (the SHADOW SOURCES row for a starter's absence; None leaves it out);
    `opportunity` is `results_signals.opportunity_report` (OPPORTUNITY PER DOLLAR; None leaves it out);
    `our_dst_race` is `dfs.model.dst_study.model_check_race` (the OurDST row; None leaves it out)."""
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

    _add_learned(b, scored, signal_table, reliability)
    _add_projection_race(b, scored)
    _add_shadow_sources(b, scored, teammate_race, our_dst_race)
    _add_ownership(b, ownership)
    _add_ownership_gaps(b, ownership_gaps)
    _add_props_shadow(b, props_shadow)
    _add_opportunity(b, opportunity)
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
    race = calibration.backtest(scored, with_um=with_um, shadow=calibration.configured_shadow())
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
    trend = calibration.backtest_by_week(scored, with_um=with_um, shadow=calibration.configured_shadow())
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


# ---- "What the model learned this week" (Week 6, slice 6) -------------------------------------------------
LEARNED_BIAS_ROWS = 6  # the biggest bias corrections listed
LEARNED_MIN_CELL_N = 10  # a bias cell needs this many players behind it to be listed
WEEK_8_CHECKPOINT = (
    f"Week {RELIABILITY_ADJUST_WEEK}: recalibrate Hit3x%/Boom%/Bust% on 2026 results, drop any signal still "
    "not beating its baseline. No action now."
)
LEARNED_EXPLAINER = (
    "What learns and what does not. CalPts refits every week on all earlier weeks (the source weights and "
    "the bias corrections below). Model Check scores every week after `dfs week close`. The probability "
    "tables (Hit3x%, Boom%, Bust%) and the signal thresholds are FIXED: they come from 12 seasons and "
    "change only at a planned recalibration."
)
_SOURCE_ORDER = ("ProjPts", "SleeperPts", "FantasyProsPts")


def weeks_text(weeks: tuple[int, ...]) -> str:
    """`1-4` for a run of weeks, `1, 2, 4` otherwise, `none` for no weeks."""
    if not weeks:
        return "none"
    ordered = sorted(weeks)
    if ordered == list(range(ordered[0], ordered[-1] + 1)) and len(ordered) > 1:
        return f"{ordered[0]}\u2013{ordered[-1]}"
    return ", ".join(str(w) for w in ordered)


def shown_sources(now: calibration.Calibration, was: calibration.Calibration) -> tuple[str, ...]:
    """The three standing sources, then any shadow source that has joined the blend (now or last week), so the
    weights shown always add to 100%."""
    extra = [s for s in (*now.sources, *was.sources) if s not in _SOURCE_ORDER]
    return (*_SOURCE_ORDER, *dict.fromkeys(extra))


def weight_table(now: calibration.Calibration, was: calibration.Calibration) -> pd.DataFrame:
    """CalPts's source weights by position, now and last week (percent; blank when last week had no fit)."""
    rows = []
    for position in calibration.POSITIONS:
        if position not in now.weights:
            continue
        row = {"Position": position}
        for source in shown_sources(now, was):
            label = calibration.SOURCE_LABELS[source]
            row[label] = now.weights[position].get(source)
            row[f"{label} (last week)"] = None if was.is_empty else was.weights.get(position, {}).get(source)
        rows.append(row)
    return pd.DataFrame(rows)


def bias_changes(
    now: calibration.Calibration, was: calibration.Calibration, *, top: int = LEARNED_BIAS_ROWS
) -> pd.DataFrame:
    """The `top` biggest bias corrections now (by size, cells with at least `LEARNED_MIN_CELL_N` players),
    each beside what it was last week. A negative bias means the source runs high: CalPts takes that off."""
    rows = []
    for (source, position, tier), bias in now.bias.items():
        n = now.counts.get((source, position, tier), 0)
        labels = calibration.tier_labels(position)
        if n < LEARNED_MIN_CELL_N or not labels or tier >= len(labels):
            continue
        rows.append(
            {
                "Source": calibration.SOURCE_LABELS.get(source, source),
                "Position": position,
                "Salary tier": labels[tier],
                "Bias now": bias,
                "Bias last week": None if was.is_empty else was.bias.get((source, position, tier), 0.0),
                "n": n,
                "Thin": ra.is_thin(n),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.reindex(frame["Bias now"].abs().sort_values(ascending=False).index).head(top)


def race_leaders(race: pd.DataFrame) -> str:
    """ "Lowest MAE by position: QB TFFB, RB CalPts, ..." among TFFB, AggPts and CalPts; a position with fewer
    than `RACE_CLEAR_MIN_N` players is marked early."""
    parts = []
    for position in calibration.POSITIONS:
        part = race[race["Position"] == position]
        if part.empty:
            continue
        best = part.sort_values("MAE").iloc[0]
        early = " (early)" if best["n"] < RACE_CLEAR_MIN_N else ""
        parts.append(f"{position} {best['Source']}{early}")
    return "Lowest MAE by position: " + ", ".join(parts) + "." if parts else ""


def signal_progress(signal_table: pd.DataFrame) -> pd.DataFrame:
    """Each signal that has fired: n, its running hit rate against CalPts, how far it ran from CalPts, and the
    unflagged average at its position (the baseline it has to beat)."""
    if signal_table is None or signal_table.empty:
        return pd.DataFrame()
    rows = signal_table.set_index("Signal")
    out = []
    for name in rows.index:
        if name.startswith(results_signals.BASELINE_PREFIX) or int(rows.loc[name, "n"]) == 0:
            continue
        position = _signal_position(name)
        base = f"{results_signals.BASELINE_PREFIX}{position}"
        out.append(
            {
                "Signal": name,
                "n": int(rows.loc[name, "n"]),
                "Hit (vs CalPts)": rows.loc[name, "HitCal"],
                "Actual - CalPts": rows.loc[name, "VsCal"],
                "Unflagged avg": rows.loc[base, "VsCal"] if base in rows.index else None,
                "Thin": bool(rows.loc[name, "Thin"]),
            }
        )
    return pd.DataFrame(out)


def _add_learned(
    b: _Builder, scored: pd.DataFrame, signal_table: pd.DataFrame | None, reliability: pd.DataFrame | None
) -> None:
    """The block at the top: what CalPts learned from this week against last week, who leads the race, how
    each signal is running and whether the probabilities hold, then the Week 8 checkpoint. Nothing here is a
    recommendation: it makes the learning visible."""
    latest = int(pd.to_numeric(scored["week"], errors="coerce").max())
    b.section(f"WHAT THE MODEL LEARNED THIS WEEK  —  Week {latest}, against last week")
    shadow = calibration.configured_shadow()
    now = calibration.fit(scored, before_week=latest + 1, shadow=shadow)
    was = calibration.fit(scored, before_week=latest, shadow=shadow)
    if now.is_empty:
        b.note("Nothing to learn from yet: CalPts needs one scored week.")
    else:
        changes = bias_changes(now, was)
        first = f"CalPts is fitted on Weeks {weeks_text(now.weeks)}" + (
            f" (last week: {weeks_text(was.weeks)})."
            if not was.is_empty
            else " (its first fit: nothing to compare)."
        )
        if not changes.empty:
            top = changes.iloc[0]
            was_text = "" if pd.isna(top["Bias last week"]) else f" (was {top['Bias last week']:+.1f})"
            first += (
                f" Biggest correction: {top['Source']} {top['Position']} {top['Salary tier']} bias now "
                f"{top['Bias now']:+.1f}{was_text}."
            )
        b.verdict(first)
        weights = weight_table(now, was)
        b.note("Source weights by position (inverse error, pulled toward equal until there is enough data):")
        columns = [("Position", "Position")]
        for source in shown_sources(now, was):
            label = calibration.SOURCE_LABELS[source]
            columns += [(label, label)]
        for source in shown_sources(now, was):
            label = calibration.SOURCE_LABELS[source]
            columns += [(f"{label} (last week)", f"{label} (last week)")]
        b.table(weights, columns, percent={c for c, _ in columns if c != "Position"})
        if not changes.empty:
            b.blank()
            b.note("Biggest bias corrections (negative = the source runs high; CalPts takes that off):")
            b.table(
                changes,
                [
                    ("Source", "Source"),
                    ("Position", "Position"),
                    ("Salary tier", "Salary tier"),
                    ("Bias now", "Bias now"),
                    ("Bias last week", "Bias last week"),
                    ("n", "n"),
                ],
                signed={"Bias now", "Bias last week"},
            )
    race = calibration.backtest(scored, with_um=False, shadow=calibration.configured_shadow())
    if not race.empty:
        b.blank()
        b.verdict(race_leaders(race))
    progress = signal_progress(signal_table)
    if not progress.empty:
        b.blank()
        b.note("Signals that have fired, against the unflagged average at their position:")
        b.table(
            progress,
            [
                ("Signal", "Signal"),
                ("n", "n"),
                ("Hit (vs CalPts)", "Hit (vs CalPts)"),
                ("Actual - CalPts", "Actual - CalPts"),
                ("Unflagged avg", "Unflagged avg"),
            ],
            percent={"Hit (vs CalPts)"},
            signed={"Actual - CalPts", "Unflagged avg"},
        )
    if reliability is not None and not reliability.empty:
        b.blank()
        b.verdict("Probabilities: " + reliability_verdict(reliability))
    b.blank()
    b.note(LEARNED_EXPLAINER)
    b.note(WEEK_8_CHECKPOINT)


def _add_projection_race(b: _Builder, scored: pd.DataFrame) -> None:
    """The table Sam uses to choose his default projection: TFFB, AggPts and CalPts on weeks none of them
    trained on (CalPts is fitted on earlier weeks only; the 2nd scored week is the first it can be judged),
    first without UM, then with UM on the rows UM rates."""
    b.section("PROJECTION RACE  —  TFFB vs AggPts vs CalPts, each judged on weeks it never trained on")
    plain = calibration.backtest(scored, with_um=False, shadow=calibration.configured_shadow())
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
        "CalPts is the projection the model-driven tools use (late swap, lineup odds, Suggested); TFFB stays "
        "visible for comparison and this race keeps scoring both every week. It is each "
        "source's level bias corrected per position and salary tier (shrunk toward 0), then weighted; "
        "UM is not part of CalPts (it added nothing in 12 seasons of back-tests); it is its own row. "
        f"Scored weeks so far: {_weeks_text(weeks_trained)}. rho = rank correlation within position and week "
        "(TFFB is hard to beat at ranking; the gain from CalPts is expected in the level, MAE and bias). "
        "Rosterable pool, scored players only."
    )


def _add_shadow_sources(
    b: _Builder,
    scored: pd.DataFrame,
    teammate_race: pd.DataFrame | None = None,
    our_dst_race: pd.DataFrame | None = None,
) -> None:
    """Shadow sources (ESPN, props): tracked from their first scored week, in the CalPts blend only after
    `shadow_min_weeks`. A status line always; the table (the source beside TFFB and CalPts on the players it
    rated, marked "shadow") once a source has rated anyone."""
    policy = calibration.configured_shadow()
    progress = calibration.shadow_progress(scored, policy)
    if not progress:
        return
    status = "; ".join(
        f"{label} {'in the blend' if joined else f'{weeks} of {needed} scored weeks'}"
        for label, weeks, needed, joined in progress
    )
    race = pd.concat(
        [
            calibration.shadow_race(scored, shadow=policy),
            calibration.rb1_spread_race(scored),
            *(race for race in (teammate_race, our_dst_race) if race is not None and not race.empty),
        ],
        ignore_index=True,
    )
    b.section("SHADOW SOURCES  —  scored every week, kept out of CalPts until they have enough weeks")
    b.verdict(f"Shadow status: {status}.")
    if race.empty:
        b.note(
            "No shadow source has rated a scored player yet (ESPN and props are collected from Week 6; "
            "`dfs sync` pulls ESPN, and props come with the Saturday pull)."
        )
        return
    race = race.assign(
        _p=race["Position"].map(["All", *calibration.POSITIONS].index),
        _s=race["Source"].map(lambda name: 2 if "(shadow)" in name else (1 if name == "CalPts" else 0)),
    )
    race = race.sort_values(["Rated by", "_p", "_s"]).drop(columns=["_p", "_s"])
    race = race.assign(Thin=race["n"].map(ra.is_thin)).rename(
        columns={"Rated by": "Players rated by", "Source": "Projection"}
    )
    b.table(
        race,
        [
            ("Players rated by", "Players rated by"),
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
    b.note(
        "Each shadow source is judged on the players it rated, beside TFFB and CalPts (weeks CalPts did "
        "not train on). A source joins the CalPts blend, through the same weighting and shrinkage, once it "
        "has the weeks shown above; until then CalPts is unchanged by it. The RB1 spread row is CalPts plus "
        f"{calibration.RB1_SPREAD_PER_POINT:g} point per point of spread in his team's favour, for each "
        "team's top running back only; it is not a blend source and joins CalPts only if it beats CalPts. "
        "The teammate-bump row is CalPts plus what the research measured teammates scoring above "
        "projection when a regular WR1, WR2, TE1 or RB1 was listed OUT (models/research/teammate_bump.csv), "
        "on the teammates of the one regular out; it does not touch CalPts or late swap. The OurDST row is "
        "our own DST projection (the Vegas line, both units' sack rates and the league's recent turnover "
        "level, fitted on 2014 to last season; docs/MODEL.md), on every DST CalPts rated; nothing reads it."
    )


def _add_ownership(b: _Builder, ownership: pd.DataFrame | None) -> None:
    """Shadow ownership (`field_own`): how far each ownership number is from what his contests really
    drafted, and how light it runs on the chalk. Left out when it could not be built."""
    if ownership is None:
        return
    b.section("OWNERSHIP  —  GppOwn and CashOwn against what your contests really drafted (shadow)")
    if ownership.empty:
        b.note("Not enough data yet -- needs two weeks of your contest standings in a format.")
        return
    b.table(
        ownership,
        [
            ("Format", "Format"),
            ("Model", "Model"),
            ("Weeks", "Weeks"),
            ("MAE", "MAE"),
            ("Chalk bias", "Chalk bias"),
        ],
        percent={"MAE", "Chalk bias"},
        signed={"Chalk bias"},
    )
    b.note(
        "MAE is the mean miss in ownership points on players owned 5%+ (actual or TFFB's projection). Chalk "
        "bias is projected minus actual on the players your contests drafted at 20%+ (GPP) or 30%+ (cash); "
        "negative = the projection ran light. Each week is judged on a fit from the weeks before it. Shadow: "
        "nothing reads GppOwn, CashOwn or OurOwn until three scored weeks out of sample and your OK."
    )


def _add_ownership_gaps(b: _Builder, gaps: pd.DataFrame | None) -> None:
    """When CalPts and TFFB disagreed (the premise behind `OurOwn`): who was right."""
    if gaps is None:
        return
    b.section("WHEN WE DISAGREE  —  did the gap between CalPts and TFFB's projection come true? (shadow)")
    if gaps.empty:
        b.note("Not enough data yet -- needs a scored week with a pre-lock snapshot.")
        return
    b.table(
        gaps,
        [
            ("Week", "Week"),
            ("n", "n"),
            ("Gap share", "Gap share"),
            ("Top fifth", "Top fifth"),
            ("Bottom fifth", "Bottom fifth"),
        ],
        three_decimals={"Gap share"},
        signed={"Top fifth", "Bottom fifth"},
    )
    b.note(
        "Gap share is how much of CalPts minus ProjPts came true (1 = CalPts was right, 0 = TFFB was, "
        "negative = the gap went the wrong way). Top / Bottom fifth are actual minus ProjPts for the fifth "
        "of players CalPts liked most / least relative to TFFB; if CalPts is right the top is positive and "
        "the bottom negative. Players projected at 1%+ owned who played."
    )


def _add_opportunity(b: _Builder, report: pd.DataFrame | None) -> None:
    """Opportunity per dollar (`opportunity.py`, shadow): does usage that outruns salary beat what CalPts and
    the salary alone expected? Left out when it could not be built."""
    if report is None:
        return
    b.section("OPPORTUNITY PER DOLLAR  —  does opportunity the salary has not caught beat CalPts? (shadow)")
    if report.empty or int(report["n"].sum()) == 0:
        b.note("Not enough data yet -- needs three scored weeks of games before a slate.")
        return
    b.table(
        report,
        [
            ("Signal", "Signal"),
            ("n", "n"),
            ("vs ProjPts", "VsProj"),
            ("vs CalPts", "VsCal"),
            ("vs salary", "VsSal"),
            ("SE", "SE"),
            ("Z", "Z"),
            ("Hit vs CalPts", "HitCal"),
            ("Weeks", "Weeks"),
            ("Weeks up", "WeeksUp"),
        ],
        percent={"Hit vs CalPts"},
        signed={"vs ProjPts", "vs CalPts", "vs salary", "Z"},
    )
    b.note(
        "Mean actual minus ProjPts / CalPts / what a player at that salary and position scored in the "
        "earlier weeks. Z is the CalPts mean over its standard error, signed so positive = the signal "
        "pointed the right way (player-games are treated as independent, so it reads generous). Weeks up = "
        "weeks the group beat CalPts in the signal's direction. Every feature is built from earlier games "
        "only. TPRR~ is a proxy (snaps x team dropback rate, not real route counts). Shadow: nothing reads "
        "these until they beat CalPts by 2+ SE on held-out weeks and in most weeks, and you say so."
    )


def _add_props_shadow(b: _Builder, shadow: Shadow | None) -> None:
    """ESPN's stat projections against the prop market (`props_edge`): when ESPN sat away from the book's
    line, did its side beat the price? Left out when it could not be built."""
    if shadow is None:
        return
    b.section("PROPS SHADOW  —  when ESPN differs from the prop line, does its side beat the price? (shadow)")
    if shadow.table.empty:
        b.note("Not enough data yet -- needs a saved props pull and ESPN snapshot from a completed week.")
        return
    b.verdict(props_edge.headline(shadow))
    b.table(
        shadow.table,
        [
            ("Market", "Market"),
            ("ESPN vs market", "Band"),
            ("n", "n"),
            ("ESPN side won", "Won"),
            ("Price needed", "Needed"),
            ("Return", "Return"),
            ("Verdict", "Verdict"),
        ],
        percent={"ESPN side won", "Price needed", "Return"},
        signed={"Return"},
    )
    b.note(
        "ESPN vs market is how far ESPN's chance of the Over (Yes for a TD) sits from the book's, in points; "
        "each band includes the bigger gaps. ESPN side won is how often the side ESPN favoured won (pushes "
        "and players who did not play dropped); Price needed is the win rate the median quoted prices "
        "asked for; Return is per unit staked at those prices. ESPN's spread comes from "
        f"{shadow.counts.get('fit_players', 0)} projections, week(s) {_weeks_text(shadow.fit_weeks)}, shrunk "
        "toward nflverse history, and a week is judged on a spread fitted without it. A verdict word needs "
        f"{props_edge.MIN_N_VERDICT} rows in the cell. Shadow: no player column, no picks, nothing reads it."
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
    # A rewritten tab keeps its old notes: the layout moves from week to week, so a header's note would land
    # on whatever value now sits at that cell. Clear them all; the header rows get theirs back below.
    client.clear_notes(tab, f"A1:{LAST_COLUMN}{last_row + 20}")
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
    # The widths above are reset on every write, so fit any column this run's text cuts off (a long R6 signal
    # name in A, a long metric header): the polish-time fit does not survive a rewrite of the tab.
    fit_tab_widths(client, tab)
    return f"{tab}: built ({len(layout.rows)} rows, {len(layout.thin_rows)} thin, {notes} header note(s))"
