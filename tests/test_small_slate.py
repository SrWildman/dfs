"""Small-slate mode (`small_slate.py`): a slate is small by its game count (4 or fewer), read from its own
DraftKings draft group. A main slate (more games, or no synced draft group) behaves exactly as before; every
test here checks the main side next to the small one."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from dfs import cash_pairs, field_own, small_slate, store, week_plays
from dfs import late_swap_search as lss
from dfs.kickoff import parse_kickoff
from dfs.late_swap import lineup_slot_status
from dfs.lock_state import state as lock_state_at
from dfs.sources.base import SourcePending
from dfs.sources.tffb_projections import (
    TffbProjectionsFetchError,
    classic_names,
    off_day_games,
    pick_slate_name,
)

# Thanksgiving 2026 (Thu Nov 26, EST): three windows that never overlap, as TFFB writes GameStart (Eastern
# wall-clock time with a "Z").
WINDOWS = {
    ("DET", "CHI"): "2026-11-26T12:30:00Z",
    ("DAL", "KC"): "2026-11-26T16:30:00Z",
    ("BAL", "GB"): "2026-11-26T20:20:00Z",
}
MAIN_GAMES = [(f"A{i}", f"H{i}") for i in range(10)]


def draft_group(games: list[tuple[str, str]]) -> pd.DataFrame:
    """DraftKings' salary CSV shape, one row per team (enough for `Game Info`)."""
    rows = []
    for away, home in games:
        for team in (away, home):
            rows.append({"Name": f"{team} QB", "TeamAbbrev": team, "Game Info": f"{away}@{home} 11/26/2026"})
    return pd.DataFrame(rows)


def sync_draft_group(games: list[tuple[str, str]]) -> None:
    folder = store.current_dir()
    folder.mkdir(parents=True, exist_ok=True)
    draft_group(games).to_csv(folder / "draftkings.csv", index=False)


def slate_rows(games: list[tuple[str, str]], owns: list[float] | None = None) -> list[dict]:
    """Eight players a team, EdgeRaw-shaped, with a CashOwn spread like a small slate's."""
    rows = []
    positions = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "DST")
    k = 0
    for away, home in games:
        start = WINDOWS.get((away, home), "2026-11-29T13:00:00Z")
        for team, opp in ((away, home), (home, away)):
            for j, pos in enumerate(positions):
                own = owns[k % len(owns)] if owns else 0.05
                rows.append(
                    {
                        "Name": f"{team} {pos}{j}",
                        "Position": pos,
                        "Team": team,
                        "Opp": opp,
                        "Salary": 4000 + 500 * j,
                        "CalPts": 8.0 + j,
                        "ProjPts": 8.0 + j,
                        "CashOwn": own,
                        "GppOwn": own,
                        "Own%": own,
                        "Avail": "",
                        "GameStart": start,
                        "GameID": f"{away}@{home}",
                    }
                )
                k += 1
    return rows


THANKSGIVING = list(WINDOWS)
OWNS = [0.72, 0.55, 0.48, 0.41, 0.36, 0.31, 0.22, 0.12, 0.08, 0.05, 0.03, 0.02]


# -- the game count and what is small --


def test_games_are_counted_from_the_draft_group_or_team_pairs():
    assert small_slate.games_in_frame(draft_group(THANKSGIVING)) == 3
    assert small_slate.games_in_frame(pd.DataFrame(slate_rows(THANKSGIVING))) == 3
    assert small_slate.games_in_frame(pd.DataFrame(slate_rows(MAIN_GAMES))) == 10
    assert small_slate.SlateSize(3).small and small_slate.SlateSize(3).label == "3-game slate"
    assert not small_slate.SlateSize(5).small and not small_slate.SlateSize(0).small


def test_small_comes_from_the_synced_draft_group_and_the_frame_together():
    frame = pd.DataFrame(slate_rows(THANKSGIVING))
    assert small_slate.current() is None and not small_slate.frame_is_small(frame)  # nothing synced: main
    sync_draft_group(THANKSGIVING)
    assert small_slate.current().games == 3 and small_slate.frame_is_small(frame)
    # a 10-game frame read while the slate is small (a past main slate, during the playoffs) stays main
    assert not small_slate.frame_is_small(pd.DataFrame(slate_rows(MAIN_GAMES)))
    sync_draft_group(MAIN_GAMES)
    assert not small_slate.frame_is_small(frame)  # a main slate's draft group: a 3-team fixture stays main


# -- the slate's own chalk cut --


def test_the_chalk_cut_is_the_slate_s_top_tenth_never_under_the_fixed_line():
    values = np.linspace(0.01, 0.80, 40)
    cut = small_slate.slate_chalk_cut(values, 0.30)
    assert cut == pytest.approx(np.quantile(values, 0.9), abs=1e-4) and cut > 0.30
    assert small_slate.slate_chalk_cut([0.1] * 40, 0.30) == 0.30  # never under the fixed cut
    assert small_slate.slate_chalk_cut([0.9] * 5, 0.30) == 0.30  # too few owned players: the fixed cut


def test_the_page_cut_is_30_percent_on_a_main_slate_and_the_slate_s_own_on_a_small_one():
    small_rows = slate_rows(THANKSGIVING, OWNS)
    assert week_plays.slate_cut(small_rows) == week_plays.CHALK  # nothing synced: main
    sync_draft_group(THANKSGIVING)
    cut = week_plays.slate_cut(small_rows)
    assert cut > week_plays.CHALK
    main_rows = slate_rows(MAIN_GAMES, OWNS)
    assert week_plays.slate_cut(main_rows) == week_plays.CHALK
    # the plays view marks chalk at the slate's cut: a 36% player is chalk on a main slate, not here
    view = week_plays.plays(small_rows, per=50)
    flagged = {p.name: p.chalk for plays_ in view.cash.values() for p in plays_}
    owns = {r["Name"]: r["CashOwn"] for r in small_rows}
    assert all(flag == (owns[name] >= cut) for name, flag in flagged.items())


def test_the_edge_chalk_flag_cut_moves_only_on_a_small_slate():
    frame = pd.DataFrame(slate_rows(THANKSGIVING, OWNS))
    assert small_slate.column_cut(frame, "Own%", 0.20) == 0.20
    sync_draft_group(THANKSGIVING)
    assert small_slate.column_cut(frame, "Own%", 0.20) > 0.20


# -- TFFB's slate --


NAMES = ["Main", "Classic (Thu-Mon)", "Classic (Early Only)", "Week 12 Classic (Thu)", "Showdown (DET @ CHI)"]


def test_the_tffb_slate_is_the_one_named_for_the_day_never_a_range():
    assert pick_slate_name(NAMES, "thu") == "Week 12 Classic (Thu)"
    assert pick_slate_name(["Main", "Classic (Thanksgiving)"], "thu") == "Classic (Thanksgiving)"
    assert pick_slate_name(["Main", "Classic (Sat)"], "sat") == "Classic (Sat)"
    with pytest.raises(SourcePending):  # not posted yet: pending, not failed
        pick_slate_name(["Main", "Classic (Thu-Mon)"], "thu")
    with pytest.raises(TffbProjectionsFetchError):  # two could be it: never a guess
        pick_slate_name(["Classic (Thu)", "Week 12 Classic (Thursday)"], "thu")
    assert classic_names({"classic": [{"name": "Main"}, {"name": "X"}], "showdown": [{"name": "S"}]}) == [
        "Main",
        "X",
    ]


def test_a_tffb_slate_with_a_game_off_the_day_is_caught():
    players = [
        {"Game": "DET@CHI", "GameStart": "2026-11-26T12:30:00Z"},
        {"Game": "BAL@GB", "GameStart": "2026-11-26T20:20:00Z"},
    ]
    assert off_day_games(players, date(2026, 11, 26)) == []
    players.append({"Game": "A@H", "GameStart": "2026-11-29T13:00:00Z"})
    assert off_day_games(players, date(2026, 11, 26)) == ["A@H"]


# -- ownership --


def model() -> field_own.FieldOwnModel:
    totals = {"QB": 1.0, "RB": 2.4, "WR": 3.3, "TE": 1.3, "DST": 1.0}
    return field_own.FieldOwnModel({"gpp": 1.24, "cash": 1.81}, {"gpp": totals, "cash": totals}, 4.36, (1,))


def test_a_small_slate_follows_tffb_and_keeps_the_model_as_a_shadow():
    frame = pd.DataFrame(slate_rows(THANKSGIVING, OWNS))
    main = field_own.attach(frame, model())
    small = field_own.attach(frame, model(), small=True)
    pd.testing.assert_series_equal(main["CashOwn"], small["CashOwn"])  # the double-up model is the same
    for pos, total in model().totals["gpp"].items():  # sums to the position total: concentration stays
        assert small.loc[small.Position == pos, "GppOwn"].sum() == pytest.approx(total, abs=0.01)
    # TFFB's own shares (k = 1): within a position GppOwn is Own% rescaled, so the ratios are TFFB's
    qb = small[small.Position == "QB"]
    ratio = qb["GppOwn"] / qb["Own%"]
    assert ratio.max() == pytest.approx(ratio.min(), rel=1e-3)
    shadow = field_own.shadow(frame, model())
    assert list(shadow.columns) == ["Name", "Position", "Team", "Own%", "GppOwn", "ModelGppOwn"]
    top = shadow.iloc[0]
    assert top["ModelGppOwn"] > top["GppOwn"]  # the main-slate sharpening concentrates the chalk further


# -- the rules a small pool breaks --


def sp(name, pos, team, opp, salary=5000, value=10.0, game="") -> lss.SwapPlayer:
    return lss.SwapPlayer(name, pos, team, opp, game or "-".join(sorted((team, opp))), salary, value, "")


def test_two_rbs_from_one_game_and_a_qb_against_his_dst_are_allowed_only_when_relaxed():
    rb1, rb2 = sp("Rb One", "RB", "DET", "CHI"), sp("Rb Two", "RB", "CHI", "DET")
    qb, dst = sp("Qb", "QB", "DET", "CHI"), sp("Bears", "DST", "CHI", "DET")
    assert lss.conflicts(rb2, [rb1]) and lss.conflicts(dst, [qb])
    assert lss.hard_rules()
    with lss.relaxed_rules():
        assert not lss.hard_rules()
        assert not lss.conflicts(rb2, [rb1]) and not lss.conflicts(dst, [qb])
    with lss.relaxed_rules(False):  # a main slate passes its answer straight in: nothing changes
        assert lss.conflicts(rb2, [rb1])
    assert lss.conflicts(rb2, [rb1]) and lss.hard_rules()  # restored


def test_the_cheap_pair_is_flagged_but_kept_when_the_rule_is_off():
    pair = [sp("Wr A", "WR", "DET", "CHI", 3500), sp("Wr B", "WR", "DET", "CHI", 4500)]
    others = [sp(f"P{i}", "RB", f"T{i}", f"O{i}", 6000) for i in range(7)]
    lineup = [*pair, *others]
    called = []

    def search_free(keep):
        called.append(keep)
        return [others + [sp("Wr C", "WR", "KC", "DAL", 6500), sp("Wr D", "WR", "GB", "BAL", 6500)]]

    kept, flag = cash_pairs.choose([lineup], search_free, own={"Wr A": 0.3, "Wr B": 0.4}, enforce=False)
    assert kept == [lineup] and not called  # nothing searched again, nothing moved
    assert flag.kept and flag.why == cash_pairs.SMALL and "off on a small slate" in flag.text
    _, on = cash_pairs.choose([lineup], search_free, own={"Wr A": 0.3, "Wr B": 0.4})
    assert called and on.why == cash_pairs.EARNS  # the main slate's rule, unchanged


# -- the cash line --


def test_the_cash_band_says_the_line_is_not_calibrated_on_a_small_slate():
    from dfs import cash_core as cc

    main = cc.band(0.62, (14, 25), 9.5)
    small = cc.band(0.62, (14, 25), 9.5, small=True)
    assert main.word == cc.LEAN and main.text.startswith(cc.LEAN)
    assert small.word == "" and small.text.startswith("small slate: line not calibrated")
    assert "14 of 25" in small.text and small.shown == 0.62  # kept for the scorecard, never a word


# -- the page header --


def test_the_header_names_a_small_slate_and_says_what_changes():
    from types import SimpleNamespace

    from dfs.week_shell import small_slate_html

    rows = slate_rows(THANKSGIVING, OWNS)
    assert small_slate_html(SimpleNamespace(edge=rows)) == ("", "")
    sync_draft_group(THANKSGIVING)
    badge, note = small_slate_html(SimpleNamespace(edge=rows))
    assert "3-game slate" in badge and "Small slate" in note and "untested" in note
    assert small_slate.percent(week_plays.slate_cut(rows)) in note


# -- late swap game by game across three windows --


def edge_frame() -> pd.DataFrame:
    return pd.DataFrame(slate_rows(THANKSGIVING, OWNS))


LINEUP = ["DET QB0", "DET RB1", "KC RB2", "DAL WR3", "GB WR4", "BAL WR5", "KC TE6", "CHI WR3", "GB DST7"]
EARLY, MID, LATE = (
    datetime(2026, 11, 26, 18, 0, tzinfo=UTC),  # 1:00 pm ET: the 12:30 game is on
    datetime(2026, 11, 26, 22, 0, tzinfo=UTC),  # 5:00 pm ET: two windows started
    datetime(2026, 11, 27, 2, 0, tzinfo=UTC),  # 9:00 pm ET: all three
)


@pytest.mark.parametrize(
    ("now", "open_teams"),
    [(EARLY, {"KC", "DAL", "GB", "BAL"}), (MID, {"GB", "BAL"}), (LATE, set())],
)
def test_late_swap_opens_and_locks_window_by_window(now, open_teams):
    edge = edge_frame()
    statuses = lineup_slot_status(LINEUP, edge, now=now)
    state = lss.lineup_state(statuses, edge, cap=50000, metric="CalPts")
    assert {s.current.team for s in state.open} == {
        t for t in open_teams if t in {n.split()[0] for n in LINEUP}
    }
    pool = lss.swap_pool(edge, now=now, all_players=True)
    assert {p.team for p in pool} <= open_teams  # only unstarted games can be swapped in


def test_the_thursday_lock_state_runs_through_all_three_windows():
    starts = [parse_kickoff(s) for s in WINDOWS.values()]
    assert lock_state_at(datetime(2026, 11, 25, 22, 0, tzinfo=UTC), starts, None) == "build"  # Wednesday
    for now in (EARLY, MID, LATE):
        assert lock_state_at(now, starts, None) == "lock"
    assert lock_state_at(datetime(2026, 11, 27, 9, 0, tzinfo=UTC), starts, None) == "close"


def test_a_small_slate_late_swap_may_add_a_second_rb_from_one_game():
    edge = edge_frame()
    statuses = lineup_slot_status(LINEUP, edge, now=EARLY)
    state = lss.lineup_state(statuses, edge, cap=50000, metric="CalPts")
    pool = lss.swap_pool(edge, now=EARLY, all_players=True)
    with lss.relaxed_rules():
        relaxed = lss.best_refill(state, pool)
    strict = lss.best_refill(state, pool)
    assert relaxed is not None and strict is not None
    assert relaxed.delta >= strict.delta  # relaxing removes a rule, never adds one


# -- the plays model, the sim lines and the run's line --


def test_a_call_keeps_a_small_slate_s_cut_and_a_main_call_reads_as_before():
    from dfs import plays

    call = plays.Call("X", "WR", "DET", "CHI", 5000, 10.0, 0.40, 0.3, "CORE", 1.0, "", "PLAY", 1.0, "")
    assert "chalk_cut" not in plays.call_dict(call)
    small = plays.call_dict(_with_cut(call, 0.52))
    assert small["chalk_cut"] == 0.52
    # a 40% AVOID is held back at 30% (main), not at a small slate's 52%
    rec = {"cash": "AVOID", "cash_own": 0.40, "cash_reason": ""}
    assert plays.held_back(rec, "cash")
    assert not plays.held_back({**rec, "chalk_cut": 0.52}, "cash")


def _with_cut(call, cut):
    import dataclasses

    return dataclasses.replace(call, chalk_cut=cut)


def test_the_sim_lines_say_a_small_slate_s_line_is_not_calibrated():
    from dfs.sim_inputs import SimLines

    lines = SimLines("fallback", 110.0, 150.0, None, "", "", "Top 1%", small=True)
    assert lines.status.startswith("small slate: line not calibrated")  # no field yet: still not this slate's
    assert SimLines("fallback", 110.0, 150.0, None, "", "", "Top 1%").status.startswith("cash line")  # main
    assert small_slate.CASH_LINE_NOTE.startswith("small slate: line not calibrated")


def test_a_small_slate_without_ownership_never_shows_the_main_slate_s_line_as_its_own():
    from dfs import config as C
    from dfs import sim_inputs as si

    cash = si.CashLine(137.18, (2, 4), "season median of your typed Cash Line, Weeks 2, 4")

    def blank(games):
        rows = pd.DataFrame(slate_rows(games))
        return rows.assign(Id=[str(k) for k in range(len(rows))], **{"Own%": np.nan})

    sync_draft_group(THANKSGIVING)
    edge = blank(THANKSGIVING)
    lines = si.resolve_lines(edge, si.build_specs(edge), cash, 190.0, C.SimLinesConfig())
    assert lines.mode == "fallback" and lines.field is None and lines.small
    note = "small slate: line not calibrated"
    assert note in lines.note and note in lines.status
    assert lines.cash_basis.startswith(note) and "137.18" not in lines.cash_basis
    sync_draft_group(MAIN_GAMES)  # a main slate: the fixed wording, exactly as before
    edge = blank(MAIN_GAMES)
    lines = si.resolve_lines(edge, si.build_specs(edge), cash, 190.0, C.SimLinesConfig())
    assert not lines.small and "FIXED lines, cash line 137.18" in lines.note
    assert lines.cash_basis == "cash line 137.18, fixed (ownership not published yet)"


def test_the_run_line_names_what_a_small_slate_turns_off():
    from dfs.cli import _small_slate
    from dfs.config import Config, GoogleSheetsConfig, SmallSlateConfig

    cfg = Config(google_sheets=GoogleSheetsConfig(sheet_id="x", credentials_file="x", tab_mappings={}))
    assert cfg.small_slate == SmallSlateConfig() and cfg.small_slate.max_games == 4
    assert _small_slate(cfg).line() is None and _small_slate(cfg).enforce_pair  # nothing synced: main
    sync_draft_group(THANKSGIVING)
    small = _small_slate(cfg)
    assert small.small and small.relax_rules and not small.enforce_pair
    assert "3-game slate" in small.line() and "cheap-pair" in small.line()
    on = cfg.model_copy(update={"small_slate": SmallSlateConfig(cheap_pair=True, hard_rules=True)})
    assert _small_slate(on).enforce_pair and not _small_slate(on).relax_rules


# -- the GPP ownership meter --


def test_the_gpp_meter_band_is_the_slate_s_own_on_a_small_slate_only():
    from dfs import lineup_watch as lw

    assert lw.field_own_sum([0.5, 0.5, None, float("nan"), -1]) == pytest.approx(0.5)
    assert (
        lw.gpp_band([0.3] * 10, "3-game slate") is lw.MAIN_BAND
    )  # lighter than a main slate: the fixed band
    heavy = lw.gpp_band([0.6] * 10, "3-game slate")  # field lineup 360%: 2.75x a main slate's
    scale = 3.6 / lw.MAIN_FIELD_OWN_SUM
    assert heavy.low == pytest.approx(lw.GPP_OWN_LOW * scale) and heavy.high == pytest.approx(
        lw.GPP_OWN_HIGH * scale
    )
    assert lw.own_meter(3.70, "gpp", heavy) == "" and "over the 125%-145%" in lw.own_meter(3.70, "gpp")
    assert lw.own_meter(5.0, "gpp", heavy).endswith("GPP target (3-game slate's own)")
    assert lw.own_meter(5.0, "cash", heavy) == ""  # cash lineups still get no meter


def test_the_band_for_a_frame_is_fixed_on_a_main_slate():
    from dfs import lineup_watch as lw

    small_rows = pd.DataFrame(slate_rows(THANKSGIVING, OWNS))
    assert lw.band_for(small_rows) is lw.MAIN_BAND  # nothing synced: main
    assert lw.band_for(None) is lw.MAIN_BAND and lw.band_for(small_rows.drop(columns="Own%")) is lw.MAIN_BAND
    sync_draft_group(THANKSGIVING)
    band = lw.band_for(small_rows)
    assert band.slate == "3-game slate" and band.low > lw.GPP_OWN_LOW
    assert band.low == pytest.approx(
        lw.GPP_OWN_LOW * lw.field_own_sum(small_rows["Own%"]) / lw.MAIN_FIELD_OWN_SUM
    )
    assert lw.band_for(pd.DataFrame(slate_rows(MAIN_GAMES, OWNS))) is lw.MAIN_BAND
