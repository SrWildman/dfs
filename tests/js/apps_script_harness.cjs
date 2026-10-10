// A tiny in-memory SpreadsheetApp, enough to run Code.gs's onEdit end to end under node.
// usage: node apps_script_harness.cjs <Code.gs path> <scenario json path>   (prints the final state as JSON)
const fs = require('fs');
const vm = require('vm');

const scenario = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const toasts = [];

class Validation {
  constructor(values, strict) { this.values = values; this.strict = strict !== false; }
  getCriteriaType() { return 'VALUE_IN_LIST'; }
  getCriteriaValues() { return [this.values]; }
}

class Sheet {
  constructor(name, data) {
    this.name = name;
    this.cells = {}; // "r,c" -> {value, formula, validation}
    (data.rows || []).forEach((row, r) => row.forEach((v, c) => {
      if (v !== null && v !== undefined && v !== '') this.set(r + 1, c + 1, { value: v });
    }));
    (data.formulas || []).forEach(f => this.set(f.row, f.col, { value: f.value, formula: f.formula }));
    (data.validations || []).forEach(v => { this.cell(v.row, v.col).validation = new Validation(v.values, v.strict); });
    this.lastRow = data.lastRow; this.lastCol = data.lastCol;
  }
  key(r, c) { return r + ',' + c; }
  cell(r, c) { const k = this.key(r, c); if (!this.cells[k]) this.cells[k] = {}; return this.cells[k]; }
  set(r, c, obj) { this.cells[this.key(r, c)] = Object.assign(this.cell(r, c), obj); }
  getName() { return this.name; }
  getParent() { return ss; }
  getLastRow() { return this.lastRow; }
  getLastColumn() { return this.lastCol; }
  getRange(r, c, nr, nc) { return new Range(this, r, c, nr || 1, nc || 1); }
}

class Range {
  constructor(sheet, r, c, nr, nc) { this.sheet = sheet; this.r = r; this.c = c; this.nr = nr; this.nc = nc; }
  getSheet() { return this.sheet; }
  getRow() { return this.r; }
  getColumn() { return this.c; }
  getLastRow() { return this.r + this.nr - 1; }
  getNumRows() { return this.nr; }
  getNumColumns() { return this.nc; }
  eachCell(fn) { for (let i = 0; i < this.nr; i++) for (let j = 0; j < this.nc; j++) fn(this.r + i, this.c + j, i, j); }
  getValue() { const x = this.sheet.cells[this.sheet.key(this.r, this.c)]; return x && x.value !== undefined ? x.value : ''; }
  getValues() { const out = []; for (let i = 0; i < this.nr; i++) { const row = []; for (let j = 0; j < this.nc; j++) { const x = this.sheet.cells[this.sheet.key(this.r + i, this.c + j)]; row.push(x && x.value !== undefined ? x.value : ''); } out.push(row); } return out; }
  getDataValidations() { const out = []; for (let i = 0; i < this.nr; i++) { const row = []; for (let j = 0; j < this.nc; j++) { const x = this.sheet.cells[this.sheet.key(this.r + i, this.c + j)]; row.push(x && x.validation ? x.validation : null); } out.push(row); } return out; }
  setValue(v) { this.eachCell((r, c) => { const x = this.sheet.cell(r, c); x.value = v; delete x.formula; }); return this; }
  // Apps Script checks a formula's result against a STRICT dropdown; the scenario's `strictRejects` makes the mock
  // behave as that did for a blank result (Sam's A741), so a non-strict cell or a lifted rule lets it through.
  setFormula(f) {
    this.eachCell((r, c) => {
      const x = this.sheet.cell(r, c);
      if (scenario.strictRejects && x.validation && x.validation.strict) {
        throw new Error('The data you entered in cell ' + String.fromCharCode(64 + c) + r + ' violates the data validation rules set on this cell.');
      }
    });
    this.eachCell((r, c) => { const x = this.sheet.cell(r, c); x.formula = f; x.value = '(formula)'; });
    return this;
  }
  getDataValidation() { const x = this.sheet.cells[this.sheet.key(this.r, this.c)]; return x && x.validation ? x.validation : null; }
  clearDataValidations() { this.eachCell((r, c) => { delete this.sheet.cell(r, c).validation; }); return this; }
  setDataValidation(rule) { this.eachCell((r, c) => { this.sheet.cell(r, c).validation = rule; }); return this; }
  clearContent() { this.eachCell((r, c) => { const x = this.sheet.cell(r, c); delete x.value; delete x.formula; }); return this; }
  createTextFinder(text) {
    const self = this; let exact = false;
    const finder = {
      matchEntireCell(b) { exact = b; return finder; },
      findNext() {
        let hit = null;
        self.eachCell((r, c) => { const v = String(self.sheet.cell(r, c).value === undefined ? '' : self.sheet.cell(r, c).value);
          if (!hit && (exact ? v === text : v.indexOf(text) !== -1)) hit = new Range(self.sheet, r, c, 1, 1); });
        return hit;
      }
    };
    return finder;
  }
}

const sheets = {};
Object.keys(scenario.sheets).forEach(n => { sheets[n] = new Sheet(n, scenario.sheets[n]); });
const ss = {
  getSheetByName: n => sheets[n] || null,
  toast: (m) => toasts.push(m),
  getRangeByName: () => null
};
const sandbox = {
  SpreadsheetApp: {
    getActiveSpreadsheet: () => ss,
    flush: () => {},
    DataValidationCriteria: { VALUE_IN_LIST: 'VALUE_IN_LIST' },
    getUi: () => ({ createMenu: () => ({ addItem() { return this; }, addToUi() {} }) })
  },
  console
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
scenario.edits.forEach(e => {
  const sheet = sheets[e.sheet];
  const rng = sheet.getRange(e.row, e.col, e.rows || 1, 1);
  (e.values || []).forEach((v, i) => { if (v === null) sheet.cell(e.row + i, e.col).value = undefined; else sheet.cell(e.row + i, e.col).value = v; });
  // a typed pick overwrites the formula, as Sheets does
  rng.eachCell((r, c) => { delete sheet.cell(r, c).formula; });
  sandbox.onEdit({ range: rng });
});
const out = { toasts, sheets: {} };
Object.keys(sheets).forEach(n => {
  out.sheets[n] = {};
  Object.keys(sheets[n].cells).forEach(k => { const x = sheets[n].cells[k]; out.sheets[n][k] = { value: x.value === undefined ? null : x.value, formula: x.formula || null, validated: !!x.validation }; });
});
console.log(JSON.stringify(out));
