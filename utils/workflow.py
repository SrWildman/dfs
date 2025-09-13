#!/usr/bin/env python3
"""
DFS Workflow Management Utilities

Workflow orchestration system for DFS data pipelines.
"""

import traceback

try:
    # When imported as a module from main scripts
    from utils.config import get_google_sheets_config, validate_google_sheets_config
    from utils.file_manager import organize_downloads, show_organization_summary
    from utils.sheets_uploader import SheetsUploader, validate_credentials
except ImportError:
    # When run directly from utils directory
    from config import get_google_sheets_config, validate_google_sheets_config
    from file_manager import organize_downloads, show_organization_summary
    from sheets_uploader import SheetsUploader, validate_credentials


def organize_files() -> bool:
    """Execute file organization workflow for DFS data management."""
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
    """Execute Google Sheets upload workflow for DFS data synchronization."""
    try:
        print("\n🔄 Google Sheets Upload...")

        sheets_config = get_google_sheets_config()
        if not validate_google_sheets_config(sheets_config):
            print("💡 Check your config.json file and ensure proper Google Sheets setup")
            return False

        # Validate API credentials and permissions
        if not validate_credentials(sheets_config['credentials_file']):
            print("💡 Ensure credentials.json is valid and has proper permissions")
            return False

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
    """Print a standardized workflow header."""
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
    """Print the quick update header and description."""
    print("🔄 DFS Quick Update - Projections & Odds")
    print("=" * 45)
    print("📊 Running frequently updated data sources only...")
    print("💡 Skipping DraftKings salaries (updated weekly)")
    print("⚠️  Note: Projections requires one manual click")
    print()


def print_update_summary(successful, total, upload_success=False, upload_skipped=False):
    """Print summary for update workflow."""
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
    """Print final workflow summary and next steps."""
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
    print("🧪 Testing workflow utilities...")
    print_workflow_header("Test Workflow")

    sheets_config = get_google_sheets_config()
    if sheets_config:
        print("✅ Configuration loaded successfully")
    else:
        print("❌ Configuration loading failed")

    print("✅ Workflow utilities test completed")