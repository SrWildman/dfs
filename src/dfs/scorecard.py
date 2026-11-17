"""The process scorecard at close (Plan K item 7): did the week go to plan, separately from how it scored.

- **Spend by tier against the plan**: `contest_plan.check_week_tiers`, the line `week close` already prints.
- **Chalk decisions against what he played**: each `cash: Name` on the last round's Always use (Play) or Never
  use (Fade) lines, the Week page's chalk answers (Plan J), checked against his cash lineups: a Play is kept
  when the player is in one of them, a Fade when he is in none.
- **Overrides from the nearest build**: each distinct lineup he entered against the saved build of the same
  kind (cash / GPP, any round of the week) it shares the most players with (the earlier build on a tie). The
  players he swapped in and out, and the points that cost or gained: his lineup's actual DK points minus the
  build's. A lineup identical to a build is no override. One sharing `entered.MATCH_MIN` (8) or more players
  is an **edited build**; one sharing fewer is **his own lineup**, still measured against the nearest build
  (the model's closest offer) but counted apart.
- **The season line**: the weeks in `data/contests/scorecard.csv` (one row per closed week, replaced on a
  rerun): "your overrides against the model: +X / -Y points over N weeks", X the sum of the lineups that beat
  their build, Y of those that trailed.

It says what was decided, not whether it paid: a kept plan can lose and an override can win. Pure except
`append_csv` / `season_line_from_csv`.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from dfs.entered import MATCH_MIN
from dfs.player_join import normalize_name

CSV_COLUMNS = [
    "season",
    "week",
    "lineups",
    "overrides",
    "gain",
    "loss",
    "net",
    "decisions",
    "decisions_kept",
    "tier_over",
]


def k(name: str) -> str:
    return normalize_name(str(name))


@dataclass(frozen=True)
class Build:
    label: str
    kind: str  # cash | gpp
    names: tuple[str, ...]


@dataclass(frozen=True)
class Played:
    kind: str  # cash | gpp
    names: tuple[str, ...]
    entries: int = 1


@dataclass(frozen=True)
class Override:
    kind: str
    names: tuple[str, ...]
    entries: int
    build: str  # the nearest build's label, "" when there is none of that kind
    shared: int
    into: list[tuple[str, float]]  # his, not the build's (actual points)
    out: list[tuple[str, float]]  # the build's, not his
    points: float
    build_points: float | None

    @property
    def delta(self) -> float | None:
        return None if self.build_points is None else self.points - self.build_points


@dataclass
class Card:
    week: int
    tier_line: str = ""
    tier_over: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)  # name, answer (play / fade), kept (bool)
    overrides: list[Override] = field(default_factory=list)

    @property
    def changed(self) -> list[Override]:
        return [o for o in self.overrides if o.build and o.shared < len(o.names)]

    @property
    def gain(self) -> float:
        return sum(o.delta for o in self.changed if o.delta is not None and o.delta > 0)

    @property
    def loss(self) -> float:
        return sum(o.delta for o in self.changed if o.delta is not None and o.delta < 0)


def points_of(names, fpts: dict[str, float]) -> float:  # noqa: ANN001
    return sum(fpts.get(k(n), 0.0) for n in names)


def nearest(played: Played, builds: list[Build]) -> Build | None:
    mine = {k(n) for n in played.names}
    best, best_shared = None, -1
    for b in builds:
        if b.kind != played.kind:
            continue
        shared = len(mine & {k(n) for n in b.names})
        if shared > best_shared:
            best, best_shared = b, shared
    return best


def override(played: Played, builds: list[Build], fpts: dict[str, float]) -> Override:
    b = nearest(played, builds)
    pts = points_of(played.names, fpts)
    if b is None:
        return Override(played.kind, played.names, played.entries, "", 0, [], [], pts, None)
    mine = {k(n): n for n in played.names}
    theirs = {k(n): n for n in b.names}
    into = [(mine[x], fpts.get(x, 0.0)) for x in sorted(set(mine) - set(theirs))]
    out = [(theirs[x], fpts.get(x, 0.0)) for x in sorted(set(theirs) - set(mine))]
    return Override(
        played.kind,
        played.names,
        played.entries,
        b.label,
        len(set(mine) & set(theirs)),
        into,
        out,
        pts,
        points_of(b.names, fpts),
    )


def decisions_from_rules(rules: dict[str, str]) -> list[tuple[str, str]]:
    """(name, play / fade) for every `cash: Name` item on the rules box's Always use / Never use lines."""
    out = []
    for label, answer in (("Always use", "play"), ("Never use", "fade")):
        for item in str(rules.get(label, "") or "").replace(";", ",").split(","):
            item = item.strip()
            if item.lower().startswith("cash:"):
                name = item.split(":", 1)[1].strip()
                if name:
                    out.append((name, answer))
    return out


def check_decisions(decisions: list[tuple[str, str]], cash: list[Played]) -> list[dict]:
    held = {k(n) for p in cash for n in p.names}
    return [
        {"name": n, "answer": a, "kept": (k(n) in held) == (a == "play"), "played": k(n) in held}
        for n, a in decisions
    ]


def card(
    week: int,
    played: list[Played],
    builds: list[Build],
    fpts: dict[str, float],
    *,
    decisions: list[tuple[str, str]] | None = None,
    tier_line: str = "",
    tier_over: list[str] | None = None,
) -> Card:
    c = Card(week, tier_line, list(tier_over or []))
    c.decisions = check_decisions(decisions or [], [p for p in played if p.kind == "cash"])
    c.overrides = [override(p, builds, fpts) for p in played]
    return c


def payload(c: Card, season_line: str = "") -> dict:
    return {
        "week": c.week,
        "tier_line": c.tier_line,
        "tier_over": c.tier_over,
        "decisions": c.decisions,
        "lineups": len(c.overrides),
        "no_build": sum(1 for o in c.overrides if not o.build),
        "overrides": [
            {
                "kind": o.kind,
                "entries": o.entries,
                "build": o.build,
                "shared": o.shared,
                "into": o.into,
                "out": o.out,
                "points": o.points,
                "build_points": o.build_points,
                "delta": o.delta,
            }
            for o in sorted(c.changed, key=lambda o: o.delta or 0.0)
        ],
        "gain": c.gain,
        "loss": c.loss,
        "season_line": season_line,
    }


def swap_text(o: dict) -> str:
    """`in Michael Wilson 29.8, Chase Brown 17.1; out Tee Higgins 40.5, CeeDee Lamb 12.8`."""
    into = ", ".join(f"{n} {pts:.1f}" for n, pts in o["into"])
    out = ", ".join(f"{n} {pts:.1f}" for n, pts in o["out"])
    return f"in {into}; out {out}"


def lines(p: dict) -> list[str]:
    """The close report's lines (and the page's summary)."""
    out = [f"Process scorecard, Week {p['week']} (what was decided, not what it paid):"]
    if p.get("tier_line"):
        out.append(f"  spend by tier: {p['tier_line']}")
    else:
        out.append(
            "  spend by tier: no tier line saved at that week's close (it needs the Weekly Budget then)"
        )
    ds = p.get("decisions") or []
    if ds:
        kept = sum(d["kept"] for d in ds)
        out.append(
            f"  chalk decisions: {kept} of {len(ds)} kept ("
            + ", ".join(
                f"{d['answer'].title()} {d['name']}: {'kept' if d['kept'] else 'not kept'}" for d in ds
            )
            + ")"
        )
    else:
        out.append("  chalk decisions: none answered on the Week page that week")
    n, changed = p.get("lineups", 0), p.get("overrides") or []
    if not n:
        out.append("  overrides: no lineups of yours in the standings")
    else:
        edited = sum(1 for o in changed if o["shared"] >= MATCH_MIN)
        out.append(
            f"  overrides: {len(changed)} of {n} lineups differ from their nearest build "
            f"({edited} edited builds, {len(changed) - edited} your own): "
            f"{p['gain']:+.1f} / {p['loss']:+.1f} points"
            + (f" ({p['no_build']} had no build of their kind)" if p.get("no_build") else "")
        )
        for o in changed[:3]:
            out.append(f"    {o['kind']}, {o['delta']:+.1f} against {o['build']}: {swap_text(o)}")
    if p.get("season_line"):
        out.append(f"  season: {p['season_line']}")
    return out


# -- the season file
# ------------------------------------------------------------------------------------------


def csv_row(season: int, c: Card) -> dict:
    return {
        "season": season,
        "week": c.week,
        "lineups": len(c.overrides),
        "overrides": len(c.changed),
        "gain": round(c.gain, 2),
        "loss": round(c.loss, 2),
        "net": round(c.gain + c.loss, 2),
        "decisions": len(c.decisions),
        "decisions_kept": sum(d["kept"] for d in c.decisions),
        "tier_over": ";".join(c.tier_over),
    }


def append_csv(path: Path, row: dict) -> None:
    """Replace this season-week's row (a rerun) and keep the rest, sorted."""
    rows = []
    if path.exists():
        with path.open(newline="") as f:
            rows = [
                r
                for r in csv.DictReader(f)
                if (str(r["season"]), str(r["week"])) != (str(row["season"]), str(row["week"]))
            ]
    rows.append({c: row.get(c, "") for c in CSV_COLUMNS})
    rows.sort(key=lambda r: (int(r["season"]), int(r["week"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def season_line(rows: list[dict], season: int) -> str:
    mine = [r for r in rows if str(r.get("season")) == str(season) and int(r.get("lineups") or 0) > 0]
    if not mine:
        return ""
    gain = sum(float(r["gain"] or 0) for r in mine)
    loss = sum(float(r["loss"] or 0) for r in mine)
    n = len(mine)
    weeks = f"{n} week{'s' if n != 1 else ''}"
    return f"your overrides against the model: {gain:+.1f} / {loss:+.1f} points over {weeks}"


def season_line_from_csv(path: Path, season: int) -> str:
    if not path.exists():
        return ""
    with path.open(newline="") as f:
        return season_line(list(csv.DictReader(f)), season)


# -- the four scorecards (blueprint Phase D; the Close view) ---------------------------------------------

PROVEN, NOT_PROVEN, OVERSTATED, NEW = "Proven", "Not proven", "Overstated", "New"
MIN_WEEKS = 3  # fewer weeks than this: New
BAR_SE = 2.0  # the registry's bar for one variant: above zero at 2 SE by week


def _mean_se(xs: list[float]) -> tuple[float | None, float | None]:
    if not xs:
        return None, None
    m = sum(xs) / len(xs)
    if len(xs) < 2:
        return m, None
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return m, (var / len(xs)) ** 0.5


def _status(gains: list[float]) -> str:
    """Proven when the mean weekly gain over the baseline is above zero by `BAR_SE` standard errors (weeks as
    the unit), New under `MIN_WEEKS` weeks, else Not proven."""
    if len(gains) < MIN_WEEKS:
        return NEW
    m, se = _mean_se(gains)
    return PROVEN if m is not None and se is not None and m > BAR_SE * se else NOT_PROVEN


def _gain_text(gains: list[float], unit: str, nd: int) -> str:
    m, se = _mean_se(gains)
    if m is None:
        return "no weeks yet"
    return f"{m:+.{nd}f}{unit} a week" + (f" (SE {se:.{nd}f})" if se is not None else "")


def projection_gains(by_week: list[dict], week: int) -> list[float]:
    """TFFB's MAE minus CalPts', per scored week up to `week` (positive: CalPts better)."""
    return [float(r["tffb"]) - float(r["calpts"]) for r in by_week if int(r["week"]) <= week]


OWN_BASELINE = {"gpp": "TFFB Own%", "cash": "Double-up curve"}


def ownership_gains(scores: list[dict], week: int) -> tuple[dict[str, list[float]], dict[str, tuple]]:
    """Per format: the baseline's MAE minus ours by week (positive: ours better), and this week's pair."""
    gains: dict[str, list[float]] = {}
    this: dict[str, tuple[float, float]] = {}
    for fmt, b in OWN_BASELINE.items():
        mae: dict[int, dict[str, float]] = {}
        for r in scores:
            if r["Format"] == fmt and int(r["Week"]) <= week and r["Model"] in (b, "Ours"):
                mae.setdefault(int(r["Week"]), {})[r["Model"]] = float(r["MAE"])
        weeks = sorted(w for w, m in mae.items() if len(m) == 2)
        gains[fmt] = [mae[w][b] - mae[w]["Ours"] for w in weeks]
        if week in weeks:
            this[fmt] = (mae[week]["Ours"], mae[week][b])
    return gains, this


def projection_card(by_week: list[dict], week: int) -> dict:
    """CalPts against TFFB, error by week (`numbers.projection_by_week`): the gain is TFFB's MAE minus
    ours."""
    gains = projection_gains(by_week, week)
    this = next((r for r in by_week if int(r["week"]) == week), None)
    better = sum(g > 0 for g in gains)
    return {
        "model": "Projection (CalPts vs TFFB)",
        "week": f"MAE {this['calpts']:.2f} vs {this['tffb']:.2f}" if this else "not scored yet",
        "season": _gain_text(gains, " pts", 2),
        "baseline": f"TFFB: CalPts better {better} of {len(gains)} weeks",
        "weeks": len(gains),
        "status": _status(gains),
    }


def ownership_card(scores: list[dict], week: int) -> dict:
    """Our ownership against its simple baseline (`field_own.score_weeks`): TFFB's Own% for GPP, the
    double-up curve for cash. The gain is the baseline's MAE minus ours, per week and format; the status is
    the weaker format's."""
    base = OWN_BASELINE
    gains, this = ownership_gains(scores, week)
    statuses = [_status(gains[f]) for f in base]
    status = NEW if NEW in statuses else (NOT_PROVEN if NOT_PROVEN in statuses else PROVEN)
    return {
        "model": "Ownership (GPP, cash)",
        "week": "; ".join(
            f"{f.upper() if f == 'gpp' else f} MAE {o:.3f} vs {b:.3f}" for f, (o, b) in this.items()
        )
        or "not scored yet",
        "season": "; ".join(f"{f.upper() if f == 'gpp' else f} {_gain_text(gains[f], '', 4)}" for f in base),
        "baseline": "GPP: TFFB's Own%; cash: the double-up curve",
        "weeks": min(len(g) for g in gains.values()),
        "status": status,
    }


def calibration_card(rows: list[dict], week: int) -> dict:
    """The cash chance shown against the cash rate (`data/contests/cash_shown.csv`): Overstated when the shown
    average is above the share that cashed by `BAR_SE` binomial standard errors, Proven when within them on
    30+ builds, else Not proven."""
    usable = [
        r
        for r in rows
        if r.get("shown") not in (None, "") and r.get("cashed") not in (None, "") and int(r["week"]) <= week
    ]
    shown = [float(r["shown"]) for r in usable]
    cashed = [str(r["cashed"]).lower() in ("true", "1") for r in usable]
    weeks = sorted({int(r["week"]) for r in usable})
    this = [i for i, r in enumerate(usable) if int(r["week"]) == week]
    if not usable:
        return {
            "model": "Cash % calibration",
            "week": "no cash builds scored",
            "season": "",
            "baseline": "",
            "weeks": 0,
            "status": NEW,
        }
    s, c = sum(shown) / len(shown), sum(cashed) / len(cashed)
    se = max((c * (1 - c) / len(cashed)) ** 0.5, 1e-9)
    gap = s - c
    if len(weeks) < MIN_WEEKS:
        status = NEW
    elif gap > BAR_SE * se:
        status = OVERSTATED
    elif abs(gap) <= BAR_SE * se and len(usable) >= 30:
        status = PROVEN
    else:
        status = NOT_PROVEN
    week_text = (
        f"shown {sum(shown[i] for i in this) / len(this) * 100:.0f}% vs cashed "
        f"{sum(cashed[i] for i in this) / len(this) * 100:.0f}%"
        if this
        else "no cash builds this week"
    )
    return {
        "model": "Cash % calibration",
        "week": week_text,
        "season": f"shown {s * 100:.0f}% vs cashed {c * 100:.0f}% (n={len(usable)})",
        "baseline": f"gap {gap * 100:+.1f} pts (SE {se * 100:.1f})",
        "weeks": len(weeks),
        "status": status,
        "gap": round(gap, 4),
    }


def edge_card(summary: dict | None) -> dict:
    """Edge at lock (`edge_at_lock.summary`). The status comes from the REALIZED margin by week (his cash
    lineups' actual points minus the field's mean actual points; Sam, 2026-11-17); the projected edge on the
    four-source consensus and its percentile are the card's text ("on paper")."""
    weeks = (summary or {}).get("weeks") or {}
    realized = [float(v["realized"]) for v in weeks.values() if v.get("realized") is not None]
    this = (summary or {}).get("this_week") or {}
    paper = (summary or {}).get("season_edge")
    pctl = (summary or {}).get("season_pct")
    if this.get("edge") is not None:
        week_text = f"actual {_signed_pts(this.get('realized'))}; on paper {this['edge']:+.1f} pts"
        if this.get("pct") is not None:
            week_text += f", {this['pct'] * 100:.0f}th percentile"
    else:
        week_text = "no standings this week"
    season_text = f"actual {_gain_text(realized, ' pts', 1)}"
    if paper is not None:
        season_text += f"; on paper: {paper:+.1f} pts" + (
            f", {pctl * 100:.0f}th percentile" if pctl is not None else ""
        )
    return {
        "model": "Edge at lock",
        "week": week_text,
        "season": season_text,
        "baseline": (
            "the double-up field's mean actual points (on paper: its mean on the four-source consensus)"
        ),
        "weeks": len(realized),
        "status": _status(realized),
    }


def _signed_pts(x: object) -> str:
    return "-" if x is None else f"{float(x):+.1f} pts"


def scorecards(
    *, week: int, projection: list[dict], ownership: list[dict], cash_rows: list[dict], edge: dict | None
) -> list[dict]:
    """The Close view's four rows: Projection, Ownership, Calibration, Edge at lock. Each is
    `{model, week, season, baseline, weeks, status}`, status one of Proven, Not proven, Overstated, New.
    Pure."""
    return [
        projection_card(projection, week),
        ownership_card(ownership, week),
        calibration_card(cash_rows, week),
        edge_card(edge),
    ]
