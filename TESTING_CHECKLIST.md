# DFS Manager Backend & Frontend Testing Checklist
*Fill in boxes as you complete each test. Focus on verifying what works before moving to next phase.*

## 📋 Instructions
1. **Start full stack first**: `npm run dev` in project root (starts both Tauri backend + Vue frontend)
2. **For API debugging only**: Use `cargo tauri dev` in src-tauri directory
3. **The app uses Tauri IPC, NOT HTTP** — curl to `localhost:14273` will fail
4. **Mark PASS/FAIL** and add brief notes in provided spaces
5. **If a test fails**: Fix issue before proceeding
6. **Save progress**: Consider git committing after major verification phases

---

## 🔧 Phase 1: Tauri Foundation Testing ✅ COMPLETE
*Test the desktop app builds and launches correctly*

### 1. Verify Frontend TypeScript Build
[X] **PASS**  [ ] **FAIL**  
**Steps**:
1. Run `npm run build` — must complete without TypeScript errors
2. Verify all 5 views compile (Dashboard, Scrapers, Sheets, Scheduling, Lineup Builder)
**Success**:
- ✅ vue-tsc type check passes
- ✅ vite build succeeds (166 kB JS bundle, 4.7 kB CSS)
- ✅ No TypeScript errors
**Notes**: 
________________________________________________________

### 2. Verify Node Version & Build Environment
[X] **PASS**  [ ] **FAIL**  
**Steps**:
1. Check `node --version` — should be 18+ (LTS for `styleText` API)
2. Run `npm run build` — vite build succeeds
3. Check `cargo check` in src-tauri — no new errors
**Success**:
- Node 18+ (LTS) confirmed
- Frontend builds cleanly
- Rust backend compiles without new errors
**Notes**: ________________________________________________________  
________________________________________________________

### 3. Launch Full App & Verify UI
[X] **PASS**  [ ] **FAIL**  
**Steps**:
1. Run `npm run dev` — starts Tauri backend + Vue frontend
2. Open the app window
3. Confirm Dashboard loads and all 5 navbar links work
**Success**: App launches; dashboard, scrapers, sheets, scheduling, and lineup builder views are accessible
**Notes**: ________________________________________________________  
________________________________________________________

### 4. Verify Scraper Status API via UI
[X] **PASS**  [ ] **FAIL**  
**Steps**:
1. Navigate to Scrapers view in the app
2. Click "Refresh" — backend status should populate
3. Check: scraper cards show status, last run times, progress bars
**Success**:
- Scrapers list populates from backend
- Status cards update with live data
- Progress bars and error messages display correctly
**Notes**: ________________________________________________________  
________________________________________________________

### 5. Verify Sheets Connection UI
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Navigate to Sheets view in the app
2. Click "Test Connection" button
3. Check: Connection status updates, recent activity feed shows
**Success**:
- Connection status updates (connected/disconnected)
- Recent activity displays with sync/upload entries
- Indigo mapping icons render correctly
**Notes**: ________________________________________________________  
________________________________________________________

### 6. Verify Scheduler UI Controls
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Navigate to Scheduling view in the app
2. Click "Start Scheduler" — status should change
3. Click "Stop Scheduler" — status should revert
**Success**:
- Scheduler toggle buttons work
- Status displays "Running" or "Stopped" correctly
- Next run / last run times update
**Notes**: ________________________________________________________  
________________________________________________________

### 7. Verify Lineup Builder UI
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Navigate to Lineup Builder view in the app
2. Click "Load Data" — player pool should populate
3. Check: Player table renders, search works, Generate Lineup button functional
**Success**:
- Player pool loads without TypeScript errors
- Search/filter functionality works
- Lineup generation produces 9-player lineup
- Position totals display correctly
**Notes**: ________________________________________________________  
________________________________________________________

---

## 🔧 Phase 2: Scraper Integration Testing
*Test that the Python scrapers are accessible and the Rust backend spawns them correctly*

### 1. Verify Scraper Scripts Exist
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Check `../scrapers/fantasy_footballers/scraper.py` exists
2. Check `../scrapers/nfl_odds/nfl_odds_scraper.py` exists
3. Check `../scrapers/draftkings/` directory exists
4. Check `../scrapers/tffb_sos/scraper.py` exists
**Success**: All 4 scraper directories and entry points are present
**Notes**: ________________________________________________________  
________________________________________________________

### 2. Test Scraper Execution (Fantasy Footballers)
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. In Scrapers view UI, click "Run" on Fantasy Footballers (Projections)
2. Wait 10-30 seconds
3. Check status updates: idle → running → completed/failed
4. Check terminal/console for Python script output
**Success**:
- Button states transition correctly
- Progress bar advances
- Status updates to "completed" within timeout
- CSV file written to `../downloads/` directory
**Notes**: ________________________________________________________  
________________________________________________________

### 3. Test Week Parameter Functionality
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. In Scrapers view, find NFL odds scraper
2. Verify week parameter is passed correctly
3. Run scraper with specific week (e.g., week 3)
**Success**: Backend logs show command includes `--week 3` parameter
**Notes**: ________________________________________________________  
________________________________________________________

### 4. Verify Scraper CSV Output
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Run Fantasy Footballers scraper
2. Check `../downloads/projections_*.csv` file exists
3. Open file and verify column structure
**Success**:
- CSV file is generated
- Columns include player name, team, position, projection, etc.
- File is readable by pandas/csv module
**Notes**: ________________________________________________________  
________________________________________________________

---

## ⚙️ Phase 3: Feature Validation
*Test advanced implemented features*

### 1. Test Scheduler Start/Stop
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Navigate to Scheduling view
2. Click "Start Scheduler" — button changes to "Stop"
3. Verify status changes to "Running"
4. Click "Stop Scheduler" — status reverts to "Stopped"
**Success**:
- Scheduler toggle works bidirectionally
- Status updates immediately
- No errors in backend
**Notes**: ________________________________________________________  
________________________________________________________

### 2. Verify Scheduled Jobs Configuration
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. View scheduled jobs list (Monday Morning Refresh, Daily Odds Update)
2. Click "Pause" / "Resume" on a job
3. Check status badges update
**Success**:
- Job states toggle correctly
- Backend persists state changes
**Notes**: ________________________________________________________  
________________________________________________________

### 3. Test Google Sheets Integration
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Verify `config.json` has correct Google Sheets ID
2. Check `credentials.json` exists for service account
3. Click "Test Connection" in UI
4. Click "Upload to Sheets" (if connection works)
**Success**:
- Connection test passes
- Upload runs without panic
- `lastSync` timestamp updates
- Spreadsheet tab data populates
**Notes**: ________________________________________________________  
________________________________________________________

### 4. Test Lineup Generation Algorithm
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Load data in Lineup Builder
2. Set salary cap to 50000 (DraftKings)
3. Click "Generate Lineup"
4. Verify lineup has 9 players (1QB/2RB/3WR/1TE/1FLEX/1DST)
5. Check total salary is ≤ 50000
**Success**:
- Lineup contains valid position distribution
- Salary fits under cap
- Total projected points displayed
- Value rating calculated (pts/$1000)
**Notes**: ________________________________________________________  
________________________________________________________

---

## 🛡️ Phase 4: Error Handling & Edge Cases
*Test robustness*

### 1. Test Invalid Scraper Name
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. In UI, try to run a non-existent scraper (modify scraperName map)
2. Or trigger error via DevTools console
**Success**: Returns error message, NO panic/crash in app
**Notes**: ________________________________________________________  
________________________________________________________

### 2. Test Missing Scraper Script
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Temporarily rename: `mv ../scrapers/fantasy_footballers/scraper.py ../scrapers/fantasy_footballers/scraper.py.bak`
2. Try to run scraper from UI
3. Restore: `mv ../scrapers/fantasy_footballers/scraper.py.bak ../scrapers/fantasy_footballers/scraper.py`
**Success**: Error message displayed in UI, app continues functioning
**Notes**: ________________________________________________________  
________________________________________________________

### 3. Test Duplicate Scraper Execution
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Start a long-running scraper
2. Immediately try to run same scraper again
**Success**:
- Second run returns "Scraper is already running" error
- Only one execution proceeds
- No race conditions
**Notes**: ________________________________________________________  
________________________________________________________

### 4. Test Empty Data State
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Clear `../downloads/` directory
2. Navigate to Lineup Builder, click "Load Data"
3. Verify graceful empty state (no crash)
**Success**: 
- Empty player pool shown
- "No players match your search" message
- App remains functional
**Notes**: ________________________________________________________  
________________________________________________________

---

## 🔍 Phase 5: Polish & Verification
*Final validation checks*

### 1. Confirm No Compilation Warnings
[ ] **PASS**  [ ] **FAIL**  
**Steps**: In `src-tauri` directory:  
```bash
cargo check
```
**Success**:
- Output ends with: `Finished dev [unoptimized + debuginfo] target(s) in X.XXs`
- Shows: `0 warnings and 0 errors` (or only pre-existing warnings)
**Notes**: ________________________________________________________  
________________________________________________________

### 2. Validate Binary Size/Performance
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Check size: `ls -lh target/release/bundle/macos/*.app`
2. Time app launch from cold start
**Success**:
- App bundle size: 10-50MB range
- Startup time: <2 seconds to interactive UI
- No immediate segfaults/panics on startup
**Notes**: ________________________________________________________  
________________________________________________________

### 3. Full System Smoke Test
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Start full app: `npm run dev`
2. Perform basic workflow:
   - Click: Test Connection (Sheets)
   - Click: Run Scraper (fantasy_footballers)
   - Click: Start Scheduler
   - Navigate to Lineup Builder
   - Generate a lineup
   - Wait 15 seconds
   - Check: All status updated
**Success**:
- No crash/freeze during workflow
- All functions respond appropriately
- Data flows: UI → Backend → Scraper → Status → UI
**Notes**: ________________________________________________________  
________________________________________________________

### 4. Test All 5 Routes
[ ] **PASS**  [ ] **FAIL**  
**Steps**:
1. Click each nav link: Dashboard, Scrapers, Sheets, Scheduling, Lineup Builder
2. Verify each view loads without errors
3. Check DevTools console for warnings
**Success**:
- All 5 routes render correctly
- No 404s for missing components
- No Vue warnings about undefined props
**Notes**: ________________________________________________________  
________________________________________________________

---

## ✅ Signs You're Ready to Move Forward
You can confidently proceed to next development phase when:
- [x] Phase 1.1 (TypeScript build) shows **PASS** ✅
- [x] Phase 1.3 (App launches) shows **PASS** ✅
- [ ] At least 80% of Phase 2 tests show **PASS** (scraper integration)
- [ ] You've successfully run a complete workflow: UI → Backend → Scraper → Status → UI
- [ ] Binary builds cleanly with `cargo check` showing only pre-existing warnings
- [ ] You can start/stop the backend multiple times without issues

## 📝 Testing Tips
- **Test in Order**: Don't skip Phase 1 - foundation must be solid before UI testing
- **One Change at a Time**: If testing fails, revert to last known good state
- **Use Terminal as Primary Truth**: Backend logs show what's actually happening
- **Save Working States**: Git commit after each major verification
- **Check Both Endpoints**: Verify Tauri IPC commands are registered AND reachable
- **Note Timing**: Some operations (scrapers, scheduler) take time - wait appropriately

---

*Last Updated: 2026-09-04 — Phase 1 TypeScript foundation verified*
*Keep going. Make it great.* 🚀