"""Shared colour-scale rule construction.

PROMPT_BOARD_FIXES.md item 7 (2026-09-25): "route them all through the
shared `_scale_rule_specs`" -- there are roughly 33 real `add_color_scale`
call sites across the workbook, several of them hand-rolling their own
gradient dict instead of going through the one dispatch that knows how to
exclude a real, common zero from a gradient's min AND mid (see
`ZERO_EXCLUDED_COLUMNS`'s own comment for why that matters). Pulling the
dispatch (and the palette/kind constants it needs) out of `sheet_style.py`
into its own module, rather than leaving it there, is what makes that
possible at all: `sheet_style.py` already imports from `sheet_links.py`
(`LINKED_EDGE_COLUMNS`/`PLAYER_POOL_RAW_TAB`), and `sheet_links.py`'s own
two hand-coded scales (plus `sheet_pool_resize.py`'s one) need this same
dispatch too -- importing it FROM `sheet_style.py` there would be
circular. This module imports from neither, so every direction is safe.

`sheet_style.py` still owns the rest of the palette (`INK`, `HEADER_BG`,
chip colours, ...) and its own private copy of `_rgb` -- only the pieces
a gradient rule itself needs live here.
"""

from __future__ import annotations

import re

from dfs.derived import CHALK_OWNERSHIP_THRESHOLD
from dfs.sheets import column_letter


def _rgb(hex_str: str) -> dict:
    h = hex_str.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }


WHITE = _rgb("#FFFFFF")

# The red -> yellow -> green gradient the now-removed `dfs sheets
# format-edge` command originally introduced, kept identical here.
GRAD_MIN = {"red": 0.96, "green": 0.80, "blue": 0.80}
GRAD_MID = {"red": 1.0, "green": 1.0, "blue": 0.80}
GRAD_MAX = {"red": 0.72, "green": 0.88, "blue": 0.72}

_GRADIENT = "gradient"  # red -> yellow -> green, more is better
# Round 5 item 3: FIVE-BAND formula rules (see `band_rule_specs`) replace the gradient for
# most columns. Bands are driven by a PERCENTILE, follow the row through any sort or
# filter, and never colour a zero or a blank.
_PCT = "pct"  # a player metric: banded by its hidden within-position percentile helper
_SCORE = "score"  # already 0-100 (ValAdj, CeilPct, GameEnv): banded on its own value
_LEVERAGE = "leverage"  # centred on 0 (CeilPct - OwnPct, -100..+100)
_RANK = "rank"  # a 1-32 matchup rank, LOW is better
_GAME = "game"  # a per-game/team metric: PERCENTRANK against the tab's own column, high is good
_GAME_REVERSED = "game_reversed"  # same, LOW is good (Pace, Spread)
_DIVERGING = "diverging"  # red -> white -> green, zero is the midpoint
_REVERSED = "reversed"  # green -> yellow -> red, LOW is better
# White -> amber -> red -- the one exception to "more is better" (Own%):
# high ownership is chalk, a caution not a quality, so it never gets the
# green=good treatment. Reuses WARN_BG/CRIT_BG's exact hues so it still
# reads as the workbook's existing warning language.
_WARM = "warm"

WARM_MIN = WHITE
WARM_MID = _rgb("#F7E9CF")  # == WARN_BG
WARM_MAX = _rgb("#F8DEDA")  # == CRIT_BG

# Every field name in the workbook that gets a colour scale, and which
# shape -- shared across every tab that happens to carry a column of this
# name (Fix 2.1), including Board's own leaders/punt/stack sections, which
# reuse EdgeRaw's own field names (ValAdj, ProjPts, Salary, ...) for their
# column headers.
FIELD_COLOR_SCALES = {
    # Round 5 item 3: the five player-performance metrics are banded by their
    # within-position percentile helper (`derived.PLAYER_METRIC_PCT_COLUMNS`), the
    # same number on every tab, so a player looks identical everywhere and a sort
    # or filter can never move a colour onto the wrong player.
    "ProjPts": _PCT,
    "AggPts": _PCT,
    "Pts": _PCT,
    "Ceiling": _PCT,
    "Ceil": _PCT,
    "Val": _PCT,
    "CeilVal": _PCT,
    # Already 0-100 scores/percentiles: banded on their own value (Rule 3).
    "ValAdj": _SCORE,
    "CeilPct": _SCORE,
    "GameEnv": _SCORE,
    # CeilPct - OwnPct, so -100..+100 and centred on 0 (Rule 3 -- see `LEVERAGE_BANDS`).
    "Leverage": _LEVERAGE,
    # Game/team metrics: PERCENTRANK against the tab's own column, live (Rule 4).
    # Pace is faster-is-better (lower), Spread lower-is-better (a bigger favourite).
    "Pace": _GAME_REVERSED,
    "PROE": _GAME,
    "Expl%": _GAME,
    "Team Implied": _GAME,
    "O/U": _GAME,
    "OU": _GAME,
    "OverUnder": _GAME,
    "Total": _GAME,
    "GPS": _GAME,
    "Spread": _GAME_REVERSED,
    # OppPosRank is a 1-32 rank where LOW is the tough matchup (this opponent allows
    # the FEWEST fantasy points at the position): bands on the rank itself.
    "OppPosRank": _RANK,
    # Round 5 item 9: higher = softer matchup for every position (DST sign-flipped).
    "OppEPA": _GAME,
    # Rule 5: kept as they were -- movement diverges around zero, Own% is the warm scale.
    "ImpliedMove": _DIVERGING,
    "TotMove": _DIVERGING,
    "SpdMove": _DIVERGING,
    "Own%": _WARM,
    # Item 1d (Exposure zeros): Exposure's share column joins the shared system so its
    # zero-heavy column stops anchoring the gradient. Low is comfortable, high is
    # concentration, so it stays a reversed gradient.
    "Exposure": _REVERSED,
    # Phase 5B: a count (0..however many lineups H1 says are being built),
    # same "more is better" reading as everything else in _GRADIENT -- a
    # heavily-used player earning the deepest colour is exactly the point.
    # Zero is also this column's overwhelmingly common value (most pool
    # players are rostered nowhere), so it's zero-excluded too, for the
    # same reason Own% is: an unrostered player is normal, not the bottom
    # of a gradient.
    "Used": _GRADIENT,
}

# Deliberately absent from FIELD_COLOR_SCALES: `Salary`/`DK Sal` -- a
# constraint, not a quality; scaling it would imply cheap is good.
# `GameID`/`TmRank` (Part 7.4) too -- GameID is an identifier, not a
# quantity; TmRank is a crude target-hierarchy PROXY (salary rank within
# team+position), and colour-scaling it would visually imply it's a
# measured quality worth ranking by, which docs/CALCULATIONS.md
# explicitly warns against reading it as.

# Columns where a real, common zero would otherwise anchor a gradient's
# low end (and, per PROMPT_BOARD_FIXES.md item 7, drag its MEDIAN midpoint
# down too) and compress everyone else's actual spread into a sliver of
# the scale (Fix 2.7 first found this for `Own%`; item 7, 2026-09-25,
# found the identical problem on 363/658 `ProjPts` zeros -- mostly OUT/
# deep-backup players -- and generalized the fix to every scaled column).
# Gets its own flat grey chip (added after the gradient so it wins -- see
# the shared insert-at-front note on FLAG_CHIPS in sheet_style.py); the
# gradient's own min AND mid are computed over non-zero values only via
# live MINIFS/MEDIAN(FILTER(...)) formulas (verified live, 2026-09-25,
# that Sheets accepts FILTER inside a gradient interpolation point's
# formula -- see CONTRIBUTING.md), not the raw minimum/50th-percentile.
#
# Every scaled column gets this EXCEPT: diverging columns (`_DIVERGING` --
# ImpliedMove/TotMove/SpdMove and Slate Grid's movement columns already
# render 0 as a meaningful white center; greying it would hide "no real
# move" as if it were missing data) and `Spread` (0 is a real pick'em, not
# missing data). `Own%`'s own fixed 20% (CHALK) midpoint is untouched --
# only `_GRADIENT`/`_REVERSED` columns get the new MEDIAN(FILTER(...))
# midpoint; `_WARM`'s own explicit midpoint always wins (see
# `_scale_rule_specs`).
#
# A handful of real scales elsewhere are deliberately NOT in this system
# at all (so they never pass through here) -- each is a genuinely bespoke,
# one-off column that never made it into FIELD_COLOR_SCALES: Exposure's
# own "Exposure" percentage, Results' "H2H %" (a real 0% is a genuine bad
# result, not missing data), and the SoS tabs' "Rank" (1..32, never zero).
ZERO_EXCLUDED_COLUMNS = frozenset(name for name, kind in FIELD_COLOR_SCALES.items() if kind != _DIVERGING) - {
    "Spread"
}
ZERO_GREY_BG = _rgb("#EDEEF1")


def _zero_exclude_formula(fn: str, ranges: str | list[str]) -> str:
    """`fn` applied over one range, or the combination of several -- used
    by both the MINIFS-based min and the MEDIAN(FILTER(...))-based mid
    below. A list of ranges (`apply_edge_position_scales`'s own multi-
    range calls, one position's rows scattered across several contiguous
    runs) combines every run's own non-zero values into the SAME
    computation rather than picking a min/median of any one run alone."""
    if isinstance(ranges, str):
        ranges = [ranges]
    if fn == "MIN":
        parts = [f'MINIFS({r},{r},"<>0")' for r in ranges]
        return f"MIN({','.join(parts)})" if len(parts) > 1 else parts[0]
    if fn == "MEDIAN":
        parts = [f"FILTER({r},{r}<>0)" for r in ranges]
        return f"MEDIAN({','.join(parts)})"
    raise ValueError(f"Unsupported zero-exclude function {fn!r}")


def _absolute(a1_range: str) -> str:
    """`K2:K19` -> `$K$2:$K$19` (an optional `Tab!` prefix is kept as is)."""
    prefix, _, ref = a1_range.rpartition("!")
    absolute = re.sub(r"(\$?)([A-Z]{1,3})(\$?)(\d+)", r"$\2$\4", ref)
    return f"{prefix}!{absolute}" if prefix else absolute


def diverging_anchor_kwargs(ranges: str | list[str]) -> dict:
    """Symmetric end anchors for a zero-centred (red -> white -> green) scale: the
    endpoints sit at -m and +m, where m is the column's largest absolute value.

    Without this the ends default to the column's own min/max, and a column with no
    negatives (or no positives) has its min (or max) AT the zero midpoint -- Sheets then
    paints every zero the end colour (Slate Grid's `Total move` read solid red where
    nothing moved, `Spread move` solid green). With symmetric anchors a zero is white
    whatever the data looks like, and a +1 and a -1 are equally saturated.
    """
    if isinstance(ranges, str):
        ranges = [ranges]
    # ABSOLUTE references, or Sheets shifts the formula per row: a cell below the column's one
    # non-zero value then evaluates a window with none in it, the anchors collapse onto the
    # midpoint, and its zero renders green (found live on Week 4's Slate Grid).
    joined = ",".join(_absolute(r) for r in ranges)
    bound = f"MAX(MAX({joined}),-MIN({joined}))"
    return {"min_type": "NUMBER", "min_value": f"=-{bound}", "max_type": "NUMBER", "max_value": f"={bound}"}


def _scale_rule_specs(
    a1: str,
    kind: str,
    name: str,
    *,
    zero_exclude_range: str | list[str],
    min_value: str | None = None,
    max_value: str | None = None,
) -> tuple[dict, dict | None]:
    """Shared dispatch building the rule SPECS (kwargs dicts for
    `SheetsClient.add_color_scale`/`add_boolean_rule`, each carrying its
    own `a1_range`) for one column -- never calls the client itself, so
    callers can either apply a spec immediately (a handful of whole-tab
    rules) or collect many and apply them in one batched
    `add_color_scales`/`add_boolean_rules`/`add_color_scales_multi_range`
    call (which can generate hundreds -- see `SheetsClient.
    add_color_scales`' own docstring for why that matters). Used by every
    `add_color_scale` call site in the workbook that scales a real
    `FIELD_COLOR_SCALES` field name (PROMPT_BOARD_FIXES.md item 7).

    `zero_exclude_range` is `a1` itself for a whole-tab scale, a group's
    own narrower range for a per-position/per-lineup scale, or a LIST of
    a position's own (possibly non-contiguous) row runs for `apply_edge_
    position_scales`'s multi-range calls -- see `_zero_exclude_formula`.
    Only replaces an explicit `min_value` when the caller didn't already
    provide one; there is no equivalent override for `mid_value` because
    no caller has ever needed one (a diverging/warm kind's own fixed
    midpoint below already wins ahead of it). Returns `(gradient_spec,
    boolean_spec_or_None)`.
    """
    min_kwargs: dict = {}
    if min_value is not None:
        min_kwargs = {"min_type": "NUMBER", "min_value": min_value}
    elif name in ZERO_EXCLUDED_COLUMNS:
        min_kwargs = {
            "min_type": "NUMBER",
            "min_value": f"={_zero_exclude_formula('MIN', zero_exclude_range)}",
        }
    max_kwargs = {"max_type": "NUMBER", "max_value": max_value} if max_value is not None else {}

    # PROMPT_BOARD_FIXES.md item 7: the midpoint must exclude zeros too,
    # not just the minimum -- most scales default to the 50th-percentile
    # midpoint, and half a column sitting at zero drags that median down
    # with it. Only for `_GRADIENT`/`_REVERSED` -- `_DIVERGING`'s own zero-
    # as-white center and `_WARM`'s own fixed CHALK threshold both already
    # have a meaningful, deliberately-chosen midpoint that this must never
    # override.
    mid_kwargs: dict = {}
    if name in ZERO_EXCLUDED_COLUMNS:
        mid_kwargs = {
            "mid_type": "NUMBER",
            "mid_value": f"={_zero_exclude_formula('MEDIAN', zero_exclude_range)}",
        }

    if kind == _DIVERGING:
        gradient_spec = {
            "a1_range": a1,
            "min_color": GRAD_MIN,
            "mid_color": WHITE,
            "max_color": GRAD_MAX,
            "mid_type": "NUMBER",
            "mid_value": "0",
            **diverging_anchor_kwargs(zero_exclude_range),
            **min_kwargs,
            **max_kwargs,
        }
    elif kind == _REVERSED:
        gradient_spec = {
            "a1_range": a1,
            "min_color": GRAD_MAX,
            "mid_color": GRAD_MID,
            "max_color": GRAD_MIN,
            **mid_kwargs,
            **min_kwargs,
            **max_kwargs,
        }
    elif kind == _WARM:
        # Week 3 feedback (A2), 2026-09-22: Sam: "Ownership highlighting is
        # hard to discern differences." Min/max were already adaptive to
        # the slate (non-zero MINIFS / real MAX -- Fix 2.7), so the actual
        # problem was the MIDPOINT: it defaulted (like every other scale
        # here) to the statistical median, but ownership is right-skewed
        # -- most players sit low, a few chalk plays sit high -- so the
        # median lands low too, and the entire "meaningfully different"
        # low-ownership majority gets crushed into the white-to-amber
        # third of the scale while the amber-to-red two-thirds is spent on
        # a handful of outliers. Anchoring the midpoint at
        # `CHALK_OWNERSHIP_THRESHOLD` instead (the same 20% line `Flag`
        # already calls out as CHALK) fixes that AND gives the transition
        # real meaning: white-to-amber is "below the chalk line," amber-
        # to-red is "how far past it."
        gradient_spec = {
            "a1_range": a1,
            "min_color": WARM_MIN,
            "mid_color": WARM_MID,
            "max_color": WARM_MAX,
            "mid_type": "NUMBER",
            "mid_value": str(CHALK_OWNERSHIP_THRESHOLD),
            **min_kwargs,
            **max_kwargs,
        }
    else:
        gradient_spec = {
            "a1_range": a1,
            "min_color": GRAD_MIN,
            "mid_color": GRAD_MID,
            "max_color": GRAD_MAX,
            **mid_kwargs,
            **min_kwargs,
            **max_kwargs,
        }

    boolean_spec = None
    if name in ZERO_EXCLUDED_COLUMNS:
        # Added AFTER the gradient above (later in the same batch, or a
        # later individual call), so it lands at index 0 and wins for any
        # exact-zero cell -- see FLAG_CHIPS' comment (sheet_style.py) on
        # add_boolean_rule/add_color_scale's shared insert-at-front
        # behavior, verified against a live sheet's raw conditionalFormats
        # metadata.
        boolean_spec = {
            "a1_range": a1,
            "condition_type": "NUMBER_EQ",
            "values": ["0"],
            "fmt": {"backgroundColor": ZERO_GREY_BG},
        }
    return gradient_spec, boolean_spec


# ---------------------------------------------------------------------------
# Round 5 item 3: five-band formula rules
# ---------------------------------------------------------------------------
#
# Percentile | Colour
#   >= 90    | strong green
#   70 - 90  | light green
#   30 - 70  | none
#   10 - 30  | light red
#   < 10     | red
#   0/blank  | none  (an exact zero keeps its grey chip, see `ZERO_GREY_BG`)
#
# Every rule is a custom formula with RELATIVE row references, applied to a whole
# column, so it moves with the row through any sort, filter or sync -- no per-block
# ranges, no re-polish needed after a sync. No new hues: the two strong colours are the
# existing gradient ends (`GRAD_MAX`/`GRAD_MIN`), the light ones a 50% tint of each.


def _tint(color: dict, amount: float = 0.5) -> dict:
    return {k: round(1 - amount * (1 - v), 4) for k, v in color.items()}


BAND_STRONG_GREEN = GRAD_MAX
BAND_LIGHT_GREEN = _tint(GRAD_MAX)
BAND_LIGHT_RED = _tint(GRAD_MIN)
BAND_STRONG_RED = GRAD_MIN

# Cut-offs on a 0-100 percentile (`PERCENTRANK * 100` for game metrics).
PCT_STRONG, PCT_LIGHT, PCT_LIGHT_LOW, PCT_STRONG_LOW = 90, 70, 30, 10

# Rule 3, `Leverage` (CeilPct - OwnPct) is centred on 0 and spans -100..+100, not
# 0-100, so its bands are on the value itself: +40/+15 above, -15/-40 below.
LEVERAGE_BANDS = (40, 15, -15, -40)

# `OppPosRank` is 1-32 (LOW = tough matchup): the 90/70/30/10 cut-offs, expressed as
# ranks over 32 teams -- the top ~10% (<= 4), top ~30% (<= 10), bottom ~30% (>= 23),
# bottom ~10% (>= 29).
RANK_BANDS = (4, 10, 23, 29)


def _bool_spec(a1_range: str, formula: str, color: dict) -> dict:
    return {
        "a1_range": a1_range,
        "condition_type": "CUSTOM_FORMULA",
        "values": [formula],
        "fmt": {"backgroundColor": color},
    }


def band_rule_specs(
    kind: str,
    letter: str,
    first_row: int,
    last_row: int,
    *,
    pct_letter: str | None = None,
    allow_zero: bool = False,
) -> list[dict]:
    """The four coloured bands (the middle band is deliberately no colour) for one
    column, as boolean-rule specs over `letter{first_row}:letter{last_row}`.

    `kind` picks what is compared: `_PCT` reads the row's hidden percentile helper
    (`pct_letter`), `_SCORE`/`_LEVERAGE`/`_RANK` compare the cell itself, and
    `_GAME`/`_GAME_REVERSED` compare `PERCENTRANK` of the cell against the column
    (`_GAME_REVERSED`: LOW is good). A zero or a non-number never matches any band
    (unless `allow_zero`, for `Spread`, where 0 is a real pick'em)."""
    a1 = f"{letter}{first_row}:{letter}{last_row}"
    cell = f"${letter}{first_row}"
    numeric = f"ISNUMBER({cell})" if allow_zero else f"ISNUMBER({cell}),{cell}<>0"

    if kind == _PCT:
        if pct_letter is None:
            return []
        p = f"${pct_letter}{first_row}"
        guard = f"ISNUMBER({p})"
        sg = f"=AND({guard},{p}>={PCT_STRONG})"
        lg = f"=AND({guard},{p}>={PCT_LIGHT},{p}<{PCT_STRONG})"
        lr = f"=AND({guard},{p}>={PCT_STRONG_LOW},{p}<{PCT_LIGHT_LOW})"
        sr = f"=AND({guard},{p}<{PCT_STRONG_LOW})"
    elif kind == _SCORE:
        sg = f"=AND({numeric},{cell}>={PCT_STRONG})"
        lg = f"=AND({numeric},{cell}>={PCT_LIGHT},{cell}<{PCT_STRONG})"
        lr = f"=AND({numeric},{cell}>={PCT_STRONG_LOW},{cell}<{PCT_LIGHT_LOW})"
        sr = f"=AND({numeric},{cell}<{PCT_STRONG_LOW})"
    elif kind == _LEVERAGE:
        a, b, c, d = LEVERAGE_BANDS
        sg = f"=AND({numeric},{cell}>={a})"
        lg = f"=AND({numeric},{cell}>={b},{cell}<{a})"
        lr = f"=AND({numeric},{cell}<={c},{cell}>{d})"
        sr = f"=AND({numeric},{cell}<={d})"
    elif kind == _RANK:
        a, b, c, d = RANK_BANDS
        sg = f"=AND({numeric},{cell}<={a})"
        lg = f"=AND({numeric},{cell}>{a},{cell}<={b})"
        lr = f"=AND({numeric},{cell}>={c},{cell}<{d})"
        sr = f"=AND({numeric},{cell}>={d})"
    elif kind in (_GAME, _GAME_REVERSED):
        rank = f"PERCENTRANK(${letter}${first_row}:${letter}${last_row},{cell})"
        # High-is-good reads `PERCENTRANK * 100`; low-is-good mirrors it.
        hi = lo = f"{rank}*100" if kind == _GAME else f"(1-{rank})*100"
        sg = f"=AND({numeric},{hi}>={PCT_STRONG})"
        lg = f"=AND({numeric},{hi}>={PCT_LIGHT},{hi}<{PCT_STRONG})"
        lr = f"=AND({numeric},{lo}>={PCT_STRONG_LOW},{lo}<{PCT_LIGHT_LOW})"
        sr = f"=AND({numeric},{lo}<{PCT_STRONG_LOW})"
    else:
        return []
    return [
        _bool_spec(a1, sg, BAND_STRONG_GREEN),
        _bool_spec(a1, lg, BAND_LIGHT_GREEN),
        _bool_spec(a1, lr, BAND_LIGHT_RED),
        _bool_spec(a1, sr, BAND_STRONG_RED),
    ]


BAND_KINDS = frozenset({_PCT, _SCORE, _LEVERAGE, _RANK, _GAME, _GAME_REVERSED})

# Every player metric's hidden within-position percentile column on EdgeRaw, keyed by
# the header text that metric carries on ANY tab (Player Pool/Lineups call ProjPts
# "Pts" and Ceiling "Ceil").
PCT_HELPER_FOR_FIELD = {
    "ProjPts": "ProjPts%ile",
    "Pts": "ProjPts%ile",
    "AggPts": "AggPts%ile",
    "Ceiling": "Ceiling%ile",
    "Ceil": "Ceiling%ile",
    "Val": "Val%ile",
    "CeilVal": "CeilVal%ile",
}


def column_rule_specs(
    name: str,
    letter: str,
    first_row: int,
    last_row: int,
    *,
    header: list | None = None,
    pct_letter: str | None = None,
) -> tuple[list[dict], list[dict]]:
    """`(gradient_specs, boolean_specs)` for one `FIELD_COLOR_SCALES` column over
    `letter{first_row}:letter{last_row}` -- the single dispatch every call site uses.

    A band kind gives no gradient and its four bands plus the grey zero chip
    (added LAST, so it wins any exact-zero cell); every other kind (Own%, movement,
    Exposure) gives its gradient and chip exactly as `_scale_rule_specs` always did.
    `pct_letter` overrides the helper column found by name in `header` (Board's
    hidden lookup column)."""
    kind = FIELD_COLOR_SCALES[name]
    a1 = f"{letter}{first_row}:{letter}{last_row}"
    if kind not in BAND_KINDS:
        gradient_spec, boolean_spec = _scale_rule_specs(a1, kind, name, zero_exclude_range=a1)
        return [gradient_spec], ([boolean_spec] if boolean_spec is not None else [])
    if kind == _PCT and pct_letter is None and header is not None:
        helper = PCT_HELPER_FOR_FIELD.get(name)
        if helper in header:
            pct_letter = column_letter(header.index(helper))
    booleans = band_rule_specs(
        kind, letter, first_row, last_row, pct_letter=pct_letter, allow_zero=(name == "Spread")
    )
    if name in ZERO_EXCLUDED_COLUMNS:
        booleans.append(
            {
                "a1_range": a1,
                "condition_type": "NUMBER_EQ",
                "values": ["0"],
                "fmt": {"backgroundColor": ZERO_GREY_BG},
            }
        )
    return [], booleans
