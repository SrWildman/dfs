# The bound Apps Script (`apps_script/Code.gs`)

One small Google Apps Script is bound to the sheet. It does three things, and nothing else:

1. **`Set` dropdowns.** On the **Edge Finder** and **Board** tabs, pick `Cash`, `GPP`, `Both` or `Remove` in a `Set` cell and the player's pool cell on EdgeRaw is written
   (`Remove` clears it, and also removes his name from Player Pool's hidden `Added` list). The `Set` cell then clears itself, and the row's `Pool` column shows the new state.
   The script finds the player by the hidden **`Id`** cell on that row (his DraftKings id), never by name.
2. **Lineup Tools > Clear Lineup Names.** Always acts on the **Lineups** tab (never the one you are looking at), clears only the 9 player rows of each lineup
   (never the Total or Remaining rows, so a lineup's Cash/GPP marker stays), and asks you to confirm first.
3. **Version stamp.** Each time the sheet is opened it writes its version into a hidden named range, `DFS_SCRIPT_VERSION` (a cell on the hidden `NameAlias` tab).
   `dfs doctor` reads it and warns if the script is missing or older than the repo's.

The script before this one is kept for the record in `apps_script/legacy_Code.gs`. Do not paste it back: it acts on whichever sheet is active (run from Player Pool it wipes the Name formulas)
and clears every row between the lineup headers, Totals included.

## Paste it (5 steps; do the template once and Week 5 once, weekly copies inherit it)

1. Open the sheet in a browser: **Extensions > Apps Script**. A script editor opens with one file, `Code.gs`.
2. Click inside the editor, select everything (Cmd-A) and delete it.
3. Open `apps_script/Code.gs` from this repo (or run `cat apps_script/Code.gs | pbcopy`), copy the whole file and paste it into the editor.
4. Click the **Save** icon (or Cmd-S). Close the Apps Script tab.
5. Back in the sheet, **reload the page** (this runs `onOpen`, which draws the Lineup Tools menu and writes the version stamp). The first time you use a feature Google may ask you to
   authorise the script (it only touches this spreadsheet): choose your account, **Advanced > Go to project (unsafe) > Allow**.

Check it worked: `dfs doctor --sheet-id <id>` no longer prints the `WARN` about the Apps Script. Do this on the **template** first, then **Week 5**.

A new weekly sheet is a copy of the template, so it carries the script and the stamp. If a copy's doctor still warns, reload the sheet once (the stamp is written on open).

## When `Code.gs` changes

Bump `DFS_SCRIPT_VERSION` at the top of `apps_script/Code.gs`, paste the file again into the template and the live sheet (steps 1-5), and `dfs doctor` goes quiet again.

## Manual test checklist (needs a browser; the pure functions are unit-tested with node in `tests/test_apps_script.py`)

- Edge Finder: pick `Cash` in a `Set` cell on a player's row. The cell clears, the row's `Pool` shows `Cash`, its `Do` reads `In pool (Cash)`, and EdgeRaw's Pool cell for that player says `Cash`.
- Pick `Both`, then `GPP`: EdgeRaw's Pool cell follows. Pick `Remove`: it is blank again and `Do` goes back to its verb.
- Board: the same on a Queue row and on a Pool check row.
- A player you typed into Player Pool's add-a-player control: `Pool` reads `Added`; `Remove` clears it.
- Type something that is not an option into a `Set` cell (the dropdown rejects it); edit any other cell: nothing happens.
- Lineups: set a lineup's marker, then **Lineup Tools > Clear Lineup Names** from the *Player Pool* tab: the confirm dialog names Lineups, the names clear, the markers and Totals stay,
  and Player Pool's Name formulas are untouched.
