"""R6 on the sheet: the 12 definitions load from the research JSON, boundaries use a 1e-9 tolerance, one Proj
chip per player (both directions: none), windows match R6's own code, no arrow without 9 earlier games."""

import json

import numpy as np
import pandas as pd
import pytest

from dfs import usage_r6 as u
from dfs.research import r6_features as r6
from dfs.research_constants import RESEARCH_DIR, ResearchConstantsError

CHIPS = u.load_chips()
BY_ID = {c.id: c for c in CHIPS}


def _features(rows):
    base = {"gsis_id": "p", "team": "AAA", "position": "WR", "earlier_games": 12}
    frame = pd.DataFrame([{**base, "gsis_id": f"p{i}", **row} for i, row in enumerate(rows)])
    for chip in CHIPS:
        for cond in chip.conditions:
            if cond.feature not in frame:
                frame[cond.feature] = np.nan
    return frame


def test_exactly_the_twelve_recommended_chips_load_from_the_json():
    data = json.loads((RESEARCH_DIR / "usage_signals.json").read_text())
    assert [c.id for c in CHIPS] == data["recommended_chips"] and len(CHIPS) == 12
    # the redundant RB "change up" row is built once: targets per game, not high-value touches
    ids = {c.id for c in CHIPS}
    assert "tgt_pg|RB|change_up" in ids and "hvt|RB|change_up" not in ids
    snap_wr = BY_ID["snap_pct|WR|change_down"]
    assert snap_wr.conditions[0] == u.Condition("snap_pct_chg", "<=", -0.11) and snap_wr.direction == u.FADE
    assert BY_ID["tgt_pg|TE|change_down"].direction == u.BUMP  # a role loss that beats projection
    assert {c.id for c in CHIPS if c.weaker} == {
        "ez_tgt|WR|change_down",
        "ez_tgt|WR|change_up",
        "snap_pct|RB|change_down",
        "adot|WR|level_hi",
        "deep_tgt|TE|change_down",
    }


def test_a_missing_file_or_chip_is_a_named_error_not_a_guess(tmp_path):
    with pytest.raises(ResearchConstantsError, match="usage_signals.json"):
        u.load_chips(tmp_path)
    (tmp_path / "usage_signals.json").write_text(
        json.dumps(
            {
                "metadata": {"test_seasons": [2022, 2025]},
                "recommended_chips": ["x|WR|level_hi"],
                "signals": [],
            }
        )
    )
    with pytest.raises(ResearchConstantsError, match="x|WR|level_hi"):
        u.load_chips(tmp_path)


def test_thresholds_are_inclusive_with_a_tolerance_for_per_game_counts_in_sixths():
    te_down = BY_ID["ez_tgt|TE|change_down"]  # end-zone targets/G (L3 - prior6) <= -0.5
    fire = _features(
        [
            {"position": "TE", "ez_tgt_pg_chg": -0.5},
            {"position": "TE", "ez_tgt_pg_chg": -0.5000000001},  # computed in sixths
            {"position": "TE", "ez_tgt_pg_chg": -0.4999},
            {"position": "TE", "ez_tgt_pg_chg": np.nan},
            {"position": "WR", "ez_tgt_pg_chg": -0.9},  # wrong position for this row
        ]
    )
    mask = u.chip_masks(fire, [te_down])[te_down.id].tolist()
    assert mask == [True, True, False, False, False]
    rb_up = BY_ID["tgt_pg|RB|change_up"]  # >= 1.2
    mask = u.chip_masks(
        _features([{"position": "RB", "tgt_pg_chg": 1.2}, {"position": "RB", "tgt_pg_chg": 1.1999}]), [rb_up]
    )
    assert mask[rb_up.id].tolist() == [True, False]
    level = BY_ID["hvt|RB|level_hi"]  # hvt_pg_l3 >= 4.7
    mask = u.chip_masks(_features([{"position": "RB", "hvt_pg_l3": 4.7}]), [level])
    assert mask[level.id].tolist() == [True]


def test_one_chip_per_player_and_both_directions_means_no_chip():
    # An RB can be a FADE (high-value touches 4.7+) and a BUMP at once only across rows; build that directly.
    fade = u.PlayerSignal(fade=("hvt|RB|level_hi",), bump=())
    bump = u.PlayerSignal(fade=(), bump=("tgt_pg|TE|change_down",))
    both = u.PlayerSignal(fade=("hvt|RB|level_hi",), bump=("tgt_pg|TE|change_down",))
    assert (fade.chip, bump.chip, both.chip) == ("Proj ▼", "Proj ▲", "")
    assert both.conflicted and not fade.conflicted
    assert u.proj_token(fade, BY_ID) == "Proj ▼"  # a strong-evidence row: no mark
    weak = u.PlayerSignal(fade=("adot|WR|level_hi",), bump=())
    assert u.weaker_only(weak, BY_ID) and u.proj_token(weak, BY_ID) == "Proj ▼?"
    mixed = u.PlayerSignal(fade=("adot|WR|level_hi", "snap_pct|WR|change_down"), bump=())
    assert not u.weaker_only(mixed, BY_ID) and u.proj_token(mixed, BY_ID) == "Proj ▼"
    assert u.proj_token(both, BY_ID) == ""
    why = u.signal_why(both, BY_ID)
    assert "Signals point both ways, so no Proj chip" in why


def test_slate_signals_cover_the_pool_only_and_name_every_signal_that_fired():
    feats = _features(
        [
            {"position": "TE", "tgt_pg_chg": 2.3, "ez_tgt_pg_chg": 0.0},
            {"position": "TE", "tgt_pg_chg": 2.3},  # same flag, not in the pool
            {"position": "RB", "hvt_pg_l3": 5.0, "tgt_pg_chg": 1.5},
        ]
    )
    found = u.slate_signals(feats, CHIPS, {"p0", "p2"})
    assert set(found) == {"p0", "p2"}
    assert found["p0"].fade == ("tgt_pg|TE|change_up",) and found["p0"].chip == "Proj ▼"
    assert set(found["p2"].fade) == {"hvt|RB|level_hi", "tgt_pg|RB|change_up"}
    text = u.signal_why(found["p2"], BY_ID)
    assert "high-value touches 4.7+/game over the last 3" in text and "targets up 1.2+/game" in text


def test_the_why_says_what_fired_how_big_the_measured_effect_is_and_when_the_evidence_is_weaker():
    te_up = u.chip_why(BY_ID["tgt_pg|TE|change_up"])
    assert te_up == (
        "TE targets up 2.2+/game over the last 3 (usually fades back: −0.8 pts vs the research model's "
        "projection, 2022–25)"
    )
    weak = u.chip_why(BY_ID["adot|WR|level_hi"])
    assert weak.endswith("; weaker evidence") and "18+ yards" in weak
    assert "snap share down 11+ points" in u.chip_why(BY_ID["snap_pct|WR|change_down"])


def test_edge_tokens_keep_the_proj_chips_whole():
    assert u.edge_tokens("INJ+ FADE↓ Proj ▼") == ["INJ+", "FADE↓", "Proj ▼"]
    assert u.edge_tokens("Proj ▲? USAGE↑") == ["Proj ▲?", "USAGE↑"]
    assert u.edge_tokens(None) == [] and u.edge_tokens(float("nan")) == [] and u.edge_tokens("") == []


# ---- windows: parity with R6's own code, no lookahead, byes skipped ----------------------------------


def _games(n_games=12, players=("P1", "P2"), seed=3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for pid in players:
        for i in range(n_games):
            season, week = (2025, i + 1) if i < 8 else (2026, i - 7)
            row = {"gsis_id": pid, "season": season, "week": week, "team": "AAA", "position": "WR"}
            row |= {c: float(rng.integers(0, 9)) + float(rng.random()) for c in _num_cols()}
            row["targets"] = float(rng.integers(1, 12))
            row["team_gl_carries"] = float(rng.integers(1, 5))
            rows.append(row)
    g = pd.DataFrame(rows)
    g["t"] = g["season"] * 100 + g["week"]
    return g


def _num_cols():
    return sorted({c for num, den in r6.METRICS.values() for c in (num, den) if c})


def test_features_as_of_match_the_research_windows_exactly_and_cross_the_season_boundary():
    games = _games()  # 8 games in 2025, 4 in 2026: week 5 of 2026 is the 13th game
    feats = u.features_as_of(games, 2026, 5)
    research = r6.window_features(
        pd.concat(
            [
                games,
                games[["gsis_id", "team", "position"]]
                .drop_duplicates()
                .assign(season=2026, week=5, t=202605),
            ],
            ignore_index=True,
        ),
        r6.METRICS,
    )
    slate = research[
        pd.concat([games, games[["gsis_id"]].drop_duplicates().assign(t=202605)], ignore_index=True)["t"]
        == 202605
    ]
    for metric in ("tgt_pg", "snap_pct", "hvt_pg", "ez_tgt_pg"):
        got = feats.set_index("gsis_id")[f"{metric}_chg"]
        want = research.loc[slate.index, f"{metric}_chg"].to_numpy()
        assert got.to_numpy() == pytest.approx(want)
    p1 = games[games["gsis_id"] == "P1"].sort_values("t")
    one = feats.set_index("gsis_id").loc["P1"]
    assert one["tgt_pg_l3"] == pytest.approx(p1["targets"].iloc[-3:].mean())
    assert one["tgt_pg_prior"] == pytest.approx(p1["targets"].iloc[-9:-3].mean())
    assert one["earlier_games"] == 12


def test_a_game_at_or_after_the_slate_never_reaches_its_own_features():
    games = _games()
    played = games.copy()
    played.loc[(played["season"] == 2026) & (played["week"] == 4), "targets"] = 99.0  # the last REAL game
    slate_week_row = (
        played[played["gsis_id"] == "P1"].iloc[[-1]].assign(season=2026, week=5, t=202605, targets=999.0)
    )
    with_leak = pd.concat([played, slate_week_row], ignore_index=True)
    a = u.features_as_of(played, 2026, 5).set_index("gsis_id").loc["P1", "tgt_pg_l3"]
    b = u.features_as_of(with_leak, 2026, 5).set_index("gsis_id").loc["P1", "tgt_pg_l3"]
    assert a == pytest.approx(
        b
    )  # the Thursday game already played in the slate week is not in its own window


def test_a_player_with_fewer_than_nine_earlier_games_has_no_change_and_no_arrow():
    games = _games(n_games=8, players=("NEW",))
    feats = u.features_as_of(games, 2026, 1)
    assert feats.iloc[0]["earlier_games"] == 8 and np.isnan(feats.iloc[0]["tgt_pg_chg"])
    bands = u.load_trend_bands()
    assert u.compute_trends(feats, bands, min_games=u.min_earlier_games()).empty  # "not enough games"
    long = u.features_as_of(_games(n_games=12, players=("OLD",)), 2026, 5)
    assert not u.compute_trends(long, bands, min_games=u.min_earlier_games()).empty


def test_arrows_use_the_json_threshold_per_metric_and_position_and_the_flag_rate_in_the_why():
    bands = u.load_trend_bands()
    tgt_wr = bands[("tgt_share", "WR")]
    assert tgt_wr.threshold == pytest.approx(0.0836) and tgt_wr.flag_rate == pytest.approx(0.15)
    feats = _features(
        [
            {"position": "WR", "tgt_share_l3": 0.30, "tgt_share_prior": 0.20, "tgt_share_chg": 0.10},
            {"position": "WR", "tgt_share_l3": 0.20, "tgt_share_prior": 0.28, "tgt_share_chg": -0.0836},
            {"position": "WR", "tgt_share_l3": 0.20, "tgt_share_prior": 0.25, "tgt_share_chg": -0.05},
            {"position": "RB", "tgt_share_l3": 0.15, "tgt_share_prior": 0.08, "tgt_share_chg": 0.07},
        ]
    )
    for column in [f"{k}_{s}" for k in set(u.TREND_FEATURE.values()) for s in ("l3", "prior", "chg")]:
        if column not in feats:
            feats[column] = np.nan
    trends = u.compute_trends(feats, bands, min_games=9)
    tgt = trends[trends["Metric"] == "Tgt%"].set_index("GsisId")
    assert tgt.loc["p0", "Direction"] == "▲" and tgt.loc["p1", "Direction"] == "▼"  # the boundary counts
    assert tgt.loc["p2", "Direction"] == "" and tgt.loc["p3", "Direction"] == "▲"  # RB band is 0.0623
    why = u.trend_why(tgt.loc["p0"])
    assert why.startswith("Tgt% 20% → 30% over the last 3 (▲, a bigger jump than 85% of weeks).")
    assert why.endswith("Historically projections over-react to jumps like this.")
    down = u.trend_why(tgt.loc["p1"])
    assert "a bigger drop than 85% of weeks" in down and "cut too deep after drops" in down
