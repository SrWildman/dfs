"""Late swap against a live contest's real field: the export's lineups become the cash and top-1% lines."""

import io
import os
import zipfile
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from dfs import late_swap_field as lf
from dfs.contests import standings as st
from dfs.sim.simulate import PlayerSpec

NOW = datetime(2026, 10, 11, 17, 30, tzinfo=UTC)  # 1:30 pm ET
EARLY, LATE = "2026-10-11T13:00:00Z", "2026-10-11T16:25:00Z"
HEADER = "Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS"


def lineup_text(names):
    qb, rb1, rb2, wr1, wr2, wr3, te, flex, dst = names
    return f"DST {dst} FLEX {flex} QB {qb} RB {rb1} RB {rb2} TE {te} WR {wr1} WR {wr2} WR {wr3}"


def standings_csv(lineups, points, players, *, remaining=1):
    """`lineups`: nine-name lists; `points`: their points so far; `players`: (name, pos, pct, fpts)."""
    rows = []
    for i in range(max(len(lineups), len(players))):
        left = ["", "", "", "", "", ""]
        if i < len(lineups):
            left = [str(i + 1), str(1000 + i), f"user{i} (1/1)", str(remaining), f"{points[i]:.2f}"]
            left.append(f'"{lineup_text(lineups[i])}"')
        right = ["", "", "", ""]
        if i < len(players):
            name, pos, pct, fpts = players[i]
            right = [name, pos, f"{pct:.2f}%", f"{fpts}"]
        rows.append(",".join([*left, "", *right]))
    return "\n".join([HEADER, *rows]).encode()


def spec(name, pos, team, role, projection, game):
    return PlayerSpec(name, pos, team, "OPP", game, role, projection, 5000)


NAMES = [
    "Quinn Ace",
    "Rex Ace",
    "Rex Bee",
    "Walt Ace",
    "Walt Bee",
    "Walt Cee",
    "Tom Ace",
    "Walt Dee",
    "Dallas D",
]
POS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST"]
ROLE = ["QB1", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1", "WR4", "DST"]
TEAM = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III"]


def specs_of(names, projections, *, shared_game=False):
    return {
        n: spec(n, p, t, r, proj, "G" if shared_game else f"g{i}")
        for i, (n, p, t, r, proj) in enumerate(zip(names, POS, TEAM, ROLE, projections, strict=True))
    }


def edge_of(names, starts):
    return pd.DataFrame({"Name": names, "GameStart": starts})


def test_a_standings_export_parses_into_lineups_and_ownership():
    raw = standings_csv(
        [NAMES, NAMES], [10.0, 10.0], [(n, p, 100.0, 1.0) for n, p in zip(NAMES, POS, strict=True)]
    )
    parsed = st.parse_standings(raw, contest_id="1")
    assert len(parsed.lineups) == 2
    own = lf.real_ownership(parsed, NAMES)
    assert own["Quinn Ace"] == pytest.approx(1.0)


def test_kind_of_format_reads_the_series_formats():
    assert lf.kind_of_format("Double Up") == "cash"
    assert lf.kind_of_format("SE GPP") == "gpp"
    assert lf.kind_of_format("N-max GPP") == "gpp"
    assert lf.kind_of_format("Showdown") == "unknown"


def test_the_cash_fraction_is_the_median_double_up_share_of_the_history_log():
    log = pd.DataFrame(
        {
            "contest_key": ["a", "b", "c", "d"],
            "format": ["Double Up", "Double Up", "Double Up", "SE GPP"],
            "entries": [1000, 2000, 4000, 100],
            "places_paid": [400, 900, 1800, 22],
        }
    )
    assert lf.default_cash_fraction(log) == pytest.approx(0.45)
    assert lf.default_cash_fraction(None) == lf.DEFAULT_CASH_FRACTION
    assert lf.default_cash_fraction(log.iloc[3:]) == lf.DEFAULT_CASH_FRACTION  # no double up in it


def test_a_player_who_has_not_started_scores_nothing_so_far_and_a_started_one_his_export_points():
    names = NAMES
    specs = specs_of(names, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    starts = [EARLY] * 3 + [LATE] * 6
    kick = lf.kickoffs_from_edge(edge_of(names, starts))
    fpts = {lf.normalize_name(n): 5.0 for n in names}  # the export holds 5.0 for everyone
    state = lf.slate_state(specs, kick, NOW, fpts)
    by_id = {p.id: i for i, p in enumerate(state.specs)}
    qb, wr = by_id["Quinn Ace"], by_id["Walt Cee"]
    assert state.now_pts[qb] == 5.0  # kicked off at 1 pm: 5.0 so far
    assert 0 < state.frac[qb] < 1  # half an hour into a 3.25-hour game
    assert state.frac[qb] == pytest.approx(1 - 0.5 / lf.GAME_HOURS)
    assert (
        state.now_pts[wr] == 0.0 and state.frac[wr] == 1.0
    )  # not started: nothing yet, a whole game to come


def test_game_hours_none_treats_every_started_player_as_final():
    specs = specs_of(NAMES, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    kick = lf.kickoffs_from_edge(edge_of(NAMES, [EARLY] * 9))
    state = lf.slate_state(specs, kick, NOW, {}, game_hours=None)
    assert (state.frac == 0).all()
    draws = lf.simulate_remaining(state, n_sims=50)
    assert np.allclose(draws.final, state.now_pts[None, :])  # nothing left to draw


def test_a_player_with_a_game_to_come_is_drawn_around_his_projection():
    specs = specs_of(NAMES, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    kick = lf.kickoffs_from_edge(edge_of(NAMES, [LATE] * 9))
    state = lf.slate_state(specs, kick, NOW, {})
    draws = lf.simulate_remaining(state, n_sims=4000, seed=3)
    qb = draws.column["Quinn Ace"]
    assert draws.final[:, qb].mean() == pytest.approx(20.0, rel=0.15)
    assert draws.final[:, qb].std() > 3
    again = lf.simulate_remaining(state, n_sims=4000, seed=3)
    assert np.array_equal(draws.final, again.final)  # a seed is the same draws


def test_the_lines_are_the_field_scores_at_the_paid_place_and_the_top_one_percent():
    """Ten field lineups of one player each repeated: with nothing left to simulate the lines are the sorted
    points so far, at rank `places_paid` and `ceil(1%)`."""
    specs = specs_of(NAMES, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    kick = lf.kickoffs_from_edge(edge_of(NAMES, [EARLY] * 9))
    # everybody's game is over (the 1 pm state of a back-test); each lineup's points are its players' actuals
    fpts = {lf.normalize_name(n): float(10 + i) for i, n in enumerate(NAMES)}
    state = lf.slate_state(specs, kick, NOW, fpts, game_hours=None)
    draws = lf.simulate_remaining(state, n_sims=20)
    # 200 lineups, each holding the same nine; points so far therefore identical: 10+...+18 = 126
    lineups = [NAMES] * 200
    raw = standings_csv(
        lineups,
        [126.0] * 200,
        [(n, p, 100.0, fpts[lf.normalize_name(n)]) for n, p in zip(NAMES, POS, strict=True)],
    )
    parsed = st.parse_standings(raw, contest_id="2")
    lines = lf.real_lines(parsed, draws, fpts, cash_fraction=0.45)
    assert lines.cash_rank == 90 and lines.top_rank == 2
    assert np.allclose(lines.cash, 126.0) and np.allclose(lines.top, 126.0)
    assert lines.points_match == 1.0 and lines.unresolved == 0 and lines.open_slots == 0


def test_the_cash_line_sits_at_the_last_paid_place_when_lineups_differ():
    """Twenty lineups share eight players (10 points each) and differ in the flex: 1, 2 ... 20 points."""
    base = NAMES[:7] + ["Dallas D"]
    flex = [f"Flex {chr(65 + i)}" for i in range(20)]
    specs = specs_of(NAMES, [10] * 9)
    for name in flex:
        specs[name] = spec(name, "WR", "ZZZ", "WR4", 5.0, f"g{name}")
    kick = lf.kickoffs_from_edge(edge_of(list(specs), [EARLY] * len(specs)))
    fpts = {lf.normalize_name(n): 10.0 for n in base}
    fpts.update({lf.normalize_name(n): float(i + 1) for i, n in enumerate(flex)})
    state = lf.slate_state(specs, kick, NOW, fpts, game_hours=None)
    draws = lf.simulate_remaining(state, n_sims=10)
    lineups = [[*NAMES[:7], f, "Dallas D"] for f in flex]
    points = [80.0 + i + 1 for i in range(20)]
    table = [(n, "WR", 100.0, fpts[lf.normalize_name(n)]) for n in [*base, *flex]]
    parsed = st.parse_standings(standings_csv(lineups, points, table), contest_id="3")
    lines = lf.real_lines(parsed, draws, fpts, places_paid=5)
    assert lines.cash_rank == 5 and lines.top_rank == 1
    assert np.allclose(lines.cash, 96.0)  # the 5th highest of 81 ... 100
    assert np.allclose(lines.top, 100.0)
    assert lines.points_match == 1.0


def test_swap_deltas_score_a_swap_against_the_lines_on_shared_draws():
    specs = specs_of(NAMES, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    specs["Star Receiver"] = spec("Star Receiver", "WR", "JJJ", "WR1", 22, "gStar")
    state = lf.slate_state(specs, {}, NOW, {})
    draws = lf.simulate_remaining(state, n_sims=6000, seed=1)
    lineup = [specs[n] for n in NAMES]
    base = lf.LiveLines(draws, np.full(6000, 90.0), np.full(6000, 130.0), "field: real (test)")
    (delta,) = base.swap_deltas(
        lineup, [(["Walt Dee"], [specs["Star Receiver"]])]
    )  # an 8-point flex for a 22-point star
    assert delta.delta_p_cash > 0 and delta.delta_p_gpp > 0
    assert delta.p_cash == pytest.approx(base.probabilities(base.scores(lineup))[0] + delta.delta_p_cash)
    (sit,) = base.swap_deltas(
        lineup, [(["Quinn Ace"], [])]
    )  # nobody in: the lineup loses its quarterback's points
    assert sit.delta_p_cash < 0


def test_a_higher_line_lowers_every_probability():
    specs = specs_of(NAMES, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    draws = lf.simulate_remaining(lf.slate_state(specs, {}, NOW, {}), n_sims=3000, seed=2)
    lineup = [specs[n] for n in NAMES]
    low = lf.LiveLines(draws, np.full(3000, 80.0), np.full(3000, 120.0), "x")
    high = lf.LiveLines(draws, np.full(3000, 110.0), np.full(3000, 150.0), "x")
    assert high.probabilities(high.scores(lineup))[0] < low.probabilities(low.scores(lineup))[0]
    assert high.probabilities(high.scores(lineup))[1] < low.probabilities(low.scores(lineup))[1]


def test_pick_files_gives_the_cash_line_to_the_double_up_and_the_top_line_to_the_gpp():
    def f(kind, name):
        return lf.FieldFile("1", "x", kind, name, None, None)

    du, gpp = f("cash", "DU"), f("gpp", "GPP")
    cash, top, notes = lf.pick_files([gpp, du])
    assert (cash, top, notes) == (du, gpp, [])
    only = f("unknown", "Mystery")
    cash, top, notes = lf.pick_files([only])
    assert cash is top is only and "format is unknown" in notes[0]
    cash, top, notes = lf.pick_files([gpp])
    assert cash is top is gpp and "no Double Up export" in notes[0]


def test_overlay_ownership_replaces_only_the_players_the_export_names():
    edge = pd.DataFrame({"Name": ["A", "B", "C"], "Own%": [0.10, 0.20, np.nan]})
    out = lf.overlay_ownership(edge, {"B": 0.35, "C": 0.05})
    assert list(out["Own%"]) == [0.10, 0.35, 0.05]
    assert list(edge["Own%"].fillna(-1)) == [0.10, 0.20, -1]  # the input is untouched
    assert lf.overlay_ownership(edge, {}) is edge


def test_find_auto_takes_live_exports_downloaded_this_week_and_says_what_it_skipped(tmp_path):
    live = standings_csv([NAMES], [10.0], [(n, p, 100.0, 1.0) for n, p in zip(NAMES, POS, strict=True)])
    done = standings_csv(
        [NAMES], [10.0], [(n, p, 100.0, 1.0) for n, p in zip(NAMES, POS, strict=True)], remaining=0
    )
    (tmp_path / "contest-standings-111.csv").write_bytes(live)
    (tmp_path / "contest-standings-222.csv").write_bytes(done)
    (tmp_path / "unrelated.csv").write_text("a,b\n1,2\n")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("contest-standings-333.csv", live)
    (tmp_path / "NFL Contest Standings (3).zip").write_bytes(buffer.getvalue())
    old = tmp_path / "contest-standings-444.csv"
    old.write_bytes(live)
    os.utime(old, (0, 0))  # downloaded long before the slate
    files, notes = lf.find_auto(
        tmp_path,
        first_kickoff=datetime.now(UTC) - timedelta(days=1),
        formats={"111": ("NFL GIANT $10 Double Up [Single Entry]", "Double Up")},
    )
    ids = {f.contest_id for f in files}
    assert ids == {"111", "333"}  # live and recent; 222 is finished, 444 is old
    assert next(f for f in files if f.contest_id == "111").kind == "cash"
    assert any("222" in n and "finished" in n for n in notes)
    assert any("444" in n and "before this slate" in n for n in notes)


def test_a_missing_downloads_folder_is_a_note_not_a_crash(tmp_path):
    files, notes = lf.find_auto(tmp_path / "nope", first_kickoff=None)
    assert files == [] and "no Downloads folder" in notes[0]


def test_read_field_rejects_a_file_that_is_not_a_standings_export(tmp_path):
    bad = tmp_path / "contest-standings-9.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(st.StandingsError):
        lf.read_field(bad)


def test_a_started_player_missing_from_every_export_is_reported():
    specs = specs_of(NAMES, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    starts = [EARLY] * 3 + [LATE] * 6
    kick = lf.kickoffs_from_edge(edge_of(NAMES, starts))
    state = lf.slate_state(
        specs, kick, NOW, {lf.normalize_name("Quinn Ace"): 5.0, lf.normalize_name("Rex Ace"): 4.0}
    )
    draws = lf.simulate_remaining(state, n_sims=20)
    live = lf.LiveLines(
        draws,
        np.zeros(20),
        np.zeros(20),
        "x",
        known_points=frozenset({lf.normalize_name("Quinn Ace"), lf.normalize_name("Rex Ace")}),
    )
    lineup = [specs[n] for n in NAMES]
    assert live.missing_points(lineup) == ["Rex Bee"]  # started at 1 pm, in nobody's export table


def test_a_player_ruled_out_scores_nothing_in_any_lineup():
    specs = specs_of(NAMES, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    kick = lf.kickoffs_from_edge(edge_of(NAMES, [LATE] * 9))
    out = lf.unavailable(pd.DataFrame({"Name": NAMES, "Avail": ["OUT"] + [None] * 7 + ["ir"]}))
    assert out == {"Quinn Ace", "Dallas D"}
    state = lf.slate_state(specs, kick, NOW, {}, out=out)
    draws = lf.simulate_remaining(state, n_sims=200)
    assert (draws.final[:, draws.column["Quinn Ace"]] == 0).all()
    assert draws.final[:, draws.column["Rex Ace"]].mean() > 5  # the rest of the slate still plays


def test_espn_game_progress_sets_the_share_of_a_started_game_left():
    specs = specs_of(NAMES, [20, 15, 12, 14, 12, 10, 9, 8, 7])
    kick = lf.kickoffs_from_edge(edge_of(NAMES, [EARLY] * 9))
    flat = lf.slate_state(specs, kick, NOW, {})
    assert flat.frac[0] == pytest.approx(1 - 0.5 / lf.GAME_HOURS)  # the flat clock: half an hour in
    by_clock = lf.slate_state(specs, kick, NOW, {}, progress={"AAA": 0.5, "BBB": 1.0})
    column = by_clock.column
    assert by_clock.frac[column["Quinn Ace"]] == pytest.approx(0.5)  # ESPN: halftime
    assert by_clock.frac[column["Rex Ace"]] == 0.0  # final
    assert by_clock.frac[column["Rex Bee"]] == pytest.approx(flat.frac[column["Rex Bee"]])  # team not named
