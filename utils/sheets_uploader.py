#!/usr/bin/env python3
"""
Google Sheets Upload Utility

Simple, reusable module for uploading DFS CSV data to Google Sheets.
Uses service account authentication for secure, automated uploads.

Usage:
    from utils.sheets_uploader import SheetsUploader

    uploader = SheetsUploader("credentials.json", "your-sheet-id")
    uploader.upload_all_dfs_data()
"""

import os
import pandas as pd
import gspread
from google.auth import default
from google.auth.exceptions import DefaultCredentialsError
from pathlib import Path
from typing import Dict, List, Optional


class SheetsUploader:
    """
    Handles Google Sheets uploads for DFS data.

    Authenticates using service account and uploads CSV files to specific tabs.
    """

    # Default tab mappings for DFS data sources
    DEFAULT_TAB_MAPPINGS = {
        'fantasy_footballers': 'Projections',
        'draftkings': 'Salaries',
        'nfl_odds': 'Odds'
    }

    def __init__(self, credentials_path: str, sheet_id: str, tab_mappings: Dict[str, str] = None):
        """
        Initialize the Google Sheets uploader.

        Args:
            credentials_path: Path to Google service account JSON file
            sheet_id: Google Sheets ID (from the sheet URL)
            tab_mappings: Optional custom tab mappings (defaults to DEFAULT_TAB_MAPPINGS)
        """
        self.credentials_path = Path(credentials_path)
        self.sheet_id = sheet_id
        self.tab_mappings = tab_mappings or self.DEFAULT_TAB_MAPPINGS
        self.client = None
        self.sheet = None

        # Initialize connection
        self._authenticate()

    def _authenticate(self):
        """
        Authenticate with Google Sheets using service account credentials.
        """
        try:
            if not self.credentials_path.exists():
                raise FileNotFoundError(
                    f"Credentials file not found: {self.credentials_path}\n"
                    "Please ensure you have downloaded your Google service account JSON file."
                )

            # Authenticate using service account
            self.client = gspread.service_account(filename=self.credentials_path)
            self.sheet = self.client.open_by_key(self.sheet_id)

            print(f"✅ Connected to Google Sheet: {self.sheet.title}")

        except DefaultCredentialsError as e:
            raise Exception(f"Authentication failed: {e}")
        except gspread.exceptions.APIError as e:
            raise Exception(f"Google Sheets API error: {e}")
        except Exception as e:
            raise Exception(f"Failed to connect to Google Sheets: {e}")

    def upload_csv_to_tab(self, csv_path: Path, tab_name: str) -> bool:
        """
        Upload a CSV file to a specific Google Sheets tab.

        Args:
            csv_path: Path to the CSV file
            tab_name: Name of the sheet tab to update

        Returns:
            bool: True if upload successful, False otherwise
        """
        try:
            if not csv_path.exists():
                print(f"⏭️  Skipping {tab_name}: CSV file not found ({csv_path.name})")
                return False

            # Read CSV file
            df = pd.read_csv(csv_path)

            if df.empty:
                print(f"⏭️  Skipping {tab_name}: CSV file is empty")
                return False

            # Handle NaN and infinite values
            df = df.fillna('')  # Replace NaN with empty strings
            df = df.replace([float('inf'), float('-inf')], '')  # Replace inf with empty strings
            df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col) # Trim Whitespace

            # Get or create the worksheet
            try:
                worksheet = self.sheet.worksheet(tab_name)
            except gspread.WorksheetNotFound:
                print(f"📋 Creating new tab: {tab_name}")
                worksheet = self.sheet.add_worksheet(title=tab_name, rows=1000, cols=26)

            # Clear existing data
            worksheet.clear()

            # Convert DataFrame to list of lists (including headers)
            data = [df.columns.tolist()] + df.values.tolist()

            # Upload data in batch
            worksheet.update(range_name='A1', values=data)

            print(f"✅ {tab_name}: Uploaded {len(df)} rows")
            return True

        except Exception as e:
            print(f"❌ {tab_name}: Upload failed - {e}")
            return False

    def get_available_csvs(self) -> Dict[str, Path]:
        """
        Find available DFS CSV files to upload.

        Returns:
            Dict mapping source names to CSV file paths
        """
        available_files = {}

        # Check for each expected CSV file
        downloads_dir = Path(__file__).parent.parent / "downloads"

        for source, tab_name in self.tab_mappings.items():
            # Look for the latest CSV file for this source
            source_dir = downloads_dir / source
            latest_file = source_dir / f"{source.replace('_', '-')}_latest.csv"

            if latest_file.exists():
                available_files[source] = latest_file

        return available_files

    def upload_all_dfs_data(self) -> Dict[str, bool]:
        """
        Upload all available DFS CSV files to their respective Google Sheets tabs.

        Returns:
            Dict mapping source names to upload success status
        """
        print("📤 Starting Google Sheets upload...")

        available_files = self.get_available_csvs()

        if not available_files:
            print("⚠️  No CSV files found to upload")
            return {}

        print(f"📊 Found {len(available_files)} file(s) to upload")

        results = {}

        for source, csv_path in available_files.items():
            tab_name = self.tab_mappings[source]
            success = self.upload_csv_to_tab(csv_path, tab_name)
            results[source] = success

        # Summary
        successful_uploads = sum(results.values())
        total_files = len(results)

        print(f"\n📊 Upload Summary:")
        print(f"✅ {successful_uploads} of {total_files} files uploaded successfully")

        if successful_uploads == total_files:
            print(f"🏆 All uploads completed! Sheet: {self.sheet.url}")
        elif successful_uploads > 0:
            print(f"⚠️  Partial success - check individual file status above")
        else:
            print(f"❌ All uploads failed - check error messages above")

        return results

    def get_sheet_url(self) -> str:
        """
        Get the URL of the Google Sheet.

        Returns:
            str: Google Sheets URL
        """
        return self.sheet.url if self.sheet else ""


def validate_credentials(credentials_path: str) -> bool:
    """
    Validate that credentials file exists and has proper permissions.

    Args:
        credentials_path: Path to credentials JSON file

    Returns:
        bool: True if credentials are valid and secure
    """
    cred_path = Path(credentials_path)

    if not cred_path.exists():
        print(f"❌ Credentials file not found: {credentials_path}")
        print("💡 Please download your Google service account JSON file")
        return False

    # Check file permissions (warn if too open)
    file_stat = cred_path.stat()
    file_mode = oct(file_stat.st_mode)[-3:]

    if file_mode != '600':  # Not owner read/write only
        print(f"⚠️  Warning: Credentials file has permissive permissions ({file_mode})")
        print(f"💡 Consider running: chmod 600 {credentials_path}")

    return True