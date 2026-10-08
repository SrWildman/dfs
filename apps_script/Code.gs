// File: Code.gs
//
// The one Apps Script bound to the DFS sheet (Extensions > Apps Script). Paste this whole file over
// everything in the editor; see docs/APPS_SCRIPT.md. The previous script is kept for the record in
// apps_script/legacy_Code.gs.
//
// What it does:
//   1. Lineup Tools > Clear Lineup Names: clears the player names of the Lineups tab (always that tab,
//      never the active one), only the 9 player rows of each block, after a confirm dialog.
//   2. onEdit: the `Set` dropdown on the Edge Finder and Board tabs. Pick Cash, GPP, Both or Remove and
//      the player's pool cell on EdgeRaw is written; the Set cell then clears itself.
//   3. onOpen: the menu, plus a version stamp in the hidden named range DFS_SCRIPT_VERSION so `dfs
//      doctor` can tell whether this script is pasted and current.
//
// Everything is found by header text or label, never by a fixed row or column. The pure helper
// functions at the bottom are unit-tested with node (tests/test_apps_script.py).

// Bump this whenever this file changes: `dfs doctor` warns when the sheet's stamp is older.
var DFS_SCRIPT_VERSION = 1;

var VERSION_RANGE_NAME = 'DFS_SCRIPT_VERSION';
var VERSION_TAB = 'NameAlias'; // a hidden tab nothing rewrites
var VERSION_CELL = 'H1';

var LINEUPS_TAB = 'Lineups';
var EDGE_RAW_TAB = 'EdgeRaw';
var PLAYER_POOL_TAB = 'Player Pool';
var SET_TABS = ['Edge Finder', 'Board'];

var NAME_HEADER = 'Name';
var TOTAL_LABEL = 'Total';
var PLAYERS_PER_BLOCK = 9;
var LABEL_SEARCH_COLUMNS = 8; // the Total label sits in one of the first columns of its row

var SET_HEADER = 'Set';
var ID_HEADER = 'Id';
var POOL_HEADER = 'Pool';
var ADDED_HEADER = 'Added';
var SET_VALUES = ['Cash', 'GPP', 'Both', 'Remove'];
var HEADER_SEARCH_ROWS = 10; // Player Pool's header is within the first rows


// ---------------------------------------------------------------------------------------------
// Menu and version stamp
// ---------------------------------------------------------------------------------------------

function onOpen(e) {
  SpreadsheetApp.getUi()
    .createMenu('Lineup Tools')
    .addItem('Clear Lineup Names', 'clearLineupNames')
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
// The Set dropdown
// ---------------------------------------------------------------------------------------------

function onEdit(e) {
  try {
    handleSetEdit_(e);
  } catch (err) {
    SpreadsheetApp.getActiveSpreadsheet().toast('Set failed: ' + err.message, 'DFS', 8);
  }
}

function handleSetEdit_(e) {
  if (!e || !e.range) return;
  var range = e.range;
  if (range.getNumRows() !== 1 || range.getNumColumns() !== 1) return;
  var sheet = range.getSheet();
  if (SET_TABS.indexOf(sheet.getName()) === -1) return;

  var action = normalizeSetValue(range.getValue());
  if (action === null) return; // not one of our values (or a clear): leave the cell alone
  var row = range.getRow();
  var col = range.getColumn();

  var colValues = sheet.getRange(1, col, row, 1).getValues().map(function (r) { return r[0]; });
  var headerRow = headerRowAbove(colValues, row, SET_HEADER);
  if (headerRow === -1) return; // an edit in a column that is not a Set column
  var headerValues = sheet.getRange(headerRow, 1, 1, sheet.getLastColumn()).getValues()[0];
  var idCol = findColumn(headerValues, ID_HEADER);
  if (idCol === -1) {
    toast_('No Id column under this Set column, nothing changed.');
    return;
  }
  var id = sheet.getRange(row, idCol).getValue();
  if (String(id).trim() === '') {
    toast_('That row has no player, nothing changed.');
    range.clearContent();
    return;
  }

  var ss = sheet.getParent();
  var edge = ss.getSheetByName(EDGE_RAW_TAB);
  if (!edge) {
    toast_('There is no ' + EDGE_RAW_TAB + ' tab.');
    return;
  }
  var edgeHeader = edge.getRange(1, 1, 1, edge.getLastColumn()).getValues()[0];
  var edgeIdCol = findColumn(edgeHeader, ID_HEADER);
  var poolCol = findColumn(edgeHeader, POOL_HEADER);
  var nameCol = findColumn(edgeHeader, NAME_HEADER);
  if (edgeIdCol === -1 || poolCol === -1) {
    toast_(EDGE_RAW_TAB + ' has no ' + ID_HEADER + ' / ' + POOL_HEADER + ' header.');
    return;
  }
  var edgeLast = edge.getLastRow();
  var hit = edge.getRange(2, edgeIdCol, Math.max(edgeLast - 1, 1), 1)
    .createTextFinder(String(id)).matchEntireCell(true).findNext();
  if (!hit) {
    toast_('Player ' + id + ' is not on ' + EDGE_RAW_TAB + ' this week.');
    return;
  }
  var edgeRow = hit.getRow();
  var poolCell = edge.getRange(edgeRow, poolCol);
  var pool = poolValueFor(action);
  if (pool === '') poolCell.clearContent(); else poolCell.setValue(pool);

  if (action === 'Remove' && nameCol !== -1) {
    removeFromAddedList_(ss, String(edge.getRange(edgeRow, nameCol).getValue()));
  }
  range.clearContent();
  toast_((action === 'Remove' ? 'Removed ' : 'Set ' + action + ': ') +
    edge.getRange(edgeRow, nameCol === -1 ? 1 : nameCol).getValue());
}

/** Blank the player's entry in Player Pool's hidden `Added` list, if he has one. */
function removeFromAddedList_(ss, name) {
  var pool = ss.getSheetByName(PLAYER_POOL_TAB);
  if (!pool || name === '') return;
  var rows = Math.min(HEADER_SEARCH_ROWS, pool.getLastRow());
  var top = pool.getRange(1, 1, rows, pool.getLastColumn()).getValues();
  for (var r = 0; r < top.length; r++) {
    var c = findColumn(top[r], ADDED_HEADER);
    if (c === -1) continue;
    var below = pool.getRange(r + 2, c, Math.max(pool.getLastRow() - (r + 1), 1), 1);
    var values = below.getValues().map(function (v) { return v[0]; });
    var indices = indicesOfName(values, name);
    indices.forEach(function (i) { pool.getRange(r + 2 + i, c).clearContent(); });
    return;
  }
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
  var skipped = [];
  var blocks = 0;
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
    blocks++;
    for (var r = i + 1; r < total; r++) rows.push(r + 1);
  }
  return { rows: rows, blocks: blocks, skipped: skipped };
}

function rowHasLabel(row, label) {
  var n = Math.min(row.length, LABEL_SEARCH_COLUMNS);
  for (var k = 0; k < n; k++) {
    if (String(row[k]).trim() === label) return true;
  }
  return false;
}

/** The value as one of SET_VALUES (any case, trimmed), or null. */
function normalizeSetValue(value) {
  var text = String(value === null || value === undefined ? '' : value).trim().toLowerCase();
  for (var i = 0; i < SET_VALUES.length; i++) {
    if (SET_VALUES[i].toLowerCase() === text) return SET_VALUES[i];
  }
  return null;
}

/** What goes in EdgeRaw's Pool cell for an action: the value itself, or '' (blank) for Remove. */
function poolValueFor(action) {
  return action === 'Remove' ? '' : action;
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

/** 0-based indices of the cells equal to `name` (trimmed). */
function indicesOfName(values, name) {
  var out = [];
  var want = String(name).trim();
  for (var i = 0; i < values.length; i++) {
    if (String(values[i]).trim() === want && want !== '') out.push(i);
  }
  return out;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    planLineupClear: planLineupClear,
    normalizeSetValue: normalizeSetValue,
    poolValueFor: poolValueFor,
    headerRowAbove: headerRowAbove,
    findColumn: findColumn,
    indicesOfName: indicesOfName,
    rowHasLabel: rowHasLabel,
    DFS_SCRIPT_VERSION: DFS_SCRIPT_VERSION,
    SET_VALUES: SET_VALUES
  };
}
