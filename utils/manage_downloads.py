#!/usr/bin/env python3
"""
DFS Downloads Manager

Manages CSV files downloaded by scrapers:
1. Moves files from ~/Downloads to project downloads folder
2. Organizes by data source (fantasy_footballers, draftkings, nfl_odds)
3. Replaces old files with new ones (keeps latest version)
4. Provides upload preparation for future integration

Usage:
    python3 manage_downloads.py
"""

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

# Configuration constants
DEFAULT_MAX_AGE_MINUTES = 30  # Default time window for recent files
CONTENT_SAMPLE_SIZE = 500  # Characters to read for content analysis
DEFAULT_SCAN_WINDOW_MINUTES = 60  # Default scan window for main function
TIMESTAMP_FORMAT = "%Y%m%d_%H%M"  # Format for timestamped filenames

class DownloadsManager:
    """
    Manages CSV files downloaded by DFS scrapers.

    Provides functionality to:
    - Move files from ~/Downloads to organized project structure
    - Identify data sources automatically
    - Create timestamped files and latest symlinks
    - Generate upload manifests for future integration
    """

    def __init__(self):
        self.project_dir = Path(__file__).parent.parent  # Go up one level from utils/
        self.downloads_dir = self.project_dir / "downloads"
        self.system_downloads = Path.home() / "Downloads"

        self.sources = {
            'projections': self.downloads_dir / "projections",
            'draftkings': self.downloads_dir / "draftkings",
            'nfl_odds': self.downloads_dir / "nfl_odds",
            'sos': self.downloads_dir / "sos"
        }

        self._setup_directories()

    def _setup_directories(self):
        """
        Create download directories if they don't exist.

        Creates the main downloads directory and subdirectories for each data source.
        """
        self.downloads_dir.mkdir(exist_ok=True)
        for source_dir in self.sources.values():
            source_dir.mkdir(exist_ok=True)

    def find_recent_csv_files(self, max_age_minutes=DEFAULT_MAX_AGE_MINUTES):
        """
        Find CSV files downloaded in the last N minutes.

        Args:
            max_age_minutes (int): Maximum age in minutes for files to be considered recent

        Returns:
            list: List of file info dictionaries with path, name, size, modified time, and identified source
        """
        recent_files = []
        cutoff_time = time.time() - (max_age_minutes * 60)

        try:
            for file in self.system_downloads.glob("*.csv"):
                if file.stat().st_mtime > cutoff_time:
                    recent_files.append({
                        'path': file,
                        'name': file.name,
                        'size': file.stat().st_size,
                        'modified': file.stat().st_mtime,
                        'source': self._identify_source(file.name, file)
                    })
        except Exception as e:
            print(f"⚠️  Error scanning downloads: {e}")

        return sorted(recent_files, key=lambda x: x['modified'], reverse=True)

    def _check_content_patterns(self, filepath):
        """
        Check file content for data source identification patterns.

        Args:
            filepath (Path): Full path to the CSV file

        Returns:
            str or None: Source identifier if found, None if no patterns match
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read(CONTENT_SAMPLE_SIZE).lower()  # First N chars

            # SOS patterns (check first since it contains 'fantasy')
            if 'strength of schedule' in content or ('opp avg' in content and 'fpa' in content):
                return 'sos'
            
            # Projections patterns
            if any(pattern in content for pattern in ['projpts', 'projown', 'fantasy', 'footballers']):
                return 'projections'

            # DraftKings patterns
            if any(pattern in content for pattern in ['draftkings', 'salary', 'roster position']):
                return 'draftkings'

            # NFL odds patterns
            if any(pattern in content for pattern in ['spread', 'moneyline']) or ('total' in content and any(p in content for p in ['odds', 'line', 'bet'])):
                return 'nfl_odds'

        except Exception:
            pass

        return None

    def _check_filename_patterns(self, filename):
        """
        Check filename for data source identification patterns.

        Args:
            filename (str): Name of the CSV file

        Returns:
            str: Source identifier based on filename patterns
        """
        filename_lower = filename.lower()

        if 'strength of schedule' in filename_lower and 'fantasy' in filename_lower:
            return 'sos'
        elif any(pattern in filename_lower for pattern in ['projection', 'fantasy', 'footballers']):
            return 'projections'
        elif any(pattern in filename_lower for pattern in ['draftkings', 'dk', 'salaries']):
            return 'draftkings'
        elif any(pattern in filename_lower for pattern in ['odds', 'lines', 'betting']):
            return 'nfl_odds'

        return 'unknown'

    def _identify_source(self, filename, filepath):
        """
        Identify which data source a CSV file came from.

        Uses both filename patterns and file content analysis for accurate identification.

        Args:
            filename (str): Name of the CSV file
            filepath (Path): Full path to the CSV file

        Returns:
            str: Source identifier ('fantasy_footballers', 'draftkings', 'nfl_odds', or 'unknown')
        """
        # First try content analysis (more accurate)
        content_source = self._check_content_patterns(filepath)
        if content_source:
            return content_source

        # Fallback to filename patterns
        return self._check_filename_patterns(filename)

    def _extract_sos_position(self, filename):
        """
        Extract position from SOS filename.
        
        Args:
            filename (str): Name of the SOS CSV file
            
        Returns:
            str or None: Position (QB, RB, WR, TE, DST) if found, None otherwise
        """
        filename_upper = filename.upper()
        
        # Check for position patterns in filename
        if 'SOS_QB_' in filename_upper or '_QB_' in filename_upper:
            return 'QB'
        elif 'SOS_RB_' in filename_upper or '_RB_' in filename_upper:
            return 'RB'
        elif 'SOS_WR_' in filename_upper or '_WR_' in filename_upper:
            return 'WR'
        elif 'SOS_TE_' in filename_upper or '_TE_' in filename_upper:
            return 'TE'
        elif 'SOS_D/ST_' in filename_upper or '_DST_' in filename_upper or 'D%2FST' in filename_upper:
            return 'DST'
        
        return None

    def move_and_organize_files(self, files_to_move):
        """
        Move files to organized project structure.

        Creates timestamped copies and updates latest files for each data source.

        Args:
            files_to_move (list): List of file info dictionaries to process

        Returns:
            list: List of successfully moved file information
        """
        moved_files = []

        for file_info in files_to_move:
            source = file_info['source']
            original_path = file_info['path']

            if source == 'unknown':
                print(f"⚠️  Skipping unknown file: {file_info['name']}")
                continue

            if source not in self.sources:
                print(f"⚠️  Unknown source: {source}")
                continue

            # Determine destination
            dest_dir = self.sources[source]
            
            # Handle SOS position-specific files
            if source == 'sos':
                position = self._extract_sos_position(file_info['name'])
                if position:
                    base_name = f"sos-{position.lower()}"
                else:
                    base_name = "sos"
            else:
                base_name = source.replace('_', '-')

            timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
            dest_filename = f"{base_name}_{timestamp}.csv"
            dest_path = dest_dir / dest_filename

            # Also create a "latest" symlink/copy
            latest_path = dest_dir / f"{base_name}_latest.csv"

            try:
                # Move the file
                shutil.copy2(original_path, dest_path)

                # Update latest file
                if latest_path.exists():
                    latest_path.unlink()
                shutil.copy2(dest_path, latest_path)

                # Remove from system downloads
                original_path.unlink()

                moved_files.append({
                    'source': source,
                    'original': str(original_path),
                    'destination': str(dest_path),
                    'latest': str(latest_path),
                    'size': file_info['size']
                })

                print(f"✅ {source}: {file_info['name']} → {dest_filename}")

            except Exception as e:
                print(f"❌ Failed to move {file_info['name']}: {e}")

        return moved_files

    def create_upload_manifest(self, moved_files):
        """
        Create manifest for future upload integration.

        Generates a JSON manifest file listing all latest files ready for upload.

        Args:
            moved_files (list): List of recently moved files

        Returns:
            Path: Path to the created manifest file
        """
        manifest = {
            'timestamp': datetime.now().isoformat(),
            'files': []
        }

        # Add all latest files to manifest
        for source, source_dir in self.sources.items():
            latest_file = source_dir / f"{source.replace('_', '-')}_latest.csv"
            if latest_file.exists():
                manifest['files'].append({
                    'source': source,
                    'path': str(latest_file),
                    'size': latest_file.stat().st_size,
                    'modified': latest_file.stat().st_mtime,
                    'ready_for_upload': True
                })

        manifest_path = self.downloads_dir / "upload_manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        return manifest_path

    def show_status(self):
        """
        Show current status of organized files.

        Displays a summary of available files for each data source,
        including file counts, latest file timestamps, and upload readiness.
        """
        print("\n📁 Current DFS Data Files:")
        print("=" * 35)

        total_files = 0
        for source, source_dir in self.sources.items():
            files = list(source_dir.glob("*.csv"))
            latest_file = source_dir / f"{source.replace('_', '-')}_latest.csv"

            print(f"\n🏈 {source.replace('_', ' ').title()}:")

            if latest_file.exists():
                mod_time = datetime.fromtimestamp(latest_file.stat().st_mtime)
                size_mb = latest_file.stat().st_size / (1024 * 1024)
                print(f"   📄 Latest: {latest_file.name}")
                print(f"   📅 Updated: {mod_time.strftime('%Y-%m-%d %H:%M')}")
                print(f"   📊 Size: {size_mb:.1f} MB")
            else:
                print("   ❌ No data available")

            print(f"   📁 Total files: {len(files)}")
            total_files += len(files)

        print(f"\n🎯 Total CSV files: {total_files}")

        # Check for upload manifest
        manifest_path = self.downloads_dir / "upload_manifest.json"
        if manifest_path.exists():
            print(f"📋 Upload manifest: Ready ({manifest_path.name})")
        else:
            print("📋 Upload manifest: Not created")

def main():
    """
    Main function to manage downloads.

    Scans for recent CSV files, organizes them by data source,
    creates upload manifest, and displays status summary.

    Returns:
        bool: True if files were successfully organized, False otherwise
    """
    print("📁 DFS Downloads Manager")
    print("=" * 30)

    manager = DownloadsManager()

    # Find recent CSV files
    print("🔍 Scanning for recent CSV downloads...")
    recent_files = manager.find_recent_csv_files(max_age_minutes=DEFAULT_SCAN_WINDOW_MINUTES)  # Last hour

    if not recent_files:
        print("ℹ️  No recent CSV files found in Downloads folder")
        print("💡 Run scrapers first, then run this script")
        manager.show_status()
        return

    print(f"📊 Found {len(recent_files)} recent CSV file(s):")
    for file_info in recent_files:
        size_mb = file_info['size'] / (1024 * 1024)
        source = file_info['source']
        print(f"   📄 {file_info['name']} ({size_mb:.1f} MB) → {source}")

    # Move and organize files
    print(f"\n🔄 Moving files to organized structure...")
    moved_files = manager.move_and_organize_files(recent_files)

    if moved_files:
        print(f"\n✅ Successfully organized {len(moved_files)} file(s)")

        manifest_path = manager.create_upload_manifest(moved_files)
        print(f"📋 Upload manifest created: {manifest_path.name}")

        # Show current status
        manager.show_status()

        print(f"\n🚀 Files ready for future upload integration!")
        print(f"📁 All files in: {manager.downloads_dir}")

    else:
        print("⚠️  No files were moved")

    return len(moved_files) > 0

if __name__ == "__main__":
    main()