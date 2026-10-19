"""What Sam has decided on the Suggested tab, and the file that remembers it: the state behind
`dfs lineups suggest --iterate` (`lineup_iterate.py`). Pure: no sheet, no network.

**On the tab** (`sheet_suggested.py` writes and reads it): a decision cell on each build's bar (Keep /
Rebuild / Drop / Vary N), a lock tick beside every player, names Sam types over (an edit counts as a lock),
and a rules box of five typed lines. `sheet_suggested.parse_state` turns the grid into a `TabState`.

**On disk**: `data/suggest_state/<week>.json` holds every round (`RoundRecord`: the rules text, each build's
players, locks, "your picks", note and numbers), so a run can be reproduced, the history survives a rewrite of
the tab, and `--round N` can bring an earlier round back. The tab is the working copy, the file the memory.

**The rules box.** One cell per line, typed in the tab's Name column. A line holds several items separated by
commas, semicolons or line breaks. The syntax is small on purpose; an item it cannot read is reported (printed
and written on the tab), never guessed:

    Always use     names every new or rebuilt build must contain       Bijan, Nacua
    Never use      names no new or rebuilt build may contain           Kyren
    Max exposure   `Name: 4` (in at most 4 GPP builds; new ones are steered to reach 4), `more Name` (no
                   cap, new builds prefer him)                          Stafford: 4; more Nacua
    Stack rules    `TEAM: no bring-back`, `TEAM: QB+RB ok` (that team's stacks must be exactly that make-up;
                   several allowed make-ups on separate items), `no stacks from TEAM`
    Builds wanted  `cash 3, gpp 8`

A name is a full name or any part of one that matches a single player on the slate ("Stafford", "Bijan").
Locks beat rules: a locked player stays even when he is on the Never list or outside the pool."""

from __future__ import annotations

import difflib
import json
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dfs import lineup_shapes as shapes_mod
from dfs.late_swap_search import SwapPlayer
from dfs.paths import DATA_DIR

STATE_DIR = DATA_DIR / "suggest_state"

# The rules box: each label is the text in column A, each value the typed text beside it.
RULE_LABELS = ("Always use", "Never use", "Max exposure", "Stack rules", "Builds wanted")
RULE_FIELDS = dict(zip(RULE_LABELS, ("always", "never", "exposure", "stack", "wanted"), strict=True))
RULE_SYNTAX = (
    "Names: a full name or part of one (Stafford). Max exposure: `Stafford: 4` or `more Stafford`. Stack "
    "rules: `LAR: no bring-back`, `DET: QB+RB ok`, `no stacks from GB`. Builds wanted: `cash 3, gpp 8`. "
    "Separate items with commas or semicolons."
)

DECISIONS = ("keep", "rebuild", "drop", "vary")
DECISION_OPTIONS = [
    "Keep",
    "Rebuild",
    "Drop",
    "Vary 2",
    "Vary 3",
    "Vary 5",
]  # the dropdown (any Vary N is read)
VARY_MAX = 10
WANTED_MAX = {"cash": 20, "gpp": 60}  # the same limits as `--cash` / `--gpp`


# ---------------------------------------------------------------------------------------------
# What the tab holds
# ---------------------------------------------------------------------------------------------


@dataclass
class RulesText:
    """The rules box as typed: one string per line, labels in `RULE_LABELS` order."""

    always: str = ""
    never: str = ""
    exposure: str = ""
    stack: str = ""
    wanted: str = ""

    def by_label(self) -> dict[str, str]:
        return {label: getattr(self, attr) for label, attr in RULE_FIELDS.items()}

    @classmethod
    def from_labels(cls, values: dict[str, str]) -> RulesText:
        return cls(**{attr: str(values.get(label, "") or "").strip() for label, attr in RULE_FIELDS.items()})

    def blank(self) -> bool:
        return not any(v.strip() for v in self.by_label().values())


@dataclass
class TabBuild:
    """One build as the tab holds it right now (before the rerun reads it)."""

    number: int
    kind: str  # "cash" or "gpp"
    title: str
    decision: str  # as typed ("" is Rebuild)
    names: list[str]  # the nine player cells, slot order, as typed (a blank is a blank)
    locked: list[bool]  # the nine lock ticks


@dataclass
class TabState:
    """The Suggested tab read back: the round it says it is, the rules box and every build."""

    round: int | None = None
    rules: RulesText = field(default_factory=RulesText)
    builds: list[TabBuild] = field(default_factory=list)


@dataclass(frozen=True)
class Decision:
    kind: str  # one of DECISIONS
    n: int = 0  # Vary N


def parse_decision(text: str) -> Decision | None:
    """`Keep`, `Rebuild` (blank too), `Drop` or `Vary N` (1 to `VARY_MAX`), any case; None when unreadable."""
    t = re.sub(r"\s+", " ", str(text or "").strip().casefold())
    if t in ("", "rebuild"):
        return Decision("rebuild")
    if t in ("keep", "drop"):
        return Decision(t)
    m = re.fullmatch(r"vary ?(\d+)", t)
    if m and 1 <= int(m.group(1)) <= VARY_MAX:
        return Decision("vary", int(m.group(1)))
    return None


# ---------------------------------------------------------------------------------------------
# Names and rules
# ---------------------------------------------------------------------------------------------


def _norm(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().casefold())


def resolve_name(text: str, names: Sequence[str]) -> tuple[str | None, str]:
    """The player `text` means: an exact name (any case), else the one player whose name contains it. Returns
    `(name, "")`, or `(None, reason)` when nobody or several match (the reason says who, or who is close)."""
    wanted = _norm(text)
    if not wanted:
        return None, "no name"
    exact = [n for n in names if _norm(n) == wanted]
    if exact:
        return exact[0], ""
    partial = sorted({n for n in names if wanted in _norm(n)})
    if len(partial) == 1:
        return partial[0], ""
    if partial:
        return None, f"'{text.strip()}' matches {', '.join(partial[:4])}{' ...' if len(partial) > 4 else ''}"
    close = difflib.get_close_matches(str(text).strip(), list(names), n=2, cutoff=0.6)
    hint = f" (did you mean {' or '.join(close)}?)" if close else ""
    return None, f"no player '{text.strip()}' on the slate{hint}"


def split_items(text: str) -> list[str]:
    """The items of a rules line: split on commas, semicolons and line breaks."""
    return [item.strip() for item in re.split(r"[;,\n]", str(text or "")) if item.strip()]


def canonical_make_up(text: str) -> str | None:
    """`QB+WR+WR`, `QB+2WR` and `qb + wr + wr` all read "QB+2WR" (the make-up `lineup_shapes.composition`
    writes); None when the text is not a QB plus one to `MAX_MATES` skill players."""
    parts = [p.strip().upper() for p in str(text).split("+")]
    if not parts or parts[0] != "QB":
        return None
    counts = dict.fromkeys(shapes_mod.SKILL, 0)
    for part in parts[1:]:
        m = re.fullmatch(r"(\d?)(RB|WR|TE)", part)
        if not m:
            return None
        counts[m.group(2)] += int(m.group(1) or 1)
    total = sum(counts.values())
    if not 1 <= total <= shapes_mod.MAX_MATES:
        return None
    return "QB" + "".join(
        f"+{counts[pos] if counts[pos] > 1 else ''}{pos}" for pos in shapes_mod.SKILL if counts[pos]
    )


@dataclass
class StackRules:
    """The stack rules as a test on a finished lineup (`violation`)."""

    no_stack: set[str] = field(default_factory=set)  # a QB of these teams is never stacked
    no_bring_back: set[str] = field(default_factory=set)  # a stack of these teams has no bring-back
    only: dict[str, set[str]] = field(default_factory=dict)  # a team's stacks must be one of these make-ups

    def __bool__(self) -> bool:
        return bool(self.no_stack or self.no_bring_back or self.only)

    def violation(self, players: Sequence[SwapPlayer]) -> str | None:
        qb = next((p for p in players if p.position == "QB"), None)
        if qb is None or not shapes_mod.is_stacked(players):
            return None
        team = qb.team
        if team in self.no_stack:
            return f"no stacks from {team}"
        if team in self.no_bring_back and shapes_mod.has_bring_back(players):
            return f"{team}: no bring-back"
        allowed = self.only.get(team)
        if allowed and shapes_mod.composition(players) not in allowed:
            return f"{team}: stacks must be {' or '.join(sorted(allowed))}"
        return None

    def ok(self, players: Sequence[SwapPlayer]) -> bool:
        return self.violation(players) is None


@dataclass
class Rules:
    """The rules box, read: names resolved, numbers parsed, and every line it could not read in `problems`."""

    always: list[str] = field(default_factory=list)
    never: set[str] = field(default_factory=set)
    caps: dict[str, int] = field(default_factory=dict)  # `Name: N`
    more: set[str] = field(default_factory=set)  # `more Name`
    stacks: StackRules = field(default_factory=StackRules)
    wanted: dict[str, int] = field(default_factory=dict)  # "cash" / "gpp" -> how many
    problems: list[str] = field(default_factory=list)


_BRING_BACK = r"bring[- ]?backs?"


def parse_rules(text: RulesText, names: Sequence[str], teams: Iterable[str]) -> Rules:
    """The rules box as `Rules`. `names` is every player on the slate, `teams` its team codes."""
    rules = Rules()
    team_set = {str(t).upper() for t in teams}

    def player(label: str, item: str) -> str | None:
        name, reason = resolve_name(item, names)
        if name is None:
            rules.problems.append(f"{label}: {reason}")
        return name

    for item in split_items(text.always):
        if (name := player("Always use", item)) is not None and name not in rules.always:
            rules.always.append(name)
    for item in split_items(text.never):
        if (name := player("Never use", item)) is not None:
            rules.never.add(name)
    for item in split_items(text.exposure):
        more = re.fullmatch(r"more\s+(.+)", item, flags=re.IGNORECASE)
        capped = re.fullmatch(r"(.+?)\s*[:=]\s*(\d+)\s*(?:builds?)?", item, flags=re.IGNORECASE)
        if more and (name := player("Max exposure", more.group(1))) is not None:
            rules.more.add(name)
        elif capped and (name := player("Max exposure", capped.group(1))) is not None:
            rules.caps[name] = int(capped.group(2))
        elif not more and not capped:
            rules.problems.append(f"Max exposure: cannot read '{item}' (write `Name: 4` or `more Name`)")
    for item in split_items(text.stack):
        _stack_item(rules, item, team_set)
    for item in split_items(text.wanted):
        m = re.fullmatch(r"(cash|gpp)\s*[:=]?\s*(\d+)", item, flags=re.IGNORECASE)
        if not m:
            rules.problems.append(f"Builds wanted: cannot read '{item}' (write `cash 3, gpp 8`)")
            continue
        kind, count = m.group(1).lower(), int(m.group(2))
        if count > WANTED_MAX[kind]:
            rules.problems.append(f"Builds wanted: {kind} {count} is more than {WANTED_MAX[kind]}")
            continue
        rules.wanted[kind] = count
    for name in sorted(rules.never & set(rules.always)):
        rules.problems.append(f"{name} is on both the Always use and Never use lines: Never wins")
    rules.always = [n for n in rules.always if n not in rules.never]
    return rules


def _stack_item(rules: Rules, item: str, teams: set[str]) -> None:
    def team(code: str) -> str | None:
        code = code.upper()
        if teams and code not in teams:
            rules.problems.append(f"Stack rules: no team {code} on the slate ('{item}')")
            return None
        return code

    none_from = re.fullmatch(r"no\s+stacks?\s+from\s+(\w+)", item, flags=re.IGNORECASE)
    no_back = re.fullmatch(rf"(\w+)\s*:\s*no\s+{_BRING_BACK}", item, flags=re.IGNORECASE)
    make_up = re.fullmatch(r"(\w+)\s*:\s*([A-Za-z0-9+ ]+?)(?:\s+ok)?", item, flags=re.IGNORECASE)
    if none_from:
        if (code := team(none_from.group(1))) is not None:
            rules.stacks.no_stack.add(code)
    elif no_back:
        if (code := team(no_back.group(1))) is not None:
            rules.stacks.no_bring_back.add(code)
    elif make_up and (shape := canonical_make_up(make_up.group(2))) is not None:
        if (code := team(make_up.group(1))) is not None:
            rules.stacks.only.setdefault(code, set()).add(shape)
    else:
        rules.problems.append(
            f"Stack rules: cannot read '{item}' (write `LAR: no bring-back`, `DET: QB+RB ok` or "
            "`no stacks from GB`)"
        )


# ---------------------------------------------------------------------------------------------
# The file: every round
# ---------------------------------------------------------------------------------------------


@dataclass
class BuildRecord:
    """One build of one round, as the file keeps it."""

    number: int
    kind: str
    title: str
    names: list[str]  # nine, in the slot order the tab showed
    decision: str = ""  # what the decision cell said when the round was written
    locked: list[str] = field(default_factory=list)
    yours: list[str] = field(default_factory=list)
    no_bring_back: bool = False  # Sam removed the bring-back: it stays removed unless the gain is large
    origin: str = "new"
    note: str = ""
    metrics: dict[str, float] = field(default_factory=dict)  # p_cash, p_gpp, projection, salary, typical, ...


@dataclass
class RoundRecord:
    round: int
    at: str  # ISO timestamp, UTC
    command: str  # "suggest", "iterate" or "restore"
    rules: dict[str, str]  # label -> typed text
    builds: list[BuildRecord]
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    restored_from: int | None = None


@dataclass
class StateFile:
    week: int
    rounds: list[RoundRecord] = field(default_factory=list)

    @property
    def latest(self) -> RoundRecord | None:
        return self.rounds[-1] if self.rounds else None

    def get(self, number: int) -> RoundRecord | None:
        return next((r for r in self.rounds if r.round == number), None)

    def next_round(self) -> int:
        return (self.rounds[-1].round + 1) if self.rounds else 1


def state_path(week: int) -> Path:
    return STATE_DIR / f"{week}.json"


def load_state(week: int) -> StateFile:
    """The week's file, or an empty one when there is none yet. A file that cannot be read is an error (it
    holds history that must not be overwritten by a guess)."""
    path = state_path(week)
    if not path.exists():
        return StateFile(week=week)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        rounds = [
            RoundRecord(
                **{**r, "builds": [BuildRecord(**b) for b in r.get("builds", [])]},
            )
            for r in raw.get("rounds", [])
        ]
    except (ValueError, TypeError) as e:
        raise ValueError(
            f"{path} is not a readable suggest-state file ({e}); move it aside to start over"
        ) from e
    return StateFile(week=int(raw.get("week", week)), rounds=rounds)


def save_state(state: StateFile) -> Path:
    """Write the file (a temp file, then a rename: a crash never leaves half a history)."""
    path = state_path(state.week)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(state), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def history_lines(state: StateFile) -> list[str]:
    """One line per round for the tab's History block: number, when, how it ran, what it holds."""
    lines = []
    for r in state.rounds:
        cash = sum(1 for b in r.builds if b.kind == "cash")
        how = {
            "suggest": "fresh run",
            "iterate": "rerun",
            "restore": f"restored round {r.restored_from}",
        }.get(r.command, r.command)
        stamp = r.at[:16].replace("T", " ") + " UTC" if r.at else ""
        lines.append(
            f"Round {r.round}  ·  {stamp}  ·  {how}  ·  {len(r.builds)} builds ({cash} cash, "
            f"{len(r.builds) - cash} GPP)"
        )
    return lines
