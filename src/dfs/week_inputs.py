"""What the Week page reads: every output the tool has already saved, gathered into one `WeekInputs`.

Nothing here computes a number the tool does not already compute, and nothing writes a sheet or opens the
network. A loader returns None when its file is missing or unreadable, and the page says "not run yet" for
that section; it never invents a value.

Where a command's result lived only on the terminal or in the sheet (the Stacks tab's top rows, the Season
sheet's per-league view), the command now also leaves a small JSON beside the page, under
`data/week/inputs/` (`save_input`, fail-soft, called from the command that already computed the result). Two
inputs belong to Plan D (money) and are read the same way once its commands write them: `contest_plan`
(`dfs contests plan`) and `postmortem` (`week close`'s cash post-mortem). Their expected shape is the
docstring of `CONTEST_PLAN_SHAPE` / `POSTMORTEM_SHAPE`; until the file exists the sections say "not run yet".

Paths follow `paths.DATA_DIR` at call time, so tests/conftest.py's `_hermetic_data_dir` (which points it at
the test's tmp dir) is all that keeps a test off the real `data/`.
"""

from __future__ import annotations

import csv
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dfs import paths, runlog

INPUTS_SUBDIR = Path("week") / "inputs"


def data_root() -> Path:
    return paths.DATA_DIR


CONTEST_PLAN_SHAPE = """`dfs contests plan` leaves (via `week_inputs.save_input("contest_plan", {...})`):
{"caps": {"Showdown": 5, "N-max GPP (2,000+ entrants)": 10, ...},          # dollars a week, by format
 "spent": {"Showdown": 7.0, ...},                                           # last week's spend, same keys
 "budget": 150.0, "total_spent": 179.0,                                     # weekly total vs Bankroll budget
 "over": ["Showdown ($7 vs $5)"],                                           # the "over in:" list
 "roi": [{"format": "Double Up", "entries": 152, "roi": 0.091, "rake": 0.13}, ...],
 "h2h_avoid": [{"opponent": "Jpbielke", "record": "0-6"}, ...],
 "line": "spent $179 vs plan $150; over in: Showdown"}"""

POSTMORTEM_SHAPE = """`week close` leaves (via `week_inputs.save_input("postmortem", {...})`):
{"headline": "5 of our 10 cash builds would have cashed (line 123.3, best 139.5)",
 "lines": ["Ja'Marr Chase: in 7 of 10 cash builds, missing from your entry", ...]}"""


# -- sidecars ---------------------------------------------------------------------------------------


def inputs_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or data_root()) / INPUTS_SUBDIR


def input_path(name: str, season: int | None, week: int | None, data_dir: Path | None = None) -> Path:
    stem = name if season is None or week is None else f"{season}-wk{week:02d}-{name}"
    return inputs_dir(data_dir) / f"{stem}.json"


def save_input(
    name: str,
    payload: dict,
    *,
    season: int | None = None,
    week: int | None = None,
    data_dir: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Write one input for the page (adds `saved_at`). Never raises; False when it could not be written."""
    try:
        path = input_path(name, season, week, data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            **payload,
            "saved_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(body, indent=1, default=str))
        return True
    except Exception:  # noqa: BLE001 - a command never fails over the page
        return False


def load_input(name: str, season: int | None, week: int | None, data_dir: Path | None = None) -> dict | None:
    return read_json(input_path(name, season, week, data_dir))


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# -- what commands leave ----------------------------------------------------------------------------


def stacks_summary(layout: Any, top: int = 3) -> dict | None:
    """The first `top` rows of the Stacks tab's table, from the layout `stacks_tab.build_layout` returns."""
    try:
        if not layout.header_rows:
            return None
        rows = []
        for row in layout.rows[layout.header_rows[0] :]:  # header_rows is 1-based: this is the row after it
            if len(row) < 13 or not isinstance(row[4], int | float):
                break
            rows.append(
                {
                    "cue": row[0],
                    "qb": row[1],
                    "mates": row[2],
                    "game": row[3],
                    "salary": row[4],
                    "top1": row[6],
                    "lift": row[7],
                    "lev": row[10] if isinstance(row[10], int | float) else None,
                    "rule": row[12],
                }
            )
            if len(rows) >= top:
                break
        return {"rows": rows, "sorted_by": getattr(layout, "sort_label", "")} if rows else None
    except Exception:  # noqa: BLE001
        return None


def save_stacks(layout: Any, *, now: datetime | None = None) -> None:
    """Called by `dfs stacks` and the Stacks tab writer: the top rows, for the Week page."""
    from dfs import nfl_calendar

    summary = stacks_summary(layout)
    if summary:
        save_input(
            "stacks", summary, season=nfl_calendar.current_season(), week=nfl_calendar.current_week(), now=now
        )


def season_run_summary(run: Any, headline: list[str]) -> dict:
    """One league's run (`season_long.season_tabs.LeagueRun`) as plain text for the page. `headline` is the
    lines `dfs season sync` printed for it. Duck-typed, lazy imports: the season package stays optional."""
    from dfs.season_long import finder as fd
    from dfs.season_long import waivers as wv

    view = run.view
    return {
        "league": view.league.name,
        "headline": headline[:3],
        "alerts": [alert_line(a) for a in (run.alerts or [])],
        "pickups": [wv.pickup_line(view, p) for p in (run.pickups or [])[:3]],
        "ideas": [fd.idea_line(view, i) for i in (run.ideas or [])[:3]],
    }


def alert_line(a: Any) -> str:
    swap = f" -> {a.swap}" if getattr(a, "swap", "") else ""
    return f"{a.cue} {a.slot} {a.player}: {a.reason}{swap}"


def save_season_league(summary: dict, *, data_dir: Path | None = None, now: datetime | None = None) -> None:
    """Merge one league's summary into `season_long.json` (a league keeps its own `saved_at`)."""
    try:
        path = input_path("season_long", None, None, data_dir)
        current = read_json(path) or {}
        leagues = dict(current.get("leagues") or {})
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        leagues[summary["league"]] = {**summary, "saved_at": stamp}
        save_input("season_long", {"leagues": leagues}, data_dir=data_dir, now=now)
    except Exception:  # noqa: BLE001
        return


def save_season_alerts(
    league: str, alerts: list[str], *, data_dir: Path | None = None, now: datetime | None = None
) -> None:
    """`dfs season check`: the league's alerts as of now (the rest of its summary is kept)."""
    try:
        path = input_path("season_long", None, None, data_dir)
        leagues = dict((read_json(path) or {}).get("leagues") or {})
        entry = dict(leagues.get(league) or {"league": league})
        entry["alerts"] = list(alerts)
        entry["checked_at"] = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        leagues[league] = entry
        save_input("season_long", {"leagues": leagues}, data_dir=data_dir, now=now)
    except Exception:  # noqa: BLE001
        return


# -- the loaders ------------------------------------------------------------------------------------


def money(text: Any) -> float:
    try:
        return float(str(text).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _int(text: Any) -> int | None:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _read_csv(path: Path) -> list[dict]:
    try:
        with path.open(newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


@dataclass
class LastWeek:
    week: int
    formats: list[dict]  # {"format", "entries", "fees", "net", "roi"}, biggest fees first
    fees: float
    net: float
    cash_entries: list[dict]  # {"series", "points", "line", "margin", "cashed", "copies"}


def last_week(data_dir: Path, season: int) -> LastWeek | None:
    """The newest week of `season` in the contest log (NFL, paid entries), by format, and the cash entries'
    margin to the line from `decisions.csv`. Net = cash winnings minus the entry fee."""
    log = _read_csv(data_dir / "contests" / "contest_log.csv")
    rows = [
        r
        for r in log
        if _int(r.get("Season")) == season
        and r.get("Sport", "NFL") == "NFL"
        and money(r.get("Entry_Fee")) > 0
    ]
    weeks = [w for r in rows if (w := _int(r.get("Week"))) is not None]
    if not weeks:
        return None
    week = max(weeks)
    by_format: dict[str, dict] = {}
    for r in rows:
        if _int(r.get("Week")) != week:
            continue
        f = by_format.setdefault(
            r.get("Format") or "Other",
            {"format": r.get("Format") or "Other", "entries": 0, "fees": 0.0, "net": 0.0},
        )
        fee = money(r["Entry_Fee"])
        f["entries"] += 1
        f["fees"] += fee
        f["net"] += money(r.get("Winnings_Non_Ticket")) - fee
    formats = sorted(by_format.values(), key=lambda f: -f["fees"])
    for f in formats:
        f["roi"] = f["net"] / f["fees"] if f["fees"] else 0.0
    fees = sum(f["fees"] for f in formats)
    net = sum(f["net"] for f in formats)
    cash = []
    for r in _read_csv(data_dir / "contests" / "decisions.csv"):
        if _int(r.get("season")) == season and _int(r.get("week")) == week and r.get("cls") == "cash":
            cash.append(
                {
                    "series": r.get("series", ""),
                    "points": _num(r.get("points")),
                    "line": _num(r.get("cash_line")),
                    "margin": _num(r.get("margin")),
                    "cashed": str(r.get("cashed")).lower() == "true",
                    "copies": _int(r.get("copies")),
                }
            )
    return LastWeek(week, formats, fees, net, cash)


def _num(text: Any) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return None if value != value else value


def read_config(repo_root: Path | None = None) -> dict:
    """`config.toml` as a plain dict ({} when it is missing or unreadable): the page reads two switches from
    it and must work on a machine without credentials."""
    try:
        return tomllib.loads(((repo_root or paths.REPO_ROOT) / "config.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def latest_round(data_dir: Path, week: int) -> dict | None:
    state = read_json(data_dir / "suggest_state" / f"{week}.json")
    rounds = (state or {}).get("rounds") or []
    return rounds[-1] if rounds else None


EDGE_COLUMNS_USED = [
    "Id",
    "Name",
    "Position",
    "Team",
    "Opp",
    "Salary",
    "ProjPts",
    "CalPts",
    "Floor",
    "Ceiling",
    "CeilM",
    "Hit3x%",
    "Boom%",
    "Bust%",
    "Avail",
    "Edge",
    "Kickoff",
    "GameStart",
    "GameID",
    "OverUnder",
    "Spread",
    "TotMove",
    "SpdMove",
    "Roof",
    "Wind",
    "WX",
    "OppPosRank",
    "Snap% L5",
    "Tgt L5",
    "Car L5",
    "DK L5",
    "xFP/G",
    "CashOwn",
    "GppOwn",
]


def load_edge(data_dir: Path) -> list[dict] | None:
    """The synced slate's rows (only the columns the page uses), or None when nothing is synced."""
    rows = _read_csv(data_dir / "current" / "edge.csv")
    if not rows:
        return None
    return [{k: r.get(k, "") for k in EDGE_COLUMNS_USED} for r in rows]


SNAPSHOT_STAMP = "%Y%m%dT%H%M%SZ"  # store._SNAPSHOT_STAMP_FORMAT: a raw snapshot's file name


def load_prev_edge(data_dir: Path, current: list[dict] | None) -> tuple[list[dict], datetime] | None:
    """The slate as the sync before the last one saw it (`store.load_previous`'s rule: the second-newest raw
    snapshot) and when that sync ran, or None when there is none or it is another week's slate (the first sync
    of a new week must not diff against last week's games)."""
    raw = data_dir / "raw" / "edge"
    files = sorted(raw.glob("*.csv")) if raw.exists() else []
    if len(files) < 2:
        return None
    path = files[-2]
    try:
        at = datetime.strptime(path.stem, SNAPSHOT_STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None
    rows = [{k: r.get(k, "") for k in EDGE_COLUMNS_USED} for r in _read_csv(path)]
    if not rows:
        return None
    now_games = {r["GameID"] for r in current or [] if r.get("GameID")}
    then_games = {r["GameID"] for r in rows if r.get("GameID")}
    if now_games and (not then_games or len(now_games & then_games) / len(now_games) < 0.5):
        return None
    return rows, at


def load_signal_table(data_dir: Path, season: int) -> tuple[dict[str, dict], tuple[int, int] | None]:
    """Model Check's per-signal record (`results/signal_table_<season>.csv`, written by
    `dfs results update`) by signal group, and the first and last scored week
    (`results/scored_<season>_wNN.csv`). Empty when there is none."""
    results = data_dir / "results"
    table: dict[str, dict] = {}
    for r in _read_csv(results / f"signal_table_{season}.csv"):
        table[r.get("Signal", "")] = {
            "n": _int(r.get("n")) or 0,
            "VsProj": _num(r.get("VsProj")),
            "VsCal": _num(r.get("VsCal")),
            "HitProj": _num(r.get("HitProj")),
            "HitCal": _num(r.get("HitCal")),
            "Thin": str(r.get("Thin")).lower() == "true",
        }
    weeks = sorted(
        int(m.group(1))
        for f in results.glob(f"scored_{season}_w*.csv")
        if (m := re.fullmatch(rf"scored_{season}_w(\d+)\.csv", f.name))
    )
    return table, ((weeks[0], weeks[-1]) if weeks else None)


def load_pool(data_dir: Path, season: int, week: int) -> dict[str, str]:
    snaps = read_json(data_dir / "pools" / f"{season}-wk{week:02d}.json") or []
    return dict(snaps[-1].get("pool") or {}) if snaps else {}


@dataclass
class WeekInputs:
    season: int
    week: int
    now: datetime
    data_dir: Path
    manifest: dict = field(default_factory=dict)
    edge: list[dict] | None = None
    prev_edge: list[dict] | None = None  # the slate as the sync before the last one saw it
    prev_edge_at: datetime | None = None
    signal_table: dict[str, dict] | None = None  # Model Check's record by signal
    scored_weeks: tuple[int, int] | None = None  # the first and last week it was scored over
    pool: dict[str, str] = field(default_factory=dict)
    suggest: dict | None = None  # the latest round of data/suggest_state/<week>.json
    stacks: dict | None = None
    stacks_retro: dict | None = None
    late_swap: dict | None = None
    du_line: dict | None = None
    contest_plan: dict | None = None
    postmortem: dict | None = None
    tier_week: dict | None = None  # `week close`'s tier plan line for the week it closed
    budget_input: dict | None = None  # the Weekly Budget typed on the live page (`{"budget": 150}`)
    entered: dict | None = None
    last_week: LastWeek | None = None
    season_long: dict | None = None
    config: dict = field(default_factory=dict)
    runs: list[runlog.Run] = field(default_factory=list)


def gather(season: int, week: int, now: datetime, data_dir: Path | None = None) -> WeekInputs:
    """Read every saved output for the week. Pure reading: a missing file is a None, never an error."""
    d = data_dir or data_root()
    edge = load_edge(d)
    prev = load_prev_edge(d, edge)
    signal_table, scored_weeks = load_signal_table(d, season)
    return WeekInputs(
        season=season,
        week=week,
        now=now,
        data_dir=d,
        manifest=read_json(d / "manifest.json") or {},
        edge=edge,
        prev_edge=prev[0] if prev else None,
        prev_edge_at=prev[1] if prev else None,
        signal_table=signal_table,
        scored_weeks=scored_weeks,
        pool=load_pool(d, season, week),
        suggest=latest_round(d, week),
        stacks=load_input("stacks", season, week, d),
        stacks_retro=read_json(d / "model_cache" / "stacks_retro.json"),
        late_swap=read_json(d / "cockpit" / "late_swap.json"),
        du_line=read_json(d / "model_cache" / "du_line.json"),
        contest_plan=load_input("contest_plan", season, week, d),
        # `week close` writes the closing week's post-mortem: Monday it is this week's, Tuesday on last week's
        postmortem=load_input("postmortem", season, week, d) or load_input("postmortem", season, week - 1, d),
        tier_week=load_input("tier_week", season, week, d) or load_input("tier_week", season, week - 1, d),
        budget_input=load_input("budget", season, week, d),
        entered=read_json(d / "entered" / f"{season}-wk{week:02d}.json"),
        last_week=last_week(d, season),
        season_long=load_input("season_long", None, None, d),
        config=read_config(),
        runs=runlog.read(d / "runlog.csv"),
    )
