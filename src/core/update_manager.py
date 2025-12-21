"""Update manager for RVU Counter - handles version checking and updates."""

import os
import sys
import json
import logging
import subprocess
import urllib.request
from datetime import datetime
from typing import Optional, Tuple

from .config import APP_VERSION, GITHUB_OWNER, GITHUB_REPO, HELPERS_FOLDER
from .platform_utils import get_app_root

logger = logging.getLogger(__name__)

class UpdateManager:
    """Manages application updates via GitHub Releases."""
    
    def __init__(self):
        self.current_version = APP_VERSION.split(' ')[0]
        self.repo_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
        self.app_root = get_app_root()
        
    def check_for_updates(self) -> Tuple[bool, Optional[dict]]:
        """Check if a newer version is available.
        
        Returns:
            (is_available, release_info)
        """
        try:
            logger.info(f"Checking for updates at {self.repo_url}")
            
            # Add User-Agent to avoid 403 from GitHub
            req = urllib.request.Request(
                self.repo_url,
                headers={'User-Agent': 'RVU-Counter-Updater'}
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status != 200:
                    logger.error(f"GitHub API returned status {response.status}")
                    return False, None
                
                release_info = json.loads(response.read().decode())
                latest_version = release_info.get("tag_name", "").lstrip('v')
                
                if not latest_version:
                    logger.warning("No version tag found in latest release")
                    return False, None
                
                logger.info(f"Current version: {self.current_version}, Latest version: {latest_version}")
                
                # Simple semantic version comparison (v1.7 > v1.6)
                if self._is_newer(latest_version, self.current_version):
                    logger.info("New version available!")
                    return True, release_info
                
                return False, None
                
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
            return False, None
            
    def _is_newer(self, latest: str, current: str) -> bool:
        """Compare version strings."""
        try:
            l_parts = [int(x) for x in latest.split('.')]
            c_parts = [int(x) for x in current.split('.')]
            return l_parts > c_parts
        except:
            return latest > current

    def download_update(self, release_info: dict, progress_callback=None) -> Optional[str]:
        """Download the latest executable from the release.
        
        Args:
            release_info: GitHub release info dictionary
            progress_callback: Optional callback function(current_bytes, total_bytes)
        
        Returns:
            Path to downloaded file or None if failed.
        """
        try:
            assets = release_info.get("assets", [])
            exe_asset = None
            
            # Look for RVU Counter.exe or RVU.Counter.exe in assets (GitHub renames spaces to dots)
            for asset in assets:
                asset_name = asset.get("name", "")
                if asset_name in ["RVU Counter.exe", "RVU.Counter.exe"]:
                    exe_asset = asset
                    break
            
            if not exe_asset:
                logger.error("No 'RVU Counter.exe' or 'RVU.Counter.exe' found in release assets")
                return None
            
            download_url = exe_asset.get("browser_download_url")
            file_size = exe_asset.get("size", 0)
            temp_path = os.path.join(self.app_root, HELPERS_FOLDER, "RVU Counter.new.exe")
            
            logger.info(f"Downloading update from {download_url} to {temp_path} (size: {file_size} bytes)")
            
            # Ensure helpers folder exists
            os.makedirs(os.path.dirname(temp_path), exist_ok=True)
            
            req = urllib.request.Request(
                download_url,
                headers={'User-Agent': 'RVU-Counter-Updater'}
            )
            
            with urllib.request.urlopen(req, timeout=60) as response:
                total_size = int(response.headers.get('content-length', file_size))
                downloaded = 0
                chunk_size = 8192
                
                with open(temp_path, 'wb') as out_file:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        out_file.write(chunk)
                        downloaded += len(chunk)
                        
                        # Report progress
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)
            
            logger.info("Download complete.")
            return temp_path
            
        except Exception as e:
            logger.error(f"Error downloading update: {e}")
            return None

    def start_update_process(self, new_exe_path: str):
        """Launch the sidecar updater script and exit the application."""
        try:
            updater_bat = os.path.join(self.app_root, HELPERS_FOLDER, "updater.bat")
            
            # Create the updater script if it doesn't exist
            self._ensure_updater_script(updater_bat)
            
            logger.info(f"Launching updater script: {updater_bat}")
            
            # Launch detached process
            if sys.platform == 'win32':
                # Use cmd.exe to call the batch file with CREATE_NEW_CONSOLE
                subprocess.Popen(
                    f'cmd.exe /c start "RVU Update" "{updater_bat}"',
                    shell=True,
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
            
            # Exit the main app
            sys.exit(0)
            
        except Exception as e:
            logger.error(f"Error starting update process: {e}")

    def _ensure_updater_script(self, path: str):
        """Ensure the batch script exists - ALWAYS extract from current bundle to get latest version."""
        # Always extract from bundle to ensure we have the latest updater script
        logger.info(f"Extracting latest updater script from bundle to {path}")
        try:
            if getattr(sys, 'frozen', False):
                # Running as frozen exe - extract from _MEIPASS
                bundle_dir = sys._MEIPASS
                bundled_updater = os.path.join(bundle_dir, HELPERS_FOLDER, "updater.bat")
                
                if os.path.exists(bundled_updater):
                    # Ensure helpers folder exists
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    
                    # Copy updater.bat from bundle to app directory (overwrite existing)
                    with open(bundled_updater, 'r') as src:
                        content = src.read()
                    with open(path, 'w') as dst:
                        dst.write(content)
                    
                    logger.info(f"Extracted updater script to {path}")
                else:
                    logger.error(f"Bundled updater not found at {bundled_updater}")
            else:
                # Running from source - copy from source helpers folder
                source_updater = os.path.join(self.app_root, HELPERS_FOLDER, "updater.bat")
                if os.path.exists(source_updater):
                    logger.info(f"Updater script already exists in source at {source_updater}")
                else:
                    logger.error(f"Updater script not found at {source_updater}")
        except Exception as e:
            logger.error(f"Error extracting updater script: {e}")

__all__ = ['UpdateManager']
