#!/usr/bin/env python3
"""
DFS Quick Update - Projections & Odds

A lightweight data refresh pipeline for Daily Fantasy Sports (DFS) that updates
only the most frequently changing data sources. This script is optimized for
mid-week updates when full data collection is unnecessary.

Features:
    - Selective scraper execution (Fantasy Footballers + NFL Odds only)
    - Automatic file organization and management
    - Optional Google Sheets integration
    - Optimized for speed and efficiency
    - Smart skipping of weekly data (DraftKings salaries)

Data Sources:
    ✅ Fantasy Footballers projections (updated multiple times per week)
    ✅ NFL odds data (changes throughout the week)
    ⏭️  DraftKings salaries (updated weekly, skipped for efficiency)

Usage:
    python3 run_update.py [--no-upload]

Arguments:
    --no-upload: Skip automatic Google Sheets upload (optional)

Examples:
    python3 run_update.py               # Quick update with upload
    python3 run_update.py --no-upload  # Update data only

Performance:
    - Typical execution time: 30-60 seconds
    - Reduces data collection time by ~50% vs full workflow
"""

import argparse

from utils.config import get_update_scrapers
from utils.scraper_runner import run_scrapers, print_results_summary
from utils.workflow import (
    organize_files,
    print_update_header,
    print_update_summary,
    upload_to_sheets,
)


def main() -> bool:
    """
    Execute the DFS quick update workflow for frequently changing data.

    This optimized workflow focuses on data sources that change multiple times
    per week, providing faster updates without full data collection overhead.

    Workflow steps:
    1. Parse command line arguments for configuration
    2. Execute selective data collection (Fantasy Footballers + NFL Odds)
    3. Organize downloaded files into proper structure
    4. Optionally upload fresh data to Google Sheets
    5. Provide update-specific status reporting

    Returns:
        bool: True if all update scrapers completed successfully,
              False if any scraper failed

    Raises:
        SystemExit: If argument parsing fails (handled by argparse)

    Note:
        This function intentionally skips DraftKings salary data since
        salaries typically only change once per week (usually Monday).
    """
    # Configure and parse command line arguments
    parser = argparse.ArgumentParser(
        description='DFS Quick Update - Projections & Odds',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Use "python3 run_all.py" for complete data refresh including DraftKings salaries.'
    )
    parser.add_argument(
        '--no-upload',
        action='store_true',
        help='Skip automatic Google Sheets upload'
    )
    args = parser.parse_args()

    # Display update-specific header
    print_update_header()

    # Step 1: Execute frequent update scrapers only
    scrapers = get_update_scrapers()
    results = run_scrapers(scrapers)
    successful, total = print_results_summary(results, "Update Summary")

    # Step 2: Organize downloaded files
    organize_files()

    # Step 3: Optional Google Sheets upload
    upload_success = False
    if not args.no_upload:
        upload_success = upload_to_sheets()

    # Step 4: Display update-specific summary
    print_update_summary(successful, total, upload_success, args.no_upload)

    return successful == total


if __name__ == "__main__":
    main()