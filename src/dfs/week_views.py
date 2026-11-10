"""Plan O2 on the Week page: the view bar and the four new views, plus search and compare.

- **The view bar** (`VIEWS`, Sam 2026-11-09): Overview (the default) | Board & Explorer | Usage | Injuries |
  Model vs sources | Everything (the full page: every block). The progress strip and the brief sit above it
  on every view; each block of the page is wrapped in `wrap(html, views)` naming the views it shows on
  (`PLACES`). Each view has its own `#name` (a link or the back button opens it), the last one is kept per
  week in the browser (`dfs-view-<season>-<week>`), and the pool store is one for the whole page, so a pool
  click on one view is on every view.
- **Overview** (`overview_section`, item 10): one row per position (the top value, the cash core and chalk,
  the best GPP leverage, the fades, the top injury fallout), the slate's top 3 games and the pool by
  position. Every name is a `week_page.pn` link: the pool chip and the drawer come with it.
- **Usage** (`usage_section`, item 6): `data/current/usage_weeks.csv` / `team_weeks.csv`
  (`sources/usage_weeks.py`, saved by a full `dfs sync`): per player snap share, targets, target share, air
  yards share, WOPR, red-zone looks and high-value touches, carries, each with a trend line over his last
  five games against his season mean, two scatters per position (targets against snaps, opportunities
  against salary) with the outliers labelled; per team plays, dropback rate, PROE, pace, red-zone trips,
  and the split of targets and carries among the skill players. Drawn by the client (`JS`) from the
  `usage-data` JSON, because the trend metric, the window and the position are the viewer's choice.
- **Injuries** (`injury_section`, item 7): one card per slate team, every player DraftKings or the
  injury report lists (OUT / D / Q / IR), the practice trend (`practice.py`) and who benefits (the plays
  model's teammate bump, `plays.py`: the calls input's `bump` / `bump_from`).
- **Model vs sources** (`model_section`, item 9, M2's first half): CalPts against TFFB, Sleeper,
  FantasyPros, ESPN and props for every player, the biggest disagreements by position (higher / lower than
  the sources' median), and the plays calls (Shadow) leading with AVOID and FADE, each label with its
  record: the M1 backtest (`docs/research/plays/*-plays-summary.json`, Weeks 2 and 4-8, walk-forward) and
  the live record (`data/research/calls_history.csv`, from the Week 9 close on), never mixed.
- **Search and compare** (item 8, `JS`): a search box over every priced player on every view, and a
  compare tray of 2-4 players (added from the search, the drawer, or any name's pool chip) side by side:
  the Compare tab's rows (`compare_card.SECTIONS`, the same directions and the same "best is green" rule),
  every source's projection, the usage trends and ownership.

Pure: every function reads `WeekInputs` and returns HTML / JSON; nothing writes anything.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

from dfs import compare_card as cc
from dfs import player_card as pc
from dfs import week_board as wb
from dfs import week_plays
from dfs.week_page import TRUST, caption, chip_html, e, how_html, num, pn, rich

POSITIONS = week_plays.POSITIONS
SKILL = ("QB", "RB", "WR", "TE")

VIEWS = (
    ("overview", "Overview"),
    ("explore", "Board & Explorer"),
    ("usage", "Usage"),
    ("injuries", "Injuries"),
    ("model", "Model vs sources"),
    ("everything", "Everything"),
)
DEFAULT_VIEW = "overview"
VIEW_NAMES = tuple(k for k, _ in VIEWS)
# Which views each block of the page shows on (the progress strip and the brief show on all of them).
PLACES = {
    "pool": "explore everything",
    "decisions": "explore everything",
    "board": "explore everything",
    "charts": "explore everything",
    "games": "explore everything",
    "explorer": "explore everything",
    "overview": "overview",
    "usage": "usage everything",
    "injuries": "injuries everything",
    "model": "model everything",
    # the rest of the week, the run panel and the tools: under the overview ("the full sections sit below")
    "rest": "overview everything",
}


def wrap(html: str, place: str) -> str:
    """`html` shown only on the views `PLACES[place]` names (every view without the page's script)."""
    return f'<div class="vw" data-v="{e(PLACES[place])}">{html}</div>'


def view_bar() -> str:
    links = "".join(
        f'<a href="#{k}" data-view="{k}"{" class=on" if k == DEFAULT_VIEW else ""}>{e(label)}</a>'
        for k, label in VIEWS
    )
    return (
        f'<nav id="views" aria-label="Views">{links}'
        '<span class="vsearch"><input id="psearch" type="search" placeholder="Find a player" '
        'autocomplete="off" aria-label="Find a player"><span id="psearch-list" class="srlist" hidden>'
        "</span></span></nav>"
    )


def _pid(row: dict) -> str:
    return wb.pid(row)


def _name(row: dict) -> str:
    return pn(row.get("Name", ""), row.get("Team", ""), _pid(row))


def _money(value: object) -> str:
    x = num(value)
    return "" if x is None else f"${x:,.0f}"


def _p(value: object, digits: int = 0) -> str:
    x = num(value)
    return "" if x is None else f"{x * 100:.{digits}f}%"


def _f(value: object, digits: int = 1) -> str:
    x = num(value)
    return "" if x is None else f"{x:.{digits}f}"


# -- the plays calls (the latest version of this week's calls input) ----------------------------------------


def calls_of(inp) -> list[dict]:
    """The newest timestamped version of the week's calls (`data/week/inputs/<season>-wk<NN>-calls.json`)."""
    versions = (getattr(inp, "calls", None) or {}).get("versions") or []
    return list((versions[-1] if versions else {}).get("calls") or [])


def held(c: dict, kind: str) -> bool:
    """M1.6's hold-back (`plays.held_back`): until the new labels clear the bar, no cash AVOID on a 30%+
    CashOwn player and no label whose own reason contradicts it is shown; the page says
    `plays.HOLD_TEXT` instead."""
    from dfs import plays

    return plays.held_back(c, kind)


def calls_at(inp) -> str:
    """When the newest version was saved, in the page's ET format (the raw text when it is not a time)."""
    from datetime import datetime

    from dfs import kickoff

    versions = (getattr(inp, "calls", None) or {}).get("versions") or []
    at = str((versions[-1] if versions else {}).get("at") or "")
    try:
        return kickoff.format_et(datetime.fromisoformat(at))
    except ValueError:
        return at


@dataclass
class Bump:
    name: str
    team: str
    position: str
    bump: float
    source: str  # the teammate whose news moved him


def bumps(inp) -> list[Bump]:
    out = []
    for c in calls_of(inp):
        b = num(c.get("bump"))
        if b and b > 0 and c.get("bump_from"):
            out.append(Bump(c["name"], c.get("team", ""), c.get("position", ""), b, str(c["bump_from"])))
    out.sort(key=lambda b: -b.bump)
    return out


# -- overview ------------------------------------------------------------------------------------------------

OVERVIEW_HOW = (
    "One row per position from the board (Value = CalPts per $1K among the cheap and mid-priced; Cash core = "
    "the best Cash % for the price; Chalk = 30%+ CashOwn; Best GPP leverage = the board's Upside: the top half of "
    "Boom% ranked by Boom% rank minus GppOwn rank), the fades (FADE↓, with its Model Check record; the plays model's AVOID / FADE are "
    "Shadow), and the top injury fallout (the plays model's teammate bump for a teammate ruled OUT or "
    "Doubtful)."
)


def _role_rows(rows: list[wb.BoardRow], *roles: str) -> list[wb.BoardRow]:
    return [r for r in rows if any(x in r.roles for x in roles)]


def _cell(items: list[str], empty: str = "") -> str:
    if not items:
        return f'<span class="dim">{e(empty)}</span>' if empty else ""
    return "<br>".join(items)


def overview_rows(inp, board: wb.Board) -> dict[str, dict[str, list[str]]]:
    """Position -> column -> the HTML lines of that cell."""
    edge_by = {(r.get("Name"), r.get("Team")): r for r in inp.edge or []}
    calls = calls_of(inp)
    fallout = bumps(inp)
    out: dict[str, dict[str, list[str]]] = {}
    for pos in POSITIONS:
        rows = board.by_pos.get(pos, [])
        value = sorted(_role_rows(rows, wb.VALUE), key=lambda r: -(wb.per_k(r.row) or 0))[:2]
        cash = sorted(_role_rows(rows, wb.CASH, wb.CHALK_CORE), key=lambda r: -r.calpts)[:3]
        lev = _role_rows(rows, wb.UPSIDE)[:2]
        fades = [r for r in rows if r.fade][:2]
        avoid = [c for c in calls if c.get("position") == pos and c.get("cash") == "AVOID"]
        avoid.sort(key=lambda c: (held(c, "cash"), num(c.get("cash_score")) or 0))
        gfade = [c for c in calls if c.get("position") == pos and c.get("gpp") == "FADE"]
        gfade.sort(key=lambda c: (held(c, "gpp"), num(c.get("gpp_score")) or 0))
        cell: dict[str, list[str]] = {
            "value": [
                f"{_name(r.row)} {_money(r.row.get('Salary'))} · {_f(wb.per_k(r.row), 2)}/1K" for r in value
            ],
            "cash": [
                f"{_name(r.row)} {_f(r.row.get('CalPts'))}"
                + (
                    f' <span class="chip chalk">chalk {_p(r.row.get("CashOwn"))}</span>'
                    if wb.CHALK_CORE in r.roles
                    else ""
                )
                for r in cash
            ],
            "gpp": [
                f"{_name(r.row)} <span class='dim'>Boom {_f(r.row.get('Boom%'), 0)}% · "
                f"{_p(r.row.get('GppOwn'))} owned</span>"
                for r in lev
            ],
            "fades": fade_lines(fades, avoid[:2], gfade[:1]),
            "injury": [
                f"{pn(b.name, b.team)} +{b.bump:.1f} <span class='dim'>({e(b.source)} out)</span>"
                for b in [b for b in fallout if b.position == pos][:2]
            ],
        }
        if not cell["injury"]:
            inj = [
                r for r in edge_by.values() if r.get("Position") == pos and week_plays.has_token(r, "INJ+")
            ]
            inj.sort(key=lambda r: -(num(r.get("CalPts")) or 0))
            cell["injury"] = [f'{_name(r)} <span class="chip shadow">INJ+</span>' for r in inj[:2]]
        out[pos] = cell
    return out


def fade_lines(fades: list[wb.BoardRow], avoid: list[dict], gfade: list[dict]) -> list[str]:
    """One line per player: FADE↓ (the signal, with its record) and the plays model's AVOID cash / FADE GPP
    (Shadow) as chips on the same name."""
    lines: dict[tuple[str, str], list[str]] = {}
    names: dict[tuple[str, str], str] = {}
    for r in fades:
        key = (r.name, str(r.row.get("Team", "")))
        names[key] = _name(r.row)
        lines.setdefault(key, []).append('<span class="chip fade">FADE↓</span>')
    from dfs import plays

    for calls, tag, kind in ((avoid, "AVOID cash", "cash"), (gfade, "FADE GPP", "gpp")):
        for c in calls:
            key = (c["name"], str(c.get("team", "")))
            names.setdefault(key, pn(c["name"], c.get("team", "")))
            if held(c, kind):
                lines.setdefault(key, []).append(
                    f'<span class="chip held" title="The plays model (Shadow): this {e(tag)} label is held '
                    f'back until the M1.6 labels clear the bar">{kind}: {e(plays.HOLD_TEXT)}</span>'
                )
                continue
            lines.setdefault(key, []).append(
                f'<span class="chip shadow" title="The plays model (Shadow)">{tag}</span>'
            )
    return [f"{names[k]} {' '.join(v)}" for k, v in lines.items()]


def overview_section(inp, board: wb.Board, panel: wb.PoolPanel) -> str:
    if board.empty:
        return (
            '<section class="card" id="overview-card"><div class="card-head"><h2>Overview</h2></div>'
            '<p class="dim">No slate yet: run <code>dfs sync</code>.</p></section>'
        )
    cells = overview_rows(inp, board)
    head = "".join(
        f"<th>{h}</th>"
        for h in ("Pos", "Top value", "Cash core and chalk", "Best GPP leverage", "Fades", "Injury fallout")
    )
    body = "".join(
        f'<tr data-pos="{pos}"><th>{pos}</th>'
        + "".join(
            f"<td>{_cell(cells[pos][k], '-')}</td>" for k in ("value", "cash", "gpp", "fades", "injury")
        )
        + "</tr>"
        for pos in POSITIONS
    )
    games = [g for g in week_plays.slate(inp.edge) if g.top][:3]
    game_items = []
    for g in games:
        imp = " · ".join(f"{t} {v:.1f}" for t, v in sorted(g.implied.items(), key=lambda kv: -kv[1]))
        spread = f"{g.favorite} {g.spread:+.1f}" if g.spread is not None else ""
        hurt = ", ".join(f"{pn(n, t)} {e(s)}" for n, _p2, t, s in g.injuries[:3])
        game_items.append(
            f'<li><a class="tm" href="#" data-team="{e(g.away)}" data-game="{e(g.key)}">{e(g.away)} @ '
            f"{e(g.home)}</a> <b>{_f(g.total)}</b> {e(spread)} <span class='dim'>({e(imp)}; {e(g.kickoff)}"
            f"{'; ' + e(g.weather) if g.weather else ''})</span>"
            + (f"<br><span class='dim'>Injuries:</span> {hurt}" if hurt else "")
            + "</li>"
        )
    counts = panel.counts
    pool_rows = "".join(
        f"<tr><th>{k}</th>" + "".join(f"<td>{counts[k][p]}</td>" for p in POSITIONS) + "</tr>"
        for k in ("Cash", "GPP")
    )
    pool_head = "<tr><th></th>" + "".join(f"<th>{p}</th>" for p in POSITIONS) + "</tr>"
    gaps = f'<p class="note warn">{e("; ".join(panel.gaps))}</p>' if panel.gaps else ""
    return (
        '<section class="card" id="overview-card"><div class="card-head"><h2>Overview</h2>'
        f"{how_html(OVERVIEW_HOW)}</div>"
        f'<div class="scroll"><table class="ov stickyname" data-shade="off"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>{caption(OVERVIEW_HOW, ('board', 'calls'))}"
        '<div class="ov-row"><div><h3>Top 3 games</h3>'
        + (
            f'<ul class="ov-games">{"".join(game_items)}</ul>'
            if game_items
            else '<p class="dim">No odds yet.</p>'
        )
        + f'</div><div><h3>Your pool: {panel.total} players</h3><table class="ov-pool">{pool_head}{pool_rows}'
        f"</table>{gaps}</div></div></section>"
    )


# -- usage ----------------------------------------------------------------------------------------------------

USAGE_HOW = (
    "Every game he played this season (nflverse's box score, play-by-play and snap counts; a missed game is "
    "skipped, never a zero). Snap % = offensive snaps; Tgt % = his targets / the team's; Air % = his "
    "receiving air yards / the team's passing air yards; WOPR = 1.5 x Tgt % + 0.7 x Air %; RZ = targets + "
    "carries from inside the 20, HVT = from inside the 10; per game over the window chosen. The trend line "
    "is his last five games of the metric chosen, the dashed line his season mean."
)
TEAM_HOW = (
    "Per team-game from nflverse's play-by-play: plays = pass and run plays (no two-point tries); Pass % = "
    "dropbacks (sacks and scrambles count) / plays; PROE = pass rate over expected, neutral script (win "
    "probability 20-80%, quarters 1-3, not the last two minutes of a half); pace = seconds per snap, neutral "
    "script (lower is faster); RZ trips = drives with a snap inside the opponent's 20. The bars split the "
    "team's targets and carries over the window among its players."
)
# The order of each game's numbers in the usage JSON (compact arrays, not objects).
WEEK_FIELDS = ("Wk", "Opp", "Snap%", "Tgt", "Car", "Tgt%", "Air%", "WOPR", "RZ", "HVT", "DK")
TEAM_FIELDS = ("Wk", "Opp", "Plays", "Pass%", "PROE", "Pace", "RZTrips", "Tgt", "Car")


def _cellv(value: object) -> object:
    x = num(value)
    if x is not None:
        return round(x, 4)
    return value if value not in (None, "") else None


def usage_data(inp) -> dict:
    """The usage view's JSON: every slate player's games (by EdgeRaw Id) and every slate team's."""
    players = getattr(inp, "usage_weeks", None) or []
    teams = getattr(inp, "team_weeks", None) or []
    edge = [r for r in inp.edge or [] if r.get("Position") in SKILL and _pid(r)]
    by_key: dict[tuple[str, str], list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for g in players:
        by_key.setdefault((str(g.get("NameKey", "")), str(g.get("Team", ""))), []).append(g)
        by_name.setdefault(str(g.get("NameKey", "")), []).append(g)
    out_p = {}
    for r in edge:
        key = str(r.get("NameKey") or "")
        games = by_key.get((key, str(r.get("Team", ""))))
        if not games:  # traded: his name at his position, when only one player has it
            cands = [g for g in by_name.get(key, []) if g.get("Position") == r.get("Position")]
            if len({g.get("Team") for g in cands}) and len({g.get("Name") for g in cands}) == 1:
                games = cands
        if not games:
            continue
        games = sorted(games, key=lambda g: num(g.get("Wk")) or 0)
        out_p[_pid(r)] = [[_cellv(g.get(f)) for f in WEEK_FIELDS] for g in games]
    slate_teams = sorted({str(r.get("Team")) for r in inp.edge or [] if r.get("Team")})
    out_t: dict[str, list] = {}
    for t in teams:
        if str(t.get("Team")) in slate_teams:
            out_t.setdefault(str(t["Team"]), []).append([_cellv(t.get(f)) for f in TEAM_FIELDS])
    for v in out_t.values():
        v.sort(key=lambda g: g[0] or 0)
    return {
        "fields": list(WEEK_FIELDS),
        "team_fields": list(TEAM_FIELDS),
        "players": out_p,
        "teams": out_t,
        "positions": list(SKILL),
    }


def usage_section(inp) -> str:
    data = usage_data(inp)
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    if not data["players"]:
        body = (
            '<p class="dim">Not run yet: the usage view reads <code>data/current/usage_weeks.csv</code> and '
            "<code>team_weeks.csv</code>, which a full <code>dfs sync</code> saves (not <code>--live</code>). "
            'Say "sync".</p>'
        )
    else:
        body = (
            '<div class="uv-bar"><span class="seg" id="uv-pos"></span>'
            '<label>Window <select id="uv-win"><option value="season">Season</option>'
            '<option value="5">Last 5</option><option value="3">Last 3</option></select></label>'
            '<label>Trend <select id="uv-metric"></select></label>'
            '<label>Show <select id="uv-show"><option>15</option><option>30</option>'
            '<option value="999">all</option></select></label></div>'
            f'<div class="tbl">{how_html(USAGE_HOW)}<div class="scroll" id="uv-table"></div>'
            f"{caption(USAGE_HOW, ('usage_weeks.csv', 'EdgeRaw Salary'))}</div>"
            '<div class="charts-row" id="uv-charts"></div>'
            f'<h3>Teams</h3><div class="tbl">{how_html(TEAM_HOW)}<div class="scroll" id="uv-teams"></div>'
            f"{caption(TEAM_HOW, ('team_weeks.csv', 'usage_weeks.csv'))}</div>"
        )
    return (
        '<section class="card" id="usage-card"><div class="card-head"><h2>Usage</h2>'
        f"{chip_html('Data', 'Box scores and play-by-play: what happened, not a prediction.')}</div>{body}"
        f'<script type="application/json" id="usage-data">{blob}</script></section>'
    )


# -- injuries -------------------------------------------------------------------------------------------------

INJURY_HOW = (
    "Every player DraftKings marks OUT / D / Q / IR on the slate, and every other player on the injury report "
    "(nflverse) with a game-day designation. Practice = nflverse's practice status at each sync this week, "
    "by the day we saw it. Benefits = the plays model's teammate bump (Shadow): his work to his teammates, "
    "the measured bump minus what the projections already moved."
)
STATUS_RANK = {"OUT": 0, "IR": 0, "D": 1, "Q": 2}
REPORT_SHORT = {"Out": "OUT", "Doubtful": "D", "Questionable": "Q"}


@dataclass
class Listed:
    name: str
    pos: str
    status: str
    row: dict | None = None  # his EdgeRaw row, when he is priced
    practice: str = ""
    injury: str = ""
    benefits: list[Bump] = field(default_factory=list)


def injury_teams(inp) -> dict[str, list[Listed]]:
    from dfs.week_inputs import _read_csv

    edge = inp.edge or []
    report = _read_csv(inp.data_dir / "current" / "nflverse_injuries.csv")
    week_rows = [r for r in report if str(r.get("week")) == str(inp.week)] or report
    inj = {(str(r.get("Name")), str(r.get("Team"))): r for r in week_rows}
    by_from: dict[str, list[Bump]] = {}
    for b in bumps(inp):
        by_from.setdefault(b.source, []).append(b)
    teams: dict[str, list[Listed]] = {}
    seen: set[tuple[str, str]] = set()
    for r in edge:
        st = week_plays.avail(r)
        if st not in STATUS_RANK:
            continue
        key = (str(r.get("Name")), str(r.get("Team")))
        seen.add(key)
        rep = inj.get(key) or {}
        teams.setdefault(key[1], []).append(
            Listed(
                key[0], str(r.get("Position", "")), st, r, injury=str(rep.get("report_primary_injury") or "")
            )
        )
    slate = {str(r.get("Team")) for r in edge}
    for key, rep in inj.items():
        status = REPORT_SHORT.get(str(rep.get("report_status") or ""))
        if key in seen or not status or key[1] not in slate:
            continue
        teams.setdefault(key[1], []).append(
            Listed(
                key[0],
                str(rep.get("Position", "")),
                status,
                injury=str(rep.get("report_primary_injury") or ""),
            )
        )
    for team, listed in teams.items():
        for x in listed:
            if inp.practice is not None:
                t = inp.practice.for_player(x.name, team)
                x.practice = t.text() if t is not None else ""
            x.benefits = by_from.get(x.name, [])
        listed.sort(
            key=lambda x: (
                STATUS_RANK.get(x.status, 9),
                x.row is None,
                -(num((x.row or {}).get("Salary")) or 0),
            )
        )
    return teams


def injury_section(inp) -> str:
    teams = injury_teams(inp)
    if not inp.edge:
        body = '<p class="dim">No slate yet: run <code>dfs sync</code>.</p>'
    elif not teams:
        body = '<p class="dim">Nobody on the slate is listed OUT, Doubtful or Questionable.</p>'
    else:
        order = {}
        for g in sorted(week_plays.slate(inp.edge), key=lambda g: g.start):
            for t in (g.away, g.home):
                order.setdefault(t, len(order))
        cards = []
        for team in sorted(teams, key=lambda t: order.get(t, 99)):
            rows = []
            for x in teams[team]:
                name = _name(x.row) if x.row is not None else e(x.name)
                ben = ", ".join(f"{pn(b.name, b.team)} +{b.bump:.1f}" for b in x.benefits[:4])
                rows.append(
                    f'<tr data-pos="{e(x.pos)}"><td>{name}</td><td>{e(x.pos)}</td>'
                    f'<td><span class="chip q">{e(x.status)}</span></td><td>{e(x.injury)}</td>'
                    f'<td class="prac">{e(x.practice) or "<span class=dim>-</span>"}</td>'
                    f"<td>{ben or '<span class=dim>-</span>'}</td></tr>"
                )
            opp = next((str(r.get("Opp")) for r in inp.edge if r.get("Team") == team), "")
            lines = (getattr(inp, "team_news", None) or {}).get(team) or []
            news = (
                '<ul class="team-news">' + "".join(f"<li>{e(x)}</li>" for x in lines) + "</ul>"
                if lines
                else ""
            )
            cards.append(
                f'<div class="inj-team"><h3>{e(team)} <span class="dim">vs {e(opp)}</span></h3>{news}'
                '<table class="inj" data-shade="off"><thead><tr><th>Player</th><th>Pos</th><th>Status</th>'
                "<th>Injury</th><th>Practice (Wed / Thu / Fri)</th><th>Who benefits</th></tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table></div>"
            )
        note = inp.practice.note() if inp.practice is not None else "No practice report saved this week yet."
        body = (
            f'{how_html(INJURY_HOW)}<div class="inj-grid">{"".join(cards)}</div>'
            f"{caption(INJURY_HOW + ' ' + note, ('EdgeRaw Avail', 'nflverse_injuries', 'calls'))}"
        )
    label, why = TRUST["plays_shadow"]
    return (
        '<section class="card" id="injury-card"><div class="card-head"><h2>Injury report</h2>'
        f"{chip_html(label, why)}</div>{body}</section>"
    )


# -- model vs sources -----------------------------------------------------------------------------------------

SOURCES = (
    ("ProjPts", "TFFB"),
    ("SleeperPts", "Sleeper"),
    ("FantasyProsPts", "FantasyPros"),
    ("EspnPts", "ESPN"),
    ("PropPts", "Props"),
)
MIN_SOURCES = 2  # a consensus needs at least two sources' numbers
MIN_PTS = 4.0  # disagreements are listed for players either side projects at least this many points
DISAGREE = 5  # per position and side
SHOWN = 10  # per position in the every-player table before Show more
MODEL_HOW = (
    "CalPts (our calibrated blend) against each source's projection in DK points; Sources = the median of "
    "the sources that rate him (two or more); vs sources = CalPts minus that median. Disagreements: players "
    "either side projects 4+ points, the five furthest above and below per position (Range = the lowest and highest "
    "source, n = how many rate him; hover a row for each source)."
)
CALLS_HOW = (
    "The plays model's calls (Shadow: not proven). Until the M1.6 labels clear the bar, a cash AVOID on a 30%+ "
    'CashOwn player and any label its own reason contradicts read "no call: label under review". '
    "Cash CORE / PLAY / AVOID from the sim's Cash % for the "
    "price; GPP TARGET / LEVERAGE / FADE from the simulated top-1% share against GppOwn. A hit: CORE / PLAY "
    "at or above his position's median points per $1K, AVOID below it; TARGET / LEVERAGE a real top-1% share "
    "above his real ownership, FADE not. The backtest is M1's walk-forward rerun of Weeks 2 and 4-8; the live "
    "record starts at the Week 9 close and is never mixed with it."
)
CASH_LABELS, GPP_LABELS = ("CORE", "PLAY", "AVOID"), ("TARGET", "LEVERAGE", "FADE")


@dataclass
class Compared:
    row: dict
    calpts: float
    consensus: float
    diff: float
    by_source: dict[str, float | None]


def compared(edge: list[dict] | None) -> dict[str, list[Compared]]:
    """Position -> every rosterable player with CalPts and 2+ sources, furthest from the sources first."""
    out: dict[str, list[Compared]] = {}
    for r in week_plays.rosterable(edge or []):
        cal = num(r.get("CalPts"))
        vals = {c: num(r.get(c)) for c, _ in SOURCES}
        have = [v for v in vals.values() if v is not None]
        if cal is None or len(have) < MIN_SOURCES:
            continue
        med = statistics.median(have)
        out.setdefault(str(r.get("Position", "")), []).append(Compared(r, cal, med, cal - med, vals))
    for v in out.values():
        v.sort(key=lambda c: -abs(c.diff))
    return out


def _cmp_row(c: Compared, pos: str, cls: str = "") -> str:
    cells = [
        _name(c.row),
        e(c.row.get("Team", "")),
        _money(c.row.get("Salary")),
        f"{c.calpts:.1f}",
        *[_f(c.by_source[k]) for k, _ in SOURCES],
        f"{c.consensus:.1f}",
        f"{c.diff:+.1f}",
    ]
    cls_attr = f' class="{cls}"' if cls else ""
    hidden = " hidden" if cls == "more" else ""
    return (
        f'<tr data-pos="{pos}" data-id="{e(_pid(c.row))}"{cls_attr}{hidden}>'
        + "".join(f"<td>{x}</td>" for x in cells)
        + "</tr>"
    )


CMP_HEAD = ["Player", "Team", "Salary", "CalPts", *[label for _, label in SOURCES], "Sources", "vs sources"]
DIS_HEAD = ["Player", "Team", "Salary", "CalPts", "Sources", "Range", "n", "vs sources"]


def _dis_row(c: Compared, pos: str) -> str:
    have = [v for v in c.by_source.values() if v is not None]
    cells = [
        _name(c.row),
        e(c.row.get("Team", "")),
        _money(c.row.get("Salary")),
        f"{c.calpts:.1f}",
        f"{c.consensus:.1f}",
        f"{min(have):.1f}-{max(have):.1f}",
        str(len(have)),
        f"{c.diff:+.1f}",
    ]
    tip = "; ".join(f"{label} {_f(c.by_source[k])}" for k, label in SOURCES if c.by_source[k] is not None)
    return f'<tr data-pos="{pos}" title="{e(tip)}">' + "".join(f"<td>{x}</td>" for x in cells) + "</tr>"


def _dis_table(rows: list[Compared], pos: str) -> str:
    head = "".join(f"<th>{e(h)}</th>" for h in DIS_HEAD)
    body = (
        "".join(_dis_row(c, pos) for c in rows)
        or f'<tr><td colspan="{len(DIS_HEAD)}" class="dim">None.</td></tr>'
    )
    return (
        f'<div class="scroll"><table class="mvs dis" data-shade="off"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _cmp_table(rows_html: str, tid: str = "") -> str:
    head = "".join(f"<th>{e(h)}</th>" for h in CMP_HEAD)
    return (
        f'<div class="scroll"><table class="stickyname mvs"{f" id={tid}" if tid else ""}>'
        f"<thead><tr>{head}</tr></thead>{rows_html}</table></div>"
    )


def week_span(weeks) -> str:
    """[2, 4, 5, 6, 7, 8] -> '2, 4-8'."""
    ws = sorted({int(w) for w in weeks})
    out, i = [], 0
    while i < len(ws):
        j = i
        while j + 1 < len(ws) and ws[j + 1] == ws[j] + 1:
            j += 1
        out.append(str(ws[i]) if i == j else f"{ws[i]}-{ws[j]}")
        i = j + 1
    return ", ".join(out)


def _record_line(label: str, kind: str, record: dict | None) -> str:
    """'60% of 202 (backtest, Weeks 2, 4-8) · live: none yet'."""
    record = record or {}
    summary = record.get("backtest") or {}
    bt = summary.get(f"{kind} hit {label}")
    # the GPP labels were scored over the weeks with GPP standings only (`gpp.weeks`: 4-8, not Week 2)
    weeks = (summary.get(kind) or {}).get("weeks") if isinstance(summary.get(kind), dict) else None
    weeks = weeks or summary.get("weeks") or []
    live = ((record.get("live") or {}).get(kind) or {}).get(label)
    parts = []
    if bt:
        rate, n = bt
        wk = week_span(weeks)
        parts.append(f"backtest {rate * 100:.0f}% of {int(n)} (Weeks {wk})")
    else:
        parts.append("backtest: none saved")
    if live and live[1]:
        parts.append(f"live {live[0]} of {live[1]} ({live[0] / live[1] * 100:.0f}%)")
    else:
        parts.append("live: none yet, it starts at the Week 9 close (0 of 0)")
    return " · ".join(parts)


def _call_row(c: dict, kind: str) -> str:
    from dfs import plays

    score = f"{kind}_score"
    if held(c, kind):
        return (
            f'<tr class="held" data-pos="{e(c.get("position", ""))}"><td>{pn(c["name"], c.get("team", ""))}</td>'
            f"<td>{e(c.get('position', ''))}</td><td>{e(c.get('team', ''))}</td>"
            f"<td>{_money(c.get('salary'))}</td><td>{_f(c.get('calpts'))}</td><td></td>"
            f'<td class="why dim">{e(plays.HOLD_TEXT)}</td></tr>'
        )
    return (
        f'<tr data-pos="{e(c.get("position", ""))}"><td>{pn(c["name"], c.get("team", ""))}</td>'
        f"<td>{e(c.get('position', ''))}</td><td>{e(c.get('team', ''))}</td><td>{_money(c.get('salary'))}</td>"
        f"<td>{_f(c.get('calpts'))}</td><td>{_f(c.get(score), 2)}</td>"
        f'<td class="why">{e(c.get(f"{kind}_reason", ""))}</td></tr>'
    )


def _calls_block(inp, kind: str, label: str, record: dict | None, limit: int = 12) -> str:
    calls = [c for c in calls_of(inp) if c.get(kind) == label]
    score = f"{kind}_score"
    calls.sort(key=lambda c: num(c.get(score)) or 0, reverse=label not in ("AVOID", "FADE"))
    calls.sort(key=lambda c: held(c, kind))  # the calls that stand first, the held ones after them
    rows = "".join(_call_row(c, kind) for c in calls[:limit])
    n_held = sum(held(c, kind) for c in calls)
    more = f" (the top {limit} of {len(calls)})" if len(calls) > limit else ""
    more += f"; {n_held} under review" if n_held else ""
    title = {"cash": "cash", "gpp": "GPP"}[kind]
    head = "".join(f"<th>{h}</th>" for h in ("Player", "Pos", "Team", "Salary", "CalPts", "Score", "Why"))
    return (
        f'<div class="calls-block" data-label="{label}"><h4>{label} <span class="dim">({title}, {len(calls)}'
        f'{more})</span></h4><p class="record">Record: {e(_record_line(label, kind, record))}</p>'
        + (
            f'<div class="scroll"><table class="calls" data-shade="off"><thead><tr>{head}</tr></thead>'
            f"<tbody>{rows}</tbody></table></div>"
            if calls
            else '<p class="dim">None this week.</p>'
        )
        + "</div>"
    )


def model_section(inp) -> str:
    by_pos = compared(inp.edge)
    record = getattr(inp, "calls_record", None)
    if not by_pos:
        body = '<p class="dim">No projections yet: run <code>dfs sync</code>.</p>'
    else:
        dis = []
        for pos in POSITIONS:
            rows = [c for c in by_pos.get(pos, []) if max(c.calpts, c.consensus) >= MIN_PTS]
            hi = sorted([c for c in rows if c.diff > 0], key=lambda c: -c.diff)[:DISAGREE]
            lo = sorted([c for c in rows if c.diff < 0], key=lambda c: c.diff)[:DISAGREE]
            if not (hi or lo):
                continue
            dis.append(
                f'<div class="mvs-pos"><h4>{pos}</h4><div class="mvs-two">'
                f"<div><h5>We are higher</h5>{_dis_table(hi, pos)}</div>"
                f"<div><h5>We are lower</h5>{_dis_table(lo, pos)}</div>"
                "</div></div>"
            )
        groups = []
        for pos in POSITIONS:
            rows = by_pos.get(pos, [])
            if not rows:
                continue
            body_rows = "".join(_cmp_row(c, pos, "" if i < SHOWN else "more") for i, c in enumerate(rows))
            if len(rows) > SHOWN:
                body_rows += (
                    f'<tr class="showrow"><td colspan="{len(CMP_HEAD)}"><button type="button" class="showmore">'
                    f"Show all {len(rows)} {pos}s</button></td></tr>"
                )
            groups.append(f'<tbody class="posgrp" data-pos="{pos}">{body_rows}</tbody>')
        body = (
            f'<h3>The biggest disagreements</h3>{how_html(MODEL_HOW)}<div class="mvs-grid">{"".join(dis)}</div>'
            f"{caption(MODEL_HOW, ('CalPts', *[c for c, _ in SOURCES]))}"
            f'<details class="mvs-all"><summary>Every player, furthest from the sources first</summary>'
            f"{_cmp_table(''.join(groups), 'mvs-all')}</details>"
        )
    if calls_of(inp):
        label, why = TRUST["plays_shadow"]
        lead = "".join(_calls_block(inp, k, lab, record) for k, lab in (("cash", "AVOID"), ("gpp", "FADE")))
        rest = "".join(
            _calls_block(inp, k, lab, record, limit=8)
            for k, labs in (("cash", ("CORE", "PLAY")), ("gpp", ("TARGET", "LEVERAGE")))
            for lab in labs
        )
        calls_html = (
            f'<h3>The plays calls {chip_html(label, why)} <span class="dim">as of {e(calls_at(inp))}</span></h3>'
            f"{how_html(CALLS_HOW)}<p><b>Who not to play first.</b></p>{lead}"
            f'<details class="calls-rest"><summary>Who to play: CORE, PLAY, TARGET, LEVERAGE</summary>{rest}</details>'
            f"{caption(CALLS_HOW, ('calls', 'plays-summary.json', 'calls_history.csv'))}"
        )
    else:
        calls_html = (
            '<h3>The plays calls</h3><p class="dim">Not run yet: every <code>dfs sync</code> saves them '
            "(or <code>dfs plays</code>).</p>"
        )
    return (
        '<section class="card" id="model-card"><div class="card-head"><h2>Our model against the sources</h2>'
        f"</div>{body}{calls_html}</section>"
    )


# -- compare ---------------------------------------------------------------------------------------------------


def compare_spec() -> list[dict]:
    """The Compare tab's rows (`compare_card.SECTIONS`) as the compare tray reads them: EdgeRaw columns (the
    Explorer's data keys) and which way is better (None = never shaded)."""
    out = []
    for title, rows in cc.SECTIONS:
        items = []
        for r in rows:
            col = "Implied" if r.spec is pc.IMPLIED else r.spec.column
            if r.spec.source not in (pc.EDGE, pc.DERIVED):
                continue
            items.append({"label": r.spec.label, "col": col, "better": r.better})
        out.append({"title": title, "note": cc.NOTES.get(title, ""), "rows": items})
    return out


NEWS_AT = "#board, #decisions, #overview-card table.ov, #model-card table.calls, #injury-card"
NEWS_NOTE = (
    "Team news (facts, not a model): the QB each pass catcher catches from when the starter is out, a pass "
    "catcher or lead back who is out (his share of the team's targets or snaps over his last five games), and "
    "who leads the team's targets then. From: EdgeRaw Avail, the injury report, the last five games "
    "(news_context)."
)


def news_json(inp) -> str:
    """{EdgeRaw Id: [[short, text, tone], ...]}: the chips the client paints after every name (`NEWS_AT`)
    and the lines the drawer and the compare tray show."""
    data = {
        pid: [[f.short or f.text, f.text, f.tone] for f in facts]
        for pid, facts in (getattr(inp, "news", None) or {}).items()
    }
    return json.dumps({"at": NEWS_AT, "note": NEWS_NOTE, "by": data}, separators=(",", ":")).replace(
        "</", "<\\/"
    )


def compare_json() -> str:
    data = {
        "sections": compare_spec(),
        "same_pos": cc.SAME_POSITION_NOTE,
        "highlight": cc.HIGHLIGHT_NOTE,
        "max": 4,
    }
    return json.dumps(data, separators=(",", ":")).replace("</", "<\\/")


def record_of(history: list[dict] | None, backtest: dict | None) -> dict:
    """The calls' record kept apart: `backtest` (M1's summary JSON as saved) and `live` (kind -> label ->
    [hits, n] from `calls_history.csv`, every live or rebuilt week)."""
    live: dict[str, dict[str, list[int]]] = {"cash": {}, "gpp": {}}
    weeks: set[int] = set()
    for r in history or []:
        for kind in ("cash", "gpp"):
            hit = num(r.get(f"{kind}_hit"))
            label = r.get(kind)
            if hit is None or not label or label == "NEUTRAL":
                continue
            cur = live[kind].setdefault(str(label), [0, 0])
            cur[0] += int(hit)
            cur[1] += 1
            if num(r.get("week")) is not None:
                weeks.add(int(num(r.get("week")) or 0))
    return {"backtest": backtest or None, "live": live, "live_weeks": sorted(weeks)}


__all__ = [
    "DEFAULT_VIEW",
    "PLACES",
    "VIEWS",
    "compare_json",
    "injury_section",
    "model_section",
    "overview_section",
    "record_of",
    "rich",
    "usage_section",
    "view_bar",
    "wrap",
]


# -- the client half (both pages): views, usage, search, compare ------------------------------------------------

CSS = """
#views{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin:10px 0 12px;position:sticky;top:0;z-index:12;
  background:var(--bg);padding:6px 0;border-bottom:1px solid var(--line)}
#views a{padding:5px 11px;border-radius:999px;border:1px solid var(--line);color:inherit;text-decoration:none;
  font-size:13px;background:var(--card)}
#views a.on{background:var(--ink,#1f2937);color:var(--bg,#fff);border-color:transparent;font-weight:600}
.vsearch{margin-left:auto;position:relative}
#psearch{width:220px;padding:5px 9px;border:1px solid var(--line);border-radius:6px;font:inherit;font-size:13px;
  background:var(--card);color:inherit}
.srlist{position:absolute;right:0;top:32px;width:340px;max-height:60vh;overflow:auto;background:var(--card);
  border:1px solid var(--line);border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.15);z-index:70;padding:4px}
.srlist .sr{display:flex;gap:6px;align-items:center;padding:5px 6px;border-radius:5px;font-size:13px}
.srlist .sr.act{background:var(--hover,rgba(0,0,0,.06))}
.srlist .sr .dim{flex:1}
button.cmp-add{font-size:11.5px;padding:2px 7px}
.grid:has(> .vw[hidden]){grid-template-columns:minmax(0,1fr)}
table.ov th[scope],table.ov tbody th{font-size:14px;text-align:left;vertical-align:top}
table.ov td{vertical-align:top;font-size:12.5px;line-height:1.6;min-width:150px}
.ov-row{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:16px;margin-top:10px}
.ov-games{margin:4px 0;padding-left:18px;font-size:13px;line-height:1.55}
table.ov-pool td,table.ov-pool th{text-align:center;padding:2px 8px}
.chip.chalk{background:#fde7d3;color:#7a3b07}.chip.shadow{background:#ece7f8;color:#4b3a85}
.uv-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:6px 0 8px;font-size:13px}
.uv-bar .seg button{font-size:12.5px}
svg.spark{vertical-align:middle}svg.spark polyline{fill:none;stroke:#2563eb;stroke-width:1.6}
svg.spark line{stroke:#9ca3af;stroke-dasharray:3 2}svg.spark circle{fill:#2563eb}
.bars{display:flex;height:16px;border-radius:4px;overflow:hidden;min-width:220px;background:#eef0f3}
.bars span{display:block;height:100%;font-size:10px;line-height:16px;color:#fff;overflow:hidden;white-space:nowrap;
  text-indent:3px;cursor:pointer}
.bars span.rest{background:#c8ccd3;cursor:default}
.bars span:nth-child(1){background:#2563eb}.bars span:nth-child(2){background:#16a34a}
.bars span:nth-child(3){background:#d97706}.bars span:nth-child(4){background:#9333ea}
.bars span:nth-child(5){background:#0891b2}
.barkey{font-size:11.5px;margin-top:2px}
.inj-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:12px}
.inj-team h3{margin:6px 0 4px;font-size:14px}
table.inj{font-size:12.5px;width:100%}table.inj td.prac{white-space:nowrap}
.mvs-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:8px}
.mvs-two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}
.mvs-pos h4{margin:8px 0 2px}.mvs-two h5{margin:2px 0;font-size:12px;color:var(--dim,#6b7280)}
table.mvs{font-size:12.5px}
.calls-block h4{margin:10px 0 2px}.calls-block .record{margin:0 0 4px;font-size:12.5px}
table.calls td.why{max-width:520px;font-size:12px}
table.calls tr.held td{color:var(--dim)}.chip.held{background:transparent;border:1px dashed currentColor;opacity:.75}
#cmp-tray{position:fixed;left:16px;bottom:16px;z-index:80;background:var(--card);border:1px solid var(--line);
  border-radius:10px;box-shadow:0 6px 20px rgba(0,0,0,.18);padding:8px 10px;display:flex;gap:6px;align-items:center;
  flex-wrap:wrap;max-width:calc(100vw - 32px);font-size:13px}
#cmp-tray .tchip2{background:#eef2ff;border-radius:999px;padding:2px 4px 2px 9px}
#cmp-tray .tchip2 button{border:0;background:none;cursor:pointer;font-size:14px;padding:0 4px}
.news{display:inline-block;font-size:11px;line-height:16px;padding:0 6px;margin:1px 0 1px 4px;border-radius:9px;
  border:1px solid var(--line);white-space:nowrap;vertical-align:baseline;cursor:help}
.news.down{color:var(--bad);border-color:var(--bad);background:var(--bad-soft)}
.news.up{color:var(--ok);border-color:var(--ok)}
.news.warn{color:var(--warn);border-color:var(--warn);background:var(--flag)}
p.newsline{margin:6px 0}p.newsline .news{white-space:normal;cursor:default}
ul.team-news{margin:2px 0 8px;padding-left:18px;font-size:13px}ul.team-news li{margin:1px 0}
#cmp-view{position:fixed;inset:24px;z-index:90;background:var(--card);border:1px solid var(--line);border-radius:12px;
  box-shadow:0 10px 40px rgba(0,0,0,.25);display:flex;flex-direction:column;overflow:hidden}
#cmp-view .vbar{display:flex;gap:10px;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line)}
#cmp-view .vbar h2{margin:0;font-size:16px;flex:1}
#cmp-view .cbody{overflow:auto;padding:8px 14px 16px;display:block}
table.cmp{border-collapse:collapse;font-size:13px}
table.cmp th,table.cmp td{padding:3px 10px;border-bottom:1px solid var(--line);text-align:right}
table.cmp th:first-child,table.cmp td:first-child{text-align:left;position:sticky;left:0;background:var(--card)}
table.cmp tr.sect td{text-align:left;font-weight:700;padding-top:10px;background:var(--card)}
table.cmp td.best{background:#dcfce7;font-weight:600}
@media(max-width:900px){.ov-row,.mvs-two{grid-template-columns:minmax(0,1fr)}.inj-grid{grid-template-columns:1fr}
  #psearch{width:150px}.vsearch{margin-left:0}}
"""

JS = r"""
(() => {
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, cls, text) => { const x = document.createElement(tag); if (cls) x.className = cls; if (text != null) x.textContent = text; return x; };
const WEEK = (document.querySelector('meta[name="dfs-week"]') || {}).content || '';
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
                set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };
const DD = window.dfsData || null;   // the Explorer's data (every priced player, every EdgeRaw column)
const val = (id, key) => { if (!DD) return null; const i = DD.IDX[id], c = DD.COL[key]; return i == null || !c ? null : DD.D.rows[i][c.i]; };
const fmtCol = (key, x) => (DD && DD.COL[key] && x != null && x !== '') ? DD.fmt(DD.COL[key], x) : '';
const adopt = (root) => { if (window.dfsPool) window.dfsPool.adopt(root); };

/* ---------- team news: chips after every name in the places that decide (`news_context`) ---------- */
const NEWS = JSON.parse(($('#news-data') || {}).textContent || '{"at":"","by":{}}');
const newsOf = (id) => (NEWS.by || {})[id] || [];
function newsChips(id, full) {
  const wrap = el('span', 'newsrow');
  for (const [short, text, tone] of newsOf(id)) { const c = el('span', 'news ' + tone, full ? text : short); c.title = text; wrap.append(c); }
  return wrap;
}
function idOf(a) {
  if (a.dataset.id) return a.dataset.id;
  const n = a.nextElementSibling; if (n && n.classList.contains('pchip')) return n.dataset.id;
  const tr = a.closest('tr[data-id]'); return tr ? tr.dataset.id : '';
}
function paintNews(root) {
  if (!NEWS.at) return;
  for (const scope of $$(NEWS.at, root || document)) for (const a of $$('a.pl', scope)) {
    if (a.closest('#drawer, #pool-pop, svg')) continue;
    const id = idOf(a); if (!id || !newsOf(id).length) continue;
    const after = a.nextElementSibling && a.nextElementSibling.classList.contains('pchip') ? a.nextElementSibling : a;
    if (after.nextElementSibling && after.nextElementSibling.classList.contains('newsrow')) continue;
    const chips = newsChips(id, false); chips.dataset.for = id; after.after(chips);
  }
}
window.dfsNews = { get: newsOf, chips: newsChips, paint: paintNews, note: NEWS.note || '' };

/* ---------- the view bar: each view its own #name, the last one kept per week ---------- */
const NAMES = $$('#views a[data-view]').map((a) => a.dataset.view);
const VKEY = 'dfs-view-' + WEEK;
let current = null;
function show(v) {
  if (!NAMES.includes(v)) v = NAMES[0];
  const changed = v !== current; current = v;
  document.body.dataset.view = v;
  for (const w of $$('.vw')) w.hidden = !w.dataset.v.split(' ').includes(v);
  for (const a of $$('#views a[data-view]')) { const on = a.dataset.view === v; a.classList.toggle('on', on); if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); }
  store.set(VKEY, v);
  if (changed) { drawVisible(); window.dispatchEvent(new CustomEvent('dfs-view', {detail: v})); }
}
function top() { const s = document.getElementById('pagecol'); (s && s.scrollHeight > s.clientHeight ? s : document.scrollingElement).scrollTo({top: 0}); }
function fromHash(initial) {
  const h = decodeURIComponent(location.hash.slice(1));
  if (NAMES.includes(h)) { const was = current; show(h); if (!initial && was !== h) top(); return; }
  if (h) {
    const t = document.getElementById(h);
    if (t) { const w = t.closest('.vw'); if (w && w.hidden) show(w.dataset.v.split(' ')[0]); if (!initial) t.scrollIntoView(); return; }
  }
  if (initial) show(store.get(VKEY, NAMES[0]));
}
window.addEventListener('hashchange', () => fromHash(false));
window.dfsViews = { show: (v) => { location.hash = v; }, current: () => current };

/* ---------- usage ---------- */
const METRICS = {
  'Snap%': {label: 'Snap %', pct: true}, 'Tgt': {label: 'Tgt/G'}, 'Tgt%': {label: 'Tgt %', pct: true},
  'Air%': {label: 'Air %', pct: true}, 'WOPR': {label: 'WOPR', d: 2}, 'Car': {label: 'Car/G'},
  'RZ': {label: 'RZ/G'}, 'HVT': {label: 'HVT/G'}, 'DK': {label: 'DK/G'},
};
const BY_POS = {QB: ['Snap%', 'Car', 'RZ', 'DK'], RB: ['Snap%', 'Car', 'Tgt', 'Tgt%', 'RZ', 'HVT', 'DK'],
                WR: ['Snap%', 'Tgt', 'Tgt%', 'Air%', 'WOPR', 'RZ', 'DK'], TE: ['Snap%', 'Tgt', 'Tgt%', 'Air%', 'WOPR', 'RZ', 'DK']};
const TREND = {QB: 'DK', RB: 'Car', WR: 'Tgt%', TE: 'Tgt%'};
const UKEY = 'dfs-usage-' + WEEK;
let U = null, US = Object.assign({pos: 'WR', win: 'season', metric: null, show: '15'}, store.get(UKEY, {})), usageDirty = true;
function loadUsage() { const s = $('#usage-data'); U = s ? JSON.parse(s.textContent) : null; usageDirty = true; }
function games(id) {
  const g = (U.players[id] || []).map((a) => Object.fromEntries(U.fields.map((f, i) => [f, a[i]])));
  return US.win === 'season' ? g : g.slice(-Number(US.win));
}
const mean = (xs) => { const v = xs.filter((x) => x != null && !isNaN(x)); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
function fmtM(k, x) { if (x == null) return ''; const m = METRICS[k] || {}; return m.pct ? Math.round(x * 100) + '%' : x.toFixed(m.d != null ? m.d : 1); }
function spark(all, k) {
  const pts = all.slice(-5).map((g) => g[k]), season = mean(all.map((g) => g[k]));
  const xs = pts.filter((x) => x != null); if (!xs.length) return '';
  const lo = Math.min(...xs, season), hi = Math.max(...xs, season), W = 84, H = 22, sp = hi - lo || 1;
  const X = (i) => 3 + i * ((W - 6) / Math.max(pts.length - 1, 1)), Y = (x) => H - 3 - (x - lo) / sp * (H - 6);
  const line = pts.map((x, i) => x == null ? null : X(i).toFixed(1) + ',' + Y(x).toFixed(1)).filter(Boolean).join(' ');
  const last = pts.length - 1;
  const tip = 'Last ' + pts.length + ': ' + pts.map((x) => fmtM(k, x)).join(', ') + ' · season ' + fmtM(k, season);
  return '<svg class="spark" width="' + W + '" height="' + H + '" role="img" aria-label="' + tip + '"><title>' + tip + '</title><line x1="2" x2="' + (W - 2) + '" y1="' + Y(season).toFixed(1) + '" y2="' + Y(season).toFixed(1) + '"/><polyline points="' + line + '"/>' + (pts[last] != null ? '<circle r="2.4" cx="' + X(last).toFixed(1) + '" cy="' + Y(pts[last]).toFixed(1) + '"/>' : '') + '</svg>';
}
function usageRows() {
  return Object.keys(U.players).filter((id) => val(id, 'Position') === US.pos).map((id) => {
    const g = games(id), all = (U.players[id] || []).map((a) => Object.fromEntries(U.fields.map((f, i) => [f, a[i]])));
    const m = {}; for (const k of BY_POS[US.pos]) m[k] = mean(g.map((x) => x[k]));
    return {id, g, all, m, sal: Number(val(id, 'Salary')) || null};
  });
}
function drawUsage() {
  if (!U || !$('#uv-table')) return;
  usageDirty = false;
  const posBox = $('#uv-pos'); posBox.replaceChildren();
  for (const p of U.positions) { const b = el('button', p === US.pos ? 'on' : '', p); b.type = 'button'; b.dataset.v = p; b.onclick = () => { US.pos = p; US.metric = null; save(); drawUsage(); }; posBox.append(b); }
  const msel = $('#uv-metric'); msel.replaceChildren(); const metric = US.metric || TREND[US.pos];
  for (const k of BY_POS[US.pos]) { const o = new Option(METRICS[k].label, k); o.selected = k === metric; msel.append(o); }
  msel.onchange = () => { US.metric = msel.value; save(); drawUsage(); };
  const win = $('#uv-win'); win.value = US.win; win.onchange = () => { US.win = win.value; save(); drawUsage(); };
  const shw = $('#uv-show'); shw.value = US.show; shw.onchange = () => { US.show = shw.value; save(); drawUsage(); };
  const rows = usageRows().sort((a, b) => (b.m[metric] ?? -1) - (a.m[metric] ?? -1));
  const cols = BY_POS[US.pos];
  const t = el('table', 'stickyname uv');
  const head = el('tr'); for (const h of ['Player', 'Team', 'Salary', ...cols.map((k) => METRICS[k].label), 'Trend: ' + METRICS[metric].label, 'G']) head.append(el('th', null, h));
  const thead = el('thead'); thead.append(head); t.append(thead);
  const tb = el('tbody'); tb.dataset.pos = US.pos;
  for (const r of rows.slice(0, Number(US.show))) {
    const tr = el('tr'); tr.dataset.pos = US.pos; tr.dataset.id = r.id;
    const td = el('td'); const a = el('a', 'pl', val(r.id, 'Name')); a.href = '#'; a.dataset.id = r.id; td.append(a); tr.append(td);
    tr.append(el('td', null, val(r.id, 'Team')), el('td', null, r.sal ? '$' + r.sal.toLocaleString() : ''));
    for (const k of cols) tr.append(el('td', null, fmtM(k, r.m[k])));
    const sp = el('td'); sp.innerHTML = spark(r.all, metric); tr.append(sp, el('td', null, String(r.g.length)));
    tb.append(tr);
  }
  t.append(tb);
  const box = $('#uv-table'); box.replaceChildren(t);
  adopt(box);
  drawUsageCharts(rows);
  drawTeams();
}
function save() { store.set(UKEY, US); }
function fit(points) {
  const n = points.length; if (n < 3) return (p) => 0;
  const mx = mean(points.map((p) => p.x)), my = mean(points.map((p) => p.y));
  let sxy = 0, sxx = 0; for (const p of points) { sxy += (p.x - mx) * (p.y - my); sxx += (p.x - mx) * (p.x - mx); }
  const b = sxx ? sxy / sxx : 0; return (p) => Math.abs(p.y - (my + b * (p.x - mx)));
}
function scatterSpec(title, xl, yl, xfmt, points, how) {
  const res = fit(points); for (const p of points) p.prio = res(p);
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  const padx = (Math.max(...xs) - Math.min(...xs)) * 0.06 || 1, pady = (Math.max(...ys) - Math.min(...ys)) * 0.08 || 1;
  return {title, x: xl, y: yl, xfmt, yfmt: '', how, points,
          range: [Math.min(...xs) - padx, Math.max(...xs) + padx, Math.max(0, Math.min(...ys) - pady), Math.max(...ys) + pady]};
}
function drawUsageCharts(rows) {
  const box = $('#uv-charts'); if (!box || !window.dfsScatter) return; box.replaceChildren();
  const pos = US.pos, ok = (r) => r.g.length > 0;
  const pt = (r, x, y) => ({id: r.id, name: val(r.id, 'Name'), short: String(val(r.id, 'Name')).split(' ').slice(-1)[0], pos, x, y, sal: r.sal || 0});
  const yk = pos === 'QB' ? 'Car' : 'Tgt';
  const a = rows.filter((r) => ok(r) && r.m['Snap%'] != null && r.m[yk] != null).map((r) => pt(r, r.m['Snap%'] * 100, r.m[yk]));
  const b = rows.filter((r) => ok(r) && r.sal).map((r) => pt(r, r.sal, (r.m.Tgt || 0) + (r.m.Car || 0)));
  const win = US.win === 'season' ? 'this season' : 'his last ' + US.win;
  const charts = [
    [scatterSpec((pos === 'QB' ? 'Carries' : 'Targets') + ' against snaps, ' + pos + 's', 'Snap % (' + win + ')', (pos === 'QB' ? 'Carries' : 'Targets') + ' per game', 'pct', a, 'Each dot a ' + pos + ' on this slate: his mean snap share and per-game volume over ' + win + '. Labelled: the eight furthest from the straight-line fit (the outliers).'), a],
    [scatterSpec('Opportunity against salary, ' + pos + 's', 'DK salary', 'Targets + carries per game', 'money', b, 'Each dot a ' + pos + ': salary against targets + carries per game over ' + win + '. Above the crowd = more work for the price. Labelled: the eight furthest from the fit.'), b],
  ];
  for (const [spec, pts] of charts) {
    const card = el('section', 'card chartcard'); const h = el('div', 'card-head'); h.append(el('h3', null, spec.title)); card.append(h);
    const plot = el('div', 'ex-plot'); card.append(plot);
    if (pts.length < 3) plot.append(el('p', 'dim', 'Not enough players with both numbers.'));
    else plot.innerHTML = window.dfsScatter(spec, Math.max(320, Math.min(620, (box.clientWidth || 1100) / 2 - 30)), 320, 8);
    card.append(el('p', 'cap', spec.how));
    box.append(card);
  }
}
function drawTeams() {
  const box = $('#uv-teams'); if (!box || !U) return;
  const n = US.win === 'season' ? 99 : Number(US.win);
  const byTeam = {};
  for (const id of Object.keys(U.players)) { const t = val(id, 'Team'); (byTeam[t] = byTeam[t] || []).push(id); }
  const t = el('table', 'uvteams'); t.dataset.shade = 'off';
  const head = el('tr'); for (const h of ['Team', 'Plays/G', 'Pass %', 'PROE', 'Pace (s)', 'RZ trips/G', 'Targets', 'Carries']) head.append(el('th', null, h));
  const thead = el('thead'); thead.append(head); t.append(thead); const tb = el('tbody');
  const teams = Object.keys(U.teams).sort();
  for (const team of teams) {
    const g = U.teams[team].map((a) => Object.fromEntries(U.team_fields.map((f, i) => [f, a[i]]))).slice(-n);
    const wks = new Set(g.map((x) => x.Wk));
    const tr = el('tr');
    const tm = el('td'); const b = el('b', null, team); tm.append(b, el('span', 'dim', ' ' + g.length + ' g')); tr.append(tm);
    const m = (k) => mean(g.map((x) => x[k]));
    tr.append(el('td', null, (m('Plays') ?? 0).toFixed(1)), el('td', null, m('Pass%') == null ? '' : Math.round(m('Pass%') * 100) + '%'),
              el('td', null, m('PROE') == null ? '' : (m('PROE') > 0 ? '+' : '') + m('PROE').toFixed(1)), el('td', null, m('Pace') == null ? '' : m('Pace').toFixed(1)),
              el('td', null, m('RZTrips') == null ? '' : m('RZTrips').toFixed(1)));
    for (const k of ['Tgt', 'Car']) {
      const total = g.reduce((s, x) => s + (x[k] || 0), 0);
      const shares = (byTeam[team] || []).map((id) => {
        const mine = (U.players[id] || []).map((a) => Object.fromEntries(U.fields.map((f, i) => [f, a[i]]))).filter((x) => wks.has(x.Wk));
        return [id, total ? mine.reduce((s, x) => s + (x[k] || 0), 0) / total : 0];
      }).filter((x) => x[1] > 0.02).sort((a2, b2) => b2[1] - a2[1]).slice(0, 5);
      const td = el('td'); const bars = el('div', 'bars'); let used = 0;
      for (const [id, s] of shares) { const seg = el('span', 'pl'); seg.dataset.id = id; seg.style.width = (s * 100).toFixed(1) + '%'; seg.title = val(id, 'Name') + ': ' + Math.round(s * 100) + '% of the ' + (k === 'Tgt' ? 'targets' : 'carries'); if (s >= 0.12) seg.textContent = String(val(id, 'Name')).split(' ').slice(-1)[0]; bars.append(seg); used += s; }
      if (used < 0.999 && total) { const r = el('span', 'rest'); r.style.width = ((1 - used) * 100).toFixed(1) + '%'; r.title = 'Everyone else (not on the slate or under 2%)'; bars.append(r); }
      td.append(bars);
      const key = el('div', 'barkey');
      shares.slice(0, 3).forEach(([id, s], i) => { if (i) key.append(document.createTextNode(' · ')); const a = el('a', 'pl', val(id, 'Name')); a.href = '#'; a.dataset.id = id; key.append(a, document.createTextNode(' ' + Math.round(s * 100) + '%')); });
      td.append(key); tr.append(td);
    }
    tb.append(tr);
  }
  t.append(tb); box.replaceChildren(t); adopt(box);
}

/* ---------- search: every priced player, on every view ---------- */
const norm = (s) => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9 ]/g, '');
const input = $('#psearch'), list = $('#psearch-list');
let hits = [], act = 0;
function drawHits() {
  list.replaceChildren(); list.hidden = !hits.length;
  hits.forEach((id, i) => {
    const row = el('div', 'sr' + (i === act ? ' act' : '')); row.dataset.id = id;
    const a = el('a', 'pl', val(id, 'Name')); a.href = '#'; a.dataset.id = id;
    const sal = Number(val(id, 'Salary'));
    row.append(a, el('span', 'dim', [val(id, 'Position'), val(id, 'Team'), sal ? '$' + sal.toLocaleString() : ''].filter(Boolean).join(' · ')), cmpButton(id));
    list.append(row);
  });
  adopt(list);
}
function search() {
  const q = norm(input.value).trim(); act = 0;
  if (!q || !DD) { hits = []; drawHits(); return; }
  const ni = DD.COL.Name.i, pts = DD.COL.CalPts ? DD.COL.CalPts.i : -1;
  const scored = [];
  DD.D.rows.forEach((r, i) => { const n = norm(r[ni]); const at = n.indexOf(q); if (at < 0) return; const word = at === 0 || n[at - 1] === ' '; scored.push([DD.D.ids[i], word ? 0 : 1, -(Number(pts >= 0 ? r[pts] : 0) || 0)]); });
  scored.sort((a, b) => a[1] - b[1] || a[2] - b[2]);
  hits = scored.slice(0, 8).map((x) => x[0]); drawHits();
}
if (input) {
  input.addEventListener('input', search);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); if (hits.length) { act = (act + (e.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length; drawHits(); } }
    else if (e.key === 'Enter' && hits.length) { e.preventDefault(); if (window.dfsDrawer) window.dfsDrawer(hits[act]); }
    else if (e.key === 'Escape') { input.value = ''; hits = []; drawHits(); }
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('.vsearch') && !e.target.closest('#pool-pop')) { list.hidden = true; } else if (e.target === input && hits.length) list.hidden = false; });
  document.addEventListener('keydown', (e) => { if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || '')) { e.preventDefault(); input.focus(); } });
}

/* ---------- compare: 2-4 players side by side ---------- */
const SPEC = JSON.parse(($('#compare-spec') || {}).textContent || '{"sections":[],"max":4}');
const CKEY = 'dfs-compare-' + WEEK;
let picks = (store.get(CKEY, []) || []).filter((id) => DD && DD.IDX[id] != null);
function cmpButton(id) { const b = el('button', 'cmp-add', picks.includes(id) ? '✓ Compare' : '+ Compare'); b.type = 'button'; b.dataset.cmp = id; b.title = 'Add him to the compare tray (2-4 players side by side)'; return b; }
function addCompare(id) {
  if (!DD || DD.IDX[id] == null) return;
  if (picks.includes(id)) picks = picks.filter((x) => x !== id);
  else { if (picks.length >= SPEC.max) picks.shift(); picks.push(id); }
  store.set(CKEY, picks); drawTray();
  for (const b of $$('button.cmp-add[data-cmp="' + CSS.escape(id) + '"]')) b.textContent = picks.includes(id) ? '✓ Compare' : '+ Compare';
  if ($('#cmp-view')) openCompare();
}
function drawTray() {
  let tray = $('#cmp-tray');
  if (!picks.length) { if (tray) tray.remove(); return; }
  if (!tray) { tray = el('div'); tray.id = 'cmp-tray'; tray.setAttribute('aria-label', 'Compare tray'); document.body.append(tray); }
  tray.replaceChildren(el('b', null, 'Compare'));
  for (const id of picks) { const c = el('span', 'tchip2', val(id, 'Name')); const x = el('button', null, '×'); x.type = 'button'; x.title = 'Take him out'; x.onclick = () => addCompare(id); c.append(x); tray.append(c); }
  const go = el('button', 'primary', picks.length < 2 ? 'Add one more' : 'Compare ' + picks.length); go.type = 'button'; go.id = 'cmp-open'; go.disabled = picks.length < 2; go.onclick = openCompare;
  const clr = el('button', null, 'Clear'); clr.type = 'button'; clr.onclick = () => { picks = []; store.set(CKEY, picks); drawTray(); const v = $('#cmp-view'); if (v) v.remove(); for (const b of $$('button.cmp-add')) b.textContent = '+ Compare'; };
  tray.append(go, clr);
}
function best(values, better) {
  if (!better) return [];
  const xs = values.map((x) => (x == null || x === '' || isNaN(x)) ? null : Number(x)), have = xs.filter((x) => x != null);
  if (have.length < 2 || Math.max(...have) === Math.min(...have)) return [];
  const t = better === 'low' ? Math.min(...have) : Math.max(...have);
  return xs.map((x, i) => x === t ? i : -1).filter((i) => i >= 0);
}
function usageOf(id) {
  if (U && U.players[id]) return U.players[id].map((a) => Object.fromEntries(U.fields.map((f, i) => [f, a[i]])));
  return ((DD && DD.D.log[id]) || []).slice().reverse().map((g) => ({'Snap%': g.Snap, Tgt: g.Tgt, Car: g.Car, DK: g.DK}));
}
function openCompare() {
  if (picks.length < 2) return;
  let ov = $('#cmp-view');
  if (!ov) { ov = el('div'); ov.id = 'cmp-view'; ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-label', 'Compare players'); document.body.append(ov); }
  ov.replaceChildren();
  const bar = el('div', 'vbar'); bar.append(el('h2', null, 'Compare ' + picks.length + ' players'));
  const x = el('button', null, 'Close'); x.type = 'button'; x.onclick = () => ov.remove(); bar.append(x); ov.append(bar);
  const body = el('div', 'cbody'); ov.append(body);
  const pos = new Set(picks.map((id) => val(id, 'Position')));
  if (pos.size > 1) body.append(el('p', 'note warn', SPEC.same_pos));
  body.append(el('p', 'cap', SPEC.highlight));
  const t = el('table', 'cmp'); t.dataset.shade = 'off';
  const hr = el('tr'); hr.append(el('th'));
  for (const id of picks) { const th = el('th'); const a = el('a', 'pl', val(id, 'Name')); a.href = '#'; a.dataset.id = id; th.append(a); hr.append(th); }
  t.append(hr);
  const sect = (title) => { const tr = el('tr', 'sect'); const td = el('td', null, title); td.colSpan = picks.length + 1; tr.append(td); t.append(tr); };
  const row = (label, cells, marks) => { const tr = el('tr'); tr.append(el('td', null, label)); cells.forEach((c, i) => { const td = el('td', marks.includes(i) ? 'best' : ''); if (c instanceof Node) td.append(c); else td.textContent = c; tr.append(td); }); t.append(tr); };
  for (const s of SPEC.sections) {
    const rows = s.rows.filter((r) => DD.COL[r.col]); if (!rows.length) continue;
    sect(s.title);
    for (const r of rows) { const raw = picks.map((id) => val(id, r.col)); row(r.label, raw.map((v2) => fmtCol(r.col, v2)), best(raw, r.better)); }
  }
  if (picks.some((id) => newsOf(id).length)) {
    sect('TEAM NEWS');
    row('This week', picks.map((id) => newsChips(id, true)), []);
  }
  sect('USAGE TRENDS (last five games, dashed = his season mean)');
  for (const k of ['Snap%', 'Tgt%', 'WOPR', 'Car', 'RZ', 'DK']) {
    const cells = picks.map((id) => { const g = usageOf(id); const span = el('span'); span.innerHTML = g.length ? spark(g, k) + ' ' + fmtM(k, mean(g.slice(-5).map((y) => y[k]))) : ''; return span; });
    if (cells.every((c) => !c.innerHTML)) continue;
    row(METRICS[k].label, cells, []);
  }
  body.append(t); adopt(ov);
}
document.addEventListener('click', (e) => { const b = e.target.closest('button.cmp-add[data-cmp]'); if (!b) return; e.preventDefault(); e.stopPropagation(); addCompare(b.dataset.cmp); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('#cmp-view')) $('#cmp-view').remove(); });
window.dfsCompare = { add: addCompare, has: (id) => picks.includes(id), button: cmpButton, open: openCompare, picks: () => picks.slice() };

/* ---------- drawing what is visible; a refresh of the served page's sections ---------- */
function drawVisible() {
  const card = $('#usage-card');
  if (card && !card.closest('.vw[hidden]') && usageDirty) drawUsage();
  if ((current === 'explore' || current === 'everything') && window.dfsExplorer) window.dfsExplorer.render();
}
document.addEventListener('dfs-sections', () => { loadUsage(); paintNews(); drawVisible(); });
window.addEventListener('resize', () => { if (U && !usageDirty && $('#usage-card') && !$('#usage-card').closest('.vw[hidden]')) drawUsageCharts(usageRows()); });
loadUsage();
paintNews();
document.addEventListener('DOMContentLoaded', () => paintNews());  // after week_ui's decorate gives every name its id
drawTray();
fromHash(true);
drawVisible();
})();
"""
