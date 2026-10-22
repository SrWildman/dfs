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
    assert r.fit_dependence(pd.DataFrame()) == (0.0, 0.0)  # no week with a big contest of the format
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


def test_compare_means_scores_every_spec_on_the_same_players(data):
    table, fits = r.compare_means(data, specs=("tffb", "power", "size"))
    assert set(table["spec"]) == {"tffb", "power", "size"}
    # the data were drawn around TFFB itself, so a fitted exponent lands near 1
    for (_week, _fmt, spec), (mean, _) in fits.items():
        if spec == "power":
            assert 0.7 < mean.k < 1.4
        if spec == "tffb":
            assert mean.k == 1.0
    s = r.summarize_means(table)
    assert {"vs tffb", "weeks better", "mae", "bias_on_actual", "bias_on_forecast"} <= set(s.columns)
    assert (s[s["spec"] == "tffb"]["vs tffb"] == 0).all()


def test_model_without_leaves_the_week_out_and_caches_it(data, tmp_path, monkeypatch):
    calls = []
    real = r.fit_model
    monkeypatch.setattr(
        r,
        "fit_model",
        lambda d, **kw: calls.append(sorted(d)) or real(d, specs={"gpp": "power", "cash": "power"}),
    )
    m = r.model_without(3, data=data, folder=tmp_path)
    assert m.weeks == (2, 4) and calls == [[2, 4]]
    assert r.model_without(3, data=data, folder=tmp_path) == m and len(calls) == 1  # from the cache
    before = r.model_without(4, data=data, folder=tmp_path, earlier_only=True)
    assert before.weeks == (2, 3)
    assert r.model_without(2, data=data, folder=tmp_path, earlier_only=True) is None
    assert set(m.means) == set(oe.FORMATS)


def test_the_backtest_flag_swaps_the_field_and_leaves_an_unpublished_week_alone(monkeypatch):
    from dataclasses import dataclass

    from dfs.sim import field as F
    from dfs.sim import own_error_flag as flag
    from dfs.sim.simulate import PlayerSpec

    specs = [PlayerSpec(f"p{i}", "WR", f"T{i}", "Z", f"G{i}", "WR1", 10.0) for i in range(4)]
    own = {s.id: 0.25 for s in specs}
    fld = F.build_field(specs, own, cash_offset=0.6, top_offset=63.2, coverage=1.0)

    @dataclass
    class Lines:
        field: object

    ctx = SimpleNamespace(
        week=6, edge=pd.DataFrame({"Name": list(own), "Own%": list(own.values())}), lines=Lines(fld), notes=[]
    )
    model = oe.OwnershipErrorModel(
        formats={"gpp": oe.FormatError(0.2, 0.01), "cash": oe.FormatError(0.2, 0.01)}, weeks=(2, 4, 5)
    )
    monkeypatch.setattr(r, "model_before", lambda week: model if week == 6 else None)
    out = flag.prepare(ctx, True)
    assert out.lines.field.cash_draws is not None and "weeks [2, 4, 5]" in out.notes[-1]
    assert out.notes[-1].startswith("ownership_uncertainty")
    mean_only = flag.prepare_mean(SimpleNamespace(**{**vars(ctx), "lines": Lines(fld), "notes": []}), True)
    assert mean_only.lines.field.cash_draws is None and mean_only.notes[-1].startswith("ownership_mean")
    assert flag.prepare(SimpleNamespace(**{**vars(ctx), "lines": Lines(None)}), True).lines.field is None
    early = flag.prepare(SimpleNamespace(**{**vars(ctx), "week": 2, "lines": Lines(fld), "notes": []}), True)
    assert early.lines.field is fld and "no earlier weeks" in early.notes[-1]
    assert flag.prepare(ctx, False) is ctx


def test_the_plugin_modules_register_both_ownership_switches():
    from dfs import backtest_settings as bs

    assert "dfs.sim.own_error_flag" in bs.PLUGIN_MODULES and bs.load_plugins() == []
    assert {"ownership_mean", "ownership_uncertainty"} <= set(bs.FLAGS)
