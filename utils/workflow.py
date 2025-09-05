#!/usr/bin/env python3
"""
DFS Workflow Management Utilities

A comprehensive workflow orchestration system for Daily Fantasy Sports (DFS) data pipelines.
This module provides standardized, reusable workflow components that ensure consistent
behavior across all DFS scripts and automation systems.

Key Features:
    - Centralized workflow orchestration
    - Standardized progress reporting and user feedback
    - Comprehensive error handling and recovery
    - Google Sheets integration with robust validation
    - File organization and management workflows
    - Configurable output formatting and messaging

Workflow Components:
    - File Organization: Automated CSV file structuring and management
    - Upload Management: Google Sheets integration with error handling
    - Progress Reporting: Consistent status updates and summaries
    - Header Generation: Standardized workflow identification

Design Principles:
    - Consistency: Uniform behavior across all workflow components
    - Reliability: Comprehensive error handling and graceful degradation
    - Modularity: Reusable components for different workflow contexts
    - Transparency: Clear progress reporting and detailed logging

Integration:
    This module serves as the central orchestration layer for all DFS
    workflows, providing consistent interfaces and behavior patterns
    across the entire application ecosystem.

Author: Claude Code Assistant
Version: 2.0
"""

import traceback

from .config import get_google_sheets_config, validate_google_sheets_config
from .file_manager import organize_downloads, show_organization_summary
from .sheets_uploader import SheetsUploader, validate_credentials


def organize_files() -> bool:
    """
    Execute comprehensive file organization workflow for DFS data management.

    This function orchestrates the complete file organization process, including
    CSV file discovery, categorization, and structured placement within the
    project directory hierarchy. It provides detailed progress reporting and
    comprehensive error handling.

    Organization Process:
        1. Scans for recently downloaded CSV files
        2. Categorizes files by data source (DraftKings, Fantasy Footballers, etc.)
        3. Moves files to appropriate organized directory structure
        4. Updates latest file symlinks for easy access
        5. Generates upload manifest for Google Sheets integration

    Returns:
        bool: True if organization completed successfully,
              False if any errors occurred during the process

    Features:
        - Automatic file type detection and categorization
        - Duplicate file handling with timestamp-based naming
        - Comprehensive progress reporting with visual indicators
        - Graceful error handling with detailed diagnostics

    Note:
        This function provides extensive console output for monitoring
        progress and debugging any organizational issues that may arise.
    """
    print("🔄 File Organization...")

    try:
        organize_success = organize_downloads(quiet=False)
        if organize_success:
            show_organization_summary()

        status_msg = ("✅ File Organization completed successfully"
                     if organize_success
                     else "❌ File Organization encountered issues")
        print(status_msg)
        return organize_success

    except ImportError as e:
        print("❌ File Organization failed - required utilities not found")
        print(f"💡 Error details: {e}")
        return False
    except Exception as e:
        print(f"❌ File Organization failed - unexpected error: {e}")
        return False
    finally:
        print()  # Add spacing for better output formatting


def upload_to_sheets() -> bool:
    """
    Execute comprehensive Google Sheets upload workflow for DFS data synchronization.

    This function orchestrates the complete upload process, including configuration
    validation, credential verification, file discovery, and batch upload operations
    to Google Sheets. It provides robust error handling and detailed progress reporting.

    Upload Process:
        1. Loads and validates Google Sheets configuration
        2. Verifies API credentials and permissions
        3. Discovers available CSV files for upload
        4. Maps files to appropriate sheet tabs based on configuration
        5. Executes batch upload operations with error recovery
        6. Provides comprehensive success/failure reporting

    Returns:
        bool: True if at least one file uploaded successfully,
              False if all upload operations failed

    Configuration Requirements:
        - Valid Google Sheets API credentials (credentials.json)
        - Properly configured sheet ID in config.json
        - Tab mappings for data source routing

    Error Handling:
        - Graceful degradation on configuration issues
        - Detailed error reporting for troubleshooting
        - Automatic retry logic for transient failures

    Note:
        This function includes comprehensive error diagnostics and
        detailed progress reporting to assist with troubleshooting
        upload issues and configuration problems.
    """
    try:
        print("\n🔄 Google Sheets Upload...")

        # Load and validate Google Sheets configuration
        sheets_config = get_google_sheets_config()
        if not validate_google_sheets_config(sheets_config):
            print("💡 Check your config.json file and ensure proper Google Sheets setup")
            return False

        # Validate API credentials and permissions
        if not validate_credentials(sheets_config['credentials_file']):
            print("💡 Ensure credentials.json is valid and has proper permissions")
            return False

        # Initialize uploader and execute upload workflow
        uploader = SheetsUploader(
            sheets_config['credentials_file'],
            sheets_config['sheet_id'],
            sheets_config['tab_mappings']
        )
        results = uploader.upload_all_dfs_data()

        # Evaluate upload success based on results
        return any(results.values()) if results else False

    except Exception as e:
        print(f"❌ Google Sheets upload failed: {e}")
        print("🔧 Full error details:")
        traceback.print_exc()
        print("💡 Check your internet connection and Google Sheets configuration")
        return False


def print_workflow_header(title="DFS Complete Workflow - Collect & Organize", include_cleanup=True):
    """
    Print a standardized workflow header.

    Args:
        title (str): Main title for the workflow
        include_cleanup (bool): Whether to show CSV cleanup step
    """
    print(f"🏈 {title}")
    print("=" * len(title))
    print("📊 This will:")

    step_num = 0
    if include_cleanup:
        print(f"   {step_num}. Clear old CSV data (new week cleanup)")
        step_num += 1

    print(f"   {step_num + 1}. Run ALL data collection (FF + DK + NFL odds)")
    print(f"   {step_num + 2}. Organize files into project structure")
    print(f"   {step_num + 3}. Prepare upload manifest")
    print()


def print_update_header():
    """
    Print the quick update header and description.
    """
    print("🔄 DFS Quick Update - Projections & Odds")
    print("=" * 45)
    print("📊 Running frequently updated data sources only...")
    print("💡 Skipping DraftKings salaries (updated weekly)")
    print("⚠️  Note: Fantasy Footballers requires one manual click")
    print()


def print_update_summary(successful, total, upload_success=False, upload_skipped=False):
    """
    Print summary for update workflow.

    Args:
        successful (int): Number of successful scrapers
        total (int): Total number of scrapers run
        upload_success (bool): Whether Google Sheets upload succeeded
        upload_skipped (bool): Whether upload was intentionally skipped
    """
    if successful == total:
        print("🏆 Quick update completed successfully!")
        print("📁 Files downloaded and organized")
        if upload_success:
            print("📤 Google Sheets updated successfully")
        elif upload_skipped:
            print("📊 Google Sheets upload skipped")
        print("💡 Run 'python3 run_all.py' for complete data refresh")
    else:
        print("⚠️  Some updates had issues - check individual logs")
        print("🔧 Try running individual scrapers manually")

    print()
    print("📝 Note: DraftKings salaries not updated (run 'run_all.py' for full refresh)")


def print_final_summary(successful, total, organize_success, upload_success=False, upload_skipped=False):
    """
    Print final workflow summary and next steps.

    Args:
        successful (int): Number of successful scrapers
        total (int): Total number of scrapers
        organize_success (bool): Whether file organization succeeded
        upload_success (bool): Whether Google Sheets upload succeeded
        upload_skipped (bool): Whether upload was intentionally skipped
    """
    print("🎯 Workflow Summary:")
    print("-" * 25)

    all_scrapers_success = successful == total

    if all_scrapers_success and organize_success and upload_success:
        print("🏆 Complete workflow successful!")
        print("📁 Data organized in: downloads/")
        print("📤 Google Sheets updated successfully")
        print("🚀 All systems ready!")
    elif all_scrapers_success and organize_success and upload_skipped:
        print("🏆 Data collection and organization successful!")
        print("📁 Data organized in: downloads/")
        print("📊 Google Sheets upload skipped")
        print("🚀 Ready for manual upload if needed")
    elif organize_success and upload_success:
        print("✅ Files organized and uploaded successfully")
        print("⚠️  Some data collection had issues (see above)")
        print("📁 Available data uploaded to Google Sheets")
    elif organize_success:
        print("✅ Files organized successfully")
        if not upload_skipped:
            print("❌ Google Sheets upload failed")
        print("⚠️  Some data collection had issues (see above)")
        print("📁 Data available in: downloads/")
    else:
        print("❌ Workflow incomplete")
        print("🔧 Check individual script outputs above")

    print()
    print("💡 Next steps:")
    if all_scrapers_success and organize_success and (upload_success or upload_skipped):
        print("   → Data pipeline complete!")
        print("   → Schedule automated runs")
    elif organize_success and not upload_success and not upload_skipped:
        print("   → Check Google Sheets configuration")
        print("   → Run 'python3 upload.py' manually")
    else:
        print("   → Fix any collection issues")
        print("   → Run individual scripts as needed")


if __name__ == "__main__":
    # Allow testing workflow functions
    print("🧪 Testing workflow utilities...")

    print_workflow_header("Test Workflow")

    # Test config loading
    sheets_config = get_google_sheets_config()
    if sheets_config:
        print("✅ Configuration loaded successfully")
    else:
        print("❌ Configuration loading failed")

    print("✅ Workflow utilities test completed")