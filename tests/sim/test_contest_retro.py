"""The retro's pure helpers: the herd's decay from the top lineup's share, and the lead lineup's rank
decay."""

import pytest

from dfs.sim import contest_retro as cr
from dfs.sim import field_gen as fg


@pytest.mark.parametrize("top", [0.3, 0.55, 0.8])
def test_the_decay_reproduces_the_top_lineups_share(top):
    decay = cr.decay_for_top_share(top, k=20)
    assert fg.HerdModel(decay=decay, k=20).weights(20)[0] == pytest.approx(top, abs=1e-6)


def test_the_decay_edges():
    assert cr.decay_for_top_share(1 / 40, k=20) == 1.0  # flatter than uniform: uniform
    assert cr.decay_for_top_share(1.0) == 0.0


def test_the_lead_decay_is_the_geometric_fit_of_the_other_weeks_ranks():
    ranks = {4: 4, 5: 1, 6: 0}
    assert cr.fit_lead_decay(ranks, exclude_week=6) == pytest.approx(2.5 / 3.5)
    assert cr.fit_lead_decay(ranks, exclude_week=4) == pytest.approx(0.5 / 1.5)
    assert cr.fit_lead_decay({6: 0}, exclude_week=None) == 0.0
    assert cr.fit_lead_decay({}, exclude_week=None) == 0.0


def _table(seed: int = 0, weeks=(4, 5, 6, 7)):
    """Synthetic retro pairs: a GPP and a cash contest per week, 20 lineups each; `good` tracks the realized
    dollars, `noise` does not."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)
    rows = []
    for w in weeks:
        for fmt, fee in (("gpp", 10.0), ("cash", 10.0)):
            skill = rng.normal(size=20)
            realized = np.where(skill + rng.normal(scale=0.7, size=20) > 0.5, 20.0, 0.0)
            for i in range(20):
                rows.append(
                    {
                        "week": w,
                        "contest": f"{fmt}{w}",
                        "fmt": fmt,
                        "lineup": f"w{w}-{i}",
                        "fee": fee,
                        "realized": realized[i],
                        "realized_roi": realized[i] / fee - 1,
                        "e_dollars": 10 + skill[i],
                        "e_dollars_herd_copies": 10 + skill[i],
                        "e_dollars_ex_top": 10 + skill[i],
                        "roi": skill[i] / 10,
                        "top1_pct": rng.random(),
                        "cash_pct": rng.random(),
                        "gen_top1_pct": 0.5 + skill[i] / 10,
                        "gen_cash_pct": 0.5 + skill[i] / 10,
                        "p_cash": 0.9,
                        "source": "sam" if i < 3 else "field_gpp",
                        "entered": i < 3 and fmt == "gpp",
                    }
                )
    return pd.DataFrame(rows)


def test_a_ranking_that_tracks_the_money_clears_the_bar_and_noise_does_not():
    t = _table()
    rank = cr.ranking_table(t)
    assert len(rank) == 8
    good = cr.compare(rank, "gen_line", "gpp")
    assert good["diff"] > 0 and good["weeks"] == 4
    assert cr.passes_bar(good)
    three_weeks = cr.compare(cr.ranking_table(_table(weeks=(4, 5, 6))), "gen_line", "gpp")
    assert not cr.passes_bar(three_weeks)  # fewer than four weeks never ships


def test_cash_recalibration_pulls_an_overconfident_probability_down():
    t = _table()
    rc = cr.cash_recalibration(t)
    assert rc["all p_cash"] == pytest.approx(0.9)
    assert rc["all p_recal"] < 0.9
    assert rc["all brier recal"] < rc["all brier"]


def test_best_in_compares_the_top_predicted_contest_with_where_he_entered():
    t = _table()
    bi = cr.best_in_check(t)
    assert bi["lineups"] == 12  # three of his lineups a week, four weeks
    assert set(bi) == {"lineups", "best_in_roi", "entered_roi", "diff", "se"}


def test_simlev_check_reads_the_excess_over_ownership():
    import pandas as pd

    opt = pd.DataFrame(
        {
            "name": list("abcdefghijkl") * 2,
            "week": [5] * 12 + [6] * 12,
            "own": [0.05] * 24,
            "real_own": [0.05] * 24,
            "sim_lev": list(range(12)) * 2,
            "real_opt": [0.05 + 0.01 * i for i in range(12)] * 2,
        }
    )
    s = cr.simlev_check(opt)
    assert s["corr"] == pytest.approx(1.0)
    assert s["top_fifth_excess"] > s["bottom_fifth_excess"]
    assert s["weeks_positive"] == 2
