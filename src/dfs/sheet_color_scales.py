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
# Sam, 2026-10-02: keep the soft pastels, only slightly more prominent -- the end colours sit about
# 15% of the way from the old pastels (#F5CCCC / #B8E0B8) toward Sheets' own default scale
# (#E67C73 / #57BB8A), which he found "way too much to stare at a whole sheet of". (A first try at
# 40% was too strong: "I think I liked the old colors better ... maybe 10-20% more".)
GRAD_MIN = {"red": 0.95, "green": 0.75, "blue": 0.75}  # ~#F2C0BF
GRAD_MID = {"red": 1.0, "green": 1.0, "blue": 0.80}
GRAD_MAX = {"red": 0.66, "green": 0.86, "blue": 0.69}  # ~#A8DBB0

# Sam, 2026-10-01: no fixed cut-offs, and a colour means one thing per column. Two shapes:
#
# - STEPS (`_PCT`, `_SCORE`): the player metrics. Compared WITHIN POSITION (a QB only against
#   QBs), as a percentile rank, in many small shades (see `step_rule_specs`) -- Round 5 item 3's
#   five wide bands made 3.00 white and 3.14 light green; ten shades per side make neighbouring
#   values differ by a shade you can barely see, while the colour still follows the row through
#   any sort or filter (a true gradient cannot: it only sees its own range).
# - GRADIENT (`_GRADIENT`/`_REVERSED`/`_DIVERGING`/`_WARM`): everything that has no position (game
#   and team metrics, ownership, exposure, movement): a smooth red -> white -> green gradient,
#   white at the column's median, full colour at its 5th/95th percentile.
#
# Exact zeros are grey in both. Nothing is a fixed number.
_PCT = "pct"  # a player metric: stepped by its hidden within-position percentile helper
_SCORE = "score"  # already a 0-100 percentile (ValAdj, CeilPct, GameEnv): stepped on its own value
_GRADIENT = "gradient"  # red -> white -> green, more is better
_REVERSED = "reversed"  # green -> white -> red, LOW is better
_DIVERGING = "diverging"  # red -> white -> green, zero is the midpoint
# Where the gradient reaches full colour, as a percentile of the column's non-zero values.
EDGE_LOW_PERCENTILE, EDGE_HIGH_PERCENTILE = 0.05, 0.95
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
    # Player metrics: within-position percentile, stepped (`derived.PLAYER_METRIC_PCT_COLUMNS`).
    "ProjPts": _PCT,
    "AggPts": _PCT,
    "Pts": _PCT,
    "Ceiling": _PCT,
    "Ceil": _PCT,
    "Val": _PCT,
    "CeilVal": _PCT,
    # Edge Finder (2026-10-07): the calibrated projection and last-3 xFP are compared within position
    # like the other player metrics (hidden helpers `CalPts%ile`, `xFP/G%ile`).
    "CalPts": _PCT,
    "xFP/G": _PCT,
    # Usage volume (2026-10-02): compared within position, like ProjPts -- a 25% target share
    # means something different for a WR and an RB. Blank where a metric doesn't apply.
    "Tgt%": _PCT,
    "WOPR": _PCT,
    "Rush%": _PCT,
    "RZ/G": _PCT,
    "HVT/G": _PCT,
    # Already 0-100 percentile scores: stepped on their own value.
    "ValAdj": _SCORE,
    "CeilPct": _SCORE,
    "GameEnv": _SCORE,
    # Outcome probabilities (0-100). Stepped bands around 50 would paint every Boom% (typically 5-30)
    # red, so these are gradients anchored on the column's own 5th/95th percentile; Bust% is the
    # reverse (low is good).
    "Hit3x%": _GRADIENT,
    "Boom%": _GRADIENT,
    "Bust%": _REVERSED,
    # Game/team metrics have no position: a smooth gradient over the column. More is better.
    "PROE": _GRADIENT,
    "Expl%": _GRADIENT,
    "Team Implied": _GRADIENT,
    "O/U": _GRADIENT,
    "OU": _GRADIENT,
    "OverUnder": _GRADIENT,
    "Total": _GRADIENT,
    "GPS": _GRADIENT,
    # Higher = softer matchup for every position (DST sign-flipped).
    "OppEPA": _GRADIENT,
    # Slate Grid's TEAMS section: more is better for an offence on all of these. For "Opp Def"
    # a higher number means the opponent's defense allows more, i.e. a softer spot.
    "Implied": _GRADIENT,
    "Off EPA/play": _GRADIENT,
    "Off EPA/pass": _GRADIENT,
    "Off EPA/rush": _GRADIENT,
    "Opp Def EPA/pass": _GRADIENT,
    "Opp Def EPA/rush": _GRADIENT,
    # Lower is better: Pace (seconds per snap, faster is better) and Spread (a bigger favourite).
    "Pace": _REVERSED,
    "Spread": _REVERSED,
    # OppPosRank is a 1-32 matchup rank where the LOW end reads as the good colour (it always
    # did under the bands: rank <= 4 was the strong green).
    "OppPosRank": _REVERSED,
    # CeilPct - OwnPct is -100..+100 and centred on 0: zero is a real, meaningful white.
    "Leverage": _DIVERGING,
    # Movement diverges around zero; Own% is the warm scale (high ownership is a caution).
    "ImpliedMove": _DIVERGING,
    "TotMove": _DIVERGING,
    "SpdMove": _DIVERGING,
    "Own%": _WARM,
    # Low is comfortable, high is concentration, so Exposure is a reversed gradient.
    "Exposure": _REVERSED,
    # A count of lineups using the player: a heavily-used player earning the deepest colour is
    # the point, and an unrostered player (the common zero) is grey, not the bottom of the scale.
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
# missing data). Every other kind, `_WARM` (Own%) included, anchors its low end and
# midpoint to the column's own non-zero values.
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


def _zero_exclude_formula(fn: str, ranges: str | list[str], q: float | None = None) -> str:
    """`fn` applied over one range, or the combination of several -- the MINIFS-style min, the
    MEDIAN(FILTER(...)) mid and the PERCENTILE(FILTER(...)) end anchors below all go through
    here. A list of ranges (a column whose data sits in several row blocks) combines every
    block's own non-zero values into the SAME computation rather than picking a value from
    any one block alone.

    Every range goes through `_absolute`: Sheets shifts a RELATIVE reference inside a
    colour-scale anchor formula per row (see `diverging_anchor_kwargs`), so a cell far
    down the column would evaluate a window that has slid off the data."""
    if isinstance(ranges, str):
        ranges = [ranges]
    ranges = [_absolute(r) for r in ranges]
    if fn == "MIN":
        parts = [f'MINIFS({r},{r},"<>0")' for r in ranges]
        return f"MIN({','.join(parts)})" if len(parts) > 1 else parts[0]
    if fn == "MEDIAN":
        parts = [f"FILTER({r},{r}<>0)" for r in ranges]
        return f"MEDIAN({','.join(parts)})"
    if fn == "PERCENTILE":
        parts = [f"FILTER({r},{r}<>0)" for r in ranges]
        data = parts[0] if len(parts) == 1 else "{" + ";".join(parts) + "}"
        return f"PERCENTILE({data},{q})"
    if fn == "PERCENTILE_ALL":  # zero is a real value here (Spread): no filter
        data = ranges[0] if len(ranges) == 1 else "{" + ";".join(ranges) + "}"
        return f"PERCENTILE({data},{q})"
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
    no caller has ever needed one (a diverging kind's own zero midpoint
    below already wins ahead of it). Returns `(gradient_spec,
    boolean_spec_or_None)`.
    """
    # Sam, 2026-10-01: white at the column's median, full colour only near the extremes. The
    # ends sit at the 5th/95th percentile of the column's non-zero values (not its min/max), so
    # one outlier can't flatten everyone else's colour; values beyond them just stay at the end
    # colour. A diverging kind (zero is its midpoint) keeps its own symmetric anchors.
    zero_excluded = name in ZERO_EXCLUDED_COLUMNS
    pct_fn = "PERCENTILE" if zero_excluded else "PERCENTILE_ALL"
    min_kwargs: dict = {}
    if min_value is not None:
        min_kwargs = {"min_type": "NUMBER", "min_value": min_value}
    elif kind != _DIVERGING:
        min_kwargs = {
            "min_type": "NUMBER",
            "min_value": f"={_zero_exclude_formula(pct_fn, zero_exclude_range, EDGE_LOW_PERCENTILE)}",
        }
    max_kwargs = {"max_type": "NUMBER", "max_value": max_value} if max_value is not None else {}
    if max_value is None and kind != _DIVERGING:
        max_kwargs = {
            "max_type": "NUMBER",
            "max_value": f"={_zero_exclude_formula(pct_fn, zero_exclude_range, EDGE_HIGH_PERCENTILE)}",
        }

    # The midpoint must exclude zeros too (PROMPT_BOARD_FIXES.md item 7): a scale's default
    # midpoint is the 50th percentile, and half a column sitting at zero drags that down with it.
    mid_kwargs: dict = {}
    if zero_excluded:
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
            "mid_color": WHITE,
            "max_color": GRAD_MIN,
            **mid_kwargs,
            **min_kwargs,
            **max_kwargs,
        }
    elif kind == _WARM:
        # White -> amber -> red, high ownership is a caution. Week 3 feedback (A2) + Sam,
        # 2026-10-02 ("the roster percent numbers are really flat in terms of colour spread"):
        # ownership is right-skewed -- most players sit low, a few chalk plays sit high -- so a
        # median midpoint (~5%) crushes almost every value into the same pale amber. Anchoring the
        # midpoint at `CHALK_OWNERSHIP_THRESHOLD` (the same 20% line `Flags` calls CHALK) gives the
        # white-to-amber half real meaning ("below the chalk line") and spends the amber-to-red
        # half on "how far past it". The low end is the column's lowest NON-ZERO value and the high
        # end its real maximum (no percentile trimming: the few chalk plays ARE the point).
        gradient_spec = {
            "a1_range": a1,
            "min_color": WARM_MIN,
            "mid_color": WARM_MID,
            "max_color": WARM_MAX,
            "mid_type": "NUMBER",
            "mid_value": str(CHALK_OWNERSHIP_THRESHOLD),
            "min_type": "NUMBER",
            "min_value": f"={_zero_exclude_formula('MIN', zero_exclude_range)}",
            **({"max_type": "NUMBER", "max_value": max_value} if max_value is not None else {}),
        }
    else:
        gradient_spec = {
            "a1_range": a1,
            "min_color": GRAD_MIN,
            "mid_color": WHITE,
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


def _zero_chip(a1_range: str) -> dict:
    """The flat grey chip for an exact zero. Added AFTER the colour rules (later in the same
    batch), so it lands at index 0 and wins for any exact-zero cell."""
    return {
        "a1_range": a1_range,
        "condition_type": "NUMBER_EQ",
        "values": ["0"],
        "fmt": {"backgroundColor": ZERO_GREY_BG},
    }


# ---------------------------------------------------------------------------
# Steps: within-position percentile, many small shades
# ---------------------------------------------------------------------------
#
# A percentile p in 0-100 (50 = the typical player at his position). The middle 20% (40-60) is
# left plain. Beyond that the shade deepens with the distance d = |p - 50| from the middle: one
# step per 5 points out to d = 25 (p 75 / 25), then HALF-SIZE steps (2.5 points) from there to
# d = 47.5, then one open-ended top/bottom shade (p >= 97.5 / <= 2.5). The fine steps sit where
# the players Sam actually weighs live -- the top of each position -- so a tight group of near
# top players still lands on distinguishable shades (Sam, 2026-10-02: "the top 10 WRs are all
# projected within 5 points of each other ... tough to tell who's the better play").
# Each shade is a straight blend from white to the strong colour, `amount` rising linearly with
# d from 12% (the first step) to 100% (the last, which IS `GRAD_MAX`/`GRAD_MIN`).
STEP_PLAIN_HALF = 10  # |p - 50| below this is left plain (the middle 20%)
STEP_EDGES = [10, 15, 20, 25, 27.5, 30, 32.5, 35, 37.5, 40, 42.5, 45, 47.5]  # lower bound of each d band
STEP_LEVELS = len(STEP_EDGES)  # shades per side; the last one is open-ended
STEP_FIRST_AMOUNT = 0.12

# Every player metric's hidden within-position percentile column on EdgeRaw, keyed by the header
# text that metric carries on ANY tab (Player Pool/Lineups call ProjPts "Pts", Ceiling "Ceil").
PCT_HELPER_FOR_FIELD = {
    "ProjPts": "ProjPts%ile",
    "Pts": "ProjPts%ile",
    "AggPts": "AggPts%ile",
    "Ceiling": "Ceiling%ile",
    "Ceil": "Ceiling%ile",
    "Val": "Val%ile",
    "CeilVal": "CeilVal%ile",
    "Tgt%": "Tgt%ile",
    "WOPR": "WOPR%ile",
    "Rush%": "Rush%ile",
    "RZ/G": "RZ/G%ile",
    "HVT/G": "HVT/G%ile",
    "CalPts": "CalPts%ile",
    "xFP/G": "xFP/G%ile",
}


def _blend(strong: dict, amount: float) -> dict:
    """`amount` of `strong` over white (0 = white, 1 = `strong`)."""
    return {k: round(1 - amount * (1 - v), 4) for k, v in strong.items()}


def _bool_spec(a1_range: str, formula: str, color: dict) -> dict:
    return {
        "a1_range": a1_range,
        "condition_type": "CUSTOM_FORMULA",
        "values": [formula],
        "fmt": {"backgroundColor": color},
    }


def step_rule_specs(
    kind: str, letter: str, first_row: int, last_row: int, *, pct_letter: str | None = None
) -> list[dict]:
    """The 2 x `STEP_LEVELS` shaded steps (middle left plain) for one column, as boolean-rule specs
    over `letter{first_row}:letter{last_row}`. `_PCT` reads the row's hidden percentile helper
    (`pct_letter`; none given -> no rules, never a guess), `_SCORE` the cell itself. A blank, a
    non-number or a zero never matches (a zero gets the grey chip instead). Rules use a RELATIVE row
    reference, so a colour moves with its row through any sort or filter."""
    a1 = f"{letter}{first_row}:{letter}{last_row}"
    if kind == _PCT:
        if pct_letter is None:
            return []
        p = f"${pct_letter}{first_row}"
        guard = f"ISNUMBER({p})"
    elif kind == _SCORE:
        p = f"${letter}{first_row}"
        guard = f"ISNUMBER({p}),{p}<>0"
    else:
        return []
    specs = []
    top = STEP_EDGES[-1]
    for i, lo in enumerate(STEP_EDGES):
        last = i == STEP_LEVELS - 1
        hi = None if last else STEP_EDGES[i + 1]
        amount = STEP_FIRST_AMOUNT + (1 - STEP_FIRST_AMOUNT) * (lo - STEP_PLAIN_HALF) / (
            top - STEP_PLAIN_HALF
        )
        up = f"{p}>={50 + lo:g}" + ("" if last else f",{p}<{50 + hi:g}")
        down = f"{p}<={50 - lo:g}" + ("" if last else f",{p}>{50 - hi:g}")
        specs.append(_bool_spec(a1, f"=AND({guard},{up})", _blend(GRAD_MAX, amount)))
        specs.append(_bool_spec(a1, f"=AND({guard},{down})", _blend(GRAD_MIN, amount)))
    return specs


def _pct_letter_for(name: str, header: list | None, pct_letter: str | None) -> str | None:
    if pct_letter is not None or header is None:
        return pct_letter
    helper = PCT_HELPER_FOR_FIELD.get(name)
    return column_letter(header.index(helper)) if helper in header else None


STEP_KINDS = frozenset({_PCT, _SCORE})


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

    A step kind gives no gradient and its sixteen shaded steps plus the grey zero chip (added
    LAST, so it wins any exact-zero cell); every other kind gives one smooth gradient and its
    chip. `pct_letter` overrides the helper column found by name in `header` (Board's hidden
    lookup column)."""
    kind = FIELD_COLOR_SCALES[name]
    a1 = f"{letter}{first_row}:{letter}{last_row}"
    if kind in STEP_KINDS:
        booleans = step_rule_specs(
            kind, letter, first_row, last_row, pct_letter=_pct_letter_for(name, header, pct_letter)
        )
        if name in ZERO_EXCLUDED_COLUMNS:
            booleans.append(_zero_chip(a1))
        return [], booleans
    gradient_spec, boolean_spec = _scale_rule_specs(a1, kind, name, zero_exclude_range=a1)
    return [gradient_spec], ([boolean_spec] if boolean_spec is not None else [])


def grouped_column_rule_specs(
    name: str,
    letter: str,
    groups: list[tuple[int, int]],
    *,
    header: list | None = None,
) -> tuple[dict | None, list[dict]]:
    """Same as `column_rule_specs`, for a column whose data sits in several row blocks (Player
    Pool's positions, Lineups' lineups). A step kind is one set of rules over the blocks' overall
    span (header/totals rows between them hold text or blanks, which no step matches). A gradient
    kind is ONE gradient over the union of the blocks, so it scales across the whole column and
    the totals rows stay out of it. Returns `(gradient_spec_with_a1_ranges_or_None,
    boolean_specs)` -- the gradient spec carries `a1_ranges` (plural) for
    `SheetsClient.add_color_scales_multi_range`."""
    kind = FIELD_COLOR_SCALES[name]
    span = f"{letter}{min(start for start, _ in groups)}:{letter}{max(end for _, end in groups)}"
    if kind in STEP_KINDS:
        first = min(start for start, _ in groups)
        last = max(end for _, end in groups)
        booleans = step_rule_specs(kind, letter, first, last, pct_letter=_pct_letter_for(name, header, None))
        if name in ZERO_EXCLUDED_COLUMNS:
            booleans.append(_zero_chip(span))
        return None, booleans
    ranges = [f"{letter}{start}:{letter}{end}" for start, end in groups]
    gradient_spec, boolean_spec = _scale_rule_specs(ranges[0], kind, name, zero_exclude_range=ranges)
    gradient_spec["a1_ranges"] = ranges
    del gradient_spec["a1_range"]
    return gradient_spec, ([_zero_chip(span)] if boolean_spec is not None else [])
