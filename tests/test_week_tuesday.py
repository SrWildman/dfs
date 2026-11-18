"""The design pass's Tuesday (Sam, 2026-11-17): the closed week leads in the order he reads it, the one thing
right under the sentence; and the stale notice as one line (Sam, 2026-11-18)."""

from __future__ import annotations

import re
from datetime import UTC, timedelta

from test_close_view import TUE, close_input
from test_week_plays import inputs, page_text, render, slate_rows

from dfs import week_shell, week_today, week_tuesday


def _open(page: str, sid: str) -> bool:
    start = page.index(f'id="{sid}"')
    return '<details class="dfold" open>' in page[start : start + 200]


def test_tuesday_leads_with_the_sentence_and_the_one_thing_then_the_close_in_order(tmp_path):
    page = render(inputs(tmp_path, slate_rows(), now=TUE, close=close_input()))
    ids = ("close-card", "close-decided", "close-vs", "close-scores", "close-ledger", "decision-log")
    at = [page.index(f'id="{i}"') for i in ids]
    assert at == sorted(at)
    first = page[at[0] : at[1]]
    text = re.sub(r"\s+", " ", page_text(first))
    # the sentence, then the one thing directly under it
    assert text.index("133.0 pts against double-up lines") < text.index("The one thing worth changing")
    assert "Nothing." in text and "nothing here blocks, warns or writes a sheet" in text
    scores = page[at[3] : at[4]]
    assert scores.index("close-tiles") < scores.index("Projection (CalPts vs TFFB)")  # tiles, then the table
    assert not _open(page, "close-ledger") and not _open(page, "decision-log")  # folded
    assert "41% → 31%" in page_text(page[at[1] : at[2]])  # predicted → actual cash own
    assert 'id="next-week"' in page and "start week 10" in page
    assert page.index('id="status-close"') < at[0]


def test_tuesday_before_the_close_says_what_runs_it(tmp_path):
    out = week_tuesday.tuesday_html(inputs(tmp_path, slate_rows(), now=TUE))
    assert "Not run yet" in out and "close the week" in out and 'id="decision-log"' in out
    assert 'id="close-decided"' not in out
    assert "close the week" in week_tuesday.next_html(inputs(tmp_path, slate_rows(), now=TUE))


# -- the stale notice ----------------------------------------------------------------------------------------


def _notes(**over):
    now = TUE
    stamp = (now - timedelta(hours=1)).astimezone(UTC).isoformat()
    args = {
        "now": now,
        "manifest": {
            "edge": {"synced_at": stamp, "error": None},
            "tffb_gps": {"synced_at": stamp, "error": None, "pending": "GPS not published"},
        },
        "suggest_at": now - timedelta(hours=30),
        "late_swap_at": None,
        "season_at": None,
        "day": week_today.TUE,
    } | over
    return week_today.stale_notes(**args)


def test_each_stale_thing_has_a_short_label_and_the_one_command_that_fixes_it():
    notes = _notes()
    assert [(n.short, n.fix) for n in notes] == [
        ("Stale builds", "dfs lineups suggest --iterate"),
        ("tffb_gps pending", ""),  # nothing fixes a source that has not published: last
    ]
    assert "builds on Suggested are from" in notes[0].text
    sun = _notes(late_swap_at=TUE - timedelta(hours=2), day=week_today.SUN)
    assert [n.fix for n in sun][:2] == ["dfs lineups late-swap", "dfs lineups suggest --iterate"]
    assert [n.fix for n in _notes(manifest={})] == ["dfs sync"]
    # the old sentences are unchanged
    assert week_today.stale_items(
        now=TUE, manifest={}, suggest_at=None, late_swap_at=None, season_at=None, day=0
    ) == [n.text for n in _notes(manifest={}, suggest_at=None)]


def test_the_stale_notice_is_one_line_with_the_rest_behind_a_click():
    html = week_shell.stale_html(_notes())
    summary = html[html.index("<summary") : html.index("</summary>")]
    assert "Stale builds" in summary and "dfs lineups suggest --iterate" in summary and "+1 more" in summary
    assert "builds on Suggested" not in summary.split(">", 1)[1]  # the sentence is behind the click
    assert html.count("<li>") == 2 and "Pending (not published yet, not a failure): tffb_gps." in html
    assert 'class="wk-meta stale warn"' in html
    only_pending = week_shell.stale_html(_notes(suggest_at=None))
    assert 'class="wk-meta stale "' in only_pending and "warn" not in only_pending  # grey: nothing to fix
    assert week_shell.stale_html([]) == '<section class="stalebox" id="stale-note"></section>'
