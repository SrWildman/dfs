"""Season-long leagues: Sam's Sleeper leagues, their rest-of-season values and trades (`dfs season ...`).

Nothing here touches DraftKings or the weekly DFS sheet. Leagues come from `season.toml` (gitignored, beside
config.toml): its own file so the weekly CLI's `config.toml` (whose models reject unknown keys) is never
touched. Data comes from Sleeper's public, unauthenticated API (`sleeper_league.py`), plus the FantasyPros and
ESPN projections the weekly sync already archives.

Layers, each pure where it can be: `scoring` (a stat line in one league's scoring), `ros` (per player per
remaining week), `team_value` (best legal lineups, needs, survival in a chopped league).
"""
