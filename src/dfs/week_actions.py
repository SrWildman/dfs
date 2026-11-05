"""What the served Week page's buttons do, as plain functions over a `week_writes.SheetEdits` (the server is
only HTTP around these, so the tests drive them with a fake sheet).

- **Pool** (`pool_change`): Cash / GPP / Both / none on a player, the same EdgeRaw Pool cell the sheet's
  dropdown writes. `start_pool` is "Add the suggested starting pool", one batch.
- **Decisions** (`decide`): the server recomputes the decision from the week's data (the page sends only its
  key and the option), so a page cannot write anything but the three answers Sam chose:
  - chalk Play / Fade: `cash: Name` on Always use / Never use (cash builds only);
  - the GPP stack: `more QB`, `more Mate` on Max exposure (GPP builds only already);
  - a Questionable star's pivot: that player's Pool cell to Both, and the answer saved in the week's
    decisions file (`week_inputs.save_input("decisions", ...)`); Wait for news writes nothing.
  Each answer can be undone; undo removes exactly what the answer wrote.

Every write re-reads the cell first and refuses on a change (`week_writes.Conflict`), takes the run lock, and
is read back and logged (`data/week/edits.log`) by `SheetEdits`.
"""

from __future__ import annotations

from dfs import week_board as wb
from dfs import week_writes as ww
from dfs.week_inputs import WeekInputs, load_input, save_input

ALWAYS, NEVER, MAX_EXPOSURE = "Always use", "Never use", "Max exposure"


def answers(inp: WeekInputs) -> dict:
    """The week's decisions file (the pivots lined up), `{}` when there is none."""
    return load_input("decisions", inp.season, inp.week, inp.data_dir) or {}


def _save_answers(inp: WeekInputs, data: dict) -> None:
    if not save_input("decisions", data, season=inp.season, week=inp.week, data_dir=inp.data_dir):
        raise ww.WriteError("the sheet was written, but the decision could not be saved on this Mac")


def pool_change(edits: ww.SheetEdits, changes: list[dict]) -> dict[str, str]:
    if not isinstance(changes, list) or not changes:
        raise ww.WriteError("nothing to write")
    return edits.set_pool(changes, action="pool")


def start_pool(edits: ww.SheetEdits, inp: WeekInputs) -> dict[str, str]:
    """The suggested starting pool, recomputed from the sheet as it is now (never the page's copy): Value and
    Cash core as Cash, Upside as GPP, both as Both, only for players not in the pool."""
    live = edits.read()
    if not live.ok:
        raise ww.WriteError(f"could not read the sheet: {live.error}")
    pool = wb.pool_by_name(inp, live.pool)
    start = wb.starting_pool(wb.board(inp), pool)
    if not start:
        raise ww.WriteError("nothing to add: every suggested player is already in your pool")
    changes = [{"id": wb.pid(r), "value": v, "was": ""} for r, v in start]
    return edits.set_pool(changes, action="starting pool")


def _find(inp: WeekInputs, rules: dict[str, str], key: str) -> wb.Decision:
    for d in wb.decisions(inp, rules, answers(inp)):
        if d.key == key:
            return d
    raise ww.NotFound("that decision is not on the page any more (the data changed): the page now shows it")


def decide(
    edits: ww.SheetEdits,
    inp: WeekInputs,
    *,
    key: str,
    option: str,
    rules_was: dict[str, str],
    pivot: str = "",
    pool_was: str = "",
) -> str:
    """Apply (or, `option == "undo"`, take back) one decision. `rules_was` is the rules box as the page showed
    it (a line that changed since is a `Conflict`). Returns a sentence saying what was written."""
    rules_was = {str(k): str(v) for k, v in (rules_was or {}).items()}
    d = _find(inp, rules_was, key)
    slate = wb.slate_names(inp)
    if option != "undo" and d.answer:
        raise ww.Conflict("that decision is already answered (undo it first)", {})
    if option == "undo" and not (d.answer and d.undo):
        raise ww.WriteError("there is nothing on the page to undo for that decision")

    if d.kind == "chalk":
        name = str(d.row["Name"])
        match = ww.scoped_matcher("cash", name, slate)
        if option == "undo":
            label = ALWAYS if d.answer == "play" else NEVER
            edits.edit_rule(
                label, rules_was.get(label, ""), lambda t: ww.remove_items(t, match), action="undo"
            )
            return f"{name} taken off {label} (cash)."
        if option not in ("play", "fade"):
            raise ww.WriteError("Play or Fade")
        label = ALWAYS if option == "play" else NEVER
        edits.edit_rule(
            label, rules_was.get(label, ""), lambda t: ww.add_item(t, f"cash: {name}", match), action="decide"
        )
        return f"{name}: {'Play' if option == 'play' else 'Fade'}, written as cash: {name} on {label}."

    if d.kind == "stack":
        a, b = d.stacks
        if option == "undo":
            names = {n for s in d.stacks for n in wb.stack_names(s) if n}
            drop = [ww.more_matcher(n, slate) for n in names]
            edits.edit_rule(
                MAX_EXPOSURE,
                rules_was.get(MAX_EXPOSURE, ""),
                lambda t: ww.remove_items(t, lambda i: any(m(i) for m in drop)),
                action="undo",
            )
            return "The stack's more items are off Max exposure."
        chosen = {"a": [a], "b": [b], "both": [a, b]}.get(option)
        if chosen is None:
            raise ww.WriteError("pick one of the two stacks, or Both")
        names = list(dict.fromkeys(n for s in chosen for n in wb.stack_names(s) if n))

        def add_all(text: str) -> str:
            for n in names:
                text = ww.add_item(text, f"more {n}", ww.more_matcher(n, slate))
            return text

        edits.edit_rule(MAX_EXPOSURE, rules_was.get(MAX_EXPOSURE, ""), add_all, action="decide")
        return f"Max exposure now steers GPP builds to {', '.join(names)}."

    # a Questionable star
    rec = answers(inp)
    if option == "wait":
        return "Waiting for news: nothing written."
    if option == "undo":
        mine = rec.get(key) or {}
        pid, back = str(mine.get("pivot_id", "")), str(mine.get("was", ""))
        if pid:
            edits.set_pool([{"id": pid, "value": back, "was": "Both"}], action="undo pivot")
        rec.pop(key, None)
        _save_answers(inp, rec)
        return f"Pivot taken back: {mine.get('pivot', 'the pivot')} is {back or 'out of the pool'} again."
    if option != "pivot":
        raise ww.WriteError("Line up a pivot or Wait for news")
    offered = {wb.pid(p): p for p in d.pivots}
    if pivot not in offered:
        raise ww.WriteError("pick one of the pivots the page offers")
    p = offered[pivot]
    edits.set_pool([{"id": pivot, "value": "Both", "was": pool_was}], action="pivot")
    rec[key] = {
        "pivot": str(p.get("Name", "")),
        "pivot_id": pivot,
        "was": pool_was,
        "star": d.row.get("Name"),
    }
    _save_answers(inp, rec)
    return f"{p.get('Name')} is in your pool as Both (a pivot from {d.row.get('Name')})."
