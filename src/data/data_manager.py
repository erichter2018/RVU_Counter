"""Data management layer for RVU Counter - handles settings and data persistence."""

import os
import sys
import json
import yaml
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from ..core.platform_utils import (
    get_app_paths,
    get_all_monitor_bounds,
    get_primary_monitor_bounds,
    is_point_on_any_monitor,
    find_nearest_monitor_for_window
)
from ..core.config import (
    SETTINGS_FILE_NAME,
    DATABASE_FILE_NAME,
    RECORDS_JSON_FILE_NAME,
    OLD_DATA_FILE_NAME,
    USER_SETTINGS_FILE_NAME,
    RULES_FILE_NAME,
    DATA_FOLDER
)
from .database import RecordsDatabase
from .backup_manager import BackupManager

logger = logging.getLogger(__name__)

class RVUData:
    """Manages data persistence with SQLite for records and JSON for settings."""
    
    def __init__(self, base_dir: str = None):
        settings_dir, data_root = get_app_paths()
        
        # Use absolute paths based on application root
        self.user_settings_file = os.path.join(data_root, USER_SETTINGS_FILE_NAME)
        self.rules_file = os.path.join(data_root, RULES_FILE_NAME)
        self.legacy_settings_file = os.path.join(data_root, SETTINGS_FILE_NAME)
        self.db_file = os.path.join(data_root, DATABASE_FILE_NAME)
        
        # Legacy file paths (for migration) - located in data folder now
        self.records_file = os.path.join(data_root, DATA_FOLDER, RECORDS_JSON_FILE_NAME)
        self.old_data_file = os.path.join(data_root, DATA_FOLDER, OLD_DATA_FILE_NAME)
        
        # Track if running as frozen app
        self.is_frozen = getattr(sys, 'frozen', False)
        
        logger.info(f"User settings file: {self.user_settings_file}")
        logger.info(f"Rules file: {self.rules_file}")
        logger.info(f"Database file: {self.db_file}")
        
        # Split legacy settings if needed
        self._migrate_to_split_settings()
        
        # Load settings and rules
        self.settings_data = self.load_settings()
        self.rules_data = self.load_rules()
        # Validate and fix window positions after loading
        self.settings_data = self._validate_window_positions(self.settings_data)
        
        # Initialize SQLite database
        self.db = RecordsDatabase(self.db_file)
        
        # Check if we need to migrate from JSON to SQLite
        self._migrate_json_to_sqlite()
        
        # Migrate old rvu_data.json file if it exists
        self.migrate_old_file()
        
        # Load records from database into memory for compatibility
        self.records_data = self._load_records_from_db()
        
        # Use settings directly (no need to merge separate user settings)
        merged_settings = self.settings_data.get("settings", {})
        merged_window_positions = self.settings_data.get("window_positions", {})
        
        # Merge into single data structure for compatibility
        self.data = {
            "settings": merged_settings,
            "direct_lookups": self.rules_data.get("direct_lookups", {}),
            "rvu_table": self.rules_data.get("rvu_table", {}),
            "classification_rules": self.rules_data.get("classification_rules", {}),
            "compensation_rates": self.settings_data.get("compensation_rates", {}),
            "window_positions": merged_window_positions,
            "backup": self.settings_data.get("backup", {}),  # Load backup settings
            "records": self.records_data.get("records", []),
            "current_shift": self.records_data.get("current_shift", {
                "shift_start": None,
                "shift_end": None,
                "records": []
            }),
            "shifts": self.records_data.get("shifts", [])
        }
        
        # Initialize cloud backup manager
        self.backup_manager = BackupManager(self.db_file, self.data, self)
    
    def _migrate_json_to_sqlite(self):
        """Migrate data from JSON to SQLite if JSON exists and DB is empty."""
        # Check if JSON file exists and has data
        if os.path.exists(self.records_file):
            try:
                with open(self.records_file, 'r') as f:
                    json_data = json.load(f)
                
                # Check if database is empty (no shifts)
                cursor = self.db.conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM shifts')
                shift_count = cursor.fetchone()[0]
                
                if shift_count == 0:
                    # Database is empty, migrate from JSON
                    has_data = (
                        json_data.get('records', []) or 
                        json_data.get('shifts', []) or 
                        json_data.get('current_shift', {}).get('records', [])
                    )
                    
                    if has_data:
                        logger.info("Migrating records from JSON to SQLite database...")
                        self.db.migrate_from_json(json_data)
                        
                        # Rename JSON file to backup
                        backup_path = self.records_file + ".migrated_backup"
                        os.rename(self.records_file, backup_path)
                        logger.info(f"JSON file backed up to: {backup_path}")
                    else:
                        logger.info("JSON file exists but is empty, no migration needed")
                else:
                    logger.info(f"Database already has {shift_count} shifts, skipping migration")
                    
            except Exception as e:
                logger.error(f"Error during JSON to SQLite migration: {e}")
    
    def _load_records_from_db(self) -> dict:
        """Load records from SQLite database into the legacy dict format."""
        try:
            return self.db.export_to_json()
        except Exception as e:
            logger.error(f"Error loading records from database: {e}")
            return {
                "records": [],
                "current_shift": {
                    "shift_start": None,
                    "shift_end": None,
                    "records": []
                },
                "shifts": []
            }
    
    def _load_bundled_file(self, filename: str) -> Optional[dict]:
        """Load a bundled YAML file from the settings folder.
        
        When running as a PyInstaller executable, files are in sys._MEIPASS/settings/.
        When running as a script, they're in the project's settings/ folder.
        """
        try:
            if self.is_frozen:
                # Running as compiled executable - look in bundle
                bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
                bundled_file = os.path.join(bundle_dir, 'settings', filename)
            else:
                # Running as script - look in project settings folder
                script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                bundled_file = os.path.join(script_dir, 'settings', filename)
            
            if os.path.exists(bundled_file):
                logger.info(f"Loading bundled file from: {bundled_file}")
                with open(bundled_file, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f)
            else:
                logger.warning(f"Bundled file not found: {bundled_file}")
                return None
                
        except Exception as e:
            logger.error(f"Error loading bundled file {filename}: {e}")
            return None
    
    def _migrate_to_split_settings(self):
        """One-time migration to split rvu_settings.yaml into user_settings.yaml and rvu_rules.yaml."""
        if os.path.exists(self.legacy_settings_file) and not os.path.exists(self.user_settings_file):
            try:
                logger.info(f"Migrating legacy settings from {self.legacy_settings_file}...")
                with open(self.legacy_settings_file, 'r', encoding='utf-8') as f:
                    legacy_data = yaml.safe_load(f)
                
                if not legacy_data:
                    return

                # Extract user settings
                user_data = {
                    "settings": legacy_data.get("settings", {}),
                    "compensation_rates": legacy_data.get("compensation_rates", {}),
                    "window_positions": legacy_data.get("window_positions", {}),
                    "backup": legacy_data.get("backup", {})
                }
                
                # Extract rules
                rules_data = {
                    "direct_lookups": legacy_data.get("direct_lookups", {}),
                    "rvu_table": legacy_data.get("rvu_table", {}),
                    "classification_rules": legacy_data.get("classification_rules", {})
                }
                
                # Save split files
                with open(self.user_settings_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(user_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                
                with open(self.rules_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(rules_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                
                logger.info("Migration to split settings complete.")
                # Keep legacy file as backup for now, or delete it? 
                # The design said to delete it in the transition tool.
                # Here we just leave it for now to be safe, or rename it.
                os.rename(self.legacy_settings_file, self.legacy_settings_file + ".migrated")
                
            except Exception as e:
                logger.error(f"Error during settings split migration: {e}")

    def load_rules(self) -> dict:
        """Load RVU table and classification rules."""
        if os.path.exists(self.rules_file):
            try:
                with open(self.rules_file, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Error loading rules file: {e}")
        
        # On first run, copy bundled rules file
        logger.info("Rules file not found, copying from bundled template...")
        bundled_rules = self._load_bundled_file('rvu_rules.yaml')
        if bundled_rules:
            # Save rules file for future use
            try:
                os.makedirs(os.path.dirname(self.rules_file), exist_ok=True)
                with open(self.rules_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(bundled_rules, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                logger.info(f"Created rules file from bundle: {self.rules_file}")
            except Exception as e:
                logger.error(f"Error saving initial rules file: {e}")
            return bundled_rules
        
        # Fallback to empty if not found
        logger.warning("No bundled rules found, using empty defaults")
        return {
            "direct_lookups": {},
            "rvu_table": {},
            "classification_rules": {}
        }

    def load_settings(self) -> dict:
        """Load user preferences, compensation rates, and window positions."""
        if os.path.exists(self.user_settings_file):
            try:
                with open(self.user_settings_file, 'r', encoding='utf-8') as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"Error loading user settings file: {e}")
        
        # On first run, copy bundled settings file
        logger.info("User settings file not found, copying from bundled template...")
        bundled_settings = self._load_bundled_file('user_settings.yaml')
        if bundled_settings:
            # Save settings file for future use
            try:
                os.makedirs(os.path.dirname(self.user_settings_file), exist_ok=True)
                with open(self.user_settings_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(bundled_settings, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                logger.info(f"Created user settings file from bundle: {self.user_settings_file}")
            except Exception as e:
                logger.error(f"Error saving initial settings file: {e}")
            return bundled_settings
        
        # Fallback to minimal defaults if bundled file not found
        logger.warning("No bundled settings found, using minimal defaults")
        return {
            "settings": {
                "auto_start": True,
                "show_total": True,
                "show_avg": True,
                "role": "Partner",
                "min_study_seconds": 1,
                "dark_mode": True,
                "stay_on_top": True
            },
            "window_positions": {},
            "compensation_rates": {},
            "backup": {}
        }
    
    def _validate_window_positions(self, data: dict) -> dict:
        """Validate window positions and reset invalid ones to safe defaults.
        
        Returns data dict with validated/reset window positions.
        Handles multi-monitor setups using Windows API for accurate detection.
        """
        try:
            # Get actual monitor bounds using Windows API (handles all monitor configurations)
            virtual_left, virtual_top, virtual_right, virtual_bottom, monitors = get_all_monitor_bounds()
            
            logger.debug(f"Monitor validation: virtual bounds ({virtual_left}, {virtual_top}) to ({virtual_right}, {virtual_bottom}), {len(monitors)} monitors")
            
            # Get primary monitor for default positioning
            primary_bounds = get_primary_monitor_bounds()
            primary_left, primary_top, primary_right, primary_bottom = primary_bounds
            
            # Default safe positions on primary monitor
            default_positions = {
                "main": {"x": primary_left + 50, "y": primary_top + 50, "width": 240, "height": 500},
                "settings": {"x": primary_left + 100, "y": primary_top + 100},
                "statistics": {"x": primary_left + 150, "y": primary_top + 150}
            }
            
            # Window size constraints (minimum visible area)
            window_sizes = {
                "main": {"width": 240, "height": 500},
                "settings": {"width": 450, "height": 700},
                "statistics": {"width": 1350, "height": 800}
            }
            
            if "window_positions" not in data:
                data["window_positions"] = default_positions.copy()
                return data
            
            positions = data["window_positions"]
            positions_updated = False
            
            for window_type in ["main", "settings", "statistics"]:
                if window_type not in positions:
                    positions[window_type] = default_positions[window_type].copy()
                    positions_updated = True
                    continue
                
                pos = positions[window_type]
                x = pos.get("x", 0)
                y = pos.get("y", 0)
                
                # Get window dimensions
                window_size = window_sizes.get(window_type, {"width": 400, "height": 400})
                win_width = window_size["width"]
                win_height = window_size["height"]
                
                # Use Windows API to check if window top-left is on ANY monitor
                # This is more accurate than virtual bounds for non-rectangular monitor arrangements
                top_left_visible = is_point_on_any_monitor(x + 50, y + 50)  # Check slightly inward
                
                if not top_left_visible:
                    # Window is not visible on any monitor - find nearest valid position
                    new_x, new_y = find_nearest_monitor_for_window(x, y, win_width, win_height)
                    
                    if new_x != x or new_y != y:
                        logger.warning(f"{window_type} window off-screen (x={x}, y={y}), moving to ({new_x}, {new_y})")
                        positions[window_type]["x"] = new_x
                        positions[window_type]["y"] = new_y
                        positions_updated = True
                    else:
                        # Couldn't find a good position, use default
                        logger.warning(f"{window_type} window off-screen (x={x}, y={y}), resetting to default")
                        positions[window_type] = default_positions[window_type].copy()
                        positions_updated = True
            
            # If positions were updated, save the corrected data
            if positions_updated:
                data["window_positions"] = positions
                try:
                    # Save corrected positions back to file
                    with open(self.user_settings_file, 'w') as f:
                        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                    logger.info("Window positions validated and corrected")
                except Exception as e:
                    logger.error(f"Error saving corrected window positions: {e}")
            
            return data
            
        except Exception as e:
            logger.error(f"Error validating window positions: {e}")
            # Return data with safe defaults if validation fails
            if "window_positions" not in data:
                data["window_positions"] = {
                    "main": {"x": 50, "y": 50, "width": 240, "height": 500},
                    "settings": {"x": 100, "y": 100},
                    "statistics": {"x": 150, "y": 150}
                }
            return data
    
    
    def load_records(self) -> dict:
        """Load records from SQLite database.
        
        Note: This method now loads from SQLite, not JSON.
        Kept for compatibility with existing code structure.
        """
        return self._load_records_from_db()
    
    def migrate_old_file(self):
        """Migrate data from old rvu_data.json file if it exists."""
        if os.path.exists(self.old_data_file):
            try:
                logger.info("Found old rvu_data.json file, migrating to new format...")
                with open(self.old_data_file, 'r') as f:
                    old_data = json.load(f)
                
                # Migrate settings to settings file
                if not os.path.exists(self.user_settings_file):
                    # Load default rvu_table from YAML (or JSON for backwards compatibility) if available
                    default_rvu_table = {}
                    try:
                        if os.path.exists(self.user_settings_file):
                            with open(self.user_settings_file, 'r', encoding='utf-8') as default_f:
                                # Try YAML first, then JSON for backwards compatibility
                                try:
                                    default_data = yaml.safe_load(default_f)
                                except:
                                    default_f.seek(0)
                                    default_data = json.load(default_f)
                                default_rvu_table = default_data.get("rvu_table", {})
                    except:
                        pass
                    
                    settings_data = {
                        "settings": old_data.get("settings", {}),
                        "rvu_table": old_data.get("rvu_table", default_rvu_table),
                        "classification_rules": old_data.get("classification_rules", {}),
                        "window_positions": old_data.get("window_positions", {
                            "main": {"x": 100, "y": 100},
                            "settings": {"x": 200, "y": 200}
                        })
                    }
                    with open(self.user_settings_file, 'w', encoding='utf-8') as f:
                        yaml.safe_dump(settings_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                    logger.info(f"Migrated settings to {self.user_settings_file}")
                    self.settings_data = settings_data
                
                # Migrate records to records file
                if not os.path.exists(self.records_file):
                    records_data = {
                        "records": old_data.get("records", []),
                        "current_shift": old_data.get("current_shift", {
                            "shift_start": old_data.get("shift_start"),
                            "shift_end": None,
                            "records": []
                        }),
                        "shifts": old_data.get("shifts", [])
                    }
                    # Migrate old format if needed
                    if "shift_start" in old_data and "current_shift" not in old_data:
                        records_data["current_shift"] = {
                            "shift_start": old_data.get("shift_start"),
                            "shift_end": None,
                            "records": old_data.get("records", [])
                        }
                    with open(self.records_file, 'w') as f:
                        json.dump(records_data, f, indent=2)
                    logger.info(f"Migrated records to {self.records_file}")
                    self.records_data = records_data
                
                logger.info("Migration complete. Old file can be deleted.")
            except Exception as e:
                logger.error(f"Error migrating old file: {e}")
    
    def save(self, save_records=True):
        """Save data to appropriate files/database.
        
        Args:
            save_records: If True, save both settings and records. If False, only save settings.
        """
        # Update internal data structures from merged data
        if "settings" in self.data:
            self.settings_data["settings"] = self.data["settings"]
        if "compensation_rates" in self.data:
            self.settings_data["compensation_rates"] = self.data["compensation_rates"]
        if "window_positions" in self.data:
            self.settings_data["window_positions"] = self.data["window_positions"]
        if "backup" in self.data:
            self.settings_data["backup"] = self.data["backup"]
        
        # Rules are generally not updated by the user in-app except via direct_lookups
        # BUT we still allow saving them if they were modified
        if "direct_lookups" in self.data:
            self.rules_data["direct_lookups"] = self.data["direct_lookups"]
        if "rvu_table" in self.data:
            self.rules_data["rvu_table"] = self.data["rvu_table"]
        if "classification_rules" in self.data:
            self.rules_data["classification_rules"] = self.data["classification_rules"]
        
        if save_records:
            if "records" in self.data:
                self.records_data["records"] = self.data["records"]
            if "current_shift" in self.data:
                self.records_data["current_shift"] = self.data["current_shift"]
            if "shifts" in self.data:
                self.records_data["shifts"] = self.data["shifts"]
        
        # Save user settings file
        try:
            with open(self.user_settings_file, 'w', encoding='utf-8') as f:
                yaml.safe_dump(self.settings_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            logger.info(f"Saved user settings to {self.user_settings_file}")
        except Exception as e:
            logger.error(f"Error saving user settings: {e}")
            
        # Save rules file
        try:
            with open(self.rules_file, 'w', encoding='utf-8') as f:
                yaml.safe_dump(self.rules_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            logger.info(f"Saved rules to {self.rules_file}")
        except Exception as e:
            logger.error(f"Error saving rules: {e}")
        
        # Save records to SQLite database (not JSON anymore)
        if save_records:
            try:
                self._sync_to_database()
                logger.info(f"Saved records to database: {self.db_file}")
            except Exception as e:
                logger.error(f"Error saving records: {e}")
    
    def _sync_to_database(self):
        """Sync in-memory data to SQLite database.
        
        This handles the complexity of syncing the legacy dict-based structure
        to the normalized SQLite database.
        """
        current_shift_data = self.data.get("current_shift", {})
        
        # Get or create current shift in database
        db_current = self.db.get_current_shift()
        
        if current_shift_data.get("shift_start"):
            # We have an active shift in memory
            if db_current:
                # Update existing shift times if needed
                self.db.update_current_shift_times(
                    effective_shift_start=current_shift_data.get("effective_shift_start"),
                    projected_shift_end=current_shift_data.get("projected_shift_end")
                )
                current_shift_id = db_current['id']
            else:
                # Create new shift in database
                current_shift_id = self.db.start_shift(
                    shift_start=current_shift_data.get("shift_start"),
                    effective_shift_start=current_shift_data.get("effective_shift_start"),
                    projected_shift_end=current_shift_data.get("projected_shift_end")
                )
            
            # Sync records for current shift
            # Get existing records from DB
            db_records = self.db.get_records_for_shift(current_shift_id)
            db_accessions = {r['accession']: r for r in db_records}
            
            # Add/update records from memory
            memory_records = current_shift_data.get("records", [])
            memory_accessions = set()
            
            for record in memory_records:
                accession = record.get('accession', '')
                memory_accessions.add(accession)
                
                if accession in db_accessions:
                    # Update existing record if needed
                    db_rec = db_accessions[accession]
                    # Check if duration changed (main update scenario)
                    if record.get('duration_seconds', 0) != db_rec.get('duration_seconds', 0):
                        self.db.update_record(db_rec['id'], record)
                else:
                    # Add new record
                    self.db.add_record(current_shift_id, record)
            
            # Delete records that were removed from memory
            for accession, db_rec in db_accessions.items():
                if accession not in memory_accessions:
                    self.db.delete_record(db_rec['id'])
        
        elif db_current:
            # No shift in memory but DB has current shift - end it
            self.db.end_current_shift()
    
    def end_current_shift(self):
        """End the current shift and move it to historical shifts."""
        if self.data["current_shift"]["shift_start"]:
            current_shift = self.data["current_shift"].copy()
            current_shift["shift_end"] = datetime.now().isoformat()
            self.data["shifts"].append(current_shift)
            
            # End in database as well
            self.db.end_current_shift(current_shift["shift_end"])
            
            logger.info(f"Ended shift: {current_shift['shift_start']} to {current_shift['shift_end']}")
    
    def clear_current_shift(self):
        """Clear records for current shift."""
        self.end_current_shift()
        self.data["current_shift"]["shift_start"] = None
        self.data["current_shift"]["shift_end"] = None
        self.data["current_shift"]["records"] = []
        self.save()
        logger.info("Cleared current shift data")
    
    def clear_all_data(self):
        """Clear all historical data."""
        self.end_current_shift()
        self.data["current_shift"]["shift_start"] = None
        self.data["current_shift"]["shift_end"] = None
        self.data["current_shift"]["records"] = []
        self.data["records"] = []  # Clear legacy records too
        self.data["shifts"] = []
        # Also clear the records_data structure
        self.records_data["records"] = []
        self.records_data["current_shift"] = {
            "shift_start": None,
            "shift_end": None,
            "records": []
        }
        self.records_data["shifts"] = []
        
        # Clear the database completely
        try:
            cursor = self.db.conn.cursor()
            cursor.execute('DELETE FROM records')
            cursor.execute('DELETE FROM shifts')
            cursor.execute('DELETE FROM legacy_records')
            self.db.conn.commit()
            logger.info("Cleared all data from database")
        except Exception as e:
            logger.error(f"Error clearing database: {e}")
        
        self.save()
        logger.info("Cleared all data")
    
    def export_records_to_json(self, filepath: str = None):
        """Export all records to a JSON file (for backups).
        
        Args:
            filepath: Path to save the JSON file. If None, uses default backup path.
        """
        if filepath is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            settings_dir, data_dir = get_app_paths()
            filepath = os.path.join(data_dir, f"rvu_records_backup_{timestamp}.json")
        
        self.db.export_to_json_file(filepath)
        return filepath
    
    def import_records_from_json(self, filepath: str):
        """Import records from a JSON backup file.
        
        Args:
            filepath: Path to the JSON file to import.
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # Clear current database
            cursor = self.db.conn.cursor()
            cursor.execute('DELETE FROM records')
            cursor.execute('DELETE FROM shifts')
            cursor.execute('DELETE FROM legacy_records')
            self.db.conn.commit()
            
            # Import from JSON
            self.db.migrate_from_json(json_data)
            
            # Reload into memory
            self.records_data = self._load_records_from_db()
            self.data["records"] = self.records_data.get("records", [])
            self.data["current_shift"] = self.records_data.get("current_shift", {
                "shift_start": None,
                "shift_end": None,
                "records": []
            })
            self.data["shifts"] = self.records_data.get("shifts", [])
            
            logger.info(f"Imported records from: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Error importing records: {e}")
            return False
    
    def save_data(self, save_records=True):
        """Save user settings to file.
        
        Args:
            save_records: Deprecated, kept for compatibility. Records are auto-saved to SQLite.
        """
        try:
            # Save user settings (preferences, window positions, compensation rates)
            settings_to_save = {
                "settings": self.data.get("settings", {}),
                "window_positions": self.data.get("window_positions", {}),
                "compensation_rates": self.data.get("compensation_rates", {}),
                "backup": self.data.get("backup", {})
            }
            
            with open(self.user_settings_file, 'w', encoding='utf-8') as f:
                yaml.dump(settings_to_save, f, default_flow_style=False, allow_unicode=True)
            
            logger.info(f"Saved user settings to {self.user_settings_file}")
            
            # Records are automatically saved to SQLite database, no action needed here
            
        except Exception as e:
            logger.error(f"Error saving user settings: {e}")
    
    def close(self):
        """Close database connection. Call this when app exits."""
        if hasattr(self, 'db') and self.db:
            self.db.close()




__all__ = ['RVUData']
