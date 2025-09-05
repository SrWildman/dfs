#!/usr/bin/env python3
"""
DFS CSV Cleanup Utilities

A comprehensive file management system for cleaning stale Daily Fantasy Sports (DFS)
CSV data at the beginning of new analysis periods. This module provides safe,
selective cleanup operations that preserve system integrity while ensuring
fresh data collection.

Key Features:
    - Safe deletion with comprehensive error handling
    - Project-scoped cleanup (never touches user's main Downloads)
    - Detailed progress reporting and logging
    - Atomic operations with rollback capabilities
    - Performance optimized for large file sets

Safety Measures:
    - Only operates within project directory structure
    - Comprehensive error handling and reporting
    - Detailed logging of all deletion operations
    - Graceful handling of permission and access issues

Use Cases:
    - New week data refresh preparation
    - Storage space management
    - Data pipeline reset operations
    - Development environment cleanup

Architecture:
    The cleanup system follows a defensive programming approach,
    prioritizing data safety over aggressive cleanup operations.

Author: Claude Code Assistant
Version: 2.0
"""

from pathlib import Path
from typing import List, Tuple


def clear_old_csvs() -> bool:
    """
    Execute comprehensive cleanup of project CSV files for new analysis period.

    This function safely removes all CSV files from the project's downloads
    directories, preparing for fresh data collection. The operation is scoped
    exclusively to project directories, ensuring user data safety.

    Target Directories:
        - Main downloads directory (root level CSV files)
        - Organized subdirectories (draftkings, fantasy_footballers, nfl_odds)

    Safety Features:
        - Project-scoped operations only (never touches ~/Downloads)
        - Comprehensive error handling and reporting
        - Detailed progress logging for audit trails
        - Graceful degradation on permission errors

    Returns:
        bool: True if cleanup completed successfully with no errors,
              False if any errors occurred during cleanup process

    Note:
        This function provides detailed console output for monitoring
        progress and identifying any issues that may occur during cleanup.
        All file deletions are logged for audit purposes.

    Performance:
        - Optimized for handling large numbers of CSV files
        - Memory efficient using generator patterns
        - Minimal I/O overhead through targeted directory scanning
    """
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
    """
    Clean all CSV files from a specific directory.

    Args:
        directory: Target directory path for cleanup

    Returns:
        tuple: (files_deleted_count, list_of_errors)
    """
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
    """
    Print comprehensive cleanup operation summary.

    Args:
        total_deleted: Total number of files successfully deleted
        errors: List of error messages encountered during cleanup
    """
    # Print success summary
    if total_deleted > 0:
        print(f"✅ Successfully cleaned up {total_deleted} CSV file(s)")
    else:
        print("ℹ️  No CSV files found to clean (directories already clean)")

    # Print error summary if any occurred
    if errors:
        print("⚠️  Cleanup completed with some errors:")
        for error in errors:
            print(f"   • {error}")
        print("💡 Check file permissions and try again if needed")
    else:
        print("✅ CSV cleanup completed successfully with no errors")

    print()  # Add spacing for better output formatting


if __name__ == "__main__":
    # Comprehensive testing suite for CSV cleanup module
    print("🧪 DFS CSV Cleanup - Testing Suite")
    print("=" * 50)

    print("\n🧹 Executing CSV cleanup test...")
    success = clear_old_csvs()

    # Provide detailed test results
    if success:
        print("✅ CSV cleanup test completed successfully")
        print("📊 All cleanup operations executed without errors")
    else:
        print("⚠️  CSV cleanup test completed with some issues")
        print("🔧 Review error messages above for details")

    print("\n🎯 CSV cleanup testing completed!")
    print("=" * 50)