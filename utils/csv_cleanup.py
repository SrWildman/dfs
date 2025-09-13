#!/usr/bin/env python3
"""
DFS CSV Cleanup Utilities

Safe cleanup of project CSV files for new analysis periods.
"""

from pathlib import Path
from typing import List, Tuple


def clear_old_csvs() -> bool:
    """Execute cleanup of project CSV files for new analysis period."""
    print("🧹 Clearing old CSV data (new week cleanup)...")

    errors: List[str] = []
    total_deleted = 0

    # Define project-scoped cleanup directories
    project_root = Path(__file__).parent.parent
    cleanup_dirs = [
        project_root / "downloads",
        project_root / "downloads" / "draftkings",
        project_root / "downloads" / "fantasy_footballers",
        project_root / "downloads" / "nfl_odds",
    ]

    # Process each target directory
    for directory in cleanup_dirs:
        if not directory.exists():
            continue

        deleted_count, dir_errors = _cleanup_directory(directory)
        total_deleted += deleted_count
        errors.extend(dir_errors)

    # Generate comprehensive summary report
    _print_cleanup_summary(total_deleted, errors)

    return len(errors) == 0


def _cleanup_directory(directory: Path) -> Tuple[int, List[str]]:
    """Clean all CSV files from a specific directory."""
    deleted_count = 0
    errors: List[str] = []

    try:
        csv_files = list(directory.glob("*.csv"))

        for csv_file in csv_files:
            try:
                csv_file.unlink()
                deleted_count += 1
                print(f"   🗑️  Deleted: {csv_file.name}")
            except (OSError, PermissionError) as e:
                error_msg = f"Could not delete {csv_file.name}: {e}"
                errors.append(error_msg)
            except Exception as e:
                error_msg = f"Unexpected error deleting {csv_file.name}: {e}"
                errors.append(error_msg)

    except Exception as e:
        error_msg = f"Error scanning directory {directory}: {e}"
        errors.append(error_msg)

    return deleted_count, errors


def _print_cleanup_summary(total_deleted: int, errors: List[str]) -> None:
    """Print cleanup operation summary."""
    if total_deleted > 0:
        print(f"✅ Successfully cleaned up {total_deleted} CSV file(s)")
    else:
        print("ℹ️  No CSV files found to clean (directories already clean)")

    if errors:
        print("⚠️  Cleanup completed with some errors:")
        for error in errors:
            print(f"   • {error}")
        print("💡 Check file permissions and try again if needed")
    else:
        print("✅ CSV cleanup completed successfully with no errors")

    print()  # Add spacing for better output formatting


if __name__ == "__main__":
    print("🧪 DFS CSV Cleanup - Testing Suite")
    print("=" * 50)

    print("\n🧹 Executing CSV cleanup test...")
    success = clear_old_csvs()

    if success:
        print("✅ CSV cleanup test completed successfully")
    else:
        print("⚠️  CSV cleanup test completed with some issues")

    print("\n🎯 CSV cleanup testing completed!")
    print("=" * 50)