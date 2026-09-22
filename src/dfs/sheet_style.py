"""Presentation-only styling for the workbook: widths, freeze panes, number
formats, header treatment, conditional-format chips, tab colour/order/
visibility.

Deliberately scoped to things that CANNOT invalidate a hardcoded position.
Nothing here inserts, deletes, moves or renames a column, row or tab, and
nothing writes a cell value -- so no VLOOKUP index, no `LINEUPS_NAME_BLOCKS`
range, no `bankroll.cash.header_row`, and no `tab_mappings` entry can be
broken by running any of it. That is the whole design constraint; if a
future addition here needs to move something, it belongs in a different
module with a changelog entry attached.

Column positions are derived from `derived.EDGE_COLUMNS` rather than written
as literal letters, for the same reason `sheet_links.py` derives its VLOOKUP
indices: when that list changes, this file follows it instead of silently
formatting the wrong column.

Re-runnable. Every styling function clears the tab's existing conditional
formats (and, where relevant, its column groups/banding) before adding its
own, so running twice leaves the same result as running once rather than
stacking duplicate rules -- verified directly by `dfs setup audit-style`.

`polish_edge()` applies EdgeRaw's frozen header and its colour scales as
part of this pass -- the standalone `dfs sheets format-edge` command that
used to apply a couple of these effects on its own was removed once
`polish` fully superseded it (nothing else called it, and maintaining two
implementations of the same colour scales was pure drift risk).

VISUAL GRAMMAR -- the whole workbook obeys this, not just EdgeRaw. If a new
tab or column needs styling, it fits one of these; if it doesn't, that's a
sign the new thing needs a new rule stated here, not a one-off in whatever
function happens to touch it first.

- Dark header (`_HEADER_FMT`, #20262F/white/bold) means "this row is a
  table header." Nothing else in the workbook uses this fill.
- Pale yellow (`INPUT_BG`, #FFFDF5) means "you type here" -- and is the
  ONLY thing that means that. Exactly five places carry it: EdgeRaw's Pool
  column, Player Pool's own add-a-player control cell
  (`sheet_pool_control.ensure_pool_control_row`), Lineups' block column A
  (`polish_lineups_input_column`), Exposure's Target column, and the pool
  deck's B1/D1/F1 controls (`polish_pool_deck`). Everywhere else is a
  formula; if it isn't pale yellow, don't type into it.
- Red -> yellow -> green (`GRAD_MIN`/`GRAD_MID`/`GRAD_MAX`, via
  `add_color_scale`) means "more is better," for the decision numbers
  named in `FIELD_COLOR_SCALES`/`FIELD_FORMATS`. Reversed (max color at the
  low end) for a rank column, where 1st is best. A true diverging scale
  (`mid_type="NUMBER", mid_value="0"`, white midpoint) is for a signed
  delta where zero -- not the median -- is the meaningful center: ImpliedMove,
  TotMove, SpdMove and Spread, nowhere else.
- White -> amber -> red (`WARM_MIN`/`WARM_MID`/`WARM_MAX`, kind `_WARM`) is
  the ONE exception to "more is better": ownership (`Own%` -- Phase 6,
  Part 2 unified EdgeRaw's own `ProjOwn` and Player Pool/Lineups' own
  `Rstr%` into this one shared name). High ownership is chalk -- a caution,
  not a quality -- so it never gets the green=good treatment, and
  deliberately reuses `WARN_BG`/`CRIT_BG`'s exact hues rather than
  inventing a fourth palette, so it still reads as the workbook's existing
  warning language.
- Flat grey (`ZERO_GREY_BG`) on an exact `0` in a `ZERO_EXCLUDED_COLUMNS`
  column (`Own%`) means "no real value yet," not
  "the worst of the range" -- a real, common zero (unpublished ownership
  reads 0 for the whole slate until midweek) would otherwise anchor a
  gradient's low end and compress everyone else's actual spread into a
  sliver of it. The gradient's own minpoint for these columns is computed
  over non-zero values only (a live `MINIFS` formula), and the grey chip
  is added after the gradient so it wins on an exact zero -- both rely on
  the same insert-at-front behavior described on `FLAG_CHIPS` below.
- Chips (`_chip`, solid background + bold matching text) mark categorical
  STATE only -- Flags, Avail, the Guardrails column, position tints. Never
  put a chip on a number; that's what the colour scales are for.
- Grey (`INK_MUTED`) on `OwnStatus` (renamed from `LevBasis`, Part 7.9)
  marks a data-freshness note, not a value of its own -- it reads
  "unpublished" while Own% (and therefore Leverage) hasn't been populated
  by TFFB yet this week (see `polish_edge`).
- Colour that doesn't encode a value gets removed, full stop. Banding and
  the position tint are deliberately near-invisible for this reason: they
  carry structure (which row, which position), not a value, so they must
  never compete with a scale or a chip for attention.
"""

from __future__ import annotations

from dfs.derived import CHALK_OWNERSHIP_THRESHOLD, EDGE_COLUMNS, EDGE_DATA_OFFSET, ZONE_LABELS
from dfs.sheet_links import LINKED_EDGE_COLUMNS
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN, POOL_HEADER

# Rows to format on the big tabs. EdgeRaw currently carries ~743 players;
# 1000 is the provisioned depth and costs nothing to format past the data.
EDGE_ROWS = 1000
POOL_RAW_ROWS = 987

# ---------------------------------------------------------------------------
# Palette. Sheets wants 0-1 floats, not hex.
# ---------------------------------------------------------------------------


def _rgb(hex_str: str) -> dict:
    h = hex_str.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }


INK = _rgb("#12161C")
INK_MUTED = _rgb("#798494")
WHITE = _rgb("#FFFFFF")
HEADER_BG = _rgb("#20262F")
BAND_BG = _rgb("#F7F8FA")

OK_BG, OK_FG = _rgb("#DCEBE3"), _rgb("#15704A")
WARN_BG, WARN_FG = _rgb("#F7E9CF"), _rgb("#8F5406")
CRIT_BG, CRIT_FG = _rgb("#F8DEDA"), _rgb("#A22C23")
FLAT_BG, FLAT_FG = _rgb("#E7EBF0"), _rgb("#4A5563")

# The ONE cue for "you type here", everywhere in the workbook -- see the
# visual-grammar docstring below. Every typed cell uses this and nothing
# else uses it: EdgeRaw's Pool column, Player Pool's add-a-player control
# cell, Lineups block column A, Exposure's Target column, and the pool
# deck's B1/D1/F1 controls.
INPUT_BG = _rgb("#FFFDF5")

# The red -> yellow -> green gradient the now-removed `dfs sheets
# format-edge` command originally introduced, kept identical here.
GRAD_MIN = {"red": 0.96, "green": 0.80, "blue": 0.80}
GRAD_MID = {"red": 1.0, "green": 1.0, "blue": 0.80}
GRAD_MAX = {"red": 0.72, "green": 0.88, "blue": 0.72}

# Tab families -- the same four-way taxonomy already in the sheet, just
# desaturated so the strip reads as a system instead of a highlighter set.
FAMILY_COLORS = {
    "decide": _rgb("#3F6E8C"),
    "build": _rgb("#A93B30"),
    "money": _rgb("#15704A"),
    "contest": _rgb("#856508"),
    "feed": _rgb("#79356F"),
}

_HEADER_FMT = {
    "backgroundColor": HEADER_BG,
    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 10},
    "verticalAlignment": "MIDDLE",
    "horizontalAlignment": "LEFT",
}
# Public alias -- sheet_audit.py reuses this exact style so it can check
# a header's colour against what polish actually applied, without
# duplicating the color/weight choices in two places.
HEADER_FMT = _HEADER_FMT

_TITLE_FMT = {"textFormat": {"bold": True, "fontSize": 13, "foregroundColor": INK}}


def _chip(bg: dict, fg: dict) -> dict:
    return {"backgroundColor": bg, "textFormat": {"bold": True, "foregroundColor": fg}}


def _num(pattern: str, type_: str = "NUMBER") -> dict:
    return {"numberFormat": {"type": type_, "pattern": pattern}}


# ---------------------------------------------------------------------------
# FIELD_FORMATS -- the single source of truth for "what does this field look
# like", keyed by header TEXT. Every tab in the workbook that shows a given
# field applies the SAME entry here, whether that tab discovers its header
# dynamically (`apply_field_formats`, used by EdgeRaw and the builder tabs)
# or is an authored view with a literal layout (Board/Slate Grid/Exposure/
# Movement/Bankroll, which reference these constants directly rather than
# re-deriving their own `_num(...)` call). Before this existed, the exact
# same field (e.g. `Pts`) could be `General` in one place and `0.0` in
# another purely because two different dicts had drifted -- measured for
# real in the Lineups pool deck, whose window sat directly above block rows
# using a different format for the same column. Keys are the header text a
# tab actually prints, so aliases for the same concept (`Pts` vs `ProjPts`,
# `Ceil` vs `Ceiling`, `DK Sal` vs `Salary`) each get their own entry
# pointing at an equivalent format rather than being normalized away --
# normalizing header text is a structural change this file deliberately
# never makes (see the module docstring).
FIELD_FORMATS = {
    # Id is a raw DraftKings player ID, never meant to be looked at
    # (hidden outright on EdgeRaw, collapsed into the INTERNAL group
    # elsewhere) -- but it's also the exact string `sources/edge.py`'s
    # pre_upload/post_upload match Pool ticks by across a sync, and with
    # no explicit format here it silently inherited a stray "0.0" pattern
    # from wherever it happened to sit before Phase 3 moved it (carried
    # along by `moveDimension`, the same way a stale formula or column
    # group can be -- see CONTRIBUTING.md's Phase 3 changelog). Every
    # real Id rendered as e.g. "44133074.0" instead of "44133074", so a
    # tick captured before a sync never matched the freshly-written Id
    # string after it -- found live, a second real cause of the same
    # "every Pool tick vanished" symptom `sheet_links.py`'s Id-position
    # fix already covered once. A plain integer format closes this for
    # good, the same "derive/set explicitly, never inherit" rule as
    # every other field here.
    "Id": _num("0"),
    "DK Sal": _num("$#,##0", "CURRENCY"),
    "Salary": _num("$#,##0", "CURRENCY"),
    "Pts": _num("0.0"),
    "ProjPts": _num("0.0"),
    "Ceil": _num("0.0"),
    "Ceiling": _num("0.0"),
    "O/U": _num("0.0"),
    "OU": _num("0.0"),
    "OverUnder": _num("0.0"),
    "Team Implied": _num("0.0"),
    "GameEnv": _num("0.0"),
    "Total": _num("0.0"),
    "Leverage": _num("0.0"),
    "CeilPct": _num("0.0"),
    # Third real instance of the exact incident described in `Id`'s own
    # comment above (2026-09-16): OppPosRank, newly added to EDGE_COLUMNS
    # right before Stadium, landed on the physical column two prior
    # reorders had left Stadium sitting on -- itself carrying a STALE
    # "0 \"mph\"" format inherited from Wind's position two reorders
    # before that. Invisible the whole time Stadium (plain text) sat
    # there, since a number format never affects how text renders; it
    # surfaced the instant a real NUMBER (OppPosRank's rank) occupied
    # that cell, rendering a real rank like 11 as "11 mph". A plain
    # integer format here means every future reorder resets it via
    # `apply_field_formats`, rather than silently inheriting whatever a
    # stale physical column happened to carry.
    "OppPosRank": _num("0"),
    # Part 7.4: a plain integer rank (1, 2, 3...), same reasoning as
    # OppPosRank above -- a future reorder resets this via
    # apply_field_formats rather than inheriting a stale physical
    # column's format.
    "TmRank": _num("0"),
    "Spread": _num('"+"0.0;"-"0.0;0.0'),
    "ImpliedMove": _num('"+"0.0;"-"0.0;0.0'),
    "TotMove": _num('"+"0.0;"-"0.0;0.0'),
    "SpdMove": _num('"+"0.0;"-"0.0;0.0'),
    "Val": _num("0.00"),
    # A3 (2026-09-22): ValAdj is a 0-100 within-position percentile blend
    # now, not a raw points residual -- same one-decimal format as
    # CeilPct/GameEnv, its fellow 0-100 scores, not Val/CeilVal's 0.00.
    "ValAdj": _num("0.0"),
    "CeilVal": _num("0.00"),
    # Phase 6, Part 2: EdgeRaw's own ProjOwn and the other three tabs'
    # native Rstr% are unified into one shared "Own%" name -- previously
    # two different stored scales (EdgeRaw: a raw percentage-as-number;
    # the others: a true fraction, `.../100` in their own native formula)
    # under two different formats. `derived.build_edge_frame` now divides
    # EdgeRaw's own value by 100 too, so both sides are true fractions and
    # can share this one PERCENT format.
    "Own%": _num("0.0%", "PERCENT"),
    "% of Cap": _num("0.0%", "PERCENT"),
    "Exposure": _num("0.0%", "PERCENT"),
    "Target": _num("0.0%", "PERCENT"),
    "vs Target": _num("0.0%", "PERCENT"),
    "H2H %": _num("0.0%", "PERCENT"),
    "Wind": _num('0" mph"'),
    "Gust": _num('0" mph"'),
    # Part 7.5: Lineups-only lineup-metrics block. "Stack"/"Bring-back"
    # are text (a signature string, "Yes"/"No"/blank) -- no format entry
    # needed, same as any other text column.
    "Games": _num("0"),
    "Own% Used": _num("0.0%", "PERCENT"),
    "Sub-10%": _num("0"),
    "Min Unique": _num("0"),
}


def apply_field_formats(
    client: SheetsClient, tab: str, header: list, *, header_row: int, last_row: int
) -> int:
    """Format every column in `header` whose text is a FIELD_FORMATS key.
    `header` is the tab's own header row, already read (or, for EdgeRaw,
    known from EDGE_COLUMNS without a read -- see `polish_edge`) so this
    never assumes a column's position. Returns how many columns matched,
    for callers' own status lines."""
    applied = 0
    data_start = header_row + 1
    for i, name in enumerate(header):
        fmt = FIELD_FORMATS.get(name)
        if not fmt:
            continue
        letter = column_letter(i)
        client.format_range(tab, f"{letter}{data_start}:{letter}{last_row}", fmt)
        applied += 1
    return applied


# ---------------------------------------------------------------------------
# FIELD_COLOR_SCALES -- the "one visual policy" (Fix 2.1): every tab that
# shows a given field gets the SAME colour scale, found by header text the
# same way FIELD_FORMATS already works. Before this existed, EdgeRaw,
# Player Pool and Lineups each scaled a different set of columns -- some of
# it written by this codebase (EdgeRaw's own six, plus the three
# `sheet_links.link_edge_columns` puts on every EdgeRaw-linked block),
# the rest hand-applied directly in the browser at various points and never
# reconciled, including one rule that colour-scaled `Venue` (`H`/`R` text)
# as if it were a quantity. Replaces the old per-tab `EDGE_COLOR_SCALES`
# tuple entirely.
_GRADIENT = "gradient"  # red -> yellow -> green, more is better
_DIVERGING = "diverging"  # red -> white -> green, zero is the midpoint
_REVERSED = "reversed"  # green -> yellow -> red, LOW is better
# White -> amber -> red: ownership only. Deliberately NOT green=good --
# Sam: high ownership is CHALK, caution rather than quality, and this is
# the one place in the workbook where "more" isn't "better" (Fix 2.8).
# Reuses WARN_BG/CRIT_BG's exact hues so it reads as the same warning
# language as the rest of the workbook, just applied continuously.
_WARM = "warm"

WARM_MIN = WHITE
WARM_MID = _rgb("#F7E9CF")  # == WARN_BG
WARM_MAX = _rgb("#F8DEDA")  # == CRIT_BG

FIELD_COLOR_SCALES = {
    "ProjPts": _GRADIENT,
    "Pts": _GRADIENT,
    "Ceiling": _GRADIENT,
    "Ceil": _GRADIENT,
    "Val": _GRADIENT,
    "CeilVal": _GRADIENT,
    # Part 7.2: unlike Val/CeilVal, already a per-position residual
    # (Position-regressed against Salary on EdgeRaw itself), so -- same
    # reasoning as CeilPct just below -- a flat whole-tab scale is
    # already meaningful; see EDGE_UNSCALED_PLAYER_METRICS/
    # GROUPED_TAB_UNSCALED_COLUMNS for why EdgeRaw scales this one but
    # Player Pool/Lineups skip re-grouping it.
    "ValAdj": _GRADIENT,
    "Leverage": _GRADIENT,
    "GameEnv": _GRADIENT,
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
    # EDGE_UNSCALED_PLAYER_METRICS/GROUPED_TAB_UNSCALED_COLUMNS below for
    # why EdgeRaw keeps these two and Player Pool/Lineups skip them.
    "CeilPct": _GRADIENT,
    # Phase 5B: a count (0..however many lineups H1 says are being built),
    # same "more is better" reading as everything else in _GRADIENT -- a
    # heavily-used player earning the deepest colour is exactly the point.
    # Zero is also this column's overwhelmingly common value (most pool
    # players are rostered nowhere), so it's in ZERO_EXCLUDED_COLUMNS too
    # for the same reason Own% is: an unrostered player is normal, not the
    # bottom of a gradient.
    "Used": _GRADIENT,
}

# Phase 4 (4.1): EdgeRaw is sorted by Leverage, not grouped by position --
# one gradient across the whole 743-row tab paints every DST red next to
# a QB's real 27 points. Rather than add a second per-position-scaled
# copy of these columns, EdgeRaw skips scaling its raw, position-skewed
# player-performance metrics entirely and relies on the already-
# percentile CeilPct/Leverage instead (recommended over adding new
# percentile columns for Pts/Ceil, since those already exist). Player
# Pool/Lineups don't need this exclusion -- they scale per position/
# lineup block (`apply_grouped_color_scales`), where a raw Pts/Ceil
# comparison is exactly the right one.
EDGE_UNSCALED_PLAYER_METRICS = frozenset({"ProjPts", "Ceiling", "Val", "CeilVal"})

# Phase 4 (4.1): the reverse exclusion -- CeilPct is EdgeRaw's substitute
# for position-grouping (see above), which Player Pool/Lineups don't need
# (their own Pts/Ceil/etc. are already grouped for real). It also sits in
# the collapsed Ceiling detail zone there, rarely expanded -- not worth 5
# (Player Pool) or 20 (Lineups) more conditional-format rules per column
# for something that reads correctly-but-redundantly if skipped. `OwnPct`
# used to sit here too; dropped entirely from the sheet in Part 7.9 (its
# only consumer was the Leverage formula), so there's nothing left to
# exclude it from. `ValAdj` (Part 7.2) joins it for the same reason: it's
# already a per-position residual computed once on EdgeRaw, so re-scaling
# it again per position-block here would be redundant, not wrong.
GROUPED_TAB_UNSCALED_COLUMNS = frozenset({"CeilPct", "ValAdj"})

# Deliberately absent from FIELD_COLOR_SCALES: `Salary`/`DK Sal` -- a
# constraint, not a quality; scaling it would imply cheap is good.
# `GameID`/`TmRank` (Part 7.4) too -- GameID is an identifier, not a
# quantity; TmRank is a crude target-hierarchy PROXY (salary rank within
# team+position), and colour-scaling it would visually imply it's a
# measured quality worth ranking by, which docs/CALCULATIONS.md
# explicitly warns against reading it as.

# Columns where a real, common zero would otherwise anchor a gradient's
# low end and compress everyone else's actual spread into a sliver of the
# scale (Fix 2.7) -- unpublished ownership reads 0 for the whole slate
# until TFFB computes it midweek. Gets its own flat grey chip (added
# after the gradient so it wins -- see the shared insert-at-front note on
# FLAG_CHIPS above) and the gradient's own minpoint is computed over
# non-zero values only via a live MINIFS formula, not the true minimum.
ZERO_EXCLUDED_COLUMNS = frozenset({"Own%", "Used"})
ZERO_GREY_BG = _rgb("#EDEEF1")


def apply_field_color_scales(
    client: SheetsClient,
    tab: str,
    header: list,
    *,
    header_row: int,
    last_row: int,
    skip: frozenset[str] = frozenset(),
) -> int:
    """Colour-scale every column in `header` whose text is a
    FIELD_COLOR_SCALES key (except any name in `skip` -- see
    EDGE_UNSCALED_PLAYER_METRICS). Clears each matched column's own conditional
    formats first, scoped to BOTH that column AND this exact data range
    (`header_row+1`..`last_row`) -- though the real cleanup of a tab's
    pre-existing mess is its caller's whole-tab `clear_conditional_formats`
    (see `polish_edge`/`polish_builder_tab`), since a hand-applied rule can
    span multiple columns at once and a column-scoped clear alone can't
    reliably catch that.

    Fix A2, found live: a column-ONLY clear here (no row scoping) doesn't
    just clear THIS call's own prior rule -- it deletes every rule on that
    column regardless of row, including a DIFFERENT caller's rule covering
    different rows on the exact same column. `polish_pool_deck` calls this
    for the pool deck's own narrow window (rows 4-9) right after
    `polish_builder_tab` has just color-scaled the same columns across the
    20 real lineup blocks below (rows 12-268) -- a column-only clear from
    the deck's own call silently deleted the blocks' already-correct
    gradient on every single `dfs setup polish` run, which is why
    Lineups' real blocks had chips (Flag/Avail/Venue, cleared by
    `polish_guardrails`/`polish_builder_tab` itself with different scoping)
    but never any colour scale. Scoping the clear to both column and row
    range means each caller's own clear can only ever touch its own rule.
    Returns how many columns matched, for callers' own status lines.
    """
    applied = 0
    data_start = header_row + 1
    for i, name in enumerate(header):
        kind = FIELD_COLOR_SCALES.get(name)
        if not kind or name in skip:
            continue
        letter = column_letter(i)
        a1 = f"{letter}{data_start}:{letter}{last_row}"
        client.clear_conditional_formats(tab, column=letter, row_range=(data_start, last_row))
        gradient_spec, boolean_spec = _scale_rule_specs(a1, kind, name, zero_exclude_range=a1)
        client.add_color_scale(tab, gradient_spec.pop("a1_range"), **gradient_spec)
        if boolean_spec is not None:
            client.add_boolean_rule(tab, boolean_spec.pop("a1_range"), **boolean_spec)
        applied += 1
    return applied


def _scale_rule_specs(
    a1: str,
    kind: str,
    name: str,
    *,
    zero_exclude_range: str,
    min_value: str | None = None,
    max_value: str | None = None,
) -> tuple[dict, dict | None]:
    """Shared dispatch building the rule SPECS (kwargs dicts for
    `SheetsClient.add_color_scale`/`add_boolean_rule`, each carrying its
    own `a1_range`) for one column -- never calls the client itself, so
    callers can either apply a spec immediately (`apply_field_color_
    scales`, a handful of whole-tab rules) or collect many and apply them
    in one batched `add_color_scales`/`add_boolean_rules` call
    (`apply_grouped_color_scales`/`apply_deck_color_scales`, which can
    generate hundreds -- see `add_color_scales`' own docstring for why
    that matters). Used by both `apply_field_color_scales` (one rule
    spanning `a1`'s own full range) and `apply_grouped_color_scales` (one
    rule per group, `min_value`/`max_value` anchored to that group's own
    range rather than `a1`'s implicit MIN/MAX -- see that function's own
    docstring for why a single rule can't do this across multiple groups
    at once).

    Fix 2.7's zero-exclusion MINIFS formula reads over `zero_exclude_range`
    (the range whose non-zero minimum actually matters -- `a1` itself for
    a whole-tab scale, but a group's own narrower range when this is
    called per-group), never `a1` when the two differ, and only replaces
    an explicit `min_value` when the caller didn't already provide one.
    Returns `(gradient_spec, boolean_spec_or_None)`.
    """
    min_kwargs: dict = {}
    if min_value is not None:
        min_kwargs = {"min_type": "NUMBER", "min_value": min_value}
    elif name in ZERO_EXCLUDED_COLUMNS:
        min_kwargs = {
            "min_type": "NUMBER",
            "min_value": f'=MINIFS({zero_exclude_range},{zero_exclude_range},"<>0")',
        }
    max_kwargs = {"max_type": "NUMBER", "max_value": max_value} if max_value is not None else {}

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
            **min_kwargs,
            **max_kwargs,
        }

    boolean_spec = None
    if name in ZERO_EXCLUDED_COLUMNS:
        # Added AFTER the gradient above (later in the same batch, or a
        # later individual call), so it lands at index 0 and wins for any
        # exact-zero cell -- see FLAG_CHIPS' comment on add_boolean_rule/
        # add_color_scale's shared insert-at-front behavior, verified
        # against a live sheet's raw conditionalFormats metadata.
        boolean_spec = {
            "a1_range": a1,
            "condition_type": "NUMBER_EQ",
            "values": ["0"],
            "fmt": {"backgroundColor": ZERO_GREY_BG},
        }
    return gradient_spec, boolean_spec


def apply_edge_position_scales(
    client: SheetsClient, tab: str, header: list, *, position_column: str, data_start: int, last_row: int
) -> int:
    """Real per-position highlighting for EdgeRaw's raw player-performance
    metrics (`EDGE_UNSCALED_PLAYER_METRICS` -- ProjPts/Ceiling/Val/
    CeilVal), which `apply_field_color_scales` deliberately skips there
    (see that constant's own comment: a flat whole-tab gradient can't
    tell a QB's real 27 points from a DST's real 10 without misleadingly
    comparing them). EdgeRaw isn't grouped into position blocks the way
    Player Pool/Lineups are (`apply_grouped_color_scales` needs fixed,
    contiguous row ranges per group) -- it's one flat list sorted by
    Leverage/CeilPct, so a given position's rows are scattered
    non-contiguously and change position every sync.

    Sam, 2026-09-18, looking at a freshly-filtered, freshly-sorted
    EdgeRaw: "everything being white numbers makes [spotting outliers]
    very hard." His actual workflow -- filter to a position or two, sort
    by a raw stat, look for outliers -- needs exactly this: CeilPct/
    Leverage answer a different question (ceiling upside net of
    ownership), not "does this specific Pts/Val/Ceiling number stand out
    for this position."

    Works via `SheetsClient.add_color_scales_multi_range` -- verified live
    on the template's Scratch tab that one rule's min/mid/max are computed
    over the union of every range it's given, independent of anything else
    on the sheet (see that method's own docstring). Reads the tab's
    CURRENT `Position` column to find each position's actual row numbers
    right now, merges each position's rows into contiguous runs to keep
    the request compact, and issues one gradient rule per (position,
    metric) pair scoped to just that position's own rows. Must be re-run
    after every sync for the ranges to stay correct, since row order
    shifts with Leverage/CeilPct -- `dfs setup polish` (via `polish_edge`)
    is the existing hook that already runs after a sync and already owns
    EdgeRaw's other colour scales, so no new standing command is needed.
    Returns the number of (position, metric) rules applied.
    """
    if position_column not in header:
        return 0
    pos_letter = column_letter(header.index(position_column))
    pos_rows = client.read_range(tab, f"{pos_letter}{data_start}:{pos_letter}{last_row}")

    rows_by_position: dict[str, list[int]] = {}
    for offset, row in enumerate(pos_rows):
        position = row[0] if row else ""
        if not position:
            continue
        rows_by_position.setdefault(position, []).append(data_start + offset)

    specs = []
    for name in EDGE_UNSCALED_PLAYER_METRICS:
        if name not in header:
            continue
        letter = column_letter(header.index(name))
        for rows in rows_by_position.values():
            a1_ranges = [f"{letter}{start}:{letter}{end}" for start, end in _merge_contiguous(rows)]
            specs.append(
                {"a1_ranges": a1_ranges, "min_color": GRAD_MIN, "mid_color": GRAD_MID, "max_color": GRAD_MAX}
            )

    client.add_color_scales_multi_range(tab, specs)
    return len(specs)


def apply_grouped_color_scales(
    client: SheetsClient,
    tab: str,
    header: list,
    groups: list[tuple[int, int]],
    *,
    skip: frozenset[str] = frozenset(),
) -> int:
    """Phase 4 (4.1/4.2): the per-position (Player Pool) / per-lineup
    (Lineups) version of `apply_field_color_scales` -- one gradient rule
    PER `(column, group)` pair, each scoped to that group's own actual
    min/max, so a QB's real 27 points and a DST's real 10 don't share one
    scale that paints every DST red.

    Verified live before writing this (see CONTRIBUTING.md's Phase 4
    changelog): a single gradient rule's minpoint/midpoint/maxpoint are
    each evaluated ONCE from a fixed formula/reference, not row-relative
    like a custom boolean condition -- so one rule spanning multiple
    groups, even with a formula-anchored endpoint, cannot independently
    scale each group; it just anchors the whole range to whichever
    group's range the formula happens to reference. There is therefore no
    way to do this in fewer than `len(groups)` rules per column -- 4.3's
    "try a formula-anchored single rule first" doesn't reduce the count
    here, confirmed rather than assumed. Rule count is exactly
    `len(groups) * (matched column count)`; returned as the total so
    callers can report it (4.3's other ask).
    """
    gradient_specs = []
    boolean_specs = []
    for start, end in groups:
        for i, name in enumerate(header):
            kind = FIELD_COLOR_SCALES.get(name)
            if not kind or name in skip:
                continue
            letter = column_letter(i)
            a1 = f"{letter}{start}:{letter}{end}"
            client.clear_conditional_formats(tab, column=letter, row_range=(start, end))
            gradient_spec, boolean_spec = _scale_rule_specs(a1, kind, name, zero_exclude_range=a1)
            gradient_specs.append(gradient_spec)
            if boolean_spec is not None:
                boolean_specs.append(boolean_spec)

    client.add_color_scales(tab, gradient_specs)
    client.add_boolean_rules(tab, boolean_specs)
    return len(gradient_specs)


# ---------------------------------------------------------------------------
# EdgeRaw (Direction B)
# ---------------------------------------------------------------------------

# Pixel widths by EdgeRaw column NAME, not letter -- so a reordered
# EDGE_COLUMNS still gets the right width on the right column.
EDGE_WIDTHS = {
    "Id": 90,
    "Name": 165,
    # Phase 6, Part 1.5: Position/ProjPts/Ceiling/CeilPct/ProjOwn/Leverage/
    # OverUnder/Spread/GameEnv/OppPosRank all clipped live at their old
    # widths ("Posi", "ProjPt", "Ceilinc", "CeilPc", "ProjO", "Leverag",
    # "OverU", "Spreac", "GameEn", "OppPosRar") -- nothing checked a
    # column's width against its own rendered header text. Widened here;
    # `sheet_audit.py` now has a check so this can't silently regress.
    "Position": 92,
    # Widened from 54 -- NOT in the originally-reported list, but found by
    # actually looking at the rendered sheet (not trusting the pixel
    # heuristic, which said this one was fine): "Team" clipped to "Tearr"
    # live.
    "Team": 66,
    "Opp": 54,
    "Salary": 78,
    "ProjPts": 85,
    # Phase 6, Part 2: renamed from ProjOwn (EdgeRaw's own former name for
    # this column, unified with the other three tabs' Rstr% into one
    # shared "Own%").
    "Own%": 85,
    "Ceiling": 85,
    # Widened from 58 -- found by looking at Lineups' own totals row, not
    # the width-truncation heuristic (which only checks header text):
    # "Remaining" (polish_lineups_totals_rows' text label, placed in this
    # column specifically -- see that function's own docstring) clipped
    # to "Remainin" live. A short numeric Val (e.g. "3.45") never needed
    # this much room; the totals-row label did.
    "Val": 80,
    # Part 7.2: signed residual (e.g. "-3.45"), plus the 6-character
    # header itself -- not in a totals row like Val, no extra padding
    # needed for that.
    "ValAdj": 72,
    "CeilVal": 68,
    "CeilPct": 85,
    "Leverage": 92,
    # Widened from 74 -- same as Team above, found by looking, not by the
    # heuristic: "LevBasis" clipped to "LevBasi" live. Renamed from
    # LevBasis (Part 7.9).
    "OwnStatus": 90,
    "GameEnv": 90,
    "OppPosRank": 115,
    # nflverse's own GameId format is "2026_02_DET_BUF" -- season, week,
    # away, home -- up to 15 characters.
    "GameID": 130,
    "TmRank": 70,
    "Stadium": 150,
    "Roof": 76,
    "Wind": 68,
    "Avail": 60,
    # Widened from 96: can hold multiple space-separated tokens (Fix 2.1),
    # e.g. "WIND LINE↑ LEVERAGE". Renamed from Flag (Part 7.9) -- "Flag"
    # itself is hidden now, single-highest-priority-token only, and
    # doesn't need a width entry.
    "Flags": 170,
    # Widened from 78 (same width as TotMove/SpdMove, 7 chars each) --
    # "ImpliedMove" is 11 characters; Fix 2.2 renamed it from the
    # 8-character "LineMove" without widening its column to match.
    "ImpliedMove": 105,
    "TotMove": 78,
    "SpdMove": 78,
    "GameStart": 132,
    "OverUnder": 105,
    "Spread": 78,
    # Zone labels (2026-09-17): narrow but readable at their own short
    # text -- see `sheet_style._apply_zone_label_style` for the rest of
    # their look (centered, muted, a light tint to read as a divider
    # rather than a data column).
    "GAME": 52,
    "CEIL": 52,
    "MOVE": 56,
    "WX": 44,
}

# Phase 6, Part 2 (2026-09-17) overrides Fix 2.9: Stadium/Roof/Wind used
# to be deliberately left ungrouped on EdgeRaw specifically ("Sam wants
# weather visible by default on EdgeRaw itself, where it's the tab you're
# actually reading closely"), unlike the collapsed-by-default treatment
# Lineups/Player Pool already had. Part 2's own goal -- EdgeRaw/Player
# Pool/Lineups look identical, spine visible, everything else one click
# away -- explicitly overrides that: Game/Ceiling detail/Movement/Weather
# all collapse on EdgeRaw now too.
#
# 2026-09-17, later the same day: originally one merged group (OverUnder
# through Wind), since all four zones sat back-to-back with nothing
# native between them and Sheets merges adjacent same-depth groups into
# one regardless (the same reality `sheet_links.link_edge_columns` works
# around for the other three tabs). Split into four independent ranges
# once each zone got its own real label column (`derived.GAME_LABEL`
# etc., see `sheet_columns.py`'s module docstring) immediately before it
# -- a usability fix, not in the original spec: Sam wanted to be able to
# expand just Weather, or just Game, without guessing which unlabeled `+`
# is which. The label itself sits OUTSIDE each tuple's own range,
# deliberately -- collapsing hides everything inside a group's range,
# label included, so a label can only stay visible by never being part of
# what it's labeling.
#
# Id/Flag used to be grouped here too, but neither is genuinely useful to
# look at (a raw DraftKings player ID; a single-token internal duplicate
# of Flags), so both are fully hidden instead (see polish_edge's
# hide_columns call) -- a group would just be a second click for
# something that never needs to come back.
EDGE_COLUMN_GROUPS = [
    # Part 7.4 (2026-09-18) added GameID/TmRank to EDGE_COLUMNS right
    # after OppPosRank -- this tuple's own "last" member missed updating
    # at the time, leaving both sitting outside the collapsed GAME range
    # on EdgeRaw specifically (found live: visible immediately after the
    # collapsed group instead of folded into it, unlike Player Pool/
    # Lineups, where `sheet_columns.GAME` already included them).
    ("OverUnder", "TmRank"),
    ("CeilPct", "OwnStatus"),
    ("ImpliedMove", "GameStart"),
    ("Stadium", "Wind"),
]

# Muted, per-position backgrounds -- just enough to see position boundaries
# while scanning a list sorted by Leverage, not loud enough to compete with
# the colour scales on the decision columns.
POSITION_TINTS = {
    "QB": _rgb("#ECE7F5"),
    "RB": _rgb("#E5F1E8"),
    "WR": _rgb("#E5EEF7"),
    "TE": _rgb("#FBEEE0"),
    "DST": _rgb("#EEEEEE"),
}

# Same threshold and colour Slate Grid's own Wind/Gust chip uses (see
# style_slate_grid) -- one number, one meaning, everywhere it appears.
WIND_CHIP_THRESHOLD = "15"

# Part 7.10 (2026-09-18): a light per-tag tint on Player Pool's own
# `Pool` column, so the three sort groups (Both/Cash/GPP, in that order --
# see `sources.edge.POOL_TYPE_SORT_ORDER`) read as bands at a glance,
# same "muted, don't compete with the Flag chips" policy as
# `POSITION_TINTS` above. A blank Pool cell (shouldn't happen on a real
# Player Pool row -- every row is already someone's pick -- but a
# control-cell add whose EdgeRaw tag lookup came back empty is possible)
# is deliberately left untinted rather than guessed at.
POOL_TAG_TINTS = {
    "Both": _rgb("#E5F1E8"),
    "Cash": _rgb("#E5EEF7"),
    "GPP": _rgb("#FBEEE0"),
}

# Soft accent used only for "this player is already in your pool" -- applied
# to the Name column alone. A full-row tint was tried and rejected: Sheets
# shows exactly one conditional-format rule per cell, so tinting the whole
# row would silently blank out the six colour scales on every ticked
# player's row instead of coexisting with them.
POOL_TINT_BG = _rgb("#EAF1FB")

# Ordered LEAST urgent first, matching derived._flag_for_row's priority
# (OUT, WIND, LINE↑/↓, LEVERAGE, CHALK) IN REVERSE. Verified empirically
# against a live sheet's raw `conditionalFormats` metadata: both
# `add_boolean_rule` and `add_color_scale` explicitly pass `"index": 0`,
# so each new rule is inserted at the very FRONT of the sheet's rule list
# -- the LAST one added ends up at index 0, which Sheets checks first.
# That means, for a set of rules added in a loop over this dict, the
# highest-priority entry has to be the one defined LAST, not first, or
# the visual chip on an overlapping cell silently picks the wrong flag.
# This matters now that Flag can hold more than one space-separated token
# (Fix 2.1 -- e.g. "WIND LEVERAGE"): whichever of the matching rules was
# added last wins the cell's colour, so this order must track priority in
# reverse for that colour to actually match what the text says is most
# urgent.
FLAG_CHIPS = {
    "CHALK": _chip(FLAT_BG, FLAT_FG),
    "LEVERAGE": _chip(OK_BG, OK_FG),
    "LINE↓": _chip(CRIT_BG, CRIT_FG),
    "LINE↑": _chip(OK_BG, OK_FG),
    "WIND": _chip(WARN_BG, WARN_FG),
    "OUT": _chip(CRIT_BG, CRIT_FG),
}

AVAIL_CHIPS = {
    "OUT": _chip(CRIT_BG, CRIT_FG),
    "IR": _chip(CRIT_BG, CRIT_FG),
    "Q": _chip(WARN_BG, WARN_FG),
}

# Player Pool's own surfaced Pool value (Fix 2.11) -- categorical state,
# same treatment as Source/Venue, never a colour scale.
POOL_TYPE_CHIPS = {
    "Cash": _chip(OK_BG, OK_FG),
    "GPP": _chip(WARN_BG, WARN_FG),
    "Both": _chip(FLAT_BG, FLAT_FG),
}

# Venue (Fix 2.1): `H`/`R` text, categorical only -- home/road is
# provenance the same way Source is, not a quality to colour-scale (a
# hand-applied scale on this column, found live, was exactly the bug this
# replaces). Two distinct neutral tones so the two states still read at a
# glance, neither tinted as good/bad.
VENUE_CHIPS = {
    "H": _chip(_rgb("#E5EEF7"), _rgb("#2E5A82")),
    "R": _chip(_rgb("#F0EAE0"), _rgb("#8A6D3B")),
}


def _edge_letter(column_name: str) -> str | None:
    """Real EdgeRaw column letter for a column NAME, or None if that column
    isn't in EDGE_COLUMNS on this version of the CLI. Offset by
    EDGE_DATA_OFFSET since column A is Pool, not the first EDGE_COLUMNS
    entry -- see derived.py's own comment on EDGE_DATA_OFFSET."""
    if column_name not in EDGE_COLUMNS:
        return None
    return column_letter(EDGE_COLUMNS.index(column_name) + EDGE_DATA_OFFSET)


# ---------------------------------------------------------------------------
# Phase 5C: the four pieces of EdgeRaw's own look (Wind chip, per-position
# tint, OwnStatus's grey freshness marker (renamed from LevBasis, Part
# 7.9), Name bold-on-Flag) that `polish_builder_tab` didn't yet apply to
# Player Pool/Lineups -- pulled out
# so `polish_edge` and `polish_builder_tab` share one implementation instead
# of a second copy drifting the moment one of them changes. Each takes
# `header`/a column letter looked up BY NAME (never a hardcoded position,
# same discipline as every other function in this module) so either caller
# can pass its own header shape -- EdgeRaw's `[POOL_HEADER, *EDGE_COLUMNS]`
# or a builder tab's own read-back row.
# ---------------------------------------------------------------------------


def _merge_contiguous(values: list[int]) -> list[tuple[int, int]]:
    """Sorts `values` and merges consecutive runs into (start, end) pairs
    -- e.g. [2, 3, 4, 9, 11, 12] -> [(2, 4), (9, 9), (11, 12)]. Shared by
    `_unhide_ungrouped_columns` (column indices) and
    `apply_edge_position_scales` (row numbers) -- same merge, different
    axis, kept in one place so the two never drift."""
    runs: list[tuple[int, int]] = []
    for v in sorted(values):
        if runs and runs[-1][1] + 1 == v:
            runs[-1] = (runs[-1][0], v)
        else:
            runs.append((v, v))
    return runs


def _unhide_ungrouped_columns(client: SheetsClient, tab: str, total_width: int) -> None:
    """Unhides every column NOT currently covered by a collapsed group,
    across the tab's full known width -- the fix for a real, three-times-
    repeated live incident (Name; Avail/Flags; GAME/CEIL/MOVE, all on
    EdgeRaw) where a killed/retried polish run left a stray column hidden
    that should never have been: `polish_edge`/`polish_builder_tab`'s own
    hide-Id/Flag loop only ever ADDS a hide, so a stale one from an
    earlier, superseded run (computed against a header that was mid-
    reorder at the time) survives every later run unless something
    explicitly undoes it.

    Deliberately skips any column inside an EXISTING group rather than
    unhiding the whole width in one call -- verified live (see `SheetsClient.
    get_grouped_column_indices`'s own docstring) that explicitly unhiding a
    grouped range's columns makes them visible while the group's own
    metadata still says `collapsed: true`, desyncing the UI. Called
    BEFORE the specific Id/Flag hide below, so it's harmless that this
    pass leaves Id/Flag visible too -- they're about to be hidden again a
    few lines later, same as every other polish run."""
    grouped = client.get_grouped_column_indices(tab)
    ungrouped = [i for i in range(total_width) if i not in grouped]
    for start, end in _merge_contiguous(ungrouped):
        client.hide_columns(tab, column_letter(start), column_letter(end), hidden=False)


def _apply_wind_chip(client: SheetsClient, tab: str, header: list, *, data_start: int, last_row: int) -> None:
    if "Wind" not in header:
        return
    letter = column_letter(header.index("Wind"))
    client.add_boolean_rule(
        tab,
        f"{letter}{data_start}:{letter}{last_row}",
        condition_type="NUMBER_GREATER",
        values=[WIND_CHIP_THRESHOLD],
        fmt=_chip(WARN_BG, WARN_FG),
    )


def _apply_position_tint(
    client: SheetsClient, tab: str, header: list, *, column_name: str, data_start: int, last_row: int
) -> None:
    if column_name not in header:
        return
    letter = column_letter(header.index(column_name))
    for position, bg in POSITION_TINTS.items():
        client.add_boolean_rule(
            tab,
            f"{letter}{data_start}:{letter}{last_row}",
            condition_type="TEXT_EQ",
            values=[position],
            fmt={"backgroundColor": bg},
        )


def _apply_pool_tag_tint(
    client: SheetsClient, tab: str, header: list, *, column_name: str, data_start: int, last_row: int
) -> None:
    """Part 7.10: bands Player Pool's `Pool` column by tag (Both/Cash/
    GPP) so the sort groups `sheet_pool_formulas.py` now produces read
    visually, not just by scrolling and reading the text. No-ops on any
    tab without a `Pool` column (PlayerPoolRaw/Lineups have no such
    column -- only Player Pool surfaces the tag), same guard
    `_apply_position_tint` already uses for its own optional column."""
    if column_name not in header:
        return
    letter = column_letter(header.index(column_name))
    for tag, bg in POOL_TAG_TINTS.items():
        client.add_boolean_rule(
            tab,
            f"{letter}{data_start}:{letter}{last_row}",
            condition_type="TEXT_EQ",
            values=[tag],
            fmt={"backgroundColor": bg},
        )


def _apply_zone_label_style(
    client: SheetsClient, tab: str, header: list, *, data_start: int, last_row: int
) -> None:
    """Zone labels (2026-09-17 usability fix -- see `sheet_columns.py`'s
    own module docstring): a light, neutral tint down the DATA rows only,
    same `FLAT_BG`/`FLAT_FG` pairing as other categorical/neutral chips
    (`CHALK`, Player Pool's `Source`), so each label reads as a solid
    divider strip while scrolling through hundreds of player rows, not a
    data column that happens to be blank.

    Deliberately does NOT touch the header row -- first tried that (a
    lighter background there too, to stand out even more), and `dfs
    setup audit-style` immediately caught the real problem with it: it
    silently overwrote the shared dark header fill every other column's
    header cell gets, which the audit's own "shared dark fill" check
    treats as a real defect, not a style choice, because normally it is
    one. The label TEXT itself (`GAME`/`CEIL`/`MOVE`/`WX`, short and in
    all caps) already reads as distinct from an ordinary column heading
    without needing a different background too -- the data-row tint below
    it is what actually needs to be different, since that's the part that
    would otherwise look like an unexplained blank column."""
    for name in ZONE_LABELS:
        if name not in header:
            continue
        letter = column_letter(header.index(name))
        client.format_range(
            tab,
            f"{letter}{data_start}:{letter}{last_row}",
            {
                "backgroundColor": FLAT_BG,
                "textFormat": {"foregroundColor": FLAT_FG, "bold": True},
                "horizontalAlignment": "CENTER",
            },
        )


def _apply_own_status_marker(
    client: SheetsClient, tab: str, header: list, *, data_start: int, last_row: int
) -> None:
    """Renamed from `_apply_lev_basis_marker` (Part 7.9, alongside the
    `LevBasis` -> `OwnStatus` column rename)."""
    if "OwnStatus" not in header:
        return
    letter = column_letter(header.index("OwnStatus"))
    client.format_range(
        tab,
        f"{letter}{data_start}:{letter}{last_row}",
        {"textFormat": {"foregroundColor": INK_MUTED}, "horizontalAlignment": "CENTER"},
    )


def _apply_edge_link_style(
    client: SheetsClient, tab: str, header: list, *, data_start: int, last_row: int
) -> None:
    """Week 3 feedback (A5), 2026-09-22: Sam: "Can we make the link a
    little smaller, it's distracting to look at in its current state."
    Shrinks "Edge ↗"'s font and mutes its colour so it reads as a quiet
    affordance in the corner of the eye, not a call to action competing
    with real decision columns -- the same visual demotion `INK_MUTED`
    already gives `OwnStatus` just above. Column WIDTH is handled
    separately, by `BUILDER_WIDTHS["Edge ↗"]` (this function only touches
    text formatting); the hyperlink's own target/behaviour is untouched --
    `edge_row_hyperlink_formula` shortened the displayed text itself
    (`"Edge ↗"` -> `"↗"`) so a glyph-width column doesn't clip it."""
    if "Edge ↗" not in header:
        return
    letter = column_letter(header.index("Edge ↗"))
    client.format_range(
        tab,
        f"{letter}{data_start}:{letter}{last_row}",
        {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 8}, "horizontalAlignment": "CENTER"},
    )


def _apply_name_flag_style(
    client: SheetsClient,
    tab: str,
    header: list,
    *,
    data_start: int,
    last_row: int,
    pool_column: str | None = None,
) -> None:
    """Bolds the Name cell when Flag is set. On EdgeRaw (`pool_column`
    given -- the one tab spanning both pooled and unpooled players) also
    tints it when the row is pooled, as three mutually-exclusive
    combinations (Sheets renders only one matching rule per cell, so two
    overlapping single-condition rules would silently hide one cue).
    Player Pool/Lineups have no unpooled rows to distinguish -- every row
    on either tab is already someone's pool pick or roster slot -- so
    `pool_column=None` there skips the tint dimension entirely and just
    bolds on Flag.
    """
    if "Name" not in header or "Flag" not in header:
        return
    name_col = column_letter(header.index("Name"))
    flag_col = column_letter(header.index("Flag"))
    rng = f"{name_col}{data_start}:{name_col}{last_row}"
    flag_ref = f"${flag_col}{data_start}"

    if pool_column and pool_column in header:
        pool_ref = f"${column_letter(header.index(pool_column))}{data_start}"
        pooled_and_flagged = {"backgroundColor": POOL_TINT_BG, "textFormat": {"bold": True}}
        pooled_only = {"backgroundColor": POOL_TINT_BG}
        flagged_only = {"textFormat": {"bold": True}}
        rules = [
            (f'=AND({pool_ref}<>"",{flag_ref}<>"")', pooled_and_flagged),
            (f'=AND({pool_ref}<>"",{flag_ref}="")', pooled_only),
            (f'=AND({pool_ref}="",{flag_ref}<>"")', flagged_only),
        ]
        for formula, fmt in rules:
            client.add_boolean_rule(tab, rng, condition_type="CUSTOM_FORMULA", values=[formula], fmt=fmt)
    else:
        client.add_boolean_rule(
            tab,
            rng,
            condition_type="CUSTOM_FORMULA",
            values=[f'={flag_ref}<>""'],
            fmt={"textFormat": {"bold": True}},
        )


def polish_edge(client: SheetsClient, edge_tab: str) -> str:
    """Direction B: make EdgeRaw readable without moving anything.

    Widths, a dark frozen header, Id hidden and Pool+Name pinned while you
    scroll right, light row banding, number formats on every numeric
    column, FIELD_COLOR_SCALES applied to every matching column except
    EDGE_UNSCALED_PLAYER_METRICS (a diverging scale for ImpliedMove/TotMove/
    SpdMove/Spread, gradient for the rest -- see Phase 4's own comment
    below on why a flat WHOLE-TAB scale on raw Pts/Ceil/Val/CeilVal would
    be misleading). Those four get a REAL per-position scale instead
    (`apply_edge_position_scales`, 2026-09-18) -- Sam's actual workflow is
    filtering to a position and sorting by a raw stat to spot outliers,
    which a flat scale (or none at all) can't support. Also a muted
    per-position tint, a Wind chip matching Slate Grid's, Flags/Avail as
    chips, the Name cell tinted when that player is already pooled and
    bolded when Flag is set, and OwnStatus greyed as the data-freshness
    marker it is (see the module docstring's colour policy).
    """
    if not client.tab_exists(edge_tab):
        return f"{edge_tab}: not present -- skipped"

    edge_header = [POOL_HEADER, *EDGE_COLUMNS]
    last_col = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)
    client.clear_conditional_formats(edge_tab)
    client.clear_banding(edge_tab)
    client.add_row_banding(
        edge_tab, f"A2:{last_col}{EDGE_ROWS}", first_band_color=WHITE, second_band_color=BAND_BG
    )

    widths = {}
    for name, px in EDGE_WIDTHS.items():
        letter = _edge_letter(name)
        if letter:
            widths[letter] = px
    client.set_column_widths(edge_tab, widths)

    client.format_range(edge_tab, f"A1:{last_col}1", _HEADER_FMT)
    # The one typed column on this tab -- pale yellow, the same "you type
    # here" cue every other typed cell in the workbook uses (see the
    # visual-grammar docstring below).
    client.format_range(edge_tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{EDGE_ROWS}", {"backgroundColor": INPUT_BG})
    # Pool and Name pinned while you scroll right; Id/Flag are hidden
    # outright rather than pinned -- a raw DraftKings ID is never worth
    # looking at, and Flag (Part 7.9's single-highest-priority token) is
    # superseded for reading by the visible "Flags" column. Phase 3 moved
    # Id into EDGE_COLUMNS' INTERNAL zone (near the far right, now shared
    # with Flag -- Part 7.9) rather than keeping it as Pool's immediate
    # neighbor; both are still individually hidden here regardless of
    # where they physically sit, found by name like everything else in
    # this function.
    #
    # Reset every non-grouped column visible first, THEN hide only Id/Flag
    # -- found live, three separate times in one session (Name;
    # Avail/Flags; GAME/CEIL/MOVE), that a killed/retried polish run can
    # leave a STALE hide behind (see `_unhide_ungrouped_columns`'s own
    # docstring for the mechanism). Makes this call idempotent and
    # self-correcting instead of purely additive.
    _unhide_ungrouped_columns(client, edge_tab, len(EDGE_COLUMNS) + EDGE_DATA_OFFSET)
    for hidden_name in ("Id", "Flag"):
        letter = _edge_letter(hidden_name)
        if letter:
            client.hide_columns(edge_tab, letter, letter)
    name_idx = EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET if "Name" in EDGE_COLUMNS else 1
    client.freeze(edge_tab, rows=1, cols=name_idx + 1)

    # EdgeRaw's real header is `[POOL_HEADER, *EDGE_COLUMNS]` by construction
    # (see `sources/edge.py`'s `to_sheet_rows`) -- built here rather than
    # read back, same as every other position in this function.
    apply_field_formats(client, edge_tab, edge_header, header_row=1, last_row=EDGE_ROWS)

    # FIELD_COLOR_SCALES (Fix 2.1) -- the one canonical policy every tab
    # that shows a given field applies; on EdgeRaw that's ProjPts, Ceiling,
    # Val, CeilVal, Leverage, GameEnv, OverUnder (gradient), Own% (warm),
    # and ImpliedMove/TotMove/SpdMove/Spread (diverging).
    # Phase 4 (4.1): EdgeRaw is sorted by Leverage, not grouped by
    # position, so its raw player-performance metrics (Pts/Ceil/Val/
    # CeilVal) are skipped here -- one gradient across all 743 players
    # would paint every DST red next to a QB's real 27 points. The
    # already-percentile CeilPct/OwnPct/Leverage stay scaled; they don't
    # need position-grouping to mean something.
    n_scaled = apply_field_color_scales(
        client,
        edge_tab,
        edge_header,
        header_row=1,
        last_row=EDGE_ROWS,
        skip=EDGE_UNSCALED_PLAYER_METRICS,
    )
    n_position_scaled = apply_edge_position_scales(
        client, edge_tab, edge_header, position_column="Position", data_start=2, last_row=EDGE_ROWS
    )

    _apply_wind_chip(client, edge_tab, edge_header, data_start=2, last_row=EDGE_ROWS)
    _apply_position_tint(
        client, edge_tab, edge_header, column_name="Position", data_start=2, last_row=EDGE_ROWS
    )

    # Part 7.9: chips go on "Flags" (visible, every matching token) --
    # "Flag" (singular) is hidden now, single-highest-priority-token only.
    flag_col = _edge_letter("Flags")
    if flag_col:
        client.format_range(edge_tab, f"{flag_col}2:{flag_col}{EDGE_ROWS}", {"horizontalAlignment": "CENTER"})
        # TEXT_CONTAINS, not TEXT_EQ: Flags can hold more than one
        # space-separated token (Fix 2.1). None of the six tokens is a
        # substring of another (checked -- LINE↑/LINE↓ in particular don't
        # collide, different trailing glyph), so substring matching can't
        # misfire onto the wrong flag.
        for text, fmt in FLAG_CHIPS.items():
            client.add_boolean_rule(
                edge_tab,
                f"{flag_col}2:{flag_col}{EDGE_ROWS}",
                condition_type="TEXT_CONTAINS",
                values=[text],
                fmt=fmt,
            )

    avail_col = _edge_letter("Avail")
    if avail_col:
        client.format_range(
            edge_tab, f"{avail_col}2:{avail_col}{EDGE_ROWS}", {"horizontalAlignment": "CENTER"}
        )
        for text, fmt in AVAIL_CHIPS.items():
            client.add_boolean_rule(
                edge_tab,
                f"{avail_col}2:{avail_col}{EDGE_ROWS}",
                condition_type="TEXT_EQ",
                values=[text],
                fmt=fmt,
            )

    # OwnStatus's one job (renamed from LevBasis, Part 7.9) is a
    # data-freshness marker: "unpublished" means Own% is still all zeros
    # this week, so Leverage reads blank rather than a number that looks
    # real but isn't. Grey the whole row via OwnStatus itself rather than
    # graying CeilPct -- CeilPct is a real, independent number regardless
    # of ownership status, never a stand-in for Leverage anymore.
    _apply_own_status_marker(client, edge_tab, edge_header, data_start=2, last_row=EDGE_ROWS)
    _apply_zone_label_style(client, edge_tab, edge_header, data_start=2, last_row=EDGE_ROWS)

    # "Already in my pool" + "flagged" on the Name cell -- Pool is a
    # blank/Cash/GPP/Both dropdown now, not a TRUE/FALSE checkbox (Fix
    # 2.11), so any non-blank value counts as "pooled".
    _apply_name_flag_style(
        client, edge_tab, edge_header, data_start=2, last_row=EDGE_ROWS, pool_column=POOL_HEADER
    )

    client.clear_column_groups(edge_tab)
    client.set_column_group_control_before(edge_tab)
    for first, last in EDGE_COLUMN_GROUPS:
        a, b = _edge_letter(first), _edge_letter(last)
        if a and b:
            # Phase 6, Part 2 override of Fix 2.9 -- collapsed by default
            # now, matching Player Pool/Lineups (see EDGE_COLUMN_GROUPS'
            # own comment).
            client.group_columns(edge_tab, a, b, collapsed=True)

    return (
        f"{edge_tab}: widths, header, banding, formats, {n_scaled} colour scale(s), "
        f"{n_position_scaled} per-position scale(s), position tint, chips applied"
    )


# ---------------------------------------------------------------------------
# Player Pool / Lineups / PlayerPoolRaw -- number formats and freeze only
# ---------------------------------------------------------------------------

# EDGE_WIDTHS merged in so the EdgeRaw-linked block (CeilVal/Leverage/
# GameEnv/Stadium/Roof/Wind/Avail/Flag/...) gets the same widths here as
# on EdgeRaw itself, not left at Sheets' own default -- found missing by
# `dfs setup audit-style`. "Name"/"Team" appear in both dicts with
# identical values, so the merge doesn't change either.
BUILDER_WIDTHS = {
    **EDGE_WIDTHS,
    "Pos.": 52,
    "Opp.": 54,
    "Venue": 56,
    "DK Sal": 78,
    # Widened from 72 -- Phase 6, Part 1.5's own audit-style check (built
    # for EdgeRaw) also caught this one truncating live once run against
    # every audited tab, the same bug class just outside 1.5's original
    # scope. Renamed "% of Rstr" -> "% of Own" in Part 2 (alongside Rstr%'s
    # own rename to Own%), then "% of Own" -> "% of Cap" in Part 7.9 (its
    # denominator changed from the lineup's own running salary total to
    # the salary cap constant -- see `polish_lineups_pct_of_cap`).
    "% of Cap": 90,
    "Pool": 64,
    # Week 3 feedback (A5): shrunk from 64. The per-row cell text is just
    # the arrow now (`edge_row_hyperlink_formula`), which alone would fit
    # in ~28px, but the HEADER cell still reads "Edge ↗" in full (the
    # column's own lookup-by-name key, used everywhere -- renaming it
    # would be a much bigger, out-of-scope change) -- found by
    # `dfs setup audit-style` flagging 28px as truncating that header
    # text. 60 is `sheet_audit._min_header_width_px("Edge ↗")` (57) plus
    # a small margin, still meaningfully narrower than the original 64.
    "Edge ↗": 60,
    "Used": 52,
    "In": 96,
    # "Team Implied"/"Ceil"/"Overflow" had NO entry here at all before this
    # -- PlayerPoolRaw/Player Pool/Lineups' own `polish_builder_tab` only
    # sets a width for a name it finds IN this dict (see its own width
    # loop below), so an absent entry means "whatever the column
    # currently is," never actively managed. Found live: "Team Implied"
    # sat at two DIFFERENT widths on PlayerPoolRaw (81px) and Player Pool
    # (57px), both too narrow -- consistent with nothing ever having set
    # either on purpose. Added explicitly so all three tabs converge to
    # the same, sufficient width and self-heal on every future polish run.
    "Team Implied": 115,
    "Ceil": 56,
    "Overflow": 88,
    # Same gap, found live during Part 7.4's own reorder (2026-09-18):
    # inserting GameID/TmRank shifted "O/U" onto a physical column that
    # had previously held something narrower, and nothing had ever set
    # its width on purpose either -- `dfs setup audit-style` caught it
    # truncating on Player Pool (33px, needs ~37px). "Pts" has the exact
    # same gap (no entry, so no active management) -- added preemptively
    # rather than waiting for the next reorder to expose it too.
    "O/U": 60,
    "Pts": 65,
    # Part 7.5: Lineups-only, the lineup-metrics block
    # (`sheet_lineup_metrics.LINEUP_METRIC_HEADERS`). "Stack" holds
    # strings up to "QB+3 (KC) + 2 bring-back"-length.
    "Stack": 180,
    "Games": 56,
    "Bring-back": 92,
    "Own% Used": 92,
    "Sub-10%": 72,
    "Min Unique": 92,
}


def polish_builder_tab(
    client: SheetsClient,
    tab: str,
    *,
    last_row: int,
    header_row: int = 1,
    freeze_rows: int | None = None,
    freeze_cols: int = 1,
    header_repeats_at: list[int] | None = None,
    band_blocks: list[tuple[int, int]] | None = None,
    color_scale_groups: list[tuple[int, int]] | None = None,
) -> str:
    """Number formats, widths, header treatment, colour scales, chips and
    row banding on a tab whose header row names its columns -- the same
    "one visual policy" (Fix 2) EdgeRaw itself gets via `polish_edge`.
    Reads the header first and matches by name, so it never assumes a
    column is in a given position.

    Starts with a whole-tab `clear_conditional_formats`, same as
    `polish_edge` -- necessary here specifically because Player Pool and
    Lineups had accumulated hand-applied rules directly in the browser
    (including one that colour-scaled `Venue`'s `H`/`R` text, and a couple
    spanning two columns at once) that a narrower, column-scoped clear
    can't reliably find. Callers that also own conditional formats outside
    this function's reach on the same tab (`polish_guardrails`' column O)
    must re-run *after* this, not before -- see `sheets_polish`'s own call
    order.

    `header_row` defaults to 1, true for every tab this function styles --
    Lineups' own header sat at row 11 while the (now-removed) pool deck
    occupied the rows above it; back at row 1 like everything else since
    Phase 5's removal (2026-09-16, see `sheet_pool_deck.py`'s module
    docstring). `freeze_rows`/`freeze_cols` default to freezing through
    the header row and pinning Name (column A), same as every other tab.

    `header_repeats_at` styles Lineups' repeated sub-header rows (one per
    lineup block after the first, see `weekly_reset.py`) the same dark
    way as the real header, so every block reads consistently instead of
    only the first one looking like a header. Player Pool/PlayerPoolRaw
    have no repeats and pass nothing.

    `band_blocks` (Fix 2.2) row-bands each block independently -- Player
    Pool/Lineups' per-position/per-lineup blocks are separated by repeated
    header rows and spacer rows that shouldn't be banded as if they were
    data, so each block restarts its own alternating pattern. Omit for a
    tab with no gaps (PlayerPoolRaw): the whole `header_row+1:last_row`
    span is banded as one block.

    `color_scale_groups` (Phase 4, 4.1/4.2): scales colour-scaled columns
    PER GROUP (`apply_grouped_color_scales`) instead of once across the
    whole tab -- Player Pool's 5 position blocks, Lineups' 20 lineup
    blocks, each skipping `GROUPED_TAB_UNSCALED_COLUMNS`. Omit (the
    default) for a tab that isn't naturally grouped (PlayerPoolRaw),
    which keeps the original whole-tab `apply_field_color_scales`.

    Phase 5C: also applies the rest of EdgeRaw's own look wherever the
    matching column exists in `header` -- a Wind chip, a muted
    per-position tint (`Pos.`, EdgeRaw's own equivalent column is
    `Position`), OwnStatus greyed as the data-freshness marker it is, and
    Name bolded when Flag is set -- via the same shared helpers
    `polish_edge` itself calls (see the module-level comment just above
    `polish_edge`), so the two never drift into two different policies.

    Part 7.9: also hides `Id`/`Flag` outright by name, same as
    `polish_edge` already did for EdgeRaw's own `Id` -- neither was ever
    hidden here before this, since both sit past the collapsed group's own
    range (see this function's body for why).
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty header row -- skipped"

    last_col = column_letter(len(header) - 1)
    client.clear_conditional_formats(tab)
    client.clear_banding(tab)
    for start, end in band_blocks or [(header_row + 1, last_row)]:
        client.add_row_banding(
            tab, f"A{start}:{last_col}{end}", first_band_color=WHITE, second_band_color=BAND_BG
        )

    client.format_range(tab, f"A{header_row}:{last_col}{header_row}", _HEADER_FMT)
    for repeat_row in header_repeats_at or []:
        client.format_range(tab, f"A{repeat_row}:{last_col}{repeat_row}", _HEADER_FMT)
    client.freeze(tab, rows=freeze_rows if freeze_rows is not None else header_row, cols=freeze_cols)

    # Id/Flag hidden outright, same treatment as `polish_edge`'s own
    # EdgeRaw -- both sit in `sheet_columns.INTERNAL`, past the collapsed
    # Game/Ceiling detail/Movement/Weather group, so neither is inside
    # that group's own range and both would otherwise sit fully visible
    # right before this tab's own trailing columns (Overflow/Pool/Used/In
    # on Player Pool, nothing on PlayerPoolRaw/Lineups). Found missing
    # entirely on all three tabs while wiring up Part 7.9's Flag/Flags
    # split -- EdgeRaw was the only tab that ever actually hid Id.
    #
    # Reset every non-grouped column visible first, same reasoning and
    # same fix as `polish_edge`'s own reset just above it -- a
    # killed/retried run can otherwise leave a stale hide on the wrong
    # column behind forever.
    _unhide_ungrouped_columns(client, tab, len(header))
    # "Added" (A6, 2026-09-22) joins Id/Flag here -- Player Pool's own
    # hidden add-a-player accumulator, never meant to be looked at
    # directly (see `weekly_reset.PLAYER_POOL_ADDED_NAMES_HEADER`).
    for hidden_name in ("Id", "Flag", "Added"):
        if hidden_name in header:
            letter = column_letter(header.index(hidden_name))
            client.hide_columns(tab, letter, letter)

    widths = {}
    for i, name in enumerate(header):
        if name in BUILDER_WIDTHS:
            widths[column_letter(i)] = BUILDER_WIDTHS[name]
    if widths:
        client.set_column_widths(tab, widths)

    applied = apply_field_formats(client, tab, header, header_row=header_row, last_row=last_row)
    if color_scale_groups is not None:
        scaled = apply_grouped_color_scales(
            client, tab, header, color_scale_groups, skip=GROUPED_TAB_UNSCALED_COLUMNS
        )
    else:
        scaled = apply_field_color_scales(client, tab, header, header_row=header_row, last_row=last_row)

    # Flag/Avail/Venue/Pool chips, same as EdgeRaw's own (Flag/Avail were
    # found missing entirely by `dfs setup audit-style`; Venue is new --
    # Fix 2.1/2.3). The whole-tab clear above already removed any prior
    # rule on these columns; the per-column clear here just keeps this
    # loop safe to call on its own too.
    chipped = 0
    data_start = header_row + 1
    for column_name, chips in (
        ("Flags", FLAG_CHIPS),
        ("Avail", AVAIL_CHIPS),
        ("Venue", VENUE_CHIPS),
        ("Pool", POOL_TYPE_CHIPS),
    ):
        if column_name not in header:
            continue
        letter = column_letter(header.index(column_name))
        client.clear_conditional_formats(tab, column=letter)
        client.format_range(
            tab, f"{letter}{data_start}:{letter}{last_row}", {"horizontalAlignment": "CENTER"}
        )
        # Flags alone can hold more than one space-separated token (Fix
        # 2.1); Avail/Venue/Pool are still single exact values.
        condition_type = "TEXT_CONTAINS" if column_name == "Flags" else "TEXT_EQ"
        for text, fmt in chips.items():
            client.add_boolean_rule(
                tab,
                f"{letter}{data_start}:{letter}{last_row}",
                condition_type=condition_type,
                values=[text],
                fmt=fmt,
            )
        chipped += 1

    # Phase 5C: the rest of EdgeRaw's own look (Wind chip, per-position
    # tint, OwnStatus's grey freshness marker, Name bold-on-Flag) --
    # `pool_column=None` since Player Pool/Lineups/PlayerPoolRaw have no
    # unpooled rows to distinguish the way EdgeRaw does (see
    # `_apply_name_flag_style`'s own docstring).
    _apply_wind_chip(client, tab, header, data_start=data_start, last_row=last_row)
    _apply_position_tint(client, tab, header, column_name="Pos.", data_start=data_start, last_row=last_row)
    _apply_pool_tag_tint(client, tab, header, column_name="Pool", data_start=data_start, last_row=last_row)
    _apply_own_status_marker(client, tab, header, data_start=data_start, last_row=last_row)
    _apply_edge_link_style(client, tab, header, data_start=data_start, last_row=last_row)
    _apply_name_flag_style(client, tab, header, data_start=data_start, last_row=last_row)
    _apply_zone_label_style(client, tab, header, data_start=data_start, last_row=last_row)

    pin_note = "Name pinned" if freeze_cols else "no column pin"
    return (
        f"{tab}: header styled, {pin_note}, banded, {applied} column(s) number-formatted, "
        f"{scaled} colour scale(s), {chipped} chip column(s)"
    )


# ---------------------------------------------------------------------------
# Lineups Guardrails (Task L): per-lineup validation in the "Issues" column
# ---------------------------------------------------------------------------

# Renamed from "Check" -- Sam asked what it was, since the old name didn't
# say (Fix A1). Also the header text `polish_guardrails` finds ITS OWN
# column by -- Phase 3 gave "Issues" a real, designed position of its own
# in `sheet_columns.LINEUPS_COLUMN_ORDER`, so hardcoding a column letter
# here (this used to be `_GUARDRAILS_COLUMN = "O"`, true only because
# "Issues" happened to sit at O in the pre-Phase-3 layout) is exactly the
# hazard CONTRIBUTING.md warns about: Phase 3's reorder moved "Issues" to
# a new column, and this function kept writing to the OLD literal "O" --
# which by then held "Flag" -- silently clobbering Flag's real EdgeRaw
# formula with a duplicate copy of the guardrails header/formulas. Found
# during Phase 3's own template verification pass; see CONTRIBUTING.md's
# changelog.
_GUARDRAILS_HEADER = "Issues"
_GUARDRAILS_CHIPS = [
    ("TEXT_CONTAINS", "DUPLICATE", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_CONTAINS", "OVER", _chip(CRIT_BG, CRIT_FG)),
    # Part 7.4: both real Issues warnings (not the reported-only stack
    # shape, see `_stack_check_formula`'s own docstring) -- same CRIT
    # severity as DUPLICATE/OVER, since both are simply correct for cash
    # and GPP alike, not a judgment call. TEXT_CONTAINS since the totals
    # cell may combine this with an unrelated OVER/INCOMPLETE token.
    ("TEXT_CONTAINS", "DST/QB", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_CONTAINS", "RB/GAME", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_EQ", "OUT", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_EQ", "IR", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_EQ", "Q", _chip(WARN_BG, WARN_FG)),
    ("TEXT_CONTAINS", "INCOMPLETE", _chip(WARN_BG, WARN_FG)),
    ("TEXT_EQ", "OK", _chip(OK_BG, OK_FG)),
]


def _slot_check_formula(start: int, end: int, row: int, avail_col: str) -> str:
    """Flags a name duplicated elsewhere in its own block, else surfaces
    that pick's linked Avail flag (OUT/IR/Q) if it has one. `end` is the
    block's own last REAL roster row now (Fix 2.4 -- it used to also be
    the totals row), so the duplicate check spans `start..end` directly,
    no `-1` needed."""
    return (
        f'=IF($A{row}="","",'
        f'IF(COUNTIF($A${start}:$A${end},$A{row})>1,"DUPLICATE",'
        f'IF(${avail_col}{row}<>"",${avail_col}{row},"")))'
    )


def _stack_check_formula(
    start: int, end: int, *, position_col: str, team_col: str, opp_col: str, gameid_col: str
) -> str:
    """Part 7.4's two "real Issues warnings" stack rules -- both simply
    correct for cash and GPP alike (unlike stack SHAPE, which is
    REPORTED, never warned about -- see the lineup-metrics block, Part
    7.5, and 7.4's own spec text on why a QB+2/QB+1/no-stack judgment
    call doesn't belong here). Computed once per lineup block's totals
    row. `IFERROR` degrades a position genuinely absent from a still-
    partial lineup (no QB rostered yet, say) to "no violation possible
    yet" rather than a broken `#N/A` cell.

    1. Never roster a DST against your own QB's team -- correlation
       -0.46, the largest single coefficient in the whole review; when
       this DST does well, it's specifically at this QB's expense.
    2. Max one RB per game -- `COUNTIFS(GameID_range, GameID_range, ...)`
       inside `SUMPRODUCT`, a self-referential duplicate-count idiom.
       Confirmed elementwise-safe inside `SUMPRODUCT` on the template's
       Scratch tab before shipping -- unlike `MATCH`, which does NOT
       broadcast the same way inside a plain array literal (see
       `sheet_pool_formulas.py`'s own docstring for that one).

       Found live testing a real lineup (2026-09-18, right after the
       "DEF"->"DST" fix): the earlier Scratch verification of the blank-
       `GameID` case was WRONG -- it tested two rows with BOTH `Position`
       and `GameID` genuinely blank (never-typed cells), which trivially
       can't match since `Position="RB"` is already false for a blank
       cell. It never exercised the REAL shape: two fixed roster slots
       whose `Position` is a real, non-blank `"RB"` label (Lineups' `Pos.`
       column is static text per slot, filled or not) while `GameID` is a
       FORMULA cell (`=IF($A="","",...)`) that RESOLVES to `""` when the
       slot has no name typed. A formula-produced `""` is NOT the same as
       a truly empty cell to `COUNTIFS`: `COUNTIFS(range, range)` DOES
       treat two formula-blank `""` cells as matching each other (unlike
       two genuinely-blank cells, which don't match) -- confirmed
       directly on Scratch. That made `rb_per_game` fire on every
       lineup with 2+ unfilled RB slots, i.e. almost every incomplete
       lineup -- a real, active false positive.

       Fixed by multiplying in a direct `GameID_range<>""` SUMPRODUCT
       term (not a `COUNTIFS` `"<>"` criterion -- also confirmed on
       Scratch that `COUNTIFS(range,"<>")` treats a formula-blank cell as
       *non*-blank, since the cell holds a formula, so that pattern does
       NOT filter these rows out). Direct cell inequality (`<>""`) does
       correctly evaluate `FALSE` for a formula-blank cell, zeroing out
       any row with no real `GameID` before it can self-match, while
       still catching two RBs that genuinely share a real `GameID`.

    Deliberately NOT a QB+RB rule (7.4's own text: sources disagree
    wildly, 0.07 to 0.43, and the two that measured it carefully call it
    functionally zero)."""
    qb_team = (
        f"IFERROR(INDEX(${team_col}${start}:${team_col}${end},"
        f'MATCH("QB",${position_col}${start}:${position_col}${end},0)),"")'
    )
    dst_opp = (
        f"IFERROR(INDEX(${opp_col}${start}:${opp_col}${end},"
        f'MATCH("DST",${position_col}${start}:${position_col}${end},0)),"")'
    )
    dst_vs_own_qb = f'IF(AND({qb_team}<>"",{dst_opp}<>"",{qb_team}={dst_opp}),"DST/QB","")'

    rb_per_game = (
        f'IF(SUMPRODUCT((${position_col}${start}:${position_col}${end}="RB")*'
        f'(${gameid_col}${start}:${gameid_col}${end}<>"")*'
        f'(COUNTIFS(${position_col}${start}:${position_col}${end},"RB",'
        f"${gameid_col}${start}:${gameid_col}${end},${gameid_col}${start}:${gameid_col}${end})>1))>0,"
        f'"RB/GAME","")'
    )
    return f'TEXTJOIN(" ",TRUE,{dst_vs_own_qb},{rb_per_game})'


def _totals_check_formula(
    start: int, end: int, totals_row: int, salary_col: str, *, stack_check: str | None = None
) -> str:
    """Salary cap, roster completeness, or OK -- on the block's totals
    row (Fix 2.4: `totals_row` is `end + 1`, a real separate row now, not
    `end` itself). `salary_col` is found by header name at the call site
    (DK Sal), never hardcoded -- this used to read the literal column
    `D`, true only because DK Sal happened to sit there before Phase 3's
    reorder moved it.

    `stack_check` (Part 7.4, `_stack_check_formula`'s own output, passed
    in rather than built here so this function's own cap/completeness
    logic stays independently testable) is appended ADDITIVELY --
    "OVER $500 RB/GAME", say -- rather than replacing whatever OVER/
    INCOMPLETE/OK already resolved to, so a real stack violation is never
    silently masked by an unrelated cap/completeness issue (the same
    "don't let one condition hide another" principle `Flags` already
    established after Part 1.1's LINE-suppresses-everything bug). `None`
    (the caller's default when Position/Team/Opp/GameID aren't all
    linked yet) reproduces the exact prior behaviour."""
    cap_or_completeness = (
        f'IF(COUNTA($A${start}:$A${end})=0,"",'
        f'IF({salary_col}{totals_row}>50000,"OVER "&TEXT({salary_col}{totals_row}-50000,"$#,##0"),'
        f"IF(COUNTA($A${start}:$A${end})<9,"
        f'"INCOMPLETE "&COUNTA($A${start}:$A${end})&"/9","OK")))'
    )
    if not stack_check:
        return f"={cap_or_completeness}"
    base = f"({cap_or_completeness})"
    return (
        f'=IF({base}="","",'
        f'IF({stack_check}="",{base},'
        f'IF({base}="OK",{stack_check},{base}&" "&{stack_check})))'
    )


def polish_lineups_input_column(client: SheetsClient, tab: str, name_blocks: list[tuple[int, int]]) -> str:
    """Column A within each lineup block is one of the four places in the
    whole workbook you type into by hand -- give it the shared "you type
    here" cue (see the visual-grammar docstring below), same treatment as
    EdgeRaw's Pool column and Exposure's Target column. Player Pool's own
    column A must NEVER get this: it looks identical (a plain Name column)
    but is fully computed, not typed -- see `polish_builder_tab`, which is
    what actually styles Player Pool, and does not call this."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    for start, end in name_blocks:
        client.format_range(tab, f"A{start}:A{end}", {"backgroundColor": INPUT_BG})
    return f"{tab}: column A marked as input across {len(name_blocks)} lineup block(s)"


def polish_lineups_totals_rows(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]], salary_cap: int
) -> str:
    """Fix 2.4: every totals row (the row directly below each block's 9th
    real slot) was being treated as a tenth roster slot by every
    VLOOKUP-by-name column -- Venue, O/U, Spread, Team Implied,
    OppPosRank, and the entire EdgeRaw-linked block (CeilVal..Flag), all
    permanently `#N/A` since a totals row's own Name cell (column A) is
    always blank. This clears those dead cells, sums Ceil onto the totals
    row the same way Pts already is (only Salary and Pts were summed
    before; Sam had been hand-editing Ceil totals into lineups), and
    labels the row so it reads as a footer rather than a broken slot.
    Issues is left alone -- it already holds the real cap/completeness
    check, not a dead VLOOKUP, despite sitting near the columns that do.

    Phase 6, Part 1 (2026-09-17): Pts and Rstr%'s totals-row cells are
    ALSO now unconditionally overwritten with a real `SUM`, not left
    alone. They were assumed (see the paragraph above, before this fix)
    to "already hold real, working formulas" -- verified false live: only
    5 of 20 blocks (0, 1, 2, 3, 6) actually had `=SUM(...)`; the other 15
    had `=VLOOKUP($A<row>,PlayerPoolRaw!$A:S,11,false)` /
    `,14,false)` sitting in Pts/Rstr% instead -- a lookup against the
    totals row's own permanently-blank Name cell, which resolves to
    `#N/A` the moment a lineup in one of those blocks is built. Not
    written by any `dfs` command (same as Salary's own totals-row SUM,
    which was and still is correct on every block) -- likely a stale
    hand-edit or an incomplete copy/paste, not a code defect. Fixed the
    same self-healing way Ceil already was: derived from `name_blocks`,
    rewritten unconditionally on every call.

    "Total" sits at Opp.'s column (left of the Salary sum), an otherwise-
    dead cell on a totals row -- unchanged since Phase 3. "Remaining" used
    to sit a dozen columns away at Venue (the number) and Val (the label),
    both incidental leftovers of an even older, pre-Phase-3 layout -- Fix
    2.4/Phase 3's own history is in CONTRIBUTING.md's changelog.

    Week 3 feedback (A8), 2026-09-22: Sam: "In lineups, I'd like remaining
    under the total, not off to the side (I manually did it this week)."
    Found live: no code had ever actually written the documented "average
    remaining per slot" helper row either (docs/SHEET_REFERENCE.md
    described `polish_lineups_remaining_per_slot_helper` as regenerating
    a genuinely hand-authored `INDIRECT` formula -- but that formula was
    never actually present on the template OR the live sheet, so that
    function silently no-op'd on every single call it ever made; verified
    directly before touching anything, not assumed). Sam's own hand-edit
    on the live sheet matched exactly what's built here: `Remaining` now
    lives ONE ROW BELOW the totals row, in the SAME column as `Total`'s
    own Salary sum (`salary_col`) -- read directly off that row rather
    than an `INDIRECT`-built reference, since this function writes it
    fresh every time rather than trying to preserve a hand-authored
    formula's position across a reorder; there is nothing left to
    preserve. Its own "Remaining" label sits directly below "Total"'s,
    same column (`total_label_col`). `Venue`'s old totals-row cell and
    Val's old "Remaining" label are both now genuinely dead and cleared
    like any other stale cell.

    Average remaining per unfilled slot (A8's second ask) rides on the
    SAME new row, in `Pts`'s column (otherwise dead there) --
    `remaining / COUNTBLANK(this block's Name cells)`, blank (not a
    divide-by-zero error) once every slot is filled, same "blank over a
    confusing error" instinct as `% of Cap`'s own guard.

    Column positions are found from the tab's own header, never
    hardcoded -- same reasoning as every other lookup-by-name function in
    this file. Idempotent: every write here is a plain overwrite (a
    formula, a label, or a cleared cell), safe to re-run.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: no header found at row {header_row} -- skipped"

    def col(name: str) -> str | None:
        return column_letter(header.index(name)) if name in header else None

    dead_columns = [
        c for c in ("O/U", "Spread", "Team Implied", "OppPosRank", *LINKED_EDGE_COLUMNS) if col(c)
    ]
    ceil_col = col("Ceil")
    pts_col = col("Pts")
    rstr_col = col("Own%")
    salary_col = col("DK Sal")
    total_label_col = col("Opp.")
    # A8: Venue's old remaining-cap NUMBER and Val's old "Remaining" LABEL
    # are both genuinely dead now that both live one row below instead --
    # cleared alongside Team's old "Total" leftover, same reasoning. A
    # plain set: Venue shows up in both original reasons, only worth
    # clearing once.
    stale_total_cols = {c for c in (col("Team"), col("Venue"), col("Val")) if c and c != total_label_col}

    cleared = 0
    labeled = 0
    summed = 0
    reset = 0
    for start, end in name_blocks:
        totals_row = end + 1
        remaining_row = totals_row + 1
        for name in dead_columns:
            letter = col(name)
            client.update_range(tab, f"{letter}{totals_row}", [[""]])
            cleared += 1
        for stale_col in sorted(stale_total_cols):
            client.update_range(tab, f"{stale_col}{totals_row}", [[""]])
        if ceil_col:
            ceil_sum = f"=SUM({ceil_col}{start}:{ceil_col}{end})"
            client.update_range(tab, f"{ceil_col}{totals_row}", [[ceil_sum]])
            summed += 1
        if pts_col:
            pts_sum = f"=SUM({pts_col}{start}:{pts_col}{end})"
            client.update_range(tab, f"{pts_col}{totals_row}", [[pts_sum]])
            summed += 1
        if rstr_col:
            rstr_sum = f"=SUM({rstr_col}{start}:{rstr_col}{end})"
            client.update_range(tab, f"{rstr_col}{totals_row}", [[rstr_sum]])
            summed += 1
        if total_label_col:
            client.update_range(tab, f"{total_label_col}{totals_row}", [["Total"]])
            labeled += 1
        # A8: Remaining -- value and label both -- now sits directly below
        # Total, in the same two columns, rather than off to the side.
        if salary_col:
            remaining_formula = f"={salary_cap}-{salary_col}{totals_row}"
            client.update_range(tab, f"{salary_col}{remaining_row}", [[remaining_formula]])
        if total_label_col:
            client.update_range(tab, f"{total_label_col}{remaining_row}", [["Remaining"]])
            labeled += 1
        # A8: average remaining per unfilled slot, same new row, Pts'
        # column (otherwise dead there) -- blank once the block is full
        # rather than a #DIV/0! error.
        if pts_col and salary_col:
            name_range = f"$A${start}:$A${end}"
            avg_formula = (
                f'=IF(COUNTBLANK({name_range})=0,"",{salary_col}{remaining_row}/COUNTBLANK({name_range}))'
            )
            client.update_range(tab, f"{pts_col}{remaining_row}", [[avg_formula]])
        # Column A (Name) on a totals row still carried the same input
        # background AND typo-guard player dropdown as a real roster
        # slot -- a leftover from before an earlier fix (2.4) shrank each
        # block's own range to exclude the totals row, never retroactively
        # cleaned up off the row it stopped covering. Found live, from a
        # screenshot: the totals row showed the exact same dropdown arrow
        # as the real slot above it.
        client.clear_data_validation(tab, f"A{totals_row}")
        client.format_range(tab, f"A{totals_row}", {"backgroundColor": WHITE})
        reset += 1

    return (
        f"{tab}: {len(name_blocks)} totals row(s) -- {cleared} dead VLOOKUP(s) cleared, "
        f"{summed} sum(s) written (Ceil/Pts/Own%), {labeled} label(s) written, "
        f"{reset} Name cell(s) un-typo-guarded"
    )


def polish_lineups_pct_of_cap(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]], salary_cap: int
) -> str:
    """Phase 6, Part 1.2 gave this column (`% of Rstr` at the time)
    `=F<row>/F$<totals_row>` -- this player's DK Sal as a share of the
    lineup's own running salary total -- and guarded it against dividing
    by zero on a fresh lineup, since the block's total salary is 0 until
    at least one name is typed. Not written by any `dfs` command before
    that (a genuinely hand-authored template formula, like the "average
    remaining per slot" helper documented in docs/SHEET_REFERENCE.md).

    Part 7.9 found that fix was patching the wrong problem: Sam confirmed
    the intended meaning is "what share of my total lineup salary is this
    player taking up" -- cap allocation, not rostership share, and not a
    running total either (`F$<totals_row>` lurches as slots fill: three
    players in, each reads ~33%, meaningful only once the lineup is
    complete, which is exactly when it stops being needed). Renamed
    `Rstr%` -> `Own%` -> unrelated; this column itself went `% of Rstr` ->
    `% of Own` (Part 2, name-matching Own%'s own rename) -> `% of Cap`
    (Part 7.9, once the real meaning was confirmed) -- the name changed
    twice while the bug underneath was still there both times.

    Dividing by the salary cap (`config.toml`'s `[lineups] salary_cap`,
    never hardcoded 50000) instead of the block's own running total fixes
    the #DIV/0! at the source -- a constant denominator can't divide by
    zero, so the old whole-block-empty guard is gone, not just redundant.
    Still guards the individual blank-slot case (`$A<row>=""`) -- "an
    empty slot has no share" (Sam's own framing for the original bug), not
    a real 0% for a slot nobody's picked yet.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: no header found at row {header_row} -- skipped"

    def col(name: str) -> str | None:
        return column_letter(header.index(name)) if name in header else None

    name_col = col("Name")
    salary_col = col("DK Sal")
    pct_col = col("% of Cap")
    if not (name_col and salary_col and pct_col):
        return f"{tab}: 'Name'/'DK Sal'/'% of Cap' not all present -- skipped"

    written = 0
    for start, end in name_blocks:
        for row in range(start, end + 1):
            formula = f'=IF({name_col}{row}="","",{salary_col}{row}/{salary_cap})'
            client.update_range(tab, f"{pct_col}{row}", [[formula]])
            written += 1

    return f"{tab}: '% of Cap' set against the {salary_cap} salary cap for {written} row(s)"


def fix_lineups_dst_slot_label(
    client: SheetsClient, tab: str, *, position_col: str, name_blocks: list[tuple[int, int]]
) -> str:
    """Found live (2026-09-18) verifying Part 7.4's new DST-vs-own-QB
    guardrail with a real test lineup: it never fired, on either sheet,
    for any block. Root cause has nothing to do with that guardrail's own
    formula -- `Lineups`' own `Pos.` column is STATIC TEXT (never a
    formula, confirmed via a real formula-mode read), one fixed roster-
    slot label per row (`QB, RB, RB, WR, WR, WR, TE, FLEX, DST` in DK's
    own classic-contest order) written once, presumably by hand, before
    this codebase's own "DST" convention (`derived.EDGE_COLUMNS`, Player
    Pool's own DST block, EVERY other reference anywhere in this project)
    existed. The defense slot's own label reads `"DEF"` instead, on every
    single one of 20 blocks, both sheets -- confirmed by reading every
    block's own last row directly rather than assuming a single instance
    generalizes. `_stack_check_formula`'s `MATCH("DST", Position_range,
    0)` can never find a slot literally labelled `"DEF"`, so `dst_opp`
    always resolves to `""` via its own `IFERROR` guard, and the whole
    check silently never fires -- not a formula bug, a stale-data one,
    invisible until tested against a real lineup because an untested
    guardrail LOOKS identical to a working one that just hasn't found a
    violation yet.

    Verify-then-overwrite, never blind: only touches a row that actually
    still says `"DEF"`, so a block already fixed (or one that was never
    wrong) is left untouched and reported as such, not silently
    re-written."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    fixed = 0
    already_correct = 0
    unexpected: list[tuple[int, str]] = []
    for _start, end in name_blocks:
        cell = f"{position_col}{end}"
        current = client.read_range(tab, cell)
        value = current[0][0].strip() if current and current[0] else ""
        if value == "DST":
            already_correct += 1
        elif value == "DEF":
            client.update_range(tab, cell, [["DST"]])
            fixed += 1
        else:
            unexpected.append((end, value))

    note = f", {len(unexpected)} unexpected value(s) left untouched: {unexpected}" if unexpected else ""
    return f"{tab}: {fixed} 'DEF' -> 'DST' fix(es), {already_correct} already correct{note}"


def polish_guardrails(
    client: SheetsClient,
    tab: str,
    *,
    header_row: int,
    name_blocks: list[tuple[int, int]],
    header_repeats_at: list[int] | None = None,
) -> str:
    """Each lineup block currently checks exactly one thing (salary
    remaining, via its own DK Sal-column formulas). This adds the checks
    that actually catch mistakes, in the "Issues" column -- found by
    header name (see the module-level note by `_GUARDRAILS_HEADER` on why
    this was a hardcoded column letter before Phase 3 and why that broke).

    Per roster slot: DUPLICATE if the same name appears twice in that
    lineup, else that pick's Avail flag (OUT/IR/Q) if it has one. On the
    block's totals row: OVER the cap, INCOMPLETE (fewer than 9 picks),
    Part 7.4's two stack-rule violations (DST/QB, RB/GAME -- see
    `_stack_check_formula`), any combination of those additively, or OK.
    The Avail/DK Sal/Pos./Team/Opp./GameID columns are all found by
    header name, never a hardcoded letter -- exactly the class of
    assumption that caused this feature's own prerequisite bug (see
    CONTRIBUTING.md's changelog); skips cleanly if `dfs setup link-edge`
    hasn't run yet, or if "Issues"/"DK Sal" aren't in the header for some
    other reason. The two stack checks specifically degrade gracefully
    (no crash, just skipped) if Position/Team/Opp./GameID aren't all
    linked yet -- they're additive on top of the cap/completeness check,
    never a hard requirement for this function to run at all.

    `header_repeats_at` (Fix 2.6) re-prints the header at Lineups' repeated
    sub-header rows too -- the original version only wrote it once, at
    `header_row`, which is why 19 of 20 blocks were missing it (found by
    Sam: "the Check header appears only on the pool and the first
    lineup"). `link_edge_columns`/`polish_builder_tab` already take the
    same parameter for the same reason; this was the one column that
    hadn't caught up.

    Re-runnable -- clears only the Issues column's own conditional-format
    rules first, never the tab's other rules, which
    `sheet_links.link_edge_columns` already owns.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if "Avail" not in header:
        return f"{tab}: 'Avail' not linked yet (run `dfs setup link-edge` first) -- skipped"
    if _GUARDRAILS_HEADER not in header:
        return f"{tab}: {_GUARDRAILS_HEADER!r} column not found in header -- skipped"
    if "DK Sal" not in header:
        return f"{tab}: 'DK Sal' column not found in header -- skipped"
    avail_col = column_letter(header.index("Avail"))
    salary_col = column_letter(header.index("DK Sal"))
    guardrails_col = column_letter(header.index(_GUARDRAILS_HEADER))

    # Part 7.4: the two stack checks are additive, not required -- a
    # Lineups build that predates `GameID` being linked (or mid-migration)
    # still gets its cap/completeness check exactly as before.
    stack_cols = ("Pos.", "Team", "Opp.", "GameID")
    stack_ready = all(name in header for name in stack_cols)
    position_col, team_col, opp_col, gameid_col = (
        (column_letter(header.index(name)) for name in stack_cols)
        if stack_ready
        else (None, None, None, None)
    )

    client.set_column_widths(tab, {guardrails_col: 110})
    client.update_range(tab, f"{guardrails_col}{header_row}", [[_GUARDRAILS_HEADER]])
    for repeat_row in header_repeats_at or []:
        client.update_range(tab, f"{guardrails_col}{repeat_row}", [[_GUARDRAILS_HEADER]])

    for start, end in name_blocks:
        totals_row = end + 1
        rows = [[_slot_check_formula(start, end, row, avail_col)] for row in range(start, end + 1)]
        stack_check = (
            _stack_check_formula(
                start,
                end,
                position_col=position_col,
                team_col=team_col,
                opp_col=opp_col,
                gameid_col=gameid_col,
            )
            if stack_ready
            else None
        )
        rows.append([_totals_check_formula(start, end, totals_row, salary_col, stack_check=stack_check)])
        client.update_range(tab, f"{guardrails_col}{start}:{guardrails_col}{totals_row}", rows)

    client.clear_conditional_formats(tab, column=guardrails_col)
    last_row = max(end for _, end in name_blocks) + 1
    a1_range = f"{guardrails_col}2:{guardrails_col}{last_row}"
    for condition_type, value, fmt in _GUARDRAILS_CHIPS:
        client.add_boolean_rule(tab, a1_range, condition_type=condition_type, values=[value], fmt=fmt)

    return f"{tab}: guardrails applied to column {guardrails_col} across {len(name_blocks)} lineup(s)"


# ---------------------------------------------------------------------------
# Bankroll (Direction G)
# ---------------------------------------------------------------------------

_CURRENCY = _num("$#,##0.00", "CURRENCY")
_PERCENT = _num("0.0%", "PERCENT")

# The KPI block at the top of Bankroll, addressed by the cells the tab
# already uses. Rows 1-13 only; both ledgers live below row 15 and are not
# touched here beyond their header rows and money columns.
BANKROLL_CURRENCY_CELLS = ["B1:B2", "D1:D3", "F1", "I1:I2", "B6:B7", "B9", "D12:D13", "F12:F13", "H12:H13"]
BANKROLL_PERCENT_CELLS = ["D4", "F2", "B8", "B10", "B12:B13"]
BANKROLL_LABEL_CELLS = ["A1:A13", "C1:C4", "E1:E2", "G12:G13", "H1:H2", "C12:E13"]


def polish_bankroll(
    client: SheetsClient,
    tab: str,
    *,
    cash: tuple[int, int, int],
    gpp: tuple[int, int, int],
    entry_key_columns: tuple[str, ...] = (),
) -> str:
    """Direction G: the same ledger, read as a scoreboard.

    Currency and percent formats across the KPI block, the two ledger
    header rows given the same dark treatment as everywhere else, money
    columns formatted, and green/red on the net figures. `cash` and `gpp`
    are (header_row, first_row, last_row) straight from config, so no row
    number is written here.

    `entry_key_columns` (Fix 2.17) hides `bankroll.sync_bucket`'s dedupe
    key column(s) -- an unexplained value with no header sitting in the
    middle of the ledger otherwise (Sam: "a stray column appears at L
    after sync"). Hidden, not deleted or moved -- `sync_bucket` still
    writes real dedupe keys there every sync; same "grouped/hidden, never
    gone" treatment as EdgeRaw's own `Id` column.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    client.clear_conditional_formats(tab)
    for col in sorted(set(entry_key_columns)):
        client.hide_columns(tab, col, col)

    for rng in BANKROLL_CURRENCY_CELLS:
        client.format_range(tab, rng, _CURRENCY)
    for rng in BANKROLL_PERCENT_CELLS:
        client.format_range(tab, rng, _PERCENT)
    for rng in BANKROLL_LABEL_CELLS:
        client.format_range(
            tab, rng, {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9, "bold": False}}
        )

    # The three figures worth reading first, made bigger than their labels.
    for cell in ("B1", "B2", "F1"):
        client.format_range(tab, cell, {"textFormat": {"bold": True, "fontSize": 12, "foregroundColor": INK}})

    for header_row, first, last in (cash, gpp):
        client.format_range(tab, f"A{header_row}:J{header_row}", _HEADER_FMT)
        client.format_range(tab, f"B{first}:B{last}", _num("0"))
        client.format_range(tab, f"C{first}:C{last}", _num("0.0"))
        for col in ("D", "F", "G"):
            client.format_range(tab, f"{col}{first}:{col}{last}", _CURRENCY)
        for col in ("I", "J"):
            client.format_range(tab, f"{col}{first}:{col}{last}", _PERCENT)

    # Net green when positive, red when negative -- on the season net, the
    # weekly net, and both ledger nets.
    for rng in ("F1", "B9", "H12:H13"):
        client.add_boolean_rule(
            tab,
            rng,
            condition_type="NUMBER_GREATER",
            values=["0"],
            fmt={"textFormat": {"foregroundColor": OK_FG, "bold": True}},
        )
        client.add_boolean_rule(
            tab,
            rng,
            condition_type="NUMBER_LESS",
            values=["0"],
            fmt={"textFormat": {"foregroundColor": CRIT_FG, "bold": True}},
        )

    return f"{tab}: KPI block tiled, ledger headers styled, money columns formatted"


# ---------------------------------------------------------------------------
# Tab chrome (Direction M, plus D's colours and hiding -- no renames)
# ---------------------------------------------------------------------------

# Left to right in the order the week actually runs: research, shortlist,
# build, enter, monitor, reconcile. Tabs absent from a given sheet are
# skipped, so this is safe on both the template and the live copy.
# Fix 2.12: reordered to Sam's explicit most-used-first order. The phase
# tag (second element, drives tab colour-coding) stays each tab's real
# phase-of-week regardless of its new position -- this reorders the tab
# STRIP only, it doesn't reclassify anything.
WEEK_ORDER = [
    ("Board", "decide"),
    ("EdgeRaw", "decide"),
    ("Player Pool", "build"),
    ("Lineups", "build"),
    ("Bankroll", "money"),
    ("Results", "money"),
    ("Exposure", "contest"),
    ("Slate Grid", "decide"),
    ("Movement", "contest"),
    ("GPPin", "contest"),
    ("DKLineupsFinal", "contest"),
    ("Scratch", "build"),
    ("DK Upload", "build"),
    ("SoSComb", "feed"),
    ("SoSQB", "feed"),
    ("SoSRB", "feed"),
    ("SoSWr", "feed"),
    ("SoSTE", "feed"),
    ("SoSDef", "feed"),
    ("Instructions", "decide"),
    ("PlayerPoolRaw", "feed"),
]

# Pure staging. Hidden, not deleted -- the API writes to hidden tabs
# perfectly happily, so `dfs sync` is unaffected. PlayerPoolRaw stays
# visible on purpose: it's the hub every other tab reads, and hiding it
# makes a broken lookup much harder to debug.
HIDE_TABS = [
    "DKSalRaw",
    "DkSalClean",
    "oddsraw",
    "oddsFinal",
    "TFFBOptoRaw",
    "GamesRaw",
    "WeatherRaw",
    "EntriesRaw",
    "DKLineupsRaw",
]


def apply_tab_chrome(client: SheetsClient, *, hide_staging: bool = True) -> list[str]:
    """Order the tab strip by phase of the week, colour it by family, and
    hide the staging tabs. Order, colour and visibility only -- no renames,
    so nothing in `tab_mappings` or `config.toml` is affected.
    """
    results = []
    index = 0
    for tab, family in WEEK_ORDER:
        if not client.tab_exists(tab):
            continue
        client.set_tab_properties(tab, color=FAMILY_COLORS[family], index=index, hidden=False)
        index += 1
    results.append(f"tab strip: {index} tab(s) ordered by week phase and colour-coded")

    if hide_staging:
        hidden = 0
        for tab in HIDE_TABS:
            if not client.tab_exists(tab):
                continue
            client.set_tab_properties(tab, color=FAMILY_COLORS["feed"], index=index, hidden=True)
            index += 1
            hidden += 1
        results.append(f"staging: {hidden} tab(s) hidden (still fully writable by dfs sync)")

    return results


# ---------------------------------------------------------------------------
# Tab notes (Fix 3 + Fix 1.5): every visible tab explains itself
# ---------------------------------------------------------------------------

# A note, not a row -- nothing shifts, unlike Pool Picks' own title ROW
# (Fix 3.1, `sheet_pool_picks.py`), which needed a real structural change
# since it's a tab this codebase writes formulas into below row 1. Every
# other tab gets its explanation as an A1 cell note (Insert > Note)
# instead: pure metadata, safe on a typed cell (EdgeRaw's Pool header) or
# a computed one alike. Grounded in `docs/SHEET_REFERENCE.md`'s per-tab
# descriptions rather than invented here -- if a tab's behavior changes,
# update that doc's entry first and this text second, not the reverse.
_SORT_SEARCH_HINT = "Click any header arrow to sort or search. Saved views: Data > Filter views."
_SAVED_VIEW_HINT = "Sort/filter via Data > Filter views (this tab's own cells stay untouched)."

TAB_NOTES: dict[str, str] = {
    "Board": (
        "BOARD -- a read-only leverage/GameEnv snapshot across three ranked panels, "
        "rebuilt by `dfs setup build-views`. Nothing here is typed."
    ),
    "EdgeRaw": (
        "EDGERAW -- every synced player this week, sorted by Leverage. Type in the Pool "
        f"column (the checkbox on the far left) to add a player to your pool. {_SORT_SEARCH_HINT}"
    ),
    "Slate Grid": (
        "SLATE GRID -- one row per game: total, spread, wind, divisional flag. Read-only, "
        f"rebuilt by `dfs setup build-views`. {_SAVED_VIEW_HINT}"
    ),
    "Player Pool": (
        "PLAYER POOL -- everyone you've added, grouped by position. Row 1: type a name "
        "(with a search box) to add a player directly, the same as ticking Pool on "
        "EdgeRaw. Everything below row 2 is computed. Source says whether a row came "
        "from EdgeRaw or this row's own add box; Edge ↗ jumps straight to that player on "
        "EdgeRaw (e.g. to remove them -- untick Pool there); Overflow (far right) warns "
        "if a position has more picks than room."
    ),
    "Lineups": (
        "LINEUPS -- build your rosters here. Rows 1-9 are a sortable window into Player "
        "Pool (pick a position and sort field in row 1); type a player's name into column "
        "A of a lineup block below to fill a slot. Issues flags a duplicate, an "
        "unavailable player, or a salary/roster problem per lineup; Edge ↗ jumps straight "
        "to that player on EdgeRaw."
    ),
    "Scratch": (
        "SCRATCH -- a blank grid for your own notes or draft lineups. Nothing here is read by `dfs`."
    ),
    "DK Upload": (
        "DK UPLOAD -- `dfs export` writes DraftKings' bulk-upload file here. Read-only "
        "output; don't type into it."
    ),
    "Movement": (
        "MOVEMENT -- how betting lines have shifted since your last sync "
        f"(`dfs odds movement`). Read-only. {_SAVED_VIEW_HINT}"
    ),
    "Exposure": (
        "EXPOSURE -- how much of your lineups each player is in. Type a target percentage "
        f"into the Target column; everything else is computed. {_SAVED_VIEW_HINT}"
    ),
    "GPPin": "GPPIN -- a derived view of your pasted contest-entry history. Nothing here is typed.",
    "DKLineupsFinal": (
        "DKLINEUPSFINAL -- a derived view of your pasted contest-entry history. Nothing here is typed."
    ),
    "Bankroll": (
        "BANKROLL -- Cash/GPP ledgers plus starting/ending bankroll. `dfs bankroll sync "
        "--csv <file>` (or `dfs week close`) appends rows here; the summary figures are "
        "formulas, not typed."
    ),
    "Results": (
        "RESULTS -- a season-level results log, one row per week, NOT reset by a new "
        f"weekly copy. Type into every column except Cash Results/H2H %. {_SORT_SEARCH_HINT}"
    ),
    "SoSQB": (
        "SOSQB -- Strength-of-schedule for QBs, pasted in by hand each week. Rank is "
        f"colour-scaled REVERSED (low is the tough matchup). {_SORT_SEARCH_HINT}"
    ),
    "SoSRB": (
        "SOSRB -- Strength-of-schedule for RBs, pasted in by hand each week. Rank is "
        f"colour-scaled REVERSED (low is the tough matchup). {_SORT_SEARCH_HINT}"
    ),
    "SoSWr": (
        "SOSWR -- Strength-of-schedule for WRs, pasted in by hand each week. Rank is "
        f"colour-scaled REVERSED (low is the tough matchup). {_SORT_SEARCH_HINT}"
    ),
    "SoSTE": (
        "SOSTE -- Strength-of-schedule for TEs, pasted in by hand each week. Rank is "
        f"colour-scaled REVERSED (low is the tough matchup). {_SORT_SEARCH_HINT}"
    ),
    "SoSDef": (
        "SOSDEF -- Strength-of-schedule for DSTs, pasted in by hand each week. Rank is "
        f"colour-scaled REVERSED (low is the tough matchup). {_SORT_SEARCH_HINT}"
    ),
    "SoSComb": (
        "SOSCOMB -- combines all five SoS tabs into one lookup table by team and "
        f"position, feeding Player Pool's OppPosRank. {_SORT_SEARCH_HINT}"
    ),
    "Instructions": (
        "INSTRUCTIONS -- read this first. The weekly workflow lives here, row by row; "
        "`docs/` in the repo has the full reference for anything beyond it."
    ),
    "PlayerPoolRaw": (
        "PLAYERPOOLRAW -- the hub every other tab's VLOOKUPs read from. Left visible on "
        "purpose so a broken lookup is easier to debug; nothing here is typed."
    ),
}


def apply_tab_notes(client: SheetsClient, notes: dict[str, str] = TAB_NOTES) -> list[str]:
    """One explanatory note on A1 of every tab in `notes` -- see TAB_NOTES'
    own comment for why this is a note and not a row almost everywhere.
    Pure metadata (no cell value, formula or format touched), safe to call
    unconditionally as part of `dfs setup polish`."""
    results = []
    for tab, text in notes.items():
        if not client.tab_exists(tab):
            results.append(f"{tab}: not present -- skipped")
            continue
        client.set_note(tab, "A1", text)
        results.append(f"{tab}: A1 note set")
    return results


# ---------------------------------------------------------------------------
# The derived view tabs (Directions A, E, F, H)
# ---------------------------------------------------------------------------

# `sheet_views` writes these tabs' formulas; this styles them. Kept here
# rather than in that module so all presentation decisions live in one
# file -- and so the layout constants below sit next to the palette they
# use. The column positions ARE literal here, unlike everywhere else in
# this file, because these are tabs we author ourselves: their layout is
# defined by `sheet_views`, not discovered from the sheet. If you change a
# layout there, change it here.

_PANEL_FMT = {
    "backgroundColor": HEADER_BG,
    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 9},
    "horizontalAlignment": "LEFT",
}
_SUBHEAD_FMT = {
    "backgroundColor": FLAT_BG,
    "textFormat": {"bold": True, "foregroundColor": FLAT_FG, "fontSize": 9},
}
_BANNER_FMT = {
    "backgroundColor": WARN_BG,
    "textFormat": {"bold": True, "foregroundColor": WARN_FG, "fontSize": 10},
}

# Board panels: (first column, last column) for each of the three.
_BOARD_PANELS = [("A", "D"), ("F", "I"), ("K", "N")]


def style_board(client: SheetsClient, tab: str = "Board") -> str:
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    client.clear_conditional_formats(tab)
    client.set_column_widths(
        tab,
        {
            "A": 160,
            "B": 74,
            # Widened from 64 -- Phase 6, Part 1.3: row 2's "Highest total"
            # label clipped to "Highest tota" live. C doubles as panel 1's
            # narrower "Lev" body column (rows 7+), which this widening
            # affects too -- a minor, acceptable tradeoff (Part 3 rebuilds
            # this whole tab regardless), not worth a second, row-specific
            # width mechanism this codebase doesn't otherwise have.
            "C": 110,
            "D": 72,
            # Widened from 24 -- Phase 6, Part 1.3: row 2's "Max wind"
            # label clipped to "Max" live. E also doubles as the visual
            # spacer between panels 1 and 2 (rows 5+, see the spacer-fill
            # loop below) -- a wider gap there is a smaller cosmetic cost
            # than a clipped label, and again temporary (see C above).
            "E": 75,
            "F": 160,
            "G": 74,
            "H": 82,
            "I": 72,
            "J": 24,
            "K": 160,
            "L": 74,
            "M": 62,
            "N": 96,
        },
    )
    client.format_range(tab, "A1", _TITLE_FMT)
    client.format_range(tab, "A2:H2", {"textFormat": {"fontSize": 10}})
    for label in ("A2", "C2", "E2", "G2"):
        client.format_range(tab, label, {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9}})
    for value in ("B2", "D2", "F2", "H2"):
        client.format_range(tab, value, {"textFormat": {"bold": True, "foregroundColor": INK}})
    client.format_range(tab, "A3:N3", _BANNER_FMT)

    # A visible break between the three panels -- narrow columns E and J
    # were spacers already, but an unstyled spacer reads the same as a
    # panel column at a glance. A muted grey fill makes them read as a
    # deliberate rule instead.
    for spacer in ("E", "J"):
        client.format_range(tab, f"{spacer}5:{spacer}20", {"backgroundColor": FLAT_BG})

    for first, last in _BOARD_PANELS:
        client.format_range(tab, f"{first}5:{last}5", _PANEL_FMT)
        client.format_range(tab, f"{first}6:{last}6", _SUBHEAD_FMT)

    # Panel bodies: 12 rows for the two ranked panels, 14 for landmines. Same
    # FIELD_FORMATS entries the rest of the workbook uses for Pts/CeilVal/
    # Salary -- this tab's columns are literal (an authored view, not a
    # discovered header) but the format for a given field must still match.
    client.format_range(tab, "C7:C18", FIELD_FORMATS["Pts"])
    client.format_range(tab, "D7:D18", FIELD_FORMATS["CeilVal"])
    client.format_range(tab, "H7:H18", FIELD_FORMATS["Salary"])
    client.format_range(tab, "I7:I18", FIELD_FORMATS["CeilVal"])
    for rng in ("C7:C18", "D7:D18", "I7:I18"):
        client.add_color_scale(tab, rng, min_color=GRAD_MIN, mid_color=GRAD_MID, max_color=GRAD_MAX)
    for text, fmt in FLAG_CHIPS.items():
        client.add_boolean_rule(tab, "N7:N20", condition_type="TEXT_CONTAINS", values=[text], fmt=fmt)
    for text, fmt in AVAIL_CHIPS.items():
        client.add_boolean_rule(tab, "M7:M20", condition_type="TEXT_EQ", values=[text], fmt=fmt)
    client.freeze(tab, rows=6)
    return f"{tab}: styled (3 panels, banner, colour scales)"


def style_slate_grid(client: SheetsClient, tab: str = "Slate Grid") -> str:
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    client.clear_conditional_formats(tab)
    client.set_column_widths(
        tab,
        {
            "A": 140,
            "B": 116,
            "C": 64,
            "D": 72,
            "E": 84,
            "F": 72,
            "G": 72,
            "H": 84,
            "I": 52,
            "J": 160,
            # A9 (2026-09-22): per-game line movement, appended past the
            # original 10 columns -- same widths `style_movement` already
            # uses for these two header names.
            "K": 92,
            "L": 92,
        },
    )
    client.format_range(tab, "A1:L1", _HEADER_FMT)
    client.format_range(tab, "C2:C19", FIELD_FORMATS["Total"])
    client.format_range(tab, "D2:D19", FIELD_FORMATS["Spread"])
    client.format_range(tab, "F2:G19", FIELD_FORMATS["Wind"])
    client.format_range(tab, "I2:I19", {"horizontalAlignment": "CENTER"})
    client.add_color_scale(tab, "C2:C19", min_color=GRAD_MIN, mid_color=GRAD_MID, max_color=GRAD_MAX)
    client.add_boolean_rule(
        tab, "F2:G19", condition_type="NUMBER_GREATER", values=["15"], fmt=_chip(WARN_BG, WARN_FG)
    )
    client.add_boolean_rule(
        tab, "I2:I19", condition_type="TEXT_EQ", values=["DIV"], fmt=_chip(FLAT_BG, FLAT_FG)
    )
    # A9: same diverging-at-zero treatment `style_movement` gives these
    # exact two column names -- zero is the meaningful midpoint, not the
    # statistical median. Two separate rules, one per column (not one
    # rule spanning K:L), same reasoning `style_movement` already
    # documents: Total move and Spread move are different metrics and
    # each needs its own min/max, not a shared one across both.
    client.format_range(tab, "K2:L19", FIELD_FORMATS["ImpliedMove"])
    for col in ("K", "L"):
        client.add_color_scale(
            tab,
            f"{col}2:{col}19",
            min_color=GRAD_MIN,
            mid_color=WHITE,
            max_color=GRAD_MAX,
            mid_type="NUMBER",
            mid_value="0",
        )
    client.freeze(tab, rows=1, cols=1)
    return f"{tab}: styled (totals colour-scaled, high wind flagged, movement scaled)"


def style_exposure(client: SheetsClient, tab: str = "Exposure") -> str:
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    client.clear_conditional_formats(tab)
    client.set_column_widths(
        tab,
        {
            "A": 165,
            "B": 52,
            "C": 78,
            "D": 84,
            "E": 84,
            "F": 84,
            "G": 88,
            "H": 24,
            "I": 96,
            "J": 72,
            # Part 7.5's portfolio headline (K1:P1) -- label/value pairs,
            # same "no entry means Sheets' own 100px default" gap this
            # whole function exists to avoid for A-J. "Distinct games"
            # (14 characters) is the widest label and needs more than
            # the 100px default to avoid truncating.
            "K": 96,
            "L": 40,
            "M": 84,
            "N": 40,
            "O": 110,
            "P": 40,
        },
    )
    client.format_range(tab, "A1:G1", _HEADER_FMT)
    client.format_range(tab, "I1", {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9}})
    # Part 7.5's portfolio headline labels -- same muted treatment as I1
    # ("Slots filled") above, not the dark spine header style, since
    # these are a second, unrelated row-1 label/value strip.
    client.format_range(tab, "K1", {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9}})
    client.format_range(tab, "M1", {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9}})
    client.format_range(tab, "O1", {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9}})
    client.format_range(tab, "C2:C180", FIELD_FORMATS["Salary"])
    client.format_range(tab, "D2:D180", _num("0"))
    client.format_range(tab, "E2:E180", FIELD_FORMATS["Exposure"])
    client.format_range(tab, "F2:F180", FIELD_FORMATS["Target"])
    client.format_range(tab, "G2:G180", FIELD_FORMATS["vs Target"])
    # Target is the one typed column in the whole workbook that this
    # module touches -- mark it as input rather than output.
    client.format_range(tab, "F2:F180", {"backgroundColor": INPUT_BG, "textFormat": {"italic": True}})
    client.add_color_scale(tab, "E2:E180", min_color=GRAD_MAX, mid_color=GRAD_MID, max_color=GRAD_MIN)
    client.add_boolean_rule(
        tab, "G2:G180", condition_type="NUMBER_GREATER", values=["0"], fmt=_chip(CRIT_BG, CRIT_FG)
    )
    client.add_boolean_rule(
        tab, "G2:G180", condition_type="NUMBER_LESS", values=["0"], fmt=_chip(WARN_BG, WARN_FG)
    )
    client.freeze(tab, rows=1, cols=1)
    return f"{tab}: styled (Target marked as input, over/under target flagged)"


_MOVEMENT_WIDTHS = {
    "Player": 165,
    "Pos": 92,
    # Widened from 92 -- Phase 6, Part 1.5's own audit-style check caught
    # this one truncating live ("Implied move" is longer than "Total
    # move"/"Spread move", the only one of the three that didn't fit).
    "Implied move": 110,
    "Total move": 92,
    "Spread move": 92,
    "Kickoff (UTC)": 152,
    "Flags": 96,
}

# Diverging, not the standard red->yellow->green: each is a signed delta
# and zero (no movement) is the meaningful midpoint, same reasoning as
# EdgeRaw's own ImpliedMove/TotMove/SpdMove columns (see polish_edge).
_MOVEMENT_SCALED_COLUMNS = ("Implied move", "Total move", "Spread move")


def style_movement(client: SheetsClient, tab: str = "Movement") -> str:
    """Section F: `build_movement`'s header can now be anywhere from 4 to
    7 columns wide (Total move/Spread move/Kickoff are each included only
    when EdgeRaw/GameStart actually have them) -- styling by a hardcoded
    A:E range assumed the old fixed 5-column shape and silently
    mis-styled (or under-styled) the tab the moment that shape changed.
    Read row 3's real header instead and match every styled column by
    NAME, same discipline as `polish_builder_tab`.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, "A3:3")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty header row -- skipped"

    last_col = column_letter(len(header) - 1)
    client.clear_conditional_formats(tab)
    widths = {
        column_letter(i): _MOVEMENT_WIDTHS[name] for i, name in enumerate(header) if name in _MOVEMENT_WIDTHS
    }
    client.set_column_widths(tab, widths)
    client.format_range(tab, "A1", _TITLE_FMT)
    client.format_range(tab, f"A3:{last_col}3", _HEADER_FMT)

    scaled = 0
    for name in _MOVEMENT_SCALED_COLUMNS:
        if name not in header:
            continue
        letter = column_letter(header.index(name))
        rng = f"{letter}4:{letter}60"
        client.format_range(tab, rng, FIELD_FORMATS["ImpliedMove"])
        client.add_color_scale(
            tab,
            rng,
            min_color=GRAD_MIN,
            mid_color=WHITE,
            max_color=GRAD_MAX,
            mid_type="NUMBER",
            mid_value="0",
        )
        scaled += 1

    if "Flags" in header:
        letter = column_letter(header.index("Flags"))
        rng = f"{letter}4:{letter}60"
        for text, fmt in FLAG_CHIPS.items():
            client.add_boolean_rule(tab, rng, condition_type="TEXT_CONTAINS", values=[text], fmt=fmt)

    client.freeze(tab, rows=3, cols=1)
    return f"{tab}: styled ({scaled} movement column(s) colour-scaled, flags chipped)"


def style_view_tabs(client: SheetsClient) -> list[str]:
    """Style whichever of the four derived tabs exist. Each is skipped
    cleanly if `dfs setup build-views` hasn't created it yet, so `polish`
    is safe to run on a sheet that has none of them."""
    return [
        style_board(client),
        style_slate_grid(client),
        style_exposure(client),
        style_movement(client),
    ]


# ---------------------------------------------------------------------------
# Tier 2/3 (Task 2.9): tabs that have never been styled at all
# ---------------------------------------------------------------------------

# Distinct from Sheets' own 100px column default (see
# `SheetsClient.get_column_widths`'s docstring) so `dfs setup audit-style`
# recognizes a column here as deliberately set, not left untouched.
_GENERIC_COLUMN_PX = 110


def style_flat_tab(client: SheetsClient, tab: str, *, last_row: int, header_row: int = 1) -> str:
    """The standard treatment -- dark header, frozen pane, a width on
    every column, FIELD_FORMATS wherever a header matches -- for a tab
    that has otherwise never been styled: Scratch, DK Upload,
    DKLineupsFinal, SoSComb. Header-driven like `polish_builder_tab`, but
    without that function's Name-pin/Flag-Avail-chip assumptions, which
    don't apply to any of these (none have a Name, Flag or Avail column).
    Skips cleanly on an empty header -- Scratch/DK Upload/DKLineupsFinal
    always have one (their header is a fixed roster-slot or DK-export
    label row), but SoSComb is hand-built and could be blank before Sam
    has set it up for the week.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty header row -- skipped"

    last_col = column_letter(len(header) - 1)
    client.format_range(tab, f"A{header_row}:{last_col}{header_row}", _HEADER_FMT)
    client.freeze(tab, rows=header_row)

    widths = {column_letter(i): BUILDER_WIDTHS.get(name, _GENERIC_COLUMN_PX) for i, name in enumerate(header)}
    client.set_column_widths(tab, widths)

    applied = apply_field_formats(client, tab, header, header_row=header_row, last_row=last_row)
    return f"{tab}: header styled, frozen, widths set, {applied} column(s) number-formatted"


def style_results(client: SheetsClient, tab: str = "Results", *, last_row: int) -> str:
    """Results is a season-long log with real win/loss and H2H data and
    had no header fill, no freeze, and no conditional formatting at all --
    the standard treatment plus the two columns worth a glance at rather
    than a read: `Cash Results` (TRUE/FALSE) chipped green/red, `H2H %`
    colour-scaled like every other percentage-of-success metric in the
    workbook. Column layout is `week.py`'s own documented one (Week,
    Cash Pts, Cash Line, Cash Results, H2H Entered, H2H Win, H2H %, Red,
    Blue, Black) but found by header name here too, not assumed.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, "A1:1")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty header row -- skipped"

    last_col = column_letter(len(header) - 1)
    client.clear_conditional_formats(tab)
    client.format_range(tab, f"A1:{last_col}1", _HEADER_FMT)
    client.freeze(tab, rows=1)

    widths = {column_letter(i): BUILDER_WIDTHS.get(name, _GENERIC_COLUMN_PX) for i, name in enumerate(header)}
    client.set_column_widths(tab, widths)

    applied = apply_field_formats(client, tab, header, header_row=1, last_row=last_row)

    if "Cash Results" in header:
        letter = column_letter(header.index("Cash Results"))
        a1 = f"{letter}2:{letter}{last_row}"
        client.add_boolean_rule(tab, a1, condition_type="TEXT_EQ", values=["TRUE"], fmt=_chip(OK_BG, OK_FG))
        client.add_boolean_rule(
            tab, a1, condition_type="TEXT_EQ", values=["FALSE"], fmt=_chip(CRIT_BG, CRIT_FG)
        )
    if "H2H %" in header:
        letter = column_letter(header.index("H2H %"))
        client.add_color_scale(
            tab, f"{letter}2:{letter}{last_row}", min_color=GRAD_MIN, mid_color=GRAD_MID, max_color=GRAD_MAX
        )

    return f"{tab}: header styled, frozen, {applied} column(s) number-formatted, Cash Results/H2H % coloured"


# The number of NFL teams -- a hard upper bound on how many data rows a
# hand-pasted Strength-of-Schedule tab can ever have, used only to bound
# how far down formatting is applied (harmless past the real data, same
# reasoning as EDGE_ROWS/POOL_RAW_ROWS above).
_SOS_MAX_ROWS = 32


def style_sos_tab(client: SheetsClient, tab: str) -> str:
    """SoSQB/SoSRB/SoSWr/SoSTE/SoSDef: hand-pasted Strength-of-Schedule
    data, current week only (see the Instructions tab). Standard header/
    freeze/widths, plus a colour scale on `Rank` -- REVERSED from every
    other rank/value column in the workbook, since here a LOW rank is
    the good matchup (an easy upcoming schedule), not a high one.
    Skips cleanly if nothing's been pasted yet this week -- these are
    entirely hand-built, not written by any `dfs` command, so an empty
    header is the normal state between weeks, not a bug.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, "A1:1")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty -- nothing pasted yet this week, skipped"

    last_col = column_letter(len(header) - 1)
    client.clear_conditional_formats(tab)
    client.format_range(tab, f"A1:{last_col}1", _HEADER_FMT)
    client.freeze(tab, rows=1)

    widths = {column_letter(i): BUILDER_WIDTHS.get(name, _GENERIC_COLUMN_PX) for i, name in enumerate(header)}
    client.set_column_widths(tab, widths)

    scaled = False
    if "Rank" in header:
        letter = column_letter(header.index("Rank"))
        # Reversed: max colour (green) at the MIN end, min colour (red)
        # at the MAX end -- a low rank is the good matchup here.
        client.add_color_scale(
            tab,
            f"{letter}2:{letter}{_SOS_MAX_ROWS + 1}",
            min_color=GRAD_MAX,
            mid_color=GRAD_MID,
            max_color=GRAD_MIN,
        )
        scaled = True

    return f"{tab}: header styled, frozen, widths set{', Rank scaled (reversed)' if scaled else ''}"


def style_tier23_tabs(
    client: SheetsClient,
    *,
    scratch_last_row: int,
    dk_upload_last_row: int,
    dk_lineups_final_last_row: int,
    results_last_row: int,
    sos_comb_last_row: int,
) -> list[str]:
    """Every Tier 2/3 tab in one call, each skipped cleanly if the tab
    doesn't exist or (for the hand-pasted SoS tabs) is currently empty."""
    results = [
        style_flat_tab(client, "Scratch", last_row=scratch_last_row),
        style_flat_tab(client, "DK Upload", last_row=dk_upload_last_row),
        style_flat_tab(client, "DKLineupsFinal", last_row=dk_lineups_final_last_row),
        style_results(client, last_row=results_last_row),
    ]
    for tab in ("SoSQB", "SoSRB", "SoSWr", "SoSTE", "SoSDef"):
        results.append(style_sos_tab(client, tab))
    results.append(style_flat_tab(client, "SoSComb", last_row=sos_comb_last_row))
    return results
