"""Round 5, item 7: a hand-entered Betting ledger on the Bankroll tab, plus
the weekly-summary and Ending-balance wiring that folds it into the one
total bankroll alongside Cash and GPP.

The Cash/GPP ledgers are hand-built (this codebase has never generated
the Bankroll tab itself -- see `bankroll.py`'s own module docstring, which
only ever reads/writes into an already-existing ledger's data columns).
This module is the first thing that actually BUILDS Bankroll structure,
so it stays narrow: it inserts one new block (the Betting ledger) above
the Cash ledger and edits exactly two existing cells (the Weekly Net
rollup and, implicitly via Sheets' own insertDimension range-shifting,
nothing else). Cash/GPP's own formulas are never rewritten by hand here --
`SheetsClient.insert_rows` performs a real `insertDimension`, which Sheets
auto-adjusts every RANGE reference to (see CLAUDE.md's "derive positions,
never hardcode" hazard) -- confirmed live, not assumed: Cash/GPP's ranges
shifted correctly with no code change needed.

Layout decided 2026-09-28 (Sam, PROMPT_ROUND5 item 7 checkpoint): Betting
sits ABOVE Cash (not below GPP as the prompt originally sketched) so GPP
-- confirmed growable, and already the bottom-most block on the tab --
can be extended downward by inserting more rows without ever having to
move Betting or Cash out of the way again.

Real, corrected row numbers (found live, both sheets, before this change):
Cash's `% Paid`/`Place %` formula pattern actually runs rows 17-61 (not
1-59 as `config.toml`/`config.example.toml` said), and GPP's actually runs
64-127 (not 64-149). Both configs were stale before this landed; this
module's constants below are BEFORE-insert positions (i.e. today's real
layout), and the actual post-insert Cash/GPP config values (see
`CONTRIBUTING.md`'s changelog row for this change) are these plus
`BETTING_BLOCK_ROWS`.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.sheet_empty_guards import guard_formula
from dfs.sheet_style import CRIT_FG, INK_MUTED, OK_FG
from dfs.sheets import SheetsClient

BETTING_LEDGER_HEADER = ["Name", "Odds %", "Odds", "Entered", "Won", "Net"]

# Where the new block goes, in TODAY's (pre-insert) row numbers -- an
# `insertDimension` at `INSERT_AT_ROW` pushes the existing Cash header (and
# everything below it) down by `BLOCK_ROWS`.
INSERT_AT_ROW = 16
SUMMARY_ROW = 14  # already-blank row above the insert point -- no shift needed
# The existing weekly Cash/GPP summary rows sit right above it (row 14 mirrors them).
# Season reads its Cash/GPP net and cost from these (Cost = D, Net = H), so a figure
# Sam adjusts by hand -- e.g. zeroing a promo entry's fee -- is what Season shows.
CASH_SUMMARY_ROW = SUMMARY_ROW - 2
GPP_SUMMARY_ROW = SUMMARY_ROW - 1
SUMMARY_COST_COLUMN = "D"
SUMMARY_NET_COLUMN = "H"
NOTE_ROW = 15  # already-blank row above the insert point -- no shift needed
HEADER_ROW = 16
FIRST_ROW = 17
LAST_ROW = 36  # 20 data rows (Sam, 2026-09-28: "max 20 spots for bets")
BLANK_SEPARATOR_ROW = 37
BLOCK_ROWS = BLANK_SEPARATOR_ROW - INSERT_AT_ROW + 1  # 22: header + 20 rows + 1 separator

# `insert_rows`'s dark-header-bleed (see _DATA_TEXT_FMT's own comment)
# isn't limited to A:F -- it covers the FULL width of whatever the row it
# inherits from spans, which for the Cash header is A:J (its own 10 real
# columns, `% Paid`/`Place %` included). Found live: G:J stayed dark on
# every inserted row (header, data, and the separator) because only A:F
# was ever explicitly reset. G:J has no meaning for the Betting block, so
# this is reset to plain across the whole inserted range, not populated.
_BLEED_COLUMNS = ("G", "H", "I", "J")

# Column L is Sam's own hidden dedupe-key column (Cash/GPP's
# `entry_key_column`, hidden tab-wide) -- anything placed there is
# invisible everywhere on this tab, not just on Cash/GPP's own rows. The
# weekly summary row therefore skips K (spacer, matching row 1's own
# blank-column-between-groups convention) and L entirely, landing its
# last pair on M/N instead.

# Real (not `config.toml`'s stale) pre-insert extent of the existing
# ledgers -- see module docstring. Post-insert, add BLOCK_ROWS to each.
CASH_HEADER_ROW_BEFORE = 16
CASH_LAST_ROW_BEFORE = 61
GPP_HEADER_ROW_BEFORE = 63
GPP_LAST_ROW_BEFORE = 127

_WHITE = {"red": 1, "green": 1, "blue": 1}
_BLACK = {"red": 0, "green": 0, "blue": 0}

_HEADER_FMT = {
    "backgroundColor": {"red": 0.1254902, "green": 0.14901961, "blue": 0.18431373},
    "horizontalAlignment": "LEFT",
    "verticalAlignment": "MIDDLE",
    "textFormat": {"foregroundColor": _WHITE, "fontSize": 10, "bold": True},
}
# `insert_rows` inherits the row-after's format (the Cash header's dark
# fill/white bold text) for every newly inserted row -- these explicitly
# reset backgroundColor/bold/foregroundColor rather than omitting them,
# since `SheetsClient.format_range`'s fields mask only touches keys
# actually present in the dict and leaves everything else (i.e. the
# inherited dark header look) exactly as inherited.
_DATA_TEXT_FMT = {
    "backgroundColor": _WHITE,
    "horizontalAlignment": "LEFT",
    "textFormat": {"fontFamily": "Calibri", "fontSize": 10, "bold": False, "foregroundColor": _BLACK},
}
_DATA_NUM_FMT = {
    "backgroundColor": _WHITE,
    "horizontalAlignment": "RIGHT",
    "textFormat": {"fontFamily": "Calibri", "fontSize": 10, "bold": False, "foregroundColor": _BLACK},
}
_RESET_FMT = {
    "backgroundColor": _WHITE,
    "textFormat": {"bold": False, "foregroundColor": _BLACK},
}
# A literal "%" suffix, NOT a true PERCENT-type format -- Sheets' percent
# format auto-divides typed input by 100 (so "0.533" would become 0.533%,
# not 53.3%), which would break two of the three accepted input styles
# ("53.3" and "0.533" -- see `odds_formula`'s own docstring). Quoting the
# `%` makes it a literal character with no math attached, so the stored
# value (and every formula that reads it) is unaffected by this format.
_ODDS_PCT_NUMBER_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": '0.0"%"'}}
_ODDS_NUMBER_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "+0;-0"}}
_CURRENCY_FORMAT = {"numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}}
_NOTE_FMT = {"textFormat": {"italic": True, "foregroundColor": INK_MUTED, "fontSize": 9}}

# Matches row 12/13's own Weekly Cash %/GPP % label style exactly (read
# live from the sheet) -- the weekly summary row must look like it
# belongs to the same block, not like a separately-styled addition.
_SUMMARY_LABEL_FMT = {
    "horizontalAlignment": "LEFT",
    "textFormat": {
        "foregroundColor": {"red": 0.4745098, "green": 0.5176471, "blue": 0.5803922},
        "fontSize": 9,
        "bold": False,
    },
}
_SUMMARY_VALUE_FMT = {"horizontalAlignment": "RIGHT", "textFormat": {"bold": False}}

NOTE_TEXT = "Won blank = bet still pending. Pending bets are excluded from every total below."


def odds_formula(row: int) -> str:
    """American odds from Odds % (`$B{row}`), accepting 53.3, 53.3%, or
    0.533 the same way (`IF(x>1, x/100, x)`). Verified against the spec's
    five worked examples: 53.3% -> -114, 50% -> +100, 40% -> +150,
    75% -> -300, 20% -> +400."""
    return (
        f'=IF($B{row}="","",LET(p,IF($B{row}>1,$B{row}/100,$B{row}),'
        f"IF(p>0.5,-ROUND(100*p/(1-p),0),IF(p<0.5,ROUND(100*(1-p)/p,0),100))))"
    )


def net_formula(row: int) -> str:
    """Won minus Entered, blank while pending (Won blank)."""
    return f'=IF($E{row}="","",$E{row}-$D{row})'


def _settled_range(first_row: int, last_row: int, col: str) -> str:
    return f"${col}${first_row}:${col}${last_row}"


def wins_formula(first_row: int, last_row: int) -> str:
    e, d = _settled_range(first_row, last_row, "E"), _settled_range(first_row, last_row, "D")
    return f'SUMPRODUCT(({e}<>"")*({e}>{d}))'


def losses_formula(first_row: int, last_row: int) -> str:
    """Won < Entered (a normal loss), OR a $0-entered promo/free bet that
    paid out $0 -- Sam, 2026-09-28: "loss, but no money lost." A push
    stays a push only when a REAL stake (Entered > 0) comes back exactly
    even; a $0-entered bet can never satisfy `Won < Entered` (no negative
    payout), so it needs its own clause here rather than falling out of
    `wins`/`pushes` by exclusion."""
    e, d = _settled_range(first_row, last_row, "E"), _settled_range(first_row, last_row, "D")
    return f'SUMPRODUCT(({e}<>"")*(({e}<{d})+(({d}=0)*({e}=0))))'


def pushes_formula(first_row: int, last_row: int) -> str:
    """Won = Entered, but only when Entered > 0 -- a real stake returned
    even. See `losses_formula`'s own docstring for the $0-entered case."""
    e, d = _settled_range(first_row, last_row, "E"), _settled_range(first_row, last_row, "D")
    return f'SUMPRODUCT(({e}<>"")*({d}>0)*({e}={d}))'


def record_formula(first_row: int, last_row: int) -> str:
    w, losses, p = (
        wins_formula(first_row, last_row),
        losses_formula(first_row, last_row),
        pushes_formula(first_row, last_row),
    )
    return f'={w}&"-"&{losses}&"-"&{p}'


def entered_formula(first_row: int, last_row: int) -> str:
    """Total staked on SETTLED bets only -- pending stakes are excluded
    from every total, same as Net (item 7's own decision). This is
    "Weekly Betting Cost," matching Cash/GPP's own D12/D13 pattern
    (`SUM` of their ledger's Entry Fee column)."""
    e, d = _settled_range(first_row, last_row, "E"), _settled_range(first_row, last_row, "D")
    return f'=SUMPRODUCT(({e}<>"")*{d})'


def winnings_formula(first_row: int, last_row: int) -> str:
    """ "Weekly Betting Winnings," matching Cash/GPP's own F12/F13 pattern
    (`SUM` of their ledger's Winnings column) -- plain `SUM` already skips
    blank (pending) cells, no `SUMPRODUCT` guard needed."""
    e = _settled_range(first_row, last_row, "E")
    return f"=SUM({e})"


def build_betting_ledger(client: SheetsClient, tab: str) -> None:
    """One-time structural build: insert the Betting block above Cash,
    write its header/formulas/formatting, add the weekly summary row and
    note, and widen the existing Weekly Net rollup (B9) to include it.
    Idempotent-unsafe by design (like `sheet_instructions.build_instructions_tab`'s
    row inserts) -- running this twice would insert the block twice.
    Callers must check first (see `dfs bankroll build-betting-ledger`)."""
    client.insert_rows(tab, at_row=INSERT_AT_ROW, count=BLOCK_ROWS)

    client.update_range(tab, f"A{HEADER_ROW}:F{HEADER_ROW}", [BETTING_LEDGER_HEADER])
    client.format_range(tab, f"A{HEADER_ROW}:F{HEADER_ROW}", _HEADER_FMT)

    odds_rows = [[odds_formula(r)] for r in range(FIRST_ROW, LAST_ROW + 1)]
    net_rows = [[net_formula(r)] for r in range(FIRST_ROW, LAST_ROW + 1)]
    client.update_range(tab, f"C{FIRST_ROW}:C{LAST_ROW}", odds_rows)
    client.update_range(tab, f"F{FIRST_ROW}:F{LAST_ROW}", net_rows)

    client.format_range(tab, f"A{FIRST_ROW}:A{LAST_ROW}", _DATA_TEXT_FMT)
    for col, extra in (
        ("B", {}),
        ("C", _ODDS_NUMBER_FORMAT),
        ("D", _CURRENCY_FORMAT),
        ("E", _CURRENCY_FORMAT),
        ("F", _CURRENCY_FORMAT),
    ):
        client.format_range(tab, f"{col}{FIRST_ROW}:{col}{LAST_ROW}", {**_DATA_NUM_FMT, **extra})
    client.format_range(tab, f"A{BLANK_SEPARATOR_ROW}:F{BLANK_SEPARATOR_ROW}", _RESET_FMT)

    # See `_BLEED_COLUMNS`' own comment -- G:J inherited the Cash header's
    # dark fill on every row `insert_rows` touched (header, data, AND the
    # separator), not just A:F.
    for col in _BLEED_COLUMNS:
        client.format_range(tab, f"{col}{HEADER_ROW}:{col}{BLANK_SEPARATOR_ROW}", _RESET_FMT)

    # Sam, 2026-09-28: "I want to see the same metrics that I have for
    # cash nd gpp. Weekly %, cost, winnings, nnet." -- A-H now mirror row
    # 12/13's own shape and formula pattern EXACTLY (B14=D14/B7,
    # H14=F14-D14), not a separately-invented shape. Record is the one
    # addition, in the columns right after ("in the rightmost column
    # aafter the ones tha tmatch"); ROI and expected-vs-actual, which
    # Cash/GPP don't have either, are dropped.
    client.update_range(
        tab,
        f"A{SUMMARY_ROW}:J{SUMMARY_ROW}",
        [
            [
                "Weekly Betting %",
                guard_formula(f"=D{SUMMARY_ROW}/B7"),
                "Weekly Betting Cost",
                entered_formula(FIRST_ROW, LAST_ROW),
                "Weekly Betting Winnings",
                winnings_formula(FIRST_ROW, LAST_ROW),
                "Weekly Betting Net",
                f"=F{SUMMARY_ROW}-D{SUMMARY_ROW}",
                "Weekly Record (W-L-P)",
                record_formula(FIRST_ROW, LAST_ROW),
            ]
        ],
    )

    # Match row 12/13's own Weekly Cash %/GPP % styling exactly (labels
    # muted gray, fontSize 9, not bold) -- row 14 was blank but not
    # UNFORMATTED before this (confirmed live: default text landed bold,
    # and B14 specifically inherited a PERCENT format from the column
    # above it, rendering a plain result as "0%"). Every cell this row
    # touches gets its format set explicitly rather than assuming blank
    # content means blank formatting.
    for col in ("A", "C", "E", "G", "I"):
        client.format_range(tab, f"{col}{SUMMARY_ROW}", _SUMMARY_LABEL_FMT)
    client.format_range(
        tab, f"B{SUMMARY_ROW}", {**_SUMMARY_VALUE_FMT, "numberFormat": {"type": "PERCENT", "pattern": "0.0%"}}
    )
    # D12/D13 (Cash/GPP Cost) carried the SAME muted label style as their
    # own labels (foregroundColor/fontSize9) -- a pre-existing copy-paste
    # artifact in Sam's own rows that F12/H12 (Winnings/Net) never had.
    # Row 14 matched it at first (D14 should look like D12/D13), but Sam
    # caught the Cost-vs-Winnings size/colour mismatch by eye and wants it
    # actually fixed, not propagated -- D12/D13 themselves were corrected
    # to plain default (matching F/H) as part of this, live and template.
    for col in ("D", "F", "H"):
        client.format_range(
            tab,
            f"{col}{SUMMARY_ROW}",
            {**_SUMMARY_VALUE_FMT, "numberFormat": {"type": "CURRENCY", "pattern": "$#,##0.00"}},
        )
    client.format_range(tab, f"J{SUMMARY_ROW}", {**_SUMMARY_VALUE_FMT, "numberFormat": {"type": "TEXT"}})

    # Net colored green/red exactly like Cash/GPP's H12:H13 (polish_bankroll).
    for condition, color in (("NUMBER_GREATER", OK_FG), ("NUMBER_LESS", CRIT_FG)):
        client.add_boolean_rule(
            tab,
            f"H{SUMMARY_ROW}",
            condition_type=condition,
            values=["0"],
            fmt={"textFormat": {"foregroundColor": color, "bold": True}},
        )

    # Column I ("Weekly Record (W-L-P)") is the first content this tab has
    # ever had past H -- its default width (51px) clips the label against
    # J's non-blank content.
    client.set_column_widths(tab, {"I": 130})

    # Fold Betting Cost (D14) into the existing Weekly Cost rollup (B7),
    # the denominator every "Weekly ... %" cell on this row divides by --
    # otherwise "Weekly Betting %" is #DIV/0! on any week with no Cash/GPP
    # activity yet, and Cash/GPP's own % readings would keep excluding a
    # real category of spend. Same reasoning as widening B9 (below) for
    # Ending Bankroll.
    client.update_range(tab, "B7", [["=SUM(D12:D14)"]])

    client.update_range(tab, f"A{NOTE_ROW}", [[NOTE_TEXT]])
    client.format_range(tab, f"A{NOTE_ROW}", _NOTE_FMT)

    # Fold Betting Net (H14) into the existing Weekly Net rollup, which
    # B2 (Ending Bankroll = B1 + B9) already reads -- the only edit to an
    # existing formula this whole block makes.
    client.update_range(tab, "B9", [["=SUM(H12:H14)"]])


@dataclass
class WeeklyBettingStats:
    settled: int
    wins: int
    losses: int
    pushes: int
    entered: float
    net: float
    expected_wins: float


def compute_weekly_betting_stats(
    rows: list[tuple[float | None, float | None, float | None]],
) -> WeeklyBettingStats:
    """A pure-Python re-derivation of the same aggregate row 14's Sheets
    formulas compute, over raw `(odds_pct, entered, won)` tuples read back
    from the ledger -- used at `dfs week close` time (see `cli.py`) to get
    NUMBERS for the Season tab's Betting columns, since row 14 itself only
    ever holds formatted text (`"1-1-1"`, `"1.5 vs 1"`) that can't be
    summed across weeks on a season tab. `won is None` (or `""`) means
    pending, excluded from every total -- same convention as `net_formula`
    and the weekly summary formulas."""
    settled = wins = losses = pushes = 0
    entered = net = expected = 0.0
    for odds_pct, ent, won in rows:
        if won is None or won == "":
            continue
        ent = ent or 0.0
        settled += 1
        entered += ent
        net += won - ent
        if won > ent:
            wins += 1
        elif ent == 0 and won == 0:
            # A $0-entered promo/free bet that paid out $0 -- "loss, but
            # no money lost" (Sam, 2026-09-28), not a push. See
            # `losses_formula`'s own docstring for why this needs its own
            # branch rather than falling out of `won < ent`.
            losses += 1
        elif won < ent:
            losses += 1
        else:
            pushes += 1
        if odds_pct not in (None, ""):
            p = odds_pct / 100 if odds_pct > 1 else odds_pct
            expected += p
    return WeeklyBettingStats(
        settled=settled,
        wins=wins,
        losses=losses,
        pushes=pushes,
        entered=entered,
        net=net,
        expected_wins=expected,
    )
