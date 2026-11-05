"""The consensus lineup and copies of Sam's lineups (Plan K item 3).

**The field's lineup** is the legal DraftKings lineup (QB, 2 RB, 3 WR, TE, FLEX, DST under $50,000) with the
highest total predicted ownership: `CashOwn` for double-ups, `GppOwn` for GPPs. It is solved exactly by the
field generator's MILP (`sim.field_gen.chalk_lineups`, `objective="sum"`), with only DraftKings' rules: the
builder's own extra rules (no DST against his QB, one RB a game) are Sam's preferences, not the field's.
Players OUT, on IR or Doubtful are left out. On Weeks 5-8 the highest-total and the highest-product lineup
were the same lineup in all 38 of his contests, and in the double-ups it matched 6 to 9 of the nine in the
contest's most-duplicated one.

**Copies** of a lineup in a contest: `dups.DupModel` with the `(scale, exponent)` fitted on his standings
(`sim/dup_calibration.py`, saved in `data/model_cache/dup_calibration.json`, held-out error reported there),
from the lineup's ownership product and the contest's size. The size of an entered contest is the median field
of its series in the contest log (`contest_tiers.history_by_series`); before entries are recorded, the typical
field of his paid contests of that kind this season.

**Reading it.** Cash: being close to the consensus is fine (the cash line is the field's middle; sharing it is
not a mistake), so nothing is flagged. GPP: copies split a prize, so a lineup expected at `GPP_COPIES_FLAG`
(5) or more copies is flagged.

Pure: rows and dicts in, a dict out. No sheet, no network.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.contests import series as series_mod
from dfs.player_join import normalize_name
from dfs.sim import dups, field_gen

CASH, GPP = "cash", "gpp"
OWN_COLUMN = {CASH: "CashOwn", GPP: "GppOwn"}
NOT_PLAYING = frozenset({"OUT", "IR", "D"})
GPP_COPIES_FLAG = 5.0
DEFAULT_ENTRIES = {CASH: dups.DEFAULT_DOUBLE_UP_ENTRIES, GPP: dups.DEFAULT_GPP_ENTRIES}


def kind_of_format(fmt: str) -> str:
    return CASH if fmt in series_mod.CASH_FORMATS else GPP


def _num(value) -> float | None:  # noqa: ANN001
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def consensus_from_frame(frame: pd.DataFrame, own_column: str) -> tuple[str, ...] | None:
    """The highest-total-ownership legal lineup of `frame` (`name`, `position`, `team`, `opp`, `salary` and
    `own_column`), as the frame's names; None when nobody has an ownership number."""
    own = pd.to_numeric(frame[own_column], errors="coerce")
    if not own.notna().any() or float(own.max()) <= 0:
        return None
    slate = field_gen.Slate.from_frame(frame.assign(_own=own.fillna(0.0)), "_own")
    found = field_gen.chalk_lineups(slate, k=1, objective="sum")
    if len(found) == 0:
        return None
    return tuple(slate.names[i] for i in found[0])


def edge_frame(edge: Iterable[dict]) -> pd.DataFrame:
    """The synced slate as the solver's frame (display names kept), without OUT / IR / Doubtful players."""
    rows = [r for r in edge if str(r.get("Avail") or "").upper() not in NOT_PLAYING]
    return pd.DataFrame(
        {
            "name": [str(r.get("Name") or "") for r in rows],
            "position": [str(r.get("Position") or "") for r in rows],
            "team": [str(r.get("Team") or "") for r in rows],
            "opp": [str(r.get("Opp") or "") for r in rows],
            "salary": [_num(r.get("Salary")) for r in rows],
            "CashOwn": [_num(r.get("CashOwn")) for r in rows],
            "GppOwn": [_num(r.get("GppOwn")) for r in rows],
        }
    )


@dataclass(frozen=True)
class Lineup:
    """One of Sam's lineups: a label ("Cash 2", "L3", "#1"), its kind when known, its nine names, and the
    contests it went into as `(name, format)` (none before entries are recorded)."""

    label: str
    kind: str  # "cash" / "gpp" / "" (unknown: decided per contest)
    names: tuple[str, ...]
    contests: tuple[tuple[str, str], ...] = ()


def own_map(edge: Iterable[dict], kind: str) -> dict[str, float]:
    out = {}
    for r in edge:
        value = _num(r.get(OWN_COLUMN[kind]))
        if value is not None:
            out[normalize_name(r.get("Name"))] = value
    return out


def shares(names: Sequence[str], own: dict[str, float]) -> list[float]:
    return [own.get(normalize_name(n), float("nan")) for n in names]


def contest_size(
    name: str, kind: str, sizes: dict[str, dict[str, float]], typical: dict[str, float]
) -> tuple[int, str]:
    """(entrants, where the number came from) for an entered contest."""
    info = series_mod.classify("Classic", name, fee=None, contest_entries=None, places_paid=None)
    size = sizes.get(info.series)
    if size and size.get("entrants"):
        return int(size["entrants"]), "series median"
    if typical.get(kind):
        return int(typical[kind]), f"your typical {kind} field"
    return int(DEFAULT_ENTRIES[kind]), "default"


def typical_sizes(typed, season: int) -> dict[str, float]:  # noqa: ANN001 - contests.history.typed frame
    """The median field of his paid contests this season, by kind: double-ups and 50/50s for cash (an H2H's
    two entrants would make "a typical double-up" a field of 2), every Classic GPP for GPP."""
    if typed is None or not len(typed):
        return {}
    paid = typed[(typed["fee"] > 0) & (typed["season"] == season) & typed["entries"].notna()]
    paid = paid.drop_duplicates("contest_key")
    picks = {
        CASH: paid["format"].isin([series_mod.DOUBLE_UP, series_mod.FIFTY_FIFTY]),
        GPP: paid["format"].isin([series_mod.SE_GPP, series_mod.N_MAX_GPP]),
    }
    out = {}
    for kind in (CASH, GPP):
        rows = paid[picks[kind]]
        if len(rows):
            out[kind] = float(rows["entries"].astype(float).median())
    return out


def view(
    edge: list[dict] | None,
    lineups: Sequence[Lineup],
    *,
    models: dict[str, dups.DupModel | None],
    sizes: dict[str, dict[str, float]] | None = None,
    typical: dict[str, float] | None = None,
) -> dict:
    """The page's block: each format's consensus lineup (names, total ownership, salary, its own expected
    copies in a typical field) and each of Sam's lineups (players shared with the consensus, expected copies
    in each contest, the 5+ GPP flag). `pending` when the ownership columns are not out yet."""
    sizes, typical = sizes or {}, typical or {}
    if not edge:
        return {"pending": True, "reason": "nothing synced"}
    frame = edge_frame(edge)
    out: dict = {"pending": False, "consensus": {}, "lineups": [], "models": {}}
    owns = {k: own_map(edge, k) for k in (CASH, GPP)}
    salary = {normalize_name(r.get("Name")): _num(r.get("Salary")) for r in edge}
    for kind in (CASH, GPP):
        model = models.get(kind)
        out["models"][kind] = None if model is None else {"scale": model.scale, "exponent": model.exponent}
        names = consensus_from_frame(frame, OWN_COLUMN[kind]) if owns[kind] else None
        if names is None:
            out["consensus"][kind] = None
            continue
        sh = shares(names, owns[kind])
        n = int(typical.get(kind) or DEFAULT_ENTRIES[kind])
        out["consensus"][kind] = {
            "names": list(names),
            "own_sum": float(np.nansum(sh)),
            "salary": int(sum(salary.get(normalize_name(x)) or 0 for x in names)),
            "entrants": n,
            "copies": None if model is None else round(model.expected(sh, n), 1),
        }
    if all(v is None for v in out["consensus"].values()):
        return {"pending": True, "reason": "CashOwn and GppOwn are not out yet (they need TFFB's ownership)"}
    for lu in lineups:
        cons_keys = {
            k: {normalize_name(x) for x in (out["consensus"][k] or {}).get("names", [])} for k in (CASH, GPP)
        }
        mine = {normalize_name(x) for x in lu.names if x}
        row = {
            "label": lu.label,
            "kind": lu.kind,
            "names": list(lu.names),
            "shared": {k: len(mine & cons_keys[k]) if cons_keys[k] else None for k in (CASH, GPP)},
            "contests": [],
        }
        targets = list(lu.contests) or [("", lu.kind or GPP)]
        for name, fmt in targets:
            kind = fmt if fmt in (CASH, GPP) and not name else kind_of_format(fmt)
            model = models.get(kind)
            if name:
                n, source = contest_size(name, kind, sizes, typical)
            else:
                n = int(typical.get(kind) or DEFAULT_ENTRIES[kind])
                source = f"your typical {kind} field" if typical.get(kind) else "default"
            copies = None if model is None else round(model.expected(shares(lu.names, owns[kind]), n), 1)
            row["contests"].append(
                {
                    "name": name or f"a typical {'double-up' if kind == CASH else 'GPP'}",
                    "kind": kind,
                    "entrants": n,
                    "size_from": source,
                    "copies": copies,
                    "flag": kind == GPP and copies is not None and copies >= GPP_COPIES_FLAG,
                }
            )
        out["lineups"].append(row)
    return out


def lineups_from_builds(builds: Iterable[dict]) -> list[Lineup]:
    """The latest Suggested round's builds (`suggest_state` dicts) as lineups."""
    return [
        Lineup(
            f"{str(b.get('kind', '')).title()} {b.get('number', '')}".strip(),
            str(b.get("kind") or ""),
            tuple(b.get("names") or []),
        )
        for b in builds
        if b.get("names")
    ]


def lineups_from_entered(record: dict) -> list[Lineup]:
    """A `data/entered/<season>-wk<NN>.json` record (as read JSON) as lineups with their contests."""
    out = []
    for i, lu in enumerate(record.get("lineups") or [], start=1):
        src = lu.get("source") or {}
        label = lu.get("label") or (
            f"{str(src.get('kind', '')).title()} build {src.get('number')}" if src else f"#{i}"
        )
        contests = tuple(
            (str(c.get("name") or ""), str(c.get("format") or "")) for c in lu.get("contests") or []
        )
        out.append(Lineup(label, str(src.get("kind") or ""), tuple(lu.get("names") or []), contests))
    return out
