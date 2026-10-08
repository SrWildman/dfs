"""R6 on the sheet: the usage-trend arrows and the 12 `Proj ▲ / Proj ▼` signals, from the research's own
windows.

R6 (`docs/RESEARCH.md`, "R6. Usage signals, as levels and as changes") found that most "high usage" effects
are the research model over-projecting its own top decile, and that what is left is **spikes and slumps fade
back**: when a player's recent usage jumps, projections follow it too far; when it drops, they cut too deep.
It measured that against UM (the research projection), NOT against TFFB or CalPts, so everything built from
it here is **context, tracked in Model Check** until the 2026 weeks show it holds against CalPts. A
rising-usage `▲` is not a reason to bump a player.

**Nothing is typed here.** The 12 signal definitions are `recommended_chips` in
`models/research/usage_signals.json` (formula, window, threshold, measured effect); the arrow thresholds are
`models/research/trend_bands.json`, per metric and position. Re-running the research and committing the files
changes the sheet with no code change (`research_constants.ResearchConstantsError` names a missing file or
key).

**Windows** are R6's own (`dfs.research.r6_features`, imported, not copied; `window_features` is the code that
produced the research): L3 = the player's last 3 games played, prior6 = the 6 games before those, across
seasons (a week-1 L3 uses last season's games), byes skipped (a "game" is a stats row), a change needs 9
earlier games and a level 3. Every feature of a game is built from EARLIER games only. The slate's features
are those of the game about to be played: `features_as_of` drops any row at or after the slate week (a
Thursday game already played must not leak into its own prediction) and appends one empty row per player for
the slate game.

**Inputs** (public, free): nflverse `stats_player` (`dfs.model` cache), the reduced play-by-play, snap
counts and the PFR -> gsis crosswalk (`data/research_cache/`). Completed seasons are fetched once; the
current season is refreshed on every non-live sync (`ensure_data`).
"""

from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from dfs.model import data as model_data
from dfs.research import data as research_data
from dfs.research import r6_features as r6
from dfs.research_constants import ResearchConstantsError, _need, _read_json

SIGNALS_FILE = "usage_signals.json"
BANDS_FILE = "trend_bands.json"
TOLERANCE = 1e-9  # per-game counts move in sixths: `<= -0.5` must include -0.5000000001
UP, DOWN = "▲", "▼"
PROJ_DOWN, PROJ_UP = f"Proj {DOWN}", f"Proj {UP}"
FADE, BUMP = "FADE", "BUMP"

# What the sheet's trend arrows show: the label, the key of `trend_bands.json`'s `metrics`, the unit and the
# positions (the brief's list). Thresholds are NOT here: they come from the JSON per metric and position.
TREND_METRICS = (
    ("Tgt%", "tgt_share", "share", ("WR", "TE", "RB")),
    ("WOPR", "wopr", "index", ("WR", "TE")),
    ("Air-yards share", "ay_share", "share", ("WR", "TE")),
    ("Rush%", "carry_share", "share", ("RB",)),
    ("Rec/G", "rec_pg", "per game", ("RB",)),
    ("RZ/G", "rz_pg", "per game", ("RB", "WR", "TE")),
    ("HVT/G", "hvt_pg", "per game", ("RB", "WR", "TE")),
    ("Snap%", "snap_pct", "share", ("RB", "WR", "TE")),
)
# The r6_features metric behind each trend key (a trend band's metric key is not always the feature's name).
TREND_FEATURE = {
    "tgt_share": "tgt_share",
    "wopr": "wopr",
    "ay_share": "ay_share",
    "carry_share": "carry_share",
    "rec_pg": "rec_pg",
    "rz_pg": "rz_pg",
    "hvt_pg": "hvt_pg",
    "snap_pct": "snap_pct",
}
TREND_POSITIONS = ("WR", "TE", "RB")

# Plain words for each candidate: (noun phrase, unit written after a threshold). A signal's `Why` is built
# from these and the numbers in the JSON, so a new threshold changes the text with no code change.
PLAIN = {
    "snap_pct": ("snap share", " points"),
    "ez_tgt": ("end-zone targets", "/game"),
    "tgt_pg": ("targets", "/game"),
    "adot": ("average depth of target (aDOT)", " yards"),
    "hvt": ("high-value touches", "/game"),
    "deep_tgt": ("deep targets", "/game"),
}
READS = {
    ("change_up", FADE): "usually fades back",
    ("change_down", BUMP): "usually bounces back",
    ("change_down", FADE): "usually keeps falling short",
    ("change_up", BUMP): "usually keeps beating projection",
    ("level_hi", FADE): "usually falls short of projection",
    ("level_hi", BUMP): "usually beats projection",
}
WEAKER = "weaker evidence"


class R6Error(Exception):
    """The R6 inputs could not be fetched or read (reported as a warning; the rest of the sync stands)."""


# ---------------------------------------------------------------------------------------------
# The research files
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    feature: str
    op: str
    value: float

    def holds(self, series: pd.Series) -> pd.Series:
        """True where the feature is present and satisfies `op value`, with `TOLERANCE` on the boundary."""
        compare = {">=": operator.ge, "<=": operator.le}[self.op]
        edge = self.value - TOLERANCE if self.op == ">=" else self.value + TOLERANCE
        return series.notna() & compare(series.astype(float), edge)


@dataclass(frozen=True)
class Chip:
    """One of the 12 recommended R6 signals."""

    id: str
    candidate: str
    position: str
    shape: str  # level_hi, change_up or change_down
    conditions: tuple[Condition, ...]
    definition: str
    direction: str  # FADE (misses projection) or BUMP (beats it)
    effect_fit: float
    effect_test: float
    effect_um_test: float  # the UM-matched test effect: the honest one, quoted in Why
    n_test: int
    weaker: bool  # the fit interval did not exclude 0: only 2022-25 clearly supports it
    test_seasons: tuple[int, int]

    @property
    def threshold(self) -> float:
        return self.conditions[0].value


def load_chips(directory: Path | None = None) -> list[Chip]:
    """The signals listed under `recommended_chips` in `usage_signals.json`, in that order."""
    data = _read_json(SIGNALS_FILE, directory)
    wanted = _need(data, "recommended_chips", where=SIGNALS_FILE)
    by_id = {s["id"]: s for s in _need(data, "signals", where=SIGNALS_FILE)}
    seasons = _need(data, "metadata", "test_seasons", where=SIGNALS_FILE)
    chips = []
    for chip_id in wanted:
        if chip_id not in by_id:
            raise ResearchConstantsError(f"{SIGNALS_FILE}: recommended chip {chip_id!r} is not in `signals`")
        s = by_id[chip_id]
        rule = _need(s, "chosen", "rule", where=f"{SIGNALS_FILE}:{chip_id}")
        test = _need(s, "test", "diff", where=f"{SIGNALS_FILE}:{chip_id}")
        chips.append(
            Chip(
                id=chip_id,
                candidate=s["candidate"],
                position=s["position"],
                shape=s["shape"],
                conditions=tuple(Condition(c["feature"], c["op"], float(c["value"])) for c in rule),
                definition=_need(s, "chosen", "definition", where=f"{SIGNALS_FILE}:{chip_id}"),
                direction=FADE if float(test) < 0 else BUMP,
                effect_fit=float(s["fit"]["diff"]),
                effect_test=float(test),
                effect_um_test=float(
                    _need(s, "um_matched", "test", "diff", where=f"{SIGNALS_FILE}:{chip_id}")
                ),
                n_test=int(s["test"]["n_flagged"]),
                weaker=not bool(s.get("fit_interval_excludes_zero", False)),
                test_seasons=(int(seasons[0]), int(seasons[1])),
            )
        )
    return chips


@dataclass(frozen=True)
class TrendBand:
    threshold: float
    flag_rate: float


def load_trend_bands(directory: Path | None = None) -> dict[tuple[str, str], TrendBand]:
    """`(metric key, position) -> TrendBand` from `trend_bands.json`'s `recommended.per_position` (the
    thresholds
    measured over the UM-projected player-games, i.e. who the sheet lists)."""
    data = _read_json(BANDS_FILE, directory)
    out = {}
    for key, block in _need(data, "metrics", where=BANDS_FILE).items():
        per_position = _need(block, "recommended", "per_position", where=f"{BANDS_FILE}:{key}")
        for position, cell in per_position.items():
            out[(key, position)] = TrendBand(float(cell["threshold"]), float(cell["flag_rate"]))
    return out


def min_earlier_games(directory: Path | None = None) -> int:
    """The earlier games an arrow needs (9: three for L3 and six for prior6), from `trend_bands.json`."""
    return int(_need(_read_json(BANDS_FILE, directory), "window", "min_prior_games", where=BANDS_FILE))


# ---------------------------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------------------------


def ensure_data(season: int, *, refresh: bool = True, log=lambda msg: None) -> None:
    """Make the R6 inputs available for `season` and the one before it. The earlier season is fetched once;
    the
    current one is refreshed when `refresh` (it grows every week). Raises `R6Error` on any failure."""
    try:
        for s in (season - 1, season):
            current = s == season
            if (refresh and current) or not model_data.cache_path("stats_player", s).exists():
                log(f"fetching stats_player {s}")
                model_data.fetch_season_file("stats_player", s)
            if (refresh and current) or not research_data.pbp_path(s).exists():
                log(f"fetching pbp {s}")
                research_data.fetch_pbp(s)
            if (refresh and current) or not research_data.snaps_path(s).exists():
                log(f"fetching snap counts {s}")
                research_data.fetch_snaps(s)
        if refresh or not research_data.players_path().exists():
            log("fetching the players crosswalk")
            research_data.fetch_players()
    except (research_data.ResearchDataError, model_data.ModelDataError) as e:
        raise R6Error(str(e)) from e


def build_games(seasons: list[int]) -> pd.DataFrame:
    """One row per skill-position player-game of `seasons` with every per-game measure the windows read."""
    try:
        return r6.player_game_frame(
            research_data.read_stats_player(seasons),
            research_data.read_pbp(seasons),
            research_data.read_snaps(seasons),
            research_data.read_players(),
        )
    except (research_data.ResearchDataError, model_data.ModelDataError) as e:
        raise R6Error(str(e)) from e


FEATURE_KEYS = ["gsis_id", "team", "position"]


def features_as_of(games: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """Every player's R6 window features for the game of `(season, week)`: one row per player with a game
    before it, `<m>_l3 / _prior / _chg` for every metric plus `earlier_games` (how many games before it he
    has played).
    Rows at or after the slate game are dropped first (no lookahead)."""
    slate_t = season * 100 + week
    past = games[games["t"] < slate_t]
    if past.empty:
        return pd.DataFrame(columns=[*FEATURE_KEYS, "earlier_games"])
    last = past.sort_values("t").groupby("gsis_id", as_index=False).tail(1)
    slate_rows = last[FEATURE_KEYS].assign(season=season, week=week, t=slate_t)
    frame = pd.concat([past, slate_rows], ignore_index=True)
    feats = r6.window_features(frame, r6.METRICS)
    out = pd.concat([frame[FEATURE_KEYS + ["t"]], feats], axis=1)
    out = out[out["t"] == slate_t].drop(columns="t")
    out["earlier_games"] = out["gsis_id"].map(past.groupby("gsis_id").size())
    return out.reset_index(drop=True)


def historical_features(games: pd.DataFrame) -> pd.DataFrame:
    """The window features of every player-game in `games`, each built from earlier games only (what the
    signal
    would have shown before that game). Keyed by `gsis_id`, `season`, `week`."""
    feats = r6.window_features(games, r6.METRICS)
    return pd.concat([games[[*FEATURE_KEYS, "season", "week"]], feats], axis=1)


# ---------------------------------------------------------------------------------------------
# The 12 signals
# ---------------------------------------------------------------------------------------------


def chip_masks(features: pd.DataFrame, chips: list[Chip]) -> pd.DataFrame:
    """A boolean column per chip id: True where the player's position is the chip's and every condition holds
    (a missing feature never fires)."""
    out = pd.DataFrame(index=features.index)
    for chip in chips:
        mask = features["position"] == chip.position
        for cond in chip.conditions:
            mask = mask & cond.holds(features[cond.feature])
        out[chip.id] = mask
    return out


@dataclass(frozen=True)
class PlayerSignal:
    """What fired for one player: the chip ids by direction, and the resulting single chip (`Proj ▼`,
    `Proj ▲`, or "" when nothing fired or both directions did)."""

    fade: tuple[str, ...]
    bump: tuple[str, ...]

    @property
    def chip(self) -> str:
        if self.fade and not self.bump:
            return PROJ_DOWN
        if self.bump and not self.fade:
            return PROJ_UP
        return ""

    @property
    def conflicted(self) -> bool:
        return bool(self.fade and self.bump)

    @property
    def fired(self) -> tuple[str, ...]:
        return (*self.fade, *self.bump)


def player_signals(masks: pd.DataFrame, chips: list[Chip]) -> dict[str, PlayerSignal]:
    """`gsis_id -> PlayerSignal` for every player with at least one chip firing; `masks` is indexed like the
    features it came from, with `gsis_id` supplied by the caller through the index."""
    direction = {c.id: c.direction for c in chips}
    out: dict[str, PlayerSignal] = {}
    for gsis, row in masks.iterrows():
        fired = [cid for cid in masks.columns if row[cid]]
        if fired:
            out[gsis] = PlayerSignal(
                fade=tuple(c for c in fired if direction[c] == FADE),
                bump=tuple(c for c in fired if direction[c] == BUMP),
            )
    return out


def _fmt(value: float) -> str:
    return f"{abs(value):g}"


def threshold_text(chip: Chip) -> str:
    """The rule in plain words: `targets up 2.2+/game over the last 3`, `snap share down 11+ points over the
    last 3`, `high-value touches 4.7+/game over the last 3`."""
    noun, unit = PLAIN.get(chip.candidate, (chip.candidate, ""))
    value = abs(chip.threshold) * (100 if chip.candidate == "snap_pct" else 1)  # a 0-1 share, in points
    amount = f"{_fmt(value)}+{unit}"
    if chip.shape == "level_hi":
        return f"{noun} {amount} over the last 3"
    return f"{noun} {'up' if chip.shape == 'change_up' else 'down'} {amount} over the last 3"


def chip_label(chip: Chip) -> str:
    """A short name for a signal, for Model Check: `WR snap share down 11+ points`."""
    return f"{chip.position} {threshold_text(chip).removesuffix(' over the last 3')}"


def chip_why(chip: Chip) -> str:
    """One signal's reason: `TE targets up 2.2+/game over the last 3 (usually fades back: -0.8 pts vs the
    research model's projection, 2022-25)`, plus `weaker evidence` for the test-led rows."""
    reading = READS[(chip.shape, chip.direction)]
    first, last = chip.test_seasons
    effect = f"{chip.effect_um_test:+.1f}".replace("-", "−")
    text = (
        f"{chip.position} {threshold_text(chip)} ({reading}: {effect} pts vs the research model's "
        "projection, "
        f"{first}–{str(last)[-2:]})"
    )
    return text + (f"; {WEAKER}" if chip.weaker else "")


def signal_why(signal: PlayerSignal, chips: dict[str, Chip]) -> str:
    """Every signal that fired, in plain words, one `Why`. Both directions: no chip, and it says so."""
    parts = [chip_why(chips[c]) for c in signal.fired]
    text = "; ".join(parts)
    if signal.conflicted:
        text += ". Signals point both ways, so no Proj chip"
    return text


WEAK_MARK = "?"  # a Proj chip resting only on the test-led (weaker evidence) rows reads `Proj ▼?`
TOKEN_RE = re.compile(r"Proj [▲▼]\??|\S+")


def edge_tokens(text: object) -> list[str]:
    """The tokens of an `Edge` cell: whitespace-separated, except that `Proj ▼` / `Proj ▲` (and the `?` form)
    contain a space and stay whole."""
    return TOKEN_RE.findall("" if text is None or (isinstance(text, float) and text != text) else str(text))


def weaker_only(signal: PlayerSignal, chips: dict[str, Chip]) -> bool:
    """True when every signal behind the chip is a test-led (weaker evidence) row: the chip is drawn muted."""
    pool = signal.fade if signal.fade and not signal.bump else signal.bump
    return bool(pool) and all(chips[c].weaker for c in pool)


def proj_token(signal: PlayerSignal, chips: dict[str, Chip]) -> str:
    """The single `Edge` token for a player: `Proj ▼` / `Proj ▲`, with `?` appended when only weaker-evidence
    rows support it; "" when nothing fired or the two directions both did."""
    chip = signal.chip
    return chip + WEAK_MARK if chip and weaker_only(signal, chips) else chip


def slate_signals(
    features: pd.DataFrame, chips: list[Chip], pool_gsis: set[str] | None = None
) -> dict[str, PlayerSignal]:
    """`gsis_id -> PlayerSignal` for the slate. The signals apply only to players the projection covers
    (R6: "apply only to players UM projects"), so `pool_gsis` limits them to the rosterable pool."""
    feats = features if pool_gsis is None else features[features["gsis_id"].isin(pool_gsis)]
    feats = feats.drop_duplicates("gsis_id")
    masks = chip_masks(feats, chips)
    masks.index = feats["gsis_id"].to_numpy()
    return player_signals(masks, chips)


def history_flags(seasons: list[int], chips: list[Chip] | None = None) -> pd.DataFrame:
    """For Model Check: for every player-game of `seasons`, which of the 12 signals the sheet WOULD have shown
    before it (windows built from earlier games only). Columns `gsis_id`, `season`, `week`, one bool per
    chip id.
    Reads the cached inputs; raises `R6Error` if they are not there."""
    chips = chips if chips is not None else load_chips()
    feats = historical_features(build_games(seasons))
    masks = chip_masks(feats, chips)
    return pd.concat([feats[["gsis_id", "season", "week"]], masks], axis=1)


# ---------------------------------------------------------------------------------------------
# Trend arrows
# ---------------------------------------------------------------------------------------------

TREND_COLUMNS = [
    "GsisId",
    "Position",
    "Metric",
    "Recent",
    "Prior",
    "Change",
    "Threshold",
    "Z",
    "Direction",
    "FlagRate",
    "EarlierGames",
]


def compute_trends(
    features: pd.DataFrame,
    bands: dict[tuple[str, str], TrendBand],
    *,
    min_games: int,
    population: set[str] | None = None,
) -> pd.DataFrame:
    """One row per player and trend metric (`TREND_COLUMNS`): `▲` when `L3 - prior6 >= +threshold`, `▼` when
    `<= -threshold`, the threshold from `trend_bands.json` for that metric and position. A player with fewer
    than
    `min_games` earlier games, or a missing feature, gets no row at all (the tab says "not enough games")."""
    rows = []
    for label, key, _unit, positions in TREND_METRICS:
        feature = TREND_FEATURE[key]
        for position in positions:
            band = bands.get((key, position))
            if band is None:
                continue
            part = features[(features["position"] == position) & (features["earlier_games"] >= min_games)]
            if population is not None:
                part = part[part["gsis_id"].isin(population)]
            chg = part[f"{feature}_chg"]
            part = part[chg.notna()]
            for _, r in part.iterrows():
                change = float(r[f"{feature}_chg"])
                up = change >= band.threshold - TOLERANCE
                down = change <= -band.threshold + TOLERANCE
                rows.append(
                    {
                        "GsisId": r["gsis_id"],
                        "Position": position,
                        "Metric": label,
                        "Recent": float(r[f"{feature}_l3"]),
                        "Prior": float(r[f"{feature}_prior"]),
                        "Change": change,
                        "Threshold": band.threshold,
                        "Z": change / band.threshold,
                        "Direction": UP if up else DOWN if down else "",
                        "FlagRate": band.flag_rate,
                        "EarlierGames": int(r["earlier_games"]),
                    }
                )
    return pd.DataFrame(rows, columns=TREND_COLUMNS)


def trend_unit(metric: str) -> str:
    return next(unit for label, _k, unit, _p in TREND_METRICS if label == metric)


def format_value(metric: str, value: float) -> str:
    """A metric value as the tab shows it: `18%`, `0.62`, `5.3`."""
    unit = trend_unit(metric)
    if unit == "share":
        return f"{value * 100:.0f}%"
    if unit == "index":
        return f"{value:.2f}"
    return f"{value:.1f}"


def format_change(metric: str, change: float) -> str:
    unit = trend_unit(metric)
    if unit == "share":
        return f"{change * 100:+.0f} pts"
    return f"{change:+.2f}" if unit == "index" else f"{change:+.1f}"


def trend_why(row: pd.Series) -> str:
    """`Tgt% 18% → 27% over the last 3 (▲, a bigger jump than 85% of weeks). Historically projections
    over-react to jumps like this.`"""
    metric = row["Metric"]
    arrow = row["Direction"]
    rarity = f"{1 - float(row['FlagRate']):.0%}"
    move = "jump" if arrow == UP else "drop"
    ending = (
        "Historically projections over-react to jumps like this."
        if arrow == UP
        else "Historically projections cut too deep after drops like this."
    )
    return (
        f"{metric} {format_value(metric, row['Prior'])} → {format_value(metric, row['Recent'])} over the "
        "last 3 "
        f"({arrow}, a bigger {move} than {rarity} of weeks). {ending}"
    )


# ---------------------------------------------------------------------------------------------
# The sync's saved table
# ---------------------------------------------------------------------------------------------

SAVED_FILE = "r6_features.csv"


def saved_columns() -> list[str]:
    """The feature columns the sheet reads: every `<m>_l3 / _prior / _chg` of the metrics the trends and the
    12 chips use, plus the keys."""
    metrics = sorted(
        set(TREND_FEATURE.values()) | {"tgt_pg", "adot", "snap_pct", "ez_tgt_pg", "deep_tgt_pg", "hvt_pg"}
    )
    cols = [f"{m}_{s}" for m in metrics for s in ("l3", "prior", "chg")]
    return [*FEATURE_KEYS, "earlier_games", *cols]


def build_saved_table(season: int, week: int, *, log=lambda msg: None, refresh: bool = True) -> pd.DataFrame:
    """The slate's features (`features_as_of`), fetched and built fresh. Raises `R6Error` on a data
    failure."""
    ensure_data(season, refresh=refresh, log=log)
    games = build_games([season - 1, season])
    feats = features_as_of(games, season, week)
    return feats[[c for c in saved_columns() if c in feats.columns]]
