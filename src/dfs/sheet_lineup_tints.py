"""Round 5 item 4: subtle correlation tints on Lineups' identity cells
(Name, Pos., Team), replacing the removed `Stack`/`Bring-back` text columns.
Week 5 extended them to the DST.

    Stack       soft blue      the lineup's QB, every player on the QB's team, and that team's DST
    Bring-back  soft amber     every non-DST player on the QB's opponent
    Other       soft lavender  any OTHER game with 2+ non-DST players in the lineup, all of them; and a
                               DST together with every non-DST player from its own team (a team that
                               wins comfortably feeds its RB and its defense)

No QB in the lineup means no stack/bring-back tint; the lavender rule still applies.

A DST that faces one of your players is a NEGATIVE correlation, so it never gets a tint for that: a DST on
the QB's opponent (DST vs your own QB, already an `Issues` guardrail) stays plain, and so does a DST
facing a non-QB player of yours (nothing here looks at the opponent of a DST). The numeric columns are
left alone so their colour scales still read.

One custom-formula rule per tint per lineup block. Blocks are fixed row
ranges (`weekly_reset.LINEUPS_NAME_BLOCKS`), and every reference inside a
rule is to that block's own rows, so a rule can never read another lineup.

The three rules are mutually exclusive by construction, so rule priority never decides a cell's colour:

    stack       team == QB team                      (any position, DST included)
    bring-back  team == QB opponent AND not a DST
    other       team != QB team AND team != QB opponent, AND (a game / own-team-DST / DST-with-teammate
                condition)

Stack and bring-back differ on the team (a team is never both the QB's and his opponent's); `other` carries
both exclusions explicitly, so it can never overlap either. A DST on the QB's opponent matches none of
the three.

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
    "Cell tints on Name/Pos./Team: blue = your QB, his teammates and his team's DST (the stack); "
    "amber = the QB's opponent (bring-back); lavender = 2+ players from any other game, or a DST "
    "with its own team's players. A DST facing your QB is never tinted."
)


def _lookup(col: str, start: int, end: int, pos_col: str) -> str:
    """The QB row's value in `col` (blank when the block has no QB yet)."""
    return f'IFERROR(INDEX(${col}${start}:${col}${end},MATCH("QB",${pos_col}${start}:${pos_col}${end},0)),"")'


def tint_formulas(
    start: int, end: int, *, name_col: str, pos_col: str, team_col: str, opp_col: str, gameid_col: str
) -> dict[str, str]:
    """`{"stack", "bring_back", "other"}` -> CUSTOM_FORMULA text, written
    for the block's top-left cell (`start`) with row-relative references, so
    it follows every row of `A{start}:C{end}`. See the module docstring for the rules and why they
    cannot overlap."""
    named = f'${name_col}{start}<>""'
    not_dst = f'${pos_col}{start}<>"DST"'
    is_dst = f'${pos_col}{start}="DST"'
    team = f"${team_col}{start}"
    qb_team = _lookup(team_col, start, end, pos_col)
    qb_opp = _lookup(opp_col, start, end, pos_col)
    game = f"${gameid_col}{start}"
    pos_rng = f"${pos_col}${start}:${pos_col}${end}"
    name_rng = f"${name_col}${start}:${name_col}${end}"
    game_rng = f"${gameid_col}${start}:${gameid_col}${end}"
    team_rng = f"${team_col}${start}:${team_col}${end}"

    on_qb_team = f'AND({qb_team}<>"",{team}={qb_team})'
    on_qb_opp = f'AND({qb_opp}<>"",{team}={qb_opp})'
    # Lavender's three ways in. A player counts a DST's team-mate only if that DST is in THIS lineup
    # (and the other way round); both sides are limited to this block's own rows.
    two_in_a_game = f'AND({not_dst},COUNTIFS({game_rng},{game},{pos_rng},"<>DST",{name_rng},"<>")>=2)'
    has_own_dst = f'AND({not_dst},COUNTIFS({team_rng},{team},{pos_rng},"DST",{name_rng},"<>")>=1)'
    dst_with_teammate = f'AND({is_dst},COUNTIFS({team_rng},{team},{pos_rng},"<>DST",{name_rng},"<>")>=1)'
    return {
        "stack": f'=AND({named},{team}<>"",{on_qb_team})',
        "bring_back": f"=AND({named},{not_dst},{on_qb_opp})",
        "other": (
            f'=AND({named},{team}<>"",NOT({on_qb_team}),NOT({on_qb_opp}),'
            f"OR({two_in_a_game},{has_own_dst},{dst_with_teammate}))"
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
