"""The slate scoring index: main-slate definition, the candidates on a synthetic slate with known points,
the fit to cash lines, and the committed 2021-2025 backfill."""

import pandas as pd
import pytest

from dfs.contests import slate_index as SI


def games_frame(weeks=(1, 2), n_main=6):
    """`n_main` Sunday-afternoon games per week between teams M01.. plus a Thursday, Sunday-night and Monday
    game between teams that must never reach the main slate. Week-1 games finish 20-17, week-2 24-13."""
    rows = []
    for week in weeks:
        home_score, away_score = (20, 17) if week == 1 else (24, 13)
        for g in range(n_main):
            rows.append(
                (
                    "REG",
                    week,
                    "Sunday",
                    "13:00" if g % 2 == 0 else "16:25",
                    f"M{2 * g + 1:02d}",
                    f"M{2 * g + 2:02d}",
                )
            )
        rows.append(("REG", week, "Thursday", "20:15", "X01", "X02"))
        rows.append(("REG", week, "Sunday", "20:20", "X03", "X04"))
        rows.append(("REG", week, "Sunday", "09:30", "X05", "X06"))  # early international game
        rows.append(("REG", week, "Monday", "20:15", "X07", "X08"))
    df = pd.DataFrame(rows, columns=["game_type", "week", "weekday", "gametime", "home_team", "away_team"])
    df["season"] = 2025
    df["home_score"] = [20 if w == 1 else 24 for w in df["week"]]
    df["away_score"] = [17 if w == 1 else 13 for w in df["week"]]
    return df


def _player(team, week, position, number, **stats):
    row = {
        "player_id": f"{team}-{position}-{number}",
        "player_display_name": f"{team} {position} {number}",
        "position": position,
        "season_type": "REG",
        "week": week,
        "team": team,
    }
    row.update(stats)
    return row


def stats_frames(games, scale_by_week):
    """One QB, two RB, three WR, one TE per team. Points are exactly: QB 250 yds x 0.04 = 10, RB 50 rush yds
    = 5, WR 60 rec yds = 6, TE 40 rec yds = 4 (times the week's scale). Teams X* are 10x in every position."""
    players, teams = [], []
    for week in sorted(set(games["week"])):
        factor = scale_by_week[week]
        week_games = games[games["week"] == week]
        for team in sorted(set(week_games["home_team"]) | set(week_games["away_team"])):
            f = factor * (10 if team.startswith("X") else 1)
            roster = [("QB", {"passing_yards": 250 * f})]
            roster += [("RB", {"rushing_yards": 50 * f})] * 2
            roster += [("WR", {"receiving_yards": 60 * f})] * 3
            roster += [("TE", {"receiving_yards": 40 * f})]
            for number, (position, stats) in enumerate(roster, start=1):
                players.append(_player(team, week, position, number, **stats))
            game = week_games[(week_games["home_team"] == team) | (week_games["away_team"] == team)].iloc[0]
            opponent = game["away_team"] if game["home_team"] == team else game["home_team"]
            teams.append(
                {
                    "season_type": "REG",
                    "week": week,
                    "team": team,
                    "opponent_team": opponent,
                    "def_sacks": 1,
                }
            )
    return pd.DataFrame(players), pd.DataFrame(teams)


@pytest.fixture(scope="module")
def synthetic():
    games = games_frame()
    sp, st = stats_frames(games, {1: 1.0, 2: 0.8})
    return SI.weekly_candidates(sp, st, games, 2025), games, sp, st


def test_main_slate_is_the_sunday_afternoon_games_only():
    games = games_frame(weeks=(1,))
    games.loc[len(games)] = ["POST", 19, "Sunday", "13:00", "P1", "P2", 2025, 20, 17]
    main = SI.main_slate_games(games, 2025)
    assert len(main) == 6 and set(main["home_team"]).issubset({f"M{i:02d}" for i in range(1, 13)})
    assert (main["gametime"].isin(["13:00", "16:25"])).all()
    assert SI.main_slate_games(games, 2024).empty


def test_candidates_on_a_slate_with_known_points(synthetic):
    table, *_ = synthetic
    assert list(table["week"]) == [1, 2] and list(table["n_games"]) == [6, 6]
    w1 = table.set_index("week").loc[1]
    # QB 10 + 2 RB x 5 + 3 WR x 6 + TE 4 + DST 2 (a sack + 14-20 points allowed = 1) + FLEX (RB 5, WR 6,
    # TE 4 weighted 24:36:12 = 5.333)
    assert w1["top_n_lineup"] == pytest.approx(10 + 10 + 18 + 4 + 2 + 16 / 3)
    assert w1["points_per_game"] == 37.0
    # every player at a position scores the same, so any 95th percentile lineup is 10+10+18+4+2 plus the best
    # FLEX (WR, 6) the draws reach 5% of the time
    assert w1["p95_optimal"] == pytest.approx(50.0)
    w2 = table.set_index("week").loc[2]
    # week 2 is 24-13 everywhere: a home DST allowed 13 (4 points) + a sack = 5, an away DST allowed 24
    # (0 points) + a sack = 1, so the mean of the twelve is 3
    assert w2["top_n_lineup"] == pytest.approx(8 + 8 + 14.4 + 3.2 + 3 + (4 * 24 + 4.8 * 36 + 3.2 * 12) / 72)
    assert w2["points_per_game"] == 37.0


def test_players_outside_the_main_slate_never_count(synthetic):
    """The X teams score 10x; if any leaked in, week 1's numbers would be far above 49."""
    table, *_ = synthetic
    assert table["top_n_lineup"].max() < 60


def test_candidates_are_deterministic(synthetic):
    table, games, sp, st = synthetic
    again = SI.weekly_candidates(sp, st, games, 2025)
    pd.testing.assert_frame_equal(table, again)


def test_a_week_with_too_few_main_slate_games_has_no_index():
    games = games_frame(weeks=(1,), n_main=SI.MIN_MAIN_SLATE_GAMES - 1)
    sp, st = stats_frames(games, {1: 1.0})
    assert SI.weekly_candidates(sp, st, games, 2025).empty


def test_a_week_with_an_unplayed_main_slate_game_is_left_out():
    games = games_frame(weeks=(1, 2))
    games.loc[(games["week"] == 2) & (games["home_team"] == "M01"), "home_score"] = float("nan")
    sp, st = stats_frames(games, {1: 1.0, 2: 1.0})
    assert list(SI.weekly_candidates(sp, st, games, 2025)["week"]) == [1]


def test_relative_values_average_one_per_season(synthetic):
    table, *_ = synthetic
    rel = SI.with_relative(table)
    for c in SI.CANDIDATES:
        assert rel[f"rel_{c}"].mean() == pytest.approx(1.0)
    top = rel.set_index("week")["rel_top_n_lineup"]
    assert top.loc[1] == pytest.approx(
        table.set_index("week").loc[1, "top_n_lineup"] / table["top_n_lineup"].mean()
    )
    assert rel["rel_points_per_game"].tolist() == [1.0, 1.0]
    index = SI.slate_index(rel)
    assert list(index.columns) == ["season", "week", "index"] and index["index"].iloc[0] == top.loc[1]


def cash_table(slopes):
    """A table over the five known 2026 cash-line weeks whose relative candidates are given."""
    rows = [{"season": 2026, "week": w} for w in range(1, 6)]
    df = pd.DataFrame(rows)
    for c in SI.CANDIDATES:
        df[f"rel_{c}"] = slopes[c]
    return df


def test_fit_picks_the_candidate_that_tracks_the_cash_lines():
    cash = SI.KNOWN_CASH_LINES
    lines = [cash[(2026, w)] for w in range(1, 6)]
    perfect = [1 + (v - sum(lines) / 5) / 100 for v in lines]  # linear in the cash line
    noise = [1.05, 0.95, 1.0, 1.02, 0.98]
    fits = SI.fit_candidates(
        cash_table({"top_n_lineup": noise, "p95_optimal": perfect, "points_per_game": noise[::-1]})
    )
    best = fits.set_index("candidate")
    assert SI.select(fits) == "p95_optimal"
    assert best.loc["p95_optimal", "r"] == pytest.approx(1.0) and best.loc["p95_optimal", "loo_rmse"] < 1e-6
    assert best.loc["p95_optimal", "slope"] == pytest.approx(100.0)
    assert best.loc["p95_optimal", "rel_slope"] == pytest.approx(100 / (sum(lines) / 5))
    assert (fits["n"] == 5).all()
    # no index at all (the mean of the others) costs this much
    assert best.loc["top_n_lineup", "baseline_loo_rmse"] == pytest.approx(
        best.loc["p95_optimal", "baseline_loo_rmse"]
    )


def test_fit_reports_when_no_candidate_beats_predicting_the_mean():
    cash = SI.KNOWN_CASH_LINES
    flat = [1.0, 1.0, 1.0, 1.001, 0.999]
    fits = SI.fit_candidates(cash_table(dict.fromkeys(SI.CANDIDATES, flat)))
    assert (fits["loo_rmse"] >= fits["baseline_loo_rmse"] - 1e-9).all()
    assert len(cash) == 5


def test_fit_with_fewer_than_three_matching_weeks_returns_nothing():
    table = pd.DataFrame(
        {"season": [2025, 2025], "week": [1, 2], **{f"rel_{c}": [1.0, 1.0] for c in SI.CANDIDATES}}
    )
    assert SI.fit_candidates(table).empty


# ---- the committed backfill ----------------------------------------------------------------------


def test_backfill_covers_2021_to_2025_with_relative_columns_averaging_one():
    table = SI.load_backfill()
    assert list(table.columns) == SI.INDEX_COLUMNS
    assert set(table["season"]) == set(SI.BACKFILL_SEASONS)
    for season, sub in table.groupby("season"):
        assert len(sub) >= 17, season
        for c in SI.CANDIDATES:
            assert sub[f"rel_{c}"].mean() == pytest.approx(1.0, abs=1e-5)
    assert table[list(SI.CANDIDATES)].notna().all().all()
    assert (table["n_games"] >= SI.MIN_MAIN_SLATE_GAMES).all()


def test_backfill_leaves_out_the_2022_christmas_weekend_with_no_main_slate():
    table = SI.load_backfill()
    assert table[(table["season"] == 2022) & (table["week"] == 16)].empty


def test_the_selected_index_is_a_known_candidate():
    assert SI.SELECTED in SI.CANDIDATES
