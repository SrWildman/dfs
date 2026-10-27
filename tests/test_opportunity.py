"""Opportunity per dollar: the route proxy, the as-of windows (no lookahead), PriceGap, last-five averages,
the Model Check report and the Edge Finder block."""

import numpy as np
import pandas as pd
import pytest

from dfs import derived, edge_finder_tab, opportunity, results_signals

SEASON = 2026


def _pbp():
    """Team A: 10 plays in week 1, 4 dropbacks (one a sack counted as a dropback, a kneel is not a play)."""
    rows = []
    for i in range(10):
        rows.append(
            {
                "season": SEASON,
                "week": 1,
                "season_type": "REG",
                "posteam": "AAA",
                "play_type": "pass" if i < 4 else "run",
                "qb_dropback": 1 if i < 4 else 0,
                "two_point_attempt": 0,
                "pass": 1 if i < 4 else 0,
                "rush": 0 if i < 4 else 1,
                "yardline_100": 8 if i in (0, 5) else 60,
                "receiver_player_id": "rec1" if i < 4 else None,
                "rusher_player_id": "rb1" if i >= 4 else None,
            }
        )
    rows.append({**rows[0], "play_type": "pass", "two_point_attempt": 1, "qb_dropback": 1})  # a 2-pt try: out
    rows.append({**rows[0], "play_type": "qb_kneel", "qb_dropback": 0})  # not a scrimmage play
    return pd.DataFrame(rows)


def test_team_dropback_rate_counts_scrimmage_plays_only():
    rates = opportunity.team_dropback_rates(_pbp())
    assert len(rates) == 1 and rates.iloc[0]["dropback_rate"] == pytest.approx(0.4)


def test_high_value_touches_are_targets_and_carries_inside_the_10():
    hvt = opportunity.high_value_touches(_pbp()).set_index("GsisId")["hvt"]
    assert hvt["rec1"] == 1 and hvt["rb1"] == 1  # one target and one carry from the 8


def _weeks(rows):
    return pd.DataFrame(
        [
            {
                "GsisId": g,
                "Name": g,
                "Team": "AAA",
                "Position": pos,
                "season": s,
                "week": w,
                "targets": t,
                "xfp": x,
                "rec_xfp": 0.4 * x,
                "dk_actual": 10.0,
            }
            for g, pos, s, w, t, x in rows
        ]
    )


def _snaps_players():
    snaps = pd.DataFrame(
        {
            "season": [SEASON],
            "game_type": ["REG"],
            "week": [1],
            "team": ["AAA"],
            "pfr_player_id": ["Rec1"],
            "offense_snaps": [150.0],
        }
    )
    players = pd.DataFrame({"gsis_id": ["rec1"], "pfr_id": ["Rec1"]})
    return snaps, players


def test_routes_are_snaps_times_the_teams_dropback_rate_and_tprr_divides_targets_by_them():
    snaps, players = _snaps_players()
    pbp = _pbp()
    games = opportunity.player_games(_weeks([("rec1", "WR", SEASON, 1, 8, 12.0)]), snaps, pbp, players)
    assert games.iloc[0]["routes"] == pytest.approx(150 * 0.4)
    feats = opportunity.player_features(games, SEASON, 2)
    assert feats.loc["rec1", "tprr"] == pytest.approx(8 / 60)
    # under MIN_ROUTES the ratio is blank, not a noisy number
    short = games.assign(routes=opportunity.MIN_ROUTES - 1)
    assert pd.isna(opportunity.player_features(short, SEASON, 2).loc["rec1", "tprr"])


def test_a_game_without_a_snap_row_has_no_routes_not_zero_routes():
    pbp = _pbp()
    games = opportunity.player_games(_weeks([("rec1", "WR", SEASON, 1, 8, 12.0)]), None, pbp, None)
    assert games["routes"].isna().all()
    assert pd.isna(opportunity.player_features(games, SEASON, 2).loc["rec1", "tprr"])


def _games(xfps, gsis="p1", season=SEASON):
    return pd.DataFrame(
        {
            "GsisId": gsis,
            "season": season,
            "week": range(1, len(xfps) + 1),
            "xfp": xfps,
            "rec_xfp": [0.4 * x for x in xfps],
            "targets": [5.0] * len(xfps),
            "hvt": [2.0] * len(xfps),
            "routes": [np.nan] * len(xfps),
            "dk_actual": [10.0] * len(xfps),
        }
    )


def test_the_receiving_share_is_the_seasons_rec_xfp_over_its_xfp_and_blank_without_xfp():
    games = _games([10.0, 12.0, 14.0, 20.0, 99.0])
    row = opportunity.player_features(games, SEASON, 5).loc["p1"]
    assert row["rec_share"] == pytest.approx(0.4)
    # a back who only catches passes in the last game: the share is the ratio of SUMS, not a mean of ratios
    mixed = games.assign(rec_xfp=[0.0, 0.0, 0.0, 20.0, 0.0])
    assert opportunity.player_features(mixed, SEASON, 5).loc["p1", "rec_share"] == pytest.approx(20 / 56)
    none = games.assign(rec_xfp=np.nan)
    assert pd.isna(opportunity.player_features(none, SEASON, 5).loc["p1", "rec_share"])


def test_windows_use_this_season_games_before_the_slate_only():
    games = _games([10.0, 12.0, 14.0, 20.0, 99.0])
    feats = opportunity.player_features(games, SEASON, 5)  # weeks 1-4 are known, week 5 is the slate
    row = feats.loc["p1"]
    assert row["Games"] == 4 and row["xfp_szn"] == pytest.approx(14.0)
    assert row["xfp_l3"] == pytest.approx((12 + 14 + 20) / 3)
    assert row["xfp_trend"] == pytest.approx(row["xfp_l3"] - row["xfp_szn"])
    # nothing at or after the slate week reaches a feature, whatever it holds
    absurd = games.assign(xfp=[10.0, 12.0, 14.0, 20.0, -500.0])
    assert opportunity.player_features(absurd, SEASON, 5).equals(feats)
    # last season is not blended in: a player with no game yet this season is absent
    assert opportunity.player_features(_games([10.0] * 5, season=SEASON - 1), SEASON, 3).empty
    # a trend needs four games
    assert pd.isna(opportunity.player_features(games, SEASON, 4).loc["p1", "xfp_trend"])


def _slate(n=24):
    """n receivers: xFP rises 1.5 per $1k of salary; the last one has +6 on top."""
    frame = pd.DataFrame(
        {
            "Id": range(n),
            "GsisId": [f"g{i}" for i in range(n)],
            "Name": [f"P{i}" for i in range(n)],
            "Position": "WR",
            "Salary": [3000 + 200 * i for i in range(n)],
            "RosterablePool": True,
        }
    )
    xfp = 1.5 * frame["Salary"] / 1000 + np.sin(np.arange(n))
    xfp.iloc[-1] += 6
    games = pd.concat([_games([x, x, x, x], gsis=g) for g, x in zip(frame["GsisId"], xfp, strict=True)])
    return frame, games


def test_price_gap_is_the_residual_against_the_salary_line_in_sds():
    frame, games = _slate()
    feats = opportunity.attach_features(frame, games, SEASON, 5)
    gap = feats["PriceGap"]
    assert gap.idxmax() == len(frame) - 1 and gap.iloc[-1] > 2
    assert abs(gap.mean()) < 0.5
    # per $1k: xFP/$ is xFP Szn over salary in thousands
    assert feats["xFP/$"].iloc[0] == pytest.approx(feats["xFP Szn"].iloc[0] / 3.0)
    assert feats["Tgt/$"].iloc[0] == pytest.approx(5.0 / 3.0)
    assert feats["HVT/$"].isna().all()  # HVT/$ is for running backs only


def test_blanks_where_a_measure_does_not_apply_or_there_is_no_history():
    frame, games = _slate()
    frame.loc[0, "Position"] = "QB"
    feats = opportunity.attach_features(frame, games, SEASON, 5)
    assert pd.isna(feats.loc[0, "TPRR~"]) and pd.isna(feats.loc[0, "Tgt/$"])  # no QB targets role
    assert pd.notna(feats.loc[0, "xFP Szn"])
    assert (
        pd.isna(feats.loc[0, "xFP Rec%"]) and feats["xFP Rec%"].iloc[1:].notna().any()
    )  # a QB has no pass-game share
    week1 = opportunity.attach_features(frame, games, SEASON, 1)
    assert week1.isna().all().all()  # week 1: no game this season yet
    short = opportunity.attach_features(frame, games, SEASON, 3)  # two games: below MIN_GAMES
    assert short["PriceGap"].isna().all() and short["xFP/$"].isna().all() and short["xFP Szn"].notna().any()
    no_ids = opportunity.attach_features(frame.drop(columns="GsisId"), games, SEASON, 5)
    assert no_ids.isna().all().all()


def test_rb_gets_hvt_per_dollar():
    frame, games = _slate()
    frame["Position"] = "RB"
    feats = opportunity.attach_features(frame, games, SEASON, 5)
    assert feats["HVT/$"].iloc[0] == pytest.approx(2.0 / 3.0)


def test_last_five_averages_join_by_name_and_position_and_skip_blank_cells():
    log = pd.DataFrame(
        {
            "NameKey": ["wide one"] * 2 + ["qb one"],
            "Position": ["WR", "WR", "QB"],
            "Back": [1, 2, 1],
            "Snap%": [0.8, 0.6, 0.95],
            "Tgt": [10.0, 6.0, np.nan],
            "Car": [0.0, 2.0, 4.0],
            "DK": [20.0, 10.0, 18.0],
        }
    )
    frame = pd.DataFrame({"Name": ["Wide One", "QB One", "Nobody"], "Position": ["WR", "QB", "RB"]})
    out = opportunity.attach_last5(frame, log)
    assert list(out.columns) == derived.LAST5_COLUMNS
    assert out.loc[0].tolist() == pytest.approx([0.7, 8.0, 1.0, 15.0])
    assert pd.isna(out.loc[1, "Tgt L5"]) and out.loc[1, "Car L5"] == 4.0  # a QB's targets are not a role
    assert out.loc[2].isna().all()
    assert opportunity.attach_last5(frame, None).isna().all().all()


# ---- Model Check ----------------------------------------------------------------------------------------


def _evaluation(weeks=(4, 5, 6), shift=0.0):
    frame, games = _slate()
    games = pd.concat([games.assign(week=w) for w in range(1, 5)]).drop_duplicates(["GsisId", "week"])
    games["week"] = games.groupby("GsisId").cumcount() + 1
    rows = []
    for week in weeks:
        part = frame.assign(
            week=week,
            season=SEASON,
            ProjPts=10.0,
            CalPts=10.0,
            DkActual=10.0 + shift * (frame["Id"] >= 18),
        )
        rows.append(part)
    return pd.concat(rows, ignore_index=True), games


def test_the_report_scores_flagged_groups_against_calpts_and_lists_a_silent_group_with_n_zero():
    frame, games = _evaluation(shift=4.0)
    attached = results_signals.attach_opportunity(frame, games, SEASON)
    assert set(opportunity.FEATURE_COLUMNS) <= set(attached.columns)
    report = results_signals.opportunity_report(attached).set_index("Signal")
    top = report.loc["xFP/$ top third of position"]
    assert top["n"] > 0 and top["VsCal"] > 0 and top["Weeks"] == 3 and top["WeeksUp"] == 3
    assert report.loc["HVT/$ top third (RB)", "n"] == 0  # no running backs on this slate
    assert "Everyone with a PriceGap" in report.index
    assert report.reset_index().columns.tolist() == results_signals.OPPORTUNITY_REPORT_COLUMNS


def test_the_backfilled_features_use_only_games_before_each_week():
    frame, games = _evaluation()
    base = results_signals.attach_opportunity(frame, games, SEASON)
    late = games.copy()
    late.loc[late["week"] >= 5, ["xfp", "targets"]] = (
        999.0  # rewrite week 5+ games: week 5's slate must not see them
    )
    changed = results_signals.attach_opportunity(frame, late, SEASON)
    week4 = base["week"] == 4
    week5 = base["week"] == 5
    pd.testing.assert_frame_equal(
        base.loc[week4, opportunity.FEATURE_COLUMNS], changed.loc[week4, opportunity.FEATURE_COLUMNS]
    )
    pd.testing.assert_frame_equal(  # week 5 sees weeks 1-4 only: week 5's own game (and later) cannot move it
        base.loc[week5, opportunity.FEATURE_COLUMNS], changed.loc[week5, opportunity.FEATURE_COLUMNS]
    )


def test_salary_expected_is_fit_on_earlier_weeks_only():
    frame, _ = _evaluation(weeks=(1, 2, 3))
    frame["DkActual"] = 2.0 * frame["Salary"] / 1000
    expected = results_signals.salary_expected(frame)
    assert expected[frame["week"] == 1].isna().all()  # nothing earlier to fit on
    assert expected[frame["week"] == 3].iloc[0] == pytest.approx(2.0 * 3.0, abs=1e-6)


# ---- the Edge Finder block ------------------------------------------------------------------------------


def _edge_rows():
    names = ["A", "B", "C", "D"]
    return pd.DataFrame(
        {
            "Id": range(4),
            "Name": names,
            "Position": ["WR", "RB", "WR", "TE"],
            "Team": "KC",
            "Salary": [5000, 6000, 4500, 3000],
            "ProjPts": [14.0, 15.0, 12.0, 9.0],
            "Avail": ["", "", "OUT", ""],
            "PriceGap": [2.0, 0.2, 3.0, 1.0],
            "xFP Szn": [15.0, 12.0, 14.0, 9.0],
            "xFP trend": [0.5, 0.0, 0.0, 2.0],
            "TPRR~": [0.25, 0.1, 0.3, 0.2],
            "xFP/$": [3.0, 2.0, 3.1, 3.0],
            "CalPts": [14.0, 15.0, 12.0, 9.0],
            "Own%": np.nan,
            "Edge": "",
        }
    )


def test_the_price_gap_list_is_rosterable_available_and_above_the_floor_biggest_first():
    part = edge_finder_tab.price_gap_plays(_edge_rows())
    assert part["Name"].tolist() == ["A", "D"]  # C is OUT, B's gap is under the floor
    assert edge_finder_tab.price_gap_plays(_edge_rows().drop(columns="PriceGap")).empty


def test_the_price_gap_section_writes_rows_with_the_pool_cue_and_a_why():
    b = edge_finder_tab._Builder(None, None)
    edge_finder_tab._price_gap_section(b, _edge_rows())
    layout = b.layout
    assert layout.player_rows and any("PRICE GAP" in str(r[0]) for r in layout.rows)
    first = layout.rows[layout.player_rows[0] - 1]
    assert first[0] == "" and first[1] == "A"  # column A is the Pool cue the writer fills
    do, why = edge_finder_tab.TRAILING_HEADERS.index("Do"), edge_finder_tab.TRAILING_HEADERS.index("Why")
    base = edge_finder_tab.FIRST_TRAILING
    assert edge_finder_tab.build_layout(None).range_formats == []  # nothing formatted in the empty state
    edge_finder_tab._mark_formats(layout)  # build_layout does this once every section is written
    assert {p for _rng, p, _kind in layout.range_formats} == {"+0.0;-0.0;0.0", "0.00", "0%"}
    assert first[base + do] == "Price check" and "SD" in first[base + why]
    empty = edge_finder_tab._Builder(None, None)
    edge_finder_tab._price_gap_section(empty, _edge_rows().assign(PriceGap=np.nan))
    assert not empty.layout.player_rows and any("Week 4" in " ".join(map(str, r)) for r in empty.layout.rows)


# ---- the PRICE GAP block's track-record line -------------------------------------------------------------


def _record_table(**over):
    row = {
        "Signal": results_signals.PRICE_GAP_RECORD_GROUP,
        "n": 66,
        "VsCal": -0.58,
        "Z": -0.67,
        "FirstWeek": 4,
        "LastWeek": 6,
    }
    return pd.DataFrame([{**row, **over}])


def test_the_record_line_says_not_proven_with_the_gap_weeks_and_n():
    line = results_signals.price_gap_record_line(_record_table())
    assert line == "Not proven: PriceGap ≥ +1 ran −0.58 vs CalPts, Weeks 4-6, n 66"
    one = results_signals.price_gap_record_line(_record_table(FirstWeek=4, LastWeek=4))
    assert "Week 4," in one
    beat = results_signals.price_gap_record_line(_record_table(VsCal=0.9, Z=2.4))
    assert beat.startswith("Beat CalPts so far")


def test_the_record_line_is_blank_without_a_table_or_a_fired_group():
    assert results_signals.price_gap_record_line(None) == ""
    assert results_signals.price_gap_record_line(pd.DataFrame()) == ""
    assert results_signals.price_gap_record_line(_record_table(n=0, VsCal=np.nan)) == ""


def test_the_price_gap_block_opens_with_the_record_line_when_there_is_one():
    plain = edge_finder_tab._Builder(None, None)
    edge_finder_tab._price_gap_section(plain, _edge_rows())
    assert not any("Not proven" in " ".join(map(str, r)) for r in plain.layout.rows)
    b = edge_finder_tab._Builder(None, None)
    b.price_gap_record = results_signals.price_gap_record_line(_record_table())
    edge_finder_tab._price_gap_section(b, _edge_rows())
    texts = [" ".join(map(str, r)) for r in b.layout.rows]
    title = next(i for i, t in enumerate(texts) if "PRICE GAP" in t)
    line = next(i for i, t in enumerate(texts) if "Not proven: PriceGap" in t)
    assert "Track record" in texts[line] and line > title  # under the title and its "What it is" rows
    assert b.layout.player_rows and b.layout.player_rows[0] - 1 > line  # above the header and the players
