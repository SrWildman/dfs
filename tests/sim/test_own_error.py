import math

import numpy as np
import pytest

from dfs.sim import own_error as oe

POSITIONS = ["QB"] * 4 + ["RB"] * 6 + ["WR"] * 9 + ["TE"] * 4 + ["DST"] * 4
TEAMS = (
    ["A", "B", "C", "D"]  # QBs
    + ["A", "B", "C", "D", "E", "F"]  # RBs
    + ["A", "A", "B", "B", "C", "C", "D", "D", "E"]  # WRs
    + ["A", "B", "C", "D"]  # TEs
    + ["A", "B", "C", "D"]  # DSTs
)
TOTALS = {"QB": 1.0, "RB": 2.3, "WR": 3.3, "TE": 1.3, "DST": 1.0}


def _mu() -> np.ndarray:
    out = np.zeros(len(POSITIONS))
    for position in oe.POSITIONS:
        idx = [i for i, p in enumerate(POSITIONS) if p == position]
        w = np.linspace(2.0, 0.5, len(idx))
        out[idx] = w / w.sum() * TOTALS[position]
    return out


def test_sigma_falls_with_the_level_and_floors_at_c0():
    err = oe.FormatError(c0=0.05, c1=0.01)
    s = err.sigma(np.array([0.005, 0.05, 0.4]))
    assert s[0] > s[1] > s[2] > math.sqrt(math.log1p(0.05))


def test_draw_keeps_position_totals_and_zero_forecasts():
    mu = _mu()
    mu[0] = 0.0
    mu = np.where(np.array(POSITIONS) == "QB", mu / mu.sum(), mu)  # QBs still sum to 1 without player 0
    d = oe.draw_shares(mu, POSITIONS, TEAMS, TOTALS, oe.FormatError(0.1, 0.01), 50, np.random.default_rng(0))
    assert d.shape == (50, len(mu))
    assert (d[:, 0] == 0).all()
    assert (d <= oe.MAX_OWN).all()
    for position, total in TOTALS.items():
        cols = [i for i, p in enumerate(POSITIONS) if p == position]
        whole = ~(d[:, cols] >= oe.MAX_OWN).any(axis=1)  # a capped share breaks the total by design
        assert whole.sum() > 20
        assert d[whole][:, cols].sum(axis=1) == pytest.approx(total)


def test_draw_is_centred_on_the_forecast_and_deterministic():
    mu = _mu()
    err = oe.FormatError(0.08, 0.005)
    a = oe.draw_shares(mu, POSITIONS, TEAMS, TOTALS, err, 4000, np.random.default_rng(1))
    b = oe.draw_shares(mu, POSITIONS, TEAMS, TOTALS, err, 4000, np.random.default_rng(1))
    assert np.array_equal(a, b)
    assert a.mean(axis=0) == pytest.approx(mu, rel=0.08, abs=0.01)
    # and it spreads: the relative sd of a mid player is near his sigma
    j = POSITIONS.index("WR") + 3
    assert a[:, j].std() / a[:, j].mean() == pytest.approx(err.sigma(mu[j : j + 1])[0], rel=0.35)


def test_team_factor_ties_a_qb_to_his_receivers_only():
    mu = _mu()
    rng = np.random.default_rng(2)
    tied = oe.draw_shares(mu, POSITIONS, TEAMS, TOTALS, oe.FormatError(0.1, 0.0, team_corr=0.6), 6000, rng)
    free = oe.draw_shares(mu, POSITIONS, TEAMS, TOTALS, oe.FormatError(0.1, 0.0), 6000, rng)
    qb, wr = 0, POSITIONS.index("WR")  # QB of team A and a WR of team A
    rb = POSITIONS.index("RB")  # RB of team A
    corr = lambda d, i, j: np.corrcoef(np.log(d[:, i]), np.log(d[:, j]))[0, 1]  # noqa: E731
    assert corr(tied, qb, wr) > 0.3
    assert abs(corr(free, qb, wr)) < 0.1
    assert abs(corr(tied, qb, rb)) < 0.1  # a running back is not in the pass game


def test_tilt_moves_chalk_and_longshots_opposite_ways():
    mu = _mu()
    err = oe.FormatError(0.0, 0.0, tilt_sd=0.5)
    d = oe.draw_shares(mu, POSITIONS, TEAMS, TOTALS, err, 4000, np.random.default_rng(3))
    first, last = POSITIONS.index("WR"), POSITIONS.index("WR") + 8  # biggest and smallest WR forecast
    assert np.corrcoef(d[:, first], d[:, last])[0, 1] < -0.5


def test_theta_mass_is_a_distribution_centred_on_the_forecast():
    mu = np.array([0.02, 0.2, 0.6])
    pos = ["WR", "WR", "QB"]
    totals = {"WR": 1.0, "QB": 1.0}
    for family, params in (("lognormal", (0.4,)), ("hybrid", (0.05, 0.01)), ("dirichlet", (60.0,))):
        m = oe.theta_mass(family, mu, pos, totals, params)
        assert m.sum(axis=1) == pytest.approx(1.0, abs=1e-3)
        mean = (m * oe._MID[None, :]).sum(axis=1)
        assert mean == pytest.approx(mu, rel=0.08)
    point = oe.theta_mass("point", mu, pos, totals, ())
    assert (point.max(axis=1) == 1.0).all()
    with pytest.raises(ValueError):
        oe.theta_mass("normal", mu, pos, totals, ())


def test_count_loglik_prefers_the_forecast_and_penalises_a_confident_miss():
    mu = np.array([0.2])
    point = oe.theta_mass("point", mu, ["WR"], {"WR": 1.0}, ())
    wide = oe.theta_mass("lognormal", mu, ["WR"], {"WR": 1.0}, (0.5,))
    n = 4000
    on, off = np.array([800.0]), np.array([1200.0])  # 20% held, 30% held
    assert oe.count_loglik(on, n, point)[0] > oe.count_loglik(off, n, point)[0]
    # a miss that large is hopeless for a point forecast and fine for a wide one
    assert oe.count_loglik(off, n, wide)[0] > oe.count_loglik(off, n, point)[0] + 100


def test_json_round_trip_and_missing_file(tmp_path):
    model = oe.OwnershipErrorModel(
        formats={
            "gpp": oe.FormatError(0.07, 0.008, 0.33, 0.02),
            "cash": oe.FormatError(0.4, 0.02, 0.45, 0.06),
        },
        weeks=(2, 4),
    )
    path = oe.save(model, tmp_path / "m.json")
    assert oe.load(path) == model
    assert oe.load(tmp_path / "nope.json") is None
    (tmp_path / "bad.json").write_text("{")
    assert oe.load(tmp_path / "bad.json") is None


def test_mean_model_k_one_is_tffb_scaled_and_the_exponent_moves_with_stake_and_size():
    own = np.array([0.30, 0.10, 0.60, 0.20, 0.20])
    pos = ["QB", "QB", "WR", "WR", "WR"]
    m = oe.MeanModel(k=1.0, totals={"QB": 1.0, "WR": 2.0})
    s = m.shares(own, pos)
    assert s[:2] == pytest.approx([0.75, 0.25])
    assert s[2:] == pytest.approx([1.2 * 0.95 / 1.2, 0.4, 0.4], abs=0.21)  # the 60% WR is capped at MAX_OWN
    assert s[2] == pytest.approx(oe.MAX_OWN)
    sized = oe.MeanModel(k=1.1, totals={"QB": 1.0}, k_fee=0.1, k_size=-0.1)
    assert sized.exponent() == pytest.approx(1.1)
    assert sized.exponent(fee=5.0, entries=2000) == pytest.approx(1.1)
    assert sized.exponent(fee=50.0) == pytest.approx(1.1 + 0.1 * math.log(10))
    assert sized.exponent(entries=20000) == pytest.approx(1.1 - 0.1 * math.log(10))
    # a sharper exponent puts more on the favourite; a missing Own% counts at the floor, not as an error
    flat = oe.MeanModel(k=1.0, totals={"QB": 1.0}).shares(np.array([0.6, 0.4]), ["QB", "QB"])
    sharp = oe.MeanModel(k=2.0, totals={"QB": 1.0}).shares(np.array([0.6, 0.4]), ["QB", "QB"])
    assert sharp[0] > flat[0]
    assert np.isfinite(m.shares(np.array([np.nan, 0.5]), ["QB", "QB"])).all()


def test_json_round_trip_keeps_the_means_and_reads_a_part_one_file(tmp_path):
    model = oe.OwnershipErrorModel(
        formats={"gpp": oe.FormatError(0.07, 0.008)},
        weeks=(2,),
        means={"gpp": oe.MeanModel(k=1.05, totals={"QB": 1.0}, k_size=-0.08)},
    )
    assert oe.load(oe.save(model, tmp_path / "m.json")) == model
    old = tmp_path / "old.json"
    old.write_text(
        '{"formats": {"gpp": {"c0": 0.1, "c1": 0.01, "team_corr": 0, "tilt_sd": 0}}, "weeks": [2]}'
    )
    assert oe.load(old).means == {}
