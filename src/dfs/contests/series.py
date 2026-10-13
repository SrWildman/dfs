"""Contest name -> series key + format.

A *series* is one recurring contest ("NFL $8K Fair Catch [Single Entry]"): the same name every week with the
entry counter `(2/5)`, the Showdown matchup `(BUF @ LAR)` and an H2H opponent stripped off. A *format* is the
kind of contest (H2H, Double Up, SE GPP, ...), the level the toughness curves are pooled at.

**`Game_Type` decides the format first.** Only a `Classic` entry can be H2H / 50/50 / Double Up / SE GPP /
N-max GPP / Small top-heavy / Freeroll / Private; any Showdown-family type is `Showdown`; everything else
(Best Ball, Snake, Madden Classic, Single Stat, In-Game Showdown, ...) is `Other`. That ordering is the fix
for the Week 4 bug where `NFL Single Stat $1 7-Player (Top 3 Win)` (3 of 7 paid, a 0.43 payout ratio) read as
"cash" from its payout ratio alone.

`bankroll.classify_entry` (the payout-ratio band) is kept as a cross-check: `crosscheck` reports every row
where it and the format disagree about cash vs GPP. It never overrides the format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from dfs import bankroll
from dfs.models import ContestEntry

H2H = "H2H"
FIFTY_FIFTY = "50/50"
DOUBLE_UP = "Double Up"
SE_GPP = "SE GPP"
N_MAX_GPP = "N-max GPP"
SMALL_TOP_HEAVY = "Small top-heavy"
SHOWDOWN = "Showdown"
FREEROLL = "Freeroll"
PRIVATE = "Private"
OTHER = "Other"

FORMATS = (
    H2H,
    FIFTY_FIFTY,
    DOUBLE_UP,
    SE_GPP,
    N_MAX_GPP,
    SMALL_TOP_HEAVY,
    SHOWDOWN,
    FREEROLL,
    PRIVATE,
    OTHER,
)
CASH_FORMATS = frozenset({H2H, FIFTY_FIFTY, DOUBLE_UP})

# Sam's DraftKings username, for telling which side of "NFL $1 srwildman vs. <opponent>" is the opponent.
MY_USERNAME = "srwildman"

SMALL_FIELD_MAX = 100  # "Small top-heavy": a field UNDER this many entries ...
SMALL_TOP_PLACES_MAX = 5  # ... that pays this many places or fewer.

_COUNTER = re.compile(r"\(\s*(\d+)\s*/\s*(\d+)\s*\)")
_MATCHUP = re.compile(r"\(\s*([A-Za-z]{2,4})\s*@\s*([A-Za-z]{2,4})\s*\)")
_H2H_NAME = re.compile(r"^(?P<lead>(?:NFL\s+)?)\$(?P<fee>[\d.,]+[KkMm]?)\s+(?P<a>\S+)\s+vs\.?\s+(?P<b>\S+)$")
_CREATOR = re.compile(r"\bby\s+\S+", re.IGNORECASE)
_TRAILING_NUMBER = re.compile(r"\s*#\d+\s*$")
_SINGLE_ENTRY = re.compile(r"\[\s*(?:single entry|1 entry max)\s*\]|\bsingle entry\b", re.IGNORECASE)
_ENTRY_MAX = re.compile(r"\[\s*(\d+)\s*entry max\s*\]", re.IGNORECASE)


def _squash(text: str) -> str:
    return " ".join(text.split())


def game_family(game_type: str) -> str:
    """`classic`, `showdown` or `other`, from `Game_Type` alone. `In-Game Showdown (Q4)` is its own thing
    (a quarter-long contest scored on a different scale), so it is `other`, not `showdown`."""
    g = _squash(str(game_type)).casefold()
    if g == "classic":
        return "classic"
    if "in-game" in g:
        return "other"
    if "showdown" in g:
        return "showdown"
    return "other"


@dataclass(frozen=True)
class SeriesInfo:
    series: str  # the normalised series key
    format: str
    opponent: str  # H2H name pattern only, else ""
    matchup: str  # Showdown "BUF @ LAR", else ""
    entry_no: int | None  # the "2" of "(2/5)"
    entry_max: int | None  # the "5" of "(2/5)"
    bankroll_class: str  # bankroll.classify_entry: "cash" / "gpp"
    format_class: str  # "cash" if the format is H2H / 50/50 / Double Up, else "gpp"

    @property
    def agrees(self) -> bool:
        return self.bankroll_class == self.format_class


def normalize_name(name: str) -> tuple[str, str, int | None, int | None]:
    """`(name without the entry counter and the Showdown matchup, matchup, entry_no, entry_max)`."""
    text = str(name)
    entry_no = entry_max = None
    counter = _COUNTER.search(text)
    if counter:
        entry_no, entry_max = int(counter.group(1)), int(counter.group(2))
        text = _COUNTER.sub(" ", text)
    matchup = ""
    found = _MATCHUP.search(text)
    if found:
        matchup = f"{found.group(1).upper()} @ {found.group(2).upper()}"
        text = _MATCHUP.sub(" ", text)
    return _squash(text), matchup, entry_no, entry_max


def _h2h(text: str, me: str) -> tuple[str, str] | None:
    """`(fee text, opponent)` for "NFL $1 srwildman vs. <opponent>" (either side may be me), else None."""
    found = _H2H_NAME.match(text)
    if not found:
        return None
    a, b = found.group("a"), found.group("b")
    opponent = a if b.casefold() == me.casefold() else b
    return found.group("fee"), opponent


def _fee_text(fee: Decimal | float | None) -> str:
    if fee is None:
        return ""
    return format(Decimal(str(fee)).normalize(), "f")


def bankroll_class(game_type: str, contest_entries: int | None, places_paid: int | None) -> str:
    """What `bankroll.classify_entry` (the Game_Type-first payout-ratio band) says about this entry. It only
    reads these three fields, so the model is built without validation rather than with made-up others."""
    entry = ContestEntry.model_construct(
        game_type=str(game_type), contest_entries=contest_entries, places_paid=places_paid
    )
    return bankroll.classify_entry(entry)


def classify(
    game_type: str,
    name: str,
    *,
    fee: Decimal | float | None,
    contest_entries: int | None,
    places_paid: int | None,
    me: str = MY_USERNAME,
) -> SeriesInfo:
    """The series key and format of one history row. Never raises: a name nothing recognises is `Other`."""
    text, matchup, entry_no, entry_max = normalize_name(name)
    family = game_family(game_type)
    h2h = _h2h(text, me)
    opponent = h2h[1] if h2h else ""
    fee_is_zero = fee is not None and Decimal(str(fee)) == 0
    lower = text.casefold()

    fmt = OTHER
    series = text
    if family == "showdown":
        fmt = SHOWDOWN
        if h2h:
            series = f"H2H ${h2h[0]}"
    elif family == "classic":
        if fee_is_zero:
            fmt = FREEROLL
        elif not text.upper().startswith("NFL") and "$" not in text and _CREATOR.search(text):
            fmt, series = PRIVATE, _TRAILING_NUMBER.sub("", text)
        elif h2h or contest_entries == 2:
            fmt = H2H
            series = f"H2H ${h2h[0] if h2h else _fee_text(fee)}".rstrip()
        elif "double up" in lower:
            fmt = DOUBLE_UP
        elif "50/50" in lower:
            fmt = FIFTY_FIFTY
        elif (
            contest_entries is not None
            and places_paid is not None
            and contest_entries < SMALL_FIELD_MAX
            and 1 <= places_paid <= SMALL_TOP_PLACES_MAX
        ):
            fmt = SMALL_TOP_HEAVY
        elif _SINGLE_ENTRY.search(text):
            fmt = SE_GPP
        elif (limit := _ENTRY_MAX.search(text)) and int(limit.group(1)) > 1:
            fmt = N_MAX_GPP
        elif entry_max is not None and entry_max > 1:
            fmt = N_MAX_GPP

    return SeriesInfo(
        series=series,
        format=fmt,
        opponent=opponent,
        matchup=matchup,
        entry_no=entry_no,
        entry_max=entry_max,
        bankroll_class=bankroll_class(game_type, contest_entries, places_paid),
        format_class="cash" if fmt in CASH_FORMATS else "gpp",
    )


def crosscheck(log):
    """The rows of a history log where the payout-ratio band (`bankroll.classify_entry`) and the format
    disagree about cash vs GPP. Reported, never overridden: a Freeroll that pays half the field is a genuine
    disagreement worth a look, a Double Up outside the band is a mislabelled contest."""
    return log[log["Bankroll_Class"] != log["Format_Class"]]
