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
