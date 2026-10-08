"""Usage trends: last-3-games windows, ratio-of-sums shares, the SD noise band, no invented data."""

import numpy as np
import pandas as pd
import pytest

from dfs import usage_trends as ut


def _stats(rows):
    base = {"season_type": "REG", "team": "AAA", "position": "WR", "targets": 0, "carries": 0}
    return pd.DataFrame(
        [{**base, "receptions": 0, "receiving_air_yards": 0, "passing_air_yards": 0, **r} for r in rows]
    )


def _wr_weeks(pid, targets_by_week, *, team="AAA", team_targets=20):
    """One WR with the given target counts per week, plus a filler carrying the rest of the team's targets."""
    rows = []
    for week, t in enumerate(targets_by_week, start=1):
        rows.append({"player_id": pid, "player_display_name": pid, "week": week, "team": team, "targets": t})
        rows.append(
            {
                "player_id": f"{pid}-filler",
                "player_display_name": f"{pid}-filler",
                "week": week,
                "team": team,
                "targets": team_targets - t,
                "position": "TE",
            }
        )
    return rows


def test_weekly_frame_has_one_row_per_player_week_with_team_totals_and_blank_not_zero_red_zone():
    stats = _stats(_wr_weeks("w1", [5, 6, 7, 8]))
    frame = ut.weekly_frame(stats)
    one = frame[frame["GsisId"] == "w1"].sort_values("week")
    assert one["targets"].tolist() == [5, 6, 7, 8]
    assert one["team_targets"].tolist() == [20, 20, 20, 20]
    assert one["rz"].isna().all() and one["snap"].isna().all()  # no pbp / snap file: blank, never 0


def test_red_zone_counts_join_by_player_week_and_a_played_game_without_one_is_a_real_zero():
    stats = _stats(_wr_weeks("w1", [5, 6]))
    rz = pd.DataFrame({"GsisId": ["w1"], "week": [2], "rz": [3], "hvt": [1]})
    frame = ut.weekly_frame(stats, redzone=rz)
    one = frame[frame["GsisId"] == "w1"].sort_values("week")
    assert one["rz"].tolist() == [0.0, 3.0] and one["hvt"].tolist() == [0.0, 1.0]


def test_snaps_join_by_normalised_name_and_team():
    stats = _stats(_wr_weeks("w1", [5, 6]))
    stats.loc[stats["player_id"] == "w1", "player_display_name"] = "Amon-Ra St. Brown"
    snaps = pd.DataFrame(
        {
            "player": ["Amon-Ra St. Brown", "Amon-Ra St. Brown"],
            "team": ["AAA", "AAA"],
            "position": ["WR", "WR"],
            "week": [1, 2],
            "offense_pct": [0.8, 0.9],
        }
    )
    frame = ut.weekly_frame(stats, snaps=snaps)
    assert frame[frame["GsisId"] == "w1"].sort_values("week")["snap"].tolist() == [0.8, 0.9]


def test_shares_are_ratios_of_window_sums_over_the_last_three_games_against_the_earlier_ones():
    weekly = ut.weekly_frame(_stats(_wr_weeks("w1", [2, 4, 6, 8])))
    out = ut.compute_trends(weekly)
    row = out[(out["GsisId"] == "w1") & (out["Metric"] == "Tgt%")].iloc[0]
    assert row["Recent"] == pytest.approx((4 + 6 + 8) / 60)  # sum of targets over sum of team targets
    assert row["Prior"] == pytest.approx(2 / 20)
    assert row["Change"] == pytest.approx(18 / 60 - 0.1)
    assert (row["RecentGames"], row["PriorGames"]) == (3, 1)


def test_a_player_needs_a_full_recent_window_and_an_earlier_game():
    weekly = ut.weekly_frame(_stats(_wr_weeks("three", [5, 5, 5]) + _wr_weeks("four", [5, 5, 5, 9])))
    out = ut.compute_trends(weekly)
    assert "three" not in set(out["GsisId"])  # no earlier game to compare with
    assert "four" in set(out["GsisId"])


def test_a_missed_week_is_skipped_not_counted_as_zero():
    rows = [r for r in _wr_weeks("w1", [4, 4, 4, 4, 4]) if not (r["player_id"] == "w1" and r["week"] == 4)]
    weekly = ut.weekly_frame(_stats(rows))
    one = weekly[weekly["GsisId"] == "w1"]
    assert sorted(one["week"].tolist()) == [1, 2, 3, 5]  # no week-4 row, so he has four games, not five
    out = ut.compute_trends(weekly)
    assert out[(out["GsisId"] == "w1") & (out["Metric"] == "Tgt%")].iloc[0]["RecentGames"] == 3


def _season_with_many_stable_players_and_one_riser():
    rows = []
    rng = np.random.default_rng(1)
    for i in range(12):
        flat = list(rng.integers(4, 7, size=4))
        rows += _wr_weeks(f"p{i}", flat)
    rows += _wr_weeks("riser", [2, 2, 12, 12])  # a big jump in the last games
    return ut.weekly_frame(_stats(rows))


def test_only_a_change_beyond_the_standard_deviation_band_gets_an_arrow():
    out = ut.compute_trends(_season_with_many_stable_players_and_one_riser())
    tgt = out[out["Metric"] == "Tgt%"]
    riser = tgt[tgt["GsisId"] == "riser"].iloc[0]
    assert riser["Direction"] == ut.UP and riser["Z"] > 1
    quiet = tgt[tgt["GsisId"].str.startswith("p")]
    assert (quiet["Direction"] == "").sum() > 0  # most stable players sit inside the band
    assert tgt["Band"].nunique() >= 1 and tgt["Band"].notna().all()


def test_the_band_is_the_sd_of_the_changes_and_the_threshold_is_a_named_multiple_of_it():
    weekly = _season_with_many_stable_players_and_one_riser()
    out = ut.compute_trends(weekly)
    tgt = out[out["Metric"] == "Tgt%"]
    wr = tgt[tgt["Position"] == "WR"]
    assert wr["Band"].iloc[0] == pytest.approx(wr["Change"].std())
    wide = ut.compute_trends(weekly, noise_sd=100.0)
    assert (wide["Direction"] == "").all()  # a huge band marks nothing


def test_no_band_with_too_few_players_so_no_arrows():
    weekly = ut.weekly_frame(_stats(_wr_weeks("a", [2, 2, 12, 12]) + _wr_weeks("b", [5, 5, 5, 5])))
    out = ut.compute_trends(weekly)
    assert out["Band"].isna().all() and (out["Direction"] == "").all()


def test_the_population_limits_whose_changes_set_the_band():
    weekly = _season_with_many_stable_players_and_one_riser()
    everyone = ut.compute_trends(weekly)
    subset = {f"p{i}" for i in range(12)}  # the stable players only: the riser does not widen the band
    narrow = ut.compute_trends(weekly, population=subset)
    pick = lambda df: df[(df["Metric"] == "Tgt%") & (df["Position"] == "WR")]["Band"].iloc[0]  # noqa: E731
    assert pick(narrow) < pick(everyone)


def test_metrics_apply_only_to_their_positions_and_snap_needs_every_game():
    rows = _wr_weeks("w1", [5, 5, 5, 5])
    weekly = ut.weekly_frame(_stats(rows))
    out = ut.compute_trends(weekly)
    names = set(out[out["GsisId"] == "w1"]["Metric"])
    assert "Rush%" not in names and "HVT/G" not in names  # RB-only
    assert "Snap%" not in names and "RZ/G" not in names  # no snap / red-zone data: absent, not zero
    assert "Tgt%" in names and "Rec/G" not in names  # Rec/G is RB-only


def test_the_why_line_names_the_change_the_arrow_and_how_many_earlier_games_it_rests_on():
    row = pd.Series(
        {
            "Metric": "Tgt%",
            "Prior": 0.18,
            "Recent": 0.26,
            "Direction": ut.UP,
            "RecentGames": 3,
            "PriorGames": 1,
        }
    )
    text = ut.why(row)
    assert text.startswith("Tgt% 18% → 26% over the last 3 (▲, beyond normal week-to-week noise)")
    assert text.endswith("rests on 1 game")
    assert ut.format_value("Rec/G", 5.26) == "5.3" and ut.format_value("WOPR", 0.6234) == "0.62"
