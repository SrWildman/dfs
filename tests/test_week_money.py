"""The Week page's Plan K sections from fixtures, including missing inputs: bankroll health, Sunday
inactives, the consensus lineup and copies, the entry plan, and the practice text beside Q / D tags."""

from __future__ import annotations

import json
import re

from dfs import bankroll_health as bh
from dfs import practice, week_money
from dfs.config import StakesConfig
from tests.test_week_plays import SUN, WED, inputs, page_text, render, row, section, slate_rows

LINEUP = [
    "Qb Alpha",
    "Rb Warn",
    "Rb Fine",
    "Wr Value",
    "Wr Star",
    "Wr Three",
    "Te Plain",
    "Te Fade",
    "Cowboys",
]
OWN = {"CashOwn": "0.3", "GppOwn": "0.2"}


def rows_with_more_wrs(**extra):
    rows = slate_rows()
    rows += [
        row("Wr Three", "WR", "NE", "NYJ", 3000, 9.0, Spread="-1.5", OverUnder="38.5"),
        row("Wr Four", "WR", "NYJ", "NE", 3500, 10.0, Spread="1.5", OverUnder="38.5"),
    ]
    for r in rows:
        r.update(extra)
    return rows


def entered_record(names=LINEUP, contests=None, blank=None) -> dict:
    return {
        "season": 2026,
        "week": 9,
        "recorded_at": "2026-11-08T17:00:00+00:00",
        "lineups": [
            {
                "names": list(names),
                "contests": contests
                or [
                    {
                        "contest_id": "1",
                        "name": "NFL $5 Double Up",
                        "entry_fee": "$5",
                        "format": "Double Up",
                        "entry_ids": ["e1"],
                    }
                ],  # fmt: skip
                "source": {"number": 1, "kind": "cash", "round": 1},
            }
        ],
        "blank": blank or [],
    }


def test_every_new_section_says_not_run_yet_without_its_inputs(tmp_path):
    page = render(inputs(tmp_path / "data"))
    for sid in ("bankroll", "inactives", "consensus", "entry-plan"):
        assert "Not run yet." in page_text(section(page, sid)), sid


def test_the_bankroll_block_has_the_curve_the_guide_and_one_over_sentence(tmp_path):
    grid = [
        ["Week", "Cash Net", "GPP Net", "Ending Bankroll", "Cash Risked"],
        [1, 10, -50, 160, 40],
        [2, -5, 20, 175, 30],
    ]
    block = {"Starting Bankroll": 175.0, "Weekly Budget": 100.0, "Weekly Cost": 25.0}
    payload = bh.payload(
        bh.season_rows(grid), block, StakesConfig(max_pct_bankroll_per_week=10), sheet="Wk 9"
    )
    page = render(inputs(tmp_path / "data", rows=slate_rows(), bankroll=payload))
    body = section(page, "bankroll")
    text = page_text(body)
    assert '<svg class="chart bank"' in body and body.count('<path class="line') == 4
    assert "Bankroll $175.00" in text and "$75.00 left" in text
    assert text.count("is above what a $175.00 bankroll supports under your 10%-a-week rule") == 1
    assert "Stake guide (your [stakes] rules)" in text and "$17.50" in text
    assert ">Data<" in body
    assert "Spent / bankroll" in text  # the table view of the chart


def test_without_rules_the_guide_is_the_weekly_budget_by_tier(tmp_path):
    block = {"Starting Bankroll": 131.69, "Weekly Budget": 250.0, "Weekly Cost": 0.0}
    payload = bh.payload([], block, StakesConfig(), sheet="Wk 9")
    inp = inputs(tmp_path / "data", rows=slate_rows(), bankroll=payload)
    text = re.sub(r"\s+", " ", page_text(section(render(inp), "bankroll")))
    assert "Stake guide (your Weekly Budget)" in text
    assert week_money.bankroll_line(inp) == ("Bankroll $131.69; Weekly Budget $250.00 (189.8% of it)", False)
    assert "$250.00 (189.8% of the bankroll)" in text
    assert "is above what" not in text and "proposed" not in text


def test_inactives_lists_the_q_player_with_pivots_and_his_practice(tmp_path):
    star = practice.Trend(steps=[("Wed", "DNP"), ("Thu", "Limited")], report="Questionable")
    week = practice.WeekPractice({practice.player_key("Wr Star", "DAL"): star}, 2, [])
    inp = inputs(tmp_path / "data", rows=rows_with_more_wrs(), entered=entered_record(), practice=week)
    text = re.sub(r"\s+", " ", page_text(section(render(inp), "inactives")))
    assert "Wr Star WR DAL" in text and "Questionable" in text
    assert "Wr Four (WR NYJ, $3,500, 10.0 pts" in text
    assert "DNP Wed · Limited Thu ↑" in text
    assert "up to $15,500; by CalPts" in text  # his 9,000 + 6,500 left
    assert week_money.inactives_line(inp) == ("1 flagged in 1 lineup (0 OUT or Doubtful), pivots ready", True)


def test_on_sunday_the_inactives_check_leads_and_is_open_when_something_is_flagged(tmp_path):
    inp = inputs(tmp_path / "data", rows=rows_with_more_wrs(), now=SUN, entered=entered_record())
    page = render(inp)
    order = re.findall(r'<section class="card fold" id="([a-z-]+)">', page)
    assert order[0] == "inactives"
    assert re.search(r'id="inactives"><details open>', page)


def test_the_consensus_names_the_field_lineup_and_flags_a_gpp_lineup_at_five_copies(tmp_path):
    calibration = {
        "fit": {"cash": {"scale": 1.0, "exponent": 0.5}, "gpp": {"scale": 1e5, "exponent": 0.5}},
        "summary": {"gpp": {"weeks": [4, 5], "entries": 10, "log_rmse": 0.4, "top_observed": 9,
                            "top_predicted": 8.0, "mine_observed": 1, "mine_predicted": 1.5, "mine_n": 2}},
        "held_out": [],
    }  # fmt: skip
    se = {"contest_id": "2", "name": "NFL $3 SE", "entry_fee": "$3", "format": "SE GPP", "entry_ids": ["e2"]}
    gpp = entered_record(contests=[se])
    gpp["lineups"][0]["source"]["kind"] = "gpp"
    inp = inputs(tmp_path / "data", rows=rows_with_more_wrs(**OWN), entered=gpp, dup_calibration=calibration)
    page = render(inp)
    text = page_text(section(page, "consensus"))
    assert "The field's lineup: Double-ups (CashOwn)" in text
    assert "5+ copies split a prize" in text
    assert "typical error a factor of 1.5" in text
    line, urgent = week_money.consensus_line(inp)
    assert urgent and "1 GPP lineups at 5+ copies" in line


def test_the_consensus_waits_for_ownership(tmp_path):
    inp = inputs(tmp_path / "data", rows=rows_with_more_wrs(), entered=entered_record())
    assert "Not run yet." in page_text(section(render(inp), "consensus"))
    assert week_money.consensus_line(inp) == ("ownership not out yet", False)


def test_the_entry_plan_names_an_empty_entry_on_its_fold_line(tmp_path):
    blank = [
        {
            "contest_id": "3",
            "name": "NFL $10 Hundo",
            "entry_fee": "$10",
            "format": "SE GPP",
            "entry_ids": ["z"],
        }
    ]
    inp = inputs(tmp_path / "data", rows=slate_rows(), entered=entered_record(blank=blank))
    page = render(inp)
    assert "NFL $10 Hundo: 1 entry with no lineup" in page_text(section(page, "entry-plan"))
    line, urgent = week_money.entry_plan_line(inp)
    assert urgent and line.startswith("2 contests: 1 problem (NFL $10 Hundo: 1 entry with no lineup)")


def test_the_practice_trend_sits_beside_the_q_tag_on_the_board_and_in_the_explorer(tmp_path):
    star = practice.Trend(steps=[("Wed", "DNP"), ("Fri", "Full")])
    week = practice.WeekPractice({practice.player_key("Wr Star", "DAL"): star}, 3, [])
    page = render(inputs(tmp_path / "data", rows=slate_rows(), now=WED, practice=week))
    assert '<span class="prac">DNP Wed · Full Fri</span>' in page
    data = json.loads(
        re.search(r'<script type="application/json" id="explorer-data">(.*?)</script>', page, re.S).group(1)
    )
    assert data["practice"] == {"id-Wr Star": "DNP Wed · Full Fri"}
