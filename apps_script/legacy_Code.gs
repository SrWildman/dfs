// File: Code.gs
//
// LEGACY: the Apps Script bound to the sheet before the usability round, kept verbatim for the record.
// It is NOT used and must not be pasted back: it acts on the ACTIVE sheet (so run from Player Pool it
// wipes Player Pool's Name formulas) and clears column A of every row between the "Name" headers,
// including each block's Total and Remaining rows. apps_script/Code.gs replaces it.

/**
 * Clears only the "Name" column (column A) in each lineup block,
 * leaving all other columns and headers untouched.
 * Assumes each block starts with a header row containing "Name" in column A.
 */
function clearLineupNames() {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  const data = sheet.getDataRange().getValues();
  const headerText = "Name";
  let headerRows = [];

  // Find all header rows (where column A is "Name")
  for (let i = 0; i < data.length; i++) {
    if (data[i][0] === headerText) {
      headerRows.push(i);
    }
  }

  // For each block, clear only the "Name" column (A) in player rows
  for (let h = 0; h < headerRows.length; h++) {
    const start = headerRows[h] + 1; // first player row
    const end = (h + 1 < headerRows.length) ? headerRows[h + 1] : data.length;
    for (let r = start; r < end; r++) {
      // Only clear column A (index 0)
      sheet.getRange(r + 1, 1).clearContent();
    }
  }
}

/**
 * Optional: Adds a custom menu for easy access.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Lineup Tools')
    .addItem('Clear Lineup Names', 'clearLineupNames')
    .addToUi();
}
