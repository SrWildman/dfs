# The bound Apps Script (`apps_script/Code.gs`)

One small Google Apps Script is bound to the sheet. It does six things, and nothing else:

1. **The `Pool` dropdown.** Every player row on the **Edge Finder**, **Board** and **Player Pool** tabs has a `Pool` cell at the left, before his name. It shows whether he is in your pool (blank, `Cash`, `GPP` or `Both`) and it
   is also the control: pick `Cash`, `GPP` or `Both` to add him, or clear the cell (press Delete) to take him out. The script writes the pick to EdgeRaw's `Pool` cell, which stays the one source of truth, and then
   puts the cell's formula back, so every other `Pool` cell for that player follows by itself. The script finds the player by the hidden **`Id`** cell on the edited row, never by name.
2. **Player Pool's "Add a player" box.** Pick or type a name in the box in the top row (the Name column, reading "Add a player: type a name" while empty), with `Cash`, `GPP` or `Both` in the cell to its left (default `Both`): the player is added to EdgeRaw's `Pool` as that type and the hint goes back. The script finds the box from the header, not from any text in row 1.
3. **Lineup Tools > Clear Lineup Names.** Always acts on the **Lineups** tab (never the one you are looking at), clears only the 9 player rows of each lineup
   (never the Total or Remaining rows, so a lineup's Cash/GPP marker stays), and asks you to confirm first.
4. **Lineup Tools > Fix FLEX order** (version 4). For every lineup block on the **Lineups** tab, puts the player with the latest kickoff in the FLEX slot by swapping two typed names
   (column A only; the Total row's marker and every formula stay). It reads each player's `Position` and `GameStart` from EdgeRaw by name, treats "now" as Eastern time (`America/New_York`, the
   zone TFFB writes kickoffs in) and **never moves a player whose game has started**. The dialog lists every lineup it would change and asks before it writes; a tie, a missing kickoff or a started game is left
   alone and reported. The rule is `src/dfs/flex_order.py`'s, and `tests/test_apps_script.py` runs the same cases through both.
5. **Lineup Tools > Copy builds to Lineups…** (version 6; versions 4 and 5 copied one build). Asks which builds of the **Suggested** tab: type `1,4,6` or `all` (the prompt lists the builds by kind). It copies each one's nine
   names, in the order typed (`all` is Suggested's own order), into the **next lineup block on Lineups with no name in it**, in block order, and sets that block's Cash / GPP marker. It shows what will go where and asks first, never writes
   into a block that has a name, then reports which build went into which lineup and which builds found no empty block left (or are not on Suggested, or were not understood). `tests/test_apps_script.py` reads the same Suggested grid with
   both this script and `sheet_suggested.parse_builds`.
6. **Version stamp.** Each time the sheet is opened it writes its version into a hidden named range, `DFS_SCRIPT_VERSION` (a cell on the hidden `NameAlias` tab).
   `dfs doctor` reads it and warns if the script is missing or older than the repo's.

How the `Pool` cell works: it holds the formula `=IF($Id="","",IFERROR(INDEX(EdgeRaw!Pool, MATCH($Id, EdgeRaw!Id, 0)),""))` for its own row (`sheet_pool_cells.pool_formula`; the script writes the same text, and
`tests/test_apps_script.py` pins the two equal). Picking a value overwrites that formula; the script writes EdgeRaw and restores it. If the script ever does not run (not pasted yet, an error, a copy of the sheet without
it), a picked value stays in the cell and goes stale: **`dfs doctor` flags any `Pool` cell on a player row that holds a plain value instead of the formula**, and every `dfs sync`, `build-views` and `polish` writes the formulas again.

If the script cannot find the player (his `Id` is blank, or EdgeRaw does not have him) it restores the formula and shows a toast ("nothing changed"); it never leaves a typed value behind.

The script before this one is kept for the record in `apps_script/legacy_Code.gs`. Do not paste it back: it acts on whichever sheet is active (run from Player Pool it wipes the Name formulas)
and clears every row between the lineup headers, Totals included.

## Paste it (5 steps; do the template once and the live week once, weekly copies inherit it)

1. Open the sheet in a browser: **Extensions > Apps Script**. A script editor opens with one file, `Code.gs`.
2. Click inside the editor, select everything (Cmd-A) and delete it.
3. Open `apps_script/Code.gs` from this repo (or run `cat apps_script/Code.gs | pbcopy`), copy the whole file and paste it into the editor.
4. Click the **Save** icon (or Cmd-S). Close the Apps Script tab.
5. Back in the sheet, **reload the page** (this runs `onOpen`, which draws the Lineup Tools menu and writes the version stamp). The first time you use a feature Google may ask you to
   authorise the script (it only touches this spreadsheet): choose your account, **Advanced > Go to project (unsafe) > Allow**.

Check it worked: `dfs doctor --sheet-id <id>` no longer prints the `WARN` about the Apps Script. Do this on the **template** first, then **Week 5**.

A new weekly sheet is a copy of the template, so it carries the script and the stamp. If a copy's doctor still warns, reload the sheet once (the stamp is written on open).

## When `Code.gs` changes

Bump `DFS_SCRIPT_VERSION` at the top of `apps_script/Code.gs`, paste the file again into the template and the live sheet (steps 1-5), and `dfs doctor` goes quiet again. **Version 2** (the actions round) replaced the `Set`
dropdown with the `Pool` dropdown and added the add-a-player box: a sheet still on version 1 has no working Pool dropdown. **Version 3** (the polish round) fixes "Pool change failed: The data you entered in cell A741 violates the data validation rules": the
script's `restoreFormula_` lets EdgeRaw settle, tries the plain write, and if a strict dropdown refuses the formula's result (a player taken out of the pool shows a blank) it lifts the rule for that one
write and puts it back. `dfs setup polish` and every Edge Finder write now also make the Pool dropdowns warnings rather than rejections, so a sheet that has been polished never needs the workaround.

**Version 4** (the Week 6 round) adds **Lineup Tools > Fix FLEX order** and **Copy build to Lineups**; a sheet still on version 3 simply has no such menu item. **Version 5** moves the add-a-player box into the Name column of row 1 (it held a separate label with the box one cell to its right: a name typed over the label left the version-4 script nowhere to look, and the box did nothing). Until version 5 is pasted, the new layout works only through `dfs sync`, which adds a name left in the box. **Version 6** (2026-10-17) turns **Copy build to Lineups** into **Copy builds to Lineups…**: several builds at once (`1,4,6` or `all`) into the empty blocks in order. Until it is pasted the old single-build menu item keeps working. **Version 7** (Week 7, the iterate round) reads the Suggested tab's new layout (a lock column left of Name, a decision cell on each bar), so Copy builds works on any round of `dfs lineups suggest --iterate`. **Paste it before the first run that writes the new layout:** version 6 reads the Name from column B, which is now the lock column.

## Manual test checklist (needs a browser; the pure functions are unit-tested with node in `tests/test_apps_script.py`)

- Edge Finder: pick `Cash` in a player row's `Pool` cell. EdgeRaw's Pool cell for him says `Cash`; his `Pool` cell still holds the formula (click it to see) and shows `Cash`; his `Do` reads `In pool (Cash)`.
- Pick `Both`, then `GPP`: EdgeRaw follows. Press Delete on the cell: EdgeRaw's Pool is blank, the cell is blank and `Do` goes back to its verb.
- Board: the same on a Queue row, a Pool check row and a Chalk map row. The Slate shape, Pool summary and Stack candidates rows have no Pool cell.
- Player Pool: clear a pooled player's `Pool` cell: he leaves the pool and the Name column refills without him (rows below move up). Pick `GPP` on another: his tag group changes.
- Player Pool's add-a-player box: set the type beside it to `GPP`, pick a name: he appears in the pool as GPP and the box shows its hint again. Delete the box's text: the hint comes back. A name EdgeRaw does not have: a toast, nothing added.
- After each step read EdgeRaw's `Pool` and the player's `Pool` cell on every tab: they agree (the planning session does this read-back after you paste).
- Type something that is not an option into a `Pool` cell (the dropdown rejects it); edit any other cell: nothing happens.
- Lineups: set a lineup's marker, then **Lineup Tools > Clear Lineup Names** from the *Player Pool* tab: the confirm dialog names Lineups, the names clear, the markers and Totals stay,
  and Player Pool's Name formulas are untouched.
- Lineups: build one lineup with a WR in FLEX whose game is earlier than a WR in a WR slot, then **Lineup Tools > Fix FLEX order**: the dialog names both players ("X to FLEX (plays SNF), Y to WR");
  YES swaps the two typed names and nothing else. Run it again: "already has the latest kickoff". Late on Sunday with the early games started, it leaves the lineup alone and says why.
- Suggested: after `dfs lineups suggest`, **Lineup Tools > Copy builds to Lineups…**, enter `1,4,6`: the dialog names each build and the empty lineup it will use; YES fills column A of those blocks and each Total row's marker, then
  an alert reports what went where. Enter `all` with more builds than empty blocks: the extra builds are listed as "No empty lineup left". Run it again: it goes to the next empty blocks. Enter `99` or `x`: it says so and writes nothing.
  With every block holding a name it refuses.
