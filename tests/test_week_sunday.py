"""The design pass's Sunday (Sam, 2026-11-18): what changed since Saturday night leads, "Chalk you're not on"
reads the latest sync, the four checks one line each, the late window folded."""

from __future__ import annotations

import csv
import html
import json
import re
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_week_plays import inputs, slate_rows

from dfs import lock_state, news_store, week_inputs, week_page, week_shell, week_sunday

FIX = Path(__file__).parent / "fixtures" / "week9_sunday"


def _replay(tmp_path: Path, stamp: str, at: datetime) -> week_inputs.WeekInputs:
    """Week 9 at `at`: the real EdgeRaw snapshot of that minute (trimmed to the chalk and Sam's L1) and the
    real news change log up to it."""
    d = tmp_path / stamp
    (d / "current").mkdir(parents=True)
    shutil.copy(FIX / f"edge_{stamp}.csv", d / "current" / "edge.csv")
    log = d / news_store.NEWS_SUBDIR / news_store.CHANGES_FILE
    log.parent.mkdir(parents=True)
    with (FIX / "changes.csv").open() as src, log.open("w", newline="") as out:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(r for r in reader if datetime.fromisoformat(r["at"].replace("Z", "+00:00")) <= at)
    return week_inputs.gather(2026, 9, at.astimezone(), d)


@pytest.mark.parametrize(
    "stamp,at,own,move",
    [
        ("1157", datetime(2026, 11, 8, 16, 57, 30, tzinfo=UTC), "36%", "+17"),  # 11:57 am ET
        ("1240", datetime(2026, 11, 8, 17, 40, 40, tzinfo=UTC), "41%", "+21"),  # 12:40 pm ET
    ],
)
def test_sunday_chalk_finds_week_9s_ceedee_lamb_from_the_latest_sync(tmp_path, stamp, at, own, move):
    """Sam's Week 9: CeeDee Lamb was 20% projected cash ownership Saturday night (under the 30% line), then
    36% at 11:57 am and 41% at 12:40 pm Sunday; 58% of double-up cashers had him and Sam's cash lineup did
    not. The Sunday card reads the latest sync against the Lineups tab's cash lineup (L1, before lock)."""
    inp = _replay(tmp_path, stamp, at)
    l1 = json.loads((FIX / "lineup_l1.json").read_text())
    lv = lock_state.view(inp, live_lineups=[l1])
    names, where = week_sunday.cash_lineup(inp, lv)
    assert names == l1["names"] and "L1" in where
    assert "CeeDee Lamb" not in names
    card = html.unescape(week_sunday.chalk_html(inp, lv))
    row = re.search(r'<tr data-id="[^"]*">(?:(?!</tr>).)*CeeDee Lamb.*?</tr>', card, re.S)
    assert row, "CeeDee Lamb is not on Sunday's chalk card"
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row.group(0), re.S)
    text = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
    assert own in text and move in text  # his ownership now, and the move since Saturday night
    assert "<button" not in card  # no Play / Fade: Sunday writes no sheet


def test_saturday_night_had_ceedee_under_the_line(tmp_path):
    """The honest half: at Saturday 6:01 pm ET (the 23:01Z sync) he was 19.7%, so Saturday's card could not
    list him; the 30% line is Sam's, kept."""
    rows = [r for r in csv.DictReader((FIX / "changes.csv").open()) if r["name"] == "CeeDee Lamb"]
    sat = [r for r in rows if r["field"] == "cash_own" and r["at"] == "2026-11-07T23:01:09Z"]
    assert sat and float(sat[0]["after"]) < week_page.CHALK_THRESHOLD


# -- what changed: the lead list, the status line ------------------------------------------------------------


def _row(name, status="", *, touches=(), pooled=False, swap=None, stake=1.0, started=False, kind="status"):
    return lock_state.Row(
        f"id-{name}:{kind}", None, f"id-{name}", name, "WR", "DAL", kind, name, None, None, None, (),
        tuple(touches), pooled, swap, stake, started, status,
    )  # fmt: skip


def _view(rows, sources=()):
    lv = lock_state.LockView(
        "lock",
        lock_state.Baseline(datetime(2026, 11, 8, 2, tzinfo=UTC), "since Saturday 9 pm"),
        lock_state.ChangeList(none=list(rows)),
        [],
        [],
        [],
        "the Lineups tab, read now",
        {},
        None,
        True,
        tuple(sources),
    )
    return lv


def test_inactives_lead_then_movement_and_a_teammates_lineups_are_not_his():
    from dfs.inactives import Source

    swap = object()
    rows = [
        _row("Pool Q", "Q", pooled=True, stake=9),
        _row("Mover", "", touches=("L2",), swap=swap, stake=5, kind="projection"),
        _row("Lineup Out", "OUT", touches=("L1",), stake=3),
        _row("Mate Out", "OUT", touches=("L1",), stake=8),  # his teammate is in L1, he is not
        _row("Quiet", "", touches=("L1",), stake=4, kind="projection"),
        _row("Started Mover", "", touches=("L1",), swap=swap, stake=7, started=True, kind="projection"),
    ]
    lv = _view(rows, [Source("L1", "cash", ("Lineup Out", "A")), Source("L2", "gpp", ("Mover",))])
    lead, rest = week_sunday.lead_rows(lv)
    # inactives first (in a lineup by stake, the pool-only one after), then movement with a swap
    assert [r.name for r in lead] == ["Mate Out", "Lineup Out", "Mover", "Pool Q"]
    assert [r.name for r in rest] == ["Quiet", "Started Mover"]
    assert [r.name for r in week_sunday.at_risk(lv)] == ["Lineup Out"]  # only who is in a lineup
    facts = {f.label: f for f in week_shell.sunday_facts(replace(_inp_stub(), changes=None), lv)}
    assert facts["Lineups"].value == "1 player at risk" and "L1" in facts["Lineups"].note
    assert "since Sat night" in facts["News"].note


def _inp_stub():
    return week_inputs.WeekInputs(
        season=2026, week=9, now=datetime(2026, 11, 8, 16, tzinfo=UTC), data_dir=Path(".")
    )


def test_the_sunday_view_leads_with_what_changed_then_chalk_checks_and_a_folded_late_window(tmp_path):
    inp = inputs(tmp_path, slate_rows(), now=datetime(2026, 11, 8, 15, tzinfo=UTC).astimezone())
    lv = lock_state.view(inp, live_lineups=[{"label": "L1", "marker": "", "names": ["Qb Alpha", "Wr Star"]}])
    out = week_sunday.sunday_html(inp, lv, live=True)
    order = [out.index(f'id="{sid}"') for sid in ("lock-card", "chalk-sun", "prelock", "late")]
    assert order == sorted(order)
    assert out.count('<li class="chk1') == 5  # the five checks, one line each
    late = out[out.index('id="late"') :]
    assert "<details" in late and " open" not in late.split(">", 2)[1]  # folded to its one line
    page = week_page.render(inp)
    assert page.index('id="status-lock"') < page.index('id="lock-card"') < page.index('id="chalk-sun"')
