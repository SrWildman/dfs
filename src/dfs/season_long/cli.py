"""`dfs season ...`: Sam's season-long leagues (Sleeper), on their own Season sheet.

Every command takes `--league <name>` or `--all` (leagues are in `season.toml`). Nothing here touches
DraftKings or the weekly DFS sheet. Heavy imports stay inside the commands.
"""

from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console
from rich.markup import escape

from dfs.prompt_missing import PromptingGroup

season_app = typer.Typer(
    help="Season-long leagues: rest-of-season values, a trade analyzer and finder, the Season sheet.",
    cls=PromptingGroup,
)
console = Console()

LEAGUE_HELP = "A league's short name from season.toml (e.g. Bourbon)."
ALL_HELP = "Every league in season.toml."


def _say(message: str) -> None:
    console.print(f"[dim]{escape(message)}[/dim]")


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]FAIL[/red] {escape(message)}")
    return typer.Exit(1)


def _print(lines: list[str]) -> None:
    for line in lines:
        console.print(line, markup=False, highlight=False, soft_wrap=True)


def _config():
    from dfs.season_long.config import SeasonConfigError, load_season_config

    try:
        return load_season_config()
    except SeasonConfigError as e:
        raise _fail(str(e)) from e


def _leagues(cfg, league: str | None, all_leagues: bool, *, default_all: bool = False):
    from dfs.season_long.config import SeasonConfigError, select_leagues

    try:
        chosen = select_leagues(cfg, league, all_leagues or (default_all and not league))
    except SeasonConfigError as e:
        raise _fail(str(e)) from e
    skipped = [lc for lc in chosen if lc.platform != "sleeper"]
    for lc in skipped:
        _say(f"{lc.name}: {lc.platform} leagues come in phase 2; skipped.")
    return [lc for lc in chosen if lc.platform == "sleeper"]


def _client(cfg, sheet_id: str | None):
    from dfs.season_long.sheet_season import client_for

    sid = sheet_id or cfg.sheet_id
    if not sid:
        raise _fail("No Season sheet: set sheet_id in season.toml (or pass --sheet-id).")
    client = client_for(sid)
    title, url = client.describe()
    console.print(f"Sheet: {escape(title)} ({url})")
    return client


def _run(lc, cfg, table, filters=None, top: int = 20, find: bool = True, waive: bool = True, targets=None):
    """One league's view, evaluator, market and ideas (`season_tabs.LeagueRun`). With `targets` (the buy
    list), the targeted ideas are ADDED to the standard ones, never instead of them."""
    from dfs.season_long import finder as fd
    from dfs.season_long import league_view, objective
    from dfs.season_long import season_tabs as st
    from dfs.season_long import trade as tr

    view = league_view.build(lc, cfg, table)
    ev = objective.Evaluator(view)
    market = tr.build_market(view, ev)
    found: list[str] = []
    ideas = fd.find_trades(view, ev, market, filters or fd.Filters(), top=top, notes=found) if find else []
    if find and targets:
        aimed = fd.pick_targeted(
            fd.find_trades(view, ev, market, fd.Filters(targets=list(targets)), top=200, notes=found)
        )
        ideas = fd.merge_targeted(ideas, aimed)
    run = st.LeagueRun(
        view, ev, market, ideas, tr.free_move(view, ev, view.league.my_roster_id, tr.free_agents(view))
    )
    run.notes += found
    lg = view.league
    from dfs.season_long import consensus_check

    consensus_check.archive_ours(lg.name, view.ros.summary)
    run.check = consensus_check.score(lg.name, lg.scoring, lg.season, lg.last_scored_week, view.table).line()
    from dfs.season_long import activity as act
    from dfs.season_long import lineups as lu

    run.activity = act.manager_activity(view)
    run.schedule = act.playoff_schedule(view)
    inactive = {rid for rid in run.activity if act.is_inactive(run.activity, rid, lg.current_week)}
    for idea in run.ideas:
        if idea.analysis.partner in inactive:
            last = run.activity[idea.analysis.partner].last_move
            idea.why += f"; they look inactive (last move Week {last})" if last else "; they look inactive"
    run.plan = lu.plan(view, ev)
    run.alerts = lu.check(view, ev, _news())
    run.streams = lu.streams(view, ev)
    if waive:
        from dfs.season_long import waivers as wv

        run.pickups = wv.rank_pickups(view, ev, top=5, inactive=inactive)
        run.cuffs = wv.handcuffs(view, ev)
        run.chop = wv.chop_plan(view, ev)
    if lg.raw_settings.get("league_average_match"):
        run.notes.append(st.MEDIAN_NOTE)
    if lg.raw_settings.get("divisions") and not lg.chopped:
        run.notes.append(f"{lg.raw_settings['divisions']} divisions: playoff odds ignore division winners.")
    if lg.chopped:
        run.notes.append("Chopped: last man standing; the lowest score each week is eliminated.")
    return run


_NEWS: list = []


def _news():
    """ESPN's game-day injury feed (the one DFS late swap reads), once per command; None when unreachable."""
    from dfs.late_swap_news import NewsError, fetch_news

    if not _NEWS:
        try:
            _NEWS.append(fetch_news())
        except NewsError as e:
            _say(f"ESPN's injury feed could not be read ({e}); the check uses Sleeper's statuses only.")
            _NEWS.append(None)
    return _NEWS[0]


def _table():
    from dfs.season_long import ros
    from dfs.season_long import sleeper_league as sl

    return ros.player_table(sl.fetch_players())


def _filters(get, give, keep, partner, shapes, max_loss, lopsided=False, players=None):
    from dfs.season_long import finder as fd
    from dfs.season_long import trade as tr

    loss = tr.DEFAULT_MAX_CONSENSUS_LOSS
    if max_loss:
        text = str(max_loss).strip().rstrip("%")
        try:
            loss = float(text) / (100 if "%" in str(max_loss) or float(text) > 1 else 1)
        except ValueError as e:
            raise _fail(f"--max-consensus-loss {max_loss!r}: write it like 10%.") from e
    try:
        return fd.Filters(
            get=fd.normalize_terms(get),
            give=fd.normalize_terms(give),
            keep=fd.normalize_terms(keep),
            partner=partner or None,
            shapes=fd.parse_shapes(shapes),
            max_consensus_loss=loss,
            show_lopsided=bool(lopsided),
            players=fd.normalize_terms(players),
        )
    except tr.TradeError as e:
        raise _fail(str(e)) from e


def _summary(run) -> list[str]:
    from dfs.season_long import finder as fd
    from dfs.season_long import season_tabs as st

    v, lg = run.view, run.view.league
    o = run.ev.base()[lg.my_roster_id]
    if lg.chopped:
        odds = (
            f"survive Week {lg.remaining_weeks[0]} {o.p_survive_now:.0%}, expected weeks "
            f"{o.expected_weeks:.1f}, "
            f"last man standing {o.p_last_standing:.1%}"
        )
    else:
        odds = f"playoffs {o.p_playoffs:.0%}"
    lines = [
        f"{lg.name} ({lg.title}): {lg.me.label}; {odds}; needs {st.needs_text(run, lg.my_roster_id)}",
        f"  free move first: {st.free_move_text(run)}",
        f"  FAAB: ${lg.me.faab_left}; $1 = {run.market.points_per_dollar:.2f} pts ({run.market.faab_source})",
    ]
    lines += [f"  {n}" for n in run.notes]
    for i, idea in enumerate(run.ideas, 1):
        lines.append(f"  {i:2d}. {fd.idea_line(v, idea)}")
    if not run.ideas:
        lines.append("  no realistic trade found")
    return lines


@season_app.command("sync")
def season_sync(
    league: str = typer.Option(None, "--league", help=LEAGUE_HELP),
    all_leagues: bool = typer.Option(False, "--all", help=ALL_HELP + " (the default)."),
    top: int = typer.Option(20, "--top", help="Trade ideas kept per league."),
    open_search: bool = typer.Option(
        False, "--open", help="Leave out the extra ideas for season.toml's buy list and your weakest spot."
    ),
    no_sheet: bool = typer.Option(False, "--no-sheet", help="Print only; write nothing."),
    sheet_id: str = typer.Option(None, "--sheet-id", help="A Season sheet other than season.toml's."),
) -> None:
    """Sync every league from Sleeper, rebuild values and trade ideas, and write the Season sheet (a few
    minutes for four leagues). With a buy list in season.toml, ideas for it and each league's weakest spot
    are added to the standard ones (tagged "target"), unless --open."""
    from dfs.season_long import sheet_season as ss

    cfg = _config()
    chosen = _leagues(cfg, league, all_leagues, default_all=True)
    client = None if no_sheet else _client(cfg, sheet_id)
    table = _table()
    runs = []
    for lc in chosen:
        _say(f"{lc.name}: syncing")
        try:
            targets = None if open_search or not cfg.buy_list else list(cfg.buy_list)
            runs.append(_run(lc, cfg, table, top=top, targets=targets))
        except Exception as e:  # noqa: BLE001 -- one league failing never stops the others
            console.print(f"[red]FAIL[/red] {escape(lc.name)}: {escape(str(e))}")
            continue
        _print(_summary(runs[-1]))
    if client is not None and runs:
        if len(runs) < len(cfg.leagues):
            _say("Only some leagues ran: the sheet's raw tabs, Overview and My Teams show just these.")
        _print([f"[sheet] {line}" for line in ss.write_sync(client, runs)])


@season_app.command("trades")
def season_trades(
    league: str = typer.Option(None, "--league", help=LEAGUE_HELP),
    all_leagues: bool = typer.Option(False, "--all", help=ALL_HELP),
    get: str = typer.Option(None, "--get", help='Land a player or a position ("Bijan", "RB").'),
    give: str = typer.Option(
        None, "--give", help='Sell a player or move a position ("Garrett Wilson", "WR").'
    ),
    keep: str = typer.Option(None, "--keep", help='Untouchables, never offered ("Nacua, Love").'),
    players: str = typer.Option(
        None,
        "--players",
        help='Trades moving any of these either way ("Hubbard, Coleman"): yours are sells, the rest buys.',
    ),
    partner: str = typer.Option(None, "--partner", help='One team only ("Team 4", a team or manager name).'),
    shapes: str = typer.Option(
        None, "--shapes", help="You give - you get: 1-1, 2-1, 1-2, 2-2 (default all)."
    ),
    max_consensus_loss: str = typer.Option(
        None, "--max-consensus-loss", help="How far below fair (by the market) the partner may come out (5%)."
    ),
    top: int = typer.Option(20, "--top", help="Ideas to keep."),
    lopsided: bool = typer.Option(
        False,
        "--lopsided",
        help="Also show ideas the market has over 125% for the partner (with the FAAB to ask back).",
    ),
    open_search: bool = typer.Option(
        False, "--open", help="With no filter, leave out the extra buy-list / weakest-spot ideas."
    ),
    from_sheet: bool = typer.Option(
        False,
        "--from-sheet",
        help="Use the Trade Finder tab's targeting box (also used when no filter is given).",
    ),
    no_sheet: bool = typer.Option(False, "--no-sheet", help="Print only; write nothing."),
    sheet_id: str = typer.Option(None, "--sheet-id", help="A Season sheet other than season.toml's."),
) -> None:
    """Find realistic trades: you gain, the partner gains by our model, and the market calls it fair for
    them. Writes this league's ideas onto the Trade Finder tab."""
    from dfs.season_long import sheet_season as ss
    from dfs.season_long import trade as tr

    cfg = _config()
    client = None if no_sheet else _client(cfg, sheet_id)
    typed = [get, give, keep, partner, shapes, max_consensus_loss, lopsided, players]
    if client is not None and (from_sheet or not any(typed)):
        box = ss.read_box(client)
        if box:
            _say("using the Trade Finder's targeting box: " + "; ".join(f"{k}={v}" for k, v in box.items()))
            league = league or box.get("League")
            get, give, keep = box.get("Get"), box.get("Give"), box.get("Keep")
            partner, shapes, max_consensus_loss = (
                box.get("Partner"),
                box.get("Shapes"),
                box.get("Max consensus loss"),
            )
            lopsided = lopsided or box.get("Show lopsided", "").strip().lower() in {"yes", "y", "true", "1"}
    filters = _filters(get, give, keep, partner, shapes, max_consensus_loss, lopsided, players)
    targets = None
    if not any([get, give, partner, players]) and cfg.buy_list and not open_search:
        targets = list(cfg.buy_list)
    chosen = _leagues(cfg, league, all_leagues)
    table = _table()
    runs = []
    for lc in chosen:
        try:
            runs.append(_run(lc, cfg, table, filters, top, targets=targets))
        except tr.TradeError as e:
            raise _fail(f"{lc.name}: {e}") from e
        _print(_summary(runs[-1]))
    if client is not None and runs:
        _print([f"[sheet] {ss.write_ideas(client, runs)}"])


@season_app.command("waivers")
def season_waivers(
    league: str = typer.Option(None, "--league", help=LEAGUE_HELP),
    all_leagues: bool = typer.Option(False, "--all", help=ALL_HELP + " (the default)."),
    player: str = typer.Option(
        None, "--player", help='One free agent: what he adds and what to bid ("Bo Nix").'
    ),
    top: int = typer.Option(5, "--top", help="Pickups per league."),
    no_sheet: bool = typer.Option(False, "--no-sheet", help="Print only; write nothing."),
    sheet_id: str = typer.Option(None, "--sheet-id", help="A Season sheet other than season.toml's."),
) -> None:
    """Free agents ranked by what each adds to your team (the drop that pays for him, this week apart), with
    a FAAB bid guide from the league's own claims, handcuffs to stash, and a chopped league's bid plan.
    Writes the Waivers tab (sync also does)."""
    from dfs.season_long import finder as fd
    from dfs.season_long import season_tabs as st
    from dfs.season_long import sheet_season as ss
    from dfs.season_long import trade as tr
    from dfs.season_long import waivers as wv

    cfg = _config()
    chosen = _leagues(cfg, league, all_leagues, default_all=True)
    client = None if (no_sheet or player) else _client(cfg, sheet_id)
    table = _table()
    runs = []
    for lc in chosen:
        run = _run(lc, cfg, table, find=False, waive=player is None)
        v = run.view
        if player:
            try:
                hits = fd._resolve([player], tr.free_agents(v), v)[0]
            except tr.TradeError as e:
                raise _fail(f"{lc.name}: {e} (only free agents can be bid on)") from e
            picks = wv.rank_pickups(v, run.ev, only=list(hits))
            _print([f"{lc.name}: {wv.pickup_line(v, p)}" for p in picks])
            continue
        lines = [f"{lc.name}: FAAB ${v.league.me.faab_left}; free move first: {st.free_move_text(run)}"]
        lines += [f"  {i}. {wv.pickup_line(v, p)}" for i, p in enumerate(run.pickups[:top], 1)]
        lines += [f"  handcuff: {wv.handcuff_text(v, h)}" for h in run.cuffs]
        if run.chop is not None:
            c = run.chop
            lines.append(
                f"  bid plan: {c.team} is {c.chop_risk:.0%} likely chopped this week; you have ${c.faab}"
            )
            for pid, gain, worth, price in c.picks:
                lines.append(
                    f"    {v.name(pid)}: survival {gain:+.3f}, worth ${worth}, the league paid ${price:.0f} "
                    f"for that pick -> {c.bid(worth, price)}"
                )
        _print(lines)
        runs.append(run)
    if client is not None and runs:
        _print([f"[sheet] {ss.write_waivers(client, runs)}"])


@season_app.command("lineups")
def season_lineups(
    league: str = typer.Option(None, "--league", help=LEAGUE_HELP),
    all_leagues: bool = typer.Option(False, "--all", help=ALL_HELP + " (the default)."),
    no_sheet: bool = typer.Option(False, "--no-sheet", help="Print only; write nothing."),
    sheet_id: str = typer.Option(None, "--sheet-id", help="A Season sheet other than season.toml's."),
) -> None:
    """This week's recommended lineup per league: each starter's floor-ceiling, close calls decided by the
    matchup (win probability, the median game, or survival in a chopped league), K/DEF streams. Sam sets them
    in Sleeper by hand; nothing is written to Sleeper. Writes My Teams."""
    from dfs.season_long import lineups as lu
    from dfs.season_long import sheet_season as ss

    cfg = _config()
    chosen = _leagues(cfg, league, all_leagues, default_all=True)
    client = None if no_sheet else _client(cfg, sheet_id)
    table = _table()
    runs = []
    for lc in chosen:
        run = _run(lc, cfg, table, find=False, waive=False)
        lines = lu.plan_lines(run.view, run.plan)
        for pos, picks in run.streams.items():
            lines.append(f"  stream {pos}: " + ", ".join(
                f"{n if n.startswith('yours') else run.view.name(n)} {w}" for n, w in picks))  # fmt: skip
        lines += [
            f"  {a.cue} {a.slot} {a.player}: {a.reason}" + (f" -> {a.swap}" if a.swap else "")
            for a in run.alerts
        ]
        _print(lines)
        runs.append(run)
    if client is not None and runs:
        if len(runs) < len([lc for lc in cfg.leagues if lc.platform == "sleeper"]):
            _say("Only some leagues ran: My Teams keeps the last full sync (run with --all to rewrite it).")
        else:
            _print([f"[sheet] {ss.write_my_teams(client, runs)}"])


@season_app.command("check")
def season_check(
    league: str = typer.Option(None, "--league", help=LEAGUE_HELP),
    all_leagues: bool = typer.Option(False, "--all", help=ALL_HELP + " (the default)."),
) -> None:
    """The Sunday check across every league: your lineups as set in Sleeper against byes, empty slots and
    OUT / Doubtful / Questionable (Sleeper's status plus ESPN's game-day feed), each with the swap to make.
    Read-only: writes nothing anywhere."""
    from dfs.season_long import league_view, objective
    from dfs.season_long import lineups as lu

    cfg = _config()
    table = _table()
    clean = []
    for lc in _leagues(cfg, league, all_leagues, default_all=True):
        view = league_view.build(lc, cfg, table)
        alerts = lu.check(view, objective.Evaluator(view), _news())
        if not alerts:
            clean.append(lc.name)
        for a in alerts:
            mark = "[red]SWAP[/red]" if a.cue == "SWAP" else "[yellow]LOOK[/yellow]"
            swap = f" -> {escape(a.swap)}" if a.swap else ""
            console.print(
                f"{mark} {escape(lc.name)} {escape(a.slot)} {escape(a.player)}: {escape(a.reason)}{swap}"
            )
    if clean:
        console.print(f"[green]ok[/green] {', '.join(clean)}: every starter is set, playing and healthy")


@season_app.command("trade")
def season_trade(
    give: str = typer.Argument(..., help='What you give: "Garrett Wilson, Tuten + $10 FAAB".'),
    get: str = typer.Argument(..., help='What you get: "Bijan Robinson".'),
    league: str = typer.Option(None, "--league", help=LEAGUE_HELP),
    partner: str = typer.Option(None, "--partner", help="The partner, when what you get is only FAAB."),
    no_sheet: bool = typer.Option(False, "--no-sheet", help="Print only; write nothing."),
    sheet_id: str = typer.Option(None, "--sheet-id", help="A Season sheet other than season.toml's."),
) -> None:
    """Analyze one trade: each side's change in team value (regular season and playoffs, or survival in a
    chopped league), the market's view, and a one-line verdict. Writes the Analyzer tab."""
    from dfs.season_long import season_tabs as st
    from dfs.season_long import sheet_season as ss
    from dfs.season_long import trade as tr

    cfg = _config()
    chosen = _leagues(cfg, league, False)
    if len(chosen) != 1:
        raise _fail("Name one league with --league.")
    client = None if no_sheet else _client(cfg, sheet_id)
    run = _run(chosen[0], cfg, _table(), find=False)
    try:
        a = tr.analyze(run.view, run.ev, run.market, give, get, partner)
    except tr.TradeError as e:
        raise _fail(str(e)) from e
    lines = tr.describe(run.view, a, run.ev)
    _print(lines)
    if client is not None:
        rows = st.analyzer_rows(run.view, lines, a, datetime.now().strftime("%a %b %d %H:%M"))
        _print([f"[sheet] {ss.write_analyzer(client, rows)}"])


@season_app.command("ros")
def season_ros(
    league: str = typer.Option(None, "--league", help=LEAGUE_HELP),
    all_leagues: bool = typer.Option(False, "--all", help=ALL_HELP),
    check: bool = typer.Option(False, "--check", help="The held-out accuracy check of the blend."),
    top: int = typer.Option(25, "--top", help="Players to list."),
) -> None:
    """Rest-of-season points in a league's scoring (top players), or with --check the accuracy of the
    blend against the season and last-3 averages on this season's finished weeks."""
    from dfs.season_long import ros
    from dfs.season_long import sleeper_league as sl

    cfg = _config()
    table = _table()
    for lc in _leagues(cfg, league, all_leagues):
        lg = sl.load_league(lc.name, lc.league_id, lc.username)
        if check:
            r = ros.accuracy_check(lg.scoring, lg.season, lg.last_scored_week, table)
            lines = [f"{lc.name}: Weeks 1-{r.through}, {r.players} players, per-game error (lower is better)"]
            lines += [f"  {m:20s} {v:.2f}" for m, v in r.mae.items()]
            lines.append(f"  blend k = {r.k:g} (weight on the season rate = games / (games + k))")
            if r.consensus_note:
                lines.append(f"  {r.consensus_note}")
            _print(lines)
            continue
        result = ros.build_ros(lg, table, ir_weeks_out=cfg.ir_weeks_out, playoff_weight=cfg.playoff_weight)
        s = result.summary.sort_values("ros_weighted", ascending=False).head(top)
        _print(
            [
                f"{lc.name}: rest of season (Weeks {lg.remaining_weeks[0]}-{lg.last_week}), k = "
                f"{result.blend_k:g}"
            ]
        )
        _print(
            [
                f"  {r['name']:24s} {r['position']:3s} {r['team']:3s} {r['ros_weighted']:7.1f}"
                for _, r in s.iterrows()
            ]
        )


@season_app.command("setup")
def season_setup(
    sheet_id: str = typer.Option(None, "--sheet-id", help="A Season sheet other than season.toml's."),
) -> None:
    """Build (or rebuild) the Season sheet's tabs, pickers, targeting box and protection. Keeps what is
    typed in the pickers and the box. Then run `dfs season sync`."""
    from dfs.season_long import sheet_season as ss

    cfg = _config()
    client = _client(cfg, sheet_id)
    _print(ss.setup(client, [lc.name for lc in cfg.leagues if lc.platform == "sleeper"]))
    console.print("[green]OK[/green] now run `dfs season sync`, then `dfs season doctor`")


@season_app.command("doctor")
def season_doctor(
    sheet_id: str = typer.Option(None, "--sheet-id", help="A Season sheet other than season.toml's."),
) -> None:
    """Read-only structure check of the Season sheet."""
    from dfs.season_long import sheet_season as ss

    cfg = _config()
    client = _client(cfg, sheet_id)
    results = ss.doctor(client)
    for name, ok, detail in results:
        mark = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        console.print(f"{mark} {escape(name)}: {escape(detail)}")
    if not all(ok for _, ok, _ in results):
        raise typer.Exit(1)
