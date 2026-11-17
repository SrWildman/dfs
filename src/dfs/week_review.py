"""`dfs week review [--week N]`: the learning loop's inputs for a closed week (Plan K, K2 items 6-9), saved
for the Week page. `dfs week close` runs it at its end (fail-soft); run it by hand to rebuild a week.

From the week's saved standings (the downloads `week close` keeps, `sim.contest_retro.load_real_contests`:
Classic double-ups, 50/50s, multipliers and GPPs that have a contest-log row), the week's saved builds
(`data/suggest_state/<week>.json`), the last pre-lock EdgeRaw snapshot and Model Check's files:

- `field_review` (item 6, `field_review.py`): what won and how the field built, Sam's lineups beside it;
- `scorecard` (item 7, `scorecard.py`): spend by tier (the `tier_week` line `week close` saved), the chalk
  decisions against what he played, overrides from the nearest build; a row in `data/contests/scorecard.csv`;
- `exposure_close` (item 8, `exposure.py`): his GPP exposure against the actual and the predicted ownership;
- `numbers` (item 9, `numbers.py`): CalPts against TFFB by week, Cash % shown against the cash rate (this
  week's cash builds join `data/contests/cash_shown.csv`), the signals' records;
- `edge_at_lock` (blueprint Phase D, `edge_at_lock.py`): the week's rows of `data/contests/edge_at_lock.csv`
  (replaced on a rerun) and its summary for the Close view's tile;
- `close` (blueprint Phase D, `close_view.py`): the Close view's one screen (the week line, what decided it,
  you against the build, the four scorecards, the tiles, the one thing), from files only.

Each part fails soft (a line says what was skipped). Reads no sheet and writes none: files only. `only` runs
a subset by name (`PARTS`): `dfs week review --week N --only edge_at_lock,close` is the backfill that writes
only those two parts' files.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dfs import exposure, field_review, numbers, scorecard
from dfs.player_join import normalize_name

PARTS = ("field_review", "scorecard", "exposure", "numbers", "edge_at_lock", "close")
NEEDS_STANDINGS = ("field_review", "scorecard", "exposure", "numbers", "edge_at_lock")


def _contests_dir() -> Path:
    from dfs.paths import CONTESTS_DIR

    return CONTESTS_DIR


def _mine(c, me: str) -> list[tuple[str, ...]]:  # noqa: ANN001 - RealContest
    feats = c.features
    rows = feats[feats["entry_name"].astype(str).str.strip().str.lower() == me]
    return [tuple(n for n in str(k).split("|") if n) for k in rows["lineup_key"]]


def _kind(fmt: str) -> str:
    return "cash" if fmt in field_review.CASH_FORMATS else "gpp"


def played_from(contests, me: str) -> list[scorecard.Played]:  # noqa: ANN001
    """His distinct lineups by kind, with how many entries each was."""
    counts: dict[tuple[str, frozenset], list] = {}
    for c in contests:
        for names in _mine(c, me):
            key = (_kind(c.format), frozenset(normalize_name(n) for n in names))
            if key in counts:
                counts[key][1] += 1
            else:
                counts[key] = [names, 1]
    return [scorecard.Played(kind, names, n) for (kind, _), (names, n) in counts.items()]


def builds_from(state) -> tuple[list[scorecard.Build], list[tuple[str, str]]]:  # noqa: ANN001 - StateFile
    """Every distinct build of the week (first round seen wins the label) and the last round's chalk
    decisions."""
    seen: dict[tuple[str, frozenset], scorecard.Build] = {}
    for rnd in state.rounds:
        for b in rnd.builds:
            key = (b.kind, frozenset(normalize_name(n) for n in b.names))
            seen.setdefault(
                key, scorecard.Build(f"{b.kind} build {b.number} (round {rnd.round})", b.kind, tuple(b.names))
            )
    decisions: list[tuple[str, str]] = []
    if state.rounds:
        rules = state.rounds[-1].rules
        labels = rules.by_label() if hasattr(rules, "by_label") else dict(rules or {})
        decisions = scorecard.decisions_from_rules(labels)
    return list(seen.values()), decisions


def actual_gpp_own(contests, me: str) -> dict[str, float]:  # noqa: ANN001
    """The actual ownership in the GPPs he entered, averaged over them weighted by their size."""
    total: dict[str, float] = {}
    weight = 0.0
    for c in contests:
        if _kind(c.format) != "gpp" or not _mine(c, me):
            continue
        weight += c.entries
        for k, v in c.own.items():
            total[k] = total.get(k, 0.0) + v * c.entries
    return {k: v / weight for k, v in total.items()} if weight else {}


def _tier_saved(season: int, week: int) -> tuple[str, list[str]]:
    from dfs import week_inputs

    for w in (week + 1, week):
        saved = week_inputs.load_input("tier_week", season, w)
        if saved and int(saved.get("week") or 0) == week:
            return str(saved.get("line") or ""), list(saved.get("over") or [])
    return "", []


def _csv_rows(path: Path) -> list[dict]:
    import csv

    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def scorecard_inputs(
    season: int, week: int, *, edge_summary: dict | None = None, say: Callable[[str], None] = print
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """The four scorecards (`scorecard.scorecards`) and what they were built from: (cards, projection by
    week, ownership scores, the edge-at-lock summary). Files only; runs no builder (about 20 seconds, most
    of it the ownership scoring)."""
    from dfs import edge_at_lock, field_own, results_update
    from dfs.sim import dups

    projection = numbers.projection_by_week(results_update.load_all_scored(season))
    try:
        own_frame, _counts = field_own.training_frame(season, through=week)
        own = field_own.score_weeks(own_frame, curve=dups.double_up_ownership).to_dict("records")
    except Exception as exc:  # noqa: BLE001
        say(f"  ownership scorecard: skipped ({type(exc).__name__}: {exc})")
        own = []
    cash_rows = numbers.read_csv(_contests_dir() / "cash_shown.csv")
    edge = edge_summary or edge_at_lock.summary(edge_at_lock.read(), season, week)
    cards = scorecard.scorecards(
        week=week, projection=projection, ownership=own, cash_rows=cash_rows, edge=edge
    )
    return cards, projection, own, edge


def close_input(season: int, week: int, *, edge_summary: dict | None, say: Callable[[str], None]) -> dict:
    """The `close` input (`close_view.payload`) from the files the close leaves. Each piece fails soft."""
    from dfs import cash_postmortem, close_view, entered, ledger
    from dfs.contests import history

    contests_dir = _contests_dir()
    decisions = _csv_rows(contests_dir / "decisions.csv")
    frame = ledger.load(history.read_log(contests_dir / "contest_log.csv"), through=(season, week))
    week_rows = frame[(frame["Season"] == season) & (frame["Week"] == week)]
    net, by_group = close_view.money(week_rows[["group", "net"]].to_dict("records"))
    fun = frame[(frame["Season"] == season) & (frame["group"] == ledger.FUN)]
    fun_by_week = [float(v) for _, v in sorted(fun.groupby(fun["Week"].astype(int))["fee"].sum().items())]
    try:
        pm = cash_postmortem.postmortem(cash_postmortem.load_week(season, week))
        decided = close_view.decided(pm.gaps, [e.names for e in pm.entered])
    except Exception as exc:  # noqa: BLE001
        say(f"  what decided it: skipped ({type(exc).__name__}: {exc})")
        decided = []
    from dfs import week_inputs

    against = "the Default" if week_inputs.load_input("cash_core", season, week) else "the nearest build"
    vs = close_view.vs_default(_csv_rows(contests_dir / "scorecard.csv"), season, week, against=against)
    blank = {}
    for w in range(1, week + 1):
        rec = entered.load(season, w)
        if rec is not None:
            blank[w] = len(rec.blank)
    cards, projection, own, edge = scorecard_inputs(season, week, edge_summary=edge_summary, say=say)
    gap = cards[2].get("gap")
    try:
        from dfs.config import load_config

        fun_per_week = load_config().ledger.fun_per_week
    except Exception:  # noqa: BLE001 - no config: no number set
        fun_per_week = None
    one = close_view.one_thing(
        cards,
        projection_gains=scorecard.projection_gains(projection, week),
        ownership_gains=scorecard.ownership_gains(own, week)[0],
        edge_by_week=[
            float(v["edge"]) for v in (edge or {}).get("weeks", {}).values() if v.get("edge") is not None
        ],
        calibration_gap=gap,
        fun_by_week=fun_by_week,
        fun_per_week=fun_per_week,
    )
    with_standings = len(
        {r["contest_id"] for r in decisions if int(r["season"]) == season and int(r["week"]) == week}
    )
    return close_view.payload(
        season=season,
        week=week,
        cash=close_view.cash_line(decisions, season, week),
        net=net if len(week_rows) else None,
        by_group=by_group,
        decided_rows=decided,
        vs=vs,
        avoid=close_view.avoidable(blank, week),
        percentile=close_view.percentile_by_week(decisions, season, week),
        cards=cards,
        edge=edge,
        calibration_gap=gap,
        one=one,
        contests=with_standings,
        entries=len(week_rows),
        du_entered=int((week_rows["bucket"] == "Double-up").sum()),
    )


def run(
    season: int, week: int, *, say: Callable[[str], None] = print, only: tuple[str, ...] | None = None
) -> bool:
    """Build and save the inputs for `week` (`only`: those parts of `PARTS`). True when the standings were
    found (each part may still have been skipped, said through `say`)."""
    from dfs import week_inputs
    from dfs.contests import series as series_mod
    from dfs.sim import contest_retro

    wanted = tuple(only) if only else PARTS
    unknown = [p for p in wanted if p not in PARTS]
    if unknown:
        raise ValueError(f"unknown review parts {unknown}; choose from {', '.join(PARTS)}")
    me = series_mod.MY_USERNAME
    contests = contest_retro.load_real_contests(weeks={week})
    if not contests:
        say(
            f"Week {week}: no saved standings with a contest-log row, so nothing to review against the field."
        )
        if "close" in wanted:
            _close_part(season, week, None, say)
        return False

    def part(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - one part never stops the others
            say(f"  {name} skipped ({type(exc).__name__}: {exc})")

    def do_field() -> None:
        view = field_review.review(
            [
                field_review.Contest(
                    c.contest_id, c.series, c.format, c.entries, c.places_paid, c.features, c.own
                )
                for c in contests
            ],
            me,
            week,
        )
        week_inputs.save_input("field_review", view, season=season, week=week)
        say(f"What won: {field_review.headline(view)}")

    def do_card() -> None:
        from dfs import suggest_state

        fpts: dict[str, float] = {}
        for c in contests:
            fpts.update(c.fpts)
        builds, decisions = builds_from(suggest_state.load_state(week))
        line, over = _tier_saved(season, week)
        card = scorecard.card(
            week, played_from(contests, me), builds, fpts, decisions=decisions, tier_line=line, tier_over=over
        )
        path = _contests_dir() / "scorecard.csv"
        scorecard.append_csv(path, scorecard.csv_row(season, card))
        body = scorecard.payload(card, scorecard.season_line_from_csv(path, season))
        week_inputs.save_input("scorecard", body, season=season, week=week)
        for text in scorecard.lines(body):
            say(text)

    def do_exposure() -> None:
        entries = [names for c in contests if _kind(c.format) == "gpp" for names in _mine(c, me)]
        edge = contest_retro.pre_lock_edge(week, season)
        predicted = {}
        for name, own in zip(edge["Name"], edge.get("GppOwn", [None] * len(edge)), strict=True):
            try:
                v = float(own)
            except (TypeError, ValueError):
                continue
            if v == v:
                predicted[normalize_name(name)] = v
        display = {normalize_name(n): n for lineup in entries for n in lineup}
        display.update({normalize_name(n): n for n in edge["Name"] if normalize_name(n) not in display})
        body = {
            "week": week,
            "actual": exposure.view(
                entries, actual_gpp_own(contests, me), key=normalize_name, display=display
            ),
            "predicted": exposure.view(entries, predicted, key=normalize_name, display=display),
        }
        week_inputs.save_input("exposure_close", body, season=season, week=week)
        a = body["actual"]
        say(
            f"GPP exposure vs actual ownership: {len(a['over'])} overweights, {len(a['under'])} underweights "
            f"({a['entries']} entries)"
        )

    def do_numbers() -> None:
        from dfs import cash_postmortem, results_update
        from dfs.results_loop import RESULTS_DIR

        cash_path = _contests_dir() / "cash_shown.csv"
        try:
            pm = cash_postmortem.postmortem(cash_postmortem.load_week(season, week))
        except FileNotFoundError as exc:
            say(f"  Cash % shown vs cashed: {exc}")
        else:
            rows = [
                {
                    "season": season,
                    "week": week,
                    "label": s.build.label,
                    "shown": "" if s.build.shown_cash is None else s.build.shown_cash,
                    "points": round(s.points, 2),
                    "line": pm.cash_line,
                    "cashed": s.cashed,
                }
                for s in pm.builds or []
            ]
            numbers.replace_week(cash_path, season, week, rows)
        body = numbers.payload(
            results_update.load_all_scored(season),
            numbers.read_csv(cash_path),
            numbers.read_csv(RESULTS_DIR / f"signal_table_{season}.csv"),
        )
        body["week"] = week
        week_inputs.save_input("numbers", body, season=season, week=week)
        say(f"Our numbers: {numbers.headline(body)}")

    edge_summary: dict | None = None

    def do_edge() -> None:
        nonlocal edge_summary
        from dfs import edge_at_lock

        try:
            n = edge_at_lock.run(season, week, contests=contests)
        except edge_at_lock.NoSnapshot as exc:
            say(f"  edge at lock: Week {week} skipped ({exc}); the tile shows the season's other weeks")
            n = 0
        edge_summary = edge_at_lock.summary(edge_at_lock.read(), season, week)
        week_inputs.save_input("edge_at_lock", edge_summary, season=season, week=week)
        this = edge_summary.get("this_week")
        say(
            f"Edge at lock: {n} rows"
            + (
                f"; double-ups {this['edge']:+.1f} pts, beat {this['pct'] * 100:.0f}% of the field"
                if this
                else ""
            )
        )

    steps = {
        "field_review": ("what won", do_field),
        "scorecard": ("scorecard", do_card),
        "exposure": ("exposure", do_exposure),
        "numbers": ("numbers", do_numbers),
        "edge_at_lock": ("edge at lock", do_edge),
    }
    for name in PARTS:
        if name in wanted and name in steps:
            part(*steps[name])
    if "close" in wanted:
        _close_part(season, week, edge_summary, say)
    return True


def _close_part(season: int, week: int, edge_summary: dict | None, say: Callable[[str], None]) -> None:
    from dfs import close_view, week_inputs

    try:
        body = close_input(season, week, edge_summary=edge_summary, say=say)
    except Exception as exc:  # noqa: BLE001 - one part never stops the others
        say(f"  close skipped ({type(exc).__name__}: {exc})")
        return
    week_inputs.save_input("close", body, season=season, week=week)
    say(f"The close: {close_view.headline(body)}")
