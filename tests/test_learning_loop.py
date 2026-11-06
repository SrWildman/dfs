"""Plan K2's pure parts: what won (field_review), the process scorecard, exposure against the field, how good
are our numbers, the salary watch, and `week_review`'s joins (fake contests, no files)."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from dfs import exposure, field_review, numbers, salary_watch, scorecard, week_review

# -- salary watch --------------------------------------------------------------------------------------------


def games(snaps, tgts=(None, None, None)):
    return [{"Snap": s, "Tgt": t} for s, t in zip(snaps, tgts, strict=True)]


def test_a_steady_role_is_snaps_or_targets_within_ten_percent_of_their_average():
    assert salary_watch.steady_role(games([0.80, 0.75, 0.78])) == "snaps 80 / 75 / 78%"
    assert salary_watch.steady_role(games([0.9, 0.5, 0.7], [10, 10, 11])) == "targets 10 / 10 / 11"
    assert salary_watch.steady_role(games([0.9, 0.5, 0.7], [2, 6, 7])) == ""
    assert salary_watch.steady_role(games([0.8, 0.8, 0.8])[:2]) == ""  # fewer than three games


def test_the_watch_flags_a_300_drop_with_a_steady_role_and_lists_the_rest_apart():
    edge = [
        {"Id": "1", "Name": "Steady Guy", "Position": "WR", "Team": "KC", "Salary": 4400},
        {"Id": "2", "Name": "Moved Guy", "Position": "RB", "Team": "LV", "Salary": 5000},
        {"Id": "3", "Name": "Small Drop", "Position": "WR", "Team": "NE", "Salary": 3800},
        {"Id": "4", "Name": "New Guy", "Position": "TE", "Team": "NE", "Salary": 3000},
        {"Id": "5", "Name": "Defense", "Position": "DST", "Team": "NYJ", "Salary": 2500},
    ]
    before = {
        salary_watch.key("Steady Guy", "KC"): 4900,
        salary_watch.key("Moved Guy", "LV"): 5800,
        salary_watch.key("Small Drop", "NE"): 4000,
        salary_watch.key("Defense", "NYJ"): 3200,
    }
    log = {"1": games([0.94, 0.87, 0.90]), "2": games([0.40, 0.72, 0.58], [4, 8, 6])}
    v = salary_watch.watch(edge, before, log)
    assert v["found"] and v["matched"] == 4 and v["drops"] == 3
    assert [m["name"] for m in v["flagged"]] == ["Steady Guy"]
    assert v["flagged"][0]["change"] == -500 and v["flagged"][0]["role"].startswith("snaps")
    others = {m["name"]: m for m in v["other_drops"]}
    assert set(others) == {"Moved Guy", "Defense"}
    assert others["Defense"]["logged"] is False and others["Moved Guy"]["logged"] is True


def test_without_last_weeks_salaries_there_is_no_watch():
    assert salary_watch.watch([{"Name": "A", "Team": "X", "Salary": 1}], None, {})["found"] is False


# -- exposure ------------------------------------------------------------------------------------------------


def test_exposure_counts_each_entry_and_names_the_chalk_he_skipped():
    entries = [("A", "B"), ("A", "C"), ("A", "B"), ("D", "B")]
    v = exposure.view(entries, {"A": 0.30, "B": 0.70, "C": 0.15, "D": None, "E": 0.40})
    rows = {r["name"]: r for r in v["rows"]}
    assert v["entries"] == 4
    assert rows["A"]["mine"] == 0.75 and rows["A"]["count"] == 3
    assert [r["name"] for r in v["over"]] == ["A"]  # +45 points
    assert [r["name"] for r in v["under"]] == ["E"]  # 0% against 40%; B is 75% vs 70%
    assert "D" in v["unknown"]


def test_no_entries_means_no_rows():
    assert exposure.view([], {"A": 0.5})["rows"] == []


# -- what won ------------------------------------------------------------------------------------------------


def lineup(name, points, key, stack1=1.0, stack2=0.0, bring=0.0, team="KC", unspent=100.0, flex="WR"):
    return {
        "entry_name": name,
        "points": points,
        "lineup_key": key,
        "stack1": stack1,
        "stack2": stack2,
        "bringback": bring,
        "qb_team": team if stack1 else "",
        "unspent": unspent,
        "flex_pos": flex,
    }


def test_the_review_names_the_winner_the_last_paid_place_and_the_field_beside_sam():
    feats = pd.DataFrame(
        [
            lineup("rival", 150.0, "A|B", stack2=1.0, bring=1.0),
            lineup("srwildman", 120.0, "A|C", stack1=0.0, flex="RB", unspent=300.0),
            lineup("other", 110.0, "B|C", team="BUF"),
            lineup("other2", 90.0, "C|D", team="BUF"),
        ]
    )
    contest = field_review.Contest(
        "9", "NFL $5 Double Up", "Double Up", 4, 2, feats, {"a": 0.5, "b": 0.2, "c": 0.1}
    )
    gpp = field_review.Contest("8", "NFL $3 SE", "SE GPP", 4, 1, feats.assign(entry_name="x"), {})
    v = field_review.review([gpp, contest], "srwildman", 8)
    assert [b["format"] for b in v["formats"]] == ["Double Up", "SE GPP"]  # cash first
    du = v["formats"][0]
    assert du["winner"]["points"] == 150.0 and du["winner"]["shape"] == "KC QB + 2+ WR/TE, bring-back"
    assert abs(du["winner"]["own_sum"] - 0.7) < 1e-9
    assert du["cash_line"]["points"] == 120.0  # two places paid
    assert du["field"]["stack"] == 0.75 and du["field"]["flex_rb"] == 0.25
    assert du["field"]["top_stacks"][0] == ("BUF", 2 / 4)
    assert du["mine"]["entries"] == 1 and du["mine"]["stack"] == 0.0 and du["mine"]["salary_left"] == 300.0
    assert v["formats"][1]["mine"] is None
    assert "Double Up line 120.0" in field_review.headline(v)


# -- the scorecard -------------------------------------------------------------------------------------------

FPTS = {"a": 10.0, "b": 20.0, "c": 5.0, "d": 30.0, "e": 0.0}


def test_an_override_is_measured_against_the_nearest_build_of_its_kind():
    builds = [
        scorecard.Build("cash build 1", "cash", ("A", "B", "C")),
        scorecard.Build("gpp build 2", "gpp", ("A", "B", "D")),
    ]
    played = [scorecard.Played("cash", ("A", "B", "D")), scorecard.Played("gpp", ("A", "B", "D"), 3)]
    c = scorecard.card(8, played, builds, FPTS)
    cash, gpp = c.overrides
    assert cash.build == "cash build 1" and cash.shared == 2  # the GPP build is not a cash lineup's nearest
    assert cash.into == [("D", 30.0)] and cash.out == [("C", 5.0)] and cash.delta == 25.0
    assert gpp.delta == 0.0 and gpp not in c.changed  # identical to its build: no override
    assert c.gain == 25.0 and c.loss == 0.0


def test_no_build_of_the_kind_is_said_and_not_scored():
    c = scorecard.card(8, [scorecard.Played("gpp", ("A",))], [scorecard.Build("b", "cash", ("A",))], FPTS)
    assert c.overrides[0].build == "" and c.overrides[0].delta is None and c.changed == []
    assert scorecard.payload(c)["no_build"] == 1


def test_chalk_decisions_are_read_from_the_rules_box_and_checked_against_the_cash_lineups():
    rules = {"Always use": "cash: A, gpp: Z, B", "Never use": "cash: D; C"}
    ds = scorecard.decisions_from_rules(rules)
    assert ds == [("A", "play"), ("D", "fade")]
    checked = scorecard.check_decisions(ds, [scorecard.Played("cash", ("A", "D"))])
    assert [d["kept"] for d in checked] == [True, False]


def test_the_scorecard_lines_split_edited_builds_from_his_own(monkeypatch):
    monkeypatch.setattr(scorecard, "MATCH_MIN", 2)
    builds = [scorecard.Build("cash build 1", "cash", ("A", "B", "C"))]
    played = [scorecard.Played("cash", ("A", "B", "D")), scorecard.Played("cash", ("E", "D", "B"))]
    c = scorecard.card(
        8, played, builds, FPTS, decisions=[("A", "play")], tier_line="A $10 of $15", tier_over=[]
    )
    text = "\n".join(scorecard.lines(scorecard.payload(c, "your overrides against the model: +1.0 / -2.0")))
    assert "2 of 2 lineups differ from their nearest build (1 edited builds, 1 your own)" in text
    assert "chalk decisions: 1 of 1 kept" in text and "within the tier plan" in text
    assert "in D 30.0; out C 5.0" in text


def test_the_season_file_replaces_a_rerun_week_and_sums_the_season(tmp_path):
    path = tmp_path / "scorecard.csv"
    builds = [scorecard.Build("b", "cash", ("A", "B"))]
    win = scorecard.card(7, [scorecard.Played("cash", ("A", "D"))], builds, FPTS)  # +10
    lose = scorecard.card(8, [scorecard.Played("cash", ("A", "C"))], builds, FPTS)  # -15
    scorecard.append_csv(path, scorecard.csv_row(2026, win))
    scorecard.append_csv(path, scorecard.csv_row(2026, lose))
    scorecard.append_csv(path, scorecard.csv_row(2026, lose))  # a rerun replaces, never doubles
    assert scorecard.season_line_from_csv(path, 2026) == (
        "your overrides against the model: +10.0 / -15.0 points over 2 weeks"
    )
    assert scorecard.season_line_from_csv(tmp_path / "none.csv", 2026) == ""


# -- how good are our numbers --------------------------------------------------------------------------------


def test_cash_percent_is_compared_with_the_cash_rate_by_week():
    rows = [
        {"week": 7, "shown": 0.7, "cashed": "True"},
        {"week": 7, "shown": 0.6, "cashed": "False"},
        {"week": 8, "shown": 0.8, "cashed": "False"},
        {"week": 8, "shown": "", "cashed": "True"},  # no Cash % shown: left out
    ]
    c = numbers.cash_calibration(rows)
    assert [w["week"] for w in c["weeks"]] == [7, 8]
    assert (
        c["all"]["n"] == 3 and abs(c["all"]["shown"] - 0.7) < 1e-9 and abs(c["all"]["cashed"] - 1 / 3) < 1e-9
    )
    assert numbers.cash_calibration([])["all"] is None


def test_signals_leave_out_baselines_and_unfired_and_carry_their_chip():
    table = [
        {"Signal": "USAGE↑", "n": "83", "HitCal": "0.446", "VsCal": "1.39", "Thin": "False"},
        {"Signal": "FADE↓", "n": "52", "HitCal": "0.673", "VsCal": "-2.51", "Thin": "False"},
        {"Signal": "Props ▼", "n": "1", "HitCal": "0.0", "VsCal": "30.6", "Thin": "True"},
        {"Signal": "Unflagged QB", "n": "96", "HitCal": "", "VsCal": "-0.1", "Thin": "False"},
        {"Signal": "Never", "n": "0", "HitCal": "", "VsCal": "", "Thin": "False"},
    ]
    rows = numbers.signal_rows(table)
    assert [r["signal"] for r in rows] == ["FADE↓", "USAGE↑", "Props ▼"]
    assert rows[0]["trust"] == "Proven" and rows[1]["trust"] == "Shadow" and rows[2]["thin"]


def test_the_cash_file_replaces_a_rerun_week(tmp_path):
    path = tmp_path / "cash_shown.csv"
    row = {"season": 2026, "week": 8, "label": "b1", "shown": 0.7, "points": 120, "line": 110, "cashed": True}
    numbers.replace_week(path, 2026, 8, [row])
    numbers.replace_week(path, 2026, 8, [row, {**row, "label": "b2"}])
    assert [r["label"] for r in numbers.read_csv(path)] == ["b1", "b2"]


def test_no_scored_week_means_no_projection_rows():
    assert numbers.projection_by_week(pd.DataFrame()) == []
    assert numbers.headline(None) == ""


# -- week_review's joins -------------------------------------------------------------------------------------


def fake_contest(fmt, rows, own=None, entries=100):
    feats = pd.DataFrame(rows, columns=["entry_name", "lineup_key"])
    return SimpleNamespace(format=fmt, features=feats, own=own or {}, entries=entries)


def test_his_lineups_are_distinct_by_kind_with_their_entry_counts():
    contests = [
        fake_contest("Double Up", [("srwildman", "A|B"), ("x", "A|C")]),
        fake_contest("SE GPP", [("srwildman", "A|B")]),
        fake_contest("N-max GPP", [("srwildman", "B|A"), ("srwildman", "C|D")]),
    ]
    played = sorted(week_review.played_from(contests, "srwildman"), key=lambda p: (p.kind, p.names))
    assert [(p.kind, p.entries) for p in played] == [("cash", 1), ("gpp", 2), ("gpp", 1)]


def test_actual_ownership_is_weighted_by_the_gpps_he_entered():
    contests = [
        fake_contest("SE GPP", [("srwildman", "A")], {"a": 0.2}, entries=100),
        fake_contest("N-max GPP", [("srwildman", "A")], {"a": 0.5}, entries=300),
        fake_contest("N-max GPP", [("x", "A")], {"a": 0.9}, entries=1000),  # not his
        fake_contest("Double Up", [("srwildman", "A")], {"a": 0.9}),  # cash
    ]
    assert week_review.actual_gpp_own(contests, "srwildman") == pytest.approx({"a": (20 + 150) / 400})


def test_builds_and_decisions_come_from_the_saved_rounds():
    from dfs.suggest_state import RulesText

    b = SimpleNamespace
    state = b(
        rounds=[
            b(round=1, rules=RulesText(), builds=[b(kind="cash", number=1, names=["A", "B"])]),
            b(
                round=2,
                rules=RulesText(always="cash: A", never="cash: C"),
                builds=[b(kind="cash", number=1, names=["B", "A"]), b(kind="gpp", number=2, names=["C"])],
            ),
        ]
    )
    builds, decisions = week_review.builds_from(state)
    assert [x.label for x in builds] == ["cash build 1 (round 1)", "gpp build 2 (round 2)"]
    assert decisions == [("A", "play"), ("C", "fade")]


def test_a_week_without_standings_says_so(monkeypatch):
    from dfs.sim import contest_retro

    monkeypatch.setattr(contest_retro, "load_real_contests", lambda weeks=None: [])
    said = []
    assert week_review.run(2026, 8, say=said.append) is False
    assert "no saved standings" in said[0]
