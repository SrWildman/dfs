#!/usr/bin/env python3
"""
DFS File Management Utilities

Shared functions for organizing downloaded DFS files.
Used by run_all.py and run_update.py workflows.
"""

import subprocess
import sys
from pathlib import Path

def organize_downloads(quiet=False):
    """
    Organize downloaded CSV files into project structure.

    Args:
        quiet (bool): If True, suppress output messages

    Returns:
        bool: True if successful, False otherwise
    """
    if not quiet:
        print("🔄 Organizing downloaded files...")

    try:
        utils_dir = Path(__file__).parent
        manage_script = utils_dir / "manage_downloads.py"
        project_root = utils_dir.parent

        # Run the file management script
        result = subprocess.run([sys.executable, str(manage_script)],
                              cwd=project_root,
                              capture_output=quiet,  # Suppress output if quiet
                              timeout=120)

        success = result.returncode == 0

        if not quiet:
            if success:
                print("✅ Files organized successfully!")
            else:
                print("⚠️  File organization had issues")

        return success

    except subprocess.TimeoutExpired:
        if not quiet:
            print("⏰ File organization timed out")
        return False
    except Exception as e:
        if not quiet:
            print(f"❌ File organization failed: {e}")
        return False

def show_organization_summary():
    """Show a brief summary of organized files."""
    try:
        project_root = Path(__file__).parent.parent
        downloads_dir = project_root / "downloads"

        if not downloads_dir.exists():
            print("📁 No organized files yet")
            return

        # Count files in each category
        categories = ['fantasy_footballers', 'draftkings', 'nfl_odds']
        file_counts = {}

        for category in categories:
            category_dir = downloads_dir / category
            if category_dir.exists():
                csv_files = list(category_dir.glob("*.csv"))
                latest_file = category_dir / f"{category.replace('_', '-')}_latest.csv"
                file_counts[category] = {
                    'total': len(csv_files),
                    'has_latest': latest_file.exists()
                }
            else:
                file_counts[category] = {'total': 0, 'has_latest': False}

        print("\n📊 Organized Files Summary:")
        for category, counts in file_counts.items():
            name = category.replace('_', ' ').title()
            status = "✅" if counts['has_latest'] else "❌"
            print(f"   {status} {name}: {counts['total']} files")

        # Check for upload manifest
        manifest_file = downloads_dir / "upload_manifest.json"
        manifest_status = "✅" if manifest_file.exists() else "❌"
        print(f"   {manifest_status} Upload Manifest: {'Ready' if manifest_file.exists() else 'Not found'}")

    except Exception as e:
        print(f"⚠️  Could not show summary: {e}")

if __name__ == "__main__":
    # Allow running this module directly for testing
    print("🧪 Testing file organization...")
    success = organize_downloads(quiet=False)
    if success:
        show_organization_summary()
    else:
        print("❌ Test failed")