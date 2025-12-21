"""Cloud backup manager for RVU Counter - handles automatic database backups."""

import os
import sqlite3
import shutil
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

class BackupManager:
    """Manages automatic cloud backup of the database to OneDrive.
    
    Features:
    - Auto-detects OneDrive folder location
    - Creates safe backups using SQLite backup API
    - Verifies backup integrity
    - Cleans up old backups
    - Tracks backup status for UI display
    
    Designed for novice users - minimal configuration, automatic operation.
    """
    
    # Backup subfolder within OneDrive
    BACKUP_SUBFOLDER = "Apps/RVU Counter/Backups"
    
    # Default settings
    DEFAULT_SETTINGS = {
        "cloud_backup_enabled": False,
        "backup_schedule": "shift_end",  # "shift_end", "hourly", "daily", "manual"
        "backup_retention_count": 10,
        "last_backup_time": None,
        "last_backup_status": None,  # "success", "failed", "pending"
        "last_backup_error": None,
        "onedrive_path": None,  # Auto-detected or manually set
        "github_backup_enabled": False,  # Developer only
    }
    
    def __init__(self, db_path: str, settings: dict, data_manager=None):
        """Initialize BackupManager.
        
        Args:
            db_path: Path to the SQLite database file
            settings: App settings dictionary (will be modified to add backup settings)
            data_manager: Optional reference to RVUData for closing/reconnecting database during restore
        """
        self.db_path = db_path
        self.settings = settings
        self.data_manager = data_manager
        self.backup_in_progress = False
        self._last_check_time = 0
        self._onedrive_path_cache = None
        
        # Initialize backup settings if not present
        self._ensure_settings()
        
        # Detect OneDrive on init (cached)
        self._detect_onedrive_folder()
    
    def _ensure_settings(self):
        """Ensure backup settings exist in the settings dict."""
        if "backup" not in self.settings:
            self.settings["backup"] = {}
        
        for key, default in self.DEFAULT_SETTINGS.items():
            if key not in self.settings["backup"]:
                self.settings["backup"][key] = default
    
    def _detect_onedrive_folder(self) -> Optional[str]:
        """Detect OneDrive folder location.
        
        Checks multiple sources in priority order:
        1. Cached/saved path from settings
        2. Environment variables
        3. Windows Registry
        4. Common default paths
        
        Returns:
            Path to OneDrive folder, or None if not found
        """
        # Return cached value if available
        if self._onedrive_path_cache:
            return self._onedrive_path_cache
        
        # Check if manually set in settings
        saved_path = self.settings["backup"].get("onedrive_path")
        if saved_path and os.path.isdir(saved_path):
            self._onedrive_path_cache = saved_path
            return saved_path
        
        detected_path = None
        
        # Method 1: Environment variables
        # Business OneDrive typically uses OneDriveCommercial
        for env_var in ['OneDriveCommercial', 'OneDriveConsumer', 'ONEDRIVE']:
            path = os.environ.get(env_var)
            if path and os.path.isdir(path):
                detected_path = path
                logger.info(f"OneDrive detected via environment variable {env_var}: {path}")
                break
        
        # Method 2: Windows Registry
        if not detected_path:
            try:
                import winreg
                
                # Try Business account first (most common in healthcare)
                for account in ['Business1', 'Personal']:
                    try:
                        key = winreg.OpenKey(
                            winreg.HKEY_CURRENT_USER,
                            rf"Software\Microsoft\OneDrive\Accounts\{account}"
                        )
                        path, _ = winreg.QueryValueEx(key, "UserFolder")
                        winreg.CloseKey(key)
                        
                        if path and os.path.isdir(path):
                            detected_path = path
                            logger.info(f"OneDrive detected via registry ({account}): {path}")
                            break
                    except (FileNotFoundError, OSError):
                        continue
            except Exception as e:
                logger.debug(f"Registry check failed: {e}")
        
        # Method 3: Common default paths
        if not detected_path:
            home = os.path.expanduser("~")
            for item in os.listdir(home):
                item_path = os.path.join(home, item)
                if os.path.isdir(item_path) and item.lower().startswith("onedrive"):
                    detected_path = item_path
                    logger.info(f"OneDrive detected via default path: {item_path}")
                    break
        
        if detected_path:
            self._onedrive_path_cache = detected_path
            self.settings["backup"]["onedrive_path"] = detected_path
        
        return detected_path
    
    def is_onedrive_available(self) -> bool:
        """Check if OneDrive is available for backup."""
        return self._detect_onedrive_folder() is not None
    
    def get_backup_folder(self) -> Optional[str]:
        """Get the full path to the backup folder within OneDrive.
        
        Creates the folder if it doesn't exist.
        
        Returns:
            Path to backup folder, or None if OneDrive not available
        """
        onedrive = self._detect_onedrive_folder()
        if not onedrive:
            return None
        
        backup_folder = os.path.join(onedrive, self.BACKUP_SUBFOLDER)
        
        # Create folder if it doesn't exist
        try:
            os.makedirs(backup_folder, exist_ok=True)
            return backup_folder
        except Exception as e:
            logger.error(f"Failed to create backup folder: {e}")
            return None
    
    def create_backup(self, force: bool = False) -> dict:
        """Create a backup of the database to OneDrive and optionally GitHub.
        
        Args:
            force: If True, bypass schedule check and create backup immediately
            
        Returns:
            Dict with OneDrive backup result
        """
        onedrive_result = self._create_onedrive_backup(force)
        
        # Also do GitHub backup if enabled (developer only)
        if self.settings["backup"].get("github_backup_enabled", False):
            try:
                self.create_github_backup()
            except Exception as e:
                logger.error(f"GitHub backup failed: {e}")
                
        return onedrive_result

    def _create_onedrive_backup(self, force: bool = False) -> dict:
        """Create a backup of the database to OneDrive.
        
        Uses SQLite's online backup API for a consistent copy even during writes.
        """
        result = {
            "success": False,
            "path": None,
            "error": None,
            "timestamp": datetime.now().isoformat()
        }
        
        # Check if backup already in progress
        if self.backup_in_progress:
            result["error"] = "Backup already in progress"
            return result
        
        # Check if backup is enabled
        if not self.settings["backup"].get("cloud_backup_enabled", False) and not force:
            result["error"] = "Cloud backup is disabled"
            return result
        
        # Get backup folder
        backup_folder = self.get_backup_folder()
        if not backup_folder:
            result["error"] = "OneDrive not available"
            self._update_backup_status("failed", "OneDrive not available")
            return result
        
        # Check if source database exists
        if not os.path.exists(self.db_path):
            result["error"] = "Database file not found"
            self._update_backup_status("failed", "Database not found")
            return result
        
        self.backup_in_progress = True
        
        try:
            # Generate backup filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"rvu_records_{timestamp}.db"
            backup_path = os.path.join(backup_folder, backup_name)
            temp_path = os.path.join(backup_folder, f".{backup_name}.tmp")
            
            logger.info(f"Starting backup to: {backup_path}")
            
            # Step 1: Use SQLite backup API for consistent copy
            source_conn = None
            dest_conn = None
            
            try:
                source_conn = sqlite3.connect(self.db_path)
                dest_conn = sqlite3.connect(temp_path)
                source_conn.backup(dest_conn)
                dest_conn.close()
                dest_conn = None
                source_conn.close()
                source_conn = None
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e).lower():
                    raise Exception("Database is locked - will retry later")
                raise
            finally:
                if dest_conn:
                    try: dest_conn.close()
                    except: pass
                if source_conn:
                    try: source_conn.close()
                    except: pass
            
            # Step 2: Verify backup integrity
            verify_conn = sqlite3.connect(temp_path)
            cursor = verify_conn.execute("PRAGMA integrity_check")
            integrity_result = cursor.fetchone()[0]
            
            try:
                cursor = verify_conn.execute("SELECT COUNT(*) FROM records")
                record_count = cursor.fetchone()[0]
            except:
                record_count = 0
            verify_conn.close()
            
            if integrity_result.lower() != "ok":
                os.remove(temp_path)
                raise Exception(f"Backup integrity check failed: {integrity_result}")
            
            # Step 3: Atomic rename
            if os.path.exists(backup_path):
                os.remove(backup_path)
            os.rename(temp_path, backup_path)
            
            # Step 4: Cleanup old backups
            self._cleanup_old_backups(backup_folder)
            
            # Success!
            result["success"] = True
            result["path"] = backup_path
            result["record_count"] = record_count
            
            self._update_backup_status("success", None)
            logger.info(f"Backup completed successfully: {backup_path} ({record_count} records)")
            
        except Exception as e:
            result["error"] = str(e)
            self._update_backup_status("failed", str(e))
            logger.error(f"Backup failed: {e}")
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try: os.remove(temp_path)
                except: pass
        finally:
            self.backup_in_progress = False
        
        return result

    def create_github_backup(self):
        """Upload database backup to private GitHub repository.
        
        Uses gh.exe (GitHub CLI) if available.
        """
        import subprocess
        from ..core.config import GITHUB_BACKUP_REPO, BACKUP_BRANCH
        from ..core.platform_utils import get_app_root
        
        root = get_app_root()
        gh_path = os.path.join(root, "bin", "gh.exe")
        
        # Check if gh.exe exists
        if not os.path.exists(gh_path):
            # Try system path
            gh_path = "gh"
            
        try:
            # Check if authenticated
            result = subprocess.run([gh_path, "auth", "status"], capture_output=True, text=True)
            if result.returncode != 0:
                logger.warning("GitHub CLI not authenticated, skipping GitHub backup")
                return
            
            # Create a temporary JSON export for GitHub (smaller than DB)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_name = f"rvu_records_{timestamp}.json"
            export_path = os.path.join(root, "logs", export_name)
            
            if self.data_manager:
                self.data_manager.export_records_to_json(export_path)
                
                # Use gh to upload to gist or repo
                # For simplicity, we'll assume a repo is configured
                logger.info(f"Uploading backup to GitHub repo: {GITHUB_BACKUP_REPO}")
                
                # Alternative: Upload as a gist (no repo setup needed)
                subprocess.run([
                    gh_path, "gist", "create", export_path,
                    "-d", f"RVU Counter Backup {timestamp}",
                    "-p"  # Private
                ])
                
                # Cleanup temp export
                if os.path.exists(export_path):
                    os.remove(export_path)
                    
                logger.info("GitHub backup (gist) completed successfully")
                
        except Exception as e:
            logger.error(f"GitHub backup failed: {e}")

    
    def _update_backup_status(self, status: str, error: Optional[str]):
        """Update backup status in settings."""
        self.settings["backup"]["last_backup_time"] = datetime.now().isoformat()
        self.settings["backup"]["last_backup_status"] = status
        self.settings["backup"]["last_backup_error"] = error
    
    def _cleanup_old_backups(self, backup_folder: str):
        """Remove old backups beyond retention limit."""
        retention = self.settings["backup"].get("backup_retention_count", 10)
        
        try:
            # Get all backup files
            backup_files = []
            for f in os.listdir(backup_folder):
                if f.startswith("rvu_records_") and f.endswith(".db"):
                    full_path = os.path.join(backup_folder, f)
                    backup_files.append((full_path, os.path.getmtime(full_path)))
            
            # Sort by modification time (newest first)
            backup_files.sort(key=lambda x: x[1], reverse=True)
            
            # Remove files beyond retention limit
            for old_file, _ in backup_files[retention:]:
                try:
                    os.remove(old_file)
                    logger.info(f"Removed old backup: {old_file}")
                except Exception as e:
                    logger.warning(f"Failed to remove old backup {old_file}: {e}")
                    
        except Exception as e:
            logger.warning(f"Error during backup cleanup: {e}")
    
    def get_backup_history(self) -> List[dict]:
        """Get list of available backups.
        
        Returns:
            List of dicts with backup info (path, timestamp, size, record_count)
        """
        backup_folder = self.get_backup_folder()
        if not backup_folder:
            return []
        
        backups = []
        
        try:
            for f in os.listdir(backup_folder):
                if f.startswith("rvu_records_") and f.endswith(".db"):
                    full_path = os.path.join(backup_folder, f)
                    
                    # Parse timestamp from filename
                    try:
                        timestamp_str = f.replace("rvu_records_", "").replace(".db", "")
                        timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                    except:
                        timestamp = datetime.fromtimestamp(os.path.getmtime(full_path))
                    
                    # Get file size
                    size = os.path.getsize(full_path)
                    
                    # Get record count (quick query)
                    record_count = 0
                    try:
                        conn = sqlite3.connect(full_path)
                        cursor = conn.execute("SELECT COUNT(*) FROM records")
                        record_count = cursor.fetchone()[0]
                        conn.close()
                    except:
                        pass
                    
                    backups.append({
                        "path": full_path,
                        "filename": f,
                        "timestamp": timestamp,
                        "size": size,
                        "size_formatted": self._format_size(size),
                        "record_count": record_count
                    })
            
            # Sort by timestamp (newest first)
            backups.sort(key=lambda x: x["timestamp"], reverse=True)
            
        except Exception as e:
            logger.error(f"Error getting backup history: {e}")
        
        return backups
    
    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
    
    def restore_from_backup(self, backup_path: str) -> dict:
        """Restore database from a backup.
        
        Creates a pre-restore backup of current database first.
        
        Args:
            backup_path: Path to the backup file to restore
            
        Returns:
            Dict with 'success', 'error', 'pre_restore_backup' keys
        """
        result = {
            "success": False,
            "error": None,
            "pre_restore_backup": None
        }
        
        # Verify backup exists
        if not os.path.exists(backup_path):
            result["error"] = "Backup file not found"
            return result
        
        # Verify backup integrity
        try:
            conn = sqlite3.connect(backup_path)
            cursor = conn.execute("PRAGMA integrity_check")
            integrity_result = cursor.fetchone()[0]
            conn.close()
            
            if integrity_result.lower() != "ok":
                result["error"] = f"Backup file is corrupted: {integrity_result}"
                return result
        except Exception as e:
            result["error"] = f"Cannot read backup file: {e}"
            return result
        
        # Create pre-restore backup of current database
        try:
            backup_folder = self.get_backup_folder()
            if backup_folder:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                pre_restore_name = f"rvu_records_pre_restore_{timestamp}.db"
                pre_restore_path = os.path.join(backup_folder, pre_restore_name)
                
                # Copy current database
                source_conn = sqlite3.connect(self.db_path)
                dest_conn = sqlite3.connect(pre_restore_path)
                source_conn.backup(dest_conn)
                dest_conn.close()
                source_conn.close()
                
                result["pre_restore_backup"] = pre_restore_path
                logger.info(f"Created pre-restore backup: {pre_restore_path}")
        except Exception as e:
            logger.warning(f"Failed to create pre-restore backup: {e}")
            # Continue with restore anyway
        
        # Perform restore
        temp_restore = None
        db_closed = False
        try:
            # Close database connection if available (needed to replace the file)
            if self.data_manager and hasattr(self.data_manager, 'db') and self.data_manager.db:
                try:
                    if self.data_manager.db.conn:
                        self.data_manager.db.conn.close()
                        self.data_manager.db.conn = None
                        db_closed = True
                        logger.info("Closed database connection for restore")
                except Exception as e:
                    logger.warning(f"Error closing database connection: {e}")
            
            # Use SQLite backup API for safe copy
            source_conn = sqlite3.connect(backup_path)
            
            # Create a temporary file first
            temp_restore = self.db_path + ".restoring"
            dest_conn = sqlite3.connect(temp_restore)
            
            source_conn.backup(dest_conn)
            
            dest_conn.close()
            source_conn.close()
            
            # Atomic replace
            if os.path.exists(self.db_path):
                # Try to remove with retry logic in case file is still locked
                max_retries = 5
                retry_delay = 0.2
                for attempt in range(max_retries):
                    try:
                        os.remove(self.db_path)
                        break
                    except PermissionError as e:
                        if attempt < max_retries - 1:
                            time.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                        else:
                            raise
            
            os.rename(temp_restore, self.db_path)
            
            # Reconnect database if we closed it
            if db_closed and self.data_manager and hasattr(self.data_manager, 'db'):
                try:
                    self.data_manager.db._connect()
                    logger.info("Reconnected to database after restore")
                except Exception as e:
                    logger.warning(f"Error reconnecting to database: {e}")
            
            result["success"] = True
            logger.info(f"Database restored from: {backup_path}")
            
        except Exception as e:
            result["error"] = f"Restore failed: {e}"
            logger.error(f"Restore failed: {e}")
            
            # Try to reconnect if we closed the connection
            if db_closed and self.data_manager and hasattr(self.data_manager, 'db'):
                try:
                    self.data_manager.db._connect()
                    logger.info("Reconnected to database after failed restore")
                except Exception as e:
                    logger.error(f"Error reconnecting to database after failed restore: {e}")
            
            # Cleanup temp file
            if temp_restore and os.path.exists(temp_restore):
                try:
                    os.remove(temp_restore)
                except:
                    pass
        
        return result
    
    def get_backup_status(self) -> dict:
        """Get current backup status for UI display.
        
        Returns:
            Dict with status info for display
        """
        enabled = self.settings["backup"].get("cloud_backup_enabled", False)
        last_time = self.settings["backup"].get("last_backup_time")
        last_status = self.settings["backup"].get("last_backup_status")
        last_error = self.settings["backup"].get("last_backup_error")
        
        status = {
            "enabled": enabled,
            "available": self.is_onedrive_available(),
            "onedrive_path": self._detect_onedrive_folder(),
            "last_backup_time": last_time,
            "last_backup_status": last_status,
            "last_backup_error": last_error,
            "time_since_backup": None,
            "status_text": "",
            "status_icon": ""
        }
        
        if not enabled:
            status["status_text"] = "Backup disabled"
            status["status_icon"] = "⚪"
        elif not status["available"]:
            status["status_text"] = "OneDrive not found"
            status["status_icon"] = "⚠️"
        elif last_time:
            try:
                last_dt = datetime.fromisoformat(last_time)
                delta = datetime.now() - last_dt
                
                if delta.total_seconds() < 3600:
                    time_str = f"{int(delta.total_seconds() / 60)}m ago"
                elif delta.total_seconds() < 86400:
                    time_str = f"{int(delta.total_seconds() / 3600)}h ago"
                else:
                    time_str = f"{int(delta.days)}d ago"
                
                status["time_since_backup"] = time_str
                
                if last_status == "success":
                    status["status_text"] = f"Backed up {time_str}"
                    status["status_icon"] = "☁️"
                else:
                    status["status_text"] = f"Backup failed"
                    status["status_icon"] = "⚠️"
            except:
                status["status_text"] = "Unknown"
                status["status_icon"] = "⚪"
        else:
            status["status_text"] = "No backup yet"
            status["status_icon"] = "⚪"
        
        return status
    
    def should_backup_now(self, schedule: str = None) -> bool:
        """Check if a backup should be performed now based on schedule.
        
        Args:
            schedule: Override schedule setting ("shift_end", "hourly", "daily", "manual")
            
        Returns:
            True if backup should be performed
        """
        if not self.settings["backup"].get("cloud_backup_enabled", False):
            return False
        
        schedule = schedule or self.settings["backup"].get("backup_schedule", "shift_end")
        
        if schedule == "manual":
            return False
        
        last_time = self.settings["backup"].get("last_backup_time")
        if not last_time:
            return True  # Never backed up
        
        try:
            last_dt = datetime.fromisoformat(last_time)
            elapsed = (datetime.now() - last_dt).total_seconds()
            
            if schedule == "hourly":
                return elapsed >= 3600
            elif schedule == "daily":
                return elapsed >= 86400
            elif schedule == "shift_end":
                # This is triggered manually at shift end
                return False
                
        except:
            return True
        
        return False




__all__ = ['BackupManager']
