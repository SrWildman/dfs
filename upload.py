#!/usr/bin/env python3
"""
DFS Google Sheets Upload

A standalone utility for uploading Daily Fantasy Sports (DFS) CSV data to Google Sheets.
This script provides both standalone execution and programmatic integration capabilities
for seamless data pipeline integration.

Features:
    - Automated CSV file discovery and processing
    - Google Sheets API integration with error handling
    - Configurable sheet and tab mappings
    - Comprehensive upload status reporting
    - Support for multiple data sources (DraftKings, Fantasy Footballers, NFL Odds)
    - Batch upload optimization

Data Processing:
    - Automatically detects available CSV files in organized structure
    - Maps data to appropriate Google Sheets tabs based on configuration
    - Handles data validation and format conversion
    - Provides detailed upload statistics and error reporting

Usage:
    # Standalone execution
    python3 upload.py

    # Programmatic integration
    from upload import upload_to_sheets
    success = upload_to_sheets()

Configuration:
    - Edit config.json with your Google Sheet ID and tab mappings
    - Ensure credentials.json exists in the project root
    - Verify Google Sheets API is enabled for your project

Requirements:
    - Google Sheets API credentials (credentials.json)
    - Proper configuration in config.json
    - CSV files in organized project structure

Examples:
    python3 upload.py  # Upload all available DFS data

Performance:
    - Typical upload time: 10-30 seconds depending on data volume
    - Supports batch operations for efficiency
    - Includes automatic retry logic for transient failures
"""

import sys
from typing import NoReturn

from utils.workflow import upload_to_sheets


def main() -> NoReturn:
    """
    Execute the standalone Google Sheets upload workflow.

    This function provides a clean interface for uploading all available
    DFS CSV data to Google Sheets with comprehensive error handling and
    user feedback.

    The upload process includes:
    1. Display of professional header and branding
    2. Execution of the upload workflow with error handling
    3. Comprehensive success/failure reporting
    4. Appropriate exit codes for automation integration

    Exits:
        0: Upload completed successfully
        1: Upload failed or encountered errors

    Raises:
        SystemExit: Always exits after completion (success or failure)

    Note:
        This function is designed for standalone execution and will
        always terminate the program with an appropriate exit code.
    """
    # Display professional header
    print("🏈 DFS Google Sheets Upload")
    print("=" * 40)

    # Execute upload workflow
    success = upload_to_sheets()

    # Provide comprehensive status reporting
    if success:
        print("\n🎉 Upload process completed successfully!")
        print("📊 All available data has been synchronized to Google Sheets")
        sys.exit(0)
    else:
        print("\n❌ Upload process failed")
        print("🔧 Please check configuration and try again")
        print("💡 See error messages above for specific details")
        sys.exit(1)


if __name__ == "__main__":
    main()