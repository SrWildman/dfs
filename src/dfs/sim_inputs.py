"""Everything between the sheet and the lineup simulator (`dfs.sim`): players, the cash line, swap maths.

Pure and offline-testable (dataframes and lists in, values out); `sheet_lineup_sim.py` reads and writes the
sheet and `cli.py` wires the two together. Nothing here touches a sheet or the network.

**Players.** `build_specs` turns the synced EdgeRaw frame (`data/current/edge.csv`) into one `PlayerSpec` per
player, keyed by the name Sam types on Lineups:

- *projection*: `CalPts` where present, else TFFB's `ProjPts` (the sheet's default projection is untouched);
- *game, team, opponent*: EdgeRaw's `GameID`, `Team`, `Opp` (kickoff times stay with `kickoff.py`; the
  simulator does not condition on games already played, so it needs none);
- *role* (QB1, RB1-RB3, WR1-WR4, TE1-TE2, DST): a quarterback is QB1, a defense is DST; backs, receivers and
  tight ends are ranked within their team and position by the depth chart's `pos_rank` (ties, and players
  the chart lacks, by current usage `Tgt% + Rush%`, the tiebreak `injury_beneficiaries.depth_order` uses),
  skipping anyone listed OUT or IR; ranks past the last named role fold into it.

**Cash line.** The median of every typed `Cash Line` in Results this season, all the weeks before the slate's
week (column found by header name; read only). Results' Cash columns are cash contests by construction (`Cash
Pts`, `Cash Line`, `Cash Results`), so no GPP value can be in it. It reads "cash line 141.3 (season median, 4
weeks)" wherever it is shown (`CashLine.basis`). With no typed line yet the simulator's placeholder (145) is
used and says so.

**Lines.** `resolve_lines` decides what a lineup is scored against. Normally the lines move with the slate
(`dfs.sim.field`): Cash % is P(lineup >= field mean + the double-up offset) and Top 1% is P(lineup >= field
mean + the top-1% offset), the field mean being the ownership-weighted sum of the draw's points
(`[sim.lines]`). Before ownership publishes (EdgeRaw's `Own%` is blank) there is no field, and it falls back
to the FIXED lines (`[sim.lines] fallback_cash_line` / `fallback_top1_line`, left unset the old numbers: the
cash line above and `[sim] gpp_target`) and says so; `--fixed-lines` asks for the fixed lines on purpose, for
one season, so the old and new numbers can be compared.

**Swaps.** `swap_deltas` simulates a lineup and every candidate swap of it in ONE run, so players the versions
share have identical draws and the difference in P(cash) / P(GPP) is the swap's own effect (this is
`dfs.sim.simulate.swap_impact`, generalised from one player to the 2-for-2 swaps and full re-fills that late
swap finds).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.player_join import normalize_team
from dfs.sim.field import FieldModel, build_field, has_ownership
from dfs.sim.roles import ROLE_NAMES
from dfs.sim.simulate import DEFAULT_CASH_LINE, PlayerSpec, simulate_lineups

N_SIMS = 20000  # the sheet's numbers must not jitter between syncs: a fixed number of draws ...
SEED = 0  # ... and a fixed seed, so the same lineups always read the same
NOT_PLAYING = frozenset({"OUT", "IR"})
USAGE_COLUMNS = ("Tgt%", "Rush%")
NO_CHART_RANK = 999
SIM_POSITIONS = (*ROLE_NAMES, "DST")


@dataclass(frozen=True)
class CashLine:
    """The cash line the simulator uses, with where it came from (shown in the sync's summary line)."""

    value: float
    weeks: tuple[int, ...]  # the Results weeks whose typed values it is the median of; () for the placeholder
    note: str

    @property
    def is_default(self) -> bool:
        return not self.weeks

    @property
    def basis(self) -> str:
        """How the number was reached, for a header note or the Board's portfolio line."""
        if self.is_default:
            return f"cash line {self.value:g} (placeholder: no typed Cash Line yet)"
        count = len(self.weeks)
        return f"cash line {self.value:.1f} (season median, {count} week{'s' if count != 1 else ''})"


def _number(cell: object) -> float | None:
    text = str(cell).replace("$", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def cash_line_from_results(header: list, rows: list[list], *, before_week: int) -> CashLine:
    """The median of every typed `Cash Line` value this season strictly BEFORE `before_week`.

    `header` is Results' header row and `rows` its data rows; the `Week` and `Cash Line` columns are found by
    header name. A week with no typed line (or a non-number) is skipped, not counted as zero; with none at
    all the placeholder `DEFAULT_CASH_LINE` is returned and flagged."""
    names = [str(h).strip() for h in header]
    if "Week" not in names or "Cash Line" not in names:
        return CashLine(
            DEFAULT_CASH_LINE, (), "Results has no Week / Cash Line columns: using the placeholder"
        )
    week_col, line_col = names.index("Week"), names.index("Cash Line")
    typed: dict[int, float] = {}
    for row in rows:
        week = _number(row[week_col]) if week_col < len(row) else None
        line = _number(row[line_col]) if line_col < len(row) else None
        if week is not None and line is not None and week < before_week:
            typed[int(week)] = line
    if not typed:
        return CashLine(
            DEFAULT_CASH_LINE, (), f"no typed Cash Line before Week {before_week}: using the placeholder"
        )
    weeks = tuple(sorted(typed))
    value = round(statistics.median(typed[w] for w in weeks), 2)
    return CashLine(
        value, weeks, f"season median of your typed Cash Line, Weeks {', '.join(str(w) for w in weeks)}"
    )


@dataclass(frozen=True)
class SimLines:
    """What the simulator scores lineups against, and how to say so.

    `mode` is "relative" (against the field mean: `field` is set and the two fixed numbers are unused),
    "fixed" (`--fixed-lines`) or "fallback" (ownership is blank, so the fixed numbers stand in). `cash_line`
    and `gpp_target` are the fixed numbers (what "fixed" and "fallback" use)."""

    mode: str
    cash_line: float
    gpp_target: float
    field: FieldModel | None
    note: str  # one line for the terminal / the sync summary
    cash_basis: str  # how the cash line was reached, for a hover note or the Board's portfolio line
    top_label: str  # what the top-end number is called in a sentence: "Top 1%" or "190+"

    @property
    def relative(self) -> bool:
        return self.field is not None

    @property
    def status(self) -> str:
        """The line on the Suggested tab: what the Cash % and Top 1% numbers were measured against."""
        if self.field is None:
            return f"cash line {self.cash_line:g}  ·  GPP target {self.gpp_target:g}  ·  {self.mode} lines"
        return (
            f"lines move with the slate: Cash % = field average +{self.field.cash_offset:g}, "
            f"Top 1% = field average +{self.field.top_offset:g}"
        )


def ownership_by_name(edge: pd.DataFrame) -> dict[str, float]:
    """`{Name: Own%}` from the synced EdgeRaw frame (fractions: 0.146 is 14.6%); first row wins for a
    repeated name. Blank / non-numeric cells are left out. Empty when the column is missing or blank (Own% is
    blank until TFFB publishes ownership)."""
    if "Own%" not in edge.columns or "Name" not in edge.columns:
        return {}
    own: dict[str, float] = {}
    for name, value in zip(edge["Name"], pd.to_numeric(edge["Own%"], errors="coerce"), strict=True):
        key = str(name or "").strip()
        if key and key not in own and pd.notna(value):
            own[key] = float(value)
    return own


def resolve_lines(
    edge: pd.DataFrame,
    specs: dict[str, PlayerSpec],
    cash: CashLine,
    gpp_target: float,
    lines,  # noqa: ANN001 - dfs.config.SimLinesConfig
    *,
    fixed_lines: bool = False,
) -> SimLines:
    """The lines to simulate against: relative to the field when ownership is published, else fixed (see the
    module docstring). `specs` must hold every player the field should include (build them for the whole
    slate, not just the lineups' teams)."""
    if fixed_lines:
        return SimLines(
            "fixed",
            cash.value,
            gpp_target,
            None,
            f"FIXED lines (--fixed-lines): cash line {cash.value:g} ({cash.note}), "
            f"GPP target {gpp_target:g}.",
            cash.basis,
            gpp_label(gpp_target),
        )
    own = ownership_by_name(edge)
    field = (
        build_field(
            specs.values(),
            own,
            cash_offset=lines.double_up_offset,
            top_offset=lines.gpp_top1_offset,
            cash_curve=lines.double_up_own_curve,
            top_curve=lines.gpp_own_curve,
            coverage=lines.own_coverage,
        )
        if has_ownership(own)
        else None
    )
    uncertainty = ""
    draws = getattr(lines, "ownership_uncertainty", False)
    if field is not None and (draws or getattr(lines, "ownership_mean", False)):
        field, uncertainty = _with_uncertainty(field, own, lines.ownership_scenarios, draws=draws)
    if field is None:
        cash_line = lines.fallback_cash_line if lines.fallback_cash_line is not None else cash.value
        top_line = lines.fallback_top1_line if lines.fallback_top1_line is not None else gpp_target
        source = "[sim.lines] fallback" if lines.fallback_cash_line is not None else cash.note
        return SimLines(
            "fallback",
            cash_line,
            top_line,
            None,
            f"Ownership has not published (Own% is blank), so there is no field to measure against: using "
            f"FIXED lines, cash line {cash_line:g} ({source}) and a {top_line:g}-point top-end line.",
            f"cash line {cash_line:g}, fixed (ownership not published yet)",
            gpp_label(top_line),
        )
    return SimLines(
        "relative",
        cash.value,
        gpp_target,
        field,
        f"Lines move with the slate: Cash % beats the field average +{lines.double_up_offset:g}, Top 1% "
        f"beats it +{lines.gpp_top1_offset:g} (ownership of {len(field.players)} players, "
        f"{len(field.cover)} simulated with your lineups){uncertainty}.",
        f"cash line = the field's average score +{lines.double_up_offset:g}",
        "Top 1%",
    )


def _with_uncertainty(
    field: FieldModel, own: dict[str, float], scenarios: int, *, draws: bool = True
) -> tuple[FieldModel, str]:
    """`[sim.lines] ownership_mean` / `ownership_uncertainty`: the field's ownership is the fitted mean, and
    (`draws`) drawn per simulation around it (`dfs.sim.own_error`); unchanged, and said so, while there is no
    fitted model."""
    from dfs.sim import own_error
    from dfs.sim.field import with_ownership_draws

    switch = "ownership uncertainty" if draws else "the fitted ownership mean"
    model = own_error.load()
    if model is None:
        return field, f"; {switch} is on but no model is fitted, so ownership is taken as known"
    out = with_ownership_draws(field, own, model, scenarios=scenarios, seed=SEED, draws=draws)
    if not draws:
        return out, "; field ownership is the fitted mean (stake and field size), not Own% as is"
    return out, f"; ownership drawn per simulation around the fitted mean ({scenarios} field scenarios)"


def gpp_label(target: float) -> str:
    """`190+` for a 190 target."""
    return f"{target:g}+"


# ---------------------------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------------------------


def _clean(value: object) -> float | None:
    number = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(number) else float(number)


def role_for_rank(position: str, rank: int) -> str:
    """The role of the `rank`-th (0-based) back / receiver / tight end of a team: ranks past the last named
    role fold into it (a fourth back is RB3, a fifth receiver WR4, a third tight end TE2)."""
    names = ROLE_NAMES[position]
    return names[min(rank, len(names) - 1)]


def team_roles(
    edge: pd.DataFrame, depth_rows: pd.DataFrame | None, gsis_by_id: dict | None = None
) -> dict[int | str, str]:
    """Role by EdgeRaw `Id` for every back, receiver and tight end: depth-chart rank, then usage.

    `gsis_by_id` maps `Id` to the nflverse id (the Edge Finder's players table); without it, or without a
    depth chart, everyone is ranked by usage alone. Players listed OUT or IR are ranked after everyone who can
    play, so the man behind a missing starter moves up. (The same ordering `injury_beneficiaries.depth_order`
    gives, for every team and position at once.)"""
    roles: dict[int | str, str] = {}
    roles.update(dict.fromkeys(edge.loc[edge["Position"] == "QB", "Id"], "QB1"))
    roles.update(dict.fromkeys(edge.loc[edge["Position"] == "DST", "Id"], "DST"))
    frame = edge.loc[edge["Position"].isin(["RB", "WR", "TE"]), ["Id", "Team", "Position", "Avail"]].copy()
    if frame.empty:
        return roles
    usage = pd.Series(0.0, index=edge.index)
    for column in USAGE_COLUMNS:
        if column in edge.columns:
            usage = usage + pd.to_numeric(edge[column], errors="coerce").fillna(0.0)
    frame["usage"] = usage.reindex(frame.index)
    frame["gone"] = frame["Avail"].fillna("").astype(str).str.upper().isin(NOT_PLAYING)
    frame["gsis"] = frame["Id"].map(gsis_by_id or {})
    frame["chart"] = NO_CHART_RANK
    if depth_rows is not None and not depth_rows.empty:
        best = (
            depth_rows.assign(Team=depth_rows["Team"].map(normalize_team))
            .groupby(["Team", "Position", "GsisId"], as_index=False)["pos_rank"]
            .min()
            .rename(columns={"GsisId": "gsis", "pos_rank": "_rank"})
        )
        merged = frame.assign(Team=frame["Team"].map(normalize_team)).merge(
            best, on=["Team", "Position", "gsis"], how="left"
        )
        frame["chart"] = merged["_rank"].fillna(NO_CHART_RANK).to_numpy()
    ordered = frame.sort_values(
        ["Team", "Position", "gone", "chart", "usage", "Id"],
        ascending=[True, True, True, True, False, True],
        kind="stable",
    )
    ordered["rank"] = ordered.groupby(["Team", "Position"]).cumcount()
    roles.update(
        {
            player_id: role_for_rank(position, int(rank))
            for player_id, position, rank in zip(
                ordered["Id"], ordered["Position"], ordered["rank"], strict=True
            )
        }
    )
    return roles


def build_specs(
    edge: pd.DataFrame,
    depth_rows: pd.DataFrame | None = None,
    gsis_by_id: dict | None = None,
    only_names: set[str] | None = None,
) -> dict[str, PlayerSpec]:
    """One `PlayerSpec` per EdgeRaw player, keyed by `Name`. A row with no projection (neither `CalPts` nor
    `ProjPts`), team or position is left out (a lineup naming him can then not be simulated). The first row
    wins for a name that appears twice. A pass-catcher's `Tgt%` (his share of his team's targets over his last
    three games) rides along as `tshare`: the simulator's QB1-TE1 correlation reads it (`sim.shape_table`).

    `only_names` limits the work to the teams those players are on (a role depends only on his own team's
    players), which is what keeps the sync's simulator step quick; the result then holds the whole of those
    teams, not only the named players."""
    if only_names is not None:
        teams = set(edge.loc[edge["Name"].isin(only_names), "Team"])
        edge = edge[edge["Team"].isin(teams)]
    roles = team_roles(edge, depth_rows, gsis_by_id)
    specs: dict[str, PlayerSpec] = {}
    for row in edge.to_dict("records"):
        name = str(row.get("Name") or "").strip()
        if not name or name in specs:
            continue
        position, team = str(row.get("Position") or ""), str(row.get("Team") or "")
        projection = _clean(row.get("CalPts"))
        if projection is None:
            projection = _clean(row.get("ProjPts"))
        role = roles.get(row.get("Id"))
        if projection is None or not team or position not in SIM_POSITIONS or role is None:
            continue
        opp = str(row.get("Opp") or "")
        game = row.get("GameID")
        game_id = str(game) if pd.notna(game) and str(game) else "-".join(sorted(t for t in (team, opp) if t))
        specs[name] = PlayerSpec(
            id=name,
            position=position,
            team=team,
            opp=opp,
            game_id=game_id,
            role=role,
            projection=projection,
            salary=_clean(row.get("Salary")),
            tshare=_clean(row.get("Tgt%")),
        )
    return specs


def lineup_from_names(names: list[str], specs: dict[str, PlayerSpec]) -> list[PlayerSpec] | None:
    """The lineup's players in slot order, or None unless all of them are filled in and known (a half-built
    lineup, or a name EdgeRaw does not have, cannot be simulated)."""
    cleaned = [str(n or "").strip() for n in names]
    if not cleaned or any(not n or n not in specs for n in cleaned) or len(set(cleaned)) != len(cleaned):
        return None
    return [specs[n] for n in cleaned]


# ---------------------------------------------------------------------------------------------
# Swaps
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SwapDelta:
    """What one candidate swap does to the lineup's chances."""

    delta_p_cash: float
    delta_p_gpp: float
    p_cash: float
    p_gpp: float


def swap_deltas(
    lineup: list[PlayerSpec],
    swaps: list[tuple[list[str], list[PlayerSpec]]],
    *,
    cash_line: float,
    gpp_target: float,
    n_sims: int = N_SIMS,
    seed: int = SEED,
    field: FieldModel | None = None,
) -> list[SwapDelta]:
    """The change in P(cash) and P(GPP) for each candidate swap, `(names going out, specs coming in)`.

    The lineup and every swapped version are simulated in a single run (the same draws for every player they
    share), so a difference is the swap's own effect and the sampling noise left in it is the incoming
    players'. Deterministic in `seed`. An empty `swaps` simulates nothing. A `field` scores against the
    field's lines (Cash % / Top 1%) instead of `cash_line` / `gpp_target`."""
    if not swaps:
        return []
    versions = []
    for out_names, ins in swaps:
        gone = set(out_names)
        versions.append([p for p in lineup if p.id not in gone] + list(ins))
    result = simulate_lineups(
        [lineup, *versions], n_sims=n_sims, seed=seed, cash_line=cash_line, gpp_target=gpp_target, field=field
    )
    base, rest = result.lineups[0], result.lineups[1:]
    return [
        SwapDelta(
            delta_p_cash=float(s.p_cash - base.p_cash),
            delta_p_gpp=float(s.p_gpp - base.p_gpp),
            p_cash=float(s.p_cash),
            p_gpp=float(s.p_gpp),
        )
        for s in rest
    ]


def portfolio_summary(result) -> dict[str, float]:  # noqa: ANN001 - dfs.sim.simulate.SimResult
    """The portfolio line: P(at least one lineup cashes), the expected number that cash, and P(at least one
    reaches the GPP target)."""
    return {
        "p_any_cash": float(result.p_any_cash),
        "expected_cashes": float(result.expected_cashing),
        "p_any_gpp": float(result.p_any_gpp),
    }


def finite(value: float) -> float | str:
    """A number for a sheet cell, or "" when it is not finite."""
    return "" if value is None or not np.isfinite(value) else float(value)
