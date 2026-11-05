"""The duplicate estimate calibrated on the real standings (Plan K item 3): how many other entries hold a
given lineup, from our own ownership (`CashOwn` for double-ups, `GppOwn` for GPPs), and how far off that is.

**The model** is `dups.DupModel`: `E[copies] = scale x entries x product ** exponent`, where `product` is the
lineup's nine ownership shares multiplied (`dups.ownership_product`). One `(scale, exponent)` per format,
fitted by Poisson maximum likelihood (`dups.fit_dup_model`) on every parsed entry of Sam's paid Classic
standings: its copies are the OTHER entries in that contest with the identical nine players.

**Out of sample.** Each week is scored on a fit that never saw it (leave one week out), and so is the
ownership: `CashOwn` / `GppOwn` come from `field_own` refitted without the scored week (the live columns were
fitted on earlier weeks only; Week 4 has no earlier week with both formats, so the leave-one-out ownership is
the only way to score it). Reported per held-out week and format:

- `log_rmse`: the root mean square of `log(1 + observed) - log(1 + predicted)` over every entry;
- `top`: the most-duplicated lineup of each contest, observed against predicted;
- `mine`: Sam's own entries (`series.MY_USERNAME`), observed against predicted;
- `consensus`: the field's lineup (`consensus.consensus_lineup`, the highest total ownership), observed
  against predicted, and how many of its nine are in the contest's most-duplicated lineup.

The final fit (every week) is what the Week page uses, saved with the report to
`data/model_cache/dup_calibration.json` (`save`). Run by `dfs contests dupes` and by `dfs week close`
(fail-soft).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.contests import series as series_mod
from dfs.player_join import normalize_name
from dfs.sim import dups

CALIBRATION_FILE = Path("model_cache") / "dup_calibration.json"
FORMATS = ("cash", "gpp")
OWN_COLUMN = {"cash": "cash_own", "gpp": "gpp_own"}
EDGE_COLUMN = {"cash": "CashOwn", "gpp": "GppOwn"}


def calibration_path(data_dir: Path | None = None) -> Path:
    from dfs import paths

    return (data_dir or paths.DATA_DIR) / CALIBRATION_FILE


def load(data_dir: Path | None = None) -> dict | None:
    try:
        return json.loads(calibration_path(data_dir).read_text())
    except (OSError, ValueError):
        return None


def model_from(calibration: dict | None, fmt: str) -> dups.DupModel | None:
    """The fitted model for `fmt` ("cash" / "gpp") from a saved calibration, or None."""
    fit = ((calibration or {}).get("fit") or {}).get(fmt) or {}
    if fit.get("scale") is None or fit.get("exponent") is None:
        return None
    return dups.DupModel(scale=float(fit["scale"]), exponent=float(fit["exponent"]))


# -- pure pieces -------------------------------------------------------------------------------------------


def entry_rows(lineup_keys: list[tuple[str, ...]], own: dict[str, float], entries: int) -> pd.DataFrame:
    """One row per entry: its ownership product and its observed copies (other entries with the same key)."""
    counts = pd.Series(lineup_keys).value_counts()
    products = [dups.ownership_product([own.get(n, float("nan")) for n in key]) for key in lineup_keys]
    return pd.DataFrame(
        {
            "key": lineup_keys,
            "product": products,
            "copies": [int(counts[k]) - 1 for k in lineup_keys],
            "entries": entries,
        }
    )


def log_rmse(observed, predicted) -> float:  # noqa: ANN001
    o = np.log1p(np.asarray(observed, dtype=float))
    p = np.log1p(np.asarray(predicted, dtype=float))
    return float(np.sqrt(np.mean((o - p) ** 2))) if len(o) else float("nan")


def fit(frame: pd.DataFrame) -> dups.DupModel:
    scale, exponent = dups.fit_dup_model(frame["product"], frame["entries"].to_numpy(), frame["copies"])
    return dups.DupModel(scale=scale, exponent=exponent)


def predict(model: dups.DupModel, frame: pd.DataFrame) -> np.ndarray:
    return model.scale * frame["entries"].to_numpy(float) * frame["product"].to_numpy(float) ** model.exponent


def summarise(held: list[dict]) -> dict[str, dict]:
    """Per format: the entry-weighted log-RMSE over the held-out weeks and the mean ratio of predicted to
    observed copies for the most-duplicated lineups and for Sam's own."""
    out = {}
    for fmt in FORMATS:
        rows = [h for h in held if h["fmt"] == fmt]
        if not rows:
            continue
        n = sum(h["n"] for h in rows)
        rmse = math.sqrt(sum(h["log_rmse"] ** 2 * h["n"] for h in rows) / n) if n else None
        tops = [t for h in rows for t in h["top"]]
        mine = [m for h in rows for m in h["mine"]]
        out[fmt] = {
            "weeks": [h["week"] for h in rows],
            "entries": n,
            "log_rmse": rmse,
            "top_observed": sum(t["observed"] for t in tops),
            "top_predicted": round(sum(t["predicted"] for t in tops), 1),
            "mine_observed": sum(m["observed"] for m in mine),
            "mine_predicted": round(sum(m["predicted"] for m in mine), 1),
            "mine_n": len(mine),
        }
    return out


# -- the run (reads the standings and the snapshots) ---------------------------------------------------------


def _key(slots) -> tuple[str, ...]:  # noqa: ANN001
    return tuple(sorted(normalize_name(n) for _, n in slots))


def run(weeks: list[int], season: int, *, now: datetime | None = None) -> dict:
    """Fit and score on the standings of `weeks` (each held out in turn), then fit on all of them."""
    from dfs import consensus, field_own, results_update
    from dfs.sim import contest_retro as cr
    from dfs.sim import retro

    contests = [c for c in cr.load_real_contests(set(weeks) if weeks else None) if c.fmt in FORMATS]
    if not contests:
        raise ValueError("no saved standings with a contest-log row: nothing to calibrate on")
    weeks = sorted({c.week for c in contests})
    train, _ = field_own.training_frame(season)
    scored = results_update.load_all_scored(season)
    rows: dict[int, dict[str, pd.DataFrame]] = {}
    tops: dict[int, list[dict]] = {}
    for w in weeks:
        model = field_own.fit(train[train["week"] != w].reset_index(drop=True))
        path, _ = retro.edge_snapshot_before(w, season)
        edge = field_own.attach(retro.with_calpts(pd.read_csv(path), scored, w, season), model)
        key = edge["Name"].map(normalize_name)
        frame = cr.week_slate_frame(w, season)
        own = {}
        for fmt in FORMATS:
            col = pd.to_numeric(edge[EDGE_COLUMN[fmt]], errors="coerce")
            own[fmt] = {k: float(v) for k, v in zip(key, col, strict=True) if v == v}
            frame[OWN_COLUMN[fmt]] = frame["name"].map(own[fmt])
        rows[w] = {}
        tops[w] = []
        cons = {fmt: consensus.consensus_from_frame(frame, OWN_COLUMN[fmt]) for fmt in FORMATS}
        for c in [c for c in contests if c.week == w]:
            keys = [_key(s) for s in c.standings.lineups["slots"]]
            part = entry_rows(keys, own[c.fmt], c.entries)
            part["contest"] = c.contest_id
            part["series"] = c.series
            part["mine"] = (
                c.standings.lineups["entry_name"].astype(str).str.lower() == series_mod.MY_USERNAME
            ).to_numpy()
            rows[w].setdefault(c.fmt, [])
            rows[w][c.fmt].append(part)
            counts = pd.Series(keys).value_counts()
            ckey = tuple(sorted(cons[c.fmt])) if cons[c.fmt] else ()
            tops[w].append(
                {
                    "contest": c.contest_id,
                    "series": c.series,
                    "fmt": c.fmt,
                    "entries": c.entries,
                    "top_key": counts.index[0],
                    "top_copies": int(counts.iloc[0]) - 1,
                    "consensus_key": ckey,
                    "consensus_copies": int(counts.get(ckey, 0)) - 1 if ckey in counts.index else 0,
                    "consensus_shared": len(set(ckey) & set(counts.index[0])),
                    "consensus_product": dups.ownership_product(
                        [own[c.fmt].get(n, float("nan")) for n in ckey]
                    )
                    if ckey
                    else None,
                }
            )
    by_fmt = {
        fmt: {w: pd.concat(rows[w][fmt], ignore_index=True) for w in weeks if fmt in rows[w]}
        for fmt in FORMATS
    }
    held = []
    for fmt in FORMATS:
        for w, test in by_fmt[fmt].items():
            others = [f for o, f in by_fmt[fmt].items() if o != w]
            if not others:
                continue
            model = fit(pd.concat(others, ignore_index=True))
            pred = predict(model, test)
            top_rows = []
            for t in [t for t in tops[w] if t["fmt"] == fmt]:
                part = test[test["contest"] == t["contest"]]
                prod = part.loc[part["key"] == t["top_key"], "product"]
                top_rows.append(
                    {
                        "series": t["series"],
                        "observed": t["top_copies"],
                        "predicted": round(
                            float(model.scale * t["entries"] * prod.iloc[0] ** model.exponent), 1
                        )
                        if len(prod)
                        else None,
                        "consensus_observed": t["consensus_copies"],
                        "consensus_predicted": round(
                            float(model.scale * t["entries"] * t["consensus_product"] ** model.exponent), 1
                        )
                        if t["consensus_product"]
                        else None,
                        "consensus_shared": t["consensus_shared"],
                    }
                )
            mine = test[test["mine"]]
            held.append(
                {
                    "week": w,
                    "fmt": fmt,
                    "n": len(test),
                    "scale": model.scale,
                    "exponent": model.exponent,
                    "log_rmse": log_rmse(test["copies"], pred),
                    "top": [t for t in top_rows if t["predicted"] is not None],
                    "mine": [
                        {"series": s, "observed": int(o), "predicted": round(float(p), 1)}
                        for s, o, p in zip(
                            mine["series"], mine["copies"], pred[test["mine"].to_numpy()], strict=True
                        )
                    ],
                }
            )
    final = {}
    for fmt in FORMATS:
        if by_fmt[fmt]:
            m = fit(pd.concat(by_fmt[fmt].values(), ignore_index=True))
            final[fmt] = {
                "scale": m.scale,
                "exponent": m.exponent,
                "entries": int(sum(len(f) for f in by_fmt[fmt].values())),
            }
    return {
        "fitted_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
        "season": season,
        "weeks": weeks,
        "fit": final,
        "held_out": held,
        "summary": summarise(held),
    }


def save(result: dict, data_dir: Path | None = None) -> Path:
    path = calibration_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=1, default=str))
    return path


def report_lines(result: dict) -> list[str]:
    """The terminal report: the fit and the held-out error per format."""
    out = [
        f"Duplicate estimate calibrated on Weeks {', '.join(map(str, result['weeks']))} of "
        f"{result['season']}."
    ]
    names = {"cash": "Double-ups (CashOwn)", "gpp": "GPPs (GppOwn)"}
    for fmt in FORMATS:
        f = (result.get("fit") or {}).get(fmt)
        s = (result.get("summary") or {}).get(fmt)
        if not f:
            continue
        out.append(
            f"{names[fmt]}: copies = {f['scale']:.2f} x entries x product^{f['exponent']:.3f} "
            f"({f['entries']:,} entries)."
        )
        if s:
            out.append(
                f"  held out week by week: log error {s['log_rmse']:.2f}; most-duplicated lineups "
                f"{s['top_observed']:,} copies seen, {s['top_predicted']:,.0f} predicted; your entries "
                f"{s['mine_observed']} seen, {s['mine_predicted']:.0f} predicted (n={s['mine_n']})."
            )
    return out
