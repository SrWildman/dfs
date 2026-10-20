"""Generate a contest's field: the lineups the other entrants play, from projected ownership and the way real
fields are built.

**Inputs.** A slate (`Slate`: every rosterable player's position, team, opponent, salary and projected
ownership) and the construction rates of the contest's format (`FieldRates`, measured on Sam's real standings
by `rates_from_features`: the share of lineups that stack their QB with 1 / 2+ of his WR/TE, the bring-back
rate with and without a stack, and the salary left unspent).

**The sampler** (all lineups at once, vectorised):

1. the QB, in proportion to his weight;
2. a stack with probability `stack1` (2+ pass catchers with `stack2 / stack1` of those): WR/TE of the QB's
   team, by weight; a lineup that does not stack takes none of them (that is what "not stacked" means in the
   real fields' measure);
3. a bring-back (a RB/WR/TE from the QB's opponent) with the rate measured for stacked or for unstacked
   lineups; a lineup without one takes no offensive player from the opponent;
4. every other slot by weight among the players that fit an open slot (FLEX takes any RB/WR/TE), keep the
   lineup completable under the $50,000 cap and still let it spend down to a salary-left amount drawn for it
   from the real fields' distribution; a lineup that ends with more than that left (plus `UNSPENT_SLACK`) is
   drawn again.

**Ownership.** The weights start at the projected ownership and are raked (`RAKE_ROUNDS` rounds, multiplying
each player's weight by target / generated share) so the field holds each player at his projected share; the
lineup structure the sampler imposes stays. Per position the target is scaled to the slots it fills: QB and
DST to one lineup's worth each, RB + WR + TE to seven together.

**The chalk herd.** Real fields are not all hand-built: in every double-up of Weeks 4-6 one lineup was held by
6-21% of the field, and it is (to 7-9 of its 9 players) the feasible lineup with the highest product of
pre-lock projected ownership; the same lineups top the GPPs' duplicates at well under 1%. So a share
`HerdModel.share` of the field plays the `HERD_K` lineups with the highest ownership product (`chalk_lineups`,
an exact MILP with no-good cuts), the k-th of them `decay ** (k - 1)` as often as the first. The sampler fills
the rest, raked so that herd plus sampler hold each player at his target. Both numbers are fitted per format
on the real fields (`contest_retro.fit_herd`); duplicates are counted after generation (`dup_share`,
`copies`), with `dups.DUP_MODEL_GPP` / `DUP_MODEL_CASH` as a yardstick (`model_copies`).

Pure functions, deterministic in the seed. No network, no sheet.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

SALARY_CAP = 50_000
POSITIONS = ("QB", "RB", "WR", "TE", "DST")
FLEX_POSITIONS = ("RB", "WR", "TE")
PASS_CATCHERS = ("WR", "TE")
NEEDS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
FLEX_SLOTS = 1
LINEUP_SIZE = 9
# A player projected at (near) 0% counts at 0.01%: the ~300 such players on a main slate held 0.01-0.02 of the
# nine slots in total in his real fields (a 0.2% floor put most of a slot on them and pulled the field mean
# down).
OWN_FLOOR = 0.0001
RAKE_ROUNDS = 6
RAKE_POWER = 1.0
RAKE_SIZE = 5_000  # lineups per raking round: enough to measure every relevant player's share
UNSPENT_SLACK = 300  # a lineup may leave this much more than its drawn salary-left target
MAX_REDRAWS = 8
HERD_K = 20


@dataclass(frozen=True)
class FieldRates:
    """How one format's field builds (shares of lineups, salary left in dollars)."""

    stack1: float
    stack2: float
    bringback_stacked: float
    bringback_unstacked: float
    unspent: tuple[float, ...]  # the real fields' salary left, one value per lineup (sampled from)

    @property
    def stack2_given_stack1(self) -> float:
        return self.stack2 / self.stack1 if self.stack1 > 0 else 0.0


@dataclass(frozen=True)
class HerdModel:
    """The share of the field on the chalk lineups and how fast it falls off down their ranking.

    `weights` puts the most on the highest-product lineup. Real fields pick their megadup further down now and
    then (Week 4's was the fifth), so `draw_weights` picks the LEAD lineup per draw: rank `j` with probability
    proportional to `lead_decay ** j` (0: always the first), and the lead takes the share the first lineup has
    in `weights`, the others the rest in rank order."""

    share: float = 0.0
    decay: float = 0.5
    k: int = HERD_K
    lead_decay: float = 0.0

    def weights(self, n_lineups: int) -> np.ndarray:
        w = self.decay ** np.arange(n_lineups, dtype=float)
        return w / w.sum() if n_lineups else w

    def lead_probabilities(self, n_lineups: int) -> np.ndarray:
        if n_lineups == 0:
            return np.zeros(0)
        if self.lead_decay <= 0:
            return np.eye(n_lineups)[0]
        p = self.lead_decay ** np.arange(n_lineups, dtype=float)
        return p / p.sum()

    def draw_weights(self, n_lineups: int, n_draws: int, rng: np.random.Generator) -> np.ndarray:
        """(n_draws x n_lineups): each draw's split of the herd over the chalk lineups (rows sum to 1)."""
        base = self.weights(n_lineups)
        if n_lineups == 0:
            return np.zeros((n_draws, 0))
        leads = rng.choice(n_lineups, size=n_draws, p=self.lead_probabilities(n_lineups))
        out = np.empty((n_draws, n_lineups))
        for lead in np.unique(leads):
            order = [lead, *[j for j in range(n_lineups) if j != lead]]
            row = np.empty(n_lineups)
            row[order] = base
            out[leads == lead] = row
        return out

    def expected_weights(self, n_lineups: int) -> np.ndarray:
        """The mean of `draw_weights` over draws: what the herd holds on average."""
        base = self.weights(n_lineups)
        out = np.zeros(n_lineups)
        for lead, p in enumerate(self.lead_probabilities(n_lineups)):
            order = [lead, *[j for j in range(n_lineups) if j != lead]]
            row = np.empty(n_lineups)
            row[order] = base
            out += p * row
        return out


def rates_from_features(features: pd.DataFrame) -> FieldRates:
    """`FieldRates` from `contests.field.field_stats(...).features` (one row per real lineup, pooled over any
    number of contests): only the lineups whose players all resolved count."""
    ok = features[features["resolved"].astype(bool)]
    stacked = ok["stack1"] == 1.0
    unspent = features["unspent"].dropna().to_numpy(float)
    return FieldRates(
        stack1=float(stacked.mean()),
        stack2=float((ok["stack2"] == 1.0).mean()),
        bringback_stacked=float(ok.loc[stacked, "bringback"].mean()) if stacked.any() else 0.0,
        bringback_unstacked=float(ok.loc[~stacked, "bringback"].mean()) if (~stacked).any() else 0.0,
        unspent=tuple(np.sort(unspent)) if len(unspent) else (0.0,),
    )


@dataclass(frozen=True)
class Slate:
    """The rosterable players of one slate, in a fixed order (by name). `own` is the projected share of
    lineups holding each player (a fraction)."""

    names: tuple[str, ...]
    position: np.ndarray  # str
    team: np.ndarray  # str
    opp: np.ndarray  # str
    salary: np.ndarray  # int
    own: np.ndarray  # float

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, own_column: str = "own") -> Slate:
        """From a frame with `name`, `position`, `team`, `opp`, `salary` and an ownership column. Rows
        without a salary, a team or a known position are dropped; a missing ownership counts at
        `OWN_FLOOR`."""
        f = frame.copy()
        f["salary"] = pd.to_numeric(f["salary"], errors="coerce")
        f = f[f["salary"].notna() & f["position"].isin(POSITIONS) & f["team"].astype(str).ne("")]
        f = f.drop_duplicates("name").sort_values("name", kind="stable")
        own = pd.to_numeric(f[own_column], errors="coerce").fillna(0.0).clip(lower=0.0)
        return cls(
            names=tuple(f["name"].astype(str)),
            position=f["position"].astype(str).to_numpy(),
            team=f["team"].astype(str).str.upper().to_numpy(),
            opp=f["opp"].astype(str).str.upper().to_numpy(),
            salary=f["salary"].astype(int).to_numpy(),
            own=np.maximum(own.to_numpy(float), OWN_FLOOR),
        )

    def __len__(self) -> int:
        return len(self.names)

    def targets(self, curve=None) -> np.ndarray:
        """The ownership the field is raked to: each player's share (through `curve`, a function of one
        share, when given: the double-up curve for a cash field), then QB and DST scaled to one lineup's worth
        each and RB + WR + TE to the seven slots they fill (FLEX included)."""
        out = self.own.astype(float).copy()
        if curve is not None:
            out = np.array([curve(x) for x in out])
        for pos in ("QB", "DST"):
            m = self.position == pos
            if m.any():
                out[m] *= 1.0 / out[m].sum()
        flex = np.isin(self.position, FLEX_POSITIONS)
        if flex.any():
            out[flex] *= (LINEUP_SIZE - 2) / out[flex].sum()
        return np.minimum(out, 1.0)


@dataclass
class Field:
    """A generated field: `lineups` is (n x 9) player indices into `slate`, sorted within each row."""

    slate: Slate
    lineups: np.ndarray
    weights: np.ndarray  # the sampler's final per-player weights

    def __len__(self) -> int:
        return len(self.lineups)

    def ownership(self) -> np.ndarray:
        """Each player's share of the generated lineups."""
        counts = np.bincount(self.lineups.ravel(), minlength=len(self.slate))
        return counts / max(len(self.lineups), 1)

    def keys(self) -> list[tuple[int, ...]]:
        return [tuple(row) for row in self.lineups]

    def copies(self) -> np.ndarray:
        """For each lineup, how many OTHER lineups in the field are identical to it."""
        keys = self.keys()
        count = Counter(keys)
        return np.array([count[k] - 1 for k in keys])

    def dup_share(self) -> float:
        """The share of lineups whose exact nine players appear twice or more (`contests.field`'s measure)."""
        return float((self.copies() >= 1).mean()) if len(self) else 0.0

    def features(self) -> pd.DataFrame:
        """Per lineup: `stack1`, `stack2`, `bringback`, `unspent`, `flex_pos` (as the real-field measures)."""
        s = self.slate
        pos, team, opp = s.position[self.lineups], s.team[self.lineups], s.opp[self.lineups]
        is_qb = pos == "QB"
        qb_team = team[is_qb]
        qb_opp = opp[is_qb]
        catchers = (np.isin(pos, PASS_CATCHERS)) & (team == qb_team[:, None])
        bring = np.isin(pos, FLEX_POSITIONS) & (team == qb_opp[:, None])
        unspent = SALARY_CAP - s.salary[self.lineups].sum(axis=1)
        return pd.DataFrame(
            {
                "stack1": (catchers.sum(axis=1) >= 1).astype(float),
                "stack2": (catchers.sum(axis=1) >= 2).astype(float),
                "bringback": bring.any(axis=1).astype(float),
                "unspent": unspent.astype(float),
                "flex_pos": _flex_positions(pos),
            }
        )

    def scores(self, points: np.ndarray) -> np.ndarray:
        """Each lineup's score given one number per slate player (`points[i]`), or per draw (`points` is
        draws x players): returns (n,) or (draws x n)."""
        points = np.asarray(points, dtype=float)
        if points.ndim == 1:
            return points[self.lineups].sum(axis=1)
        return points[:, self.lineups].sum(axis=2)


def _flex_positions(pos: np.ndarray) -> np.ndarray:
    """The FLEX position of each lineup: the position holding one more player than its base need."""
    out = np.full(len(pos), "", dtype=object)
    for p in FLEX_POSITIONS:
        out[(pos == p).sum(axis=1) > NEEDS[p]] = p
    return out


# ---------------------------------------------------------------------------------------------------------
# The sampler
# ---------------------------------------------------------------------------------------------------------


def _pick(weights: np.ndarray, mask: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """One player index per row, in proportion to `weights` among `mask` (-1 for a row with none)."""
    w = np.where(mask, weights[None, :], 0.0)
    cum = np.cumsum(w, axis=1)
    total = cum[:, -1]
    u = rng.random(len(w)) * total
    idx = (cum > u[:, None]).argmax(axis=1)
    idx[total <= 0] = -1
    return idx


class _Batch:
    """The state of `n` lineups being filled."""

    def __init__(self, slate: Slate, n: int):
        self.s = slate
        self.n = n
        self.chosen = np.zeros((n, len(slate)), dtype=bool)
        self.open = {p: np.full(n, NEEDS[p]) for p in POSITIONS}
        self.flex = np.full(n, FLEX_SLOTS)
        self.budget = np.full(n, SALARY_CAP)
        self.banned = np.zeros((n, len(slate)), dtype=bool)
        self.rows = np.arange(n)
        self.min_salary = {
            p: int(slate.salary[slate.position == p].min()) if (slate.position == p).any() else 0
            for p in POSITIONS
        }
        self.max_salary = {
            p: int(slate.salary[slate.position == p].max()) if (slate.position == p).any() else 0
            for p in POSITIONS
        }
        flex_min = [self.min_salary[p] for p in FLEX_POSITIONS if (slate.position == p).any()]
        self.min_flex = min(flex_min) if flex_min else 0
        self.max_flex = max((self.max_salary[p] for p in FLEX_POSITIONS), default=0)

    def slots_left(self) -> np.ndarray:
        return sum(self.open[p] for p in POSITIONS) + self.flex

    def min_cost(self) -> np.ndarray:
        """The cheapest way to fill every open slot of each lineup."""
        return sum(self.open[p] * self.min_salary[p] for p in POSITIONS) + self.flex * self.min_flex

    def max_cost(self) -> np.ndarray:
        """The dearest way to fill every open slot of each lineup."""
        return sum(self.open[p] * self.max_salary[p] for p in POSITIONS) + self.flex * self.max_flex

    def fits(self, left_at_most: np.ndarray | None = None) -> np.ndarray:
        """(n x players): the player fits an open slot, is not taken or banned, keeps the lineup completable
        under the cap and (with `left_at_most`) still lets the open slots spend down to that much left."""
        pos = self.s.position
        fits_slot = np.zeros((self.n, len(self.s)), dtype=bool)
        slot_min = np.zeros((self.n, len(self.s)))
        slot_max = np.zeros((self.n, len(self.s)))
        for p in POSITIONS:
            col = pos == p
            own_slot = self.open[p] > 0
            via_flex = (self.flex > 0) & ~own_slot if p in FLEX_POSITIONS else np.zeros(self.n, dtype=bool)
            fits_slot[:, col] = (own_slot | via_flex)[:, None]
            slot_min[:, col] = np.where(own_slot, self.min_salary[p], self.min_flex)[:, None]
            slot_max[:, col] = np.where(own_slot, self.max_salary[p], self.max_flex)[:, None]
        salary = self.s.salary[None, :]
        ok = salary <= (self.budget - self.min_cost())[:, None] + slot_min
        if left_at_most is not None:
            ok &= salary >= (self.budget - left_at_most - self.max_cost())[:, None] + slot_max
        return fits_slot & ok & ~self.chosen & ~self.banned

    def take(self, rows: np.ndarray, players: np.ndarray) -> None:
        ok = players >= 0
        rows, players = rows[ok], players[ok]
        self.chosen[rows, players] = True
        self.budget[rows] -= self.s.salary[players]
        pos = self.s.position[players]
        for p in POSITIONS:
            hit = rows[pos == p]
            own_slot = self.open[p][hit] > 0
            self.open[p][hit[own_slot]] -= 1
            self.flex[hit[~own_slot]] -= 1


def sample_lineups(
    slate: Slate,
    rates: FieldRates,
    n: int,
    weights: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """`n` lineups (n x 9 player indices, each row sorted) from the sampler in the module docstring, with
    per-player `weights`. Lineups the sampler cannot finish, or that leave too much salary, are redrawn (up to
    `MAX_REDRAWS` times; any still short are dropped, so fewer than `n` can come back on a broken slate)."""
    out: list[np.ndarray] = []
    need, accept = n, 0.6  # the share of drawn lineups that pass, updated from each draw
    for _ in range(MAX_REDRAWS):
        if need <= 0:
            break
        tried = int(need / max(accept, 0.05) * 1.1) + 16
        rows = _sample_once(slate, rates, tried, weights, rng)
        accept = max(len(rows) / tried, 0.05)
        out.append(rows[:need])
        need -= len(rows[:need])
    return np.concatenate(out) if out else np.zeros((0, LINEUP_SIZE), dtype=int)


def _sample_once(
    slate: Slate, rates: FieldRates, n: int, weights: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    s = slate
    b = _Batch(s, n)
    pos, team, opp = s.position, s.team, s.opp
    is_qb = pos == "QB"
    catcher = np.isin(pos, PASS_CATCHERS)
    offense = np.isin(pos, FLEX_POSITIONS)

    target_left = rng.choice(np.asarray(rates.unspent, dtype=float), size=n)
    qb = _pick(weights, np.broadcast_to(is_qb, (n, len(s))) & b.fits(target_left), rng)
    alive = qb >= 0
    b.take(b.rows, qb)
    qb_team = np.where(alive, team[np.maximum(qb, 0)], "")
    qb_opp = np.where(alive, opp[np.maximum(qb, 0)], "")
    same = team[None, :] == qb_team[:, None]
    facing = team[None, :] == qb_opp[:, None]

    stacked = rng.random(n) < rates.stack1
    two = stacked & (rng.random(n) < rates.stack2_given_stack1)
    bb_rate = np.where(stacked, rates.bringback_stacked, rates.bringback_unstacked)
    bring = rng.random(n) < bb_rate

    for k in range(2):
        want = stacked if k == 0 else two
        pick = _pick(weights, b.fits(target_left) & same & catcher[None, :] & want[:, None], rng)
        if k == 0:
            stacked &= pick >= 0  # a QB whose team has no rosterable pass catcher cannot stack
        b.take(b.rows, np.where(want, pick, -1))
    pick = _pick(weights, b.fits(target_left) & facing & offense[None, :] & bring[:, None], rng)
    bring &= pick >= 0
    b.take(b.rows, np.where(bring, pick, -1))
    # no more pass catchers of the QB's team unless stacked; no more of the opponent's offence at all
    b.banned |= (same & catcher[None, :] & ~stacked[:, None]) | (facing & offense[None, :])
    b.banned |= same & catcher[None, :] & ~two[:, None] & stacked[:, None] & ~b.chosen

    for _ in range(LINEUP_SIZE):
        left = b.slots_left()
        todo = left > 0
        if not todo.any():
            break
        steered = b.fits(target_left) & todo[:, None]
        loose = b.fits() & todo[:, None]
        has = steered.any(axis=1)
        pick = _pick(weights, np.where(has[:, None], steered, loose), rng)
        b.take(b.rows, np.where(todo, pick, -1))

    done = (b.slots_left() == 0) & alive & (b.budget >= 0) & (b.budget <= target_left + UNSPENT_SLACK)
    lineups = np.sort(np.nonzero(b.chosen[done])[1].reshape(-1, LINEUP_SIZE), axis=1)
    return lineups


def chalk_lineups(slate: Slate, k: int = HERD_K, own: np.ndarray | None = None) -> np.ndarray:
    """The `k` legal lineups with the highest product of ownership (`own`, default the slate's projected
    shares), best first: (k x 9) sorted player indices. Exact: each is a MILP on sum(log own) under the roster
    rules and the cap, with a no-good cut for every lineup already found."""
    from scipy.optimize import Bounds, LinearConstraint, milp

    o = np.maximum(slate.own if own is None else np.asarray(own, dtype=float), OWN_FLOOR)
    pos = slate.position
    rows = [np.ones(len(slate))]
    lo, hi = [float(LINEUP_SIZE)], [float(LINEUP_SIZE)]
    for p in POSITIONS:
        rows.append((pos == p).astype(float))
        lo.append(float(NEEDS[p]))
        hi.append(float(NEEDS[p] + (FLEX_SLOTS if p in FLEX_POSITIONS else 0)))
    rows.append(slate.salary.astype(float))
    lo.append(0.0)
    hi.append(float(SALARY_CAP))
    found: list[np.ndarray] = []
    for _ in range(k):
        cuts = [np.isin(np.arange(len(slate)), f).astype(float) for f in found]
        a = np.vstack(rows + cuts)
        cons = LinearConstraint(a, lo + [-np.inf] * len(cuts), hi + [LINEUP_SIZE - 1.0] * len(cuts))
        res = milp(-np.log(o), constraints=cons, integrality=np.ones(len(slate)), bounds=Bounds(0, 1))
        if res.x is None:
            break
        found.append(np.sort(np.nonzero(res.x > 0.5)[0]))
    return np.array(found, dtype=int).reshape(-1, LINEUP_SIZE)


def herd_lineups(chalk: np.ndarray, herd: HerdModel, n: int, rng: np.random.Generator) -> np.ndarray:
    """The herd's share of a field of `n`: `round(share * n)` entries spread over the chalk lineups by the
    model's weights (a multinomial draw)."""
    m = int(round(herd.share * n))
    if m <= 0 or len(chalk) == 0:
        return np.zeros((0, LINEUP_SIZE), dtype=int)
    counts = rng.multinomial(m, herd.weights(len(chalk)))
    return np.repeat(chalk, counts, axis=0)


def generate_field(
    slate: Slate,
    rates: FieldRates,
    n: int,
    *,
    herd: HerdModel | None = None,
    chalk: np.ndarray | None = None,
    seed: int = 0,
    rake_rounds: int = RAKE_ROUNDS,
    rake_size: int | None = None,
    targets: np.ndarray | None = None,
) -> Field:
    """A field of `n` lineups: the herd's entries on the chalk lineups (`chalk`, default `chalk_lineups`) and
    the sampler's for the rest, raked so the whole field holds each player at `targets` (default
    `slate.targets()`). Each raking round samples `rake_size` lineups (default `RAKE_SIZE`) and multiplies
    each player's weight by what the sampler must hold over what it held; the field returned is a fresh draw
    with the final weights."""
    rng = np.random.default_rng(seed)
    t = slate.targets() if targets is None else np.asarray(targets, dtype=float)
    herd = herd or HerdModel()
    if herd.share > 0 and chalk is None:
        chalk = chalk_lineups(slate, herd.k)
    herd_rows = herd_lineups(chalk, herd, n, rng) if herd.share > 0 else np.zeros((0, LINEUP_SIZE), dtype=int)
    rest = n - len(herd_rows)
    held = np.bincount(herd_rows.ravel(), minlength=len(slate)) / max(n, 1)
    need = np.maximum(t - held, 0.0) * n / max(rest, 1)  # what the sampler's lineups must hold
    size = min(rake_size or RAKE_SIZE, max(rest, 1000))
    w = rake_weights(slate, rates, need, rng, rake_rounds=rake_rounds, size=size)
    lineups = sample_lineups(slate, rates, rest, w, rng) if rest > 0 else herd_rows[:0]
    return Field(slate=slate, lineups=np.concatenate([herd_rows, lineups]), weights=w)


def rake_weights(
    slate: Slate,
    rates: FieldRates,
    need: np.ndarray,
    rng: np.random.Generator,
    *,
    rake_rounds: int = RAKE_ROUNDS,
    size: int = RAKE_SIZE,
) -> np.ndarray:
    """The sampler's per-player weights after raking toward `need` (each player's share of the sampler's
    lineups): each round samples `size` lineups and multiplies each weight by need over the share held."""
    w = np.maximum(need, 1e-6).copy()
    for _ in range(rake_rounds):
        lineups = sample_lineups(slate, rates, size, w, rng)
        got = np.bincount(lineups.ravel(), minlength=len(slate)) / max(len(lineups), 1)
        ratio = np.where(got > 0, need / np.maximum(got, 1e-9), 2.0)
        w = np.maximum(w * np.clip(ratio, 0.2, 5.0) ** RAKE_POWER, 1e-9)
    return w


def model_copies(field: Field, own: Mapping[str, float] | np.ndarray, model, entries: int) -> np.ndarray:
    """The dup model's expected copies for each generated lineup in a contest of `entries`, on the ownership
    `own` (the projected shares the model was fitted on)."""
    shares = np.asarray(own, dtype=float) if not isinstance(own, Mapping) else None
    if shares is None:
        shares = np.array([float(own.get(n, 0.0)) for n in field.slate.names])
    return np.array([model.expected(shares[row], entries) for row in field.lineups])


def lines(scores: np.ndarray, entries: int, places_paid: int, top_fraction: float = 0.01) -> dict[str, float]:
    """The field mean, the cash line (the score at the last paid place) and the top-1% line (the score at
    place `top_fraction * entries`) of one field's scores, places scaled from `len(scores)` to `entries`."""
    srt = np.sort(np.asarray(scores, dtype=float))[::-1]
    n = len(srt)

    def at(place: int) -> float:
        k = int(np.clip(round(place * n / entries) - 1, 0, n - 1))
        return float(srt[k])

    return {
        "mean": float(srt.mean()),
        "cash_line": at(max(1, places_paid)) if places_paid else float("nan"),
        "top1": at(max(1, round(top_fraction * entries))),
    }


def lineup_indices(slate: Slate, names: Sequence[str]) -> np.ndarray | None:
    """The slate indices of nine named players (None if any is not on the slate)."""
    where = {n: i for i, n in enumerate(slate.names)}
    idx = [where.get(n) for n in names]
    return None if any(i is None for i in idx) else np.sort(np.array(idx))
