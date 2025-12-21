"""Documentation manager for RVU Counter - handles self-healing documentation."""

import os
import logging
import urllib.request
from typing import List

from ..core.config import GITHUB_OWNER, GITHUB_REPO, BACKUP_BRANCH
from ..core.platform_utils import get_app_root

logger = logging.getLogger(__name__)

class DocManager:
    """Checks for and downloads missing documentation files."""
    
    DOC_FILES = [
        "README.md",
        "Body_Part_Organization_Plan.md",
        "WHATS_NEW_v1.7.md",
        "AUTO_UPDATE_DESIGN.md",
        "YAML_Migration.md"
    ]
    
    def __init__(self):
        self.root = get_app_root()
        self.doc_dir = os.path.join(self.root, "documentation")
        self.base_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BACKUP_BRANCH}/documentation"
        
    def ensure_docs(self, force: bool = False):
        """Check for missing docs and download them."""
        if not os.path.exists(self.doc_dir):
            os.makedirs(self.doc_dir, exist_ok=True)
            
        missing_files = []
        for filename in self.DOC_FILES:
            path = os.path.join(self.doc_dir, filename)
            if force or not os.path.exists(path):
                missing_files.append(filename)
                
        if not missing_files:
            return
            
        logger.info(f"Missing {len(missing_files)} documentation files. Downloading...")
        
        for filename in missing_files:
            try:
                url = f"{self.base_url}/{filename}"
                dest = os.path.join(self.doc_dir, filename)
                
                req = urllib.request.Request(
                    url,
                    headers={'User-Agent': 'RVU-Counter-Doc-Healer'}
                )
                
                with urllib.request.urlopen(req, timeout=10) as response:
                    with open(dest, 'wb') as f:
                        f.write(response.read())
                logger.info(f"Downloaded: {filename}")
            except Exception as e:
                logger.warning(f"Failed to download doc {filename}: {e}")

__all__ = ['DocManager']
