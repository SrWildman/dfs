# Troubleshooting

Symptom, then cause, then fix. For the weekly grind, see
[WORKFLOW.md](WORKFLOW.md); for what a column/tab means, see
[SHEET_REFERENCE.md](SHEET_REFERENCE.md).

---

**I don't know what to run next.**
Cause: the weekly workflow has ~15 commands across setup/sync/pool/
lineups/bankroll, and it's easy to lose track of where you are mid-week.
Fix: just run `dfs` with no arguments -- it shows a compact status (sheet,
sync freshness, pool/lineup counts) and the two or three commands that
actually make sense right now, each printed next to its real name.

---

**A command you used to run says "No such command".**
Cause: this project's CLI got reorganized (one-time sheet construction
moved under `dfs setup ...`, `dfs sheets doctor` promoted to top-level
`dfs doctor`) -- see README's Commands table or `dfs --help` for the
current shape.
Fix: `dfs sheets ...` (the old spelling) still works for one season as a
deprecated alias and prints the new name the first time you use it; or
just use the new name directly.

---

**Player Pool is empty (or a position block is empty).**
Cause: nothing's been added to the pool for that position yet.
Fix: three ways in -- tick players in EdgeRaw's Pool column, type a name
into Pool Picks, or `dfs pool add "name"`. Check `dfs pool list` to see
what's currently ticked.

---

**#N/A across an entire lineup row.**
Cause: a typo in the name typed into Lineups column A -- everything else
in that row VLOOKUPs off the name, so one bad cell breaks fourteen
columns with no obvious cause.
Fix: use the dropdown in that cell (it validates against PlayerPoolRaw's
real Name column) instead of typing free text, or retype the name to
match exactly.

---

**Added a player but they're not showing in Player Pool.**
Cause: that position's block is at its cap (QB 10 / RB 20 / WR 25 / TE
10 / DST 10) -- the extra is hidden, not dropped.
Fix: check that block's Overflow column (Z) for a warning naming the cap
and how many are actually ticked/typed. Remove someone else at that
position first, or accept the cap.

---

**A tick in EdgeRaw's Pool column vanished after `dfs sync`.**
Cause: usually nothing's actually wrong -- ticks are preserved across a
sync by matching on the DraftKings player Id, not row position, so a
player whose Id changed (rare) or who dropped off this week's DK salary
file entirely won't have anything to restore onto.
Fix: check whether that player is still in this week's DKSalRaw/EdgeRaw
at all. If they are and the tick still didn't survive, that's a real bug
-- see `sources/edge.py`'s `pre_upload`/`post_upload` preserve-by-Id
logic.

---

**`dfs setup link-edge` reports a column appended twice, or Player Pool/
Lineups' EdgeRaw-linked columns look duplicated.**
Cause: `link_edge_columns`'s own idempotency guard (skip if
`LINKED_EDGE_COLUMNS` already appears as a contiguous run in the header)
failed to recognize an existing link, usually because something inserted
a column into the middle of that block.
Fix: run `dfs doctor` -- it checks for exactly this. If it fails,
do not re-run `link-edge` blindly; see `sheet_links.py`'s own docstring
for the "clear the old linked columns by hand first" recovery path.

---

**The Lineups pool deck shows fewer players than you know are ticked.**
Cause: `PLAYER_POOL_NAME_BLOCKS` grew (a position cap was raised) but
something in the deck/Player Pool chain still has the old, shorter range
hardcoded -- this exact bug shipped twice before `doctor` had a check
for it.
Fix: run `dfs doctor` -- its `pool-deck-range` and
`deck-block-alignment` checks exist specifically to catch this loudly
instead of letting it silently hide players.

---

**A number shows as `33.2900000001` instead of a clean decimal.**
Cause: the cell has no number format applied (or the wrong one), so
Sheets is showing a raw float.
Fix: run `dfs setup polish` -- every column whose header is a
`FIELD_FORMATS` key gets its format re-applied, safe to re-run any time.

---

**A tab looks unstyled (no dark header, no colours, default-width
columns).**
Cause: that tab was never styled, or a styling command didn't reach it
(e.g. a SoS tab pasted after the last `dfs setup polish` run).
Fix: run `dfs setup audit-style` -- it reports exactly which tabs and
which checks (header fill, freeze, widths, number formats, chips) are
missing, per tab, without trusting any command's own "OK" output. Then
run `dfs setup polish` and check again.

---

**You're not sure which sheet a command is about to write to.**
Cause: `config.toml`'s `sheet_id` is an opaque string, and a new sheet
gets copied every week -- easy to lose track of which week you're
pointed at.
Fix: run `dfs status` (or note the title/URL any writing command prints)
before running `dfs sync` or anything else that writes.

---

**I can't sort or search EdgeRaw, and I can't find a search bar.**
Cause: both exist, but the wrong one is easy to miss and the right one
is easy to never look for. `dfs setup add-filters` puts a saved preset
under Data > Filter views, which is genuinely hidden if you've never
opened that menu.
Fix: click the dropdown arrow in any EdgeRaw header cell -- that's a
basic filter (Data > Create a filter), on by default, and sorts/searches
that column directly. The saved presets under Data > Filter views
("Pool picking", "Leverage plays", ...) are the secondary, quick-preset
mechanism, not the primary one.

---

**Player Pool's Venue column (or another column that's just a category,
like Source) looks colour-scaled instead of chipped.**
Cause: a colour scale hand-applied directly in the sheet at some point,
before `sheet_style.FIELD_COLOR_SCALES` existed as the one canonical
policy every tab follows.
Fix: run `dfs setup polish` -- it clears a builder tab's conditional
formats before reapplying them, so a stray manual rule doesn't survive a
re-run.

---

**A cell you're sure you didn't touch shows a "you're editing a
protected range" warning.**
Cause: working as intended -- most formula-driven tabs are protected
(warning-only, see `dfs setup protect`) so a stray keystroke doesn't
silently overwrite a working formula.
Fix: the warning is dismissible, not a hard lock -- if you really meant
to edit there, click through. If you didn't mean to, that's the warning
doing its job; undo (Ctrl+Z) instead of confirming.
