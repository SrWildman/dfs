"""Round 5 item 4: subtle correlation tints on Lineups' identity cells
(Name, Pos., Team), replacing the removed `Stack`/`Bring-back` text columns.

    Stack       soft blue      the lineup's QB, and every non-DST player on the QB's team
    Bring-back  soft amber     every non-DST player on the QB's opponent
    Other       soft lavender  any OTHER game with 2+ non-DST players in the lineup, all of them

No QB in the lineup means no stack/bring-back tint; the lavender rule still
applies. DSTs never get a tint (DST vs your own QB is already an `Issues`
guardrail). The numeric columns are left alone so their colour scales still
read.

One custom-formula rule per tint per lineup block. Blocks are fixed row
ranges (`weekly_reset.LINEUPS_NAME_BLOCKS`), and every reference inside a
rule is to that block's own rows, so a rule can never read another lineup.
The three conditions are mutually exclusive by construction (stack: team ==
QB team; bring-back: team == QB opponent; other: game != QB game), so rule
priority never decides a cell's colour.

The rules are added BEFORE the guardrail/typo-guard rules in the polish run
(added later = higher priority) so a red warning on a name cell still beats
a tint.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient, column_letter

# Soft tints -- deliberately paler than the position tints and colour scales.
STACK_TINT = {"red": 0.86, "green": 0.91, "blue": 0.98}  # soft blue
BRING_BACK_TINT = {"red": 0.99, "green": 0.92, "blue": 0.78}  # soft amber
OTHER_TINT = {"red": 0.91, "green": 0.88, "blue": 0.96}  # soft lavender

LEGEND = (
    "Cell tints on Name/Pos./Team: blue = your QB and his teammates (the stack); amber = "
    "the QB's opponent (bring-back); lavender = 2+ players from any other game. "
    "DSTs are never tinted."
)


def _lookup(col: str, start: int, end: int, pos_col: str) -> str:
    """The QB row's value in `col` (blank when the block has no QB yet)."""
    return f'IFERROR(INDEX(${col}${start}:${col}${end},MATCH("QB",${pos_col}${start}:${pos_col}${end},0)),"")'


def tint_formulas(
    start: int, end: int, *, name_col: str, pos_col: str, team_col: str, opp_col: str, gameid_col: str
) -> dict[str, str]:
    """`{"stack", "bring_back", "other"}` -> CUSTOM_FORMULA text, written
    for the block's top-left cell (`start`) with row-relative references, so
    it follows every row of `A{start}:C{end}`."""
    filled = f'${name_col}{start}<>"",${pos_col}{start}<>"DST"'
    qb_team = _lookup(team_col, start, end, pos_col)
    qb_opp = _lookup(opp_col, start, end, pos_col)
    qb_game = _lookup(gameid_col, start, end, pos_col)
    game = f"${gameid_col}{start}"
    pos_rng = f"${pos_col}${start}:${pos_col}${end}"
    name_rng = f"${name_col}${start}:${name_col}${end}"
    game_rng = f"${gameid_col}${start}:${gameid_col}${end}"
    return {
        "stack": f'=AND({filled},{qb_team}<>"",${team_col}{start}={qb_team})',
        "bring_back": f'=AND({filled},{qb_opp}<>"",${team_col}{start}={qb_opp})',
        "other": (
            f'=AND({filled},{game}<>"",{game}<>{qb_game},'
            f'COUNTIFS({game_rng},{game},{pos_rng},"<>DST",{name_rng},"<>")>=2)'
        ),
    }


def apply_lineup_tints(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]]
) -> str:
    """Adds the three tint rules to every lineup block's identity cells
    (columns Name..Team). Columns found by header text, never by letter.
    Not idempotent by itself -- called from the polish run, which clears the
    tab's conditional formats first."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    needed = ["Name", "Pos.", "Team", "Opp.", "GameID"]
    missing = [n for n in needed if n not in header]
    if missing:
        return f"{tab}: column(s) {missing} not found -- correlation tints skipped"
    cols = {n: column_letter(header.index(n)) for n in needed}
    first, last = cols["Name"], cols["Team"]

    specs: list[dict] = []
    for start, end in name_blocks:
        formulas = tint_formulas(
            start,
            end,
            name_col=cols["Name"],
            pos_col=cols["Pos."],
            team_col=cols["Team"],
            opp_col=cols["Opp."],
            gameid_col=cols["GameID"],
        )
        for key, tint in (("stack", STACK_TINT), ("bring_back", BRING_BACK_TINT), ("other", OTHER_TINT)):
            specs.append(
                {
                    "a1_range": f"{first}{start}:{last}{end}",
                    "condition_type": "CUSTOM_FORMULA",
                    "values": [formulas[key]],
                    "fmt": {"backgroundColor": tint},
                }
            )
    client.add_boolean_rules(tab, specs)
    return f"{tab}: correlation tints added ({len(specs)} rule(s) over {len(name_blocks)} lineup block(s))"
