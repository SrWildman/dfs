"""Round 5 item 6, sheet side: typed player names that don't match DK's
spelling exactly ("kenneth walker", "  AJ Brown ", "Marvin Harrison", "KC
DST") still resolve.

Two pieces:

- **`NameKey`**, a hidden EdgeRaw column: `player_join.normalize_name(Name)`
  computed in Python (lower-case, punctuation stripped, Jr/Sr/II/III/IV/V
  dropped, whitespace collapsed).
- **`norm_expr(ref)`**, the SAME normalization as a sheet formula
  (`LOWER`/`REGEXREPLACE`/`TRIM`), applied to whatever Sam typed. The two
  must agree exactly -- `tests/test_sheet_names.py` pins a Python model of
  the formula against `normalize_name`, and the template's live parity check
  (every EdgeRaw name through the real formula) must read 0 mismatches.

`resolve_name_expr(ref, edge_tab)` turns a typed cell into DK's canonical
`Name`: normalize it, look it up in the hidden `NameAlias` tab first (so
`KC`, `Chiefs`, `Kansas City`, `KC DST`, `Chiefs D/ST`... all mean DK's
`Chiefs` DST), then match the result against EdgeRaw's `NameKey`. An
unmatched name falls back to what was typed, so nothing that resolved
before can stop resolving.

Where it is used (every place a typed name is looked up): the linked-column
VLOOKUPs (`sheet_links.edge_lookup_formula`), the "Edge ↗" row link, the
native PlayerPoolRaw lookups (`sheet_native_links.native_lookup_formula`),
and the add-a-player control cell's position/pool/salary lookups
(`sheet_pool_formulas`). The sync's drain step stores the canonical name.
Counts that ask "is this the same player?" (Lineups' DUPLICATE flag and Min
Unique, Exposure, Player Pool's Used/In) compare Lineups' hidden `Player Key`
-- DK's canonical name for whatever was typed, see `sheet_lineup_keys.py`.
"""

from __future__ import annotations

from dfs.derived import edge_sheet_letter
from dfs.player_join import normalize_name
from dfs.sheets import SheetsClient

NAME_KEY_HEADER = "NameKey"
ALIAS_TAB = "NameAlias"
ALIAS_HEADER = ["Alias (normalized)", "NameKey", "Team", "DK name"]

# DK's DST `Name` is the team NICKNAME ("Chiefs", "49ers"); `city` is the
# common short place name a person types.
TEAMS: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"),
    "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"),
    "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"),
    "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"),
    "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"),
    "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"),
    "LAC": ("Los Angeles", "Chargers"),
    "LAR": ("Los Angeles", "Rams"),
    "LV": ("Las Vegas", "Raiders"),
    "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"),
    "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"),
    "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"),
    "SF": ("San Francisco", "49ers"),
    "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"),
    "WAS": ("Washington", "Commanders"),
}
# Extra spellings people use for a team code.
_CODE_ALIASES = {"JAX": ["JAC"], "LAR": ["LA"], "WAS": ["WSH"], "GB": ["GBP"], "NE": ["NWE"], "NO": ["NOR"]}


def norm_expr(ref: str) -> str:
    """The sheet-formula twin of `player_join.normalize_name` for cell `ref`:
    lower-case, drop everything but letters/digits/underscore/whitespace,
    drop the suffix words (Jr, Sr, II, III, IV, V), collapse whitespace."""
    return (
        f'TRIM(REGEXREPLACE(REGEXREPLACE(REGEXREPLACE(LOWER({ref}),"[^a-z0-9_\\s]",""),'
        f'"\\b(jr|sr|ii|iii|iv|v)\\b",""),"\\s+"," "))'
    )


def _edge_col(name: str) -> str:
    return edge_sheet_letter(name)


def resolve_name_expr(ref: str, edge_tab: str) -> str:
    """DK's canonical `Name` for whatever is typed in `ref` (a cell reference
    or any expression), or `ref` itself when nothing matches."""
    name_col, key_col = _edge_col("Name"), _edge_col(NAME_KEY_HEADER)
    normalized = norm_expr(ref)
    key = f"IFERROR(VLOOKUP({normalized},{ALIAS_TAB}!$A:$B,2,FALSE),{normalized})"
    return (
        f"IFERROR(INDEX({edge_tab}!${name_col}:${name_col},"
        f"MATCH({key},{edge_tab}!${key_col}:${key_col},0)),{ref})"
    )


def dst_alias_rows() -> list[list[str]]:
    """`[alias_key, NameKey, team code, DK name]` for every spelling of every
    DST that resolves to exactly one team. A bare city shared by two teams
    (Los Angeles, New York) is skipped rather than guessed."""
    city_counts: dict[str, int] = {}
    for city, _nick in TEAMS.values():
        city_counts[city] = city_counts.get(city, 0) + 1
    rows: list[list[str]] = []
    seen: set[str] = set()
    for code, (city, nick) in TEAMS.items():
        target = normalize_name(nick)
        spellings = [code, *_CODE_ALIASES.get(code, []), nick, f"{city} {nick}"]
        if city_counts[city] == 1:
            spellings.append(city)
        for label in (code, nick):
            spellings += [f"{label} DST", f"{label} D/ST", f"{label} defense"]
        if city_counts[city] == 1:
            spellings += [f"{city} DST", f"{city} D/ST", f"{city} defense"]
        for spelling in spellings:
            key = normalize_name(spelling)
            if key and key not in seen:
                seen.add(key)
                rows.append([key, target, code, nick])
    return rows


def resolve_typed_name(typed: str, edge_names: list[str]) -> str | None:
    """Python twin of `resolve_name_expr`: DK's canonical `Name` for `typed`
    (DST aliases first, then the normalized name), or `None` when nothing on
    the slate matches. Used where Python, not a formula, decides -- the
    sync's add-a-player drain and `dfs pool add`."""
    key = normalize_name(typed)
    if not key:
        return None
    alias = {row[0]: row[1] for row in dst_alias_rows()}
    key = alias.get(key, key)
    for name in edge_names:
        if normalize_name(name) == key:
            return name
    return None


def build_name_alias_tab(client: SheetsClient) -> str:
    """Writes the hidden `NameAlias` lookup tab (static NFL facts -- not a
    synced source). Safe to re-run: `write_tab` replaces the whole tab."""
    rows = [ALIAS_HEADER, *dst_alias_rows()]
    client.write_tab(ALIAS_TAB, rows)
    return f"{ALIAS_TAB}: {len(rows) - 1} DST alias(es) written"
