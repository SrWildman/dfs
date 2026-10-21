from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dfs.player_join import normalize_name
from dfs.sim import own_error as oe
from dfs.sim import own_error_retro as r

TOTALS = {"QB": 1.0, "RB": 2.3, "WR": 3.3, "TE": 1.3, "DST": 1.0}
TRUTH = {"gpp": oe.FormatError(0.07, 0.008, team_corr=0.4), "cash": oe.FormatError(0.4, 0.02, team_corr=0.4)}
ENTRIES = 6000


def _slate(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for team in range(12):
        t = f"T{team:02d}"
        for position, n in (("QB", 1), ("RB", 2), ("WR", 3), ("TE", 1), ("DST", 1)):
            rows += [(f"{position.lower()}{chr(97 + team)}{chr(97 + k)}", position, t) for k in range(n)]
    f = pd.DataFrame(rows, columns=["name", "position", "team"])
    f["tffb"] = 0.0
    for position, total in TOTALS.items():
        m = f["position"] == position
        w = rng.dirichlet(np.full(m.sum(), 0.7)) * total
        f.loc[m, "tffb"] = np.minimum(w, 0.6)
    f["display"] = f["name"]
    assert (f["name"].map(normalize_name) == f["name"]).all()  # field_own joins on normalised names
    f["opp"] = "X"
    f["salary"] = 5000
    return f


def _contest(week: int, fmt: str, cid: str, frame: pd.DataFrame, rng, entries: int = ENTRIES):
    mu = frame["tffb"].to_numpy()
    theta = oe.draw_shares(mu, frame["position"], frame["team"], TOTALS, TRUTH[fmt], 1, rng)[0]
    held = rng.binomial(entries, theta) / entries
    pts = rng.gamma(2.0, 4.0, len(frame))
    return SimpleNamespace(
        contest_id=cid,
        week=week,
        fmt=fmt,
        entries=entries,
        fee=5.0,
        own=dict(zip(frame["name"], held, strict=True)),
        fpts=dict(zip(frame["name"], pts, strict=True)),
        points=np.array([float(pts @ held)] * 3),
    )


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(7)
    out = {}
    for week in (2, 3, 4):
        frame = _slate(rng)
        contests = [
            _contest(week, fmt, f"{week}{fmt}{k}", frame, rng) for fmt in oe.FORMATS for k in range(2)
        ]
        out[week] = r.WeekData(week, frame, contests)
    return out


def test_week_data_counts_pooled_share_and_points(data):
    w = data[2]
    c = w.by_format("gpp")[0]
    counts = w.counts(c)
    assert counts.shape == (len(w.frame),)
    assert counts.sum() == pytest.approx(9 * ENTRIES, rel=0.05)
    pooled = w.pooled_share("cash")
    assert pooled.sum() == pytest.approx(9.0, rel=0.05)
    assert w.points(c).shape == (len(w.frame),)
    small = r.WeekData(2, w.frame, [SimpleNamespace(**{**vars(c), "entries": 100})])
    assert small.pooled_share("gpp") is None  # too small to pool


def test_the_fit_recovers_the_error_the_data_was_drawn_with(data):
    for fmt, truth in TRUTH.items():
        weeks = sorted(data)
        mus = {w: data[w].frame["tffb"].to_numpy() for w in weeks}
        c0, c1 = r.fit_family(data, weeks, fmt, "hybrid", lambda w, mus=mus: mus[w], TOTALS)
        got = oe.FormatError(c0, c1).sigma(np.array([0.05, 0.3]))
        want = truth.sigma(np.array([0.05, 0.3]))
        assert got == pytest.approx(want, rel=0.3)
    assert r.fit_family(data, [2], "gpp", "point", lambda w: None, TOTALS) == ()


def test_the_error_families_beat_a_point_forecast_by_a_wide_margin(data):
    table, folds = r.compare_families(data, families=("point", "lognormal", "hybrid"))
    s = r.summarize_families(table).set_index(["format", "family"])
    for fmt in oe.FORMATS:
        assert s.loc[(fmt, "hybrid"), "vs point"] > 1000
        assert s.loc[(fmt, "lognormal"), "vs point"] > 1000
    assert set(folds) == {(w, f) for w in data for f in oe.FORMATS}
    # earlier-only drops the first week of each format (nothing to fit on)
    early, _ = r.compare_families(data, families=("point", "hybrid"), earlier_only=True)
    assert sorted(early["week"].unique()) == [3, 4]


def test_the_team_factor_shows_up_in_the_residuals(data):
    weeks = sorted(data)
    models = {w: r.fit_mean(data, [x for x in weeks if x != w], "gpp") for w in weeks}
    res = r.residual_table(data, weeks, "gpp", models)
    assert {"e", "e0", "lmu"} <= set(res.columns)
    corr, pairs = r.team_correlation(res)
    assert pairs > 20
    assert corr > 0.1
    assert set(r.week_tilts(res)) <= set(weeks)
    c, tilt = r.fit_dependence(res)
    assert 0.0 <= c <= 1.0 and tilt >= 0.0


def test_team_correlation_needs_pairs_and_tilts_need_a_spread():
    empty = pd.DataFrame({"week": [1], "position": ["QB"], "team": ["A"], "e0": [0.1], "lmu": [-2.0]})
    assert r.team_correlation(empty) == (0.0, 0)
    assert r.fit_dependence(empty) == (0.0, 0.0)
    rng = np.random.default_rng(0)
    lmu = np.log(rng.uniform(0.02, 0.4, 40))
    res = pd.DataFrame({"week": 1, "position": "WR", "team": "A", "lmu": lmu, "e0": 0.3 * (lmu - lmu.mean())})
    assert r.week_tilts(res)[1] == pytest.approx(0.3)


def test_field_mean_draws_widen_with_error_and_centre_on_the_forecast(data):
    w = data[2]
    c = w.by_format("gpp")[0]
    mu = w.frame["tffb"].to_numpy()
    none = r.field_mean_draws(w, c, mu, TOTALS, None, draws=400)
    wide = r.field_mean_draws(w, c, mu, TOTALS, TRUTH["gpp"], draws=400)
    assert wide.std() > 2 * none.std()
    assert none.mean() == pytest.approx(float(mu @ w.points(c)), rel=0.02)
    assert wide.mean() == pytest.approx(none.mean(), rel=0.1)
    assert np.array_equal(none, r.field_mean_draws(w, c, mu, TOTALS, None, draws=400))


def test_fit_model_and_summaries(data):
    model = r.fit_model(data)
    assert set(model.formats) == set(oe.FORMATS)
    assert model.weeks == (2, 3, 4)
    assert model.formats["cash"].c0 > model.formats["gpp"].c0  # the cash field was drawn noisier
    table, folds = r.compare_families(data, families=("point", "hybrid"))
    chk = r.field_mean_check(data, folds)
    assert {"none", "independent", "team", "team+tilt"} == set(chk["variant"])
    summary = r.summarize_field_mean(chk)
    assert (summary["contests"] > 0).all()
    none = summary[summary["variant"] == "none"].set_index("format")
    team = summary[summary["variant"] == "team"].set_index("format")
    assert (team["model_sd"] > none["model_sd"]).all()
    levels = r.level_table(chk.assign(mu=0.1, e=0.0, e0=0.0), {f: model.formats[f] for f in oe.FORMATS})
    assert {"n", "sd_e", "model_sigma"} <= set(levels.columns)
