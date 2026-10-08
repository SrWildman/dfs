"""The `Edge Finder` tab and the Board's "This week's edges" panel: pure layout from the sync's saved tables.

Nothing here reads a sheet or the network. `load_inputs` reads what `edge_finder.enrich` saved under
`data/current/edge_finder/` plus `data/current/edge.csv`; `build_layout` turns them into rows and formatting
instructions; `sheet_edge_finder_writer.write_tab` writes them. Python writes the tab on every sync (it is a
view of the sync, not a formula tab), and every conditional format is relative to its own row, so colours
and muting survive a sort or a filter.

**Columns (fixed across sections, A..N).** `A` Name, `B` Pos, `C` Team, `D` Salary, `E..J` section-specific,
`K` pool marker (a formula: EdgeRaw's own Pool tick for that name), `L` the `Edge ↗` link (a formula),
`M` games in his window (rows under `MUTED_BELOW_GAMES` are drawn muted), `N` notes / reason (overflows
right). Matchup rows use `A` Position, `B` Team, `C` Opp, `D` Score, `N` reasons and players.

**Sections, top to bottom:** status line; cash core per position (top `SECTION_TOP_N` by `Hit3x%` among
players whose `CalPts` is at least the position's median); GPP upside per position (top `SECTION_TOP_N` by
`Boom%`, a star when `Boom%` is in the top quartile AND `Own%` in the bottom half, once ownership has
published); projection disagreements (largest |CalPts - ProjPts| per position, both directions, with the
reason); injury beneficiaries (carries only: confirmed out first, then questionable, muted, then every absent
regular as context); matchups (context only: top 8, bottom 4, with reasons); context signals (unproven,
muted). A section is capped, with "+N more" in its heading.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import calibration
from dfs.derived import _rosterable_pool_mask
from dfs.edge_finder import (
    ABSENCES_FILE,
    BENEFICIARIES_FILE,
    MATCHUPS_FILE,
    OUTPUT_DIR,
    PLAYERS_FILE,
    STATUS_FILE,
)
from dfs.paths import CURRENT_DIR

EDGE_FINDER_TAB = "Edge Finder"
SECTION_TOP_N = 5
DISAGREEMENT_PER_DIRECTION = 2
BENEFICIARY_CONFIRMED_N = 12
BENEFICIARY_QUESTIONABLE_N = 6
SIGNALS_PER_TOKEN = 6
MUTED_BELOW_GAMES = 3
CASH_CORE_MIN_CALPTS_PCT = 50.0
BOOM_STAR_TOP_QUARTILE = 0.75
OWN_STAR_BOTTOM_HALF = 0.5
POSITIONS = ["QB", "RB", "WR", "TE", "DST"]
OUT_STATUSES = {"OUT", "IR", "D"}
COLUMN_COUNT = 14  # A..N
LAST_COLUMN = "N"
POOL_COL, LINK_COL, GAMES_COL, NOTE_COL = "K", "L", "M", "N"
STAR = "★"

PLAYER_COLUMNS = ["Name", "Pos", "Team", "Salary"]
CASH_COLUMNS = [*PLAYER_COLUMNS, "CalPts", "ProjPts", "Hit3x%", "Bust%", "Edge"]
GPP_COLUMNS = [*PLAYER_COLUMNS, "CalPts", "Boom%", "CeilM", "Own%", "Edge", "★"]
DISAGREE_COLUMNS = [*PLAYER_COLUMNS, "ProjPts", "CalPts", "Diff"]
BENEFICIARY_COLUMNS = [
    *PLAYER_COLUMNS,
    "Gain Car/G",
    "Gain xFP/G",
    "Method",
    "Priced in?",
    "Edge",
]
MATCHUP_COLUMNS = ["Position", "Team", "Opp", "Score", "Group"]
ABSENCE_COLUMNS = [*PLAYER_COLUMNS, "Role", "Tgt/G", "Car/G", "Games missed"]
SIGNAL_COLUMNS = [*PLAYER_COLUMNS, "Token", "xFP/G", "DK/G L3"]
TRAILING = {"K": "Pool", "L": "↗", "M": "Games", "N": "Notes"}


@dataclass
class Inputs:
    """What the tab is built from. `edge` is the enriched edge frame (`data/current/edge.csv`); `players` the
    signals table (`Games`, `DkG`, `GsisId`, ...); the rest are the sync's saved tables."""

    edge: pd.DataFrame
    players: pd.DataFrame
    beneficiaries: pd.DataFrame
    matchups: pd.DataFrame
    status: dict
    absences: pd.DataFrame = field(default_factory=pd.DataFrame)
    calibration: calibration.Calibration | None = None


@dataclass
class Layout:
    """Rows (1-based numbers resolved) plus what the writer must format."""

    rows: list[list]
    title_row: int = 1
    section_rows: list[int] = field(default_factory=list)
    subheader_rows: list[int] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)
    muted_rows: list[int] = field(default_factory=list)  # static muting (questionable, unproven)
    player_ranges: list[tuple[int, int]] = field(default_factory=list)  # (first, last) rows with a player
    pool_rows: list[int] = field(default_factory=list)  # rows whose K/L cells are formulas
    status_rows: list[int] = field(default_factory=list)
    percent_cells: list[str] = field(default_factory=list)
    money_cells: list[str] = field(default_factory=list)
    point_cells: list[str] = field(default_factory=list)
    chip_ranges: list[str] = field(default_factory=list)  # `Edge` / token cells
    prob_ranges: dict[str, list[str]] = field(
        default_factory=dict
    )  # header -> A1 ranges (Hit3x%, Boom%, Bust%)


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def load_inputs(*, scored: pd.DataFrame | None = None, season: int | None = None) -> Inputs | None:
    """The saved sync outputs, or None when no sync has produced them yet. `scored` (every scored table) lets
    the disagreement reasons quote the calibration's own biases."""
    edge = _read(CURRENT_DIR / "edge.csv")
    players = _read(OUTPUT_DIR / PLAYERS_FILE)
    if edge.empty or players.empty:
        return None
    status = {}
    status_path = OUTPUT_DIR / STATUS_FILE
    if status_path.exists():
        status = json.loads(status_path.read_text())
    fitted = None
    if scored is not None and not scored.empty and status.get("week"):
        fitted = calibration.fit(
            scored, before_week=int(status["week"]), season=season or status.get("season")
        )
    return Inputs(
        edge=edge,
        players=players,
        beneficiaries=_read(OUTPUT_DIR / BENEFICIARIES_FILE),
        matchups=_read(OUTPUT_DIR / MATCHUPS_FILE),
        absences=_read(OUTPUT_DIR / ABSENCES_FILE),
        status=status,
        calibration=fitted,
    )


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _clean(value):
    if isinstance(value, float) and np.isnan(value):
        return ""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _pad(values: list) -> list:
    return [*[_clean(v) for v in values], *[""] * (COLUMN_COUNT - len(values))]


def available(edge: pd.DataFrame) -> pd.Series:
    """True where DraftKings does not list him OUT, IR or doubtful."""
    return ~edge["Avail"].fillna("").astype(str).str.upper().isin(OUT_STATUSES)


def rosterable(edge: pd.DataFrame) -> pd.Series:
    return _rosterable_pool_mask(pd.to_numeric(edge["ProjPts"], errors="coerce"), edge["Position"])


def with_games(edge: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """The edge frame with `Games`, `DkG`, `TdExcess` and `UsageN` from the signals table, by `Id`."""
    cols = [c for c in ("Id", "Games", "DkG", "TdExcess", "UsageN", "GsisId") if c in players.columns]
    return edge.merge(players[cols].drop_duplicates("Id"), on="Id", how="left")


class _Builder:
    def __init__(self) -> None:
        self.layout = Layout(rows=[])

    @property
    def next_row(self) -> int:
        return len(self.layout.rows) + 1

    def add(self, values: list) -> int:
        self.layout.rows.append(_pad(values))
        return len(self.layout.rows)

    def blank(self) -> None:
        self.add([])

    def section(self, title: str) -> None:
        self.blank()
        self.layout.section_rows.append(self.add([title]))

    def sub(self, title: str) -> None:
        self.layout.subheader_rows.append(self.add([title]))

    def note(self, text: str) -> None:
        self.layout.note_rows.append(self.add([text]))

    def header(self, names: list[str]) -> int:
        row = self.add(self._with_trailing(names))
        self.layout.header_rows.append(row)
        return row

    @staticmethod
    def _with_trailing(names: list[str]) -> list:
        values = [*names, *[""] * (10 - len(names))]  # A..J
        return [*values, TRAILING["K"], TRAILING["L"], TRAILING["M"], TRAILING["N"]]

    def player_row(self, values: list, *, games, note: str = "", muted: bool = False) -> int:
        """A player row: `values` fill A..J; K/L get the pool and link formulas (written later, they need the
        EdgeRaw gid); M the games in his window; N a note."""
        row = self.add([*values, *[""] * (10 - len(values)), "", "", games, note])
        self.layout.pool_rows.append(row)
        self.layout.player_ranges.append((row, row))
        if muted:
            self.layout.muted_rows.append(row)
        return row


def on_slate(beneficiaries: pd.DataFrame, by_gsis: pd.DataFrame) -> pd.DataFrame:
    """Only beneficiaries DraftKings lists this week (a back with no salary is no option)."""
    if beneficiaries is None or beneficiaries.empty or by_gsis.empty:
        return beneficiaries.iloc[0:0] if beneficiaries is not None else pd.DataFrame()
    return beneficiaries[beneficiaries["GsisId"].isin(by_gsis.index)]


def _games(value) -> object:
    return "" if pd.isna(value) else int(value)


# ---------------------------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------------------------


def _status_lines(inputs: Inputs) -> list[str]:
    s = inputs.status
    weeks = s.get("calpts_weeks") or []
    trained = f"Weeks {min(weeks)}–{max(weeks)}" if weeks else "no earlier weeks (CalPts = AggPts)"
    sources = ", ".join(s.get("calpts_sources", []))
    snap = str(s.get("projection_snapshot") or "")
    snap_text = (
        f"{snap[:4]}-{snap[4:6]}-{snap[6:8]} {snap[9:11]}:{snap[11:13]} UTC" if len(snap) >= 13 else "unknown"
    )
    return [
        f"Week {s.get('week', '?')} · stats through Week {s.get('stats_through_week', '?')} · "
        f"injury report for Week {s.get('injury_report_week', '?')} · "
        f"depth chart {s.get('depth_chart_dt') or 'unknown'}",
        _injury_report_line(s.get("injury_report")),
        f"Projection snapshot {snap_text} · CalPts trained on {trained} ({sources})",
        "Final `dfs sync --live` after inactives (~90 min before kickoff). Context columns are not proven to "
        "beat projections; Model Check tracks every one.",
    ]


def _injury_report_line(info: dict | None) -> str:
    """Where the injury statuses came from and how complete that source was when fetched. A report with no
    final statuses yet is a timing fact (teams publish them Friday), not a fault."""
    if not info:
        return "Injury report: not recorded for this sync (statuses come from DraftKings' Avail)."
    fetched = str(info.get("fetched") or "")
    when = f"{fetched[:10]} {fetched[11:16]} UTC" if len(fetched) >= 16 else "unknown time"
    rows, with_status = int(info.get("rows") or 0), int(info.get("with_status") or 0)
    text = (
        f"Injury report: nflverse injuries release, Week {info.get('week', '?')}: {rows} rows, "
        f"{with_status} with a final status, fetched {when}."
    )
    if with_status == 0:
        text += " Practice reports only so far (final statuses come Friday): outs are DraftKings' Avail."
    return text


def cash_core(edge: pd.DataFrame, position: str) -> pd.DataFrame:
    """Top players at a position by `Hit3x%`, among available players whose `CalPts` is at least the
    position's median (CalPts%ile >= 50)."""
    part = edge[(edge["Position"] == position) & available(edge)]
    part = part[(pd.to_numeric(part["CalPts%ile"], errors="coerce") >= CASH_CORE_MIN_CALPTS_PCT)]
    part = part[pd.to_numeric(part["Hit3x%"], errors="coerce").notna()]
    return part.sort_values("Hit3x%", ascending=False)


def gpp_upside(edge: pd.DataFrame, position: str) -> pd.DataFrame:
    part = edge[(edge["Position"] == position) & available(edge)]
    part = part[pd.to_numeric(part["Boom%"], errors="coerce").notna()]
    return part.sort_values("Boom%", ascending=False)


def disagreements(edge: pd.DataFrame, position: str, direction: str) -> pd.DataFrame:
    """Largest CalPts - ProjPts at a position among rosterable, available players with a TFFB projection:
    `up` = the model is higher than TFFB, `down` = lower."""
    mask = (edge["Position"] == position) & available(edge) & rosterable(edge)
    part = edge[mask].copy()
    part["Diff"] = pd.to_numeric(part["CalPts"], errors="coerce") - pd.to_numeric(
        part["ProjPts"], errors="coerce"
    )
    part = part[part["Diff"].notna() & (pd.to_numeric(part["ProjPts"], errors="coerce") > 0)]
    return part.sort_values("Diff", ascending=(direction == "down"))


def disagreement_reason(row: pd.Series, fitted: calibration.Calibration | None) -> str:
    """Why CalPts differs from ProjPts: the cell's measured TFFB bias, in words ("TFFB runs -3.1 on RBs under
    $4.5k (n=67)"), plus how far the other sources sit from TFFB when they matter."""
    parts = []
    position, salary = row["Position"], row["Salary"]
    if fitted is not None and not fitted.is_empty:
        tier = int(calibration.tier_index(pd.Series([position]), pd.Series([salary])).iloc[0])
        key = ("ProjPts", position, tier)
        n = fitted.counts.get(key, 0)
        if n:
            raw = (
                fitted.bias[key] * (n + calibration.CAL_SHRINK_K) / n
            )  # undo the shrinkage: the raw mean miss
            labels = calibration.tier_labels(position)
            # raw = mean(actual - TFFB): negative = TFFB runs high
            parts.append(f"TFFB runs {raw:+.1f} on {position}s {labels[tier]} (n={n})")
    others = [pd.to_numeric(row.get(c), errors="coerce") for c in ("SleeperPts", "FantasyProsPts")]
    others = [v for v in others if pd.notna(v)]
    if others:
        parts.append(f"other sources avg {np.mean(others) - row['ProjPts']:+.1f} vs TFFB")
    return "; ".join(parts) if parts else "sources disagree with TFFB"


def build_layout(inputs: Inputs | None, *, edge_tab_name: str = "EdgeRaw") -> Layout:
    """The whole tab. `inputs` None gives the empty-state layout (a template, or no sync yet)."""
    b = _Builder()
    b.add(["EDGE FINDER  —  calibrated projections, outcome odds and the signals that matter"])
    if inputs is None:
        b.note("Not synced yet -- run `dfs sync`; this tab is written by the sync and holds nothing typed.")
        for title in (
            "CASH CORE  —  best Hit3x% per position",
            "GPP UPSIDE  —  best Boom% per position",
            "PROJECTION DISAGREEMENTS  —  where CalPts differs most from TFFB",
            "INJURY BENEFICIARIES  —  who inherits a back's carries; absences are context",
            "MATCHUPS (CONTEXT)  —  softest and toughest offenses",
            "CONTEXT SIGNALS  —  unproven",
        ):
            b.section(title)
            b.note("Nothing yet.")
        return b.layout

    edge = with_games(inputs.edge, inputs.players)
    for col in ("SleeperPts", "FantasyProsPts"):
        if col in inputs.players.columns and col not in edge.columns:
            edge = edge.merge(inputs.players[["Id", col]].drop_duplicates("Id"), on="Id", how="left")
    for line in _status_lines(inputs):
        b.layout.status_rows.append(b.add([line]))
    published = (edge.get("OwnStatus", pd.Series(dtype=object)) == "real").any()
    fitted = inputs.calibration

    # ---- Cash core -------------------------------------------------------------------------
    b.section("CASH CORE  —  best Hit3x% per position (CalPts at least the position median)")
    b.note("Hit3x% = chance of 3 x salary per $1,000, the line behind Val >= 3. Bust% = chance of under 2 x.")
    b.header(CASH_COLUMNS)
    for pos in POSITIONS:
        part = cash_core(edge, pos)
        if part.empty:
            continue
        more = max(len(part) - SECTION_TOP_N, 0)
        b.sub(
            f"{pos}  —  top {min(SECTION_TOP_N, len(part))} of {len(part)}"
            + (f" (+{more} more)" if more else "")
        )
        for _, r in part.head(SECTION_TOP_N).iterrows():
            b.player_row(
                [
                    r["Name"],
                    pos,
                    r["Team"],
                    r["Salary"],
                    r["CalPts"],
                    r["ProjPts"],
                    r["Hit3x%"],
                    r["Bust%"],
                    r["Edge"],
                ],
                games=_games(r.get("Games")),
                muted=bool(pd.notna(r.get("Games")) and r["Games"] < MUTED_BELOW_GAMES),
            )

    # ---- GPP upside ------------------------------------------------------------------------
    b.section("GPP UPSIDE  —  best Boom% per position")
    b.note(
        "Boom% = chance of 4 x salary per $1,000. CeilM = the model's 85th percentile. "
        f"{STAR} = Boom% in the position's top quartile and Own% in the bottom half"
        + ("." if published else " (ownership has not published yet, so no stars).")
    )
    b.header(GPP_COLUMNS)
    for pos in POSITIONS:
        part = gpp_upside(edge, pos)
        if part.empty:
            continue
        more = max(len(part) - SECTION_TOP_N, 0)
        boom_cut = pd.to_numeric(part["Boom%"], errors="coerce").quantile(BOOM_STAR_TOP_QUARTILE)
        own = pd.to_numeric(part["Own%"], errors="coerce")
        own_cut = own[own > 0].quantile(OWN_STAR_BOTTOM_HALF) if (own > 0).any() else np.nan
        b.sub(
            f"{pos}  —  top {min(SECTION_TOP_N, len(part))} of {len(part)}"
            + (f" (+{more} more)" if more else "")
        )
        for _, r in part.head(SECTION_TOP_N).iterrows():
            star = (
                STAR
                if published
                and r["Boom%"] >= boom_cut
                and pd.notna(r["Own%"])
                and pd.notna(own_cut)
                and r["Own%"] <= own_cut
                else ""
            )
            own_value = r["Own%"] if published and r["Own%"] and r["Own%"] > 0 else ""
            b.player_row(
                [
                    r["Name"],
                    pos,
                    r["Team"],
                    r["Salary"],
                    r["CalPts"],
                    r["Boom%"],
                    r["CeilM"],
                    own_value,
                    r["Edge"],
                    star,
                ],
                games=_games(r.get("Games")),
                muted=bool(pd.notna(r.get("Games")) and r["Games"] < MUTED_BELOW_GAMES),
            )

    # ---- Disagreements ---------------------------------------------------------------------
    b.section(
        "PROJECTION DISAGREEMENTS  —  where CalPts differs most from TFFB "
        "(the field mostly uses raw projections)"
    )
    b.note(
        "Diff = CalPts minus ProjPts. Rosterable players only. "
        "The reason is the measured TFFB bias for his position and salary."
    )
    b.header(DISAGREE_COLUMNS)
    for pos in POSITIONS:
        for direction, label in (("up", "CalPts above TFFB"), ("down", "CalPts below TFFB")):
            part = disagreements(edge, pos, direction).head(DISAGREEMENT_PER_DIRECTION)
            if part.empty:
                continue
            b.sub(f"{pos}  —  {label}")
            for _, r in part.iterrows():
                b.player_row(
                    [r["Name"], pos, r["Team"], r["Salary"], r["ProjPts"], r["CalPts"], round(r["Diff"], 1)],
                    games=_games(r.get("Games")),
                    note=disagreement_reason(r, fitted),
                    muted=bool(pd.notna(r.get("Games")) and r["Games"] < MUTED_BELOW_GAMES),
                )

    # ---- Injury beneficiaries ----------------------------------------------------------------
    b.section("INJURY BENEFICIARIES  —  who inherits a back's carries (confirmed first, questionable muted)")
    b.note(
        "CARRIES ONLY. When a regular back is out, the measured split of his carries (12 seasons) goes to "
        "the backs behind him and the share that historically goes to nobody stays unassigned; a "
        "with-or-without split (2+ missed games) replaces it. INJ+ marks a confirmed beneficiary worth at "
        "least 1 expected point a game. Priced in? is blank until a projection snapshot from before the "
        "out designation exists."
    )
    b.header(BENEFICIARY_COLUMNS)
    by_gsis = (
        edge.drop_duplicates("GsisId").set_index("GsisId") if "GsisId" in edge.columns else pd.DataFrame()
    )
    ben = on_slate(
        inputs.beneficiaries, by_gsis
    )  # only backs DraftKings lists: a back with no salary is no option
    if ben.empty:
        b.note("No regular back is out or questionable with a beneficiary this week.")
    else:
        for status, label, cap, muted in (
            ("out", "Confirmed out (DraftKings or the injury report)", BENEFICIARY_CONFIRMED_N, False),
            ("questionable", "Questionable (assumed to play: muted)", BENEFICIARY_QUESTIONABLE_N, True),
        ):
            part = ben[ben["OutStatus"] == status].head(cap)
            total = int((ben["OutStatus"] == status).sum())
            if part.empty:
                continue
            more = max(total - cap, 0)
            b.sub(f"{label}  —  {len(part)} of {total}" + (f" (+{more} more)" if more else ""))
            for _, r in part.iterrows():
                info = by_gsis.loc[r["GsisId"]] if r["GsisId"] in by_gsis.index else None
                salary = info["Salary"] if info is not None else ""
                edge_text = info["Edge"] if info is not None else ""
                games = _games(info["Games"]) if info is not None and "Games" in info else ""
                b.player_row(
                    [
                        r["Name"],
                        r["Position"],
                        r["Team"],
                        salary,
                        round(r["car_gain"], 1),
                        round(r["xfp_gain"], 1),
                        r["Method"] + (f" ({int(r['n'])} g)" if r["Method"] == "with-or-without" else ""),
                        r.get("PricedIn", ""),
                        edge_text,
                    ],
                    games=games,
                    note=f"out: {r['OutPlayers']}",
                    muted=muted,
                )
    # Every confirmed absence of a regular, as context. No points are attached, for any position.
    absent = inputs.absences
    b.sub("Absent regulars  —  context, not an edge (no points are moved for targets)")
    b.note(
        "Out of sample, handing a missing receiver's targets to the next man up predicted WORSE than doing "
        "nothing, so no target gain is computed and INJ+ never fires from one. Historical split and the "
        "with-or-without split (2+ missed games, display only for targets) on the right."
    )
    b.header(ABSENCE_COLUMNS)
    if absent is None or absent.empty:
        b.note("No regular is confirmed out this week.")
    else:
        for _, r in absent.iterrows():
            info = by_gsis.loc[r["GsisId"]] if r["GsisId"] in by_gsis.index else None
            salary = info["Salary"] if info is not None else ""
            texts = [str(t) for t in (r.get("WithWithout"), r.get("History")) if isinstance(t, str) and t]
            b.player_row(
                [
                    r["Name"],
                    r["Position"],
                    r["Team"],
                    salary,
                    r["Role"],
                    round(r["tgt_g"], 1),
                    round(r["car_g"], 1),
                    int(r["GamesMissed"]),
                ],
                games="",
                note=f"{r.get('Regular', '')}  |  " + "  |  ".join(texts),
                muted=True,
            )

    # ---- Matchups -----------------------------------------------------------------------------
    b.section("MATCHUPS (CONTEXT)  —  the 8 best and 4 toughest offenses (DST: the defense's spot)")
    b.note(
        "CONTEXT ONLY: nothing here feeds CalPts or any flag (research: matchups add under 0.04 points of "
        "error beyond the projection). Score = mean z of the opponent's adjusted points allowed, EPA, "
        "implied total, pace and PROE. Reasons and top-2 players by CalPts on the right."
    )
    b.header(MATCHUP_COLUMNS)
    mu = inputs.matchups
    if mu.empty:
        b.note("No matchup tables yet (the schedule or last season's results were unavailable).")
    else:
        for pos in POSITIONS:
            part = mu[mu["Position"] == pos]
            if part.empty:
                continue
            b.sub(pos)
            for _, r in part.iterrows():
                row = b.add(
                    [
                        pos,
                        r["Team"],
                        r["Opp"],
                        round(r["Score"], 2),
                        r["Group"],
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        f"{r['Reasons']}  |  {r['Players']}",
                    ]
                )
                if r["Group"] == "bottom":
                    b.layout.muted_rows.append(row)

    # ---- Context signals ------------------------------------------------------------------------
    b.section("CONTEXT SIGNALS  —  unproven; tracked in Model Check")
    b.note(
        "FADE↓ (TEs only): last-3 DK points per game at least 2.0 above expected. USAGE↑ / USAGE↓ (RBs "
        "only): carry share of the last 2 games up 10+ points / down 5+ points against the 6 before. "
        "Muted on purpose."
    )
    b.header(SIGNAL_COLUMNS)
    tokens = ["FADE↓", "USAGE↑", "USAGE↓"]
    pool = rosterable(edge)
    for token in tokens:
        has = edge["Edge"].fillna("").astype(str).str.split().map(lambda t, k=token: k in t)
        part = edge[has & pool & available(edge)].sort_values("CalPts", ascending=False)
        if part.empty:
            continue
        more = max(len(part) - SIGNALS_PER_TOKEN, 0)
        b.sub(
            f"{token}  —  {min(len(part), SIGNALS_PER_TOKEN)} of {len(part)}"
            + (f" (+{more} more)" if more else "")
        )
        for _, r in part.head(SIGNALS_PER_TOKEN).iterrows():
            b.player_row(
                [
                    r["Name"],
                    r["Position"],
                    r["Team"],
                    r["Salary"],
                    token,
                    r["xFP/G"],
                    r.get("DkG", ""),
                ],
                games=_games(r.get("Games")),
                note="context",
                muted=True,
            )
    _mark_formats(b.layout)
    return b.layout


def _mark_formats(layout: Layout) -> None:
    """Number-format and chip ranges, derived from the header rows (every section's columns by NAME)."""
    for header_row in layout.header_rows:
        names = layout.rows[header_row - 1]
        # the block of rows under this header, until the next section/blank row
        last = header_row
        while (
            last < len(layout.rows)
            and any(c != "" for c in layout.rows[last])
            and (last + 1) not in layout.section_rows
        ):
            last += 1
        first = header_row + 1
        for index, name in enumerate(names):
            letter = chr(ord("A") + index)
            rng = f"{letter}{first}:{letter}{last}"
            if name == "Salary":
                layout.money_cells.append(rng)
            elif name in (
                "CalPts",
                "ProjPts",
                "CeilM",
                "Diff",
                "xFP/G",
                "Tgt/G",
                "Car/G",
                "Gain Car/G",
                "Gain xFP/G",
                "DK/G L3",
                "Score",
            ):
                layout.point_cells.append(rng)
            elif name in ("Hit3x%", "Boom%", "Bust%"):
                layout.prob_ranges.setdefault(name, []).append(rng)
                layout.percent_cells.append(rng)
            elif name == "Own%":
                layout.percent_cells.append(rng)
            elif name in ("Edge", "Token"):
                layout.chip_ranges.append(rng)


# ---------------------------------------------------------------------------------------------
# Board panel: "This week's edges", one line each
# ---------------------------------------------------------------------------------------------

BOARD_PANEL_LINES = 5
BOARD_TOP_CASH, BOARD_TOP_GPP, BOARD_TOP_DISAGREE, BOARD_TOP_INJURY = 3, 3, 3, 2


def _name_with(row: pd.Series, text: str) -> str:
    return f"{row['Name']} ({row['Position']}, {text})"


def board_panel_lines(inputs: Inputs | None) -> list[str]:
    """Five one-line summaries: top 3 cash core, top 3 GPP upside, top 3 disagreements, top 2 injury
    beneficiaries, best offense per position. A line with nothing to say says so."""
    if inputs is None:
        return ["Not synced yet -- run `dfs sync`."] + [""] * (BOARD_PANEL_LINES - 1)
    edge = with_games(inputs.edge, inputs.players)
    cash = pd.concat([cash_core(edge, p).head(BOARD_TOP_CASH) for p in POSITIONS if p != "DST"])
    cash = cash.sort_values("Hit3x%", ascending=False).head(BOARD_TOP_CASH)
    gpp = pd.concat([gpp_upside(edge, p).head(BOARD_TOP_GPP) for p in POSITIONS if p != "DST"])
    gpp = gpp.sort_values("Boom%", ascending=False).head(BOARD_TOP_GPP)
    diffs = pd.concat(
        [disagreements(edge, p, d).head(BOARD_TOP_DISAGREE) for p in POSITIONS for d in ("up", "down")]
    )
    diffs = (
        diffs.reindex(diffs["Diff"].abs().sort_values(ascending=False).index).head(BOARD_TOP_DISAGREE)
        if not diffs.empty
        else diffs
    )
    lines = [
        "Cash core: "
        + (" · ".join(_name_with(r, f"Hit3x {r['Hit3x%']:.0f}%") for _, r in cash.iterrows()) or "none"),
        "GPP upside: "
        + (" · ".join(_name_with(r, f"Boom {r['Boom%']:.0f}%") for _, r in gpp.iterrows()) or "none"),
        "Biggest disagreements with TFFB: "
        + (" · ".join(_name_with(r, f"CalPts {r['Diff']:+.1f}") for _, r in diffs.iterrows()) or "none"),
    ]
    by_gsis = (
        edge.drop_duplicates("GsisId").set_index("GsisId") if "GsisId" in edge.columns else pd.DataFrame()
    )
    ben = on_slate(inputs.beneficiaries, by_gsis)
    confirmed = ben[ben["OutStatus"] == "out"].head(BOARD_TOP_INJURY) if not ben.empty else ben
    lines.append(
        "Injury beneficiaries (carries): "
        + (
            " · ".join(
                f"{r['Name']} ({r['Position']}, +{r['xfp_gain']:.1f} xFP/G"
                + (
                    f", priced in: {r['PricedIn']}"
                    if isinstance(r.get("PricedIn"), str) and r["PricedIn"]
                    else ""
                )
                + ")"
                for _, r in confirmed.iterrows()
            )
            or "none"
        )
    )
    mu = inputs.matchups
    best = []
    if not mu.empty:
        for pos in POSITIONS:
            part = mu[(mu["Position"] == pos) & (mu["Group"] == "top")]
            if not part.empty:
                top = part.sort_values("Score", ascending=False).iloc[0]
                best.append(f"{pos} {top['Team']} vs {top['Opp']}")
    lines.append("Best offense per position: " + (" · ".join(best) or "none"))
    return lines
