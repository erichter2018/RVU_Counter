"""YAML Update Manager - handles automatic updates of rvu_rules.yaml"""

import os
import re
import logging
import urllib.request
from typing import Optional, Tuple

from .config import GITHUB_OWNER
from .platform_utils import get_app_root

logger = logging.getLogger(__name__)

class YamlUpdateManager:
    """Manages automatic updates of rvu_rules.yaml from GitHub."""
    
    # GitHub raw content URL for the yaml file
    YAML_REPO = "RVU-Releases"  # Use the releases repository
    YAML_FILE_PATH = "rvu_rules.yaml"  # File at root of repo
    
    def __init__(self):
        self.app_root = get_app_root()
        self.settings_folder = os.path.join(self.app_root, "settings")
        self.local_yaml_path = os.path.join(self.settings_folder, "rvu_rules.yaml")
        self.raw_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{self.YAML_REPO}/main/{self.YAML_FILE_PATH}"
        
    def get_local_version(self) -> Optional[str]:
        """Extract version from local rvu_rules.yaml file.
        
        Returns:
            Version string (e.g., "1.0") or None if not found
        """
        try:
            if not os.path.exists(self.local_yaml_path):
                logger.info("Local rvu_rules.yaml not found in settings folder")
                return None
                
            with open(self.local_yaml_path, 'r', encoding='utf-8') as f:
                # Read first 10 lines looking for version comment
                for i, line in enumerate(f):
                    if i >= 10:
                        break
                    # Look for: # Version: 1.0
                    match = re.search(r'#\s*Version:\s*(\S+)', line, re.IGNORECASE)
                    if match:
                        version = match.group(1).strip()
                        logger.info(f"Local yaml version: {version}")
                        return version
                        
            logger.warning("No version found in local rvu_rules.yaml")
            return None
            
        except Exception as e:
            logger.error(f"Error reading local yaml version: {e}")
            return None
    
    def get_remote_version(self) -> Optional[str]:
        """Fetch version from GitHub rvu_rules.yaml.
        
        Returns:
            Version string (e.g., "1.0") or None if not found
        """
        try:
            logger.info(f"Checking remote yaml version at {self.raw_url}")
            
            req = urllib.request.Request(
                self.raw_url,
                headers={'User-Agent': 'RVU-Counter-YAML-Updater'}
            )
            
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status != 200:
                    logger.error(f"GitHub returned status {response.status}")
                    return None
                
                # Read first 1KB to find version (no need to download entire file)
                header = response.read(1024).decode('utf-8')
                
                # Look for version in header
                match = re.search(r'#\s*Version:\s*(\S+)', header, re.IGNORECASE)
                if match:
                    version = match.group(1).strip()
                    logger.info(f"Remote yaml version: {version}")
                    return version
                    
                logger.warning("No version found in remote yaml")
                return None
                
        except Exception as e:
            logger.error(f"Error checking remote yaml version: {e}")
            return None
    
    def check_for_updates(self) -> Tuple[bool, Optional[str], Optional[str]]:
        """Check if a newer yaml version is available.
        
        Returns:
            (is_available, local_version, remote_version)
        """
        local_ver = self.get_local_version()
        remote_ver = self.get_remote_version()
        
        # If we can't get remote version, assume no update
        if remote_ver is None:
            return False, local_ver, None
        
        # If no local yaml, download is needed
        if local_ver is None:
            logger.info("No local yaml found, update needed")
            return True, None, remote_ver
        
        # Compare versions
        if self._is_newer(remote_ver, local_ver):
            logger.info(f"YAML update available: {local_ver} -> {remote_ver}")
            return True, local_ver, remote_ver
        
        logger.info("YAML is up to date")
        return False, local_ver, remote_ver
    
    def _is_newer(self, remote: str, local: str) -> bool:
        """Compare version strings (simple semantic versioning)."""
        try:
            r_parts = [int(x) for x in remote.split('.')]
            l_parts = [int(x) for x in local.split('.')]
            return r_parts > l_parts
        except:
            # Fallback to string comparison
            return remote > local
    
    def download_yaml(self) -> bool:
        """Download the latest rvu_rules.yaml from GitHub.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"Downloading yaml from {self.raw_url}")
            
            req = urllib.request.Request(
                self.raw_url,
                headers={'User-Agent': 'RVU-Counter-YAML-Updater'}
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.status != 200:
                    logger.error(f"GitHub returned status {response.status}")
                    return False
                
                yaml_content = response.read().decode('utf-8')
                
                # Ensure settings folder exists
                os.makedirs(self.settings_folder, exist_ok=True)
                
                # Write to settings folder
                with open(self.local_yaml_path, 'w', encoding='utf-8') as f:
                    f.write(yaml_content)
                
                logger.info(f"Successfully downloaded yaml to {self.local_yaml_path}")
                return True
                
        except Exception as e:
            logger.error(f"Error downloading yaml: {e}")
            return False
    
    def update_if_needed(self) -> bool:
        """Check for updates and download if available.
        
        Returns:
            True if yaml was updated, False otherwise
        """
        try:
            is_available, local_ver, remote_ver = self.check_for_updates()
            
            if is_available:
                logger.info(f"Updating yaml from {local_ver} to {remote_ver}")
                if self.download_yaml():
                    logger.info("YAML update successful!")
                    return True
                else:
                    logger.error("YAML update failed")
                    return False
            
            return False
            
        except Exception as e:
            logger.error(f"Error in yaml update process: {e}")
            return False

__all__ = ['YamlUpdateManager']



