import pandas as pd
import pytest

from dfs.sim import roles


def _games(rows):
    df = pd.DataFrame(
        rows, columns=["gsis_id", "position", "season", "week", "attempts", "carries", "targets"]
    )
    df["t"] = df["season"] * 100 + df["week"]
    df["game_id"] = df["season"].astype(str) + "_" + df["week"].astype(str)
    df["team"] = "AAA"
    df["opp"] = "BBB"
    df["total"] = 45.0
    df["name"] = df["gsis_id"]
    return df.sort_values(["gsis_id", "t"]).reset_index(drop=True)


def test_roles_rank_by_prior_usage_only_never_the_same_game():
    # w1: WR_a 9 targets, WR_b 1. w2: WR_b blows up (20 targets) but WR_a keeps the prior edge.
    pg = _games(
        [
            ("a", "WR", 2024, 1, 0, 0, 9),
            ("b", "WR", 2024, 1, 0, 0, 1),
            ("a", "WR", 2024, 2, 0, 0, 2),
            ("b", "WR", 2024, 2, 0, 0, 20),
        ]
    )
    r = roles.assign_roles(pg).groupby([pg["gsis_id"], pg["week"]]).first()
    assert r[("a", 2)] == "WR1" and r[("b", 2)] == "WR2"  # week 2 ranks on week 1 alone
    assert r[("a", 1)] == "" and r[("b", 1)] == ""  # no prior game, no role


def test_second_quarterback_has_no_role_and_deep_ranks_collapse():
    rows = [("q1", "QB", 2024, 1, 30, 0, 0), ("q2", "QB", 2024, 1, 1, 0, 0)]
    rows += [(f"r{i}", "RB", 2024, 1, 0, 10 - i, 0) for i in range(5)]
    rows += [(f"q{i}", "QB", 2024, 2, 30 - i, 0, 0) for i in (1, 2)]
    rows += [(f"r{i}", "RB", 2024, 2, 0, 5, 0) for i in range(5)]
    pg = _games(rows)
    wk2 = pg["week"] == 2
    got = roles.assign_roles(pg)[wk2].set_axis(pg.loc[wk2, "gsis_id"])
    assert got["q1"] == "QB1" and got["q2"] == ""
    assert [got[f"r{i}"] for i in range(5)] == ["RB1", "RB2", "RB3", "RB3", "RB3"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("QB", "QB1"),
        ("te", "TE1"),
        ("RB4", "RB3"),
        ("WR7", "WR4"),
        ("TE3", "TE2"),
        ("DST", "DST"),
        ("WR2", "WR2"),
    ],
)
def test_normalize_role(raw, expected):
    assert roles.normalize_role(raw) == expected


def test_unknown_role_is_refused():
    with pytest.raises(ValueError):
        roles.normalize_role("K1")
    with pytest.raises(ValueError):
        roles.normalize_role("QB2")
