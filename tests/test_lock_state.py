"""The Lock state (blueprint Phase C2): the clock, the baseline, netting, the change list's order and swaps,
the checklist and the late window. Pure: rows in, dataclasses out."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
from test_inactives import EARLY, LATE, LINEUP, row

from dfs import inactives as ina
from dfs import lock_state as ls
from dfs import news_log

KICK = datetime(2026, 10, 25, 17, 0, tzinfo=UTC)  # 1:00 pm ET (EDT)
MORNING = datetime(2026, 10, 25, 15, 0, tzinfo=UTC)  # 11:00 am ET: before every kickoff
BUILT = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)  # Saturday 6:00 pm ET
NAMES = tuple(n for n, *_ in LINEUP)


def edge(**status) -> list[dict]:
    rows = []
    for i, (n, p, s, k) in enumerate(LINEUP):
        r = row(n, p, s, k, status.get(n, ""))
        rows.append({**r, "Id": str(100 + i), "Opp": "MIN", "CashOwn": 0.2, "GppOwn": 0.1})
    extra = [
        ("Wr Cheap", "WR", 5500, LATE, 9.0, 20),
        ("Wr Exact", "WR", 8000, LATE, 15.0, -5),
        ("Wr Bench", "WR", 3000, EARLY, 4.0, 0),
        ("Rb Late", "RB", 7000, LATE, 14.0, 10),
    ]
    for j, (n, p, s, k, cal, lev) in enumerate(extra):
        r = row(n, p, s, k, status.get(n, ""), calpts=cal, leverage=lev)
        rows.append({**r, "Id": str(200 + j), "Opp": "MIN", "CashOwn": 0.05, "GppOwn": 0.04})
    return rows


def change(name, field, before, after, at, pid=None, team="GB", pos="WR") -> dict:
    ids = {r["Name"]: r["Id"] for r in edge()}
    return {
        "at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": "2026",
        "week": "7",
        "player_id": pid or ids[name],
        "name": name,
        "position": pos,
        "team": team,
        "field": field,
        "before": before,
        "after": after,
        "source": "dk",
        "snapshot": "x",
    }


def lineup(label="L1", kind="cash") -> ina.Source:
    return ina.Source(label, kind, NAMES)


# -- the clock -----------------------------------------------------------------------------------------------


def test_the_state_follows_the_main_slate_clock():
    starts = [KICK, KICK + timedelta(hours=3, minutes=25)]
    assert ls.state(KICK - timedelta(hours=14), starts, None) == "build"
    assert ls.state(KICK - timedelta(hours=13), starts, None) == "lock"  # midnight ET for a 1:00 pm slate
    assert ls.state(KICK + timedelta(hours=1), starts, None) == "lock"
    assert ls.state(starts[-1] + timedelta(hours=3, minutes=30), starts, None) == "close"
    assert ls.state(MORNING, starts, MORNING) == "close"  # the week is closed
    assert ls.state(MORNING, [], None) == "build"  # nothing synced


# -- the baseline --------------------------------------------------------------------------------------------


def rounds() -> list[dict]:
    other = ("A", "B", "C", "D", "E", "F", "G", "H", "I")
    return [
        {"round": 3, "at": BUILT, "builds": [list(NAMES)]},
        {"round": 13, "at": MORNING - timedelta(minutes=30), "builds": [list(other)]},
    ]


def test_the_baseline_is_the_round_his_lineups_came_from_not_a_later_rebuild():
    base = ls.baseline(rounds(), [NAMES], KICK, MORNING)
    assert base.at == BUILT
    assert "round 3" in base.label


def test_eight_of_nine_still_comes_from_the_build():
    edited = (*NAMES[:8], "Somebody Else")
    assert ls.source_round(edited, rounds()) == 3
    assert ls.source_round(("Nobody",) * 9, rounds()) is None


def test_a_hand_built_lineup_falls_back_to_the_latest_round_and_no_build_to_saturday_night():
    base = ls.baseline(rounds(), [("Nobody",) * 9], KICK, MORNING)
    assert "round 13" in base.label
    sat = ls.baseline([], [], KICK, MORNING)
    assert sat.at == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)  # Sat 9:00 pm EDT
    assert "Saturday 9:00 pm" in sat.label


def test_a_round_after_now_does_not_exist_yet():
    base = ls.baseline(rounds(), [("Nobody",) * 9], KICK, MORNING - timedelta(hours=1))
    assert "round 3" in base.label


# -- netting -------------------------------------------------------------------------------------------------


def test_netting_takes_the_first_before_and_the_last_after_and_drops_a_round_trip():
    t = BUILT + timedelta(hours=10)
    rows = [
        change("Wr One", "calpts", "12.0", "11.0", t),
        change("Wr One", "calpts", "11.0", "9.0", t + timedelta(minutes=1)),
        change("Wr Two", "status", "", "Q", t),
        change("Wr Two", "status", "Q", "", t + timedelta(minutes=1)),
        change("Wr Three", "calpts", "5.0", "9.0", BUILT - timedelta(hours=1)),  # before the baseline
    ]
    nets = ls.net_changes(pd.DataFrame(rows), BUILT)
    assert [(n.name, n.field, n.before, n.after) for n in nets] == [("Wr One", "calpts", "12.0", "9.0")]
    assert nets[0].seen == t + timedelta(minutes=1)


def test_netting_reads_rows_news_log_writes():
    before = pd.DataFrame(edge())
    after = pd.DataFrame(edge(**{"Wr One": "OUT"}))
    found = news_log.diff(before, after, at=BUILT + timedelta(hours=12), snapshot="20261025T100000Z")
    nets = ls.net_changes(pd.DataFrame(found), BUILT)
    assert [(n.name, n.field, n.after) for n in nets if n.field == "status"] == [("Wr One", "status", "OUT")]


# -- the change list -----------------------------------------------------------------------------------------


def listing(rows_, now=MORNING, sources=None, pool=None, **kw):
    nets = ls.net_changes(pd.DataFrame(rows_), BUILT)
    return ls.rows(nets, edge(**kw.pop("status", {})), sources or [lineup()], pool or {}, now, **kw)


def test_a_late_scratch_in_a_lineup_is_the_first_row_with_its_swap():
    t = BUILT + timedelta(hours=12)
    got = listing(
        [
            change("Wr Cheap", "calpts", "9.0", "15.0", t, pid="200", team="MIN"),  # pooled, no lineup
            change("Wr One", "status", "", "OUT", t),
            change("Wr One", "calpts", "10.0", "0.0", t),
        ],
        pool={"Wr Cheap": "Both"},
        status={"Wr One": "OUT"},
    )
    top = got.act[0]
    assert top.what == "Wr One OUT"
    assert top.touches == ("L1",)
    assert top.swap is not None and top.swap.into.name == "Wr Exact"  # freed 6,000 + 2,000 left
    assert top.swap.gain == 15.0  # his current CalPts counts 0 once he is OUT
    assert top.stake == 15.0
    assert [r.name for r in got.none] == ["Wr Cheap"]  # pool-only rows come after lineup rows


def test_a_player_whose_game_started_needs_no_action():
    t = BUILT + timedelta(hours=12)
    got = listing(
        [change("Wr Three", "status", "", "OUT", t)],
        now=KICK + timedelta(minutes=30),
        status={"Wr Three": "OUT"},
    )
    assert got.act == []
    assert got.none[0].started and got.none[0].name == "Wr Three"


def test_a_live_problem_stays_listed_whenever_it_happened():
    # Q since Thursday: no change after the baseline, but he is in the lineup (Sam, 2026-11-17)
    thursday = BUILT - timedelta(days=2)
    got = ls.rows(
        [],
        edge(**{"Te One": "Q"}),
        [lineup()],
        {},
        MORNING,
        status_seen={"106": thursday},
    )
    [r] = got.all
    assert r.what == "Te One Questionable" and r.sticky and r.seen == thursday


def test_a_live_problem_in_the_pool_or_the_default_is_kept_too_but_not_elsewhere():
    e = edge(**{"Rb Late": "D", "Wr Bench": "OUT", "Wr Cheap": "Q"})
    got = ls.rows([], e, [], {"Rb Late": "GPP"}, MORNING, default_names=["Wr Bench"])
    assert sorted(r.name for r in got.all) == ["Rb Late", "Wr Bench"]
    assert next(r for r in got.all if r.name == "Wr Bench").touches == (ls.DEFAULT_LABEL,)


def test_projection_and_ownership_rows_and_the_other_count():
    t = BUILT + timedelta(hours=12)
    got = listing(
        [
            change("Wr Two", "calpts", "12.0", "10.5", t),  # in the lineup: a projection row
            change("Wr Three", "cash_own", "0.31", "0.40", t),  # in the lineup: an ownership row
            change("Wr Bench", "calpts", "4.0", "8.0", t, pid="202"),  # nobody's: counted, not listed
            change("Rb One", "calpts", "10.0", "10.4", t, pos="RB"),  # under MIN_MOVE: nothing
        ]
    )
    whats = {r.name: r.what for r in got.all}
    assert whats["Wr Two"] == "Wr Two: proj 12.0 to 10.5"
    assert whats["Wr Three"] == "Wr Three: cash own 31% to 40%"
    assert "Rb One" not in whats
    assert got.other == 1


def test_a_rise_never_offers_a_swap():
    t = BUILT + timedelta(hours=12)
    got = listing([change("Wr Two", "calpts", "8.0", "10.0", t)])
    assert got.act == [] and got.none[0].swap is None


def test_a_status_row_names_the_teammates_who_moved():
    t = BUILT + timedelta(hours=12)
    got = listing(
        [
            change("Wr One", "status", "", "OUT", t),
            change("Wr Two", "calpts", "10.0", "13.5", t),
            change("Wr Two", "cash_own", "0.10", "0.28", t),
        ],
        status={"Wr One": "OUT"},
    )
    out = next(r for r in got.all if r.name == "Wr One")
    assert [(m.name, m.d_points) for m in out.mates] == [("Wr Two", 3.5)]


def test_ties_break_newest_first():
    t = BUILT + timedelta(hours=12)
    got = listing(
        [
            change("Wr Two", "calpts", "10.0", "12.0", t),
            change("Wr Three", "calpts", "10.0", "12.0", t + timedelta(minutes=5)),
        ]
    )
    assert [r.name for r in got.all] == ["Wr Three", "Wr Two"]


def test_two_lineups_from_one_build_get_their_own_labels():
    got = ls.unique_labels([lineup("Cash build 1"), lineup("Cash build 1")])
    assert [s.label for s in got] == ["Cash build 1", "Cash build 1 (2)"]


# -- the checklist -------------------------------------------------------------------------------------------


def test_the_checklist_catches_an_inactive_a_late_player_outside_flex_and_salary_slack():
    e = edge(**{"Wr One": "OUT"})
    # Flex Rb is LATE, Rb Two is EARLY, so the FLEX already holds a late RB: swap Rb One (LATE) in? No:
    # both Rb One and Flex Rb are late, so put the FLEX on an early RB to make the rule fail.
    for r in e:
        if r["Name"] == "Flex Rb":
            r["GameStart"] = EARLY
    checks = {c.key: c for c in ls.checklist(e, [lineup()], None, MORNING)}
    assert checks["inactive"].ok is False and "Wr One (OUT)" in checks["inactive"].detail
    assert checks["flex"].ok is False and "move Rb One to FLEX" in checks["flex"].detail
    assert checks["entries"].ok is None  # nothing recorded before lock: not known, never a request
    assert "record" not in checks["entries"].detail.split("once")[0]
    assert checks["salary"].ok is False and "$2,000 unspent" in checks["salary"].detail


def test_a_clean_lineup_passes_and_blank_entries_fail():
    e = edge()
    for r in e:  # spend the last $2,000
        if r["Name"] == "Home DST":
            r["Salary"] = 5000
    entered = {
        "lineups": [
            {
                "names": list(NAMES),
                "contests": [
                    {
                        "contest_id": "1",
                        "name": "DU",
                        "format": "Double Up",
                        "entry_fee": "$5",
                        "entry_ids": ["9"],
                    }
                ],
            }
        ],
        "blank": [
            {"contest_id": "2", "name": "Free", "format": "Freeroll", "entry_fee": "$0", "entry_ids": ["8"]}
        ],
    }
    checks = {c.key: c for c in ls.checklist(e, [lineup()], entered, MORNING)}
    assert checks["inactive"].ok and checks["flex"].ok and checks["salary"].ok
    assert checks["entries"].ok is False and "1 blank (Freeroll" in checks["entries"].detail


def test_no_lineups_is_unknown_not_fine():
    assert {c.ok for c in ls.checklist(edge(), [], None, MORNING)} == {None}


# -- the late window and the strip ---------------------------------------------------------------------------


def test_the_late_window_lists_unstarted_late_players_with_a_better_pivot():
    [lw] = ls.late_window(edge(), [lineup()], KICK + timedelta(minutes=10))
    names = [i.name for i in lw.items]
    assert "Qb One" not in names and "Wr One" in names  # early games are gone
    wr_one = next(i for i in lw.items if i.name == "Wr One")
    assert wr_one.alt is not None and wr_one.alt.name == "Wr Exact" and wr_one.gain == 5.0


def test_tiles_say_how_long_to_lock_and_flag_old_data_on_sunday():
    starts = [KICK, KICK + timedelta(hours=3, minutes=25)]
    t = ls.time_tile("lock", KICK - timedelta(minutes=20), starts)
    assert t.value == "20 min" and "4:25 pm" in t.note
    old = ls.news_tile(edge(**{"Wr Two": "Q"}), [lineup()], MORNING, MORNING - timedelta(hours=4))
    assert old.value == "1 Q/D" and old.tone == "warn"
    assert ls.health_tile([ls.Check("x", True, "a", "b")]).value == "OK"


def test_a_teammates_news_touches_his_lineups_and_can_offer_his_swap():
    """The prototype's Pickens row: a player in no lineup goes OUT; a teammate in the lineup lost points,
    so the row touches that lineup and the swap is the teammate's."""
    t = BUILT + timedelta(hours=12)
    e = edge(**{"Wr Bench": "OUT"})
    for r in e:
        if r["Name"] == "Wr Bench":
            r["CalPts"] = 8.0  # a real player, not a backup
        if r["Name"] == "Wr Two":
            r["CalPts"] = 7.0  # Wr Cheap (9.0, $5,500) now beats him within the $7,000 he can spend
    rows_ = [
        change("Wr Bench", "status", "", "OUT", t, pid="202"),
        change("Wr Two", "calpts", "10.0", "7.0", t),  # the same team, in L1
    ]
    got = ls.rows(ls.net_changes(pd.DataFrame(rows_), BUILT), e, [lineup()], {}, MORNING)
    [row] = got.act
    assert row.name == "Wr Bench" and row.touches == ("L1",)
    assert [m.name for m in row.mates] == ["Wr Two"]
    assert row.swap is not None and row.swap.out == "Wr Two"
    assert all(r.name != "Wr Two" for r in got.all)  # absorbed into the status row, not a row of his own


def test_a_backups_status_change_is_not_a_row_but_a_live_problem_always_is():
    t = BUILT + timedelta(hours=12)
    got = listing([change("Wr Bench", "status", "", "OUT", t, pid="202")], status={"Wr Bench": "OUT"})
    assert got.all == [] and got.other == 1  # Wr Bench projects 4 pts: a backup
    kept = ls.rows([], edge(**{"Wr Bench": "Q"}), [], {"Wr Bench": "Cash"}, MORNING)
    assert [r.name for r in kept.all] == [
        "Wr Bench"
    ]  # in the pool and Questionable: kept whatever he projects


def test_an_ownership_rise_is_answered_only_by_a_less_owned_pivot():
    """A GPP lineup's player got chalkier: the swap must lower the ownership, never raise it."""
    t = BUILT + timedelta(hours=12)
    rows_ = [change("Wr Two", "gpp_own", "0.05", "0.20", t)]

    def run(cheap_own: float):
        e = edge()
        for r in e:
            if r["Name"] == "Wr Two":
                r["CalPts"], r["GppOwn"] = 7.0, 0.20
            if r["Name"] == "Wr Cheap":
                r["GppOwn"] = cheap_own
        return ls.rows(ls.net_changes(pd.DataFrame(rows_), BUILT), e, [lineup(kind="gpp")], {}, MORNING)

    [row_] = run(0.04).act
    assert row_.swap is not None and row_.swap.into.name == "Wr Cheap"
    chalky = run(0.35)
    assert chalky.act == [] or all(r.swap.into.name != "Wr Cheap" for r in chalky.act)


def test_a_teammate_swap_shows_once_on_the_worse_news():
    """Two status rows of one team share a teammate's swap: it shows on the player ruled out, not on the one
    cleared, and the second row moves to No action needed."""
    t = BUILT + timedelta(hours=12)
    e = edge(**{"Wr Bench": "OUT"})
    for r in e:
        if r["Name"] == "Wr Bench":
            r["CalPts"] = 8.0
        if r["Name"] == "Wr Two":
            r["CalPts"] = 7.0
    e.append({**next(r for r in e if r["Name"] == "Wr Bench"), "Name": "Wr Back", "Id": "299", "Avail": ""})
    rows_ = [
        change("Wr Back", "status", "Q", "", t, pid="299"),
        change("Wr Bench", "status", "", "OUT", t, pid="202"),
        change("Wr Two", "calpts", "10.0", "7.0", t),
    ]
    got = ls.rows(ls.net_changes(pd.DataFrame(rows_), BUILT), e, [lineup()], {}, MORNING)
    assert [r.name for r in got.act] == ["Wr Bench"]
    assert "Wr Back" in [r.name for r in got.none]


def test_a_past_nfl_week_is_closed_whatever_slate_the_page_reads():
    """`dfs week page --week 9` on Week 11's Tuesday reads the current slate (another week's kickoffs): the
    week is over, so the page opens on its close (blueprint Phase D)."""
    tue_after = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)  # Tuesday 1 am ET, the next NFL week has begun
    assert ls.week_over(2026, 9, tue_after) is not None
    assert ls.week_over(2026, 9, datetime(2026, 11, 9, 23, 0, tzinfo=UTC)) is None  # Monday night: not yet
    assert ls.week_over(2026, 11, tue_after) is None
