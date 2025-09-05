# DraftKings Player Salary Scraper

Automatically downloads NFL player salary data from DraftKings with dynamic contest detection.

## Features

- ✅ **Dynamic Contest Detection** - Automatically finds current week's main NFL slate
- ✅ **No Hardcoded IDs** - Contest IDs change weekly and are detected via API
- ✅ **Multiple Contest Options** - Can list all available contests
- ✅ **Authenticated Downloads** - Handles login and CSV download automatically

## Setup

1. **Make sure you're logged into DraftKings** in your browser

2. **Install requirements**:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Download Current Week's Player Salaries
```bash
python3 draftkings_scraper.py
```

### List Available Contests (No Login Required)
```bash
python3 draftkings_scraper.py --list
```

### Use Command Line Credentials
```bash
python3 draftkings_scraper.py --email your@email.com --password yourpass
```

### Run in Headless Mode
```bash
python3 draftkings_scraper.py --headless
```

### Custom Download Directory
```bash
python3 draftkings_scraper.py --download-dir /path/to/downloads
```

## How It Works

1. **Contest Detection**: Queries DraftKings API to get all NFL contests
2. **Main Slate Selection**: Finds contest with most games (usually Sunday main slate)
3. **Dynamic URL Building**: Constructs CSV download URL with current week's IDs
4. **Authentication**: Uses Selenium to log into DraftKings
5. **CSV Download**: Navigates to CSV URL and downloads player salary file

## Output

- **CSV File**: Contains all NFL player names and salaries for the current week
- **Log File**: `draftkings_scraper.log` with detailed execution logs
- **Screenshots**: Debug screenshots saved to download directory

## Current Week Detection

The scraper automatically detects:
- **Draft Group 123288**: 16 games (Full Week 1 slate)
- **CSV URL**: `https://www.draftkings.com/lineup/getavailableplayerscsv?contestTypeId=145&draftGroupId=123288`

IDs will change each week and are detected automatically!

## Examples

```bash
# Quick download
python3 draftkings_scraper.py

# Check what contests are available first
python3 draftkings_scraper.py --list

# Download with custom settings
python3 draftkings_scraper.py --headless --download-dir ./downloads
```

## Notes

- **No Navigation Needed**: Direct CSV download after authentication
- **Week Agnostic**: Works for any NFL week automatically
- **Contest Selection**: Prioritizes contests with most games
- **Error Handling**: Comprehensive logging and screenshot debugging