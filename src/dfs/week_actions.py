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
  Each answer also moves the pool (Plan N item 8): Play adds him as Cash, the stack its QB and mates as GPP,
  the pivot as Both; Fade takes him out only when he was Cash only. Each answer can be undone; undo removes
  exactly what the answer wrote, the pool cells too (each only while it still holds what the answer wrote).

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


MAX_BULK = 600  # more than any slate's priced players: a bulk write is the page's selection, never more


def pool_bulk(edits: ww.SheetEdits, changes: list[dict]) -> dict[str, dict]:
    """The multi-select's one action (Plan N item 7): every ticked player in one batch, the conflict check
    per cell (`SheetEdits.set_pool_each`): a cell that changed in the sheet is reported, never dropped."""
    if not isinstance(changes, list) or not changes:
        raise ww.WriteError("nothing to write")
    if len(changes) > MAX_BULK:
        raise ww.WriteError(f"at most {MAX_BULK} players in one write")
    return edits.set_pool_each(changes, action="bulk pool")


POOL_NOTE = "pool:"  # the decisions file's key for what a decision wrote to the pool (its undo)


def _ids_by_name(inp: WeekInputs) -> dict[str, str]:
    return {str(r.get("Name", "")): wb.pid(r) for r in inp.edge or []}


def _pool_add(edits: ww.SheetEdits, inp: WeekInputs, key: str, ids: list[str], kind: str, rec: dict) -> dict:
    """Add `ids` to the pool as `kind` (from the sheet's own value: GPP + Cash is Both), remember what moved
    for the undo. Returns Id -> the value read back."""
    moved = edits.merge_pool(
        {i: (lambda cur, k=kind: ww.add_kind(cur, k)) for i in ids}, action="decide pool"
    )
    note = {i: [before, after] for i, (before, after) in moved.items() if before != after}
    if note:
        rec[POOL_NOTE + key] = note
        _save_answers(inp, rec)
    return {i: after for i, (_, after) in moved.items()}


def _pool_undo(edits: ww.SheetEdits, inp: WeekInputs, key: str, rec: dict) -> tuple[dict, str]:
    """Put back the pool cells the decision wrote, each only while it still holds what the decision wrote (a
    cell Sam changed since is left alone and named)."""
    note = rec.get(POOL_NOTE + key) or {}
    if not note:
        return {}, ""
    out = edits.set_pool_each(
        [{"id": i, "value": before, "was": after} for i, (before, after) in note.items()], action="undo pool"
    )
    rec.pop(POOL_NOTE + key, None)
    _save_answers(inp, rec)
    names = {v: k for k, v in _ids_by_name(inp).items()}
    kept = [
        f"{names.get(i, i)} (now {r['value'] or 'not in the pool'})"
        for i, r in out.items()
        if r["status"] != "ok"
    ]
    back = [
        f"{names.get(i, i)} {note[i][0] or 'out of the pool'}" for i, r in out.items() if r["status"] == "ok"
    ]
    text = (" Pool put back: " + ", ".join(back) + ".") if back else ""
    if kept:
        text += " Changed in the sheet since, left alone: " + ", ".join(kept) + "."
    return {i: r["value"] for i, r in out.items()}, text


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


def more_stack(edits: ww.SheetEdits, inp: WeekInputs, names: list[str], rules_was: dict[str, str]) -> str:
    """The game view's "write as a Max exposure stack" (Plan N item 10): `more Name` for each ticked player on
    Max exposure (GPP builds only), the line re-read first (`Conflict` when it changed). Only players on this
    week's slate, and the stack needs its QB."""
    slate = wb.slate_names(inp)
    pos = {str(r.get("Name", "")): str(r.get("Position", "")) for r in inp.edge or []}
    names = list(dict.fromkeys(str(n) for n in names or [] if str(n).strip()))
    if not names:
        raise ww.WriteError("tick a QB and his teammates first")
    unknown = [n for n in names if n not in pos]
    if unknown:
        raise ww.WriteError(f"not on this week's slate: {', '.join(unknown)}")
    if not any(pos[n] == "QB" for n in names):
        raise ww.WriteError("a stack starts with its QB: tick him too")

    def add_all(text: str) -> str:
        for n in names:
            text = ww.add_item(text, f"more {n}", ww.more_matcher(n, slate))
        return text

    rules_was = {str(k): str(v) for k, v in (rules_was or {}).items()}
    edits.edit_rule(MAX_EXPOSURE, rules_was.get(MAX_EXPOSURE, ""), add_all, action="game stack")
    return f"Max exposure now steers GPP builds to {', '.join(names)}."


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
    """Apply (or, `option == "undo"`, take back) one decision; `decide_pool` also returns the pool cells it
    wrote, which is what the served page uses."""
    return decide_pool(
        edits, inp, key=key, option=option, rules_was=rules_was, pivot=pivot, pool_was=pool_was
    )[0]


def decide_pool(
    edits: ww.SheetEdits,
    inp: WeekInputs,
    *,
    key: str,
    option: str,
    rules_was: dict[str, str],
    pivot: str = "",
    pool_was: str = "",
) -> tuple[str, dict[str, str]]:
    """Apply (or, `option == "undo"`, take back) one decision. `rules_was` is the rules box as the page showed
    it (a line that changed since is a `Conflict`). Returns a sentence saying what was written and the pool
    cells written (Id -> the value read back).

    Decisions also move the pool (Plan N item 8): Play adds him as Cash, a stack pick adds the QB and mates as
    GPP, the pivot adds him as Both, and Fade takes him out only when he was Cash only (else the pool is left
    alone and the sentence says so). Undo takes back both the rules line and the pool cells."""
    rules_was = {str(k): str(v) for k, v in (rules_was or {}).items()}
    d = _find(inp, rules_was, key)
    slate = wb.slate_names(inp)
    if option != "undo" and d.answer:
        raise ww.Conflict("that decision is already answered (undo it first)", {})
    if option == "undo" and not (d.answer and d.undo):
        raise ww.WriteError("there is nothing on the page to undo for that decision")

    rec = answers(inp)
    if d.kind == "chalk":
        name = str(d.row["Name"])
        me = wb.pid(d.row)
        match = ww.scoped_matcher("cash", name, slate)
        if option == "undo":
            label = ALWAYS if d.answer == "play" else NEVER
            edits.edit_rule(
                label, rules_was.get(label, ""), lambda t: ww.remove_items(t, match), action="undo"
            )
            pool, text = _pool_undo(edits, inp, key, rec)
            return f"{name} taken off {label} (cash).{text}", pool
        if option not in ("play", "fade"):
            raise ww.WriteError("Play or Fade")
        label = ALWAYS if option == "play" else NEVER
        edits.edit_rule(
            label, rules_was.get(label, ""), lambda t: ww.add_item(t, f"cash: {name}", match), action="decide"
        )
        said = f"{name}: {'Play' if option == 'play' else 'Fade'}, written as cash: {name} on {label}."
        if option == "play":
            pool = _pool_add(edits, inp, key, [me], "Cash", rec)
            return f"{said} In your pool as {pool.get(me) or 'nothing'}.", pool
        moved = edits.merge_pool({me: ww.drop_cash_only}, action="decide pool")
        before, after = moved[me]
        if before != after:
            rec[POOL_NOTE + key] = {me: [before, after]}
            _save_answers(inp, rec)
            return f"{said} Taken out of your pool (he was Cash only).", {me: after}
        where = f"he is {before} there" if before else "he is not in it"
        return f"{said} Your pool is left alone ({where}).", {me: after}

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
            pool, text = _pool_undo(edits, inp, key, rec)
            return f"The stack's more items are off Max exposure.{text}", pool
        chosen = {"a": [a], "b": [b], "both": [a, b]}.get(option)
        if chosen is None:
            raise ww.WriteError("pick one of the two stacks, or Both")
        names = list(dict.fromkeys(n for s in chosen for n in wb.stack_names(s) if n))

        def add_all(text: str) -> str:
            for n in names:
                text = ww.add_item(text, f"more {n}", ww.more_matcher(n, slate))
            return text

        edits.edit_rule(MAX_EXPOSURE, rules_was.get(MAX_EXPOSURE, ""), add_all, action="decide")
        ids = _ids_by_name(inp)
        found = [ids[n] for n in names if n in ids]
        pool = _pool_add(edits, inp, key, found, "GPP", rec) if found else {}
        return f"Max exposure now steers GPP builds to {', '.join(names)}; in your pool for GPP.", pool

    # a Questionable star
    if option == "wait":
        return "Waiting for news: nothing written.", {}
    if option == "undo":
        mine = rec.get(key) or {}
        pid, back = str(mine.get("pivot_id", "")), str(mine.get("was", ""))
        pool = {}
        if pid:
            pool = edits.set_pool([{"id": pid, "value": back, "was": "Both"}], action="undo pivot")
        rec.pop(key, None)
        _save_answers(inp, rec)
        return (
            f"Pivot taken back: {mine.get('pivot', 'the pivot')} is {back or 'out of the pool'} again.",
            pool,
        )
    if option != "pivot":
        raise ww.WriteError("Line up a pivot or Wait for news")
    offered = {wb.pid(p): p for p in d.pivots}
    if pivot not in offered:
        raise ww.WriteError("pick one of the pivots the page offers")
    p = offered[pivot]
    pool = edits.set_pool([{"id": pivot, "value": "Both", "was": pool_was}], action="pivot")
    rec[key] = {
        "pivot": str(p.get("Name", "")),
        "pivot_id": pivot,
        "was": pool_was,
        "star": d.row.get("Name"),
    }
    _save_answers(inp, rec)
    return f"{p.get('Name')} is in your pool as Both (a pivot from {d.row.get('Name')}).", pool
