"""The contests engine: learn which contests suit which lineups, and how Sam's fields build theirs.

Pure Python over DraftKings files Sam downloads by hand (never fetched here -- standing rule: no DraftKings
network access of any kind) and public nflverse data. Nothing in this package touches a sheet.

- `history`     the durable contest-entry log (deduplicated by `Entry_Key`, NFL season/week/format added)
- `series`      contest name -> series key and format (`Game_Type` decides the format first)
- `standings`   one contest's "export full standings" CSV -> entries, score profile, parsed lineups, ownership
- `field`       how the field builds its lineups (stacks, salary left, FLEX, chalk, DST, duplicates)
- `slate_index` how high a slate scored, from public data, calibrated against Sam's real cash lines
- `toughness`   score -> finish curve per format and series (shrunk), plus H2H records
- `cli`         `dfs contests analyze` (hidden while unfinished; writes CSVs only)

`docs/CONTESTS.md` is the one place the method and every assumption live.
"""
