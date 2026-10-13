// File: Code.gs
//
// The one Apps Script bound to the DFS sheet (Extensions > Apps Script). Paste this whole file over
// everything in the editor; see docs/APPS_SCRIPT.md. The previous script is kept for the record in
// apps_script/legacy_Code.gs.
//
// What it does:
//   1. Lineup Tools > Clear Lineup Names: clears the player names of the Lineups tab (always that tab,
//      never the active one), only the 9 player rows of each block, after a confirm dialog.
//   2. onEdit, the `Pool` dropdown: every player row on the Edge Finder, Board and Player Pool tabs has a
//      `Pool` cell left of his name. The cell holds a formula that shows his state on EdgeRaw (blank, Cash,
//      GPP or Both); picking a value overwrites that formula, so this script writes the value to EdgeRaw's
//      `Pool` cell (found by the row's hidden `Id`, never by name) and puts the formula back. Clearing the
//      cell (Delete) removes him from the pool. Every other `Pool` cell follows by itself, because each is
//      the same formula.
//   3. onEdit, Player Pool's add-a-player box: the Name-column cell of row 1, which holds the hint "Add a
//      player: type a name" while empty. Pick or type a name, and he is added to the pool as the type beside
//      the box (default Both); the hint then goes back. The box is found from the header, never from text
//      in row 1 (version 5; before that a blank label cell silently killed it).
//   4. Lineup Tools > Fix FLEX order: for every lineup block on the Lineups tab, puts the player with the
//      latest kickoff (among the position that has one more player than its own slots hold) in the FLEX slot,
//      by swapping two typed names. Never moves a player whose game has started. Kickoffs come from EdgeRaw's
//      `GameStart` by name; "now" is read in Eastern time, the zone those kickoffs are written in.
//   5. Lineup Tools > Copy build to Lineups: asks for a build number from the Suggested tab and copies that
//      build's nine names into the FIRST EMPTY lineup block on Lineups, setting its Cash / GPP marker. It
//      never overwrites a block that has a name in it and does nothing when every block is taken.
//   6. onOpen: the menu, plus a version stamp in the hidden named range DFS_SCRIPT_VERSION so `dfs
//      doctor` can tell whether this script is pasted and current.
//
// Everything is found by header text or label, never by a fixed row or column. The pure helper
// functions at the bottom are unit-tested with node (tests/test_apps_script.py).

// Bump this whenever this file changes: `dfs doctor` warns when the sheet's stamp is older.
var DFS_SCRIPT_VERSION = 5;

var VERSION_RANGE_NAME = 'DFS_SCRIPT_VERSION';
var VERSION_TAB = 'NameAlias'; // a hidden tab nothing rewrites
var VERSION_CELL = 'H1';

var LINEUPS_TAB = 'Lineups';
var EDGE_RAW_TAB = 'EdgeRaw';
var PLAYER_POOL_TAB = 'Player Pool';
var POOL_TABS = ['Edge Finder', 'Board', 'Player Pool'];

var NAME_HEADER = 'Name';
var TOTAL_LABEL = 'Total';
var PLAYERS_PER_BLOCK = 9;
var LABEL_SEARCH_COLUMNS = 8; // the Total label sits in one of the first columns of its row

var SLOT_ORDER = ['QB', 'RB', 'RB', 'WR', 'WR', 'WR', 'TE', 'FLEX', 'DST']; // one lineup block, top to bottom
var FLEX_SLOT = 'FLEX';
var FLEX_BASE = { RB: 2, WR: 3, TE: 1 }; // the players each position's own slots hold before the FLEX
var POSITION_HEADER = 'Position'; // EdgeRaw
var GAMESTART_HEADER = 'GameStart'; // EdgeRaw: Eastern wall-clock text with a trailing Z
var KEY_HEADER = 'Player Key'; // Lineups: DraftKings' canonical name for what was typed (hidden)
var KICKOFF_TIMEZONE = 'America/New_York';
var SUGGESTED_TAB = 'Suggested';
var BUILD_LABEL = 'Build'; // column A of a Suggested block's bar row reads "Build 4"
var MARKER_VALUES = ['Cash', 'GPP'];

var ID_HEADER = 'Id';
var POOL_HEADER = 'Pool';
var POOL_VALUES = ['Cash', 'GPP', 'Both'];
var ADD_HINT = 'Add a player: type a name'; // what the add-a-player box holds while it is empty
var ADD_LEGACY_LABEL = 'Add a player'; // the old label cell's text: counts as an empty box
var ADD_DEFAULT_TYPE = 'Both';
var HEADER_SEARCH_ROWS = 10; // Player Pool's header is within the first rows under the control row
var MAX_POOL_EDIT_ROWS = 100; // a bigger paste or delete is ignored rather than half-applied


// ---------------------------------------------------------------------------------------------
// Menu and version stamp
// ---------------------------------------------------------------------------------------------

function onOpen(e) {
  SpreadsheetApp.getUi()
    .createMenu('Lineup Tools')
    .addItem('Clear Lineup Names', 'clearLineupNames')
    .addItem('Fix FLEX order', 'fixFlexOrder')
    .addItem('Copy build to Lineups', 'copyBuildToLineups')
    .addToUi();
  try {
    stampVersion_();
  } catch (err) {
    // A failed stamp must never stop the menu from appearing; `dfs doctor` will say it is missing.
  }
}

function stampVersion_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var named = ss.getRangeByName(VERSION_RANGE_NAME);
  if (!named) {
    var tab = ss.getSheetByName(VERSION_TAB);
    if (!tab) return;
    named = tab.getRange(VERSION_CELL);
    ss.setNamedRange(VERSION_RANGE_NAME, named);
  }
  if (named.getValue() !== DFS_SCRIPT_VERSION) named.setValue(DFS_SCRIPT_VERSION);
}


// ---------------------------------------------------------------------------------------------
// Clear Lineup Names
// ---------------------------------------------------------------------------------------------

function clearLineupNames() {
  var ui = SpreadsheetApp.getUi();
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(LINEUPS_TAB);
  if (!sheet) {
    ui.alert('Clear Lineup Names', 'There is no "' + LINEUPS_TAB + '" tab in this sheet.', ui.ButtonSet.OK);
    return;
  }
  var lastRow = sheet.getLastRow();
  var lastCol = Math.min(sheet.getLastColumn(), LABEL_SEARCH_COLUMNS);
  var grid = lastRow > 0 && lastCol > 0 ? sheet.getRange(1, 1, lastRow, lastCol).getValues() : [];
  var plan = planLineupClear(grid);

  if (plan.rows.length === 0) {
    ui.alert('Clear Lineup Names', 'No lineup blocks were found on ' + LINEUPS_TAB + ' (looked for a "' +
      NAME_HEADER + '" header and a "' + TOTAL_LABEL + '" row under it).', ui.ButtonSet.OK);
    return;
  }
  var filled = 0;
  plan.rows.forEach(function (r) { if (String(grid[r - 1][0]).trim() !== '') filled++; });
  var message = 'Clear ' + filled + ' player name' + (filled === 1 ? '' : 's') + ' from ' + plan.blocks +
    ' lineup' + (plan.blocks === 1 ? '' : 's') + ' on ' + LINEUPS_TAB + '?\n\n' +
    'Only the player rows are cleared. The Total and Remaining rows and each lineup\'s Cash/GPP marker stay.';
  if (plan.skipped.length > 0) {
    message += '\n\nSkipped (not ' + PLAYERS_PER_BLOCK + ' player rows between the header and the ' +
      TOTAL_LABEL + ' row): the block at row ' + plan.skipped.join(', row ') + '.';
  }
  if (ui.alert('Clear Lineup Names', message, ui.ButtonSet.YES_NO) !== ui.Button.YES) return;

  sheet.getRangeList(plan.rows.map(function (r) { return 'A' + r; })).clearContent();
}


// ---------------------------------------------------------------------------------------------
// Fix FLEX order
// ---------------------------------------------------------------------------------------------

function fixFlexOrder() {
  var ui = SpreadsheetApp.getUi();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(LINEUPS_TAB);
  var edge = openEdge_(ss);
  if (!sheet || edge === null) {
    ui.alert('Fix FLEX order', 'This needs the "' + LINEUPS_TAB + '" and "' + EDGE_RAW_TAB + '" tabs.', ui.ButtonSet.OK);
    return;
  }
  var lastRow = sheet.getLastRow();
  var lastCol = sheet.getLastColumn();
  var all = lastRow > 0 && lastCol > 0 ? sheet.getRange(1, 1, lastRow, lastCol).getValues() : [];
  var grid = all.map(function (r) { return r.slice(0, LABEL_SEARCH_COLUMNS); });
  var blocks = findLineupBlocks(grid).blocks;
  if (blocks.length === 0) {
    ui.alert('Fix FLEX order', 'No lineup blocks were found on ' + LINEUPS_TAB + '.', ui.ButtonSet.OK);
    return;
  }
  var info = edgeKickoffs_(edge);
  if (info === null) {
    ui.alert('Fix FLEX order', EDGE_RAW_TAB + ' has no ' + POSITION_HEADER + ' / ' + GAMESTART_HEADER +
      ' column to read kickoffs from. Run `dfs sync` first.', ui.ButtonSet.OK);
    return;
  }
  var keyCol = -1;
  for (var h = 0; h < blocks.length && keyCol === -1; h++) keyCol = findColumn(all[blocks[h].header - 1], KEY_HEADER);
  var nowEt = Utilities.formatDate(new Date(), KICKOFF_TIMEZONE, "yyyy-MM-dd'T'HH:mm:ss");

  var moves = [];
  var left = [];
  blocks.forEach(function (block) {
    var seats = [];
    for (var i = 0; i < SLOT_ORDER.length; i++) {
      var row = all[block.first - 1 + i];
      var typed = String(row[0]).trim();
      var key = keyCol === -1 ? '' : String(row[keyCol - 1]).trim();
      var known = info[normalizeName(key)] || info[normalizeName(typed)] || null;
      seats.push({ slot: SLOT_ORDER[i], name: typed, position: known ? known.position : '', start: known ? known.start : '' });
    }
    var plan = planFlexSwap(seats, nowEt);
    if (plan.status === 'move') {
      moves.push({ block: block, plan: plan, seats: seats });
    } else if (plan.status === 'locked' || plan.status === 'unknown') {
      left.push('the lineup at row ' + block.header + ' (' + (plan.status === 'locked' ? 'a game has started' : 'a kickoff time is missing') + ')');
    }
  });

  if (moves.length === 0) {
    ui.alert('Fix FLEX order', 'Every lineup already has the latest kickoff in FLEX, or cannot be changed.' +
      (left.length ? '\n\nLeft alone: ' + left.join('; ') + '.' : ''), ui.ButtonSet.OK);
    return;
  }
  var lines = moves.map(function (m) {
    var to = m.seats[m.plan.toFlex];
    var from = m.seats[m.plan.fromFlex];
    return 'Lineup at row ' + m.block.header + ': ' + to.name + ' to FLEX (plays ' + playsLabel(to.start) + '), ' + from.name + ' to ' + to.slot;
  });
  var message = 'Reorder ' + moves.length + ' lineup' + (moves.length === 1 ? '' : 's') + ' on ' + LINEUPS_TAB + '?\n\n' + lines.join('\n') +
    '\n\nOnly two typed names swap in each lineup.' + (left.length ? '\n\nLeft alone: ' + left.join('; ') + '.' : '');
  if (ui.alert('Fix FLEX order', message, ui.ButtonSet.YES_NO) !== ui.Button.YES) return;

  moves.forEach(function (m) {
    var toRow = m.block.first + m.plan.toFlex;
    var flexRow = m.block.first + m.plan.fromFlex;
    var a = sheet.getRange(toRow, 1);
    var b = sheet.getRange(flexRow, 1);
    var keep = a.getValue();
    a.setValue(b.getValue());
    b.setValue(keep);
  });
  toast_('FLEX order fixed on ' + moves.length + ' lineup' + (moves.length === 1 ? '' : 's') + '.');
}

/** EdgeRaw as {normalized name: {position, start}} (start = the kickoff text without its trailing Z), or null. */
function edgeKickoffs_(edge) {
  var header = edge.sheet.getRange(1, 1, 1, edge.sheet.getLastColumn()).getValues()[0];
  var posCol = findColumn(header, POSITION_HEADER);
  var startCol = findColumn(header, GAMESTART_HEADER);
  if (edge.nameCol === -1 || posCol === -1 || startCol === -1) return null;
  var last = edge.sheet.getLastRow();
  if (last < 2) return {};
  var values = edge.sheet.getRange(2, 1, last - 1, edge.sheet.getLastColumn()).getValues();
  var out = {};
  values.forEach(function (r) {
    var key = normalizeName(r[edge.nameCol - 1]);
    if (key !== '' && !out[key]) out[key] = { position: String(r[posCol - 1]).trim(), start: parseKickoff(r[startCol - 1]) };
  });
  return out;
}


// ---------------------------------------------------------------------------------------------
// Copy build to Lineups
// ---------------------------------------------------------------------------------------------

function copyBuildToLineups() {
  var ui = SpreadsheetApp.getUi();
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var suggested = ss.getSheetByName(SUGGESTED_TAB);
  var lineups = ss.getSheetByName(LINEUPS_TAB);
  if (!suggested || !lineups) {
    ui.alert('Copy build to Lineups', 'This needs the "' + SUGGESTED_TAB + '" and "' + LINEUPS_TAB + '" tabs.', ui.ButtonSet.OK);
    return;
  }
  var sGrid = suggested.getLastRow() > 0 ? suggested.getRange(1, 1, suggested.getLastRow(), Math.min(3, suggested.getLastColumn() || 3)).getValues() : [];
  var available = parseBuilds(sGrid).map(function (b) { return b.number; });
  if (available.length === 0) {
    ui.alert('Copy build to Lineups', 'The Suggested tab has no builds yet. Run `dfs lineups suggest` first.', ui.ButtonSet.OK);
    return;
  }
  var answer = ui.prompt('Copy build to Lineups', 'Which build? (' + available.join(', ') + ')', ui.ButtonSet.OK_CANCEL);
  if (answer.getSelectedButton() !== ui.Button.OK) return;
  var number = parseInt(String(answer.getResponseText()).replace(/[^0-9]/g, ''), 10);

  var lastRow = lineups.getLastRow();
  var lGrid = lastRow > 0 ? lineups.getRange(1, 1, lastRow, Math.min(lineups.getLastColumn(), LABEL_SEARCH_COLUMNS)).getValues() : [];
  var plan = planCopyBuild(sGrid, number, lGrid);
  if (plan.status === 'noBuild') {
    ui.alert('Copy build to Lineups', 'There is no build ' + answer.getResponseText() + ' on ' + SUGGESTED_TAB + '. Builds: ' + available.join(', ') + '.', ui.ButtonSet.OK);
    return;
  }
  if (plan.status === 'noBlock') {
    ui.alert('Copy build to Lineups', 'Every lineup block on ' + LINEUPS_TAB + ' already has a name in it, so nothing was copied. Clear a lineup first.', ui.ButtonSet.OK);
    return;
  }
  var message = 'Copy build ' + plan.build.number + ' (' + plan.build.kind + ': ' + plan.build.title + ') into the lineup at row ' + plan.block.header +
    ' of ' + LINEUPS_TAB + '?\n\n' + plan.build.names.join(', ') + '\n\nIt is an empty lineup; nothing is overwritten.';
  if (ui.alert('Copy build to Lineups', message, ui.ButtonSet.YES_NO) !== ui.Button.YES) return;

  lineups.getRange(plan.block.first, 1, plan.build.names.length, 1).setValues(plan.build.names.map(function (n) { return [n]; }));
  if (plan.marker) lineups.getRange(plan.block.total, 1).setValue(plan.marker);
  toast_('Build ' + plan.build.number + ' copied to the lineup at row ' + plan.block.header + '.');
}


// ---------------------------------------------------------------------------------------------
// The Pool dropdown and the add-a-player box
// ---------------------------------------------------------------------------------------------

function onEdit(e) {
  try {
    if (!e || !e.range) return;
    var sheet = e.range.getSheet();
    if (POOL_TABS.indexOf(sheet.getName()) === -1) return;
    if (sheet.getName() === PLAYER_POOL_TAB && handleAddPlayer_(e.range)) return;
    handlePoolEdit_(e.range);
  } catch (err) {
    SpreadsheetApp.getActiveSpreadsheet().toast('Pool change failed: ' + err.message, 'DFS', 8);
  }
}

/** True when the cell carries the Pool dropdown (a list of exactly POOL_VALUES). */
function isPoolControl_(validation) {
  if (!validation || validation.getCriteriaType() !== SpreadsheetApp.DataValidationCriteria.VALUE_IN_LIST) {
    return false;
  }
  return sameList(validation.getCriteriaValues()[0], POOL_VALUES);
}

function handlePoolEdit_(range) {
  if (range.getNumColumns() !== 1 || range.getNumRows() > MAX_POOL_EDIT_ROWS) return;
  var validations = range.getDataValidations();
  if (!validations.some(function (v) { return isPoolControl_(v[0]); })) return; // not a Pool control

  var sheet = range.getSheet();
  var col = range.getColumn();
  var first = range.getRow();
  var values = range.getValues();
  var colValues = sheet.getRange(1, col, range.getLastRow(), 1).getValues().map(function (r) { return r[0]; });
  var edge = openEdge_(sheet.getParent());
  if (edge === null) return;
  var headerCache = {};

  for (var i = 0; i < values.length; i++) {
    if (!isPoolControl_(validations[i][0])) continue;
    var row = first + i;
    var headerRow = headerRowAbove(colValues, row, POOL_HEADER);
    var cell = sheet.getRange(row, col);
    if (headerRow === -1) continue; // e.g. the add-a-player type cell above Player Pool's header
    if (!headerCache[headerRow]) {
      headerCache[headerRow] = sheet.getRange(headerRow, 1, 1, sheet.getLastColumn()).getValues()[0];
    }
    var idCol = findColumn(headerCache[headerRow], ID_HEADER);
    if (idCol === -1) { toast_('No Id column under this Pool column, nothing changed.'); continue; }

    var action = normalizePoolValue(values[i][0]);
    var id = String(sheet.getRange(row, idCol).getValue()).trim();
    // Whatever happens next, the cell gets its formula back: it never keeps a typed value.
    var formula = poolFormula(row, columnLetter(idCol), EDGE_RAW_TAB, columnLetter(edge.poolCol),
      columnLetter(edge.idCol));
    if (id === '') {
      restoreFormula_(cell, formula);
      toast_('That row has no player, nothing changed.');
      continue;
    }
    if (action.kind === 'invalid') {
      restoreFormula_(cell, formula);
      toast_('"' + values[i][0] + '" is not Cash, GPP or Both, nothing changed.');
      continue;
    }
    var edgeRow = findEdgeRow_(edge, id);
    if (edgeRow === -1) {
      restoreFormula_(cell, formula);
      toast_('Could not find that player on ' + EDGE_RAW_TAB + ', nothing changed.');
      continue;
    }
    var poolCell = edge.sheet.getRange(edgeRow, edge.poolCol);
    if (action.kind === 'remove') poolCell.clearContent(); else poolCell.setValue(action.value);
    restoreFormula_(cell, formula);
    var name = edge.nameCol === -1 ? id : edge.sheet.getRange(edgeRow, edge.nameCol).getValue();
    toast_(action.kind === 'remove' ? 'Removed ' + name : name + ': ' + action.value);
  }
}

/**
 * Puts the Pool formula back in a mirrored cell. The cell is a strict Cash / GPP / Both dropdown, and Apps
 * Script refuses to write a result the list does not hold (a player taken out of the pool shows a blank), with
 * "The data you entered in cell A741 violates the data validation rules": the edit then left the cell empty.
 * So: let EdgeRaw settle first, try the plain write, and if the validation refuses it, lift the rule for the one
 * write and put it back. (`dfs setup polish` now writes these dropdowns as warnings, so this is the backstop for
 * a sheet that has not been re-polished.)
 */
function restoreFormula_(cell, formula) {
  SpreadsheetApp.flush();
  try {
    cell.setFormula(formula);
    return;
  } catch (err) {
    // the validation refused the formula's current result: fall through
  }
  var rule = cell.getDataValidation();
  cell.clearDataValidations();
  try {
    cell.setFormula(formula);
  } finally {
    if (rule) cell.setDataValidation(rule);
  }
}

/** EdgeRaw's sheet and the columns the script needs, found by header text; null (with a toast) if absent. */
function openEdge_(ss) {
  var sheet = ss.getSheetByName(EDGE_RAW_TAB);
  if (!sheet) { toast_('There is no ' + EDGE_RAW_TAB + ' tab.'); return null; }
  var header = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var edge = {
    sheet: sheet,
    idCol: findColumn(header, ID_HEADER),
    poolCol: findColumn(header, POOL_HEADER),
    nameCol: findColumn(header, NAME_HEADER)
  };
  if (edge.idCol === -1 || edge.poolCol === -1) {
    toast_(EDGE_RAW_TAB + ' has no ' + ID_HEADER + ' / ' + POOL_HEADER + ' header.');
    return null;
  }
  return edge;
}

/** The 1-based EdgeRaw row of a DraftKings id, or -1. */
function findEdgeRow_(edge, id) {
  var last = edge.sheet.getLastRow();
  if (last < 2) return -1;
  var hit = edge.sheet.getRange(2, edge.idCol, last - 1, 1)
    .createTextFinder(String(id)).matchEntireCell(true).findNext();
  return hit ? hit.getRow() : -1;
}

/**
 * Player Pool's add-a-player box: the Name-column cell of row 1. The control row reads, left to right: a type
 * dropdown in the Pool column, then the box, which holds ADD_HINT while it is empty. A helper in the hidden Id
 * column of the same row holds what the box's text resolves to (the sheet's own name resolver). When the
 * edited cell is the box, add the player to EdgeRaw's Pool as the type and put the hint back; an emptied box
 * gets the hint back too. The box is found from the header under the control row, so nothing in row 1 can
 * hide it. Returns true when the edit was in the box, handled or not.
 */
function handleAddPlayer_(range) {
  var sheet = range.getSheet();
  if (range.getRow() !== 1) return false;
  var box = findNameColumnBelow_(sheet);
  if (box === -1 || range.getColumn() !== box) return false;
  var typed = String(range.getValue()).trim();
  if (typed === '') { range.setValue(ADD_HINT); return true; }
  if (isEmptyAddBox(typed)) return true;
  SpreadsheetApp.flush(); // let the resolved-name formula settle
  var resolved = '';
  var idCol = findIdColumnBelow_(sheet);
  if (idCol !== -1) resolved = sheet.getRange(1, idCol).getValue();
  var name = chooseAddName(typed, resolved);
  var type = normalizeAddType(box > 1 ? sheet.getRange(1, box - 1).getValue() : '');
  var edge = openEdge_(sheet.getParent());
  if (edge === null || edge.nameCol === -1) return true;
  var last = edge.sheet.getLastRow();
  var hit = last < 2 ? null : edge.sheet.getRange(2, edge.nameCol, last - 1, 1)
    .createTextFinder(name).matchEntireCell(true).findNext();
  if (!hit) {
    toast_('Could not find "' + typed + '" on ' + EDGE_RAW_TAB + ', nothing added.');
    return true;
  }
  edge.sheet.getRange(hit.getRow(), edge.poolCol).setValue(type);
  range.setValue(ADD_HINT);
  toast_('Added ' + name + ' (' + type + ').');
  return true;
}

/** The column whose header (in the rows just under the control row) reads Name, or -1. */
function findNameColumnBelow_(sheet) {
  return findHeaderColumnBelow_(sheet, NAME_HEADER);
}

/** The column whose header (in the rows just under the control row) reads Id, or -1. */
function findIdColumnBelow_(sheet) {
  return findHeaderColumnBelow_(sheet, ID_HEADER);
}

function findHeaderColumnBelow_(sheet, text) {
  var rows = Math.min(HEADER_SEARCH_ROWS, sheet.getLastRow() - 1);
  if (rows < 1) return -1;
  var below = sheet.getRange(2, 1, rows, sheet.getLastColumn()).getValues();
  for (var r = 0; r < below.length; r++) {
    var c = findColumn(below[r], text);
    if (c !== -1) return c;
  }
  return -1;
}

function toast_(message) {
  SpreadsheetApp.getActiveSpreadsheet().toast(message, 'DFS', 5);
}


// ---------------------------------------------------------------------------------------------
// Pure helpers (no SpreadsheetApp): unit-tested with node
// ---------------------------------------------------------------------------------------------

/**
 * Plan a Lineups clear from the sheet's values (a 2D array of the first columns, row 1 first).
 * A block starts at a row whose first cell is NAME_HEADER and ends at the first row below it with a
 * TOTAL_LABEL cell in the first LABEL_SEARCH_COLUMNS columns; its player rows are the rows between.
 * A block must have exactly PLAYERS_PER_BLOCK player rows to be cleared, else it is skipped, and a
 * block that meets another header (or the end) before a Total row is skipped too. Returns
 * {rows: [1-based rows whose column A to clear], blocks: n, skipped: [1-based header rows]}.
 */
function planLineupClear(grid) {
  var rows = [];
  var found = findLineupBlocks(grid);
  found.blocks.forEach(function (block) {
    for (var r = block.first; r < block.first + PLAYERS_PER_BLOCK; r++) rows.push(r);
  });
  return { rows: rows, blocks: found.blocks.length, skipped: found.skipped };
}

/**
 * The lineup blocks of a Lineups grid: {blocks: [{header, first, total}], skipped: [header rows]} with
 * 1-based rows (`first` is the block's first player row). A block is a NAME_HEADER row followed by exactly
 * PLAYERS_PER_BLOCK player rows and a TOTAL_LABEL row; anything else is skipped.
 */
function findLineupBlocks(grid) {
  var blocks = [];
  var skipped = [];
  for (var i = 0; i < grid.length; i++) {
    if (String(grid[i][0]).trim() !== NAME_HEADER) continue;
    var total = -1;
    for (var j = i + 1; j < grid.length; j++) {
      if (String(grid[j][0]).trim() === NAME_HEADER) break;
      if (rowHasLabel(grid[j], TOTAL_LABEL)) { total = j; break; }
    }
    if (total === -1 || total - i - 1 !== PLAYERS_PER_BLOCK) {
      skipped.push(i + 1);
      continue;
    }
    blocks.push({ header: i + 1, first: i + 2, total: total + 1 });
  }
  return { blocks: blocks, skipped: skipped };
}

function rowHasLabel(row, label) {
  var n = Math.min(row.length, LABEL_SEARCH_COLUMNS);
  for (var k = 0; k < n; k++) {
    if (String(row[k]).trim() === label) return true;
  }
  return false;
}

/**
 * The builds on a Suggested grid (`src/dfs/sheet_suggested.py` `parse_builds` does the same in Python): a bar row
 * whose column A reads "Build N" (column B the kind, Cash or GPP, column C the title), then the column header,
 * then nine player rows with the name in column B. A block without nine names is left out. Returns
 * [{number, kind, title, names: [9 names]}].
 */
function parseBuilds(grid) {
  var builds = [];
  for (var i = 0; i < grid.length; i++) {
    var label = String(grid[i][0] === undefined ? '' : grid[i][0]).trim();
    var match = /^Build (\d+)$/.exec(label);
    if (!match || label.indexOf(BUILD_LABEL) !== 0) continue;
    var names = [];
    for (var j = i + 2; j < i + 2 + SLOT_ORDER.length && j < grid.length; j++) {
      names.push(String(grid[j][1] === undefined ? '' : grid[j][1]).trim());
    }
    if (names.length !== SLOT_ORDER.length || names.some(function (n) { return n === ''; })) continue;
    builds.push({
      number: parseInt(match[1], 10),
      kind: String(grid[i][1] === undefined ? '' : grid[i][1]).trim(),
      title: String(grid[i][2] === undefined ? '' : grid[i][2]).trim(),
      names: names
    });
  }
  return builds;
}

/**
 * Where build `number` goes: the FIRST lineup block whose nine name cells (column A) are all empty. Returns
 * {status: 'ok', build, block: {header, first, total}, marker} with the Cash / GPP marker to set on the block's
 * Total row, {status: 'noBuild'} when `number` is not on the Suggested grid, or {status: 'noBlock'} when every
 * block already has a name in it. A block that has any name in it is never chosen.
 */
function planCopyBuild(suggestedGrid, number, lineupsGrid) {
  var build = null;
  parseBuilds(suggestedGrid).forEach(function (b) { if (b.number === number) build = b; });
  if (build === null) return { status: 'noBuild' };
  var blocks = findLineupBlocks(lineupsGrid).blocks;
  for (var i = 0; i < blocks.length; i++) {
    var empty = true;
    for (var r = blocks[i].first; r < blocks[i].first + PLAYERS_PER_BLOCK; r++) {
      if (String(lineupsGrid[r - 1][0]).trim() !== '') { empty = false; break; }
    }
    if (empty) {
      return {
        status: 'ok',
        build: build,
        block: blocks[i],
        marker: MARKER_VALUES.indexOf(build.kind) === -1 ? '' : build.kind
      };
    }
  }
  return { status: 'noBlock' };
}

/** A name as the sheets compare it: trimmed and lower-case. */
function normalizeName(value) {
  return String(value === null || value === undefined ? '' : value).trim().toLowerCase();
}

/**
 * A `GameStart` value as sortable Eastern wall-clock text, `YYYY-MM-DDTHH:mm:ss` (TFFB writes Eastern time
 * with a trailing "Z", which is not UTC). '' when blank or not that shape.
 */
function parseKickoff(value) {
  var text = String(value === null || value === undefined ? '' : value).trim().replace(/Z$/, '');
  var match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(:\d{2})?$/.exec(text);
  return match ? match[1] + (match[2] || ':00') : '';
}

/** Where a kickoff falls, the way Sam says it: TNF, SNF, MNF, otherwise `Sun 1:00 pm` (Eastern). */
function playsLabel(start) {
  var match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(start);
  if (!match) return 'later';
  var day = new Date(Date.UTC(+match[1], +match[2] - 1, +match[3])).getUTCDay(); // 0 = Sunday
  var hour = +match[4];
  if (day === 4) return 'TNF';
  if (day === 1) return 'MNF';
  if (day === 0 && hour >= 19) return 'SNF';
  var names = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return names[day] + ' ' + (hour % 12 || 12) + ':' + match[5] + ' ' + (hour < 12 ? 'am' : 'pm');
}

/**
 * The FLEX rule (`src/dfs/flex_order.py` has the same, tested against the same cases). `seats` is the nine
 * players of one lineup in SLOT_ORDER, each {slot, name, position, start} (start from `parseKickoff`, '' when
 * unknown); `nowEt` is the current Eastern time in the same text form. The position with one player more than
 * its own slots hold (RB beyond 2, WR beyond 3, TE beyond 1) owns the FLEX: its player with the latest
 * kickoff belongs there. Returns {status, toFlex, fromFlex} (seat indexes, set for 'move' and 'locked'):
 *   ok          the FLEX already holds the latest kickoff
 *   move        swap the players at toFlex and fromFlex
 *   tie         the latest kickoff is shared: left as it is
 *   locked      one of the two games has started: a started player is never moved
 *   unknown     a kickoff is missing for someone at the flex position
 *   incomplete  fewer than nine named players, or not a legal lineup shape
 */
function planFlexSwap(seats, nowEt) {
  if (seats.length !== SLOT_ORDER.length) return { status: 'incomplete' };
  var counts = { RB: 0, WR: 0, TE: 0 };
  var flexIndex = -1;
  for (var i = 0; i < seats.length; i++) {
    if (String(seats[i].name).trim() === '' || seats[i].position === '') return { status: 'incomplete' };
    if (counts.hasOwnProperty(seats[i].position)) counts[seats[i].position]++;
    if (seats[i].slot === FLEX_SLOT) flexIndex = i;
  }
  var surplus = [];
  var exact = true;
  Object.keys(FLEX_BASE).forEach(function (p) {
    if (counts[p] === FLEX_BASE[p] + 1) surplus.push(p);
    else if (counts[p] !== FLEX_BASE[p]) exact = false;
  });
  if (surplus.length !== 1 || !exact || flexIndex === -1 || seats[flexIndex].position !== surplus[0]) {
    return { status: 'incomplete' };
  }
  var pool = [];
  for (var j = 0; j < seats.length; j++) if (seats[j].position === surplus[0]) pool.push(j);
  for (var k = 0; k < pool.length; k++) if (seats[pool[k]].start === '') return { status: 'unknown' };
  var latest = pool.reduce(function (best, idx) { return seats[idx].start > best ? seats[idx].start : best; }, '');
  var last = pool.filter(function (idx) { return seats[idx].start === latest; });
  if (last.length > 1) return { status: 'tie' };
  var target = last[0];
  if (target === flexIndex) return { status: 'ok' };
  if (seats[target].start <= nowEt || seats[flexIndex].start <= nowEt) {
    return { status: 'locked', toFlex: target, fromFlex: flexIndex };
  }
  return { status: 'move', toFlex: target, fromFlex: flexIndex };
}

/**
 * What an edit of a Pool cell means: {kind:'set', value:'Cash'|'GPP'|'Both'} for a pool value (any case,
 * trimmed), {kind:'remove'} for a cleared cell, {kind:'invalid'} for anything else.
 */
function normalizePoolValue(value) {
  var text = String(value === null || value === undefined ? '' : value).trim();
  if (text === '') return { kind: 'remove' };
  for (var i = 0; i < POOL_VALUES.length; i++) {
    if (POOL_VALUES[i].toLowerCase() === text.toLowerCase()) return { kind: 'set', value: POOL_VALUES[i] };
  }
  return { kind: 'invalid' };
}

/** True when the add-a-player box holds nothing to add: the hint, or the old label's text. */
function isEmptyAddBox(text) {
  var t = String(text === null || text === undefined ? '' : text).trim();
  return t === '' || t === ADD_HINT || t === ADD_LEGACY_LABEL;
}

/** The type beside the add-a-player box: a pool value, else the default. */
function normalizeAddType(value) {
  var action = normalizePoolValue(value);
  return action.kind === 'set' ? action.value : ADD_DEFAULT_TYPE;
}

/** The name to look up for an added player: the resolved name when the sheet has one, else what was typed. */
function chooseAddName(typed, resolved) {
  var r = String(resolved === null || resolved === undefined ? '' : resolved).trim();
  return r !== '' ? r : String(typed).trim();
}

/**
 * The formula for a Pool cell: the player's EdgeRaw Pool value found by the Id in this row, blank when the
 * row has no Id or EdgeRaw does not have him. `tests/test_apps_script.py` pins this text against the one
 * `sheet_pool_cells.pool_formula` writes from Python.
 */
function poolFormula(row, idLetter, edgeTab, edgePoolLetter, edgeIdLetter) {
  var id = '$' + idLetter + row;
  return '=IF(' + id + '="","",IFERROR(INDEX(' + edgeTab + '!$' + edgePoolLetter + ':$' + edgePoolLetter +
    ',MATCH(' + id + ',' + edgeTab + '!$' + edgeIdLetter + ':$' + edgeIdLetter + ',0)),""))';
}

/** 1-based column number to its A1 letters (1 -> A, 27 -> AA). */
function columnLetter(n) {
  var out = '';
  while (n > 0) {
    var rem = (n - 1) % 26;
    out = String.fromCharCode(65 + rem) + out;
    n = Math.floor((n - 1) / 26);
  }
  return out;
}

/** True when two lists hold the same strings in the same order. */
function sameList(a, b) {
  if (!a || a.length !== b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (String(a[i]) !== String(b[i])) return false;
  }
  return true;
}

/**
 * The 1-based row of the nearest cell above `row` (1-based) in a column that reads `text`.
 * `colValues` is the column from row 1 down to `row`. -1 when none.
 */
function headerRowAbove(colValues, row, text) {
  for (var i = Math.min(row, colValues.length) - 2; i >= 0; i--) {
    if (String(colValues[i]).trim() === text) return i + 1;
  }
  return -1;
}

/** The 1-based column of the header cell that reads `text`, or -1. */
function findColumn(headerValues, text) {
  for (var i = 0; i < headerValues.length; i++) {
    if (String(headerValues[i]).trim() === text) return i + 1;
  }
  return -1;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    planLineupClear: planLineupClear,
    findLineupBlocks: findLineupBlocks,
    planFlexSwap: planFlexSwap,
    parseKickoff: parseKickoff,
    parseBuilds: parseBuilds,
    planCopyBuild: planCopyBuild,
    playsLabel: playsLabel,
    normalizeName: normalizeName,
    SLOT_ORDER: SLOT_ORDER,
    normalizePoolValue: normalizePoolValue,
    normalizeAddType: normalizeAddType,
    chooseAddName: chooseAddName,
    isEmptyAddBox: isEmptyAddBox,
    poolFormula: poolFormula,
    columnLetter: columnLetter,
    sameList: sameList,
    headerRowAbove: headerRowAbove,
    findColumn: findColumn,
    rowHasLabel: rowHasLabel,
    DFS_SCRIPT_VERSION: DFS_SCRIPT_VERSION,
    POOL_VALUES: POOL_VALUES
  };
}
