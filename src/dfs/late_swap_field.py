"""Late swap against the REAL field: the cash line and the top-1% line of a live contest, from its own export.

After the 1 pm ET lock Sam downloads a contest's standings by hand (DraftKings > the contest > export). The
file is every entrant's lineup, every lineup's points so far and every player's %Drafted: the field itself,
not a projection of it. `dfs lineups late-swap --field <file>|auto` scores each swap against the line that
field is heading for, instead of against the projected-ownership line `sim.field` builds (field mean + an
offset).

**The state.** Each player is in one of three states at `now`: not started (nothing scored, all of a game's
draw to come), finished or unreadable (his points so far are final), or mid-game (his points so far plus the
share of the game left times a full-game draw; the share is a flat clock, `GAME_HOURS`, because the export has
no game clock). A started player's points so far come from the export's own `FPTS` column. Mid-game is an
approximation by design: it does not condition the remaining draw on what has happened so far.

**The lines.** One correlated simulation of the slate (`simulate_remaining`: the same copula and marginals as
`sim.simulate`) gives every player's remaining points in each draw. A field lineup's score in a draw is its
points so far plus its open players' remaining points; the cash line is the score at the contest's last paid
place and the top line the score at the top 1%, per draw. Sam's lineup and every swap option are scored on the
SAME draws (`LiveLines.swap_deltas`), so a lineup that shares players with the field moves with the lines the
way it should, and a swap's own effect is the difference.

A field lineup naming a player the simulator cannot rate keeps his points so far and gets nothing more from
him (counted in `RealLines.unresolved`, so the report can say how much of the field that was).

Pure except `read_field` / `find_auto` (read files). Nothing here touches a sheet or the network.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.special import ndtr

from dfs.contests import series
from dfs.contests import standings as st
from dfs.kickoff import format_et, parse_kickoff
from dfs.player_join import normalize_name
from dfs.sim import marginal
from dfs.sim.shape_table import default_lookup
from dfs.sim.simulate import (
    PlayerSpec,
    _distinct_players,  # the one validator of a player list; reused, not copied
    _draw,  # the one principal-root draw (its docstring is why a seed means the same draws)
    correlation_matrix,
    repair_correlation,
)
from dfs.sim_inputs import SwapDelta

GAME_HOURS = 3.25  # a game's wall-clock length, kickoff to final whistle, for the share of it left
DEFAULT_CASH_FRACTION = 0.45  # a Double Up pays about this share of its entrants (43.5-50% in 2026)
TOP_FRACTION = 0.01
N_SIMS = 20000
SEED = 0
CHUNK = 500  # draws scored against the whole field at a time (bounds the entrants x draws array)
MAX_FIELD_FILES = 2  # one Double Up and one GPP: what Sam downloads
POINTS_TOLERANCE = 0.011  # DK shows two decimals


# ---------------------------------------------------------------------------------------------------------
# Reading the export
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldFile:
    """One contest's export, parsed. `kind` is "cash" (a Double Up / 50-50), "gpp" or "unknown" (the format of
    a live contest is not in the export; `read_field` is told it from the entries file or the history log)."""

    contest_id: str
    origin: str
    kind: str
    name: str
    exported_at: datetime | None
    standings: st.Standings

    @property
    def label(self) -> str:
        return self.name or f"contest {self.contest_id}"

    @property
    def entrants(self) -> int:
        return len(self.standings.lineups)

    def describe(self) -> str:
        when = f", exported {format_et(self.exported_at)}" if self.exported_at else ""
        return f"{self.label}, {self.entrants:,} lineups{when}"


def kind_of_format(fmt: str) -> str:
    if fmt in series.CASH_FORMATS:
        return "cash"
    if fmt in (series.SE_GPP, series.N_MAX_GPP, series.MULTIPLIER, series.FREEROLL):
        return "gpp"
    return "unknown"


def read_field(
    path: Path,
    *,
    formats: Mapping[str, tuple[str, str]] | None = None,
    places_paid: int | None = None,
) -> FieldFile:
    """Parse the standings CSV at `path` (bare or inside a zip). `formats` is `{contest id: (contest name,
    series format)}` from the entries file, how a live contest's kind is known. Raises `st.StandingsError` for
    a file that is not a Classic standings export."""
    scan = st.scan_standings(path)
    if not scan.sources:
        raise st.StandingsError(f"{path}: no standings CSV found")
    source = scan.sources[0]
    standings = st.parse_standings(source.raw, contest_id=source.contest_id, places_paid=places_paid)
    if standings.kind != "Classic" or standings.lineups.empty:
        raise st.StandingsError(f"{path}: not a Classic contest ({standings.kind})")
    name, fmt = (formats or {}).get(source.contest_id, ("", ""))
    try:
        stamp = datetime.fromtimestamp(Path(path).stat().st_mtime, tz=UTC)
    except OSError:
        stamp = None
    return FieldFile(source.contest_id, source.origin, kind_of_format(fmt), name, stamp, standings)


_STANDINGS_NAME = re.compile(r"standings", re.IGNORECASE)


def find_auto(
    downloads: Path,
    *,
    first_kickoff: datetime | None,
    formats: Mapping[str, tuple[str, str]] | None = None,
    limit: int = MAX_FIELD_FILES,
) -> tuple[list[FieldFile], list[str]]:
    """`(files, notes)`: the newest standings exports in `downloads` that are this week's and live.

    A file counts when it parses as a Classic export, some entry still has time remaining (`not complete`:
    last week's and finished contests are done) and it was downloaded at or after `first_kickoff` (this
    slate's earliest kickoff, minus a day's slack: nothing downloaded before the slate can be its live
    field). Newest first; at most one per contest and `limit` in all, a Double Up and a GPP preferred over two
    of one kind when the kinds are known. `notes` says every file it passed over and why."""
    folder = Path(downloads).expanduser()
    notes: list[str] = []
    if not folder.is_dir():
        return [], [f"no Downloads folder at {folder}"]
    candidates = [
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in (".csv", ".zip") and _STANDINGS_NAME.search(p.name)
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    earliest = None if first_kickoff is None else first_kickoff.timestamp() - 86400
    found: list[FieldFile] = []
    seen: set[str] = set()
    for path in candidates:
        if earliest is not None and path.stat().st_mtime < earliest:
            notes.append(f"{path.name}: downloaded before this slate")
            continue
        try:
            parsed = read_field(path, formats=formats)
        except (st.StandingsError, OSError, ValueError) as e:
            notes.append(f"{path.name}: not a usable standings export ({e})")
            continue
        if parsed.contest_id in seen:
            continue
        seen.add(parsed.contest_id)
        if parsed.standings.profile.complete:
            notes.append(f"{path.name}: contest {parsed.contest_id} is finished, not live")
            continue
        found.append(parsed)
    chosen: list[FieldFile] = []
    for kind in ("cash", "gpp"):  # one of each kind first ...
        first = next((f for f in found if f.kind == kind), None)
        if first is not None and first not in chosen:
            chosen.append(first)
    for f in found:  # ... then the newest of whatever is left
        if len(chosen) >= limit:
            break
        if f not in chosen:
            chosen.append(f)
    return chosen[:limit], notes


def entry_formats(path: Path) -> dict[str, tuple[str, str]]:
    """`{contest id: (contest name, series format)}` for every entry in a DraftKings entries file: how a LIVE
    contest's format is known (a standings export does not say). `{}` when the file cannot be read."""
    from dfs import entries as ent  # heavy imports; only needed when a field is loaded

    try:
        parsed = ent.read_entries_file(path)
    except (ent.EntriesFileError, OSError, ValueError, KeyError):
        return {}
    return {e.contest_id: (e.contest_name, ent.entry_format(e)) for e in parsed.entries if e.contest_id}


def default_cash_fraction(log: pd.DataFrame | None) -> float:
    """The median share of entrants a Double Up paid in the history log (`places_paid / entries`), else
    `DEFAULT_CASH_FRACTION`. A live export does not say how many places pay."""
    if log is None or log.empty or not {"format", "entries", "places_paid"} <= set(log.columns):
        return DEFAULT_CASH_FRACTION
    du = log[log["format"] == series.DOUBLE_UP].drop_duplicates("contest_key")
    shares = (
        pd.to_numeric(du["places_paid"], errors="coerce") / pd.to_numeric(du["entries"], errors="coerce")
    ).dropna()
    return float(shares.median()) if len(shares) else DEFAULT_CASH_FRACTION


def real_ownership(standings: st.Standings, edge_names: Sequence[str]) -> dict[str, float]:
    """`{EdgeRaw Name: share of the contest's lineups holding him}` for every export player that matches an
    EdgeRaw name (normalised, first row wins). The export's `%Drafted` is per player across the whole field,
    so this is what the field really holds, not a projection."""
    by_key: dict[str, str] = {}
    for name in edge_names:
        by_key.setdefault(normalize_name(name), name)
    out: dict[str, float] = {}
    for player, share in zip(standings.ownership["player"], standings.ownership["pct_drafted"], strict=True):
        name = by_key.get(normalize_name(player))
        if name is not None and np.isfinite(share):
            out[name] = float(share)
    return out


def overlay_ownership(edge: pd.DataFrame, own: Mapping[str, float]) -> pd.DataFrame:
    """`edge` with `Own%` set to the real share for every player in `own` (others keep the projection)."""
    if not own or "Name" not in edge.columns:
        return edge
    out = edge.copy()
    real = out["Name"].map(own)
    current = (
        pd.to_numeric(out["Own%"], errors="coerce")
        if "Own%" in out.columns
        else pd.Series(np.nan, index=out.index)
    )
    out["Own%"] = real.where(real.notna(), current)
    return out


# ---------------------------------------------------------------------------------------------------------
# The state of the slate and its draws
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SlateState:
    """Where every player stands at `now`: `now_pts` scored so far, `frac` of a game still to come (1 = not
    started, 0 = nothing left to simulate), per spec, in `specs` order."""

    specs: tuple[PlayerSpec, ...]
    now_pts: np.ndarray
    frac: np.ndarray

    def __post_init__(self) -> None:
        if not (len(self.specs) == len(self.now_pts) == len(self.frac)):
            raise ValueError("specs, now_pts and frac must be the same length")

    @property
    def column(self) -> dict[str, int]:
        return {p.id: i for i, p in enumerate(self.specs)}


def slate_state(
    specs: Mapping[str, PlayerSpec],
    kickoffs: Mapping[str, datetime | None],
    now: datetime,
    fpts_by_key: Mapping[str, float],
    *,
    game_hours: float | None = GAME_HOURS,
    out: Collection[str] = (),
) -> SlateState:
    """The state of `specs` at `now`. A player with no readable kickoff counts as not started. A started
    player's points so far are `fpts_by_key[normalize_name(id)]` (0 when the export has none: nobody drafted
    him); the share of his game left is `1 - elapsed / game_hours`, and with `game_hours=None` every started
    player is final (the back-test's 1 pm state: the early games are over). A player named in `out` (ruled
    OUT or on IR) scores nothing, whatever lineups hold him."""
    ordered = sorted(specs.values(), key=lambda p: p.id)
    now_pts = np.zeros(len(ordered))
    frac = np.ones(len(ordered))
    for i, spec in enumerate(ordered):
        if spec.id in out:
            frac[i] = 0.0
            continue
        start = kickoffs.get(spec.id)
        if start is None or now < start:
            continue
        now_pts[i] = float(fpts_by_key.get(normalize_name(spec.id), 0.0))
        if game_hours is None:
            frac[i] = 0.0
        else:
            elapsed = (now - start).total_seconds() / 3600
            frac[i] = float(np.clip(1.0 - elapsed / game_hours, 0.0, 1.0))
    return SlateState(tuple(ordered), now_pts, frac)


def kickoffs_from_edge(edge: pd.DataFrame) -> dict[str, datetime | None]:
    """`{Name: kickoff instant}` from EdgeRaw's `GameStart` (Eastern wall-clock, `kickoff.py`)."""
    if "GameStart" not in edge.columns:
        return {}
    out: dict[str, datetime | None] = {}
    for name, value in zip(edge["Name"], edge["GameStart"], strict=True):
        out.setdefault(str(name).strip(), parse_kickoff(value))
    return out


NOT_PLAYING = frozenset({"OUT", "IR"})


def unavailable(edge: pd.DataFrame) -> set[str]:
    """Names EdgeRaw lists OUT or on IR: they score nothing, in the field's lineups as in Sam's."""
    if "Avail" not in edge.columns:
        return set()
    avail = edge["Avail"].fillna("").astype(str).str.upper()
    return {str(n).strip() for n, a in zip(edge["Name"], avail, strict=True) if a in NOT_PLAYING}


def fpts_by_key(standings: st.Standings) -> dict[str, float]:
    """`{normalised name: points so far}` from the export's player table."""
    return {
        normalize_name(p): float(v)
        for p, v in zip(standings.ownership["player"], standings.ownership["fpts"], strict=True)
        if pd.notna(v)
    }


@dataclass
class SlateDraws:
    """Every player's FINAL points in each draw (`final`, draws x players, in `state.specs` order): points so
    far plus the remaining share of a full-game draw."""

    state: SlateState
    final: np.ndarray
    n_sims: int
    seed: int
    column: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.column = self.state.column

    def remaining(self) -> np.ndarray:
        """Draws x players: what each player still adds (final minus points so far)."""
        return self.final - self.state.now_pts[None, :]


def simulate_remaining(state: SlateState, *, n_sims: int = N_SIMS, seed: int = SEED) -> SlateDraws:
    """One correlated simulation of every player with a game still to come (`frac > 0`, a projection above
    zero). A player with nothing left is a constant: his points so far."""
    live = [i for i, p in enumerate(state.specs) if state.frac[i] > 0 and p.projection > 0]
    final = np.tile(state.now_pts, (n_sims, 1))
    if live:
        players = _distinct_players([[state.specs[i] for i in live]])
        raw, _ = correlation_matrix(players, default_lookup())
        r, _ = repair_correlation(raw)
        u = ndtr(_draw(r, n_sims, np.random.default_rng(seed)))
        for position in ("QB", "RB", "WR", "TE", "DST"):
            cols = [k for k, p in enumerate(players) if p.position == position]
            if not cols:
                continue
            mg = marginal.marginals(position, [players[k].projection for k in cols])
            for j, k in enumerate(cols):
                final[:, live[k]] += state.frac[live[k]] * marginal.ppf(mg, j, u[:, k])
    return SlateDraws(state, final, n_sims, seed)


# ---------------------------------------------------------------------------------------------------------
# The field's lines
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RealLines:
    """One contest's cash line and top line in every draw, and what it took to get them."""

    cash: np.ndarray  # draws: the score at the last paid place
    top: np.ndarray  # draws: the score at the top 1%
    entrants: int
    cash_rank: int
    top_rank: int
    unresolved: int  # open-slot players in the field that the simulator could not rate
    open_slots: int  # field lineup slots with a game still to come
    points_match: float  # share of lineups whose points so far equal the export's own Points


def field_scores_base(
    standings: st.Standings, state: SlateState, fpts: Mapping[str, float]
) -> tuple[sparse.csr_matrix, np.ndarray, int, int, float]:
    """`(A, base, unresolved, open_slots, points_match)` for a contest: `A` (lineups x players, a 1 where the
    lineup holds a player who still has a game to come), `base` each lineup's points so far, summed from
    `fpts` over the players with a game started (a player whose game has not begun scored nothing yet)."""
    column = {normalize_name(p.id): i for i, p in enumerate(state.specs)}
    scored = {k: float(v) for k, v in fpts.items()}
    rows, cols = [], []
    base = np.zeros(len(standings.lineups))
    unresolved = open_slots = 0
    for n, slots in enumerate(standings.lineups["slots"]):
        for _slot, name in slots:
            key = normalize_name(name)
            i = column.get(key)
            if i is None:
                base[n] += scored.get(key, 0.0)
                unresolved += 1
                continue
            base[n] += state.now_pts[i]
            if state.frac[i] > 0:
                rows.append(n)
                cols.append(i)
                open_slots += 1
    matrix = sparse.csr_matrix(
        (np.ones(len(rows)), (rows, cols)), shape=(len(standings.lineups), len(state.specs))
    )
    matched = float((np.abs(base - standings.lineups["points"].to_numpy(float)) <= POINTS_TOLERANCE).mean())
    return matrix, base, unresolved, open_slots, matched


def kth_largest(scores: np.ndarray, k: int) -> np.ndarray:
    """The `k`-th largest value (1 = the highest) in each column of `scores`."""
    return -np.partition(-scores, k - 1, axis=0)[k - 1]


def real_lines(
    standings: st.Standings,
    draws: SlateDraws,
    fpts: Mapping[str, float],
    *,
    cash_fraction: float = DEFAULT_CASH_FRACTION,
    places_paid: int | None = None,
) -> RealLines:
    """The contest's per-draw cash line and top-1% line. `places_paid` (when known) sets the cash line's rank;
    otherwise `cash_fraction` of the entrants."""
    a, base, unresolved, open_slots, matched = field_scores_base(standings, draws.state, fpts)
    n = a.shape[0]
    cash_rank = int(min(max(places_paid or round(cash_fraction * n), 1), n))
    top_rank = int(min(max(math.ceil(TOP_FRACTION * n), 1), n))
    remaining = draws.remaining()
    cash = np.empty(draws.n_sims)
    top = np.empty(draws.n_sims)
    for lo in range(0, draws.n_sims, CHUNK):
        part = base[:, None] + a @ remaining[lo : lo + CHUNK].T  # lineups x draws
        cash[lo : lo + CHUNK] = kth_largest(part, cash_rank)
        top[lo : lo + CHUNK] = kth_largest(part, top_rank)
    return RealLines(cash, top, n, cash_rank, top_rank, unresolved, open_slots, matched)


def projected_state_lines(
    draws: SlateDraws, own: Mapping[str, float], *, cash_offset: float, top_offset: float
) -> tuple[np.ndarray, np.ndarray]:
    """The projected-ownership lines (field mean + an offset, `sim.field`) on the SAME state-aware draws: the
    field mean is the projected-ownership-weighted sum of every player's final points in the draw. Used by the
    back-test to separate "knowing where the slate stands" from "knowing the real field"."""
    weights = np.array([own.get(p.id, 0.0) for p in draws.state.specs], dtype=float)
    total = weights.sum()
    if total <= 0:
        raise ValueError("no projected ownership to build a field from")
    mean = draws.final @ (weights * (9.0 / total))
    return mean + cash_offset, mean + top_offset


# ---------------------------------------------------------------------------------------------------------
# Scoring a lineup and its swaps against the lines
# ---------------------------------------------------------------------------------------------------------


@dataclass
class LiveLines:
    """What `late_swap_sim` scores swaps against when a real field is loaded: the slate's draws, the two lines
    and where they came from (`source`, for the report and the Cockpit)."""

    draws: SlateDraws
    cash_line: np.ndarray
    top_line: np.ndarray
    source: str
    notes: list[str] = field(default_factory=list)
    known_points: frozenset[str] = frozenset()  # normalised names the exports' player tables hold

    def missing_points(self, lineup: Sequence[PlayerSpec]) -> list[str]:
        """Players of `lineup` whose game has started but whom no export's player table lists (nobody in that
        contest drafted him): their points so far are unknown and count as 0."""
        started = {p.id for p, f in zip(self.draws.state.specs, self.draws.state.frac, strict=True) if f < 1}
        return [p.id for p in lineup if p.id in started and normalize_name(p.id) not in self.known_points]

    def scores(self, lineup: Sequence[PlayerSpec]) -> np.ndarray:
        """The lineup's final score in every draw; a player the draws do not hold raises KeyError."""
        cols = [self.draws.column[p.id] for p in lineup]
        return self.draws.final[:, cols].sum(axis=1)

    def probabilities(self, scores: np.ndarray) -> tuple[float, float]:
        return float((scores >= self.cash_line).mean()), float((scores >= self.top_line).mean())

    def swap_deltas(
        self, lineup: list[PlayerSpec], swaps: list[tuple[list[str], list[PlayerSpec]]]
    ) -> list[SwapDelta]:
        """The change in P(cash) and P(top 1%) for each `(names going out, specs coming in)`, on the shared
        draws. The same contract as `sim_inputs.swap_deltas`."""
        base = self.scores(lineup)
        base_cash, base_top = self.probabilities(base)
        final, column = self.draws.final, self.draws.column
        out = []
        for gone, ins in swaps:
            score = base.copy()
            for name in gone:
                score -= final[:, column[name]]
            for spec in ins:
                score += final[:, column[spec.id]]
            p_cash, p_top = self.probabilities(score)
            out.append(SwapDelta(p_cash - base_cash, p_top - base_top, p_cash, p_top))
        return out


def pick_files(files: Sequence[FieldFile]) -> tuple[FieldFile, FieldFile, list[str]]:
    """`(cash file, top file, notes)`: the Double Up for the cash line and the GPP for the top line. A kind
    that is missing (or unknown) uses the other file, and the notes say so."""
    if not files:
        raise ValueError("no field files")
    cash = next((f for f in files if f.kind == "cash"), None)
    gpp = next((f for f in files if f.kind == "gpp"), None)
    notes: list[str] = []
    if cash is None and gpp is None:
        only = files[0]
        notes.append(
            f"{only.label}: its format is unknown (no entries file names it), so it sets BOTH lines; "
            "download a Double Up and a GPP and run with the entries file in Downloads for each line "
            "from its own contest"
        )
        return only, only, notes
    if cash is None:
        notes.append(f"no Double Up export: Cash % uses {gpp.label}'s field")
        cash = gpp
    if gpp is None:
        notes.append(f"no GPP export: Top 1% uses {cash.label}'s field")
        gpp = cash
    return cash, gpp, notes


def build_live_lines(
    files: Sequence[FieldFile],
    specs: Mapping[str, PlayerSpec],
    edge: pd.DataFrame,
    now: datetime,
    *,
    cash_fraction: float = DEFAULT_CASH_FRACTION,
    n_sims: int = N_SIMS,
    seed: int = SEED,
    game_hours: float | None = GAME_HOURS,
) -> LiveLines:
    """The live lines for `files` at `now`. The points-so-far table is the union of the files' `FPTS` columns
    (the same player has the same points in every contest)."""
    cash_file, top_file, notes = pick_files(files)
    fpts: dict[str, float] = {}
    for f in files:
        fpts.update(fpts_by_key(f.standings))
    ruled_out = unavailable(edge)
    state = slate_state(specs, kickoffs_from_edge(edge), now, fpts, game_hours=game_hours, out=ruled_out)
    draws = simulate_remaining(state, n_sims=n_sims, seed=seed)
    cash_lines = real_lines(cash_file.standings, draws, fpts, cash_fraction=cash_fraction)
    top_lines = cash_lines if top_file is cash_file else real_lines(top_file.standings, draws, fpts)
    checked = [(cash_file, cash_lines)] + ([] if top_file is cash_file else [(top_file, top_lines)])
    for f, lines in checked:
        if lines.points_match < 0.9:
            notes.append(
                f"{f.label}: only {lines.points_match:.0%} of the lineups' points add up from the player "
                "table: the line may be off"
            )
        if lines.unresolved:
            notes.append(f"{f.label}: {lines.unresolved:,} open lineup slots hold a player not in EdgeRaw")
    source = "field: real (" + cash_file.describe() + ")"
    if top_file is not cash_file:
        source = f"field: real (Cash % from {cash_file.describe()}; Top 1% from {top_file.describe()})"
    return LiveLines(draws, cash_lines.cash, top_lines.top, source, notes, frozenset(fpts))
