"""Week page v2's answers (`week_board`): the deduped board and fade precedence, the pool panel and the
suggested starting pool, the decisions, the progress state machine and the brief."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from test_week_plays import SAT, WED, row, slate_rows

from dfs import runlog, week_board, week_inputs


def inputs(d, rows=None, now=WED, **fields):
    from test_week_plays import write_csv

    if rows is not None:
        write_csv(d / "current" / "edge.csv", rows)
    return replace(week_inputs.gather(2026, 9, now, d), config={}, **fields)


def chalky():
    rows = slate_rows(alpha={"CashOwn": "0.41", "GppOwn": "0.30"}, beta={"CashOwn": "0.05", "GppOwn": "0.04"})
    for r in rows:
        r.setdefault("CashOwn", "")
        if r["Name"] == "Wr Value":
            r["CashOwn"], r["GppOwn"] = "0.33", "0.10"
        elif not r["CashOwn"]:
            r["CashOwn"], r["GppOwn"] = "0.05", "0.05"
    return rows


# -- the board -----------------------------------------------------------------------------------------------


def test_every_player_is_on_the_board_once_with_all_his_roles(tmp_path):
    b = week_board.board(inputs(tmp_path, slate_rows()))
    names = [r.name for r in b.rows()]
    assert len(names) == len(set(names))  # deduped
    value = next(r for r in b.rows() if r.name == "Qb Alpha")
    assert len(value.roles) >= 2  # one row, several chips


def test_fade_wins_over_every_other_role_and_sits_last(tmp_path):
    rows = slate_rows()
    for r in rows:
        if r["Name"] == "Te Fade":
            r.update({"Salary": "2500", "CalPts": "11.0", "Boom%": "60"})  # the best value and upside TE too
    b = week_board.board(inputs(tmp_path, rows))
    te = b.by_pos["TE"]
    fade = next(r for r in te if r.name == "Te Fade")
    assert fade.roles == [week_board.FADE_ROLE] and fade.fade
    assert te[-1].name == "Te Fade"
    assert fade.record  # the signal's Model Check line (or the honest "no record yet")
    assert "Te Fade" not in [r["Name"] for r, _ in week_board.starting_pool(b, {})]


def test_the_why_line_says_what_the_columns_do_not():
    r = row(
        "Wr X", "WR", "DAL", "IND", 6000, 15.0, **{"Tgt L5": "8.0", "OverUnder": "52.5", "Spread": "-6.0"}
    )
    why = week_board.why_facts(r, {"2026_09_DAL_IND": 1})
    assert "8 targets a game" in why and "the slate's #1 game" in why
    assert "per $1K" not in why and "CalPts" not in why


# -- the pool panel and the starting pool --------------------------------------------------------------------


def test_pool_panel_counts_gaps_questionable_and_likes(tmp_path):
    inp = inputs(tmp_path, slate_rows())
    b = week_board.board(inp)
    pool = {"Qb Alpha": "Both", "Wr Star": "Cash", "Wr Value": "GPP"}
    panel = week_board.pool_panel(inp, b, pool)
    assert panel.total == 3
    assert panel.counts["Cash"]["QB"] == 1 and panel.counts["GPP"]["QB"] == 1
    assert panel.counts["Cash"]["WR"] == 1 and panel.counts["GPP"]["WR"] == 1
    assert any("TE" in g for g in panel.gaps)
    assert [r["Name"] for r in panel.questionable] == ["Wr Star"]
    assert all(r.name not in pool for r in panel.likes) and len(panel.likes) <= week_board.DATA_LIKES


def test_starting_pool_never_touches_a_pooled_questionable_or_out_player(tmp_path):
    inp = inputs(tmp_path, slate_rows())
    b = week_board.board(inp)
    start = dict((r["Name"], v) for r, v in week_board.starting_pool(b, {"Qb Alpha": "GPP"}))
    assert "Qb Alpha" not in start and "Wr Star" not in start and "Wr Out" not in start
    assert set(start.values()) <= {"Cash", "GPP", "Both"} and start


# -- the decisions -------------------------------------------------------------------------------------------


def test_chalk_decisions_read_their_answer_from_the_rules_box(tmp_path):
    inp = inputs(tmp_path, chalky())
    unread = week_board.decisions(inp, None, {})
    chalk = [d for d in unread if d.kind == "chalk"]
    assert [d.row["Name"] for d in chalk] == ["Qb Alpha", "Wr Value"]  # 41%, 33%; most owned first
    assert not any(d.answer for d in chalk)  # rules not read: nothing shown as answered
    ds = week_board.decisions(inp, {"Always use": "cash: Qb Alpha", "Never use": "Wr Value"}, {})
    alpha, value = (d for d in ds if d.kind == "chalk")
    assert alpha.answer == "play" and alpha.undo  # written by the page: it can be undone
    assert value.answer == "fade" and not value.undo  # typed unscoped on the sheet: shown, never undone


def test_a_questionable_star_offers_healthy_pivots_at_his_position_within_800(tmp_path):
    rows = [
        *slate_rows(),
        row("Wr Near", "WR", "NE", "NYJ", 8500, 16.0),
        row("Wr Far", "WR", "NE", "NYJ", 6000, 17.0),
        row("Wr Hurt", "WR", "NE", "NYJ", 8800, 18.0, Avail="Q"),
    ]
    inp = inputs(tmp_path, rows)
    qs = [d for d in week_board.decisions(inp, {}, {}) if d.kind == "q"]
    assert [d.row["Name"] for d in qs] == ["Wr Star", "Wr Hurt"]  # the higher-projected Q star first
    star = qs[0]
    assert [p["Name"] for p in star.pivots] == ["Wr Near"]  # Wr Far is $3,000 away, Wr Hurt is Questionable
    done = week_board.decisions(inp, {}, {"q:id-Wr Star": {"pivot": "Wr Near"}}, q_max=2)
    answered = next(d for d in done if d.key == "q:id-Wr Star")
    assert answered.answer == "pivot" and answered.undo and "Wr Near" in answered.answer_text


def test_the_stack_decision_is_between_two_qbs_and_reads_max_exposure(tmp_path):
    week_inputs.save_input(
        "stacks",
        {
            "rows": [
                {"qb": "Qb Alpha", "mates": "Wr Value", "game": "DAL v IND"},
                {"qb": "Qb Alpha", "mates": "Wr Star", "game": "DAL v IND"},
                {"qb": "Qb Beta", "mates": "Te Fade", "game": "IND v DAL"},
            ]
        },
        season=2026,
        week=9,
        data_dir=tmp_path,
    )
    inp = inputs(tmp_path, slate_rows())
    (d,) = [d for d in week_board.decisions(inp, {}, {}) if d.kind == "stack"]
    assert [s["qb"] for s in d.stacks] == ["Qb Alpha", "Qb Beta"]  # never two stacks of one QB
    (d,) = [
        d
        for d in week_board.decisions(inp, {"Max exposure": "more Qb Beta, more Te Fade"}, {})
        if d.kind == "stack"
    ]
    assert d.answer == "b" and d.undo


# -- the progress strip --------------------------------------------------------------------------------------


def states(steps):
    return [s.state for s in steps]


def test_progress_has_exactly_one_next_step_the_first_one_not_done(tmp_path):
    inp = inputs(tmp_path, slate_rows())
    steps = week_board.progress(inp, {})
    assert states(steps) == ["next", "todo", "todo", "todo", "todo", "todo"]
    assert steps[0].status == "0 players"
    steps = week_board.progress(inp, {"Qb Alpha": "Both"})
    assert states(steps)[:2] == ["done", "next"] and steps[0].status == "1 player"
    planned = replace(inp, contest_plan={"saved_at": (WED - timedelta(hours=2)).astimezone(UTC).isoformat()})
    built = replace(
        planned,
        suggest={"round": 2, "at": WED.astimezone(UTC).isoformat(), "builds": [{"kind": "cash"}]},
    )
    steps = week_board.progress(built, {"Qb Alpha": "Both"})
    assert states(steps) == ["done", "done", "done", "next", "todo", "todo"]
    assert steps[2].status.startswith("round 2")
    assert week_board.next_step(steps).name == "Entries recorded"


def test_last_weeks_builds_do_not_count_and_the_close_comes_from_the_run_log(tmp_path):
    inp = inputs(tmp_path, slate_rows(), now=SAT)
    old = replace(inp, suggest={"round": 1, "at": "2026-10-30T12:00:00+00:00", "builds": [{"kind": "cash"}]})
    assert week_board.progress(old, {"A": "Cash"})[2].state != "done"
    closed = replace(
        inp, runs=[runlog.Run(datetime(2026, 11, 10, 9, 0).astimezone(), "dfs week close", True)]
    )
    assert week_board.progress(closed, {})[-1].state == "done"


# -- the brief -----------------------------------------------------------------------------------------------


def test_the_brief_is_deterministic_and_says_what_the_data_says(tmp_path):
    inp = inputs(tmp_path, chalky())
    b = week_board.board(inp)
    panel = week_board.pool_panel(inp, b, {})
    first = week_board.brief(inp, b, panel)
    assert [s.text for s in first] == [s.text for s in week_board.brief(inp, b, panel)]
    assert 5 <= len(first) <= 7
    by = {s.key: s.text for s in first}
    assert "DAL @ IND" in by["games"] or "IND @ DAL" in by["games"]
    assert "{Qb Alpha} (41% in double-ups)" in by["chalk"] and "play or fade" in by["chalk"]
    assert "{Te Fade}" in by["fades"]
    assert "{Wr Star}" in by["q"] and "Friday" in by["q"]  # news due before Sunday's kickoff
    assert "empty" in by["pool"]


def test_the_brief_on_an_empty_week_says_sync():
    inp = week_inputs.WeekInputs.__new__(week_inputs.WeekInputs)
    object.__setattr__(inp, "edge", None)
    out = week_board.brief(inp, None, None)
    assert len(out) == 1 and "sync" in out[0].text
