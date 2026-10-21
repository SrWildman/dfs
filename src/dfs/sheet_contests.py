"""`Contests`: what each contest series has done for you, what the cash lines really were, and how the
fields build. A season-level tab rebuilt from `data/contests/` on EVERY run (`dfs contests update` /
`dfs week close`),
so nothing on it is typed and no hand-edited cell survives. Contents, top to bottom:

1. **Series you play this season**, one row per series, grouped by format: fee, typical field, share of the
   field paid, entries, ROI, cash rate, average finish, the cash line the format's curve expects on an average
   slate, your latest real cash line from a standings file, and how that series' cash line compares with the
   other series of its format **in the same weeks** (the within-week gap is the real toughness signal);
2. **Cash lines from your standings files**, contest by contest: the score at the last paid place, the top
   10 %, the top 1 %, the winner and the median;
3. **How the field builds**, per format: stacking, salary left, FLEX, chalk, DST and duplicates, for the whole
   field, for the top 10 % (what won) and for you;
4. **Head to head**: your win rate by score band and your record against the opponents you have met twice or
   more;
5. **Left out of the ROI above**: freerolls, Showdown, private and unrecognised contests with their results.

Series toughness from the engine (`toughness.py`) is NOT shown as a number: with the data so far the engine's
own leave-one-week-out check shrinks every series to its format, so the honest statement is "not
separable yet".
The within-week cash-line gaps in section 1 are the measured version of the same idea.

The layout is built by `build_layout` (pure, no sheet) and written by `write_contests`.
"""

from __future__ import annotations

import math
import re
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from dfs.contests import history
from dfs.contests import series as series_mod
from dfs.contests.decisions import short_series
from dfs.sheet_clipping import fit_tab_widths
from dfs.sheet_column_notes import CONTESTS_NOTES, apply_notes_to_values
from dfs.sheets import SheetsClient

CONTESTS_TAB = "Contests"
COLUMN_COUNT = 14  # A..N
LAST_COLUMN = "N"
NOTE_WRAP_CHARS = 170
FIRST_COLUMN_PX = 300
OTHER_COLUMN_PX = 92
MAIN_FORMATS = (
    series_mod.H2H,
    series_mod.FIFTY_FIFTY,
    series_mod.DOUBLE_UP,
    series_mod.MULTIPLIER,
    series_mod.SE_GPP,
    series_mod.N_MAX_GPP,
)
LEFT_OUT_FORMATS = (
    series_mod.FREEROLL,
    series_mod.SHOWDOWN,
    series_mod.PRIVATE,
    series_mod.OTHER,
)
FIELD_GROUPS = (("all", "field"), ("top10", "top 10%"), ("me", "you"))
TEXT_HEADERS = {"Series", "Format", "Latest cash line", "Your score", "Opponent (2+ meetings)"}
OPPONENTS_SHOWN = 15  # the most-met opponents
TOUGHNESS_NOTE = (
    "Series toughness is not separable yet: the engine's own leave-one-week-out check finds a series no "
    "different from its format, so the gap to the other series of the same format in the same weeks "
    "(a real number from the standings files) is shown instead. It firms up as standings pile up."
)
H2H_USE = "H2H lobby: favour opponents you beat; skip the ones who beat you."
_FORMULA_STARTS = ("=", "+", "-", "@")


@dataclass
class Tables:
    """Everything the tab reads, as the CSVs `dfs contests analyze` writes them."""

    typed: pd.DataFrame
    profiles: pd.DataFrame
    tough_series: pd.DataFrame
    field_stats: pd.DataFrame
    h2h_bands: pd.DataFrame
    h2h_opponents: pd.DataFrame
    winners: pd.DataFrame = field(default_factory=pd.DataFrame)  # contests/winners.csv: top 1% vs field
    decisions: pd.DataFrame = field(default_factory=pd.DataFrame)  # decisions.csv: his entries, scored
    swings: pd.DataFrame = field(default_factory=pd.DataFrame)  # decision_swings.csv: biggest swing a contest
    pool_hits: pd.DataFrame = field(default_factory=pd.DataFrame)  # pool_hits.csv: logged at each week close


def _read(path: Path, **kw) -> pd.DataFrame:
    return pd.read_csv(path, **kw) if path.exists() and path.stat().st_size else pd.DataFrame()


def load_tables(folder: Path) -> Tables:
    folder = Path(folder)
    log = history.read_log(folder / "contest_log.csv")
    return Tables(
        typed=history.typed(log) if len(log) else pd.DataFrame(),
        profiles=_read(folder / "profiles.csv", dtype={"contest_id": str}),
        tough_series=_read(folder / "toughness_series.csv"),
        field_stats=_read(folder / "field_stats.csv", dtype={"contest_id": str}),
        h2h_bands=_read(folder / "h2h_bands.csv"),
        h2h_opponents=_read(folder / "h2h_opponents.csv"),
        winners=_read(folder / "winners.csv"),
        decisions=_read(folder / "decisions.csv", dtype={"contest_id": str}),
        swings=_read(folder / "decision_swings.csv", dtype={"contest_id": str}),
        pool_hits=_read(folder / "pool_hits.csv"),
    )


@dataclass
class Layout:
    rows: list[list]
    title_row: int = 1
    status_row: int = 2
    section_rows: list[int] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)
    verdict_rows: list[int] = field(default_factory=list)
    muted_rows: list[int] = field(default_factory=list)
    percent_cells: list[str] = field(default_factory=list)
    number_cells: list[str] = field(default_factory=list)  # one decimal
    count_cells: list[str] = field(default_factory=list)
    money_cells: list[str] = field(default_factory=list)  # dollars
    good_high: list[str] = field(default_factory=list)  # white -> green with the value
    good_low: list[str] = field(default_factory=list)  # green for low, e.g. a finish percentile
    tough_gap: list[str] = field(default_factory=list)  # red above 0 (tougher), green below (easier)
    diff_good: list[str] = field(default_factory=list)  # green above 0, red below (ROI)


def _clean(value):
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
        return len(self.layout.rows) + 1

    def add(self, values: list) -> int:
        cleaned = [_clean(v) for v in values]
        self.layout.rows.append(cleaned + [""] * (COLUMN_COUNT - len(cleaned)))
        return len(self.layout.rows)

    def blank(self) -> None:
        self.add([])

    def section(self, title: str) -> None:
        self.blank()
        self.layout.section_rows.append(self.add([title]))

    def verdict(self, text: str) -> None:
        for line in textwrap.wrap(text, NOTE_WRAP_CHARS) or [""]:
            self.layout.verdict_rows.append(self.add([line]))

    def note(self, text: str) -> None:
        for line in textwrap.wrap(text, NOTE_WRAP_CHARS) or [""]:
            self.layout.note_rows.append(self.add([line]))

    def header(self, names: list[str]) -> int:
        row = self.add(names)
        self.layout.header_rows.append(row)
        return row


# ---- numbers --------------------------------------------------------------------------------------


def _nan() -> float:
    return math.nan


def _cashed(frame: pd.DataFrame) -> pd.Series:
    """True where an entry paid: inside the places paid when both are known, else any winnings."""
    inside = frame["place"].astype("Float64") <= frame["places_paid"].astype("Float64")
    known = frame["place"].notna() & frame["places_paid"].notna()
    return inside.where(known, frame["winnings"] > 0).fillna(False).astype(bool)


def series_rows(tables: Tables, season: int) -> pd.DataFrame:
    """One row per main-format series played in `season` (ordered: format, toughest first, most played)."""
    typed = tables.typed
    if typed.empty:
        return pd.DataFrame()
    this = typed[(typed["season"] == season) & typed["format"].isin(MAIN_FORMATS)]
    rows = []
    gaps = peer_gaps(tables.profiles)
    latest = latest_cash_lines(tables.profiles)
    tough = {}
    if len(tables.tough_series):
        for rec in tables.tough_series.itertuples(index=False):
            tough[(rec.format, rec.series)] = rec.expected_cash_line
    for (fmt, name), sub in this.groupby(["format", "series"], sort=False):
        everything = typed[(typed["format"] == fmt) & (typed["series"] == name)]
        fees = sub["fee"].sum()
        finished = sub[sub["place"].notna() & sub["entries"].notna()]
        gap = gaps.get((fmt, name))
        rows.append(
            {
                "format": fmt,
                "series": name,
                "fee": float(everything["fee"].median()),
                "field": float(everything["entries"].astype("Float64").median()),
                "paid": float(everything["payout_ratio"].astype("Float64").median()),
                "played": len(sub),
                "roi": (sub["winnings"].sum() - fees) / fees if fees else _nan(),
                "cash_rate": float(_cashed(sub).mean()) if len(sub) else _nan(),
                "finish": (
                    float(
                        (finished["place"].astype("Float64") / finished["entries"].astype("Float64")).mean()
                    )
                    if len(finished) and fmt != series_mod.H2H
                    else _nan()
                ),
                "expected": tough.get((fmt, name), _nan()),
                "latest": latest.get((fmt, name), ""),
                "gap": gap[0] if gap else _nan(),
                "gap_weeks": gap[1] if gap else 0,
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_format_order"] = out["format"].map({f: i for i, f in enumerate(MAIN_FORMATS)})
    out["_gap_sort"] = out["gap"].fillna(-1e9)
    out = out.sort_values(["_format_order", "_gap_sort", "played"], ascending=[True, False, False])
    return out.drop(columns=["_format_order", "_gap_sort"]).reset_index(drop=True)


def _classic_profiles(profiles: pd.DataFrame) -> pd.DataFrame:
    if profiles.empty:
        return profiles
    keep = profiles["kind"].astype(str).eq("Classic") & profiles["cash_line"].notna()
    return profiles[keep & profiles["week"].notna()]


def latest_cash_lines(profiles: pd.DataFrame) -> dict[tuple[str, str], str]:
    """`{(format, series): "153.1 (Wk 5)"}` from the most recent standings file of each series."""
    out: dict[tuple[str, str], str] = {}
    classic = _classic_profiles(profiles)
    if classic.empty:
        return out
    for (fmt, name), sub in classic.sort_values(["season", "week"]).groupby(["format", "series"]):
        last = sub.iloc[-1]
        out[(fmt, name)] = f"{float(last['cash_line']):.1f} (Wk {int(last['week'])})"
    return out


def peer_gaps(profiles: pd.DataFrame) -> dict[tuple[str, str], tuple[float, int]]:
    """`{(format, series): (mean gap in points, weeks)}`: the series' cash line minus the mean cash line of
    the OTHER series of its format in the same week (positive = it took a higher score to cash = tougher).
    Only weeks where the format has at least two series with a standings file count."""
    classic = _classic_profiles(profiles)
    gaps: dict[tuple[str, str], list[float]] = {}
    if classic.empty:
        return {}
    for (_season, _week, fmt), group in classic.groupby(["season", "week", "format"]):
        by_series = group.groupby("series")["cash_line"].mean()
        if len(by_series) < 2:
            continue
        for name, value in by_series.items():
            others = by_series.drop(name).mean()
            gaps.setdefault((fmt, name), []).append(float(value - others))
    return {key: (sum(v) / len(v), len(v)) for key, v in gaps.items()}


def field_by_format(tables: Tables) -> dict[str, pd.DataFrame]:
    """`{format: frame of group means}` from the per-contest field stats, joined to each contest's format."""
    stats, profiles = tables.field_stats, tables.profiles
    if stats.empty or profiles.empty:
        return {}
    formats = profiles.drop_duplicates("contest_id").set_index("contest_id")["format"]
    joined = stats.assign(format=stats["contest_id"].map(formats))
    out = {}
    for fmt, sub in joined.dropna(subset=["format"]).groupby("format"):
        rows = []
        for group, label in FIELD_GROUPS:
            part = sub[sub["group"] == group]
            if part.empty:
                continue
            means = part[
                [
                    "qb_stack1",
                    "qb_stack2",
                    "bringback",
                    "avg_unspent",
                    "share_unspent_1k",
                    "flex_rb",
                    "flex_wr",
                    "flex_te",
                    "avg_chalk",
                    "dst_with_qb",
                    "dst_vs_qb",
                    "dup_share",
                ]
            ].mean()
            rows.append({"label": label, "contests": len(part), **means.to_dict()})
        if rows:
            out[str(fmt)] = pd.DataFrame(rows)
    return out


def left_out_rows(tables: Tables, season: int) -> pd.DataFrame:
    typed = tables.typed
    if typed.empty:
        return pd.DataFrame()
    rows = []
    for fmt in LEFT_OUT_FORMATS:
        sub = typed[typed["format"] == fmt]
        if sub.empty:
            continue
        now = sub[sub["season"] == season]
        rows.append(
            {
                "format": fmt,
                "season_entries": len(now),
                "season_net": float((now["winnings"] - now["fee"]).sum()),
                "all_entries": len(sub),
                "all_net": float((sub["winnings"] - sub["fee"]).sum()),
                "cash_rate": float(_cashed(sub).mean()),
            }
        )
    return pd.DataFrame(rows)


# ---- the layout -----------------------------------------------------------------------------------


def _range(letter: str, first: int, last: int) -> str:
    return f"{letter}{first}:{letter}{last}"


DECISIONS_HEADERS = ["Your decisions", "So far", "Based on"]  # the first cell is `decisions_title(...)`
DECISIONS_NOTE = (
    "H2H entries and weeks without a standings file are not in this block, so its counts differ from "
    "Bankroll's."
)
DECISIONS_TITLE_START = "Your decisions \u2014"
MINUS = "\u2212"  # a real minus sign: a leading '-' would be read as a formula
DASH = "-"


def _rate(hits: float, total: float) -> str:
    return f"{hits / total:.0%}" if total else DASH


def _top(pct: float) -> str:
    """A finish share as 'top 4%' (lower is better)."""
    return f"top {pct * 100:.0f}%" if pct >= 0.01 else f"top {pct * 100:.1f}%"


def decision_lines(tables: Tables, season: int) -> tuple[int | None, list[str]]:
    """`(week, five lines)` for the latest week with graded entries of `season`: the Monday post-mortem. Each
    line leads with numbers; a measure with no data reads '-'."""
    d = tables.decisions
    if d.empty or "season" not in d.columns:
        return None, []
    d = d[d["season"] == season]
    if d.empty:
        return None, []
    week = int(d["week"].max())
    cur = d[d["week"] == week]
    cash, gpp = cur[cur["cls"] == "cash"], cur[cur["cls"] == "gpp"]
    lines = []

    if cash.empty:
        lines.append(f"Cash: {DASH} (no double-up entries in Week {week}).")
    else:
        per = "; ".join(
            f"{short_series(r.series)} {'cashed by' if r.cashed else 'missed by'} {abs(r.margin):.1f}"
            if r.margin == r.margin
            else f"{short_series(r.series)} {DASH}"
            for r in cash.sort_values("margin", ascending=False).itertuples(index=False)
        )
        copies = int(cash["copies"].max())
        lines.append(
            f"Cash: scored {', '.join(f'{p:.1f}' for p in sorted(set(cash['points'].round(1))))}  -  {per}. "
            f"Copies of your lineup in the field: {copies + 1 if copies else 1} identical "
            f"entr{'ies' if copies else 'y'} (including yours)."
        )

    if gpp.empty:
        lines.append(f"GPP: {DASH} (no GPP entries in Week {week}).")
    else:
        best = gpp.loc[gpp["pct"].idxmin()]
        by_contest = "; ".join(
            f"{short_series(series)} {', '.join(_top(p) for p in sub.sort_values('pct')['pct'])}"
            for series, sub in gpp.groupby("series", sort=False)
        )
        lines.append(
            f"GPP: {len(gpp)} entries, average finish {_top(gpp['pct'].mean())}, "
            f"{int(gpp['cashed'].sum())} cashed, best {_top(best['pct'])} in {short_series(best['series'])}. "
            f"{by_contest}."
        )

    swings = tables.swings
    swings = swings[(swings["season"] == season) & (swings["week"] == week)] if len(swings) else swings
    if swings.empty:
        lines.append(f"Biggest swing: {DASH}.")
    else:
        s = swings.loc[swings["swing"].abs().idxmax()]
        lines.append(
            f"Biggest swing: you played {s['player']} ({s['player_own']:.0%} owned, "
            f"{s['player_pts']:.1f} pts) instead of {s['alt']} ({s['alt_own']:.0%}, {s['alt_pts']:.1f} pts), "
            "the field's favourite at the "
            f"same position and price: {'won' if s['swing'] > 0 else 'cost'} {abs(s['swing']):.1f} points "
            f"in the {short_series(s['series'])}."
        )

    hits = tables.pool_hits
    hit = hits[(hits["season"] == season) & (hits["week"] == week)] if len(hits) else hits
    if hit.empty:
        lines.append(f"Pool hit rate: {DASH} (not logged for Week {week}: it needs your pool at the close).")
    else:
        h = hit.iloc[0]
        lines.append(
            f"Pool hit rate: {int(h['pooled_hits'])} of {int(h['pooled'])} pooled players beat their CalPts "
            f"({_rate(h['pooled_hits'], h['pooled'])}), against {int(h['likes_hits'])} of {int(h['likes'])} "
            f"model favourites you left out ({_rate(h['likes_hits'], h['likes'])})."
        )

    sib = cur[cur["sib_total"] > 0]
    if sib.empty:
        lines.append(
            f"Contest choice: {DASH} (no other contest of the same kind in your standings files that week)."
        )
    else:
        lines.append(
            f"Contest choice: the same lineups would have cashed in {int(sib['sib_cashed'].sum())} of "
            f"{int(sib['sib_total'].sum())} (lineup, other contest) pairs among the contests in your "
            "standings files."
        )
    return week, lines


def _note_header(row: list) -> list:
    """The row as the hover-note lookup sees it: the decisions block's dated title is keyed as the plain
    `DECISIONS_HEADERS[0]`."""
    if str(row[0]).startswith(DECISIONS_TITLE_START):
        return [DECISIONS_HEADERS[0], *row[1:]]
    return row


def decision_weeks(tables: Tables, season: int) -> list[int]:
    """The weeks of `season` that have graded entries (a standings file), ascending."""
    d = tables.decisions
    if d.empty or "season" not in d.columns:
        return []
    return sorted(int(w) for w in d[d["season"] == season]["week"].unique())


def decisions_title(weeks: list[int]) -> str:
    return f"{DECISIONS_TITLE_START} weeks with your standings files ({', '.join(str(w) for w in weeks)})"


def _n_of(hits: float, total: float, unit: str = "") -> str:
    """'7 of 43 (16%)': a count out of a total, never a bare fraction or percentage."""
    return f"{int(hits)} of {int(total)}{unit} ({_rate(hits, total)})"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _based_on(frame: pd.DataFrame, unit: str = "entry") -> str:
    """'7 entries in 3 weeks': how many entries, and how many different weeks they come from."""
    entries = len(frame)
    unit_text = unit if entries == 1 else "entries" if unit == "entry" else f"{unit}s"
    return f"{entries} {unit_text} in {_plural(int(frame['week'].nunique()), 'week')}"


def _contest_name(series: str) -> str:
    """'NFL GIANT $10 Double Up [Single Entry]' -> 'NFL GIANT $10 Double Up'."""
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", str(series)).strip()


def _signed(value: float) -> str:
    return f"+{value:.1f}" if value >= 0 else f"{MINUS}{abs(value):.1f}"


def decision_season_rows(tables: Tables, season: int) -> list[list[str]]:
    """The season block: the same measures added up over every week in the standings files. Every value is
    text that reads on its own ('7 of 43 (16%)'); 'Based on' counts the entries and their weeks."""
    d = tables.decisions
    d = d[d["season"] == season] if len(d) and "season" in d.columns else d
    if d.empty:
        return []
    cash, gpp = d[d["cls"] == "cash"], d[d["cls"] == "gpp"]
    rows = []
    if len(cash):
        rows.append(
            [
                "Cash entries cashed",
                _n_of(cash["cashed"].sum(), len(cash)),
                _based_on(cash),
            ]
        )
        scored = cash[cash["margin"].notna()]
        if len(scored):
            rows.append(
                [
                    "Cash: average margin to the cash line",
                    f"{_signed(scored['margin'].mean())} points",
                    _based_on(scored),
                ]
            )
        most = cash.loc[cash["copies"].idxmax()]
        rows.append(
            [
                "Cash: most copies of one lineup",
                f"{int(most['copies']) + 1} copies of your lineup "
                f"(Week {int(most['week'])}, {_contest_name(most['series'])})",
                _based_on(cash),
            ]
        )
    if len(gpp):
        rows.append(["GPP: average finish", _top(gpp["pct"].mean()), _based_on(gpp)])
        rows.append(
            [
                "GPP: entries in the top 10%",
                _n_of((gpp["pct"] <= 0.10).sum(), len(gpp)),
                _based_on(gpp),
            ]
        )
        rows.append(["GPP: entries cashed", _n_of(gpp["cashed"].sum(), len(gpp)), _based_on(gpp)])
    swings = tables.swings
    swings = swings[swings["season"] == season] if len(swings) else swings
    if len(swings):
        per_week = swings.loc[swings.groupby("week")["swing"].apply(lambda s: s.abs().idxmax())]
        won, cost = per_week[per_week["swing"] > 0], per_week[per_week["swing"] < 0]
        text = f"Won {_plural(len(won), 'week')}, cost {len(cost)}."

        def _pick(r) -> str:
            return f"Wk {int(r['week'])} {r['player']} over {r['alt']} {_signed(r['swing'])}"

        clauses = []
        if len(won):
            clauses.append(f"Biggest: {_pick(won.loc[won['swing'].idxmax()])}")
        if len(cost):
            clauses.append(f"{'worst' if clauses else 'Worst'}: {_pick(cost.loc[cost['swing'].idxmin()])}")
        if clauses:
            text += " " + "; ".join(clauses)
        rows.append(["Biggest weekly swing: won / cost", text, _plural(len(per_week), "week")])
    hits = tables.pool_hits
    hits = hits[hits["season"] == season] if len(hits) else hits
    if len(hits):
        rows.append(
            [
                "Beat their projection: your pool / model favourites you left out",
                f"{_n_of(hits['pooled_hits'].sum(), hits['pooled'].sum())} / "
                f"{_n_of(hits['likes_hits'].sum(), hits['likes'].sum())}",
                f"{_plural(len(hits), 'week')} logged",
            ]
        )
    sib = d[d["sib_total"] > 0]
    if len(sib):
        rows.append(
            [
                "Same lineups cashing in other contests",
                _n_of(sib["sib_cashed"].sum(), sib["sib_total"].sum(), " pairs"),
                _based_on(sib),
            ]
        )
    return rows


def _add_decisions(b: _Builder, tables: Tables, season: int) -> None:
    """The Monday post-mortem of his own decisions, then the season block under it."""
    week, lines = decision_lines(tables, season)
    if week is None:
        return
    b.section(f"THIS WEEK, YOUR DECISIONS  -  Week {week}")
    for line in lines:
        b.verdict(line)
    b.note(
        "Everything above comes from your standings files and the actual stats: nothing is estimated. "
        "Finish is place divided by entrants (lower is better); a swing compares your player's points with "
        "the field's most-owned player at the same position within $400 of his salary."
    )
    rows = decision_season_rows(tables, season)
    if rows:
        b.blank()
        b.note(DECISIONS_NOTE)
        b.header([decisions_title(decision_weeks(tables, season)), *DECISIONS_HEADERS[1:]])
        for row in rows:
            b.add(row)


WINNERS_HEADERS = [
    "Build feature",
    "Top 1% did",
    "Whole field did",
    "Gap (top 1% minus field)",
    "Top-1% lineups counted",
    "Field lineups counted",
]
WINNERS_BIG_GAP = 0.05  # a share that differs by this much between the top 1% and the field is called out
WINNERS_NOTE = (
    "The top 1% of each of your large GPPs (700+ entries, standings files only) beside every lineup in "
    "the same contests, by how the lineup was built. Your stack is the QB and one to three teammates at "
    "any skill position; the bring-back is a RB, WR or TE of the QB's opponent. Ownership is what the "
    "contest's own standings say, not the projection. Rebuilt at every week close."
)


def _add_winners(b: _Builder, winners: pd.DataFrame) -> None:
    """'What wins in your contests': the top 1% of the large GPPs beside the field, one row per measure."""
    lay = b.layout
    b.section("WHAT WINS IN YOUR CONTESTS  -  the top 1% of your large GPPs beside the field")
    if winners.empty:
        b.verdict("No large GPP standings with lineups yet (700+ entries).")
        return
    b.note(WINNERS_NOTE)
    big = winners[(winners["kind"] == "share") & (winners["diff"].abs() >= WINNERS_BIG_GAP)]
    if len(big):
        top = big.reindex(big["diff"].abs().sort_values(ascending=False).index)
        b.verdict(
            "Biggest gaps (top 1% minus the field): "
            + "; ".join(f"{r.measure.lower()} {r.diff * 100:+.0f} pts" for r in top.head(3).itertuples())
            + f". Based on {int(winners['n_top1'].max()):,} top-1% lineups from "
            f"{int(winners['contests'].max())} contests: directional, not proof."
        )
    b.header(WINNERS_HEADERS)
    percent, money = [], []
    first = b.next_row
    for rec in winners.itertuples(index=False):
        row = b.next_row
        if rec.kind == "share" or rec.measure.startswith(("Ownership sum", "DST ownership")):
            percent.append(row)
            cells = [rec.top1, rec.field, rec.diff]
        elif rec.measure.startswith("Salary"):
            money.append(row)
            cells = [rec.top1, rec.field, rec.diff]
        else:  # the ownership product: tiny numbers, shown as text with the ratio as the difference
            ratio = rec.top1 / rec.field if rec.field else float("nan")
            cells = [f"{rec.top1:.1e}", f"{rec.field:.1e}", f"{ratio:.2f}x" if ratio == ratio else ""]
        b.add([rec.measure, *cells, rec.n_top1, rec.n_field])
    last = b.next_row - 1
    lay.count_cells += [_range("E", first, last), _range("F", first, last)]
    lay.percent_cells += [_range(c, r, r) for r in percent for c in "BCD"]
    lay.money_cells += [_range(c, r, r) for r in money for c in "BCD"]


def build_layout(tables: Tables, season: int) -> Layout:
    b = _Builder()
    lay = b.layout
    typed = tables.typed
    entries_now = int((typed["season"] == season).sum()) if len(typed) else 0
    b.add(["Contests"])
    files = int(len(tables.profiles))
    b.add(
        [
            f"{len(typed):,} entries logged ({entries_now} in {season}), {files} standings file(s). "
            "Rebuilt by dfs week close; nothing here is typed."
        ]
    )

    _add_decisions(b, tables, season)

    # 1. series
    b.section(f"SERIES YOU PLAY, {season}  -  by format")
    series = series_rows(tables, season)
    b.note(TOUGHNESS_NOTE)
    if series.empty:
        b.verdict("No Classic entries logged this season yet.")
    else:
        b.header(
            [
                "Series",
                "Format",
                "Fee",
                "Field",
                "Paid %",
                "Played",
                "ROI",
                "Cash rate",
                "Avg finish",
                "Cash line (avg slate)",
                "Latest cash line",
                "Cash line vs same-format peers",
                "Weeks behind it",
            ]
        )
        first = b.next_row
        for rec in series.itertuples(index=False):
            b.add(
                [
                    rec.series,
                    rec.format,
                    rec.fee,
                    rec.field,
                    rec.paid,
                    rec.played,
                    rec.roi,
                    rec.cash_rate,
                    rec.finish,
                    rec.expected,
                    rec.latest,
                    rec.gap,
                    rec.gap_weeks if rec.gap_weeks else "",
                ]
            )
        last = b.next_row - 1
        lay.count_cells += [_range("D", first, last), _range("F", first, last), _range("M", first, last)]
        lay.money_cells.append(_range("C", first, last))
        lay.percent_cells += [_range(c, first, last) for c in "EGHI"]
        lay.number_cells += [_range("J", first, last), _range("L", first, last)]
        lay.diff_good.append(_range("G", first, last))
        lay.good_high.append(_range("H", first, last))
        lay.good_low.append(_range("I", first, last))
        lay.tough_gap.append(_range("L", first, last))

    # 2. real cash lines
    classic = _classic_profiles(tables.profiles)
    b.section("CASH LINES FROM YOUR STANDINGS FILES  -  the score at the last paid place")
    if classic.empty:
        b.verdict("No standings files yet. Download them from each contest's results page.")
    else:
        b.note(
            "DraftKings only serves a contest's standings for about two weeks: download them each Monday or "
            "Tuesday, they cannot be backfilled."
        )
        b.header(
            [
                "Series",
                "Week",
                "Format",
                "Entrants",
                "Paid %",
                "Cash line",
                "Top 10%",
                "Top 1%",
                "Winner",
                "Median",
            ]
        )
        first = b.next_row
        ordered = classic.assign(
            _f=classic["format"].map({f: i for i, f in enumerate(MAIN_FORMATS)})
        ).sort_values(["season", "week", "_f", "series"], ascending=[False, False, True, True])
        for rec in ordered.itertuples(index=False):
            b.add(
                [
                    rec.series,
                    int(rec.week),
                    rec.format,
                    int(rec.entrants),
                    rec.places_paid / rec.entrants if rec.entrants else _nan(),
                    rec.cash_line,
                    rec.top10,
                    rec.top1,
                    rec.win,
                    rec.median,
                ]
            )
        last = b.next_row - 1
        lay.count_cells += [_range("B", first, last), _range("D", first, last)]
        lay.percent_cells.append(_range("E", first, last))
        lay.number_cells += [_range(c, first, last) for c in "FGHIJ"]

    # 3. how the field builds
    fields = field_by_format(tables)
    b.section("HOW THE FIELD BUILDS  -  averaged over the contests logged, by format")
    if not fields:
        b.verdict("No standings files with lineups yet.")
    else:
        b.note(
            "Each row averages the contests in that format. The top 10% is what won, next to what all did. "
            "Chalk counts how many of the contest's five most-owned players a lineup holds."
        )
        b.header(
            [
                "Format",
                "Contests",
                "QB + 1 catcher",
                "QB + 2 catchers",
                "Bring-back",
                "Salary left",
                "Left $1,000+",
                "FLEX RB",
                "FLEX WR",
                "FLEX TE",
                "Chalk (of 5)",
                "DST with QB",
                "DST vs QB",
                "Duplicates",
            ]
        )
        first = b.next_row
        for fmt in [f for f in MAIN_FORMATS if f in fields]:
            for rec in fields[fmt].itertuples(index=False):
                row = b.add(
                    [
                        f"{fmt}  -  {rec.label}",
                        rec.contests,
                        rec.qb_stack1,
                        rec.qb_stack2,
                        rec.bringback,
                        rec.avg_unspent,
                        rec.share_unspent_1k,
                        rec.flex_rb,
                        rec.flex_wr,
                        rec.flex_te,
                        rec.avg_chalk,
                        rec.dst_with_qb,
                        rec.dst_vs_qb,
                        rec.dup_share,
                    ]
                )
                if rec.label == "you":
                    lay.verdict_rows.append(row)
        last = b.next_row - 1
        lay.count_cells += [_range("B", first, last), _range("F", first, last)]
        lay.percent_cells += [_range(c, first, last) for c in "CDEGHIJLMN"]
        lay.number_cells.append(_range("K", first, last))

    _add_winners(b, tables.winners)

    # 4. head to head
    b.section("HEAD TO HEAD")
    if tables.h2h_bands.empty:
        b.verdict("No head-to-head entries yet.")
    else:
        b.header(["Your score", "Entries", "Wins", "Losses", "Ties", "Win rate"])
        first = b.next_row
        for rec in tables.h2h_bands.itertuples(index=False):
            b.add([rec.band, rec.n, rec.wins, rec.losses, rec.ties, rec.win_rate])
        last = b.next_row - 1
        lay.count_cells.append(_range("B", first, last))
        lay.count_cells.append(_range("C", first, last))
        lay.count_cells.append(_range("D", first, last))
        lay.count_cells.append(_range("E", first, last))
        lay.percent_cells.append(_range("F", first, last))
        lay.good_high.append(_range("F", first, last))
        if len(tables.h2h_opponents):
            b.blank()
            b.header(["Opponent (2+ meetings)", "Meetings", "Wins", "Losses", "Ties", "Net"])
            first = b.next_row
            for rec in tables.h2h_opponents.head(OPPONENTS_SHOWN).itertuples(index=False):
                b.add([rec.opponent, rec.meetings, rec.wins, rec.losses, rec.ties, rec.net])
            last = b.next_row - 1
            lay.count_cells += [_range(c, first, last) for c in "BCDE"]
            lay.money_cells.append(_range("F", first, last))
            lay.diff_good.append(_range("F", first, last))
        b.note(H2H_USE)

    # 5. left out
    left = left_out_rows(tables, season)
    b.section("LEFT OUT OF THE ROI ABOVE  -  freerolls, Showdown, private and unrecognised contests")
    if left.empty:
        b.verdict("None logged.")
    else:
        b.header(
            [
                "Format",
                "Entries this season",
                "Net this season",
                "Entries, all seasons",
                "Net, all seasons",
                "Cash rate",
            ]
        )
        first = b.next_row
        for rec in left.itertuples(index=False):
            b.add(
                [rec.format, rec.season_entries, rec.season_net, rec.all_entries, rec.all_net, rec.cash_rate]
            )
        last = b.next_row - 1
        lay.count_cells += [_range("B", first, last), _range("D", first, last)]
        lay.money_cells += [_range("C", first, last), _range("E", first, last)]
        lay.diff_good += [_range("C", first, last), _range("E", first, last)]
        lay.percent_cells.append(_range("F", first, last))
    return lay


# ---- writing --------------------------------------------------------------------------------------


def write_contests(client: SheetsClient, layout: Layout, tab: str = CONTESTS_TAB) -> str:
    """Write the rows, then style them (the tab is rebuilt each time, so its format is reset first)."""
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
        client.format_range(
            tab,
            f"A{row}:{LAST_COLUMN}{row}",
            {**_HEADER_FMT, "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"},
        )
        client.format_range(tab, f"A{row}:{LAST_COLUMN}{row}", {"horizontalAlignment": "RIGHT"})
        for index, name in enumerate(layout.rows[row - 1]):
            if str(name).strip() in TEXT_HEADERS:
                client.format_range(tab, f"{chr(ord('A') + index)}{row}", {"horizontalAlignment": "LEFT"})
    for row in layout.verdict_rows:
        client.format_range(tab, f"A{row}", {"textFormat": {"bold": True, "fontSize": 10}})
    for row in layout.note_rows:
        client.format_range(
            tab, f"A{row}", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for rng in layout.percent_cells:
        client.format_range(tab, rng, {**_num("0%", "PERCENT"), "horizontalAlignment": "RIGHT"})
    for rng in layout.count_cells:
        client.format_range(tab, rng, {**_num("#,##0"), "horizontalAlignment": "RIGHT"})
    for rng in layout.number_cells:
        client.format_range(tab, rng, {**_num("0.0"), "horizontalAlignment": "RIGHT"})
    for rng in layout.money_cells:
        client.format_range(
            tab, rng, {**_num('"$"#,##0.00;[Red]-"$"#,##0.00'), "horizontalAlignment": "RIGHT"}
        )
    for rng in layout.good_high:
        client.add_color_scale(tab, rng, min_color=WHITE, mid_color=WHITE, max_color=GRAD_MAX)
    for rng in layout.good_low:
        client.add_color_scale(tab, rng, min_color=GRAD_MAX, mid_color=WHITE, max_color=WHITE)
    for rng in layout.diff_good:
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
    for rng in layout.tough_gap:
        client.add_color_scale(
            tab,
            rng,
            min_color=GRAD_MAX,
            mid_color=WHITE,
            max_color=GRAD_MIN,
            mid_type="NUMBER",
            mid_value="0",
            **diverging_anchor_kwargs(rng),
        )
    client.freeze(tab, rows=layout.status_row, cols=0)
    notes = sum(
        apply_notes_to_values(client, tab, row, _note_header(layout.rows[row - 1]), CONTESTS_NOTES)
        for row in layout.header_rows
    )
    fit_tab_widths(client, tab)
    return f"{tab}: built ({len(layout.rows)} rows, {notes} header note(s))"
