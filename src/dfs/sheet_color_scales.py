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

from dfs.derived import CHALK_OWNERSHIP_THRESHOLD


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
    "ProjPts": _GRADIENT,
    "AggPts": _GRADIENT,
    "Pts": _GRADIENT,
    "Ceiling": _GRADIENT,
    "Ceil": _GRADIENT,
    "Val": _GRADIENT,
    "CeilVal": _GRADIENT,
    # Part 7.2: unlike Val/CeilVal, already a per-position residual
    # (Position-regressed against Salary on EdgeRaw itself), so -- same
    # reasoning as CeilPct just below -- a flat whole-tab scale is
    # already meaningful; see EDGE_UNSCALED_PLAYER_METRICS/
    # GROUPED_TAB_UNSCALED_COLUMNS (sheet_style.py) for why EdgeRaw scales
    # this one but Player Pool/Lineups skip re-grouping it.
    "ValAdj": _GRADIENT,
    "Leverage": _GRADIENT,
    "GameEnv": _GRADIENT,
    # Part C, C7: team-level, not player/position-skewed (every player on
    # a team shares one value, same as GameEnv/OverUnder/Spread just
    # above/below) -- a flat whole-tab scale is meaningful on EdgeRaw with
    # no EDGE_UNSCALED_PLAYER_METRICS exclusion needed, unlike raw Pts/
    # Ceil/Val. Pace alone is reversed per C7's own instruction: lower
    # (faster) is the interesting/good direction.
    "Pace": _REVERSED,
    "PROE": _GRADIENT,
    "Expl%": _GRADIENT,
    "Team Implied": _GRADIENT,
    "O/U": _GRADIENT,
    "OU": _GRADIENT,
    "OverUnder": _GRADIENT,
    "Total": _GRADIENT,
    "ImpliedMove": _DIVERGING,
    "TotMove": _DIVERGING,
    "SpdMove": _DIVERGING,
    # Week 3 feedback (A1), found live 2026-09-22: Spread was already here,
    # but as _DIVERGING -- zero as a neutral midpoint, negative (this
    # team's own favorite side) mapped to red, positive (underdog) mapped
    # to green. That's backwards for what Spread actually means: unlike
    # ImpliedMove/TotMove/SpdMove (direction-agnostic deltas, where
    # "which way is good" depends on who you rostered), a more negative
    # Spread always means a bigger favorite -- the same fixed, monotonic
    # "lower is better" reading OppPosRank already gets below. Sam:
    # "Spread syntax highlighting is backwards, lower numbers are better."
    "Spread": _REVERSED,
    # A low OppPosRank is the tough matchup here (this opponent allows the
    # FEWEST fantasy points at this position) -- same "1st is best"
    # convention as the SoS tabs' own `Rank` column (`style_sos_tab`).
    "OppPosRank": _REVERSED,
    "Own%": _WARM,
    # Phase 4 (4.1): already percentile-within-position, 0-100 regardless
    # of which positions happen to be mixed into the range they're scaled
    # over -- unlike raw Pts/Ceil/Val/CeilVal, scaling these doesn't need
    # a position-grouped range to mean something. See
    # EDGE_UNSCALED_PLAYER_METRICS/GROUPED_TAB_UNSCALED_COLUMNS
    # (sheet_style.py) for why EdgeRaw keeps these two and Player
    # Pool/Lineups skip them.
    "CeilPct": _GRADIENT,
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
