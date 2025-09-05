#!/usr/bin/env python3
"""
DFS Complete Workflow - Collect & Organize

A comprehensive data pipeline for Daily Fantasy Sports (DFS) data collection and management.
This script orchestrates the complete workflow including data scraping, file organization,
and optional Google Sheets integration.

Features:
    - Automated CSV cleanup for new week data refresh
    - Multi-source data collection (Fantasy Footballers, DraftKings, NFL Odds)
    - Intelligent file organization and management
    - Google Sheets integration with error handling
    - Comprehensive progress reporting and error logging

Usage:
    python3 run_all.py [--no-upload]

Arguments:
    --no-upload: Skip automatic Google Sheets upload (optional)

Examples:
    python3 run_all.py                  # Complete workflow with upload
    python3 run_all.py --no-upload     # Data collection only

Author: Claude Code Assistant
Version: 2.0
"""

import argparse

from utils.config import get_scraper_configs
from utils.csv_cleanup import clear_old_csvs
from utils.scraper_runner import run_scrapers, print_results_summary
from utils.workflow import (
    organize_files,
    print_final_summary,
    print_workflow_header,
    upload_to_sheets,
)


def main() -> bool:
    """
    Execute the complete DFS data collection and organization workflow.

    This function coordinates the entire pipeline:
    1. Parses command line arguments for configuration
    2. Performs CSV cleanup to remove stale data
    3. Executes all data collection scrapers
    4. Organizes downloaded files into proper structure
    5. Optionally uploads data to Google Sheets
    6. Provides comprehensive status reporting

    Returns:
        bool: True if all critical components completed successfully,
              False if any essential step failed

    Raises:
        SystemExit: If argument parsing fails (handled by argparse)
    """
    # Configure and parse command line arguments
    parser = argparse.ArgumentParser(
        description='DFS Complete Workflow - Collect & Organize',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='For more information, see the project documentation.'
    )
    parser.add_argument(
        '--no-upload',
        action='store_true',
        help='Skip automatic Google Sheets upload'
    )
    args = parser.parse_args()

    # Display workflow header
    print_workflow_header()

    # Step 1: Clear old CSV data for new week
    cleanup_success = clear_old_csvs()
    if not cleanup_success:
        print("⚠️  CSV cleanup encountered issues, but continuing with workflow...")

    # Step 2: Execute all data collection scrapers
    scrapers = get_scraper_configs()
    results = run_scrapers(scrapers)
    successful, total = print_results_summary(results, "Collection Summary")

    # Step 3: Organize downloaded files
    organize_success = organize_files()

    # Step 4: Optional Google Sheets upload
    upload_success = False
    if not args.no_upload:
        upload_success = upload_to_sheets()
    else:
        print("📊 Google Sheets upload skipped (--no-upload flag)")

    # Step 5: Display comprehensive summary
    print_final_summary(
        successful,
        total,
        organize_success,
        upload_success,
        args.no_upload
    )

    return successful == total and organize_success


if __name__ == "__main__":
    main()