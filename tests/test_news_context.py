"""Teammate news (`dfs.news_context`): Sam, 2026-11-09, "JSN had a backup QB, CeeDee did not have his WR2, so
he was for sure the main guy. I need that surfaced." Week 9's two situations, built small."""

from datetime import UTC, datetime

from dfs import news_context as nc
from dfs import week_board
from dfs.week_inputs import WeekInputs


def _g(name, team, pos, wk, snap, tgt=None):
    return {"Name": name, "Team": team, "Position": pos, "Wk": wk, "Snap%": snap, "Tgt%": tgt}


def _games():
    rows = []
    for wk in range(4, 9):
        rows += [
            _g("Sam Darnold", "SEA", "QB", wk, 1.0),
            _g("Drew Lock", "SEA", "QB", wk, 0.0) if wk == 8 else None,
            _g("Jaxon Smith-Njigba", "SEA", "WR", wk, 0.9, 0.34),
            _g("Rashid Shaheed", "SEA", "WR", wk, 0.5, 0.13),
            _g("AJ Barner", "SEA", "TE", wk, 0.9, 0.15),
            _g("Zach Charbonnet", "SEA", "RB", wk, 0.5, 0.06),  # not part of the passing game: no QB note
            _g("Dak Prescott", "DAL", "QB", wk, 1.0),
            _g("CeeDee Lamb", "DAL", "WR", wk, 0.8, 0.36),
            _g("George Pickens", "DAL", "WR", wk, 0.75, 0.19),
            _g("Ryan Flournoy", "DAL", "WR", wk, 0.75, 0.13),
            # gone since Week 4: his absence is old news, never a line
            _g("Long Gone", "DAL", "WR", wk, 0.6, 0.15) if wk == 4 else None,
        ]
    return [r for r in rows if r]


def _edge():
    def row(i, name, team, pos, sal, avail="", own=0.0, calpts=10.0):
        return {
            "Id": str(i),
            "Name": name,
            "Team": team,
            "Opp": "X",
            "Position": pos,
            "Salary": sal,
            "Avail": avail,
            "CashOwn": own,
            "GppOwn": own,
            "CalPts": calpts,
        }

    return [
        row(1, "Sam Darnold", "SEA", "QB", 5600, "OUT"),
        row(2, "Drew Lock", "SEA", "QB", 4000, calpts=17),
        row(3, "Jalen Milroe", "SEA", "QB", 4000, calpts=2),
        row(4, "Jaxon Smith-Njigba", "SEA", "WR", 9300, own=0.32),
        row(5, "Rashid Shaheed", "SEA", "WR", 4100, "OUT"),
        row(6, "AJ Barner", "SEA", "TE", 3800),
        row(7, "Zach Charbonnet", "SEA", "RB", 5000),
        row(8, "Dak Prescott", "DAL", "QB", 6500),
        row(9, "CeeDee Lamb", "DAL", "WR", 8300, own=0.41),
        row(10, "George Pickens", "DAL", "WR", 6300, "OUT"),
        row(11, "Ryan Flournoy", "DAL", "WR", 4800),
        row(12, "Long Gone", "DAL", "WR", 3000, "OUT"),
    ]


def test_a_backup_qb_is_on_every_pass_catcher_and_the_lead_target_is_named():
    news = nc.player_news(_edge(), _games())
    jsn = {f.kind: f for f in news["4"]}
    assert (
        jsn["qb"].text == "QB downgrade: Lock (backup) for Darnold (OUT)"
    )  # the healthy QB with the top CalPts
    assert jsn["qb"].short == "Backup QB: Lock" and jsn["qb"].tone == nc.DOWN
    assert news["4"][0].kind == "qb"  # the QB line comes first
    assert "qb" in {f.kind for f in news["6"]}  # Barner too
    assert "qb" not in {f.kind for f in news.get("7", [])}  # a RB at 6% of the targets is not a pass catcher
    assert [f.short for f in news["2"]] == ["Starting QB"]
    ceedee = {f.kind: f for f in news["9"]}
    assert ceedee["mate_out"].text == "Teammate Pickens out (WR2, 19% of DAL's targets)"
    assert ceedee["lead"].text == "Lead target: 36% of DAL's targets; next healthy Flournoy 13%"
    assert "qb" not in ceedee  # Dak plays


def test_an_old_absence_and_a_long_lost_starter_are_not_news():
    texts = [f.text for fs in nc.player_news(_edge(), _games()).values() for f in fs]
    assert not any("Gone" in t for t in texts)  # out since Week 4: the role has moved
    # the starter is whoever started the most recent game: Darnold out since Week 6 makes Lock the starter
    games = [g for g in _games() if not (g["Name"] == "Sam Darnold" and g["Wk"] >= 6)]
    games += [_g("Drew Lock", "SEA", "QB", wk, 1.0) for wk in (6, 7, 8)]
    texts = [f.text for fs in nc.player_news(_edge(), games).values() for f in fs]
    assert not any("QB downgrade" in t for t in texts)


def test_questionable_reads_may_miss_and_the_report_fills_in_for_dk():
    edge = [dict(r, Avail="") if r["Name"] == "Sam Darnold" else r for r in _edge()]
    report = [{"Name": "Sam Darnold", "Team": "SEA", "report_status": "Questionable"}]
    jsn = {f.kind: f for f in nc.player_news(edge, _games(), report)["4"]}
    assert jsn["qb"].tone == nc.WARN and jsn["qb"].text.startswith("QB may miss: Darnold is Questionable")
    lines = nc.team_lines(_edge(), _games())
    assert lines["SEA"][0] == "QB downgrade: Lock (backup) for Darnold (OUT)"
    assert "CeeDee Lamb: Lead target: 36% of DAL's targets; next healthy Flournoy 13%" in lines["DAL"]


def test_the_brief_says_it_for_the_chalk():
    edge = _edge()
    inp = WeekInputs(
        season=2026,
        week=9,
        now=datetime(2026, 11, 5, 12, tzinfo=UTC),
        data_dir=None,
        edge=edge,
        news=nc.player_news(edge, _games()),
    )
    s = week_board.news_sentence(inp)
    assert s is not None and s.key == "news"
    assert s.text == (
        "Team news: catching from a backup QB, {Jaxon Smith-Njigba} (Lock for Darnold, OUT); "
        "leading the targets with a teammate out, {CeeDee Lamb} 36% (Pickens out) and "
        "{Jaxon Smith-Njigba} 34% (Shaheed out)."
    )  # the most owned first in each list
    assert week_board.news_sentence(WeekInputs(2026, 9, inp.now, None, edge=edge)) is None
