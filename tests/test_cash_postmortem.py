"""Plan D item 8: the cash post-mortem (week close) and the cash chalk check (lineups suggest), on fixture
double-up standings."""

from __future__ import annotations

import pandas as pd

from dfs import cash_postmortem as cp
from dfs.contests import standings as st

HEADER = "Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS"
POS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST"]

# Ten players' real points; "Chalk Back" is in every lineup, "Star Wide" separates the cashers.
FPTS = {
    "Quinn Ace": 20.0,
    "Rex Ace": 15.0,
    "Rex Bee": 10.0,
    "Walt Ace": 12.0,
    "Walt Bee": 9.0,
    "Walt Cee": 8.0,
    "Tom Ace": 6.0,
    "Star Wide": 30.0,
    "Dallas D": 5.0,
    "Walt Dee": 2.0,
}
BASE = ["Quinn Ace", "Rex Ace", "Rex Bee", "Walt Ace", "Walt Bee", "Walt Cee", "Tom Ace"]
WITH_STAR = [*BASE, "Star Wide", "Dallas D"]  # 115
WITHOUT = [*BASE, "Walt Dee", "Dallas D"]  # 87


def _text(names):
    qb, rb1, rb2, wr1, wr2, wr3, te, flex, dst = names
    return f"DST {dst} FLEX {flex} QB {qb} RB {rb1} RB {rb2} TE {te} WR {wr1} WR {wr2} WR {wr3}"


def standings(lineups, entrants):
    players = list(FPTS.items())
    rows = []
    for i in range(max(len(lineups), len(players))):
        left = ["", "", "", "", "", ""]
        if i < len(lineups):
            pts = sum(FPTS[n] for n in lineups[i])
            left = [
                str(i + 1),
                str(1000 + i),
                f"{entrants[i]} (1/1)",
                "0",
                f"{pts:.2f}",
                f'"{_text(lineups[i])}"',
            ]
        right = ["", "", "", ""]
        if i < len(players):
            name, fpts = players[i]
            right = [name, "WR", "50.00%", f"{fpts}"]
        rows.append(",".join([*left, "", *right]))
    raw = "\n".join([HEADER, *rows]).encode()
    return st.parse_standings(raw, contest_id="9", places_paid=2)


def _edge():
    names = list(FPTS)
    return pd.DataFrame(
        {
            "Name": names,
            "Position": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST", "WR"],
            "Salary": [6000, 7000, 5000, 6000, 5000, 4000, 4000, 8000, 3000, 3000],
            "CalPts": [20, 16, 12, 14, 11, 9, 8, 18, 6, 7],
            "CashOwn": [0.5, 0.6, 0.4, 0.3, 0.2, 0.1, 0.1, 0.35, 0.2, 0.05],
        }
    )


def _inputs(builds, pool=None, entered=()):
    lineups = [WITH_STAR, WITH_STAR, WITHOUT, WITHOUT]
    du = cp.du_contest(standings(lineups, ["a", "b", "srwildman", "c"]))
    return cp.WeekInputs(2026, 8, [du], builds, pool, _edge(), list(entered))


def test_du_contest_reads_the_standings_and_their_points():
    du = cp.du_contest(standings([WITH_STAR, WITHOUT], ["a", "srwildman"]))
    assert du.cash_line == 87.0  # 2 places paid of 2: the second-best score
    assert du.fpts[cp.key("Star Wide")] == 30.0
    assert sorted(p for _, p, _ in du.lineups) == [87.0, 115.0]


def test_the_separator_is_tagged_and_the_builds_are_scored():
    builds = [
        cp.CashBuild(tuple(WITH_STAR), "build 1 (round 1)", 0.7),
        cp.CashBuild(tuple(WITHOUT), "build 2 (round 1)"),
    ]
    pm = cp.postmortem(_inputs(builds, entered=[(tuple(WITHOUT), "entries record")]))
    assert (pm.lineups, pm.cashers, pm.cash_line) == (4, 2, 115.0)
    top = pm.gaps[0]
    assert top.name == "Star Wide" and (top.in_cashers, top.in_non_cashers) == (1.0, 0.0)
    assert top.tag == cp.IN_BUILDS and top.builds == 1 and top.tag_text == "in 1 cash build"
    assert top.calpts_rank == 1 and top.cash_own == 0.35 and top.field == 0.5
    assert [(s.build.label, s.points, s.cashed) for s in pm.builds] == [
        ("build 1 (round 1)", 115.0, True),
        ("build 2 (round 1)", 87.0, False),
    ]
    (d,) = pm.entered
    assert d.nearest.build.label == "build 2 (round 1)" and d.out == [] and d.into == []
    text = "\n".join(cp.report_lines(pm))
    assert "1 of 2 distinct would have cashed" in text and "the same nine as the build" in text


def test_a_player_no_cash_build_used_is_pool_only_or_missed():
    builds = [cp.CashBuild(tuple(WITHOUT), "build 1 (round 1)")]
    pm = cp.postmortem(_inputs(builds, pool={cp.key("Star Wide"): "GPP"}, entered=[(tuple(WITHOUT), "x")]))
    assert pm.gaps[0].tag == cp.POOL_ONLY and pm.gaps[0].tag_text == "pool only (GPP)"
    assert cp.postmortem(_inputs(builds, pool={})).gaps[0].tag == cp.MISSED
    assert pm.outside_builds == (1.0, 0.0)  # cashers had Star Wide outside our builds
    d = pm.entered[0]
    assert d.points == 87.0 and not d.cashed


def test_entered_lineup_against_its_nearest_build_names_the_swaps():
    builds = [cp.CashBuild(tuple(WITH_STAR), "build 1 (round 1)")]
    pm = cp.postmortem(_inputs(builds, entered=[(tuple(WITHOUT), "double-up standings")]))
    d = pm.entered[0]
    assert d.out == [("Star Wide", 30.0)] and d.into == [("Walt Dee", 2.0)] and d.cost == 28.0
    assert "left out Star Wide (30.0); put in Walt Dee (2.0): cost 28.0 pts" in "\n".join(cp.report_lines(pm))
    assert cp._points(114.08, 114.12) == "114.08 (line 114.12)" and cp._points(110.0, 114.1) == "110.0"


def test_no_build_record_says_so():
    pm = cp.postmortem(_inputs(None))
    assert pm.builds is None and pm.gaps[0].tag == "" and "no build record" in pm.gaps[0].tag_text
    assert "Cash builds: no build record for the week." in "\n".join(cp.report_lines(pm))


def test_the_running_file_replaces_a_rerun_week(tmp_path):
    builds = [cp.CashBuild(tuple(WITH_STAR), "b")]
    pm = cp.postmortem(_inputs(builds))
    path = tmp_path / "pm.csv"
    cp.append_csv(pm, path)
    cp.append_csv(pm, path)
    other = cp.postmortem(cp.WeekInputs(2026, 7, _inputs(builds).contests, builds, None, _edge(), []))
    cp.append_csv(other, path)
    df = pd.read_csv(path)
    assert list(df.columns) == cp.CSV_COLUMNS
    assert sorted(df["week"].unique()) == [7, 8] and len(df[df["week"] == 8]) == len(pm.gaps)
    assert df.iloc[0]["tag"] in (cp.IN_BUILDS, cp.MISSED, cp.POOL_ONLY)


def test_cash_builds_of_keeps_distinct_cash_builds():
    from dfs import suggest_state as ss

    def rnd(n, builds):
        return ss.RoundRecord(round=n, at="", command="suggest", rules={}, builds=builds)

    a = ss.BuildRecord(number=1, kind="cash", title="", names=WITH_STAR, metrics={"p_cash": 0.7})
    g = ss.BuildRecord(number=2, kind="gpp", title="", names=WITHOUT)
    again = ss.BuildRecord(number=4, kind="cash", title="", names=list(reversed(WITH_STAR)))
    state = ss.StateFile(week=8, rounds=[rnd(1, [a, g]), rnd(2, [again])])
    (only,) = cp.cash_builds_of(state)
    assert only.label == "build 1 (round 1)" and only.shown_cash == 0.7


def test_chalk_check_counts_cash_builds_for_every_double_up_chalk_player():
    lines = cp.chalk_check(_edge(), [WITH_STAR, WITHOUT])
    assert lines[0].startswith("Cash chalk check (CashOwn 30%+")
    body = "\n".join(lines[1:])
    assert "Rex Ace" in body and "in 2 of 2 cash builds" in body
    assert "Star Wide" in body and "in 1 of 2" in body
    assert "Walt Bee" not in body  # 20%: under the line
    assert cp.chalk_check(_edge().drop(columns="CashOwn"), [WITH_STAR]) == []
    assert cp.chalk_check(_edge(), []) == []
    fade = cp.chalk_check(_edge(), [WITHOUT])
    assert any("Star Wide" in line and "a fade" in line for line in fade)


def test_the_week_page_payload_has_a_headline_and_a_line_per_separator():
    builds = [
        cp.CashBuild(tuple(WITH_STAR), "build 1 (round 1)"),
        cp.CashBuild(tuple(WITHOUT), "build 2 (round 1)"),
    ]
    pm = cp.postmortem(_inputs(builds, entered=[(tuple(WITHOUT), "x")]))
    p = cp.week_page_payload(pm)
    assert p["headline"] == "1 of our 2 cash builds would have cashed (line 115.0, best 115.0)"
    assert p["lines"][0].startswith("Star Wide: in 1 cash build, in 100% of cashers vs 0% of non-cashers")
    assert p["lines"][0].endswith("missing from your entry")
    assert "no build record" in cp.week_page_payload(cp.postmortem(_inputs(None)))["headline"]
