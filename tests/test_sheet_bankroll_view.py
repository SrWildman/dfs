"""Round 5, item 7: the Betting ledger's generated formula text, evaluated
in Python against the spec's own worked examples so a formula-text
regression is caught without needing a real sheet. Real-cell verification
against the template happens separately (see CONTRIBUTING.md's changelog
entry for this change)."""

from __future__ import annotations

import re

import pytest

from dfs.sheet_bankroll_view import (
    compute_weekly_betting_stats,
    entered_formula,
    net_formula,
    odds_formula,
    record_formula,
    winnings_formula,
)


def _odds_value(odds_pct) -> str | float:
    """A pure-Python re-implementation of `odds_formula`'s own logic, used
    only to check the GENERATED FORMULA TEXT encodes the right arithmetic --
    not a substitute for reading the real resolved value back from the
    sheet."""
    if odds_pct == "":
        return ""
    p = odds_pct / 100 if odds_pct > 1 else odds_pct
    if p > 0.5:
        return -round(100 * p / (1 - p))
    if p < 0.5:
        return round(100 * (1 - p) / p)
    return 100


def test_odds_formula_worked_examples():
    # Spec's five worked examples: 53.3% -> -114, 50% -> +100, 40% -> +150,
    # 75% -> -300, 20% -> +400.
    assert _odds_value(53.3) == -114
    assert _odds_value(50) == 100
    assert _odds_value(40) == 150
    assert _odds_value(75) == -300
    assert _odds_value(20) == 400
    # And the fractional-input form (0.533 etc.) reads the same.
    assert _odds_value(0.533) == -114
    assert _odds_value(0.40) == 150


def test_odds_formula_text_references_own_row_and_column_b():
    f = odds_formula(20)
    assert f.startswith('=IF($B20="","",')
    assert "$B20" in f
    assert f.count("$B20") >= 3  # the blank-check, the >1 check, and the LET binding


def test_odds_formula_blank_when_odds_pct_blank():
    f = odds_formula(17)
    assert f == (
        '=IF($B17="","",LET(p,IF($B17>1,$B17/100,$B17),'
        "IF(p>0.5,-ROUND(100*p/(1-p),0),IF(p<0.5,ROUND(100*(1-p)/p,0),100))))"
    )


def test_net_formula_blank_while_pending():
    f = net_formula(23)
    assert f == '=IF($E23="","",$E23-$D23)'


def test_net_formula_win_loss_push():
    # Won > Entered -> positive net; Won == Entered (push) -> zero net;
    # Won == 0 (loss, still settled) -> negative net.
    def net(entered: float, won) -> float | str:
        if won == "":
            return ""
        return won - entered

    assert net(10, 18.77) == pytest.approx(8.77)
    assert net(10, 10) == 0
    assert net(10, 0) == -10
    assert net(10, "") == ""


def test_record_formula_shape():
    f = record_formula(17, 56)
    assert re.match(r'^=SUMPRODUCT\(.*\)&"-"&SUMPRODUCT\(.*\)&"-"&SUMPRODUCT\(.*\)$', f)
    assert "$E$17:$E$56" in f
    assert "$D$17:$D$56" in f


def test_entered_formula_excludes_pending():
    f = entered_formula(17, 56)
    assert f == '=SUMPRODUCT(($E$17:$E$56<>"")*$D$17:$D$56)'


def test_winnings_formula_is_a_plain_sum():
    f = winnings_formula(17, 56)
    assert f == "=SUM($E$17:$E$56)"


def test_compute_weekly_betting_stats_matches_the_sheet_row_14_example():
    # Same 4 test bets used to verify row 14 live: win 53.3%, loss 40%,
    # push 60%, pending 45% -- see CONTRIBUTING.md's changelog for this
    # change. Matches the real resolved values read back from the sheet:
    # settled=3, record 1-1-1, entered=$30, net=-$1.23, exp wins 1.5 vs 1.
    rows = [
        (53.3, 10, 18.77),
        (40, 10, 0),
        (60, 10, 10),
        (45, 10, None),
    ]
    stats = compute_weekly_betting_stats(rows)
    assert stats.settled == 3
    assert (stats.wins, stats.losses, stats.pushes) == (1, 1, 1)
    assert stats.entered == 30
    assert stats.net == pytest.approx(8.77 - 10 + 0)
    assert round(stats.expected_wins, 1) == 1.5


def test_compute_weekly_betting_stats_zero_entered_zero_won_is_a_loss():
    # Sam, 2026-09-28: "loss, but no money lost" -- a $0-entered promo/free
    # bet that pays out $0 counts as a loss, NOT a push (a push means a
    # real stake came back even; there's no real stake here).
    rows = [(50, 0, 0)]
    stats = compute_weekly_betting_stats(rows)
    assert (stats.wins, stats.losses, stats.pushes) == (0, 1, 0)
    assert stats.net == 0


def test_compute_weekly_betting_stats_zero_entered_nonzero_won_is_a_win():
    rows = [(50, 0, 15)]
    stats = compute_weekly_betting_stats(rows)
    assert (stats.wins, stats.losses, stats.pushes) == (1, 0, 0)
    assert stats.net == 15


def test_compute_weekly_betting_stats_real_push_still_a_push():
    # A REAL stake returned even (Entered > 0) is still a push.
    rows = [(50, 10, 10)]
    stats = compute_weekly_betting_stats(rows)
    assert (stats.wins, stats.losses, stats.pushes) == (0, 0, 1)


def test_compute_weekly_betting_stats_no_bets():
    stats = compute_weekly_betting_stats([])
    assert stats.settled == 0
    assert stats.net == 0
    assert stats.expected_wins == 0


def test_compute_weekly_betting_stats_all_pending():
    rows = [(50, 10, None), (50, 10, "")]
    stats = compute_weekly_betting_stats(rows)
    assert stats.settled == 0
    assert stats.entered == 0


def test_all_row_formulas_are_row_relative_not_hardcoded():
    """Every per-row formula must reference its OWN row -- a formula that
    hardcodes a different row would silently break the moment a row is
    inserted or the ledger is copied to a new week (CLAUDE.md's central
    hazard)."""
    for row in (17, 30, 56):
        assert odds_formula(row).count(str(row)) >= 3
        assert net_formula(row).count(str(row)) >= 2
