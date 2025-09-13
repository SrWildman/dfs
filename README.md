# DFS Data Collection Suite

Google Sheets Template: https://docs.google.com/spreadsheets/d/1ZSjMaRKRAXS-DmfOFePKaq_KemghmNQHsASSjttG97I/

Automated data collection pipeline for Daily Fantasy Sports (DFS) analysis. Collects player projections, salaries, and betting odds, then organizes and uploads to Google Sheets.

## 🚀 Quick Start

```bash
# Complete pipeline (scrape all + upload to Google Sheets)
python3 run_all.py

# Quick update (projections + odds only)
python3 run_update.py

# Skip Google Sheets upload
python3 run_all.py --no-upload
```

## 📊 Data Sources

| Source | Data Type | Update Frequency | Output |
|--------|-----------|------------------|---------|
| **Projections** | Player projections | Multiple times daily | Projections with ownership % |
| **DraftKings** | Player salaries | Weekly (usually Tuesday) | Current slate pricing |
| **NFL Odds** | Betting lines | Multiple times daily | Spreads, totals, moneylines |
| **Strength of Schedule** | Matchup analysis | Weekly | Position-specific defensive rankings |

## 🏗️ Project Structure

```
dfs/
├── 🚀 run_all.py                   # Complete workflow (all 4 scrapers + upload)
├── ⚡ run_update.py                 # Quick workflow (Projections + odds + upload)
├── 📤 upload.py                    # Standalone Google Sheets upload
├── 📋 requirements.txt             # Python dependencies
├── 📚 docs/                        # Documentation
│   ├── SHEETS_SETUP.md             # Google Sheets integration guide
│   └── draftkings_guide.md         # DraftKings setup notes
├── 📁 downloads/                   # Organized CSV output
│   ├── projections/               # Projections CSVs
│   ├── draftkings/                 # DK salary CSVs
│   ├── nfl_odds/                   # NFL odds CSVs
│   ├── sos/                        # Strength of Schedule CSVs
│   └── upload_manifest.json        # Upload status tracking
├── 🛠️ utils/                       # Core utilities
│   ├── sheets_uploader.py          # Google Sheets integration
│   ├── manage_downloads.py         # File organization
│   └── file_manager.py             # Shared file utilities
└── 🎯 scrapers/                    # Data collection modules
    ├── projections/
    ├── draftkings/
    ├── nfl_odds/
    └── tffb_sos/
```

## 📋 Setup

### 1. Install Dependencies
```bash
pip3 install -r requirements.txt
```

### 2. Google Sheets Integration (Optional)
See [`docs/SHEETS_SETUP.md`](docs/SHEETS_SETUP.md) for complete setup guide.

## 🔧 Usage

### Main Workflows

**Complete Pipeline:**
```bash
python3 run_all.py
# ✅ Runs all 4 scrapers (Projections, DraftKings, NFL Odds, Strength of Schedule)
# ✅ Auto-organizes files
# ✅ Uploads to Google Sheets
```

**Quick Update:**
```bash
python3 run_update.py
# ✅ Projections + NFL Odds only
# ✅ Auto-organizes files
# ✅ Uploads to Google Sheets
# ⏭️ Skips DraftKings & Strength of Schedule (updated weekly)
```

### Individual Operations

**Upload Only:**
```bash
python3 upload.py
# Uploads existing CSV files to Google Sheets
```

**Skip Upload:**
```bash
python3 run_all.py --no-upload
python3 run_update.py --no-upload
```

**Individual Scrapers:**
```bash
cd scrapers/projections && python3 scraper.py
cd scrapers/draftkings && python3 scraper.py
cd scrapers/nfl_odds && python3 nfl_odds_scraper.py
cd scrapers/tffb_sos && python3 scraper.py
```

## 📤 Google Sheets Integration

### Features
- ✅ **Automatic upload** after data collection
- ✅ **Secure authentication** using service accounts
- ✅ **Partial uploads** - handles missing files gracefully  
- ✅ **Tab management** - creates/updates specific tabs
- ✅ **No personal credentials** - uses dedicated service account

### Default Tab Mapping
| CSV Source | Google Sheets Tab |
|------------|-------------------|
| Projections | `Projections` |
| DraftKings | `Salaries` |
| NFL Odds | `Odds` |
| Strength of Schedule - QB | `SoSQB` |
| Strength of Schedule - RB | `SoSRB` |
| Strength of Schedule - WR | `SoSWr` |
| Strength of Schedule - TE | `SoSTE` |
| Strength of Schedule - D/ST | `SoSDef` |

### Setup
1. **Follow setup guide**: [`docs/SHEETS_SETUP.md`](docs/SHEETS_SETUP.md)
2. **Download credentials**: Save as `credentials.json` in project root
3. **Share your sheet**: With the service account email
4. **Set sheet ID**: Environment variable or edit `upload.py`

## 📂 Data Output

### File Organization
```
downloads/
├── projections/
│   ├── projections_latest.csv              # Always current
│   └── projections_YYYYMMDD_HHMM.csv        # Timestamped
├── draftkings/
│   ├── draftkings_latest.csv
│   └── draftkings_YYYYMMDD_HHMM.csv
├── nfl_odds/
│   ├── nfl-odds_latest.csv
│   └── nfl-odds_YYYYMMDD_HHMM.csv
└── sos/
    ├── sos-qb_latest.csv       # QB Strength of Schedule
    ├── sos-rb_latest.csv       # RB Strength of Schedule
    ├── sos-wr_latest.csv       # WR Strength of Schedule
    ├── sos-te_latest.csv       # TE Strength of Schedule
    ├── sos-dst_latest.csv      # D/ST Strength of Schedule
    └── sos-*_YYYYMMDD_HHMM.csv # Timestamped versions
```

### Data Formats

**Projections** (`projections`):
```csv
Id,Name,Position,Team,ProjPts,ProjOwn
39506991,Denver Broncos,DST,Broncos,10.70,8.90
```

**DraftKings** (`salaries`):
```csv
Position,Name + ID,Name,ID,Roster Position,Salary,Game Info,TeamAbbrev
QB,Josh Allen (123456),Josh Allen,123456,QB,8000,BUF@MIA 01/07 1:00PM ET,BUF
```

**NFL Odds** (`betting lines`):
```csv
Team,Date,Moneyline,Spread,Over-Under
Cowboys,2025-09-04 20:20:00,+320,+7.5,47.5
Eagles,2025-09-04 20:20:00,-410,-7.5,47.5
```

**Strength of Schedule** (`matchup analysis`):
```csv
Team,Opponent,Rank,Points_Allowed_Avg
Buffalo Bills,Miami Dolphins,1,18.2
Kansas City Chiefs,Cincinnati Bengals,2,19.5
```

## 🏈 Strength of Schedule Features

### Multi-Position Support
The system automatically scrapes Strength of Schedule data for all fantasy-relevant positions:
- **QB**: Quarterback matchup analysis
- **RB**: Running back defensive rankings
- **WR**: Wide receiver coverage analysis
- **TE**: Tight end matchup data
- **D/ST**: Defense/Special Teams rankings

### Automated Workflow
- **Separate Browser Windows**: Each position opens in its own Arc window for easy manual interaction
- **Position-Specific Files**: Each position generates its own CSV file (sos-qb_latest.csv, sos-rb_latest.csv, etc.)
- **Google Sheets Integration**: Uploads to separate tabs (SoSQB, SoSRB, SoSWr, SoSTE, SoSDef)
- **Organized Storage**: All SOS files stored in downloads/sos/ directory

### Usage
```bash
# Run full pipeline including SOS
python3 run_all.py

# Run SOS scraper individually
cd scrapers/tffb_sos && python3 scraper.py

# Run SOS for specific week
cd scrapers/tffb_sos && python3 scraper.py --week 3
```

## 🔍 Troubleshooting

### Common Issues

**Projections "Manual step required":**
- Script opens page in Arc browser
- Click "Projections" button with download icon
- Select "Projections" from dropdown

**Strength of Schedule "Manual step required":**
- Script opens separate Arc window for each position (QB, RB, WR, TE, D/ST)
- Select the correct position and week
- Click "More" → "Download CSV"
- Repeat for all 5 positions

**DraftKings "Authentication required":**
- Make sure you're logged into DraftKings
- Browser opens CSV URL automatically

**Google Sheets "Permission denied":**
- Verify sheet is shared with service account email
- Check credentials.json exists and is valid

**"No CSV files found":**
- Run individual scrapers first
- Check ~/Downloads folder for files
- Verify scrapers completed successfully

### Getting Help

1. **Check logs** - scripts show detailed progress
2. **Run individual components** - isolate the issue
3. **Verify credentials** - especially for Google Sheets
4. **Check file permissions** - ensure downloads folder is writable

## 🔧 Configuration

### Configuration File
All settings are centralized in `config.json`. Key sections include:

**Google Sheets Integration:**
```json
{
  "google_sheets": {
    "sheet_id": "your-actual-sheet-id-here",
    "credentials_file": "credentials.json",
    "tab_mappings": {
      "projections": "Projections",
      "draftkings": "Salaries",
      "nfl_odds": "Odds",
      "sos_qb": "SoSQB",
      "sos_rb": "SoSRB",
      "sos_wr": "SoSWr",
      "sos_te": "SoSTE",
      "sos_dst": "SoSDef"
    }
  }
}
```

**Scraper Settings:**
```json
{
  "scrapers": {
    "nfl_odds": {
      "default_week": 1,
      "default_season": 2025
    },
    "projections": {
      "browser_wait_time": 5
    },
    "tffb_sos": {
      "browser_wait_time": 5,
      "automation_delay": 1
    }
  }
}
```

**Workflow Behavior:**
```json
{
  "workflows": {
    "upload_by_default": true,
    "continue_on_scraper_failure": true
  }
}
```

See `config_template.json` for all available options with detailed descriptions.

## 🛡️ Security

### Google Sheets Credentials
- ✅ `credentials.json` automatically git-ignored
- ✅ Service account has minimal permissions
- ✅ Only accesses sheets you explicitly share
- ✅ Your personal Google account never used

### Best Practices
1. **Never commit credentials** to version control
2. **Use service accounts** for automation
3. **Limit sheet access** to what's needed

## 📋 Dependencies

```
selenium>=4.15.0       # Browser automation
webdriver-manager>=4.0.0  # Chrome driver management
requests>=2.25.0       # HTTP requests
gspread>=5.12.0        # Google Sheets API
google-auth>=2.23.0    # Google authentication
```

---

**Ready to collect some DFS data? Run `python3 run_all.py` to get started! 🚀**