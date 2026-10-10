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
//   3. onEdit, Player Pool's "Add a player" box: pick a name, and he is added to the pool as the type
//      beside the box (default Both); the box then clears.
//   4. onOpen: the menu, plus a version stamp in the hidden named range DFS_SCRIPT_VERSION so `dfs
//      doctor` can tell whether this script is pasted and current.
//
// Everything is found by header text or label, never by a fixed row or column. The pure helper
// functions at the bottom are unit-tested with node (tests/test_apps_script.py).

// Bump this whenever this file changes: `dfs doctor` warns when the sheet's stamp is older.
var DFS_SCRIPT_VERSION = 3;

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

var ID_HEADER = 'Id';
var POOL_HEADER = 'Pool';
var POOL_VALUES = ['Cash', 'GPP', 'Both'];
var ADD_LABEL = 'Add a player'; // the label cell of Player Pool's control row
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
 * Player Pool's "Add a player" box. The control row reads, left to right: a type dropdown in the Pool column,
 * the label, the input box. A helper in the hidden Id column of the same row holds what the box's text
 * resolves to (the sheet's own name resolver). When the edited cell is the input, add the player to
 * EdgeRaw's Pool as the type and clear the box. Returns true when the edit was in the box, handled or not.
 */
function handleAddPlayer_(range) {
  var sheet = range.getSheet();
  if (range.getRow() !== 1) return false;
  var top = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  var label = findColumn(top, ADD_LABEL);
  if (label === -1 || range.getColumn() !== label + 1) return false;
  var typed = String(range.getValue()).trim();
  if (typed === '') return true;
  SpreadsheetApp.flush(); // let the resolved-name formula settle
  var resolved = '';
  var idCol = findIdColumnBelow_(sheet);
  if (idCol !== -1) resolved = sheet.getRange(1, idCol).getValue();
  var name = chooseAddName(typed, resolved);
  var type = normalizeAddType(label > 1 ? sheet.getRange(1, label - 1).getValue() : '');
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
  range.clearContent();
  toast_('Added ' + name + ' (' + type + ').');
  return true;
}

/** The column whose header (in the rows just under the control row) reads Id, or -1. */
function findIdColumnBelow_(sheet) {
  var rows = Math.min(HEADER_SEARCH_ROWS, sheet.getLastRow() - 1);
  if (rows < 1) return -1;
  var below = sheet.getRange(2, 1, rows, sheet.getLastColumn()).getValues();
  for (var r = 0; r < below.length; r++) {
    var c = findColumn(below[r], ID_HEADER);
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
    normalizePoolValue: normalizePoolValue,
    normalizeAddType: normalizeAddType,
    chooseAddName: chooseAddName,
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
