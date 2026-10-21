"""R7: the cell table, its shrinkage and the out-of-sample check, on synthetic event rows with planted
effects (the nflverse-driven builder is covered by R1's tests; the residuals come from the sim frame)."""

import numpy as np
import pandas as pd

from dfs.research import r7_teammate_bump as r7


def make_rows(effect=2.0, n_events=300, n_control=600, seed=0):
    """WR2s in a WR1-out game score `effect` points more (all else noise); each event and control is its own
    game, split across the fit (2014-21) and test (2022-25) seasons."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_events + n_control):
        event = i < n_events
        season = 2014 + (i % 12)
        resid = rng.normal(0, 6) + (effect if event else 0.0)
        rows.append(
            {
                "event": event,
                "abs_label": "WR1" if event else None,
                "label": "WR2",
                "season": season,
                "week": 4 + i % 10,
                "team": "AAA",
                "game_key": f"AAA|{season}|{i}",
                "position": "WR",
                "resid": resid,
            }
        )
        rows.append({**rows[-1], "label": "TE1", "resid": rng.normal(0, 6), "position": "TE"})
    from dfs.research import flags

    return flags.add_split_and_demeaned(pd.DataFrame(rows))


def test_the_cell_effect_recovers_the_planted_bump_and_nothing_for_the_unaffected_role():
    table = r7.cell_effects(make_rows(), absent_roles=("WR1",)).set_index(["absent", "beneficiary"])
    wr2, te1 = table.loc[("WR1", "WR2")], table.loc[("WR1", "TE1")]
    assert abs(wr2["effect"] - 2.0) < 4 * wr2["se"] and wr2["effect"] > 1.0
    assert abs(te1["effect"]) < 3 * te1["se"]
    assert wr2["agree"] and wr2["n_events"] == 300


def test_shrinkage_pulls_a_thin_cell_toward_zero_and_drops_a_cell_the_periods_disagree_on():
    cells = pd.DataFrame(
        {
            "absent": ["WR1"] * 4,
            "beneficiary": ["WR2", "WR3", "TE1", "RB1"],
            "n_events": [300, 20, 300, 5],
            "effect": [2.0, 2.0, 2.0, 2.0],
            "se": [0.4, 2.0, 0.4, 0.4],
            "t": [5.0, 1.0, 5.0, 5.0],
            "agree": [True, True, False, True],
        }
    )
    out = r7.shrink_table(cells, tau2=1.0).set_index("beneficiary")["bump"]
    assert 1.5 < out["WR2"] < 2.0  # lightly shrunk: se 0.4 against tau 1
    assert 0 < out["WR3"] < out["WR2"] / 2  # thin: heavily shrunk
    assert out["TE1"] == 0.0  # fit and test disagree
    assert out["RB1"] == 0.0  # under the minimum events
    only = r7.shrink_table(cells, tau2=1.0, require_agree=False).set_index("beneficiary")["bump"]
    assert only["TE1"] > 0


def test_a_role_the_round_did_not_ask_for_never_gets_a_bump():
    cells = pd.DataFrame(
        {
            "absent": ["WR3", "WR1"],
            "beneficiary": ["WR2", "WR2"],
            "n_events": [200, 200],
            "effect": [3.0, 3.0],
            "se": [0.5, 0.5],
            "t": [6.0, 6.0],
            "agree": [True, True],
        }
    )
    assert r7.shrink_table(cells, tau2=1.0)["bump"].tolist()[0] == 0.0


def test_the_between_cell_variance_is_floored():
    cells = pd.DataFrame({"n_events": [50] * 5, "effect": [1.0] * 5, "se": [0.5] * 5, "t": [2.0] * 5})
    assert r7.between_cell_variance(cells) == r7.TAU2_FLOOR


def test_out_of_sample_the_fitted_bump_beats_no_bump_on_the_test_seasons():
    oos = r7.out_of_sample(make_rows(effect=3.0, n_events=600, n_control=900))
    assert oos["affected"] > 0
    assert oos["sq"]["better"] > 2 * oos["sq"]["se"]
    assert abs(oos["bias_new"]) < abs(oos["bias_old"])


def test_out_of_sample_with_a_planted_nothing_does_not_claim_a_gain():
    oos = r7.out_of_sample(make_rows(effect=0.0, n_events=400, n_control=800), min_abs_t=2.0)
    assert oos["affected"] == 0 or oos["sq"]["better"] < 2 * oos["sq"]["se"]


# --- the builders, on R1's synthetic league ------------------------------------------------------------


def _league(missing=None):
    from .helpers import make_games, make_stats

    games = make_games(bye_week=5)
    return make_stats(games, missing=missing), games


def _resid(sp, bump=3.0):
    """UM residuals for every stats row, with `bump` extra points to AAA's WR2 in week 6."""
    out = sp[["player_id", "season", "week", "position"]].rename(columns={"player_id": "gsis_id"}).copy()
    out["um"], out["dk"] = 10.0, 10.0
    out.loc[(out["gsis_id"] == "AAA-WR2") & (out["week"] == 6), "dk"] += bump
    return out.assign(resid=out["dk"] - out["um"])


def test_event_rows_label_the_teammates_of_the_one_regular_out_and_leave_games_with_two_out_alone():
    sp, games = _league(missing={("AAA", "WR1", 6)})
    rows = r7.event_rows(sp, games, _resid(sp))
    event = rows[rows["event"]]
    assert set(event["team"]) == {"AAA"} and set(event["week"]) == {6}
    assert set(event["abs_label"]) == {"WR1"}
    assert {"WR2", "WR3", "TE1", "RB1", "RB2", "QB1"} <= set(event["label"])
    assert event.set_index("label").loc["WR2", "resid"] == 3.0
    assert not (rows[~rows["event"]]["abs_label"].notna()).any()
    two, games2 = _league(missing={("AAA", "WR1", 6), ("AAA", "RB1", 6)})
    both = r7.event_rows(two, games2, _resid(two))
    assert not ((both["team"] == "AAA") & (both["week"] == 6)).any()  # in neither group


def test_season_roles_give_the_prior_rotation_with_regulars_marked(monkeypatch):
    from dfs import teammate_bump
    from dfs.research import data

    sp, games = _league()
    monkeypatch.setattr(data, "read_stats_player", lambda seasons=None, columns=None: sp)
    monkeypatch.setattr(data, "read_games", lambda seasons=None: games)
    roles = teammate_bump.season_roles(2023)
    aaa = roles[(roles["team"] == "AAA") & (roles["week"] == 6)].set_index("gsis_id")
    assert aaa.loc["AAA-WR1", "label"] == "WR1" and aaa.loc["AAA-WR1", "regular"]
    assert aaa.loc["AAA-WR3", "label"] == "WR3" and not aaa.loc["AAA-WR3", "regular"]  # 5 of 36: under 15%
    assert aaa.loc["AAA-RB2", "label"] == "RB2" and not aaa.loc["AAA-RB2", "regular"]  # 7 of 25: under 30%
    assert roles["week"].min() == 4  # nothing before a full three-game window
