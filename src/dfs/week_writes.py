"""What the served Week page writes to the sheet: a player's **Pool** cell and a line of Suggested's
**rules box**.

The rules (Plan H's write rules, applied here first):

- **The sheet stays the source of truth.** The page never keeps its own copy: it shows what `read` got from
  the sheet, and every write answers with the value read back from the sheet afterwards.
- **Cells are found by header,** never by a hardcoded position: EdgeRaw's `Pool`, `Id` and `Name` columns by
  their header text on row 1 (the same `Pool` cell the sheet's dropdown and `dfs pool add` write); a rules
  line by its label in column A of the Suggested tab, above the first build (`sheet_suggested.parse_state`'s
  rule), the text in the tab's Name column.
- **Re-read before every write.** The page sends the value it showed (`was`); if the sheet's cell is not that
  any more, nothing is written and `Conflict` says what the sheet holds now (the page refreshes and shows it).
- **One batch per action, then read back.** A whole action (a pick, the suggested starting pool) is one
  `update_ranges` call; then the same cells are read again and that is what the page shows.
- **The run lock** is held for the read-check-write-read, so nothing writes during a sync.
- **Every write is logged** to `data/week/edits.log` (time, action, cell, before and after, or why refused).
- **A closed week is read only** (`ReadOnly`).

Pure text helpers for the rules box (`add_item`, `remove_items`) are at the bottom; `pool_changes_for_start`
and friends live in `week_board`. Tests drive this with a fake client (`read_range` / `update_ranges` /
`batch_read_ranges`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dfs import paths, run_lock
from dfs.sheets import column_letter
from dfs.sources.edge import POOL_HEADER, POOL_TYPE_OPTIONS

EDIT_COMMAND = "dfs week serve (page edit)"  # what the run lock says is running
MAX_ROWS = 1000  # EdgeRaw's provisioned depth (pool.py's `_MAX_ROWS`)
POOL_VALUES = tuple(POOL_TYPE_OPTIONS)  # "", Cash, GPP, Both
RULES_READ_ROWS = 40  # the rules box sits in the first rows of Suggested, above the first build


class WriteError(Exception):
    status = 400


class ReadOnly(WriteError):
    status = 403


class Conflict(WriteError):
    """The sheet changed since the page read it: nothing was written."""

    status = 409

    def __init__(self, message: str, current: dict[str, str]):
        super().__init__(message)
        self.current = current


class NotFound(WriteError):
    status = 404


def edits_log_path() -> Path:
    return paths.DATA_DIR / "week" / "edits.log"


@dataclass
class LiveSheet:
    """What the page read from the sheet for this render."""

    pool: dict[str, str] = field(default_factory=dict)  # Id -> Cash / GPP / Both (pooled players only)
    rules: dict[str, str] = field(default_factory=dict)  # rules label -> typed text
    rules_found: bool = False  # the Suggested tab has a rules box
    title: str = ""
    error: str = ""  # the sheet could not be read: the page falls back to the last sync's pool

    @property
    def ok(self) -> bool:
        return not self.error


def _cell(row: list, i: int) -> str:
    return str(row[i]).strip() if len(row) > i and row[i] is not None else ""


class SheetEdits:
    """The served page's reads and writes against one sheet. `client` is a `SheetsClient` (or a fake)."""

    def __init__(
        self,
        client: Any,
        *,
        edge_tab: str,
        read_only: bool = False,
        log_path: Path | None = None,
        lock: Callable[..., Any] | None = None,
        title: str = "",
    ):
        self.client = client
        self.edge_tab = edge_tab
        self.read_only = read_only
        self.log_path = log_path
        self.lock = lock or (lambda: run_lock.hold(EDIT_COMMAND, origin_name="button"))
        self.title = title
        self._mutex = threading.Lock()  # one write at a time inside this server, too

    # -- logging -----------------------------------------------------------------------------------------

    def log(self, line: str) -> None:
        try:
            target = self.log_path or edits_log_path()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a") as fh:
                fh.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {line}\n")
        except OSError:
            pass

    # -- finding cells by header -----------------------------------------------------------------------------

    def edge_columns(self) -> dict[str, str]:
        """EdgeRaw's `Pool`, `Id` and `Name` column letters, found by their header text on row 1."""
        header = (self.client.read_range(self.edge_tab, "1:1") or [[]])[0]
        found: dict[str, str] = {}
        for i, text in enumerate(header):
            label = str(text).strip()
            if label in (POOL_HEADER, "Id", "Name") and label not in found:
                found[label] = column_letter(i)
        missing = [h for h in (POOL_HEADER, "Id", "Name") if h not in found]
        if missing:
            raise NotFound(f"{self.edge_tab} has no {', '.join(missing)} header on row 1")
        return found

    def _pool_rows(self) -> tuple[dict[str, str], dict[str, int], dict[str, str], str]:
        """(Id -> pool value, Id -> sheet row, Id -> name, the Pool column letter), read in one round trip."""
        cols = self.edge_columns()
        pool_col, id_col, name_col = cols[POOL_HEADER], cols["Id"], cols["Name"]
        pools, ids, names = self.client.batch_read_ranges(
            [
                (self.edge_tab, f"{pool_col}2:{pool_col}{MAX_ROWS}"),
                (self.edge_tab, f"{id_col}2:{id_col}{MAX_ROWS}"),
                (self.edge_tab, f"{name_col}2:{name_col}{MAX_ROWS}"),
            ]
        )
        value: dict[str, str] = {}
        row_of: dict[str, int] = {}
        name_of: dict[str, str] = {}
        for i, id_row in enumerate(ids):
            pid = _cell(id_row, 0)
            if not pid or pid in row_of:
                continue
            row_of[pid] = i + 2
            value[pid] = _cell(pools[i], 0) if i < len(pools) else ""
            name_of[pid] = _cell(names[i], 0) if i < len(names) else ""
        return value, row_of, name_of, pool_col

    def _rules_rows(self) -> tuple[dict[str, str], dict[str, int], str]:
        """(label -> text, label -> sheet row, the column the text sits in) for Suggested's rules box."""
        from dfs import sheet_suggested as ss
        from dfs.suggest_state import RULE_LABELS

        name_letter = column_letter(ss.NAME_INDEX)
        if hasattr(self.client, "tab_exists") and not self.client.tab_exists(ss.TAB):
            return {}, {}, name_letter
        grid = self.client.read_range(ss.TAB, f"A1:{name_letter}{RULES_READ_ROWS}") or []
        text: dict[str, str] = {}
        row_of: dict[str, int] = {}
        for i, row in enumerate(grid):
            label = _cell(row, 0)
            if label.partition(" ")[0] == ss.BUILD_LABEL and label[len(ss.BUILD_LABEL) :].strip().isdigit():
                break  # the builds start: the box is above them
            if label in RULE_LABELS and label not in row_of:
                row_of[label] = i + 1
                text[label] = str(row[ss.NAME_INDEX]) if len(row) > ss.NAME_INDEX else ""
        return text, row_of, name_letter

    # -- reading ---------------------------------------------------------------------------------------------

    def read(self) -> LiveSheet:
        """The pool and the rules box as the sheet holds them now. Never raises: an error is on the result."""
        try:
            value, _, _, _ = self._pool_rows()
            rules, rows, _ = self._rules_rows()
        except Exception as exc:  # noqa: BLE001 - the page falls back to the last sync's pool
            return LiveSheet(error=f"{type(exc).__name__}: {exc}"[:300], title=self.title)
        return LiveSheet(
            pool={pid: v for pid, v in value.items() if v},
            rules=rules,
            rules_found=bool(rows),
            title=self.title,
        )

    # -- writing ---------------------------------------------------------------------------------------------

    def _guard(self) -> None:
        if self.read_only:
            raise ReadOnly("This week is closed: the page writes nothing to its sheet.")

    def set_pool(self, changes: Iterable[dict[str, str]], *, action: str = "pool") -> dict[str, str]:
        """Write several Pool cells as one action. Each change is `{"id", "value", "was"}` (`was` is what the
        page showed). Returns Id -> the value read back. Raises `Conflict` (nothing written) when any cell is
        not what the page showed."""
        self._guard()
        wanted = [dict(c) for c in changes]
        if not wanted:
            raise WriteError("nothing to write")
        for c in wanted:
            c["value"] = str(c.get("value") or "").strip()
            c["was"] = str(c.get("was") or "").strip()
            if c["value"] not in POOL_VALUES:
                raise WriteError(f"Pool takes Cash, GPP, Both or nothing, not {c['value']!r}")
            if not str(c.get("id") or "").strip():
                raise WriteError("a pool change needs the player's Id")
        with self._mutex, self.lock():
            value, row_of, name_of, pool_col = self._pool_rows()
            missing = [c["id"] for c in wanted if c["id"] not in row_of]
            if missing:
                self.log(f"{action} refused: Id {', '.join(missing)} not on {self.edge_tab}")
                raise NotFound(f"not on {self.edge_tab} (Id {', '.join(missing)}): sync, then try again")
            changed = {c["id"]: value[c["id"]] for c in wanted if value[c["id"]] != c["was"]}
            if changed:
                text = "; ".join(f"{name_of[i]} is {v or 'not in the pool'}" for i, v in changed.items())
                self.log(f"{action} refused (changed in the sheet): {text}")
                raise Conflict(
                    f"The sheet changed since the page read it ({text}). Nothing was written; the page "
                    "now shows the sheet.",
                    changed,
                )
            updates = {f"{pool_col}{row_of[c['id']]}": [[c["value"]]] for c in wanted}
            self.client.update_ranges(self.edge_tab, updates)
            after, _, _, _ = self._pool_rows()
        for c in wanted:
            self.log(
                f"{action} {self.edge_tab}!{pool_col}{row_of[c['id']]} {name_of[c['id']]}: "
                f"{c['was'] or '-'} -> {after.get(c['id'], '') or '-'}"
            )
        return {c["id"]: after.get(c["id"], "") for c in wanted}

    def edit_rule(self, label: str, was: str, change: Callable[[str], str], *, action: str = "rules") -> str:
        """Rewrite one rules line: `change(current_text) -> new_text`, applied only when the line still reads
        `was` (else `Conflict`). Returns the text read back."""
        from dfs import sheet_suggested as ss
        from dfs.suggest_state import RULE_LABELS

        self._guard()
        if label not in RULE_LABELS:
            raise WriteError(f"{label!r} is not a rules line")
        with self._mutex, self.lock():
            text, row_of, letter = self._rules_rows()
            if label not in row_of:
                self.log(f"{action} refused: no {label} line on {ss.TAB}")
                raise NotFound(
                    f"{ss.TAB} has no rules box (an old layout): run `dfs setup suggested-controls` once"
                )
            current = text.get(label, "")
            if current.strip() != str(was or "").strip():
                self.log(f"{action} refused (changed in the sheet): {label} reads {current!r}")
                raise Conflict(
                    f"{label} changed in the sheet since the page read it (it reads {current!r}). Nothing "
                    "was written; the page now shows the sheet.",
                    {label: current},
                )
            new = change(current)
            cell = f"{letter}{row_of[label]}"
            if new != current:
                self.client.update_ranges(ss.TAB, {cell: [[new]]})
            after, _, _ = self._rules_rows()
        back = after.get(label, "")
        self.log(f"{action} {ss.TAB}!{cell} {label}: {current!r} -> {back!r}")
        return back


# -- the rules box's text, edited item by item ---------------------------------------------------------


def add_item(text: str, item: str, same: Callable[[str], bool] | None = None) -> str:
    """`text` with `item` appended (`, ` between items), unless an item that `same` matches is already
    there."""
    from dfs.suggest_state import split_items

    items = split_items(text)
    if same is not None and any(same(i) for i in items):
        return text
    return ", ".join([*items, item]) if items else item


def remove_items(text: str, drop: Callable[[str], bool]) -> str:
    """`text` without the items `drop` matches (the others kept in order, joined with `, `). Unchanged text
    when nothing matches, so a line Sam typed is never reformatted for nothing."""
    from dfs.suggest_state import split_items

    items = split_items(text)
    kept = [i for i in items if not drop(i)]
    return text if len(kept) == len(items) else ", ".join(kept)


def names_match(item_name: str, target: str, slate: list[str]) -> bool:
    """`item_name` (as typed on a rules line) means `target`: the rules box's own name resolution."""
    from dfs.suggest_state import resolve_name

    name, _ = resolve_name(item_name, slate)
    return name == target


def scoped_matcher(kind: str | None, target: str, slate: list[str]) -> Callable[[str], bool]:
    """Matches an Always / Never use item that names `target` with scope `kind` (None: unscoped)."""
    from dfs.suggest_state import scoped_item

    def match(item: str) -> bool:
        scope, name = scoped_item(item)
        return scope == kind and names_match(name, target, slate)

    return match


def more_matcher(target: str, slate: list[str]) -> Callable[[str], bool]:
    """Matches a Max exposure `more Name` item for `target`."""
    import re

    def match(item: str) -> bool:
        m = re.fullmatch(r"more\s+(.+)", item.strip(), flags=re.IGNORECASE)
        return bool(m) and names_match(m.group(1), target, slate)

    return match
