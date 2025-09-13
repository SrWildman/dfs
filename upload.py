#!/usr/bin/env python3
"""
DFS Google Sheets Upload

Standalone utility for uploading DFS CSV data to Google Sheets.

Usage:
    python3 upload.py
"""

import sys
from typing import NoReturn

from utils.workflow import upload_to_sheets


def main() -> NoReturn:
    """Execute the standalone Google Sheets upload workflow."""
    print("🏈 DFS Google Sheets Upload")
    print("=" * 40)

    success = upload_to_sheets()

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