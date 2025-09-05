# DFS Configuration Guide

All DFS suite settings are centralized in `config.json`. This document explains every configurable option.

## 📋 Configuration Structure

### 🔗 Google Sheets Integration
```json
{
  "google_sheets": {
    "sheet_id": "your-google-sheet-id",
    "credentials_file": "dfs-uploader-86ac915dfec5.json",
    "tab_mappings": {
      "fantasy_footballers": "Projections",
      "draftkings": "Salaries",
      "nfl_odds": "Odds"
    },
    "update_behavior": {
      "clear_before_upload": true,
      "create_missing_tabs": true,
      "batch_upload": true
    }
  }
}
```

**Options:**
- `sheet_id`: Your Google Sheets ID (from the URL)
- `credentials_file`: Path to your service account JSON file
- `tab_mappings`: Maps data sources to sheet tab names (customize these!)
- `clear_before_upload`: Remove existing data before uploading new data
- `create_missing_tabs`: Automatically create tabs if they don't exist
- `batch_upload`: Upload all data in one operation (faster)

### 📁 File Management
```json
{
  "file_management": {
    "downloads_folder": "downloads",
    "keep_timestamped_files": true,
    "max_age_minutes": 30,
    "content_sample_size": 500
  }
}
```

**Options:**
- `downloads_folder`: Where to organize CSV files
- `keep_timestamped_files`: Save versions with timestamps
- `max_age_minutes`: How old files can be to still be considered "recent"  
- `content_sample_size`: How many characters to read when identifying file types

### 🎯 Individual Scrapers

#### Fantasy Footballers
```json
{
  "scrapers": {
    "fantasy_footballers": {
      "url": "https://www.thefantasyfootballers.com/2025-ultimate-dfs-pass/dfs-pass-lineup-optimizer/",
      "browser_wait_time": 15,
      "download_timeout": 10,
      "automation_delay": 5
    }
  }
}
```

**Options:**
- `url`: Fantasy Footballers optimizer URL
- `browser_wait_time`: Seconds to wait for page load
- `download_timeout`: Seconds to wait for file download
- `automation_delay`: Delay between automation attempts

#### DraftKings
```json
{
  "scrapers": {
    "draftkings": {
      "api_url": "https://api.draftkings.com/draftgroups/v1/",
      "default_timeout": 30,
      "default_filename": "DraftKings Salaries.csv"
    }
  }
}
```

**Options:**
- `api_url`: DraftKings API endpoint for contests
- `default_timeout`: API request timeout in seconds
- `default_filename`: Expected download filename

#### NFL Odds
```json
{
  "scrapers": {
    "nfl_odds": {
      "base_url": "https://www.rotowire.com/betting/nfl/tables/nfl-games-by-market.php",
      "default_week": 1,
      "default_season": 2025,
      "min_week": 1,
      "max_week": 18,
      "default_timeout": 30
    }
  }
}
```

**Options:**
- `base_url`: Rotowire odds API endpoint  
- `default_week`: Week to scrape if not specified
- `default_season`: Season year
- `min_week`/`max_week`: Valid week range
- `default_timeout`: Request timeout in seconds

### 🔄 Workflow Settings
```json
{
  "workflows": {
    "upload_by_default": true,
    "continue_on_scraper_failure": true,
    "organize_files_after_scraping": true,
    "show_detailed_progress": true
  }
}
```

**Options:**
- `upload_by_default`: Auto-upload to Google Sheets unless `--no-upload` used
- `continue_on_scraper_failure`: Keep going if individual scrapers fail
- `organize_files_after_scraping`: Auto-organize files in downloads folder
- `show_detailed_progress`: Show verbose output during execution

### ⚙️ Advanced Settings
```json
{
  "advanced": {
    "retry_attempts": 3,
    "retry_delay_seconds": 2,
    "parallel_uploads": false,
    "debug_mode": false
  }
}
```

**Options:**
- `retry_attempts`: How many times to retry failed operations
- `retry_delay_seconds`: Delay between retry attempts
- `parallel_uploads`: Upload multiple files simultaneously (experimental)
- `debug_mode`: Enable detailed debugging output

## 🔧 Common Customizations

### Change Sheet Tab Names
Edit the `tab_mappings` section:
```json
{
  "google_sheets": {
    "tab_mappings": {
      "fantasy_footballers": "FF Projections",
      "draftkings": "DK Salaries", 
      "nfl_odds": "Betting Lines"
    }
  }
}
```

### Change Default NFL Week
Edit the scrapers section:
```json
{
  "scrapers": {
    "nfl_odds": {
      "default_week": 5
    }
  }
}
```

### Disable Auto-Upload
Edit workflows section:
```json
{
  "workflows": {
    "upload_by_default": false
  }
}
```

### Speed Up Browser Operations
Edit Fantasy Footballers settings:
```json
{
  "scrapers": {
    "fantasy_footballers": {
      "browser_wait_time": 10,
      "automation_delay": 2
    }
  }
}
```

## 📝 Template File

Use `config_template.json` as a starting point - it contains all available options with descriptions. Copy it to `config.json` and customize as needed.

## 🔒 Security Notes

- `config.json` is automatically git-ignored (won't be committed)
- Never share your `config.json` file (contains your Sheet ID)
- Keep credentials file secure and git-ignored
- Only grant minimal permissions to your Google service account

## 🧪 Testing Configuration

After making changes to `config.json`:

```bash
# Test upload configuration
python3 upload.py

# Test complete workflow
python3 run_all.py

# Test individual scrapers
cd scrapers/nfl_odds && python3 nfl_odds_scraper.py
```

Invalid configuration will show clear error messages pointing to the specific issue.