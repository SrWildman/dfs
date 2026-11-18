"""The Saturday view's own blocks (design pass, PR 1; Sam's answers of 2026-11-17).

- **The slate digest** (before a build it leads the day; after one it folds under the cash board): the games
  ranked by total with each team's implied total and the line's move since the opening snapshot, the key
  injuries and the line moves in one list, then the best plays per position (three per position, one number
  each: the projection; the rest is in the drawer), the pool square on every name.
- **Chalk you're not on**: every player projected at 30%+ double-up ownership (CashOwn) who is not in Sam's
  cash lineup, with his projection, salary and why. The lineup is the Default cash lineup when a run saved
  one, else the newest round's first cash build, else the cash lineup he entered. Information only.
- **Fades**: FADE↓ (Model Check's signal, its record in the header) and the plays model's AVOID in cash
  (Shadow, its own record), one list.
- **Someone else** for an open spot (`spot_options`): every slate player who fits the slot (his position;
  RB / WR / TE for FLEX) and the salary (the Default's player's salary plus what the Default leaves), not
  already in the Default, with his difference against the Default's player. The page lists the ones in his
  pool; a pick writes the same `cash: Name` on Always use as a card pick (`week_actions.decide_pool`).

Pure: everything reads `WeekInputs` and the board.
"""

from __future__ import annotations

from dfs import week_board as wb
from dfs import week_plays
from dfs.week_inputs import WeekInputs
from dfs.week_page import CHALK_THRESHOLD, TRUST, builds_of, chip_html, e, num, pct, pn
from dfs.week_style import card, fold

POSITIONS = week_plays.POSITIONS
SKILL = ("RB", "WR", "TE")
BEST_PER_POS = 3
MOVE_SPREAD = 1.0  # a spread move this big (points, since the opening snapshot) is listed as a line move
CASH_FORMATS = ("Double Up", "50/50", "H2H", "Head to Head", "Multiplier")
CHALK_ID = "chalk-off"
DIGEST_ID = "digest"
FADES_ID = "fades"


def _money(v: object) -> str:
    x = num(v)
    return "-" if x is None else f"${x:,.0f}"


def _f(v: object, digits: int = 1) -> str:
    x = num(v)
    return "-" if x is None else f"{x:.{digits}f}"


def _signed(v: float | None, digits: int = 1) -> str:
    return "" if v is None or abs(v) < 0.05 else f"{v:+.{digits}f}"


def player(row: dict, meta: str = "") -> str:
    """The player cell: position, the name (opens the drawer; the page puts the pool square before it), a
    status tag, then a 12 px meta line."""
    status = wb.avail(row)
    tag = ""
    if status in ("Q", "D"):
        tag = f' <span class="tag q">{e(status)}</span>'
    elif status in ("OUT", "IR"):
        tag = f' <span class="tag out">{e(status)}</span>'
    return (
        f'<span class="posl">{e(row.get("Position", ""))}</span>{pn(row.get("Name", ""), row.get("Team", ""), wb.pid(row))}'
        f"{tag}" + (f'<span class="meta"> {meta}</span>' if meta else "")
    )


def game_meta(row: dict) -> str:
    opp = str(row.get("Opp") or "")
    return e(f"{row.get('Team', '')} v {opp}" if opp else str(row.get("Team", "")))


# -- your cash lineup --------------------------------------------------------------------------------------


def _cash_entry(lineup: dict) -> bool:
    return any(
        any(f.lower() in str(c.get("format") or c.get("name") or "").lower() for f in CASH_FORMATS)
        for c in lineup.get("contests") or []
    )


def your_cash_lineup(inp: WeekInputs) -> tuple[list[str], str]:
    """(the names, where they came from): the Default cash lineup, else the newest round's first cash build,
    else the cash lineup he entered; ([], "") when there is none."""
    d = (inp.cash_core or {}).get("default") or {}
    if d.get("names"):
        return list(d["names"]), "the Default cash lineup"
    cash = builds_of(inp, "cash")
    if cash and cash[0].get("names"):
        return list(cash[0]["names"]), "cash build 1 of the newest round"
    for lu in (inp.entered or {}).get("lineups") or []:
        if _cash_entry(lu) and lu.get("names"):
            return list(lu["names"]), "the cash lineup you entered"
    return [], ""


def chalk_not_on(inp: WeekInputs, names: list[str]) -> list[dict] | None:
    """The 30%+ CashOwn players not in `names`, most owned first; None before CashOwn publishes."""
    mine = set(names)
    published = False
    out = []
    for r in inp.edge or []:
        own = num(r.get("CashOwn"))
        if own is None:
            continue
        published = True
        if own >= CHALK_THRESHOLD and r.get("Name") not in mine and wb.avail(r) not in ("OUT", "IR"):
            out.append(r)
    if not published:
        return None
    return sorted(out, key=lambda r: -(num(r.get("CashOwn")) or 0))


CHALK_HOW = (
    "Every player with CashOwn (the double-up field's projected ownership) 30% or more who is not in your cash "
    "lineup (the Default, else the newest round's first cash build, else the cash lineup you entered) and is "
    "not OUT or IR. From: EdgeRaw CashOwn / CalPts / Salary / Avail, data/week/inputs/*-cash_core.json."
)
DIGEST_HOW = (
    "Games ranked by the Vegas total, the implied team totals from the spread; injuries are OUT / D / Q among "
    "each team's likely starters (the top earners by salary at each position, IR left out), with who gains (the "
    "plays model's teammate bump); a line move is 2+ points of total or 1+ point of spread since the "
    "week's opening snapshot. Best plays: the board's top three per position, shown by CalPts. "
    "From: EdgeRaw Total / Spread / TotMove / Avail / Salary / CalPts."
)
FADES_HOW = (
    "FADE↓ is the signal (its record from Model Check); avoid in cash is the plays model's cash AVOID (Shadow: "
    "the bottom 30% by Cash % within the position), held back where its label is under review. From: EdgeRaw "
    "flags, data/week/inputs/*-calls.json."
)


def chalk_html(
    inp: WeekInputs,
    *,
    sid: str = CHALK_ID,
    prev: dict[str, float] | None = None,
    ds: list | None = None,
    live: bool = False,
    lineup: tuple[list[str], str] | None = None,
    controls: bool = True,
    foot: str | None = None,
    how: str = CHALK_HOW,
) -> str:
    """The card. `prev` (Sunday) maps a name to his CashOwn at the baseline, for the move column; `ds` are the
    page's chalk decisions (`week_board.decisions`): a row with one carries its Play / Fade (`cash: Name` on
    Always use / Never use), the same answer the old decisions block gave. `lineup` is (names, where) when the
    caller knows the cash lineup (Sunday: the Lineups tab's cash lineups); `controls=False` leaves out the
    Play / Fade column (Sunday writes no sheet) and `foot` replaces the footer."""
    from dfs.week_top import _cash_controls

    by_id = {wb.pid(d.row): d for d in ds or [] if d.kind == "chalk" and d.row}
    names, where = lineup if lineup is not None else your_cash_lineup(inp)
    rows = chalk_not_on(inp, names)
    title = "Chalk you're not on"
    if not names:
        return card(sid, title, '<p class="empty">No cash lineup yet: it compares the chalk with one.</p>')
    if rows is None:
        return card(
            sid, title, '<p class="empty">Ownership is not published yet (usually Thursday to Friday).</p>'
        )
    if not rows:
        return card(
            sid,
            title,
            f'<p class="empty">Every player projected at 30%+ cash ownership is in {e(where)}.</p>',
            sub=f"against {e(where)}",
        )
    ranks = wb.game_rank(inp.edge or [])
    body = []
    for r in rows:
        own = num(r.get("CashOwn")) or 0
        move = ""
        if prev is not None and r.get("Name") in prev:
            d = (own - prev[str(r["Name"])]) * 100
            move = f"{d:+.0f}" if abs(d) >= 1 else ""
        body.append(
            f'<tr data-id="{e(wb.pid(r))}"><td class="who">{player(r, game_meta(r))}'
            f'<div class="meta">{e(wb.why_facts(r, ranks))}</div></td>'
            f'<td class="n hm">{_money(r.get("Salary"))}</td><td class=n><b>{_f(r.get("CalPts"))}</b></td>'
            f'<td class="n"><b>{pct(own)}</b></td>'
            + (f'<td class="n">{e(move)}</td>' if prev is not None else "")
            + (
                f'<td class="acts"><span class="opts">{_cash_controls(by_id.get(wb.pid(r)), live)}</span></td>'
                if controls
                else ""
            )
            + "</tr>"
        )
    own_chip = chip_html(*TRUST["plays_own"])
    head = (
        '<tr><th>Player</th><th class="n hm">Salary</th><th class="n">Proj</th>'
        f'<th class="n">Cash own {own_chip}</th>'
        + ('<th class="n">since Sat night</th>' if prev is not None else "")
        + ("<th></th>" if controls else "")
        + "</tr>"
    )
    return card(
        sid,
        title,
        f'<div class="sec-b flush"><table class="t">{head}{"".join(body)}</table></div>',
        sub=f"30%+ projected cash ownership, not in {e(where)}",
        foot=foot
        if foot is not None
        else "The double-up field is expected to play them and your lineup does not. Play writes "
        "<code>cash: Name</code> on Always use, Fade on Never use (cash builds only); then rerun cash.",
        cls="dec",
        how=how,
    )


# -- the slate digest ---------------------------------------------------------------------------------------


def _game_rows(games: list[week_plays.Game]) -> str:
    out = []
    for g in games:
        imp = " · ".join(f"{t} {v:.1f}" for t, v in sorted(g.implied.items(), key=lambda kv: -kv[1]))
        tmove = _signed(g.tot_move)
        spread = f"{g.favorite} {g.spread:+.1f}" if g.spread is not None else "-"
        smove = _signed(g.spread_move)
        wx = f' <span class="tag">{e(g.weather)}</span>' if g.weather else ""
        out.append(
            f'<tr><td class="who"><a class="tm" href="#" data-game="{e(g.key)}" data-team="{e(g.away)}">'
            f"{e(g.away)} @ {e(g.home)}</a>{wx}<div class=meta>{e(g.kickoff)}</div></td>"
            f"<td class=n><b>{'-' if g.total is None else f'{g.total:g}'}</b>"
            + (f'<div class="meta">{e(tmove)} since open</div>' if tmove else "")
            + f"</td><td class=n>{e(spread)}"
            + (f'<div class="meta">{e(smove)}</div>' if smove else "")
            + f'</td><td class="hm">{e(imp)}</td></tr>'
        )
    return "".join(out)


def news_items(inp: WeekInputs, games: list[week_plays.Game]) -> list[str]:
    """The key injuries (per game, `week_plays.key_injuries`), who gains (the plays calls' teammate bump),
    and the lines that moved, one line each."""
    from dfs import week_views

    items = []
    gains: dict[str, list[str]] = {}
    for b in week_views.bumps(inp)[:12]:
        gains.setdefault(b.source, []).append(f"{pn(b.name, b.team)} +{b.bump:.1f}")
    for g in games:
        for name, pos, team, status in g.injuries:
            cls = "out" if status in ("OUT", "IR") else "q"
            gain = gains.get(name)
            items.append(
                f'<li><span class="tag {cls}">{e(status)}</span> {pn(name, team)} <span class="meta">{e(pos)} '
                f"{e(team)}</span>"
                + (f'<span class="r meta">gains: {", ".join(gain[:2])}</span>' if gain else "")
                + "</li>"
            )
    for g in games:
        moves = []
        if g.moved and g.tot_move is not None:
            moves.append(f"total {g.tot_move:+.1f} to {g.total:g}")
        if g.spread_move is not None and abs(g.spread_move) >= MOVE_SPREAD and g.spread is not None:
            moves.append(f"spread {g.spread_move:+.1f} to {g.favorite} {g.spread:+.1f}")
        if moves:
            items.append(
                f'<li><span class="tag">line</span> {e(g.away)} @ {e(g.home)}'
                f'<span class="r">{e("; ".join(moves))}</span></li>'
            )
    return items


def best_plays(board: wb.Board) -> dict[str, list[wb.BoardRow]]:
    """The board's top rows per position (its own order: most roles, then projection), fades left out."""
    return {
        pos: sorted(
            [r for r in board.by_pos.get(pos, []) if not r.fade][:BEST_PER_POS], key=lambda r: -r.calpts
        )
        for pos in POSITIONS
        if board.by_pos.get(pos)
    }


def digest_html(inp: WeekInputs, board: wb.Board, *, open_: bool = True) -> str:
    games = week_plays.slate(inp.edge)
    if not games:
        return card(DIGEST_ID, "The slate", '<p class="empty">No slate synced yet.</p>')
    news = news_items(inp, games)
    best = best_plays(board)
    proj_chip = chip_html(*TRUST["plays_value"])
    best_html = "".join(
        f'<div><div class="grp">{e(pos)}</div>'
        + "".join(
            f'<div class="prow" data-id="{e(r.id)}"><span class="nm">{pn(r.name, r.row.get("Team", ""), r.id)}'
            f'<span class="meta"> {game_meta(r.row)} · '
            f"{_money(r.row.get('Salary'))}</span></span><span class=r>{r.calpts:.1f}</span></div>"
            for r in rows
        )
        + "</div>"
        for pos, rows in best.items()
    )
    pending = (
        '<p class="meta" style="padding:0 16px">Ownership is not published yet (usually Thursday to Friday): '
        "the plays are ranked without it.</p>"
        if board.cash_pending
        else ""
    )
    body = (
        '<div class="digest"><div><h3>Games, by total</h3>'
        '<table class="t"><tr><th>Game</th><th class="n">Total</th><th class="n">Spread</th>'
        f'<th class="hm">Implied</th></tr>{_game_rows(games)}</table></div>'
        "<div><h3>Injuries and line moves</h3>"
        + (f'<ul class="dl">{"".join(news)}</ul>' if news else '<p class="meta">Nothing flagged.</p>')
        + f'</div></div><div class="sec-h" style="padding-top:4px"><h2>Best plays</h2>'
        f'<p class="sub">three per position, the projection; the rest is in the drawer</p>'
        f'<span class="end">{proj_chip}</span></div>{pending}<div class="best">{best_html}</div>'
    )
    top = games[0]
    line = f"{len(games)} games; the top total {top.away} @ {top.home} {top.total:g}" if top.total else ""
    if open_:
        return card(
            DIGEST_ID,
            "The slate",
            body,
            sub=e(line),
            foot="Information: the lines and injuries from the last sync; the board's plays (More › Board).",
            how=DIGEST_HOW,
        )
    return fold(DIGEST_ID, "The slate", line, body, how=DIGEST_HOW)


# -- fades ----------------------------------------------------------------------------------------------------


def fades_html(inp: WeekInputs, board: wb.Board, *, open_: bool = True) -> str:
    """FADE↓ (the signal, with its record) and the plays model's AVOID in cash (Shadow, held back where
    `plays.held_back` says), one row per player."""
    from dfs import plays, week_views

    fade_rows = [r for pos in POSITIONS for r in board.by_pos.get(pos, []) if r.fade]
    avoid = [
        c for c in week_views.calls_of(inp) if c.get("cash") == "AVOID" and not week_views.held(c, "cash")
    ]
    avoid.sort(key=lambda c: POSITIONS.index(c["position"]) if c.get("position") in POSITIONS else 9)
    by_name = {(str(r.get("Name")), str(r.get("Team", ""))): r for r in inp.edge or []}
    rows = []
    for r in fade_rows:
        rows.append(
            f'<tr data-id="{e(r.id)}"><td class="who">{player(r.row, game_meta(r.row))}'
            f'<div class="meta">{e(r.why)}</div></td><td><span class="tag">FADE↓</span></td>'
            f"<td class=n>{_money(r.row.get('Salary'))}</td><td class=n>{r.calpts:.1f}</td></tr>"
        )
    for c in avoid[:12]:
        row = by_name.get((str(c["name"]), str(c.get("team", ""))))
        if row is None:
            continue
        rows.append(
            f'<tr data-id="{e(wb.pid(row))}"><td class="who">{player(row, game_meta(row))}'
            f'<div class="meta">{e(c.get("cash_reason") or c.get("reason") or "")}</div></td>'
            f'<td><span class="tag">avoid in cash</span></td>'
            f"<td class=n>{_money(row.get('Salary'))}</td><td class=n>{_f(row.get('CalPts'))}</td></tr>"
        )
    fade_rec = week_plays.record_line(wb.FADE, inp.signal_table, inp.scored_weeks)
    avoid_rec = week_views._record_line("AVOID", "cash", getattr(inp, "calls_record", None))
    foot = f"{e(fade_rec)}. Avoid in cash (the plays model, Shadow): {e(avoid_rec)}"
    if any(week_views.held(c, "cash") for c in week_views.calls_of(inp) if c.get("cash") == "AVOID"):
        foot += f". Some avoid calls are held back ({e(plays.HOLD_TEXT)})"
    head = '<tr><th>Player</th><th>Call</th><th class="n">Salary</th><th class="n">Proj</th></tr>'
    body = (
        f'<div class="sec-b flush"><table class="t">{head}{"".join(rows)}</table></div>'
        if rows
        else '<p class="empty">No fades this week.</p>'
    )
    line = f"{len(fade_rows)} FADE↓, {len(avoid)} avoid in cash"
    if open_:
        return card(FADES_ID, "Fades", body, sub=e(line), foot=foot, how=FADES_HOW)
    return fold(FADES_ID, "Fades", line, body + f'<div class="sec-f">{foot}</div>', how=FADES_HOW)


# -- someone else, for an open spot ---------------------------------------------------------------------------


def fits(slot: str, position: str) -> bool:
    return position in SKILL if slot == "FLEX" else position == slot.rstrip("0123456789")


def spot_options(inp: WeekInputs, spot: dict, default: dict) -> list[dict]:
    """Every slate player who could take `spot` in the Default: fits the slot, costs at most the Default's
    player's salary plus what the Default leaves, not in the Default, not OUT; each with his difference
    against the Default's player (points, salary freed, cash ownership). Most projected first."""
    base = spot.get("default") or {}
    base_row = next((r for r in inp.edge or [] if r.get("Name") == base.get("name")), None)
    base_pts = num(base_row.get("CalPts")) if base_row else num(base.get("projection"))
    base_sal = num(base.get("salary")) or (num(base_row.get("Salary")) if base_row else None) or 0
    base_own = num(base.get("cash_own"))
    cap = base_sal + (num(default.get("left")) or 0)
    taken = set(default.get("names") or [])
    out = []
    for r in inp.edge or []:
        sal = num(r.get("Salary"))
        if (
            sal is None
            or not r.get("Name")
            or r.get("Name") in taken
            or not fits(str(spot.get("slot", "")), str(r.get("Position", "")))
            or sal > cap
            or wb.avail(r) in ("OUT", "IR", "D")
        ):
            continue
        pts = num(r.get("CalPts"))
        own = num(r.get("CashOwn"))
        out.append(
            {
                "id": wb.pid(r),
                "name": str(r["Name"]),
                "team": str(r.get("Team", "")),
                "opp": str(r.get("Opp", "")),
                "pos": str(r.get("Position", "")),
                "status": wb.avail(r),
                "d_points": None if pts is None or base_pts is None else round(pts - base_pts, 1),
                "d_salary": int(base_sal - sal),
                "cash_own": own,
                "d_own": None if own is None or base_own is None else round(own - base_own, 3),
            }
        )
    out.sort(key=lambda o: -(o["d_points"] if o["d_points"] is not None else -99))
    return out


def fits_spot(inp: WeekInputs, spot: dict, default: dict, player_id: str) -> dict | None:
    """The option for `player_id` when he may take the spot, else None (the server's check on a pick)."""
    return next((o for o in spot_options(inp, spot, default) if o["id"] == player_id), None)
