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

from ..core.platform_utils import get_app_paths, get_all_monitor_bounds, is_point_on_any_monitor, find_nearest_monitor_for_window
from ..core.config import (
    SETTINGS_FILE_NAME,
    DATABASE_FILE_NAME,
    RECORDS_JSON_FILE_NAME,
    OLD_DATA_FILE_NAME
)
from .database import RecordsDatabase
from .backup_manager import BackupManager

logger = logging.getLogger(__name__)

class RVUData:
    """Manages data persistence with SQLite for records and JSON for settings."""
    
    def __init__(self, base_dir: str = None):
        settings_dir, data_dir = get_app_paths()
        
        # Settings file (RVU tables, rules, rates, user preferences, window positions)
        self.settings_file = os.path.join(data_dir, "rvu_settings.yaml")
        # SQLite database for records (replaces rvu_records.json)
        self.db_file = os.path.join(data_dir, "rvu_records.db")
        # Legacy JSON file paths (for migration)
        self.records_file = os.path.join(data_dir, "rvu_records.json")
        self.old_data_file = os.path.join(data_dir, "rvu_data.json")  # For migration
        
        # Track if running as frozen app
        self.is_frozen = getattr(sys, 'frozen', False)
        
        logger.info(f"Settings file: {self.settings_file}")
        logger.info(f"Database file: {self.db_file}")
        
        # Load settings from JSON
        self.settings_data = self.load_settings()
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
            "direct_lookups": self.settings_data.get("direct_lookups", {}),
            "rvu_table": self.settings_data.get("rvu_table", {}),
            "classification_rules": self.settings_data.get("classification_rules", {}),
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
    
    def load_settings(self) -> dict:
        """Load settings, RVU table, classification rules, and window positions.
        
        Intelligently merges user settings with new defaults:
        - Preserves: settings, window_positions, backup, compensation_rates
        - Updates: rvu_table, classification_rules, direct_lookups (to get bug fixes)
        - Adds: any new settings keys from the new version
        """
        # Get bundled/default settings from the settings file
        # Priority: 1) Bundled file (if frozen), 2) Local file (if script)
        # There are NO hardcoded defaults - the settings file MUST exist
        default_data = None
        
        # Try bundled file first (when frozen)
        if self.is_frozen:
            try:
                bundled_settings_file = os.path.join(sys._MEIPASS, "rvu_settings.yaml")
                if os.path.exists(bundled_settings_file):
                    with open(bundled_settings_file, 'r', encoding='utf-8') as f:
                        default_data = yaml.safe_load(f)
                        logger.info(f"Loaded bundled settings from {bundled_settings_file}")
            except Exception as e:
                logger.error(f"Error loading bundled settings file: {e}")
        
        # Try local file (when running as script, or if bundled file not found)
        if default_data is None:
            # Use self.settings_file which already has the correct path from get_app_paths()
            if os.path.exists(self.settings_file):
                try:
                    with open(self.settings_file, 'r', encoding='utf-8') as f:
                        default_data = yaml.safe_load(f)
                        logger.info(f"Loaded default settings from local file: {self.settings_file}")
                except Exception as e:
                    logger.error(f"Error loading local settings file: {e}")
        
        # Settings file MUST exist - fail if it doesn't
        if default_data is None:
            error_msg = (
                f"CRITICAL ERROR: Could not load settings file!\n"
                f"Expected file: {self.settings_file}\n"
                f"The settings file must be bundled with the app or present in the root directory."
            )
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        
        # Try to load user's existing settings file
        user_data = None
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    user_data = yaml.safe_load(f)
                    logger.info(f"Loaded user settings from {self.settings_file}")
            except Exception as e:
                logger.error(f"Error loading user settings file: {e}")
        
        # If no user settings exist, use defaults and save them
        if user_data is None:
            logger.info("No existing user settings found, using defaults")
            # Save the default settings for future use
            try:
                with open(self.settings_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(default_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                logger.info(f"Created new settings file at {self.settings_file}")
            except Exception as e:
                logger.error(f"Error saving default settings: {e}")
            return default_data
        
        # Merge user settings with defaults intelligently
        merged_data = {}
        
        # Preserve user's settings (user preferences)
        merged_data["settings"] = user_data.get("settings", {})
        # Add any new settings keys from default that user doesn't have
        for key, value in default_data.get("settings", {}).items():
            if key not in merged_data["settings"]:
                merged_data["settings"][key] = value
                logger.debug(f"Added new settings key '{key}' from default")
        
        # Preserve user's window positions
        merged_data["window_positions"] = user_data.get("window_positions", {})
        # Add any new window position keys from default
        for key, value in default_data.get("window_positions", {}).items():
            if key not in merged_data["window_positions"]:
                merged_data["window_positions"][key] = value
                logger.debug(f"Added new window position '{key}' from default")
        
        # Preserve user's backup settings
        merged_data["backup"] = user_data.get("backup", {})
        # Add any new backup settings keys from default
        for key, value in default_data.get("backup", {}).items():
            if key not in merged_data["backup"]:
                merged_data["backup"][key] = value
                logger.debug(f"Added new backup setting '{key}' from default")
        
        # Preserve user's compensation rates
        merged_data["compensation_rates"] = user_data.get("compensation_rates", {})
        # Add any new compensation rate keys from default (nested merge)
        default_comp = default_data.get("compensation_rates", {})
        if default_comp:
            for day_type in ["weekday", "weekend"]:
                if day_type not in merged_data["compensation_rates"]:
                    merged_data["compensation_rates"][day_type] = default_comp.get(day_type, {})
                else:
                    for role in ["assoc", "partner"]:
                        if role not in merged_data["compensation_rates"][day_type]:
                            if day_type in default_comp and role in default_comp[day_type]:
                                merged_data["compensation_rates"][day_type][role] = default_comp[day_type][role]
        
        # RVU table - PRESERVE user's entries EXACTLY as-is, NO merging from defaults
        # User's file is the source of truth - don't add anything from hardcoded defaults
        user_rvu_table = user_data.get("rvu_table", {})
        if user_rvu_table:
            merged_data["rvu_table"] = user_rvu_table.copy()
            logger.info(f"Preserved user RVU table with {len(user_rvu_table)} entries (no defaults added)")
        else:
            # Only use defaults from JSON file if user has NO rvu_table at all
            merged_data["rvu_table"] = default_data.get("rvu_table", {})
            if merged_data["rvu_table"]:
                logger.info("No user RVU table found, using defaults from rvu_settings.yaml")
            else:
                logger.warning("No RVU table found in user or default settings file!")
        
        # Classification rules - PRESERVE user's entries EXACTLY as-is, NO merging from defaults
        # User's file is the source of truth - don't add anything from hardcoded defaults
        user_classification_rules = user_data.get("classification_rules", {})
        if user_classification_rules:
            merged_data["classification_rules"] = user_classification_rules.copy()
            logger.info(f"Preserved user classification rules with {len(user_classification_rules)} study types (no defaults added)")
        else:
            # Only use defaults if user has NO classification_rules at all
            default_classification_rules = default_data.get("classification_rules", {})
            merged_data["classification_rules"] = default_classification_rules.copy() if default_classification_rules else {}
            logger.info("No user classification rules found, using defaults")
        
        # Direct lookups - PRESERVE user's entries EXACTLY as-is, NO merging from defaults
        # User's file is the source of truth - don't add anything from hardcoded defaults
        user_direct_lookups = user_data.get("direct_lookups", {})
        if user_direct_lookups:
            merged_data["direct_lookups"] = user_direct_lookups.copy()
            logger.info(f"Preserved user direct lookups with {len(user_direct_lookups)} entries (no defaults added)")
        else:
            # Only use defaults if user has NO direct_lookups at all
            default_direct_lookups = default_data.get("direct_lookups", {})
            merged_data["direct_lookups"] = default_direct_lookups.copy() if default_direct_lookups else {}
            logger.info("No user direct lookups found, using defaults")
        
        # Save merged settings back to file
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                yaml.safe_dump(merged_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            logger.info(f"Saved merged settings to {self.settings_file}")
        except Exception as e:
            logger.error(f"Error saving merged settings: {e}")
        
        return merged_data
    
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
                    with open(self.settings_file, 'w') as f:
                        json.dump(data, f, indent=2, default=str)
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
                if not os.path.exists(self.settings_file):
                    # Load default rvu_table from YAML (or JSON for backwards compatibility) if available
                    default_rvu_table = {}
                    try:
                        if os.path.exists(self.settings_file):
                            with open(self.settings_file, 'r', encoding='utf-8') as default_f:
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
                    with open(self.settings_file, 'w', encoding='utf-8') as f:
                        yaml.safe_dump(settings_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
                    logger.info(f"Migrated settings to {self.settings_file}")
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
        if "direct_lookups" in self.data:
            self.settings_data["direct_lookups"] = self.data["direct_lookups"]
        if "rvu_table" in self.data:
            self.settings_data["rvu_table"] = self.data["rvu_table"]
        if "classification_rules" in self.data:
            self.settings_data["classification_rules"] = self.data["classification_rules"]
        if "window_positions" in self.data:
            self.settings_data["window_positions"] = self.data["window_positions"]
        if "backup" in self.data:
            self.settings_data["backup"] = self.data["backup"]
        
        if save_records:
            if "records" in self.data:
                self.records_data["records"] = self.data["records"]
            if "current_shift" in self.data:
                self.records_data["current_shift"] = self.data["current_shift"]
            if "shifts" in self.data:
                self.records_data["shifts"] = self.data["shifts"]
        
        # Save settings file (everything - settings, RVU tables, rules, window positions, backup)
        try:
            settings_to_save = {
                "settings": self.settings_data.get("settings", {}),
                "direct_lookups": self.settings_data.get("direct_lookups", {}),
                "rvu_table": self.settings_data.get("rvu_table", {}),
                "classification_rules": self.settings_data.get("classification_rules", {}),
                "compensation_rates": self.settings_data.get("compensation_rates", {}),
                "window_positions": self.settings_data.get("window_positions", {}),
                "backup": self.settings_data.get("backup", {})
            }
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                yaml.safe_dump(settings_to_save, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            logger.info(f"Saved settings to {self.settings_file}")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
        
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
    
    def close(self):
        """Close database connection. Call this when app exits."""
        if hasattr(self, 'db') and self.db:
            self.db.close()




__all__ = ['RVUData']
