"""The design pass's Saturday (Sam, 2026-11-17): the slate digest before a build, "Chalk you're not on" after
one, and "Someone else..." for an open spot."""

from __future__ import annotations

import html
import re

from test_week_plays import inputs, slate_rows

from dfs import week_board as wb
from dfs import week_page, week_saturday


def chalk_slate():
    """Wr Star is Week 9's CeeDee Lamb: 58% of the double-up field, not in the cash lineup."""
    rows = slate_rows(
        alpha={"CashOwn": "0.62"},
        star={"CashOwn": "0.58"},  # Questionable: listed all the same
        value={"CashOwn": "0.30"},
    )
    for r in rows:
        r["CashOwn"] = r.get("CashOwn") or "0.04"
        if r["Name"] == "Wr Out":
            r["CashOwn"] = "0.50"  # OUT: never listed, however owned
    return rows


def core(names):
    players = [{"name": n, "position": "", "team": "", "salary": 0, "projection": 0.0} for n in names]
    return {
        "default": {
            "names": names,
            "ids": [""] * len(names),
            "projection": 120.0,
            "left": 1500,
            "players": players,
        },
        "core": [],
        "open": [],
    }


def test_chalk_youre_not_on_catches_a_ceedee_lamb_and_leaves_out_who_you_have_and_who_is_out(tmp_path):
    inp = inputs(tmp_path, chalk_slate(), cash_core=core(["Qb Alpha", "Rb Fine"]))
    names, where = week_saturday.your_cash_lineup(inp)
    assert names == ["Qb Alpha", "Rb Fine"] and "Default" in where
    got = [r["Name"] for r in week_saturday.chalk_not_on(inp, names)]
    assert got == ["Wr Star", "Wr Value"]  # most owned first; 30% counts; Qb Alpha is his; Wr Out is OUT
    card = html.unescape(week_saturday.chalk_html(inp))
    text = re.sub(r"<[^>]+>", " ", card)
    assert "Chalk you're not on" in text and "58%" in text and "$9,000" in text and "21.0" in text
    assert 'class="how"' in card  # how it is built, and from what


def test_chalk_waits_for_ownership_and_says_so(tmp_path):
    rows = slate_rows()
    for r in rows:
        r["CashOwn"] = ""
    inp = inputs(tmp_path, rows, cash_core=core(["Qb Alpha"]))
    assert week_saturday.chalk_not_on(inp, ["Qb Alpha"]) is None


def test_before_a_build_the_slate_leads_after_one_the_chalk_and_the_cash_board_do(tmp_path):
    before = week_page.render(inputs(tmp_path, chalk_slate()))
    assert 'id="chalk-off"' not in before
    assert before.index('id="digest"') < before.index('id="fades"') < before.index('id="pool"')
    after = week_page.render(inputs(tmp_path, chalk_slate(), cash_core=core(["Qb Alpha", "Rb Fine"])))
    assert after.index('id="chalk-off"') < after.index('id="digest"')
    # More keeps what the page had: the Overview, the board and every fold are still on the page
    for sid in ("overview-card", "board", "explorer-card", "ledger", "numbers", "season"):
        assert f'id="{sid}"' in after, sid


def test_the_digest_ranks_games_by_total_with_implied_totals_and_three_plays_per_position(tmp_path):
    inp = inputs(tmp_path, chalk_slate())
    body = html.unescape(week_saturday.digest_html(inp, wb.board(inp)))
    text = re.sub(r"<[^>]+>", " ", body)
    assert text.index("52.5") < text.index("38.5")  # the high total first
    assert "DAL 29.2" in text  # 52.5 / 2 + 6 / 2: the favorite's implied total
    assert "Best plays" in text and 'class="how"' in body
    for pos, rows in week_saturday.best_plays(wb.board(inp)).items():
        assert len(rows) <= week_saturday.BEST_PER_POS, pos
        assert [r.calpts for r in rows] == sorted((r.calpts for r in rows), reverse=True), pos


def test_someone_else_offers_who_fits_the_slot_and_the_salary_left_as_differences(tmp_path):
    cc = core(["Qb Alpha", "Wr Value"])
    spot = {
        "slot": "FLEX",
        "default": {"name": "Wr Value", "salary": 4000, "cash_own": 0.30},
        "alternatives": [],
    }
    inp = inputs(tmp_path, chalk_slate(), cash_core=cc)
    opts = week_saturday.spot_options(inp, spot, cc["default"])
    # FLEX takes RB / WR / TE; at most $4,000 + the $1,500 the Default leaves; never OUT or in the Default
    assert {o["name"] for o in opts} == {"Te Plain", "Te Fade", "Rb Warn", "Rb Fine"}
    fade = next(o for o in opts if o["name"] == "Te Fade")
    assert fade["d_points"] == -3.0 and fade["d_salary"] == -500 and fade["cash_own"] == 0.04
    assert [o["d_points"] for o in opts] == sorted((o["d_points"] for o in opts), reverse=True)
    assert week_saturday.fits_spot(inp, spot, cc["default"], "id-Wr Star") is None  # $9,000: too dear
    assert week_saturday.fits_spot(inp, spot, cc["default"], "id-Qb Beta") is None  # a QB is not a FLEX
    assert week_saturday.fits("RB2", "RB") and not week_saturday.fits("RB2", "WR")
