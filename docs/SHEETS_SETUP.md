# Google Sheets Setup Guide

## 1. Create Google Cloud Service Account

1. **Go to Google Cloud Console**: https://console.cloud.google.com
2. **Create or select a project**: "DFS-Data-Upload" (or any name you prefer)
3. **Enable Google Sheets API**:
   - Go to "APIs & Services" → "Library"
   - Search for "Google Sheets API"
   - Click "Enable"

4. **Create Service Account**:
   - Go to "APIs & Services" → "Credentials"
   - Click "Create Credentials" → "Service Account"
   - Name: `dfs-sheet-updater`
   - Click "Create and Continue"
   - Skip roles (click "Continue") 
   - Skip user access (click "Done")

5. **Download Credentials**:
   - Click on your new service account
   - Go to "Keys" tab
   - Click "Add Key" → "Create New Key" → "JSON"
   - Download the JSON file
   - **Rename it to `credentials.json`**
   - **Move it to your DFS project folder**: `/Users/Sam.Wildman/Documents/Dev/Personal/dfs/credentials.json`

## 2. Share Your Google Sheet

1. **Get the service account email**:
   - Open `credentials.json`
   - Find the `"client_email"` field (looks like: `dfs-bot@project-name.iam.gserviceaccount.com`)

2. **Share your Google Sheet**:
   - Open your Google Sheet
   - Click "Share" button
   - Add the service account email
   - Give it **"Editor"** access
   - Click "Send"

## 3. Configure Sheet ID

1. **Get your Sheet ID**:
   - From your Google Sheets URL: `https://docs.google.com/spreadsheets/d/SHEET_ID_HERE/edit`
   - Copy the long ID between `/d/` and `/edit`

2. **Configure Google Sheets settings**:
   - Edit `config.json` in the project root:
     ```json
     {
       "google_sheets": {
         "sheet_id": "your-actual-sheet-id-here",
         "credentials_file": "dfs-uploader-86ac915dfec5.json",
         "tab_mappings": {
           "fantasy_footballers": "Projections",
           "draftkings": "Salaries",
           "nfl_odds": "Odds"
         }
       }
     }
     ```

## 4. Test the Setup

```bash
# Test upload manually
python3 upload.py

# Test with complete workflow  
python3 run_all.py

# Test with quick update
python3 run_update.py
```

## Tab Names Expected

The uploader expects these tab names in your Google Sheet:
- **"Projections"** - Fantasy Footballers data
- **"Salaries"** - DraftKings salary data  
- **"Odds"** - NFL betting odds

If your tabs have different names, edit the `DEFAULT_TAB_MAPPINGS` in `utils/sheets_uploader.py`

## Security Notes

- ✅ `credentials.json` is automatically git-ignored
- ✅ Service account can ONLY access sheets you explicitly share
- ✅ Your personal Google credentials are never used
- ✅ You can revoke access anytime by unsharing the sheet

## Usage

```bash
# Default (with upload)
python3 run_all.py         # Scrape all + upload
python3 run_update.py      # Quick update + upload  
python3 upload.py          # Just upload existing CSVs

# Without upload  
python3 run_all.py --no-upload
python3 run_update.py --no-upload
```

## Troubleshooting

**"Sheet not found"**: Check that you shared the sheet with the service account email

**"Authentication failed"**: Make sure `credentials.json` is in the right location and valid

**"Tab not found"**: The uploader will create missing tabs automatically

**"Permission denied"**: Make sure the service account has "Editor" access to your sheet