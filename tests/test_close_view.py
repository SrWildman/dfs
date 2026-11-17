"""The Close view (blueprint Phase D): the week line, what decided it, the four scorecards, the one thing, and
the page's Tuesday view."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from types import SimpleNamespace

import pytest
from test_week_plays import inputs, page_text, render, slate_rows

from dfs import close_view, scorecard, week_learn, week_review, week_views

TUE = datetime(2026, 11, 10, 10, 0).astimezone()


def du(week, pct, cashed, points=133.0, line=133.0, copies=40, fmt="Double Up", season=2026):
    return {
        "season": str(season),
        "week": str(week),
        "format": fmt,
        "contest_id": f"c{week}{pct}",
        "points": str(points),
        "cash_line": str(line),
        "pct": str(pct),
        "cashed": str(cashed),
        "copies": str(copies),
    }


WEEK9 = [
    du(9, 0.40, True, line=132.6, copies=39),
    du(9, 0.45, True, line=132.9, copies=55),
    du(9, 0.43, True, line=132.8),
    du(9, 0.44, False, line=135.1),
    du(9, 0.50, False, line=134.0),
    du(9, 0.43, False, line=133.5),
    du(9, 0.10, False, fmt="N-max GPP"),  # not a double-up: not in the line
    du(8, 0.70, False),
]


def close_input(**over):
    cash = close_view.cash_line(WEEK9, 2026, 9)
    cards = scorecard.scorecards(
        week=9,
        projection=[{"week": w, "calpts": 4.6, "tffb": 5.0 + 0.01 * w} for w in range(4, 10)],
        ownership=[],
        cash_rows=[{"week": 9, "shown": 0.75, "cashed": "True"}],
        edge={
            "week": 9,
            "this_week": {"edge": 7.3, "pct": 0.98, "realized": 6.5, "rows": 1, "n_src": 4},
            "weeks": {"8": {"edge": 4.0, "realized": -9.3}, "9": {"edge": 7.3, "realized": 6.5}},
            "season_edge": 5.65,
            "season_pct": 0.85,
            "season_realized": -1.4,
            "n_weeks": 2,
        },
    )
    body = close_view.payload(
        season=2026,
        week=9,
        cash=cash,
        net=-48.04,
        by_group={"cash": -37.77, "small_gpp": -20.0, "fun": 6.5, "free": 3.23},
        decided_rows=[
            {
                "name": "Wr Star",
                "position": "WR",
                "in_cashers": 0.58,
                "in_non_cashers": 0.10,
                "field": 0.31,
                "cash_own": 0.41,
                "tag": close_view.CHOICE,
                "detail": "in 5 cash builds",
            }
        ],
        vs=close_view.vs_default(
            [{"season": 2026, "week": 9, "lineups": 12, "overrides": 11, "gain": 116.1, "loss": -67.24}],
            2026,
            9,
            against="the nearest build",
        ),
        avoid=close_view.avoidable({9: 4}, 9),
        percentile=close_view.percentile_by_week(WEEK9, 2026, 9),
        cards=cards,
        edge=cards and {"this_week": {"edge": 7.3, "pct": 0.98}, "season_edge": 5.65, "n_weeks": 2},
        calibration_gap=0.1656,
        one="Nothing. No trigger fired this week; most weeks, nothing.",
        contests=14,
        entries=66,
        du_entered=6,
    )
    return body | over


# -- the week line ----------


def test_the_week_line_is_the_double_ups_against_their_lines_and_the_net():
    c = close_input()
    assert c["cash"]["entered"] == 6 and c["cash"]["cashed"] == 3
    assert c["cash"]["copies_lo"] == 39 and c["cash"]["copies_hi"] == 55
    assert close_view.week_line(c) == (
        "Cash lineup 133.0 pts against double-up lines of 132.6-135.1 · 3 of 6 double-ups cashed · beat 56% "
        "of the field (the median of them) · week net -$48"
    )


def test_partial_standings_and_no_standings_and_no_entries_say_so():
    partial = close_input(du_entered=8)
    assert "6 of 8 double-ups with standings" in close_view.week_line(partial)
    none = close_input(cash=None)
    assert close_view.week_line(none) == "No double-up standings for this week, so no line · week net -$48"
    assert close_view.week_line(close_input(entries=0)) == "No entries this week."


def test_the_percentile_is_by_week_and_a_later_week_never_counts():
    assert close_view.percentile_by_week(WEEK9, 2026, 8) == {"8": 0.3}
    assert close_view.percentile_by_week(WEEK9, 2026, 9)["9"] == pytest.approx(0.565)


def test_what_decided_it_tags_against_what_he_entered():
    from dfs.cash_postmortem import IN_BUILDS, MISSED, POOL_ONLY

    def gap(name, tag):
        return SimpleNamespace(
            name=name,
            position="WR",
            in_cashers=0.6,
            in_non_cashers=0.2,
            field=0.4,
            cash_own=0.3,
            tag=tag,
            tag_text=tag,
        )

    rows = close_view.decided(
        [gap("A Had", IN_BUILDS), gap("B Built", IN_BUILDS), gap("C Pool", POOL_ONLY), gap("D Miss", MISSED)],
        [("A Had", "Other")],
    )
    assert [r["tag"] for r in rows] == [
        close_view.HAD,
        close_view.CHOICE,
        close_view.CHOICE,
        close_view.VARIANCE,
    ]


def test_money_is_the_net_by_group():
    net, by = close_view.money(
        [{"group": "cash", "net": -10.0}, {"group": "cash", "net": 2.5}, {"group": "fun", "net": 1}]
    )
    assert net == -6.5 and by == {"cash": -7.5, "fun": 1.0}


# -- the scorecards ----------


def test_a_scorecard_is_new_under_three_weeks_and_proven_two_se_ahead():
    two = [{"week": 8, "calpts": 4.5, "tffb": 5.0}, {"week": 9, "calpts": 4.6, "tffb": 5.0}]
    assert scorecard.projection_card(two, 9)["status"] == scorecard.NEW
    steady = [{"week": w, "calpts": 4.5, "tffb": 5.0 + 0.01 * w} for w in range(4, 10)]
    card = scorecard.projection_card(steady, 9)
    assert card["status"] == scorecard.PROVEN and card["weeks"] == 6
    assert card["week"] == "MAE 4.50 vs 5.09" and "better 6 of 6" in card["baseline"]
    noisy = [{"week": w, "calpts": 5.0, "tffb": 5.0 + (0.5 if w % 2 else -0.5)} for w in range(4, 10)]
    assert scorecard.projection_card(noisy, 9)["status"] == scorecard.NOT_PROVEN


def test_ownership_takes_the_weaker_format_and_each_against_its_own_baseline():
    rows = []
    for w in range(5, 10):
        rows += [
            {"Week": w, "Format": "gpp", "Model": "TFFB Own%", "MAE": 0.040 + 0.001 * w},
            {"Week": w, "Format": "gpp", "Model": "Ours", "MAE": 0.030},
            {"Week": w, "Format": "cash", "Model": "Double-up curve", "MAE": 0.05},
            {"Week": w, "Format": "cash", "Model": "Ours", "MAE": 0.05 + (0.01 if w % 2 else -0.01)},
            {"Week": w, "Format": "cash", "Model": "TFFB Own%", "MAE": 0.9},  # not cash's baseline
        ]
    gains, this = scorecard.ownership_gains(rows, 9)
    assert len(gains["gpp"]) == 5 and all(g > 0 for g in gains["gpp"])
    assert this["cash"] == pytest.approx((0.06, 0.05))
    assert scorecard.ownership_card(rows, 9)["status"] == scorecard.NOT_PROVEN


def test_calibration_is_overstated_when_the_shown_chance_runs_two_se_high():
    rows = [{"week": w, "shown": 0.75, "cashed": str(i % 4 == 0)} for w in (7, 8, 9) for i in range(12)]
    card = scorecard.calibration_card(rows, 9)
    assert card["status"] == scorecard.OVERSTATED and card["gap"] == pytest.approx(0.75 - 9 / 36, abs=1e-4)
    fair = [{"week": w, "shown": 0.5, "cashed": str(i % 2 == 0)} for w in (7, 8, 9) for i in range(12)]
    assert scorecard.calibration_card(fair, 9)["status"] == scorecard.PROVEN
    assert scorecard.calibration_card([], 9)["status"] == scorecard.NEW


def test_the_four_cards_in_order():
    cards = close_input()["scorecards"]
    assert [c["model"] for c in cards] == [
        "Projection (CalPts vs TFFB)",
        "Ownership (GPP, cash)",
        "Cash % calibration",
        "Edge at lock",
    ]
    assert {c["status"] for c in cards} <= {
        scorecard.PROVEN,
        scorecard.NOT_PROVEN,
        scorecard.OVERSTATED,
        scorecard.NEW,
    }
    assert cards[3]["week"] == "actual +6.5 pts; on paper +7.3 pts, 98th percentile"
    assert cards[3]["season"].endswith("on paper: +5.7 pts, 85th percentile")


def edge(realized, paper=6.0):
    weeks = {str(w): {"edge": paper, "pct": 0.85, "realized": r} for w, r in enumerate(realized, start=4)}
    last = weeks[str(3 + len(realized))]
    return {"this_week": last, "weeks": weeks, "season_edge": paper, "season_pct": 0.85}


def test_the_edge_card_status_comes_from_the_realized_margin_not_the_projected_edge():
    """Sam, 2026-11-17: on paper every week is well ahead of the field; the status is what happened."""
    assert scorecard.edge_card(edge([-5.8, 9.1, -13.9, 1.9, -9.3, 6.5]))["status"] == scorecard.NOT_PROVEN
    assert scorecard.edge_card(edge([3.0, 4.0, 5.0, 3.5]))["status"] == scorecard.PROVEN
    assert scorecard.edge_card(edge([3.0, 4.0]))["status"] == scorecard.NEW
    card = scorecard.edge_card(edge([-5.8, 9.1, -13.9, 1.9, -9.3, 6.5], paper=5.9))
    assert card["season"] == "actual -1.9 pts a week (SE 3.7); on paper: +5.9 pts, 85th percentile"
    assert card["weeks"] == 6 and card["week"] == "actual +6.5 pts; on paper +5.9 pts, 85th percentile"


# -- the one thing ----------


def one(**over):
    args = {
        "projection_gains": [0.4] * 6,
        "ownership_gains": {"cash": [0.01] * 5, "gpp": [0.01] * 5},
        "edge_by_week": [5.0] * 6,
        "calibration_gap": 0.02,
        "fun_by_week": [10.0] * 4,
        "fun_per_week": 20.0,
    } | over
    return close_view.one_thing([], **args)


def test_most_weeks_the_one_thing_is_nothing_and_each_trigger_fires_alone():
    assert one().startswith("Nothing.")
    assert "CalPts has trailed TFFB 4 weeks" in one(projection_gains=[0.4, -0.1, -0.2, -0.1, -0.3])
    assert one(projection_gains=[-0.1, -0.2, -0.1]).startswith("Nothing.")  # three weeks is not four
    assert "cash ownership has trailed the double-up curve" in one(ownership_gains={"cash": [-0.01] * 4})
    said = one(edge_by_week=[5, 0, -1, 2, -3])
    assert "Edge at lock, on paper, was at or under zero in 3 of the last 4" in said and "projected" in said
    assert "17 points above the cash rate" in one(calibration_gap=0.17)
    assert "Fun line was over your $20 3 weeks" in one(fun_by_week=[5, 25, 30, 21])


# -- the page ----------


def close_block(page):
    start = page.index('id="close-card"')
    return page[start : page.index("</section></div>", start)]


def test_the_close_view_shows_the_closed_week_on_one_screen(tmp_path):
    page = render(inputs(tmp_path, slate_rows(), now=TUE, close=close_input()))
    text = re.sub(r"\s+", " ", page_text(close_block(page)))
    assert "133.0 pts against double-up lines of 132.6-135.1" in text and "3 of 6" in text and "-$48" in text
    assert "What decided it" in text and "a choice" in text and "58%" in text
    assert "You against the nearest build" in text and "+48.9 pts" in text and "too few to call" in text
    for model in (
        "Projection (CalPts vs TFFB)",
        "Ownership (GPP, cash)",
        "Cash % calibration",
        "Edge at lock",
    ):
        assert model in text, model
    assert (
        "Edge at lock, on paper +7.3 pts" in text
        and "not proof it pays" in text
        and "Avoidable errors, season 4" in text
    )
    assert "Calibration gap +16.6 pts" in text and "Cash percentile in field" in text
    assert "The one thing worth changing" in text and "Nothing." in text
    assert 'id="close-ledger"' in page and page.count('id="ledger"') <= 1
    assert 'data-v="close everything"' in page and 'href="#close" data-view="close"' in page


def test_without_the_review_the_close_view_says_not_run_yet(tmp_path):
    page = render(inputs(tmp_path, slate_rows(), now=TUE))
    assert "not run yet" in page_text(close_block(page)).lower()


def test_the_close_view_adds_no_repeated_element_id(tmp_path):
    def repeated(**over):
        ids = Counter(re.findall(r'\bid="([^"]+)"', render(inputs(tmp_path, slate_rows(), now=TUE, **over))))
        return {i for i, n in ids.items() if n > 1}, ids

    before, _ = repeated()
    after, ids = repeated(close=close_input())
    assert after == before  # the explorer's own repeats are not this view's
    assert ids["close-card"] == ids["close-ledger"] == ids["decision-log"] == 1
    assert "close" in week_views.VIEW_NAMES and "close" not in ids


def test_the_decision_log_lists_the_weeks_decisions_with_reason_pills(tmp_path):
    log = [
        {
            "id": "2026-09-0001",
            "at": "2026-11-08T17:40:00+00:00",
            "state": "lock",
            "kind": "swap",
            "lineup": "L5",
            "slot": "WR",
            "chosen": {"name": "Denzel Boston"},
            "default_at_time": {"name": "Ryan Flournoy"},
            "reason": "news",
        }
    ]
    static = render(inputs(tmp_path, slate_rows(), now=TUE, close=close_input(), decisions_log=log))
    block = close_block(static)
    assert "Decision log (1)" in block and "Denzel Boston" in block and "(was Ryan Flournoy)" in block
    assert 'class="on" data-id="2026-09-0001" data-reason="news" disabled' in block  # the static page reads
    assert len(re.findall(r'data-reason="', block)) == len(week_learn.REASON_LABELS)


def test_unknown_review_parts_are_refused():
    with pytest.raises(ValueError, match="unknown review parts"):
        week_review.run(2026, 9, only=("nope",))
