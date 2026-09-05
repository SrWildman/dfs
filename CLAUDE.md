# For Claude sessions working in this repo

Read `CONTRIBUTING.md` before adding a data source or touching the live
Google Sheet's structure -- it covers the design principles this codebase
enforces (real exit codes, no silent partial success, sources raise
rather than return partial data) and a specific formula-corruption bug
(inserting a sheet column shifts formula *ranges* app-wide but not
hardcoded integer arguments) that already happened once and is easy to
reintroduce blind.

Before running `dfs sync` or anything else that writes to the sheet,
run `dfs status` (or note the sheet title/URL any writing command prints)
to confirm you're pointed at the sheet you think you are -- `config.toml`'s
`sheet_id` is an opaque string, and a new sheet gets copied every week.

Run `pytest -q`, `ruff check .`, and `ruff format --check .` before
considering a change done -- all three run in CI on every push/PR.
