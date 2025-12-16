"""
Real-Time RVU Counter for Radiology Practice

Tracks wRVU by reading study type and accession number from PowerScribe 360.
Counts studies only when they are "read" (disappear from PowerScribe).
"""

import tkinter as tk
from tkinter import ttk, messagebox
from pywinauto import Desktop
import json
import logging
import os
import sys
import shutil
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import threading
import time
import re

# Try to import tkcalendar for date picker
try:
    from tkcalendar import DateEntry, Calendar
    HAS_TKCALENDAR = True
except ImportError as e:
    HAS_TKCALENDAR = False
    print(f"Warning: tkcalendar not available: {e}")

# Try to import matplotlib for graphing
try:
    import matplotlib
    matplotlib.use('TkAgg')  # Use TkAgg backend for Tkinter integration
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError as e:
    HAS_MATPLOTLIB = False
    print(f"Warning: matplotlib not available: {e}")


# Configure logging with FIFO-style maintenance (max 10MB, single file, remove oldest entries)
log_file = os.path.join(os.path.dirname(__file__), "rvu_counter.log")

class FIFOFileHandler(logging.FileHandler):
    """Custom file handler that maintains a single log file with FIFO-style trimming.
    
    When the log file exceeds max_bytes, it removes the oldest entries (from the top)
    and keeps only the most recent entries that fit within the size limit.
    """
    def __init__(self, filename, max_bytes=10*1024*1024, encoding='utf-8'):
        super().__init__(filename, mode='a', encoding=encoding)
        self.max_bytes = max_bytes
        self._check_interval = 100  # Check size every N writes to avoid overhead
        self._write_count = 0
    
    def emit(self, record):
        """Write log record and trim file if it exceeds max size."""
        super().emit(record)
        self._write_count += 1
        
        # Check file size periodically (not on every write to avoid performance impact)
        if self._write_count % self._check_interval == 0:
            self._trim_if_needed()
    
    def _trim_if_needed(self):
        """Trim log file to max_bytes by removing oldest entries."""
        try:
            if os.path.exists(self.baseFilename):
                file_size = os.path.getsize(self.baseFilename)
                if file_size > self.max_bytes:
                    # Read all lines
                    with open(self.baseFilename, 'r', encoding=self.encoding) as f:
                        lines = f.readlines()
                    
                    # Calculate target size (keep ~90% of max to avoid constant trimming)
                    target_size = int(self.max_bytes * 0.9)
                    
                    # Remove oldest lines from the top until we're under target size
                    trimmed_lines = lines
                    current_size = file_size
                    
                    while current_size > target_size and len(trimmed_lines) > 1:
                        # Remove oldest line (first line)
                        removed_line_size = len(trimmed_lines[0].encode(self.encoding))
                        trimmed_lines = trimmed_lines[1:]
                        current_size -= removed_line_size
                    
                    # Write back the trimmed content
                    with open(self.baseFilename, 'w', encoding=self.encoding) as f:
                        f.writelines(trimmed_lines)
        except Exception:
            # Don't log trimming errors to avoid recursion
            pass

# Create FIFO file handler (single file, max 10MB)
file_handler = FIFOFileHandler(
    log_file,
    max_bytes=10*1024*1024,  # 10MB max
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)


# =============================================================================
# Multi-Monitor Support (Windows Native)
# =============================================================================

def get_all_monitor_bounds() -> Tuple[int, int, int, int, List[Tuple[int, int, int, int]]]:
    """Get virtual screen bounds encompassing all monitors using Windows API.
    
    Returns:
        (virtual_left, virtual_top, virtual_right, virtual_bottom, list_of_monitor_rects)
        
    Uses ctypes to call Windows EnumDisplayMonitors for accurate multi-monitor detection.
    This handles:
    - Monitors with negative coordinates (left/above primary)
    - Different resolutions per monitor
    - Non-standard monitor arrangements (vertical stacking, etc.)
    - DPI scaling
    """
    try:
        import ctypes
        from ctypes import wintypes
        
        # Windows API constants
        user32 = ctypes.windll.user32
        
        monitors = []
        
        # Callback function for EnumDisplayMonitors
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,  # hMonitor
            ctypes.c_void_p,  # hdcMonitor
            ctypes.POINTER(wintypes.RECT),  # lprcMonitor
            ctypes.c_void_p   # dwData
        )
        
        def monitor_enum_callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            rect = lprcMonitor.contents
            monitors.append((rect.left, rect.top, rect.right, rect.bottom))
            return True
        
        # Enumerate all monitors
        callback = MONITORENUMPROC(monitor_enum_callback)
        user32.EnumDisplayMonitors(None, None, callback, 0)
        
        if not monitors:
            # Fallback if enumeration fails
            logger.warning("EnumDisplayMonitors returned no monitors, using fallback")
            return (0, 0, 1920, 1080, [(0, 0, 1920, 1080)])
        
        # Calculate virtual screen bounds (bounding box of all monitors)
        virtual_left = min(m[0] for m in monitors)
        virtual_top = min(m[1] for m in monitors)
        virtual_right = max(m[2] for m in monitors)
        virtual_bottom = max(m[3] for m in monitors)
        
        logger.debug(f"Detected {len(monitors)} monitors: {monitors}")
        logger.debug(f"Virtual screen bounds: ({virtual_left}, {virtual_top}) to ({virtual_right}, {virtual_bottom})")
        
        return (virtual_left, virtual_top, virtual_right, virtual_bottom, monitors)
        
    except Exception as e:
        logger.error(f"Error enumerating monitors: {e}")
        # Fallback to reasonable defaults
        return (0, 0, 1920, 1080, [(0, 0, 1920, 1080)])


def get_primary_monitor_bounds() -> Tuple[int, int, int, int]:
    """Get the bounds of the primary monitor using Windows API.
    
    Returns: (left, top, right, bottom) of the primary monitor.
    The primary monitor always contains the origin point (0, 0).
    """
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        
        # MONITOR_DEFAULTTOPRIMARY = 1
        # Get the monitor that contains point (0, 0) which is always on primary
        hMonitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
        
        if hMonitor:
            # MONITORINFO structure
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)
                ]
            
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            
            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                rect = mi.rcMonitor
                return (rect.left, rect.top, rect.right, rect.bottom)
        
        # Fallback
        return (0, 0, 1920, 1080)
        
    except Exception as e:
        logger.error(f"Error getting primary monitor: {e}")
        return (0, 0, 1920, 1080)


def is_point_on_any_monitor(x: int, y: int) -> bool:
    """Check if a point is visible on any monitor.
    
    This is more accurate than just checking virtual screen bounds because
    monitors may not form a contiguous rectangle.
    """
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        
        # MONITOR_DEFAULTTONULL = 0 - returns NULL if point not on any monitor
        point = wintypes.POINT(int(x), int(y))
        hMonitor = user32.MonitorFromPoint(point, 0)
        
        return hMonitor is not None and hMonitor != 0
        
    except Exception as e:
        logger.debug(f"Error checking point on monitor: {e}")
        # Fallback: assume point is visible if within virtual bounds
        vl, vt, vr, vb, _ = get_all_monitor_bounds()
        return vl <= x < vr and vt <= y < vb


def find_nearest_monitor_for_window(x: int, y: int, width: int, height: int) -> Tuple[int, int]:
    """Find the best position for a window that may be off-screen.
    
    Returns adjusted (x, y) coordinates that ensure the window is visible.
    Prefers keeping the window on its current monitor if partially visible,
    otherwise moves to the nearest monitor.
    """
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        
        # Check if the window center is on any monitor
        center_x = x + width // 2
        center_y = y + height // 2
        
        # MONITOR_DEFAULTTONEAREST = 2 - returns nearest monitor if not on any
        point = wintypes.POINT(int(center_x), int(center_y))
        hMonitor = user32.MonitorFromPoint(point, 2)
        
        if hMonitor:
            # Get monitor info
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),  # Work area (excludes taskbar)
                    ("dwFlags", wintypes.DWORD)
                ]
            
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            
            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                work = mi.rcWork  # Use work area to avoid taskbar
                mon_left, mon_top = work.left, work.top
                mon_right, mon_bottom = work.right, work.bottom
                mon_width = mon_right - mon_left
                mon_height = mon_bottom - mon_top
                
                # Clamp window to this monitor's work area
                new_x = max(mon_left, min(x, mon_right - width))
                new_y = max(mon_top, min(y, mon_bottom - height))
                
                # If window is larger than monitor, at least show top-left
                if width > mon_width:
                    new_x = mon_left
                if height > mon_height:
                    new_y = mon_top
                
                return (int(new_x), int(new_y))
        
        # Fallback: use primary monitor
        pm = get_primary_monitor_bounds()
        new_x = max(pm[0], min(x, pm[2] - width))
        new_y = max(pm[1], min(y, pm[3] - height))
        return (int(new_x), int(new_y))
        
    except Exception as e:
        logger.error(f"Error finding nearest monitor: {e}")
        return (50, 50)  # Safe fallback


# Version information
VERSION = "1.5 beta"
VERSION_DATE = "12/11/25"


# =============================================================================
# SQLite Database for Records Storage
# =============================================================================

class RecordsDatabase:
    """SQLite database for storing study records and shifts.
    
    Provides fast querying and scales to hundreds of thousands of records.
    Replaces JSON file storage for records (settings remain in JSON).
    
    Thread-safe: Uses a lock for all database operations since SQLite
    connections are shared across threads (check_same_thread=False).
    """
    
    def __init__(self, db_path: str):
        """Initialize database connection.
        
        Args:
            db_path: Full path to the SQLite database file
        """
        self.db_path = db_path
        self.conn = None
        self._lock = threading.Lock()  # Thread safety for database operations
        self._connect()
        self._create_tables()
        # Run migrations in background thread to avoid blocking startup
        # Fix incorrectly categorized studies (quick operation)
        self._fix_incorrectly_categorized_studies()
        # Multi-accession migration runs in background (can be slow)
        migration_thread = threading.Thread(target=self._migrate_multi_accession_records, daemon=True)
        migration_thread.start()
    
    def _connect(self):
        """Create database connection."""
        try:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row  # Enable dict-like access
            # Enable foreign keys
            self.conn.execute("PRAGMA foreign_keys = ON")
            logger.info(f"Connected to SQLite database: {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise
    
    def _create_tables(self):
        """Create database tables if they don't exist."""
        cursor = self.conn.cursor()
        
        # Shifts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_start TEXT,
                shift_end TEXT,
                is_current INTEGER DEFAULT 0,
                effective_shift_start TEXT,
                projected_shift_end TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Records table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER,
                accession TEXT NOT NULL,
                procedure TEXT,
                patient_class TEXT,
                study_type TEXT,
                rvu REAL DEFAULT 0,
                time_performed TEXT,
                time_finished TEXT,
                duration_seconds REAL,
                individual_procedures TEXT,
                individual_study_types TEXT,
                individual_rvus TEXT,
                individual_accessions TEXT,
                from_multi_accession INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE CASCADE
            )
        ''')
        
        # Add from_multi_accession column if it doesn't exist (migration for existing databases)
        try:
            cursor.execute('ALTER TABLE records ADD COLUMN from_multi_accession INTEGER DEFAULT 0')
            logger.info("Added from_multi_accession column to records table")
        except sqlite3.OperationalError:
            # Column already exists, ignore
            pass
        
        # Legacy records table (records without a shift - for backwards compatibility)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS legacy_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                accession TEXT NOT NULL,
                procedure TEXT,
                patient_class TEXT,
                study_type TEXT,
                rvu REAL DEFAULT 0,
                time_performed TEXT,
                time_finished TEXT,
                duration_seconds REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create indexes for common queries
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_shift_id ON records(shift_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_accession ON records(accession)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_records_time_performed ON records(time_performed)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_shifts_is_current ON shifts(is_current)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_shifts_shift_start ON shifts(shift_start)')
        
        self.conn.commit()
        logger.info("Database tables created/verified")
    
    def _migrate_multi_accession_records(self):
        """Migrate old multi-accession records to individual records.
        
        Finds records with individual_procedures/individual_accessions and splits them
        into separate individual records, then deletes the old multi-accession record.
        """
        try:
            with self._lock:
                if not self.conn:
                    return
                
                cursor = self.conn.cursor()
                
                # Find all records with individual data (old multi-accession format)
                # Check for records that have individual_accessions populated (JSON string)
                # OR records with study_type starting with "Multiple"
                # EXCLUDE records that are already individual records (from_multi_accession = 1)
                cursor.execute('''
                    SELECT * FROM records 
                    WHERE ((individual_accessions IS NOT NULL 
                      AND individual_accessions != ''
                      AND individual_accessions != 'null'
                      AND individual_accessions != '[]')
                    OR study_type LIKE 'Multiple %')
                    AND (from_multi_accession IS NULL OR from_multi_accession = 0)
                ''')
                
                multi_accession_records = cursor.fetchall()
                
                if not multi_accession_records:
                    logger.debug("No multi-accession records found to migrate")
                    return  # No migration needed
                
                logger.info(f"Found {len(multi_accession_records)} multi-accession records to migrate")
                
                # Pre-build a set of existing accessions for fast lookup (much faster than querying each time)
                logger.info("Building existing accessions index for fast duplicate checking...")
                cursor.execute('SELECT shift_id, accession FROM records')
                existing_accessions = {}  # {(shift_id, accession): True}
                for row in cursor.fetchall():
                    existing_accessions[(row['shift_id'], row['accession'])] = True
                logger.info(f"Indexed {len(existing_accessions)} existing records for duplicate checking")
                
                migrated_count = 0
                processed_count = 0
                for row_idx, row in enumerate(multi_accession_records):
                    processed_count += 1
                    if processed_count % 10 == 0:
                        logger.info(f"Migration progress: {processed_count}/{len(multi_accession_records)} records processed...")
                    
                    record = self._record_row_to_dict(row)
                    shift_id = row['shift_id']
                    old_record_id = row['id']
                    
                    # Parse individual data (these are JSON strings in the database)
                    individual_procedures = record.get('individual_procedures', [])
                    individual_study_types = record.get('individual_study_types', [])
                    individual_rvus = record.get('individual_rvus', [])
                    individual_accessions = record.get('individual_accessions', [])
                    
                    # If no individual_accessions but study_type is "Multiple X", try to extract from accession field
                    if not individual_accessions or len(individual_accessions) == 0:
                        # Check if study_type indicates multiple (e.g., "Multiple XR")
                        study_type = record.get('study_type', '')
                        if study_type.startswith('Multiple '):
                            # Try to parse accession field - might be comma-separated
                            accession_str = record.get('accession', '')
                            if accession_str and ',' in accession_str:
                                individual_accessions = [acc.strip() for acc in accession_str.split(',')]
                                logger.info(f"Extracted {len(individual_accessions)} accessions from comma-separated accession field for record {old_record_id}")
                        
                        if not individual_accessions or len(individual_accessions) == 0:
                            # Can't migrate this record - delete it since it's a broken multi-accession record
                            cursor.execute('DELETE FROM records WHERE id = ?', (old_record_id,))
                            self.conn.commit()
                            logger.info(f"Deleted unmigrateable multi-accession record {old_record_id} (study_type: {study_type}, no individual accessions)")
                            continue
                    
                    logger.info(f"Processing multi-accession record {old_record_id} ({processed_count}/{len(multi_accession_records)}) with {len(individual_accessions)} accessions")
                    
                    # Calculate duration per study
                    total_duration = record.get('duration_seconds', 0)
                    num_studies = len(individual_accessions)
                    duration_per_study = total_duration / num_studies if num_studies > 0 else 0
                    
                    # Batch insert individual records for better performance
                    records_to_insert = []
                    for i, accession in enumerate(individual_accessions):
                        # Fast duplicate check using pre-built index
                        if (shift_id, accession) in existing_accessions:
                            logger.debug(f"Skipping {accession} - already exists as individual record")
                            continue
                        
                        # Get corresponding data
                        procedure = individual_procedures[i] if i < len(individual_procedures) else record.get('procedure', 'Unknown')
                        study_type = individual_study_types[i] if i < len(individual_study_types) else record.get('study_type', 'Unknown')
                        rvu = individual_rvus[i] if i < len(individual_rvus) else (record.get('rvu', 0) / num_studies if num_studies > 0 else 0)
                        
                        # Create new individual record
                        individual_record = {
                            'accession': accession,
                            'procedure': procedure,
                            'patient_class': record.get('patient_class', ''),
                            'study_type': study_type,
                            'rvu': rvu,
                            'time_performed': record.get('time_performed', ''),
                            'time_finished': record.get('time_finished', ''),
                            'duration_seconds': duration_per_study,
                            'from_multi_accession': True,  # Mark as from multi-accession
                        }
                        
                        records_to_insert.append(individual_record)
                        # Add to index so we don't create duplicates within this migration
                        existing_accessions[(shift_id, accession)] = True
                    
                    # Batch insert all records for this multi-accession study using direct SQL (much faster)
                    if records_to_insert:
                        # Use direct SQL INSERT for batch operations (faster than add_record which commits each time)
                        for individual_record in records_to_insert:
                            cursor.execute('''
                                INSERT INTO records (shift_id, accession, procedure, patient_class, study_type,
                                                   rvu, time_performed, time_finished, duration_seconds,
                                individual_procedures, individual_study_types, 
                                individual_rvus, individual_accessions, from_multi_accession)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                shift_id,
                                individual_record.get('accession', ''),
                                individual_record.get('procedure', ''),
                                individual_record.get('patient_class', ''),
                                individual_record.get('study_type', ''),
                                individual_record.get('rvu', 0),
                                individual_record.get('time_performed', ''),
                                individual_record.get('time_finished', ''),
                                individual_record.get('duration_seconds', 0),
                                None,  # Don't set individual_* fields for migrated records
                                None,
                                None,
                                None,
                                1,  # Mark as from_multi_accession
                            ))
                        
                        created_count = len(records_to_insert)
                        
                        # Delete the old multi-accession record
                        delete_result = cursor.execute('DELETE FROM records WHERE id = ?', (old_record_id,))
                        deleted_rows = cursor.rowcount
                        migrated_count += 1
                        
                        # Commit after each multi-accession record (batch commit is faster than per-record commits)
                        self.conn.commit()
                        if deleted_rows > 0:
                            logger.info(f"Migrated multi-accession record {old_record_id}: created {created_count} individual records, deleted old record (rows deleted: {deleted_rows})")
                        else:
                            logger.warning(f"Migrated multi-accession record {old_record_id}: created {created_count} individual records, but DELETE returned 0 rows (record may not exist)")
                    else:
                        # Even if no new records were created (all already exist), delete the old multi-accession record
                        # The individual records already exist, so the multi-accession record is redundant
                        delete_result = cursor.execute('DELETE FROM records WHERE id = ?', (old_record_id,))
                        deleted_rows = cursor.rowcount
                        self.conn.commit()
                        if deleted_rows > 0:
                            logger.info(f"Deleted multi-accession record {old_record_id}: all individual records already exist, no migration needed (rows deleted: {deleted_rows})")
                        else:
                            logger.warning(f"Attempted to delete multi-accession record {old_record_id} but DELETE returned 0 rows (record may not exist)")
                
                # Final commit
                self.conn.commit()
                logger.info(f"Migration complete: processed {len(multi_accession_records)} multi-accession records, migrated {migrated_count}")
                
        except Exception as e:
            logger.error(f"Error migrating multi-accession records: {e}", exc_info=True)
    
    def _fix_incorrectly_categorized_studies(self):
        """Fix incorrectly categorized studies in the database.
        
        Reclassifies studies based on their procedure text using current classification rules.
        For example, "XR Chest 1 view" should be "XR Chest", not "XR Other".
        """
        try:
            with self._lock:
                if not self.conn:
                    return
                
                cursor = self.conn.cursor()
                
                # Get all records
                cursor.execute('SELECT * FROM records')
                all_records = cursor.fetchall()
                
                if not all_records:
                    return
                
                # Load classification rules and RVU table from settings
                # We need to get these from the data manager, but we don't have access here
                # So we'll use a simple approach: check procedure text for common patterns
                
                fixed_count = 0
                for row in all_records:
                    record = self._record_row_to_dict(row)
                    procedure = record.get('procedure', '').lower()
                    current_study_type = record.get('study_type', '')
                    record_id = row['id']
                    
                    # Check for XR Chest studies incorrectly categorized as XR Other
                    if current_study_type == "XR Other" and procedure:
                        # Check if procedure contains "chest"
                        if "chest" in procedure:
                            # Check if it's an XR study (should have xr, x-ray, radiograph, etc.)
                            if any(xr_term in procedure for xr_term in ["xr", "x-ray", "radiograph", "x ray"]):
                                # Update to XR Chest
                                cursor.execute('''
                                    UPDATE records 
                                    SET study_type = ? 
                                    WHERE id = ?
                                ''', ("XR Chest", record_id))
                                fixed_count += 1
                                logger.info(f"Fixed record {record_id}: XR Other -> XR Chest (procedure: {record.get('procedure', '')})")
                
                if fixed_count > 0:
                    self.conn.commit()
                    logger.info(f"Fixed {fixed_count} incorrectly categorized studies")
                else:
                    logger.debug("No incorrectly categorized studies found")
                
        except Exception as e:
            logger.error(f"Error fixing incorrectly categorized studies: {e}", exc_info=True)
    
    def close(self):
        """Close database connection."""
        with self._lock:
            if self.conn:
                self.conn.close()
                self.conn = None
                logger.info("Database connection closed")
    
    # =========================================================================
    # Shift Operations
    # =========================================================================
    
    def get_current_shift(self) -> Optional[dict]:
        """Get the current active shift."""
        with self._lock:
            if not self.conn:
                return None
            cursor = self.conn.cursor()
            cursor.execute('SELECT * FROM shifts WHERE is_current = 1 LIMIT 1')
            row = cursor.fetchone()
            if row:
                return self._shift_row_to_dict(row)
            return None
    
    def start_shift(self, shift_start: str, effective_shift_start: str = None, 
                   projected_shift_end: str = None) -> int:
        """Start a new shift. Returns the shift ID."""
        # End any existing current shift first
        self.end_current_shift()
        
        with self._lock:
            if not self.conn:
                return -1
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO shifts (shift_start, is_current, effective_shift_start, projected_shift_end)
                VALUES (?, 1, ?, ?)
            ''', (shift_start, effective_shift_start, projected_shift_end))
            self.conn.commit()
            
            shift_id = cursor.lastrowid
            logger.info(f"Started new shift: ID={shift_id}, start={shift_start}")
            return shift_id
    
    def end_current_shift(self, shift_end: str = None) -> Optional[int]:
        """End the current shift. Returns the shift ID or None."""
        if shift_end is None:
            shift_end = datetime.now().isoformat()
        
        current = self.get_current_shift()
        if current:
            with self._lock:
                if not self.conn:
                    return None
                cursor = self.conn.cursor()
                cursor.execute('''
                    UPDATE shifts SET shift_end = ?, is_current = 0 WHERE is_current = 1
                ''', (shift_end,))
                self.conn.commit()
            logger.info(f"Ended shift: ID={current['id']}, end={shift_end}")
            return current['id']
        return None
    
    def get_all_shifts(self) -> List[dict]:
        """Get all historical shifts (not including current)."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM shifts WHERE is_current = 0 ORDER BY shift_start DESC
        ''')
        return [self._shift_row_to_dict(row) for row in cursor.fetchall()]
    
    def get_shift_by_id(self, shift_id: int) -> Optional[dict]:
        """Get a specific shift by ID."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shifts WHERE id = ?', (shift_id,))
        row = cursor.fetchone()
        if row:
            return self._shift_row_to_dict(row)
        return None
    
    def delete_shift(self, shift_id: int):
        """Delete a shift and all its records."""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM shifts WHERE id = ?', (shift_id,))
        self.conn.commit()
        logger.info(f"Deleted shift: ID={shift_id}")
    
    def update_current_shift_times(self, effective_shift_start: str = None, 
                                   projected_shift_end: str = None):
        """Update the effective start and projected end times for current shift."""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE shifts SET effective_shift_start = ?, projected_shift_end = ?
            WHERE is_current = 1
        ''', (effective_shift_start, projected_shift_end))
        self.conn.commit()
    
    def _shift_row_to_dict(self, row) -> dict:
        """Convert a shift database row to a dictionary."""
        return {
            'id': row['id'],
            'shift_start': row['shift_start'],
            'shift_end': row['shift_end'],
            'is_current': bool(row['is_current']),
            'effective_shift_start': row['effective_shift_start'],
            'projected_shift_end': row['projected_shift_end']
        }
    
    # =========================================================================
    # Record Operations
    # =========================================================================
    
    def add_record(self, shift_id: int, record: dict) -> int:
        """Add a record to a shift. Returns the record ID."""
        with self._lock:
            if not self.conn:
                return -1
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO records (shift_id, accession, procedure, patient_class, study_type,
                                   rvu, time_performed, time_finished, duration_seconds,
                                   individual_procedures, individual_study_types, 
                                   individual_rvus, individual_accessions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                shift_id,
                record.get('accession', ''),
                record.get('procedure', ''),
                record.get('patient_class', ''),
                record.get('study_type', ''),
                record.get('rvu', 0),
                record.get('time_performed', ''),
                record.get('time_finished', ''),
                record.get('duration_seconds', 0),
                json.dumps(record.get('individual_procedures')) if record.get('individual_procedures') else None,
                json.dumps(record.get('individual_study_types')) if record.get('individual_study_types') else None,
                json.dumps(record.get('individual_rvus')) if record.get('individual_rvus') else None,
                json.dumps(record.get('individual_accessions')) if record.get('individual_accessions') else None,
            ))
            self.conn.commit()
            return cursor.lastrowid
    
    def update_record(self, record_id: int, record: dict):
        """Update an existing record."""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE records SET
                procedure = ?, patient_class = ?, study_type = ?, rvu = ?,
                time_performed = ?, time_finished = ?, duration_seconds = ?,
                individual_procedures = ?, individual_study_types = ?,
                individual_rvus = ?, individual_accessions = ?
            WHERE id = ?
        ''', (
            record.get('procedure', ''),
            record.get('patient_class', ''),
            record.get('study_type', ''),
            record.get('rvu', 0),
            record.get('time_performed', ''),
            record.get('time_finished', ''),
            record.get('duration_seconds', 0),
            json.dumps(record.get('individual_procedures')) if record.get('individual_procedures') else None,
            json.dumps(record.get('individual_study_types')) if record.get('individual_study_types') else None,
            json.dumps(record.get('individual_rvus')) if record.get('individual_rvus') else None,
            json.dumps(record.get('individual_accessions')) if record.get('individual_accessions') else None,
            record_id
        ))
        self.conn.commit()
    
    def delete_record(self, record_id: int):
        """Delete a record by ID."""
        with self._lock:
            if not self.conn:
                return
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM records WHERE id = ?', (record_id,))
            self.conn.commit()
        logger.debug(f"Deleted record: ID={record_id}")
    
    def delete_record_by_accession(self, shift_id: int, accession: str):
        """Delete a record by accession within a shift."""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM records WHERE shift_id = ? AND accession = ?', 
                      (shift_id, accession))
        self.conn.commit()
    
    def get_records_for_shift(self, shift_id: int) -> List[dict]:
        """Get all records for a specific shift."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT * FROM records WHERE shift_id = ? ORDER BY time_performed ASC
        ''', (shift_id,))
        return [self._record_row_to_dict(row) for row in cursor.fetchall()]
    
    def get_current_shift_records(self) -> List[dict]:
        """Get records for the current active shift."""
        current = self.get_current_shift()
        if current:
            return self.get_records_for_shift(current['id'])
        return []
    
    def find_record_by_accession(self, shift_id: int, accession: str) -> Optional[dict]:
        """Find a record by accession within a shift."""
        with self._lock:
            if not self.conn:
                return None
            cursor = self.conn.cursor()
            cursor.execute('''
                SELECT * FROM records WHERE shift_id = ? AND accession = ? LIMIT 1
            ''', (shift_id, accession))
            row = cursor.fetchone()
            if row:
                return self._record_row_to_dict(row)
            return None
    
    def get_records_in_date_range(self, start_date: str, end_date: str) -> List[dict]:
        """Get all records within a date range."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT r.*, s.shift_start, s.shift_end 
            FROM records r
            JOIN shifts s ON r.shift_id = s.id
            WHERE r.time_performed >= ? AND r.time_performed <= ?
            ORDER BY r.time_performed ASC
        ''', (start_date, end_date))
        return [self._record_row_to_dict(row) for row in cursor.fetchall()]
    
    def get_all_records(self) -> List[dict]:
        """Get all records from all shifts."""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT r.*, s.shift_start, s.shift_end
            FROM records r
            JOIN shifts s ON r.shift_id = s.id
            ORDER BY r.time_performed DESC
        ''')
        return [self._record_row_to_dict(row) for row in cursor.fetchall()]
    
    def _record_row_to_dict(self, row) -> dict:
        """Convert a record database row to a dictionary."""
        record = {
            'id': row['id'],
            'shift_id': row['shift_id'],
            'accession': row['accession'],
            'procedure': row['procedure'],
            'patient_class': row['patient_class'],
            'study_type': row['study_type'],
            'rvu': row['rvu'],
            'time_performed': row['time_performed'],
            'time_finished': row['time_finished'],
            'duration_seconds': row['duration_seconds'],
        }
        
        # Parse JSON fields for multi-accession studies
        if row['individual_procedures']:
            try:
                record['individual_procedures'] = json.loads(row['individual_procedures'])
            except:
                pass
        if row['individual_study_types']:
            try:
                record['individual_study_types'] = json.loads(row['individual_study_types'])
            except:
                pass
        if row['individual_rvus']:
            try:
                record['individual_rvus'] = json.loads(row['individual_rvus'])
            except:
                pass
        if row['individual_accessions']:
            try:
                record['individual_accessions'] = json.loads(row['individual_accessions'])
            except:
                pass
        
        return record
    
    # =========================================================================
    # Legacy Records (records without shifts - for backwards compatibility)
    # =========================================================================
    
    def add_legacy_record(self, record: dict) -> int:
        """Add a legacy record (not associated with a shift)."""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT INTO legacy_records (accession, procedure, patient_class, study_type,
                                       rvu, time_performed, time_finished, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            record.get('accession', ''),
            record.get('procedure', ''),
            record.get('patient_class', ''),
            record.get('study_type', ''),
            record.get('rvu', 0),
            record.get('time_performed', ''),
            record.get('time_finished', ''),
            record.get('duration_seconds', 0),
        ))
        self.conn.commit()
        return cursor.lastrowid
    
    def get_legacy_records(self) -> List[dict]:
        """Get all legacy records."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM legacy_records ORDER BY time_performed DESC')
        return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Statistics and Aggregation
    # =========================================================================
    
    def get_total_rvu_for_shift(self, shift_id: int) -> float:
        """Get total RVU for a shift."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT SUM(rvu) FROM records WHERE shift_id = ?', (shift_id,))
        result = cursor.fetchone()[0]
        return result if result else 0.0
    
    def get_record_count_for_shift(self, shift_id: int) -> int:
        """Get total number of records for a shift."""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM records WHERE shift_id = ?', (shift_id,))
        return cursor.fetchone()[0]
    
    def get_stats_by_study_type(self, shift_id: int = None) -> dict:
        """Get RVU and count statistics grouped by study type."""
        cursor = self.conn.cursor()
        if shift_id:
            cursor.execute('''
                SELECT study_type, SUM(rvu) as total_rvu, COUNT(*) as count
                FROM records WHERE shift_id = ?
                GROUP BY study_type
            ''', (shift_id,))
        else:
            cursor.execute('''
                SELECT study_type, SUM(rvu) as total_rvu, COUNT(*) as count
                FROM records
                GROUP BY study_type
            ''')
        return {row['study_type']: {'rvu': row['total_rvu'], 'count': row['count']} 
                for row in cursor.fetchall()}
    
    # =========================================================================
    # Migration from JSON
    # =========================================================================
    
    def migrate_from_json(self, json_data: dict):
        """Migrate data from JSON format to SQLite.
        
        Args:
            json_data: Dictionary containing 'records', 'current_shift', and 'shifts'
        """
        logger.info("Starting migration from JSON to SQLite...")
        
        # Migrate legacy records
        legacy_records = json_data.get('records', [])
        for record in legacy_records:
            self.add_legacy_record(record)
        logger.info(f"Migrated {len(legacy_records)} legacy records")
        
        # Migrate historical shifts
        shifts = json_data.get('shifts', [])
        for shift_data in shifts:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO shifts (shift_start, shift_end, is_current, 
                                   effective_shift_start, projected_shift_end)
                VALUES (?, ?, 0, ?, ?)
            ''', (
                shift_data.get('shift_start'),
                shift_data.get('shift_end'),
                shift_data.get('effective_shift_start'),
                shift_data.get('projected_shift_end')
            ))
            shift_id = cursor.lastrowid
            
            # Add records for this shift
            for record in shift_data.get('records', []):
                record_copy = record.copy()
                self.add_record(shift_id, record_copy)
            
            self.conn.commit()
        logger.info(f"Migrated {len(shifts)} historical shifts")
        
        # Migrate current shift
        current_shift = json_data.get('current_shift', {})
        if current_shift.get('shift_start') or current_shift.get('records'):
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO shifts (shift_start, shift_end, is_current,
                                   effective_shift_start, projected_shift_end)
                VALUES (?, ?, 1, ?, ?)
            ''', (
                current_shift.get('shift_start'),
                current_shift.get('shift_end'),
                current_shift.get('effective_shift_start'),
                current_shift.get('projected_shift_end')
            ))
            shift_id = cursor.lastrowid
            
            for record in current_shift.get('records', []):
                self.add_record(shift_id, record)
            
            self.conn.commit()
            logger.info(f"Migrated current shift with {len(current_shift.get('records', []))} records")
        
        logger.info("JSON to SQLite migration complete!")
    
    # =========================================================================
    # Export to JSON (for backups and compatibility)
    # =========================================================================
    
    def export_to_json(self) -> dict:
        """Export all data to JSON format (for backups)."""
        data = {
            'records': self.get_legacy_records(),
            'current_shift': {
                'shift_start': None,
                'shift_end': None,
                'records': []
            },
            'shifts': []
        }
        
        # Get current shift
        current = self.get_current_shift()
        if current:
            data['current_shift'] = {
                'shift_start': current['shift_start'],
                'shift_end': current['shift_end'],
                'records': self.get_records_for_shift(current['id']),
                'effective_shift_start': current.get('effective_shift_start'),
                'projected_shift_end': current.get('projected_shift_end')
            }
        
        # Get historical shifts
        for shift in self.get_all_shifts():
            shift_data = {
                'shift_start': shift['shift_start'],
                'shift_end': shift['shift_end'],
                'records': self.get_records_for_shift(shift['id']),
                'effective_shift_start': shift.get('effective_shift_start'),
                'projected_shift_end': shift.get('projected_shift_end')
            }
            data['shifts'].append(shift_data)
        
        return data
    
    def export_to_json_file(self, filepath: str):
        """Export all data to a JSON file."""
        data = self.export_to_json()
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Exported database to JSON: {filepath}")


# RVU Lookup Table - REMOVED: Now using rvu_settings.json as single source of truth
# All RVU values must come from the loaded JSON file


# Global cached desktop object - creating Desktop() is slow
_cached_desktop = None


# Track orphan timeout threads for monitoring (daemon threads, so they won't prevent exit)
_timeout_thread_count = 0
_timeout_thread_lock = threading.Lock()


def _extract_accession_number(entry: str) -> str:
    """Extract pure accession number from entry string.
    
    Handles formats like "ACC1234 (CT HEAD)" -> "ACC1234" or just "ACC1234" -> "ACC1234".
    Used by multi-accession tracking logic.
    
    Args:
        entry: Raw listbox entry or accession string
        
    Returns:
        Stripped accession number
    """
    if '(' in entry and ')' in entry:
        m = re.match(r'^([^(]+)', entry)
        return m.group(1).strip() if m else entry.strip()
    return entry.strip()


def _window_text_with_timeout(element, timeout=1.0, element_name=""):
    """Read window_text() with a timeout to prevent blocking.
    
    When PowerScribe transitions between studies, window_text() can block for
    extended periods (10-18 seconds). This wrapper prevents the worker thread
    from freezing by timing out after the specified duration.
    
    Note: When timeout occurs, the spawned thread becomes orphaned (blocking on 
    the UI call). These are daemon threads so they won't prevent app exit, but 
    they consume resources until the blocking call eventually returns or the 
    app exits. We track the count for monitoring purposes.
    
    Args:
        element: The UI element to read text from
        timeout: Maximum time to wait in seconds (default 1.0)
        element_name: Name/ID of element for logging (optional)
    
    Returns:
        str: The window text, or empty string if timeout/failure occurs
    """
    global _timeout_thread_count
    import time
    result = [None]
    exception = [None]
    start = time.time()
    
    def read_text():
        global _timeout_thread_count
        try:
            result[0] = element.window_text()
        except Exception as e:
            exception[0] = e
        finally:
            # If we were an orphan thread that finally completed, decrement count
            with _timeout_thread_lock:
                if _timeout_thread_count > 0:
                    _timeout_thread_count -= 1
    
    thread = threading.Thread(target=read_text, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    elapsed = time.time() - start
    
    if thread.is_alive():
        # Thread is still running - window_text() is blocking
        # Track orphan thread count for monitoring
        with _timeout_thread_lock:
            _timeout_thread_count += 1
            orphan_count = _timeout_thread_count
        logger.warning(f"window_text() call timed out after {timeout}s for {element_name} (orphan threads: {orphan_count})")
        return ""
    
    if exception[0]:
        logger.debug(f"window_text() exception for {element_name}: {exception[0]}")
        raise exception[0]
    
    return result[0] if result[0] else ""


def quick_check_powerscribe() -> bool:
    """Quick check if PowerScribe window exists (fast, no deep inspection)."""
    global _cached_desktop
    
    if _cached_desktop is None:
        _cached_desktop = Desktop(backend="uia")
    desktop = _cached_desktop
    
    # Just check if window with PowerScribe title exists
    for title in ["PowerScribe 360 | Reporting", "PowerScribe 360", "PowerScribe 360 - Reporting", 
                  "Nuance PowerScribe 360", "Powerscribe 360"]:
        try:
            windows = desktop.windows(title=title, visible_only=True)
            if windows:
                return True
        except Exception as e:
            logger.debug(f"Error checking PowerScribe window '{title}': {e}")
            continue
    return False


def quick_check_mosaic() -> bool:
    """Quick check if Mosaic window exists (fast, no deep inspection)."""
    global _cached_desktop
    
    if _cached_desktop is None:
        _cached_desktop = Desktop(backend="uia")
    desktop = _cached_desktop
    
    try:
        all_windows = desktop.windows(visible_only=True)
        for window in all_windows:
            try:
                # Quick title check without deep inspection - USE TIMEOUT to prevent hanging
                title = _window_text_with_timeout(window, timeout=0.5, element_name="Mosaic quick check")
                title_lower = title.lower()
                # Check for MosaicInfoHub variations and Mosaic Reporting
                if ("mosaicinfohub" in title_lower or 
                    "mosaic info hub" in title_lower or 
                    "mosaic infohub" in title_lower or
                    ("mosaic" in title_lower and "reporting" in title_lower)):
                    # Exclude test windows
                    if not any(x in title_lower for x in ["rvu counter", "test", "viewer", "diagnostic"]):
                        return True
            except Exception as e:
                logger.debug(f"Error checking Mosaic window: {e}")
                continue
    except Exception as e:
        logger.debug(f"Error iterating windows for Mosaic check: {e}")
    return False


def find_powerscribe_window():
    """Find PowerScribe 360 window by title."""
    global _cached_desktop
    
    # Reuse cached Desktop object
    if _cached_desktop is None:
        _cached_desktop = Desktop(backend="uia")
    desktop = _cached_desktop
    
    # Try exact title first (fastest)
    try:
        windows = desktop.windows(title="PowerScribe 360 | Reporting", visible_only=True)
        if windows:
            return windows[0]
    except Exception as e:
        logger.debug(f"Error finding PowerScribe window by exact title: {e}")
    
    # Try other common titles including Nuance variations
    for title in ["PowerScribe 360", "PowerScribe 360 - Reporting", "Nuance PowerScribe 360", "Powerscribe 360"]:
        try:
            windows = desktop.windows(title=title, visible_only=True)
            for window in windows:
                try:
                    window_text = _window_text_with_timeout(window, timeout=1.0, element_name="PowerScribe window check")
                    if "RVU Counter" not in window_text:
                        return window
                except Exception as e:
                    logger.debug(f"Error checking window text for '{title}': {e}")
                    continue
        except Exception as e:
            logger.debug(f"Error finding windows with title '{title}': {e}")
            continue
    
    return None


def find_mosaic_window():
    """Find Mosaic Info Hub window - it's a WinForms app with WebView2."""
    global _cached_desktop
    
    # Reuse cached Desktop object
    if _cached_desktop is None:
        _cached_desktop = Desktop(backend="uia")
    desktop = _cached_desktop
    
    try:
        all_windows = desktop.windows(visible_only=True)
        for window in all_windows:
            try:
                window_text = _window_text_with_timeout(window, timeout=1.0, element_name="Mosaic window check").lower()
                # Exclude test/viewer windows and RVU Counter
                if ("rvu counter" in window_text or 
                    "test" in window_text or 
                    "viewer" in window_text or 
                    "ui elements" in window_text or
                    "diagnostic" in window_text):
                    continue
                
                # Look for Mosaic Info Hub window - handle variations:
                # "MosaicInfoHub", "Mosaic Info Hub", "Mosaic InfoHub", "Mosaic Reporting"
                is_mosaic = ("mosaicinfohub" in window_text or 
                            ("mosaic" in window_text and "info" in window_text and "hub" in window_text) or
                            ("mosaic" in window_text and "reporting" in window_text))
                if is_mosaic:
                    # Verify it has the MainForm automation ID
                    try:
                        automation_id = window.element_info.automation_id
                        if automation_id == "MainForm":
                            return window
                    except Exception as e:
                        logger.debug(f"Error checking Mosaic automation ID: {e}")
                        # If we can't check automation ID, still return it if it matches
                        return window
            except:
                continue
    except:
        pass
    
    return None


def find_mosaic_webview_element(main_window):
    """Find the WebView2 control inside the Mosaic main window."""
    try:
        # The WebView2 has automation_id = "webView"
        # Limit iteration to prevent blocking
        children_list = []
        try:
            children_gen = main_window.children()
            count = 0
            for child_elem in children_gen:
                children_list.append(child_elem)
                count += 1
                if count >= 50:  # Limit to prevent blocking
                    break
        except Exception as e:
            logger.debug(f"main_window.children() iteration failed: {e}")
            children_list = []
        
        for child in children_list:
            try:
                automation_id = child.element_info.automation_id
                if automation_id == "webView":
                    return child
            except:
                continue
    except:
        pass
    
    # Fallback: search recursively
    try:
        # Limit iteration to prevent blocking
        descendants_list = []
        try:
            descendants_gen = main_window.descendants()
            count = 0
            for elem in descendants_gen:
                descendants_list.append(elem)
                count += 1
                if count >= 1000:  # Limit to prevent excessive blocking
                    break
        except Exception as e:
            logger.debug(f"main_window.descendants() iteration failed: {e}")
            descendants_list = []
        
        for child in descendants_list:
            try:
                automation_id = child.element_info.automation_id
                if automation_id == "webView":
                    return child
            except:
                continue
    except:
        pass
    
    return None
                
def get_mosaic_elements(webview_element, depth=0, max_depth=20):
    """Recursively get all UI elements from WebView2."""
    elements = []
    
    if depth > max_depth:
        return elements
    
    try:
        try:
            automation_id = webview_element.element_info.automation_id or ""
        except:
            automation_id = ""
        
        try:
            name = webview_element.element_info.name or ""
        except:
            name = ""
        
        try:
            text = _window_text_with_timeout(webview_element, timeout=0.5, element_name="mosaic element") or ""
        except:
            text = ""
        
        if automation_id or name or text:
            elements.append({
                'depth': depth,
                'automation_id': automation_id,
                'name': name,
                'text': text[:100] if text else "",
                'element': webview_element,
            })
        
        # Recursively get children
        try:
            # Limit iteration to prevent blocking
            children_list = []
            try:
                children_gen = webview_element.children()
                count = 0
                for child_elem in children_gen:
                    children_list.append(child_elem)
                    count += 1
                    if count >= 50:  # Limit to prevent blocking
                        break
            except Exception as e:
                logger.debug(f"webview_element.children() iteration failed: {e}")
                children_list = []
            
            for child in children_list:
                elements.extend(get_mosaic_elements(child, depth + 1, max_depth))
        except:
            pass
    except:
        pass
    
    return elements


# Clario extraction functions (for patient class lookup)
_clario_cache = {
    'chrome_window': None,
    'content_area': None
}


def find_clario_chrome_window(use_cache=True):
    """Find Chrome window with 'Clario - Worklist' tab.
    
    Uses cache if available and valid, only searches if cache is invalid or missing.
    """
    global _clario_cache
    
    # Check cache first
    if use_cache and _clario_cache['chrome_window']:
        try:
            _ = _window_text_with_timeout(_clario_cache['chrome_window'], timeout=1.0, element_name="Clario cache validation")
            return _clario_cache['chrome_window']
        except Exception as e:
            logger.debug(f"Clario cache validation failed, clearing cache: {e}")
            _clario_cache['chrome_window'] = None
            _clario_cache['content_area'] = None
    
    # Search for window
    desktop = Desktop(backend="uia")
    
    try:
        all_windows = desktop.windows(visible_only=True)
        for window in all_windows:
            try:
                window_text = _window_text_with_timeout(window, timeout=1.0, element_name="Clario window check").lower()
                # Exclude test/viewer windows and RVU Counter
                if ("rvu counter" in window_text or 
                    "test" in window_text or 
                    "viewer" in window_text or 
                    "ui elements" in window_text or
                    "diagnostic" in window_text):
                    continue
                
                # Look for Chrome window with "clario" and "worklist" in title
                if "clario" in window_text and "worklist" in window_text:
                    try:
                        class_name = window.element_info.class_name.lower()
                        if "chrome" in class_name:
                            _clario_cache['chrome_window'] = window
                            return window
                    except Exception as e:
                        # If we can't check class name, still return it if title matches
                        logger.debug(f"Couldn't check Clario class name: {e}")
                        _clario_cache['chrome_window'] = window
                        return window
            except Exception as e:
                logger.debug(f"Error checking window for Clario: {e}")
                continue
    except Exception as e:
        logger.debug(f"Error iterating windows for Clario: {e}")
    
    return None


def find_clario_content_area(chrome_window, use_cache=True):
    """Find the Chrome content area (where the web page is rendered)."""
    global _clario_cache
    
    # Check cache first
    if use_cache and _clario_cache['content_area']:
        try:
            _ = _clario_cache['content_area'].element_info.control_type
            return _clario_cache['content_area']
        except:
            _clario_cache['content_area'] = None
    
    if not chrome_window:
        return None
    
    try:
        # Look for elements with control_type "Document" or "Pane"
        # Limit iteration to prevent blocking
        descendants_list = []
        try:
            descendants_gen = chrome_window.descendants()
            count = 0
            for elem in descendants_gen:
                descendants_list.append(elem)
                count += 1
                if count >= 1000:  # Limit to prevent excessive blocking
                    break
        except Exception as e:
            logger.debug(f"chrome_window.descendants() iteration failed: {e}")
            descendants_list = []
        
        for child in descendants_list:
            try:
                control_type = child.element_info.control_type
                if control_type in ["Document", "Pane"]:
                    try:
                        name = child.element_info.name or ""
                        if name and len(name) > 10:
                            _clario_cache['content_area'] = child
                            return child
                    except:
                        pass
            except:
                continue
    except:
        pass
    
    # Fallback: try to find by automation_id patterns
    try:
        # Limit iteration to prevent blocking
        descendants_list = []
        try:
            descendants_gen = chrome_window.descendants()
            count = 0
            for elem in descendants_gen:
                descendants_list.append(elem)
                count += 1
                if count >= 1000:  # Limit to prevent excessive blocking
                    break
        except Exception as e:
            logger.debug(f"chrome_window.descendants() fallback iteration failed: {e}")
            descendants_list = []
        
        for child in descendants_list:
            try:
                automation_id = child.element_info.automation_id or ""
                if "content" in automation_id.lower() or "render" in automation_id.lower():
                    _clario_cache['content_area'] = child
                    return child
            except:
                continue
    except:
        pass
    
    # Last resort: return the window itself
    _clario_cache['content_area'] = chrome_window
    return chrome_window


def _combine_priority_and_class_clario(data):
    """Combine Priority and Class into a single patient_class string."""
    priority_value = data.get('priority', '').strip()
    class_value = data.get('class', '').strip()
    
    # Normalize: Replace ED/ER with "Emergency"
    if priority_value:
        priority_value = priority_value.replace('ED', 'Emergency').replace('ER', 'Emergency')
    if class_value:
        class_value = class_value.replace('ED', 'Emergency').replace('ER', 'Emergency')
    
    # Define urgency terms and location terms
    urgency_terms = ['STAT', 'Stroke', 'Urgent', 'Routine', 'ASAP', 'CRITICAL', 'IMMEDIATE', 'Trauma']
    location_terms = ['Emergency', 'Inpatient', 'Outpatient', 'Observation', 'Ambulatory']
    
    # Extract urgency from Priority
    urgency_parts = []
    location_from_priority = []
    
    if priority_value:
        priority_parts = priority_value.strip().split()
        for part in priority_parts:
            part_upper = part.upper()
            is_urgency = any(term.upper() in part_upper for term in urgency_terms)
            is_location = any(term.lower() in part.lower() for term in location_terms)
            
            if is_urgency:
                urgency_parts.append(part)
            elif is_location:
                location_from_priority.append(part)
    
    # Extract location from Class
    location_from_class = ''
    if class_value:
        class_clean = class_value.strip()
        for location_term in location_terms:
            if location_term.lower() in class_clean.lower():
                location_from_class = location_term
                break
        if not location_from_class:
            location_from_class = class_clean
    
    # Determine final location (prefer Class over Priority)
    final_location = location_from_class if location_from_class else ' '.join(location_from_priority) if location_from_priority else ''
    
    # Remove redundant location from urgency parts
    if final_location:
        final_location_lower = final_location.lower()
        urgency_parts = [part for part in urgency_parts if part.lower() not in final_location_lower]
    
    # Combine: urgency + location
    combined_parts = []
    if urgency_parts:
        combined_parts.extend(urgency_parts)
    if final_location:
        combined_parts.append(final_location)
    
    data['patient_class'] = ' '.join(combined_parts).strip()


def extract_clario_patient_class(target_accession=None):
    """Extract patient class from Clario - Worklist.
    
    Args:
        target_accession: Optional accession to match. If provided, only returns data if accession matches.
    
    Returns:
        dict with 'patient_class' and 'accession', or None if not found/doesn't match
    """
    try:
        # Find Chrome window
        chrome_window = find_clario_chrome_window(use_cache=True)
        if not chrome_window:
            logger.info("Clario: Chrome window not found")
            return None
        
        # Find content area
        content_area = find_clario_content_area(chrome_window, use_cache=True)
        if not content_area:
            logger.info("Clario: Content area not found")
            return None
        
        # Staggered depth search: try 12, then 18, then 25, stopping if data is found
        # Use a helper function to get elements (similar to get_mosaic_elements)
        def get_all_elements_clario(element, depth=0, max_depth=15):
            """Recursively get all UI elements from a window. EXACT COPY from testClario.py."""
            elements = []
            if depth > max_depth:
                return elements
            try:
                # Get element info - EXACT COPY from testClario.py
                try:
                    automation_id = element.element_info.automation_id or ""
                except:
                    automation_id = ""
                try:
                    name = element.element_info.name or ""
                except:
                    name = ""
                try:
                    # Use direct window_text() like testClario.py - Clario extraction runs in separate thread
                    text = element.window_text() or ""
                except:
                    text = ""
                
                # Only include elements with some meaningful content
                if automation_id or name or text:
                    elements.append({
                        'depth': depth,
                        'automation_id': automation_id,
                        'name': name,
                        'text': text[:100] if text else "",  # Limit text length like testClario
                    })
                
                # Recursively get children - EXACT COPY from testClario.py
                try:
                    children = element.children()
                    for child in children:
                        elements.extend(get_all_elements_clario(child, depth + 1, max_depth))
                except:
                    pass
            except:
                pass
            return elements
        
        def extract_data_from_elements(element_data):
            """Extract priority, class, and accession from element data."""
            data = {'priority': '', 'class': '', 'accession': '', 'patient_class': ''}
            
            # Log all automation_ids that contain "class" to debug
            class_automation_ids = [e.get('automation_id', '') for e in element_data if 'class' in e.get('automation_id', '').lower()]
            if class_automation_ids:
                logger.debug(f"Clario: Found {len(class_automation_ids)} elements with 'class' in automation_id: {class_automation_ids[:5]}")
            
            for i, elem in enumerate(element_data):
                if data['priority'] and data['class'] and data['accession']:
                    break
                    
                name = elem['name']
                text = elem['text']
                automation_id = elem['automation_id']
                
                # Log when we find a Class automation_id
                if automation_id and 'class' in automation_id.lower() and 'priority' not in automation_id.lower():
                    logger.debug(f"Clario: Found Class automation_id='{automation_id}' at index {i}, name='{name}', text='{text}'")
                
                # PRIORITY
                if not data['priority']:
                    if automation_id and 'priority' in automation_id.lower():
                        for j in range(i+1, min(i+10, len(element_data))):
                            next_elem = element_data[j]
                            next_name = next_elem['name']
                            next_text = next_elem['text']
                            if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                data['priority'] = next_name
                                break
                            elif next_text and ':' not in next_text and next_text.lower() not in ['priority', 'class', 'accession']:
                                data['priority'] = next_text
                                break
                    elif name and 'priority' in name.lower() and ':' in name:
                        for j in range(i+1, min(i+10, len(element_data))):
                            next_elem = element_data[j]
                            next_name = next_elem['name']
                            if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                data['priority'] = next_name
                                break
                
                # CLASS - EXACT COPY from testClario.py
                if not data['class']:
                    if automation_id and 'class' in automation_id.lower() and 'priority' not in automation_id.lower():
                        for j in range(i+1, min(i+10, len(element_data))):
                            next_elem = element_data[j]
                            next_name = next_elem['name']
                            next_text = next_elem['text']
                            if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                data['class'] = next_name
                                break
                            elif next_text and ':' not in next_text and next_text.lower() not in ['priority', 'class', 'accession']:
                                data['class'] = next_text
                                break
                    elif name and 'class' in name.lower() and ':' in name and 'priority' not in name.lower():
                        for j in range(i+1, min(i+10, len(element_data))):
                            next_elem = element_data[j]
                            next_name = next_elem['name']
                            if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                data['class'] = next_name
                                break
                
                # ACCESSION
                if not data['accession']:
                    if automation_id and 'accession' in automation_id.lower():
                        for j in range(i+1, min(i+10, len(element_data))):
                            next_elem = element_data[j]
                            next_name = next_elem['name']
                            next_text = next_elem['text']
                            if next_name and ':' not in next_name and len(next_name) > 5 and ' ' not in next_name:
                                data['accession'] = next_name
                                break
                            elif next_text and ':' not in next_text and len(next_text) > 5 and ' ' not in next_text:
                                data['accession'] = next_text
                                break
                    elif name and 'accession' in name.lower() and ':' in name:
                        for j in range(i+1, min(i+10, len(element_data))):
                            next_elem = element_data[j]
                            next_name = next_elem['name']
                            if next_name and ':' not in next_name and len(next_name) > 5 and ' ' not in next_name:
                                data['accession'] = next_name
                                break
            
            return data
        
        # Staggered depth search: try 12, then 18, then 25, stopping if all three are found
        data = {'priority': '', 'class': '', 'accession': '', 'patient_class': ''}
        search_depths = [12, 18, 25]
        
        for max_depth in search_depths:
            logger.debug(f"Clario: Searching at depth {max_depth}")
            all_elements = get_all_elements_clario(content_area, max_depth=max_depth)
            
            # Convert to list - EXACT COPY from testClario.py
            element_data = []
            for elem in all_elements:
                name = elem.get('name', '').strip()
                text = elem.get('text', '').strip()
                automation_id = elem.get('automation_id', '').strip()
                if name or text or automation_id:
                    element_data.append({
                        'name': name,
                        'text': text,
                        'automation_id': automation_id,
                        'depth': elem.get('depth', 0)
                    })
            
            # Extract data from elements at this depth
            extracted_data = extract_data_from_elements(element_data)
            
            # Update data with any newly found values
            if not data['priority'] and extracted_data['priority']:
                data['priority'] = extracted_data['priority']
                logger.debug(f"Clario: Found Priority='{data['priority']}' at depth {max_depth}")
            if not data['class'] and extracted_data['class']:
                data['class'] = extracted_data['class']
                logger.debug(f"Clario: Found Class='{data['class']}' at depth {max_depth}")
            if not data['accession'] and extracted_data['accession']:
                data['accession'] = extracted_data['accession']
                logger.debug(f"Clario: Found Accession='{data['accession']}' at depth {max_depth}")
            
            # Stop if we found all three required values
            if data['priority'] and data['class'] and data['accession']:
                logger.debug(f"Clario: Found all three values at depth {max_depth}, stopping search")
                break
        
        # Check if we found all required data
        if not (data['priority'] or data['class']):
            logger.debug(f"Clario: No priority or class found. Priority='{data['priority']}', Class='{data['class']}'")
            return None
        
        # Log raw extracted data BEFORE combining (helps debug if class is missing)
        logger.info(f"Clario: Extracted raw data - Priority='{data['priority']}', Class='{data['class']}', Accession='{data['accession']}'")
        
        # Combine priority and class
        _combine_priority_and_class_clario(data)
        
        logger.debug(f"Clario: After combining - Priority='{data['priority']}', Class='{data['class']}', Combined='{data['patient_class']}', Accession='{data['accession']}'")
        
        # If target_accession provided, verify it matches
        # If target_accession is None, we'll accept any accession (for multi-accession matching)
        if target_accession is not None:
            if data['accession'] and data['accession'].strip() != target_accession.strip():
                # Accession doesn't match - return None
                logger.debug(f"Clario: Accession mismatch - expected '{target_accession}', got '{data['accession']}'")
                return None
        
        # Return patient class and accession
        if data['patient_class']:
            logger.debug(f"Clario: Returning patient_class='{data['patient_class']}', accession='{data['accession']}'")
            return {
                'patient_class': data['patient_class'],
                'accession': data['accession']
            }
        
        logger.info(f"Clario: No patient_class found. Priority='{data.get('priority', '')}', Class='{data.get('class', '')}', Accession='{data.get('accession', '')}'")
        return None
    except Exception as e:
        logger.info(f"Clario extraction error: {e}", exc_info=True)
        return None


# =============================================================================
# MOSAIC DATA EXTRACTION - NEW METHOD (v2)
# Uses main window descendants() for reliable element discovery.
# This is the primary extraction method as of v1.4.6.
# =============================================================================

def get_mosaic_elements_via_descendants(main_window, max_elements=5000):
    """Get all Mosaic elements using descendants() - more reliable than WebView2 recursion.
    
    This is the NEW primary method for Mosaic element extraction.
    Uses pywinauto's descendants() which exhaustively searches all child elements.
    
    Args:
        main_window: The Mosaic main window (pywinauto element)
        max_elements: Maximum elements to retrieve (default 5000)
    
    Returns:
        List of element dicts with: name, text, automation_id, control_type
    """
    elements = []
    
    try:
        count = 0
        for elem in main_window.descendants():
            try:
                automation_id = elem.element_info.automation_id or ""
            except:
                automation_id = ""
            
            try:
                control_type = elem.element_info.control_type or ""
            except:
                control_type = ""
            
            try:
                name = elem.element_info.name or ""
            except:
                name = ""
            
            try:
                text = _window_text_with_timeout(elem, timeout=0.3, element_name="mosaic_descendant") or ""
            except:
                text = ""
            
            # Only include elements with meaningful content
            if automation_id or name or text:
                elements.append({
                    'automation_id': automation_id,
                    'control_type': control_type,
                    'name': name,
                    'text': text[:200] if text else "",  # Limit text length
                })
            
            count += 1
            if count >= max_elements:
                break
    except Exception as e:
        logger.debug(f"get_mosaic_elements_via_descendants error: {e}")
    
    return elements


def _is_mosaic_accession_like(s):
    """Check if string looks like an accession number (for Mosaic extraction).
    
    This is a strict validator to avoid false positives from:
    - MRN values
    - Anatomy terms
    - UI labels
    - Dates
    
    Returns True only for strings that strongly match accession patterns.
    """
    if not s:
        return False
    s = s.strip()
    if len(s) < 6:
        return False
    
    clean_s = s.replace('-', '').replace('_', '').replace(' ', '')
    if len(clean_s) < 6:
        return False
    
    # Reject dates (MM/DD/YYYY or similar)
    if re.match(r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', s):
        return False
    
    # Reject common UI labels/text
    lower_s = s.lower()
    reject_words = ['accession', 'patient', 'study', 'date', 'modality', 'gender', 'male', 'female', 
                   'mrn', 'name', 'type', 'status', 'prior', 'current', 'report', 'unknown',
                   'chrome', 'document', 'pane', 'button', 'text', 'group', 'image',
                   'current study', 'prior study', 'medical record',
                   'study date', 'body part', 'ordering', 'site group', 'site code',
                   'description', 'reason for visit', 'chest', 'abdomen', 'pelvis',
                   'head', 'neck', 'spine', 'extremity', 'knee', 'shoulder', 'hip', 'ankle', 'wrist']
    if lower_s in reject_words:
        return False
    
    # Reject if value starts with these prefixes (labeled field values)
    reject_prefixes = ['description:', 'ordering:', 'site group:', 'site code:', 
                      'body part:', 'reason for visit:', 'study date:']
    for prefix in reject_prefixes:
        if lower_s.startswith(prefix):
            return False
    
    # Reject if it's clearly an MRN (Medical Record Number)
    if 'mrn' in lower_s:
        return False
    
    # Reject common body parts and anatomy terms
    anatomy_terms = ['chest', 'abdomen', 'pelvis', 'head', 'brain', 'spine', 'cervical',
                    'thoracic', 'lumbar', 'extremity', 'shoulder', 'knee', 'hip', 'ankle',
                    'wrist', 'elbow', 'hand', 'foot', 'neck', 'face', 'orbit', 'sinus']
    if lower_s in anatomy_terms:
        return False
    
    # STRONG patterns - definitely accession-like:
    # Pattern: A000478952CVR - letter(s) + digits + optional letters
    if re.match(r'^[A-Za-z]{1,4}\d{6,15}[A-Za-z]{0,5}$', clean_s):
        return True
    
    # Pattern: SSH2512080000263CST - SSH prefix + digits + suffix
    if re.match(r'^SSH\d{10,}[A-Za-z]{0,5}$', clean_s):
        return True
    
    # Pattern: Numbers with embedded letters like facility codes
    if re.match(r'^[A-Za-z0-9]{8,20}$', clean_s):
        # Must have both letters AND numbers
        has_letter = any(c.isalpha() for c in clean_s)
        has_digit = any(c.isdigit() for c in clean_s)
        # Must be mostly digits (accessions are number-heavy)
        digit_ratio = sum(1 for c in clean_s if c.isdigit()) / len(clean_s)
        if has_letter and has_digit and digit_ratio >= 0.5:
            return True
    
    # Pure long numbers (10+ digits) could be accessions
    if clean_s.isdigit() and len(clean_s) >= 10:
        return True
    
    return False


def extract_mosaic_data_v2(main_window):
    """Extract study data from Mosaic using descendants() method.
    
    NEW PRIMARY METHOD (v1.4.6+): Uses main window descendants for reliable extraction.
    
    Extraction strategy:
    1. First pass: Look for "Current Study" label, accession is right below
    2. Second pass: Look for "Accession" label and get next element
    3. Third pass: Look for "Description:" label for procedure
    4. Fourth pass: Look for procedure keywords (CT, MR, XR, etc.)
    
    Args:
        main_window: The Mosaic main window (pywinauto element)
    
    Returns:
        dict with: procedure, accession, patient_class, multiple_accessions, extraction_method
        extraction_method indicates which pass found the data (for debugging)
    
    NOTE: Multi-accession extraction is currently limited in this method.
          Will be improved in future versions.
    """
    data = {
        'procedure': '',
        'accession': '',
        'patient_class': 'Unknown',  # Mosaic doesn't provide patient class
        'multiple_accessions': [],
        'extraction_method': ''  # For debugging which method found data
    }
    
    try:
        # Get all elements using descendants (the working method from testMosaic.py)
        all_elements = get_mosaic_elements_via_descendants(main_window, max_elements=5000)
        
        # Filter to meaningful elements
        element_data = []
        for elem in all_elements:
            name = (elem.get('name', '') or '').strip()
            text = (elem.get('text', '') or '').strip()
            auto_id = (elem.get('automation_id', '') or '').strip()
            
            if name or text or auto_id:
                element_data.append({
                    'name': name,
                    'text': text,
                    'automation_id': auto_id,
                })
        
        logger.debug(f"Mosaic v2: Found {len(element_data)} meaningful elements")
        
        # Helper to extract accession from text
        def extract_accession_from_text(text_str):
            """Extract accession(s) from a text string."""
            if not text_str:
                return []
            results = []
            
            # Pattern 1: "ACC1 (PROC1), ACC2 (PROC2)" format (multi-accession)
            if ',' in text_str and '(' in text_str:
                parts = text_str.split(',')
                for part in parts:
                    part = part.strip()
                    if '(' in part and ')' in part:
                        acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', part)
                        if acc_match:
                            acc = acc_match.group(1).strip()
                            proc = acc_match.group(2).strip()
                            if _is_mosaic_accession_like(acc):
                                results.append({'accession': acc, 'procedure': proc})
                    elif _is_mosaic_accession_like(part):
                        results.append({'accession': part, 'procedure': ''})
            
            # Pattern 2: Single accession with procedure "ACC (PROC)"
            elif '(' in text_str and ')' in text_str:
                acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', text_str)
                if acc_match:
                    acc = acc_match.group(1).strip()
                    proc = acc_match.group(2).strip()
                    if _is_mosaic_accession_like(acc):
                        results.append({'accession': acc, 'procedure': proc})
            
            # Pattern 3: Just an accession-like string
            elif _is_mosaic_accession_like(text_str):
                results.append({'accession': text_str, 'procedure': ''})
            
            return results
        
        # =====================================================================
        # FIRST PASS: Look for "Current Study" label - accession is right below
        # This is the most reliable method for single accessions
        # =====================================================================
        for i, elem in enumerate(element_data):
            name = elem['name']
            text = elem['text']
            combined = f"{name} {text}".strip().lower()
            
            if 'current study' in combined:
                # Look at nearby elements for the accession (should be right below)
                for j in range(i+1, min(i+15, len(element_data))):
                    next_elem = element_data[j]
                    next_name = next_elem['name'].strip()
                    
                    # Skip if it looks like a label or MRN
                    if next_name and not next_name.endswith(':') and 'mrn' not in next_name.lower():
                        extracted = extract_accession_from_text(next_name)
                        if extracted:
                            data['multiple_accessions'].extend(extracted)
                            if not data['accession']:
                                data['accession'] = extracted[0]['accession']
                                data['extraction_method'] = 'Current Study label'
                                if extracted[0]['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                            break
                break  # Only process first "Current Study" found
        
        # =====================================================================
        # SECOND PASS: Look for explicit "Accession" label
        # Fallback if "Current Study" method didn't find accession
        # =====================================================================
        if not data['accession']:
            for i, elem in enumerate(element_data):
                name = elem['name']
                text = elem['text']
                combined = f"{name} {text}".strip()
                
                if 'accession' in combined.lower() and ':' in combined:
                    # Look at nearby elements for the accession value
                    for j in range(i+1, min(i+15, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name'].strip()
                        next_text = next_elem['text'].strip()
                        
                        # Skip MRN values
                        if 'mrn' in next_name.lower() or 'mrn' in next_text.lower():
                            continue
                        
                        # Try to extract from name
                        if next_name:
                            extracted = extract_accession_from_text(next_name)
                            if extracted:
                                data['multiple_accessions'].extend(extracted)
                                data['accession'] = extracted[0]['accession']
                                data['extraction_method'] = 'Accession label'
                                if extracted[0]['procedure'] and not data['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                                break
                        
                        # Try to extract from text
                        if next_text:
                            extracted = extract_accession_from_text(next_text)
                            if extracted:
                                data['multiple_accessions'].extend(extracted)
                                data['accession'] = extracted[0]['accession']
                                data['extraction_method'] = 'Accession label (text)'
                                if extracted[0]['procedure'] and not data['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                                break
                    break  # Only process first "Accession" label found
        
        # =====================================================================
        # THIRD PASS: Look for "Description:" label for procedure
        # =====================================================================
        if not data['procedure']:
            for i, elem in enumerate(element_data):
                name = elem['name']
                
                if 'description:' in name.lower():
                    # Value might be after the colon in the same element
                    if ':' in name:
                        proc_value = name.split(':', 1)[1].strip()
                        if proc_value:
                            data['procedure'] = proc_value
                            break
                    # Or look at next element
                    for j in range(i+1, min(i+3, len(element_data))):
                        next_name = element_data[j]['name'].strip()
                        if next_name and not next_name.endswith(':'):
                            data['procedure'] = next_name
                            break
                    break
        
        # =====================================================================
        # FOURTH PASS: Look for procedure keywords (CT, MR, XR, etc.)
        # Most permissive - used if Description label not found
        # =====================================================================
        if not data['procedure']:
            proc_keywords = ['CT ', 'MR ', 'XR ', 'US ', 'NM ', 'PET', 'MRI', 'ULTRASOUND']
            for elem in element_data:
                name = elem['name']
                # Skip if it looks like an accession format (has comma and parentheses)
                if name and not (',' in name and '(' in name):
                    if any(keyword in name.upper() for keyword in proc_keywords):
                        data['procedure'] = name
                        break
        
    except Exception as e:
        logger.debug(f"extract_mosaic_data_v2 error: {e}")
    
    return data


# =============================================================================
# MOSAIC DATA EXTRACTION - LEGACY METHOD (v1)
# Uses WebView2 element recursion. Kept as fallback.
# TODO: Remove this method once v2 is proven stable (target: v1.5.0)
# =============================================================================

def extract_mosaic_data(webview_element):
    """LEGACY: Extract study data from Mosaic Info Hub WebView2 content.
    
    This is the OLD method using WebView2 recursion.
    Kept as fallback if the new descendants() method fails.
    
    TODO: This can be removed once extract_mosaic_data_v2 is proven stable.
    
    Returns dict with: procedure, accession, patient_class, multiple_accessions
    For Mosaic, patient_class is always "Unknown".
    """
    data = {
        'procedure': '',
        'accession': '',
        'patient_class': 'Unknown',  # Mosaic doesn't provide patient class
        'multiple_accessions': []  # List of {accession, procedure} dicts
    }
    
    try:
        # Get all elements from WebView2 with deep scan
        all_elements = get_mosaic_elements(webview_element, max_depth=20)
        
        # Convert to list for easier searching
        element_data = []
        for elem in all_elements:
            name = elem.get('name', '').strip()
            if name:
                element_data.append({
                    'name': name,
                    'depth': elem.get('depth', 0)
                })
        
        # Find elements by label and get their values
        for i, elem in enumerate(element_data):
            name = elem['name']
            
            # Check if this element itself contains multiple accessions
            # Format: "ACCESSION1 (PROC1), ACCESSION2 (PROC2)"
            if name and ',' in name and '(' in name and 'accession' not in name.lower():
                if not data['multiple_accessions']:
                    accession_parts = name.split(',')
                    for part in accession_parts:
                        part = part.strip()
                        if '(' in part and ')' in part:
                            acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', part)
                            if acc_match:
                                acc = acc_match.group(1).strip()
                                proc = acc_match.group(2).strip()
                                if acc and len(acc) > 5:
                                    data['multiple_accessions'].append({
                                        'accession': acc,
                                        'procedure': proc
                                    })
                    # Set first accession as primary
                    if data['multiple_accessions']:
                        data['accession'] = data['multiple_accessions'][0]['accession']
                        if not data['procedure'] and data['multiple_accessions'][0]['procedure']:
                            data['procedure'] = data['multiple_accessions'][0]['procedure']
            
            # Procedure - look for CT/MR/XR etc. procedures (but skip if it's part of accession format)
            if not data['procedure'] and not (',' in name and '(' in name):
                if name:
                    proc_keywords = ['CT ', 'MR ', 'XR ', 'US ', 'NM ', 'PET', 'MRI', 'ULTRASOUND']
                    if any(keyword in name.upper() for keyword in proc_keywords):
                        data['procedure'] = name
            
            # Accession - look for label "Accession(s):" and get next element(s)
            if 'accession' in name.lower() and ':' in name:
                for j in range(i+1, min(i+10, len(element_data))):
                    next_elem = element_data[j]
                    next_name = next_elem['name'].strip()
                    
                    # Check if it contains multiple accessions
                    if next_name and ',' in next_name and '(' in next_name:
                        accession_parts = next_name.split(',')
                        for part in accession_parts:
                            part = part.strip()
                            if '(' in part and ')' in part:
                                acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', part)
                                if acc_match:
                                    acc = acc_match.group(1).strip()
                                    proc = acc_match.group(2).strip()
                                    if acc:
                                        data['multiple_accessions'].append({
                                            'accession': acc,
                                            'procedure': proc
                                        })
                            else:
                                if part and len(part) > 5:
                                    data['multiple_accessions'].append({
                                        'accession': part,
                                        'procedure': ''
                                    })
                        
                        if data['multiple_accessions']:
                            data['accession'] = data['multiple_accessions'][0]['accession']
                            if not data['procedure'] and data['multiple_accessions'][0]['procedure']:
                                data['procedure'] = data['multiple_accessions'][0]['procedure']
                        break
                    # Single accession
                    elif next_name and len(next_name) > 5 and ' ' not in next_name and '(' not in next_name:
                        data['accession'] = next_name
                        break
        
    except Exception as e:
        logger.debug(f"Error extracting Mosaic data: {e}")
    
    return data


def find_elements_by_automation_id(window, automation_ids: List[str], cached_elements: Dict = None) -> Dict[str, any]:
    """Find elements by Automation ID - optimized for speed.
    
    Uses cached elements when available (instant).
    Falls back to descendants search if direct lookup fails.
    Uses SHORT timeouts (0.3s) to detect study closure quickly.
    """
    found_elements = {}
    ids_needing_search = []
    
    for auto_id in automation_ids:
        # Try cache first (instant)
        if cached_elements and auto_id in cached_elements:
            try:
                cached_elem = cached_elements[auto_id]['element']
                # SHORT timeout (0.3s) - if element is stale, fail fast
                text_content = _window_text_with_timeout(cached_elem, timeout=0.3, element_name=auto_id)
                found_elements[auto_id] = {
                    'element': cached_elem,
                    'text': text_content.strip() if text_content else '',
                }
                continue  # Got it from cache, next element
            except:
                pass  # Cache invalid, need to search
        
        ids_needing_search.append(auto_id)
    
    # If we need to search for any elements, do a single descendants() call
    if ids_needing_search:
        try:
            remaining = set(ids_needing_search)
            # Limit iteration to prevent blocking
            descendants_list = []
            try:
                descendants_gen = window.descendants()
                count = 0
                for elem in descendants_gen:
                    descendants_list.append(elem)
                    count += 1
                    if count >= 1000:  # Limit to prevent excessive blocking
                        break
            except Exception as e:
                logger.debug(f"window.descendants() iteration failed: {e}")
                descendants_list = []
            
            for element in descendants_list:
                if not remaining:
                    break
                try:
                    elem_auto_id = element.element_info.automation_id
                    if elem_auto_id and elem_auto_id in remaining:
                        # SHORT timeout (0.3s) - fail fast on stale elements
                        text_content = _window_text_with_timeout(element, timeout=0.3, element_name=elem_auto_id)
                        found_elements[elem_auto_id] = {
                            'element': element,
                            'text': text_content.strip() if text_content else '',
                        }
                        remaining.remove(elem_auto_id)
                except:
                    pass
        except:
            pass
        
    return found_elements


def match_study_type(procedure_text: str, rvu_table: dict = None, classification_rules: dict = None, direct_lookups: dict = None) -> Tuple[str, float]:
    """Match procedure text to RVU table entry using best match.
    
    Args:
        procedure_text: The procedure text to match
        rvu_table: RVU table dictionary (REQUIRED - must be provided from rvu_settings.json)
        classification_rules: Classification rules dictionary (optional)
        direct_lookups: Direct lookup dictionary (optional)
    
    Returns:
        Tuple of (study_type, rvu_value)
    """
    if not procedure_text:
        return "Unknown", 0.0
    
    # Require rvu_table - it must be provided from loaded settings
    if rvu_table is None:
        logger.error("match_study_type called without rvu_table parameter. RVU table must be loaded from rvu_settings.json")
        return "Unknown", 0.0
    
    if classification_rules is None:
        classification_rules = {}
    if direct_lookups is None:
        direct_lookups = {}
    
    procedure_lower = procedure_text.lower().strip()
    procedure_stripped = procedure_text.strip()
    
    # Check classification rules
    classification_match_name = None
    classification_match_rvu = None
    
    # FIRST: Check user-defined classification rules (highest priority)
    # Rules are grouped by study_type, each group contains a list of rule definitions
    for study_type, rules_list in classification_rules.items():
        if not isinstance(rules_list, list):
            continue
        
        for rule in rules_list:
            required_keywords = rule.get("required_keywords", [])
            excluded_keywords = rule.get("excluded_keywords", [])
            any_of_keywords = rule.get("any_of_keywords", [])
            
            # Special case for "CT Spine": exclude only if ALL excluded keywords are present
            if study_type == "CT Spine" and excluded_keywords:
                all_excluded = all(keyword.lower() in procedure_lower for keyword in excluded_keywords)
                if all_excluded:
                    continue  # Skip this rule if all excluded keywords are present
            # For other rules: exclude if any excluded keyword is present (case-insensitive, lowercase comparison)
            elif excluded_keywords:
                any_excluded = any(keyword.lower() in procedure_lower for keyword in excluded_keywords)
                if any_excluded:
                    continue  # Skip this rule if excluded keyword is present
            
            # Check if all required keywords are present (case-insensitive, lowercase comparison)
            required_match = True
            if required_keywords:
                required_match = all(keyword.lower() in procedure_lower for keyword in required_keywords)
            
            # Check if at least one of any_of_keywords is present (if specified)
            any_of_match = True
            if any_of_keywords:
                any_of_match = any(keyword.lower() in procedure_lower for keyword in any_of_keywords)
            
            # Match if all required keywords are present AND (any_of_keywords match OR no any_of_keywords specified)
            if required_match and any_of_match:
                # Get RVU from rvu_table
                rvu = rvu_table.get(study_type, 0.0)
                classification_match_name = study_type
                classification_match_rvu = rvu
                logger.debug(f"Matched classification rule for '{study_type}': {procedure_text} -> {study_type}")
                break  # Found a classification match, stop searching rules for this study_type
        
        # If we found a classification match, stop searching other study_types
        if classification_match_name:
            break
    
    # If classification rule matched, return it immediately
    if classification_match_name:
        logger.debug(f"Matched classification rule: {procedure_text} -> {classification_match_name} ({classification_match_rvu} RVU)")
        return classification_match_name, classification_match_rvu
    
    # Check for modality keywords and use "Other" types as fallback before partial matching
    # BUT: Don't use fallback if a more specific match exists (e.g., "XR Chest" should match before "XR Other")
    # This is handled by checking partial matches first, so we skip this fallback for now
    # and let it fall through to partial matching which will find "XR Chest" before "XR Other"
    
    # Try exact match first
    for study_type, rvu in rvu_table.items():
        if study_type.lower() == procedure_lower:
            return study_type, rvu
    
    # Try keyword matching FIRST (before partial matching) to correctly identify modality
    # Order matters: longer keywords checked first (e.g., "ultrasound" before "us")
    # Look up RVU values from rvu_table instead of hardcoding
    keyword_study_types = {
        "ct cap": "CT CAP",
        "ct ap": "CT AP",
        "cta": "CTA Brain",  # Default CTA
        "ultrasound": "US Other",  # Check "ultrasound" before "us"
        "mri": "MRI Other",
        "mr ": "MRI Other",
        "us ": "US Other",
        "x-ray": "XR Other",
        "xr ": "XR Other",
        "xr\t": "XR Other",  # XR with tab
        "nuclear": "NM Other",
        "nm ": "NM Other",
        # Note: "pet" intentionally excluded - PET CT must match both "pet" and "ct" together in partial matching
    }
    
    # Check for keywords - prioritize longer/more specific keywords first
    for keyword in sorted(keyword_study_types.keys(), key=len, reverse=True):
        if keyword in procedure_lower:
            study_type = keyword_study_types[keyword]
            rvu = rvu_table.get(study_type, 0.0)
            logger.info(f"Matched keyword '{keyword}' to '{study_type}' for: {procedure_text}")
            return study_type, rvu
    
    # Also check if procedure starts with modality prefix (case-insensitive)
    # Note: "pe" prefix excluded - PET CT must match both "pet" and "ct" together in partial matching
    # IMPORTANT: Check XA before CT (since "xa" starts with "x" which could match "xr")
    if len(procedure_lower) >= 2:
        first_two = procedure_lower[:2]
        # Check for 3-character prefixes first (XA, CTA) before 2-character
        if len(procedure_lower) >= 3:
            first_three = procedure_lower[:3]
            if first_three == "xa " or first_three == "xa\t":
                # XA is fluoroscopy (XR modality)
                return "XR Other", rvu_table.get("XR Other", 0.3)
            elif first_three == "cta":
                # CTA - will be handled by classification rules or keyword matching
                pass
        
        prefix_study_types = {
            "xr": "XR Other",
            "x-": "XR Other",
            "ct": "CT Other",
            "mr": "MRI Other",
            "us": "US Other",
            "nm": "NM Other",
        }
        if first_two in prefix_study_types:
            study_type = prefix_study_types[first_two]
            rvu = rvu_table.get(study_type, 0.0)
            logger.info(f"Matched prefix '{first_two}' to '{study_type}' for: {procedure_text}")
            return study_type, rvu
    
    # Try partial matches (most specific first), but exclude "Other" types initially
    # PET CT is handled separately as it requires both "pet" and "ct" together
    matches = []
    other_matches = []
    pet_ct_match = None
    
    for study_type, rvu in rvu_table.items():
        study_lower = study_type.lower()
        
        # Special handling for PET CT - only match if both "pet" and "ct" appear together
        if study_lower == "pet ct":
            if "pet" in procedure_lower and "ct" in procedure_lower:
                pet_ct_match = (study_type, rvu)
            continue  # Skip adding to matches - will handle separately at the very end
        
        if study_lower in procedure_lower or procedure_lower in study_lower:
            # Score by length (longer = more specific)
            score = len(study_type)
            if " other" in study_lower or study_lower.endswith(" other"):
                # Store "Other" types separately as fallbacks
                other_matches.append((score, study_type, rvu))
            else:
                matches.append((score, study_type, rvu))
    
    # Return most specific non-"Other" match if found
    if matches:
        matches.sort(reverse=True)  # Highest score first
        return matches[0][1], matches[0][2]
    
    # If no specific match, try "Other" types as fallback
    if other_matches:
        other_matches.sort(reverse=True)  # Highest score first
        logger.info(f"Using 'Other' type fallback '{other_matches[0][1]}' for: {procedure_text}")
        return other_matches[0][1], other_matches[0][2]
    
    # Absolute last resort: PET CT (only if both "pet" and "ct" appear together)
    if pet_ct_match:
        logger.info(f"Using PET CT as last resort match (both 'pet' and 'ct' found) for: {procedure_text}")
        return pet_ct_match
    
    return "Unknown", 0.0


def get_app_paths():
    """Get the correct paths for bundled app vs running as script.
    
    Returns:
        tuple: (settings_dir, data_dir)
        - settings_dir: Where bundled settings file is (read-only in bundle)
        - data_dir: Where to store persistent data (records, window positions)
    """
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        # Settings are bundled in _MEIPASS (temp folder)
        settings_dir = sys._MEIPASS
        # Data should be stored next to the .exe for persistence
        data_dir = os.path.dirname(sys.executable)
        logger.info(f"Running as frozen app: settings={settings_dir}, data={data_dir}")
    else:
        # Running as script
        settings_dir = os.path.dirname(__file__)
        data_dir = os.path.dirname(__file__)
        logger.info(f"Running as script: settings={settings_dir}, data={data_dir}")
    return settings_dir, data_dir


# =============================================================================
# Cloud Backup Manager (OneDrive Integration)
# =============================================================================

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
        """Create a backup of the database.
        
        Uses SQLite's online backup API for a consistent copy even during writes.
        
        Args:
            force: If True, bypass schedule check and create backup immediately
            
        Returns:
            Dict with 'success', 'path', 'error' keys
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
                
                # Perform the backup
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
                    try:
                        dest_conn.close()
                    except:
                        pass
                if source_conn:
                    try:
                        source_conn.close()
                    except:
                        pass
            
            # Step 2: Verify backup integrity
            verify_conn = sqlite3.connect(temp_path)
            cursor = verify_conn.execute("PRAGMA integrity_check")
            integrity_result = cursor.fetchone()[0]
            
            # Also get record count for verification
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
            
            # Cleanup temp file if it exists
            if 'temp_path' in locals() and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
        
        finally:
            self.backup_in_progress = False
        
        return result
    
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


class RVUData:
    """Manages data persistence with SQLite for records and JSON for settings."""
    
    def __init__(self, base_dir: str = None):
        settings_dir, data_dir = get_app_paths()
        
        # Settings file (RVU tables, rules, rates, user preferences, window positions)
        self.settings_file = os.path.join(data_dir, "rvu_settings.json")
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
                bundled_settings_file = os.path.join(sys._MEIPASS, "rvu_settings.json")
                if os.path.exists(bundled_settings_file):
                    with open(bundled_settings_file, 'r') as f:
                        default_data = json.load(f)
                        logger.info(f"Loaded bundled settings from {bundled_settings_file}")
            except Exception as e:
                logger.error(f"Error loading bundled settings file: {e}")
        
        # Try local file (when running as script, or if bundled file not found)
        if default_data is None:
            local_settings_file = os.path.join(os.path.dirname(__file__), "rvu_settings.json")
            if os.path.exists(local_settings_file):
                try:
                    with open(local_settings_file, 'r') as f:
                        default_data = json.load(f)
                        logger.info(f"Loaded default settings from local file: {local_settings_file}")
                except Exception as e:
                    logger.error(f"Error loading local settings file: {e}")
        
        # Settings file MUST exist - fail if it doesn't
        if default_data is None:
            error_msg = (
                f"CRITICAL ERROR: Could not load settings file!\n"
                f"Expected bundled file: {os.path.join(sys._MEIPASS if self.is_frozen else os.path.dirname(__file__), 'rvu_settings.json')}\n"
                f"Or local file: {os.path.join(os.path.dirname(__file__), 'rvu_settings.json')}\n"
                f"The settings file must be bundled with the app or present in the script directory."
            )
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        
        # Try to load user's existing settings file
        user_data = None
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r') as f:
                    user_data = json.load(f)
                    logger.info(f"Loaded user settings from {self.settings_file}")
            except Exception as e:
                logger.error(f"Error loading user settings file: {e}")
        
        # If no user settings exist, use defaults and save them
        if user_data is None:
            logger.info("No existing user settings found, using defaults")
            # Save the default settings for future use
            try:
                with open(self.settings_file, 'w') as f:
                    json.dump(default_data, f, indent=2, default=str)
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
                logger.info("No user RVU table found, using defaults from rvu_settings.json")
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
            with open(self.settings_file, 'w') as f:
                json.dump(merged_data, f, indent=2, default=str)
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
                    # Load default rvu_table from JSON if available
                    default_rvu_table = {}
                    try:
                        if os.path.exists(self.settings_file):
                            with open(self.settings_file, 'r') as default_f:
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
                    with open(self.settings_file, 'w') as f:
                        json.dump(settings_data, f, indent=2)
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
            with open(self.settings_file, 'w') as f:
                json.dump(settings_to_save, f, indent=2, default=str)
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


class StudyTracker:
    """Tracks active studies."""
    
    def __init__(self, min_seconds: int = 5):
        self.active_studies: Dict[str, dict] = {}  # accession -> study info
        self.completed_studies: List[dict] = []
        self.seen_accessions: set = set()
        self.min_seconds = min_seconds
    
    def add_study(self, accession: str, procedure: str, timestamp: datetime, rvu_table: dict = None, classification_rules: dict = None, direct_lookups: dict = None, patient_class: str = ""):
        """Add or update an active study."""
        if not accession:
            return
        
        if accession in self.active_studies:
            # Update existing study
            self.active_studies[accession]["last_seen"] = timestamp
            if patient_class:
                self.active_studies[accession]["patient_class"] = patient_class
            # If procedure was empty/Unknown before and now we have a valid procedure, update it
            existing_procedure = self.active_studies[accession].get("procedure", "")
            existing_study_type = self.active_studies[accession].get("study_type", "")
            if (not existing_procedure or existing_procedure.lower() in ["n/a", "na", "no report", ""] or 
                existing_study_type == "Unknown") and procedure and procedure.lower() not in ["n/a", "na", "no report", ""]:
                # Update procedure and re-match study type
                study_type, rvu = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
                self.active_studies[accession]["procedure"] = procedure
                self.active_studies[accession]["study_type"] = study_type
                self.active_studies[accession]["rvu"] = rvu
                logger.info(f"Updated study procedure and type: {accession} - {study_type} ({rvu} RVU) (was: {existing_study_type})")
        else:
            # New study - use direct lookups, classification rules if provided
            study_type, rvu = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
            self.active_studies[accession] = {
                "accession": accession,
                "procedure": procedure,
                "patient_class": patient_class,
                "study_type": study_type,
                "rvu": rvu,
                "start_time": timestamp,
                "last_seen": timestamp,
            }
            logger.info(f"Added study: {accession} - {study_type} ({rvu} RVU) - Patient Class: {patient_class}")
    
    def check_completed(self, current_time: datetime, current_accession: str = "") -> List[dict]:
        """Check for studies that have disappeared (completed)."""
        completed = []
        to_remove = []
        
        logger.info(f"check_completed called: current_accession='{current_accession}', active_studies={list(self.active_studies.keys())}")
        
        for accession, study in list(self.active_studies.items()):
            # If this accession is currently visible, it's not completed
            if accession == current_accession:
                logger.info(f"check_completed: {accession} is currently visible, skipping")
                continue
            
            # If current_accession is empty or different, this study has disappeared
            time_since_last_seen = (current_time - study["last_seen"]).total_seconds()
            
            # Study is considered completed if:
            # 1. A different study is now visible (current_accession is set and different), OR
            # 2. No study is visible (current_accession is empty) - complete immediately
            #    When no study is visible, any active studies must have closed, so complete them immediately
            should_complete = False
            if current_accession:
                # Different study is visible - this one is completed
                should_complete = True
                logger.info(f"check_completed: {accession} should complete - different study '{current_accession}' is visible")
            elif not current_accession:
                # No study is visible - complete immediately (no threshold needed)
                # If nothing is visible, this study has definitely closed
                should_complete = True
                logger.info(f"check_completed: {accession} should complete - no study visible (empty accession)")
            
            if should_complete:
                # Use current_time as end_time when accession is empty (study just closed)
                # Use last_seen when a different study is visible (was replaced)
                end_time = current_time if not current_accession else study["last_seen"]
                duration = (end_time - study["start_time"]).total_seconds()
                logger.info(f"check_completed: {accession} disappeared, time_since_last_seen={time_since_last_seen:.1f}s, duration={duration:.1f}s, min_seconds={self.min_seconds}")
                
                # Only count if duration >= min_seconds
                if duration >= self.min_seconds:
                    completed_study = study.copy()
                    completed_study["end_time"] = end_time
                    completed_study["duration"] = duration
                    completed.append(completed_study)
                    logger.info(f"Completed study: {accession} - {study['study_type']} ({duration:.1f}s)")
                else:
                    logger.info(f"Ignored short study: {accession} ({duration:.1f}s < {self.min_seconds}s)")
                
                to_remove.append(accession)
        
        # Remove completed studies from active tracking
        for accession in to_remove:
            if accession in self.active_studies:
                del self.active_studies[accession]
        
        logger.info(f"check_completed returning {len(completed)} completed studies: {[s['accession'] for s in completed]}")
        return completed
    
    def should_ignore(self, accession: str, ignore_duplicates: bool, data_manager=None) -> bool:
        """Check if study should be ignored for TRACKING purposes.
        
        IMPORTANT: This now only blocks studies that were part of multi-accession groups.
        Single studies are ALWAYS tracked (even if already recorded) to allow duration updates.
        The _record_or_update_study function handles updating duration if higher.
        
        Checks both memory (seen_accessions) and database (current shift records) for duplicates.
        Also checks if accession was part of a previously recorded multi-accession study.
        """
        if not accession:
            return True
        
        # Don't ignore if it's currently active
        if accession in self.active_studies:
            return False
        
        # Only check for duplicates if ignore_duplicates is True
        if not ignore_duplicates:
            return False
        
        # CHANGED: We no longer block tracking for single studies that were already recorded
        # This allows us to track duration and update it if the user spends more time
        # The _record_or_update_study function will update duration if higher
        
        # ONLY block if this accession was part of a multi-accession study
        # Multi-accession studies should not be re-tracked individually
        if data_manager:
            if self._was_part_of_multi_accession(accession, data_manager):
                logger.debug(f"Ignoring accession {accession} - it was already recorded as part of a multi-accession study")
                return True
        
        return False
    
    def is_already_recorded(self, accession: str, data_manager=None) -> bool:
        """Check if study has already been recorded (for display purposes only).
        
        This is separate from should_ignore - a study can be already recorded but still
        tracked for duration updates.
        """
        if not accession:
            return False
        
        # Check in-memory cache first (faster)
        if accession in self.seen_accessions:
            return True
        
        # Check database for duplicates in current shift
        if data_manager:
            try:
                if hasattr(data_manager, 'db') and data_manager.db:
                    current_shift = data_manager.db.get_current_shift()
                    if current_shift:
                        db_record = data_manager.db.find_record_by_accession(
                            current_shift['id'], accession
                        )
                        if db_record:
                            # Add to memory cache so we don't need to query DB again
                            self.seen_accessions.add(accession)
                            return True
            except Exception as e:
                logger.debug(f"Error checking database for duplicate: {e}")
            
            # Also check if this accession was part of a multi-accession study
            if self._was_part_of_multi_accession(accession, data_manager):
                return True
        
        return False
    
    def _was_part_of_multi_accession(self, accession: str, data_manager) -> bool:
        """Check if an accession was already recorded as part of a multi-accession study.
        
        Handles both old format (is_multi_accession with individual_accessions) and 
        new format (from_multi_accession on individual records).
        """
        try:
            # Check current shift records
            current_shift_records = data_manager.data.get("current_shift", {}).get("records", [])
            for record in current_shift_records:
                # New format: from_multi_accession flag on individual records
                if record.get("from_multi_accession", False):
                    if record.get("accession") == accession:
                        return True
                
                # Old format: is_multi_accession with individual_accessions array
                if record.get("is_multi_accession", False):
                    individual_accessions = record.get("individual_accessions", [])
                    if accession in individual_accessions:
                        return True
                    accession_str = record.get("accession", "")
                    if accession_str:
                        accession_list = [acc.strip() for acc in accession_str.split(",")]
                        if accession in accession_list:
                            return True
            
            # Check historical shifts
            shifts = data_manager.data.get("shifts", [])
            for shift in shifts:
                shift_records = shift.get("records", [])
                for record in shift_records:
                    # New format
                    if record.get("from_multi_accession", False):
                        if record.get("accession") == accession:
                            return True
                    
                    # Old format
                    if record.get("is_multi_accession", False):
                        individual_accessions = record.get("individual_accessions", [])
                        if accession in individual_accessions:
                            return True
                        accession_str = record.get("accession", "")
                        if accession_str and accession in accession_str.split(", "):
                            return True
        except Exception as e:
            logger.debug(f"Error checking if accession was part of multi-accession: {e}")
        
        return False
    
    def mark_seen(self, accession: str):
        """Mark accession as seen."""
        if accession:
            self.seen_accessions.add(accession)


class RVUCounterApp:
    """Main application class."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("RVU Counter")
        self.root.geometry("240x500")  # Default size
        self.root.minsize(200, 350)  # Minimum size
        self.root.resizable(True, True)
        
        # Window dragging state
        self.drag_start_x = 0
        self.drag_start_y = 0
        
        # Data management
        self.data_manager = RVUData()
        
        # Set stay on top based on settings (default True if not set)
        stay_on_top = self.data_manager.data["settings"].get("stay_on_top", True)
        self.root.attributes("-topmost", stay_on_top)
        
        # Load saved window position and size or use default (after data_manager is initialized)
        window_pos = self.data_manager.data.get("window_positions", {}).get("main", None)
        if window_pos:
            width = window_pos.get('width', 240)
            height = window_pos.get('height', 500)
            x = window_pos['x']
            y = window_pos['y']
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        else:
            # First run: center on primary monitor
            try:
                primary = get_primary_monitor_bounds()
                primary_width = primary[2] - primary[0]
                primary_height = primary[3] - primary[1]
                x = primary[0] + (primary_width - 240) // 2
                y = primary[1] + (primary_height - 500) // 2
                self.root.geometry(f"240x500+{x}+{y}")
                logger.info(f"First run: positioning window at ({x}, {y}) on primary monitor")
            except Exception as e:
                logger.error(f"Error positioning window on first run: {e}")
        
        # Schedule post-mapping validation to ensure window is visible
        self.root.after(100, self._ensure_window_visible)
        
        # Initialize last saved position tracking
        self._last_saved_main_x = self.root.winfo_x()
        self._last_saved_main_y = self.root.winfo_y()
        self.tracker = StudyTracker(
            min_seconds=self.data_manager.data["settings"]["min_study_seconds"]
        )
        
        # State
        self.shift_start: Optional[datetime] = None
        self.effective_shift_start: Optional[datetime] = None
        self.projected_shift_end: Optional[datetime] = None
        self.is_running = False
        self.current_window = None
        self.refresh_interval = 300  # 300ms for faster completion detection
        
        # Typical shift times (calculated from historical data, defaults to 11pm-8am)
        self.typical_shift_start_hour = 23  # 11pm default
        self.typical_shift_end_hour = 8     # 8am default
        self._calculate_typical_shift_times()  # Update from historical data
        
        # Adaptive polling variables for PowerScribe worker thread
        import time
        self._last_accession_seen = ""
        self._last_data_change_time = time.time()  # Initialize to current time
        self._current_poll_interval = 1.0  # Start with moderate polling
        
        # Current detected data (must be initialized before create_ui)
        self.current_accession = ""
        self.current_procedure = ""
        self.current_patient_class = ""
        self.current_study_type = ""
        self.current_study_rvu = 0.0
        self.current_multiple_accessions = []  # List of accession numbers when multiple
        
        # Multi-accession tracking
        self.multi_accession_data = {}  # accession -> {procedure, study_type, rvu, patient_class}
        self.multi_accession_mode = False  # True when tracking a multi-accession study
        self.multi_accession_start_time = None  # When we started tracking this multi-accession study
        self.multi_accession_last_procedure = ""  # Last procedure seen in multi-accession mode
        
        # Cache for performance
        self.cached_window = None
        self.cached_elements = {}  # automation_id -> element reference
        self.last_record_count = 0  # Track when to rebuild widgets
        self.no_report_skip_count = 0  # Skip expensive searches when no report is open
        
        # Background thread for PowerScribe operations
        self._ps_lock = threading.Lock()
        self._ps_data = {}  # Data from PowerScribe (updated by background thread)
        self._last_clario_accession = ""  # Track last accession we queried Clario for
        self._clario_patient_class_cache = {}  # Cache Clario patient class by accession
        self._pending_studies = {}  # Track accession -> procedure for studies detected but not yet added
        
        # Auto-switch data source detection
        self._active_source = None  # "PowerScribe" or "Mosaic" - currently active source
        self._primary_source = "PowerScribe"  # Which source to check first
        self._last_secondary_check = 0  # Timestamp of last secondary source check
        self._secondary_check_interval = 5.0  # How often to check secondary when primary is idle (seconds)
        
        self._ps_thread_running = True
        self._ps_thread = threading.Thread(target=self._powerscribe_worker, daemon=True)
        self._ps_thread.start()
        
        # Create UI
        self.create_ui()
        
        # Initialize time labels list for time display updates
        self.time_labels = []
        
        # Start timer to update time display every 5 seconds if show_time is enabled
        self._update_time_display()
        
        # Auto-resume shift if enabled and shift was running (no shift_end means it was interrupted)
        if self.data_manager.data["settings"].get("auto_start", False):
            current_shift = self.data_manager.data["current_shift"]
            shift_start = current_shift.get("shift_start")
            shift_end = current_shift.get("shift_end")
            
            # Only resume if there's a shift_start but NO shift_end (app crashed while running)
            if shift_start and not shift_end:
                try:
                    self.shift_start = datetime.fromisoformat(shift_start)
                    # Restore effective shift start and projected end if available
                    effective_start = current_shift.get("effective_shift_start")
                    projected_end = current_shift.get("projected_shift_end")
                    if effective_start:
                        self.effective_shift_start = datetime.fromisoformat(effective_start)
                    else:
                        # Fall back to calculating it
                        minutes_into_hour = self.shift_start.minute
                        if minutes_into_hour <= 15:
                            self.effective_shift_start = self.shift_start.replace(minute=0, second=0, microsecond=0)
                        else:
                            self.effective_shift_start = self.shift_start
                    if projected_end:
                        self.projected_shift_end = datetime.fromisoformat(projected_end)
                    else:
                        # Fall back to calculating it
                        shift_length = self.data_manager.data["settings"].get("shift_length_hours", 9)
                        self.projected_shift_end = self.effective_shift_start + timedelta(hours=shift_length)
                    
                    self.is_running = True
                    # Update button and UI to reflect running state
                    self.start_btn.config(text="Stop Shift")
                    self.root.title("RVU Counter - Running")
                    self.update_shift_start_label()
                    self.update_recent_studies_label()
                    # Update display to show correct counters
                    self.update_display()
                    logger.info(f"Auto-resumed shift from {self.shift_start} (app was interrupted)")
                except Exception as e:
                    logger.error(f"Error parsing shift_start for auto-resume: {e}")
            # If shift_end exists, the shift was properly stopped - don't auto-resume
            elif shift_start and shift_end:
                logger.info("Auto-resume skipped: shift was properly stopped")
        else:
            # Auto-resume is disabled, but check if we're in a running state anyway
            # This handles cases where the state might be inconsistent
            current_shift = self.data_manager.data["current_shift"]
            shift_start = current_shift.get("shift_start")
            shift_end = current_shift.get("shift_end")
            if shift_start and not shift_end:
                # There's an active shift but auto-resume is disabled
                # Still update the label to show the correct state
                try:
                    self.shift_start = datetime.fromisoformat(shift_start)
                    self.is_running = True
                    self.start_btn.config(text="Stop Shift")
                    self.root.title("RVU Counter - Running")
                    self.update_shift_start_label()
                    self.update_recent_studies_label()
                except Exception as e:
                    logger.error(f"Error updating UI for active shift: {e}")
        
        # Always ensure label is updated based on current state (fallback)
        self.update_recent_studies_label()
        
        self.setup_refresh()
        self.setup_time_sensitive_update()  # Start time-sensitive counter updates (5s interval)
        
        # Check if we should prompt for cloud backup setup
        self._check_first_time_backup_prompt()
        
        logger.info("RVU Counter application started")
    
    def create_ui(self):
        """Create the user interface."""
        # Create style
        self.style = ttk.Style()
        self.style.configure("Red.TLabelframe.Label", foreground="red")
        
        # Apply theme based on settings
        self.apply_theme()
        
        # Main frame - minimal top/bottom padding, normal sides
        main_frame = ttk.Frame(self.root, padding=(5, 2, 5, 5))  # left, top, right, bottom
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title bar is draggable (bind to main frame)
        main_frame.bind("<Button-1>", self.start_drag)
        main_frame.bind("<B1-Motion>", self.on_drag)
        main_frame.bind("<ButtonRelease-1>", self.on_drag_end)
        
        # Top section using grid for precise vertical control
        top_section = ttk.Frame(main_frame)
        top_section.pack(fill=tk.X)
        top_section.columnconfigure(0, weight=0)
        top_section.columnconfigure(1, weight=1)
        
        # Row 0: Button and shift start time
        self.start_btn = ttk.Button(top_section, text="Start Shift", command=self.start_shift, width=12)
        self.start_btn.grid(row=0, column=0, sticky=tk.W, pady=(0, 0))
        
        self.shift_start_label = ttk.Label(top_section, text="", font=("Arial", 8), foreground="gray")
        self.shift_start_label.grid(row=0, column=1, sticky=tk.W, padx=(8, 0), pady=(0, 0))
        
        # Row 1: Data source indicator (left) and version (right)
        self.data_source_indicator = ttk.Label(top_section, text="detecting...", 
                                               font=("Arial", 7), foreground="gray", cursor="hand2")
        self.data_source_indicator.grid(row=1, column=0, sticky=tk.W, padx=(2, 0), pady=(0, 0))
        self.data_source_indicator.bind("<Button-1>", lambda e: self._toggle_data_source())
        
        # Version info on the right with backup status
        version_frame = ttk.Frame(top_section)
        version_frame.grid(row=1, column=1, sticky=tk.E, padx=(0, 2), pady=(0, 0))
        
        # Backup status indicator (clickable to open settings)
        self.backup_status_label = tk.Label(version_frame, text="", font=("Arial", 7), 
                                            fg="gray", cursor="hand2",
                                            bg=self.root.cget('bg'))
        self.backup_status_label.pack(side=tk.LEFT, padx=(0, 5))
        self.backup_status_label.bind("<Button-1>", lambda e: self.open_settings())
        
        # Update backup status display
        self._update_backup_status_display()
        
        version_text = f"v{VERSION} ({VERSION_DATE})"
        self.version_label = ttk.Label(version_frame, text=version_text, font=("Arial", 7), foreground="gray")
        self.version_label.pack(side=tk.LEFT)
        
        # Counters frame - use tk.LabelFrame with explicit border control for tighter spacing
        self.counters_frame = tk.LabelFrame(main_frame, bd=1, relief=tk.GROOVE, padx=2, pady=2)
        self.counters_frame.pack(fill=tk.X, pady=(0, 3))
        counters_frame = self.counters_frame  # Keep local reference for code below
        
        # Inner frame to center the content
        counters_inner = ttk.Frame(counters_frame)
        counters_inner.pack(expand=True)  # Centers horizontally
        
        # Counter labels with aligned columns (inside centered inner frame)
        row = 0
        
        # Total
        self.total_label_text = ttk.Label(counters_inner, text="total wRVU:", font=("Arial", 9), anchor=tk.E)
        self.total_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        total_value_frame = ttk.Frame(counters_inner)
        total_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.total_label = ttk.Label(total_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.total_label.pack(side=tk.LEFT)
        self.total_comp_label = tk.Label(total_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.total_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.total_value_frame = total_value_frame
        row += 1
        
        # Average per hour
        self.avg_label_text = ttk.Label(counters_inner, text="avg/hour:", font=("Arial", 9), anchor=tk.E)
        self.avg_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        avg_value_frame = ttk.Frame(counters_inner)
        avg_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.avg_label = ttk.Label(avg_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.avg_label.pack(side=tk.LEFT)
        self.avg_comp_label = tk.Label(avg_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.avg_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.avg_value_frame = avg_value_frame
        row += 1
        
        # Last hour
        self.last_hour_label_text = ttk.Label(counters_inner, text="last hour:", font=("Arial", 9), anchor=tk.E)
        self.last_hour_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        last_hour_value_frame = ttk.Frame(counters_inner)
        last_hour_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.last_hour_label = ttk.Label(last_hour_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.last_hour_label.pack(side=tk.LEFT)
        self.last_hour_comp_label = tk.Label(last_hour_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.last_hour_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.last_hour_value_frame = last_hour_value_frame
        row += 1
        
        # Last full hour - format: "8pm-9pm hour: x.x"
        # Use a frame to hold both the time range (smaller font) and "hour:" text
        self.last_full_hour_label_frame = ttk.Frame(counters_inner)
        self.last_full_hour_label_frame.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        self.last_full_hour_range_label = ttk.Label(self.last_full_hour_label_frame, text="", font=("Arial", 8), anchor=tk.E)
        self.last_full_hour_range_label.pack(side=tk.LEFT)
        self.last_full_hour_label_text = ttk.Label(self.last_full_hour_label_frame, text="hour:", font=("Arial", 9), anchor=tk.E)
        self.last_full_hour_label_text.pack(side=tk.LEFT, padx=(2, 0))
        last_full_hour_value_frame = ttk.Frame(counters_inner)
        last_full_hour_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.last_full_hour_label = ttk.Label(last_full_hour_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.last_full_hour_label.pack(side=tk.LEFT)
        self.last_full_hour_comp_label = tk.Label(last_full_hour_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.last_full_hour_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.last_full_hour_value_frame = last_full_hour_value_frame
        row += 1
        
        # Projected This Hour
        self.projected_label_text = ttk.Label(counters_inner, text="est this hour:", font=("Arial", 9), anchor=tk.E)
        self.projected_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        projected_value_frame = ttk.Frame(counters_inner)
        projected_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.projected_label = ttk.Label(projected_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.projected_label.pack(side=tk.LEFT)
        self.projected_comp_label = tk.Label(projected_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.projected_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.projected_value_frame = projected_value_frame
        row += 1
        
        # Projected Shift Total
        self.projected_shift_label_text = ttk.Label(counters_inner, text="est shift total:", font=("Arial", 9), anchor=tk.E)
        self.projected_shift_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        projected_shift_value_frame = ttk.Frame(counters_inner)
        projected_shift_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.projected_shift_label = ttk.Label(projected_shift_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.projected_shift_label.pack(side=tk.LEFT)
        self.projected_shift_comp_label = tk.Label(projected_shift_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.projected_shift_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.projected_shift_value_frame = projected_shift_value_frame
        
        # Pace Car bar - comparison vs prior shift (initially hidden)
        self.pace_car_frame = ttk.Frame(main_frame)
        # Don't pack yet - will be shown/hidden based on settings
        
        # Pace car comparison state: 'prior', 'goal', 'best_week', 'best_ever', or 'week_N'
        # Load from settings (persists between sessions)
        self.pace_comparison_mode = self.data_manager.data["settings"].get("pace_comparison_mode", "prior")
        self.pace_comparison_shift = None  # Cache of the shift data being compared (not persisted)
        
        # Container for both bars (stacked) - clickable to change comparison
        self.pace_bars_container = tk.Frame(self.pace_car_frame, bg="#e0e0e0", height=20)
        self.pace_bars_container.pack(fill=tk.X, padx=2, pady=1)
        self.pace_bars_container.pack_propagate(False)
        
        # Current bar (top) - background track
        self.pace_bar_current_track = tk.Frame(self.pace_bars_container, bg="#e8e8e8", height=9)
        self.pace_bar_current_track.place(x=0, y=1, relwidth=1.0)
        
        # Current bar fill (grows with current RVU)
        self.pace_bar_current = tk.Frame(self.pace_bar_current_track, bg="#87CEEB", height=9)  # Sky blue
        self.pace_bar_current.place(x=0, y=0, width=0)
        
        # Prior bar (bottom) - full width background
        self.pace_bar_prior_track = tk.Frame(self.pace_bars_container, bg="#B8B8DC", height=9)  # Darker lavender
        self.pace_bar_prior_track.place(x=0, y=11, relwidth=1.0)
        
        # Prior bar marker (where prior was at this time)
        self.pace_bar_prior_marker = tk.Frame(self.pace_bars_container, bg="#000000", width=2, height=9)
        
        # Bind click to all bar widgets to open comparison selector
        for widget in [self.pace_bars_container, self.pace_bar_current_track, 
                       self.pace_bar_current, self.pace_bar_prior_track, self.pace_bar_prior_marker]:
            widget.bind("<Button-1>", self._open_pace_comparison_selector)
        
        # Labels showing the comparison (using place for precise positioning)
        self.pace_label_frame = tk.Frame(self.pace_car_frame, bg=self.root.cget('bg'), height=12)
        self.pace_label_frame.pack(fill=tk.X, padx=2)
        self.pace_label_frame.pack_propagate(False)
        
        # Left side: Build string with colored numbers using place() for tight spacing
        # We'll position labels precisely to eliminate gaps
        self.pace_label_now_text = tk.Label(self.pace_label_frame, text="Now:", font=("Arial", 7), bg=self.root.cget('bg'), fg="gray", padx=0, pady=0, bd=0)
        self.pace_label_now_text.place(x=0, y=0)
        
        self.pace_label_now_value = tk.Label(self.pace_label_frame, text="", font=("Arial", 7, "bold"), bg=self.root.cget('bg'), padx=0, pady=0, bd=0)
        # Will be positioned after measuring text width
        
        self.pace_label_separator = tk.Label(self.pace_label_frame, text=" | ", font=("Arial", 7), bg=self.root.cget('bg'), fg="gray", padx=0, pady=0, bd=0)
        
        self.pace_label_prior_text = tk.Label(self.pace_label_frame, text="Prior:", font=("Arial", 7), bg=self.root.cget('bg'), fg="gray", padx=0, pady=0, bd=0)
        
        self.pace_label_prior_value = tk.Label(self.pace_label_frame, text="", font=("Arial", 7, "bold"), bg=self.root.cget('bg'), fg="#9090C0", padx=0, pady=0, bd=0)
        
        self.pace_label_time = tk.Label(self.pace_label_frame, text="", font=("Arial", 7), bg=self.root.cget('bg'), fg="gray", padx=0, pady=0, bd=0)
        
        # Right side: status
        self.pace_label_right = tk.Label(self.pace_label_frame, text="", font=("Arial", 7), bg=self.root.cget('bg'), bd=0)
        self.pace_label_right.pack(side=tk.RIGHT, padx=0, pady=0)
        
        # Show pace car if enabled in settings AND there's an active shift
        has_active_shift = self.data_manager.data["current_shift"].get("shift_start") is not None
        if self.data_manager.data["settings"].get("show_pace_car", False) and has_active_shift:
            self.pace_car_frame.pack(fill=tk.X, pady=(0, 2))
        
        # Buttons frame - centered
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(pady=5)
        
        self.stats_btn = ttk.Button(buttons_frame, text="Statistics", command=self.open_statistics, width=8)
        self.stats_btn.pack(side=tk.LEFT, padx=3)
        
        self.undo_btn = ttk.Button(buttons_frame, text="Undo", command=self.undo_last, width=6, state=tk.DISABLED)
        self.undo_btn.pack(side=tk.LEFT, padx=3)
        
        # Track if undo has been used
        self.undo_used = False
        
        self.settings_btn = ttk.Button(buttons_frame, text="Settings", command=self.open_settings, width=8)
        self.settings_btn.pack(side=tk.LEFT, padx=3)
        
        # Current Study frame - pack first so it reserves space at bottom
        debug_frame = tk.LabelFrame(main_frame, text="Current Study", bd=1, relief=tk.GROOVE, padx=3, pady=3)
        debug_frame.pack(fill=tk.X, pady=(5, 0), side=tk.BOTTOM)
        
        # Accession row with duration on the right
        accession_frame = ttk.Frame(debug_frame)
        accession_frame.pack(fill=tk.X)
        
        self.debug_accession_label = ttk.Label(accession_frame, text="Accession: -", font=("Consolas", 8), foreground="gray")
        self.debug_accession_label.pack(side=tk.LEFT, anchor=tk.W)
        
        self.debug_duration_label = ttk.Label(accession_frame, text="", font=("Consolas", 8), foreground="gray")
        self.debug_duration_label.pack(side=tk.RIGHT, anchor=tk.E)
        
        self.debug_patient_class_label = ttk.Label(debug_frame, text="Patient Class: -", font=("Consolas", 8), foreground="gray")
        self.debug_patient_class_label.pack(anchor=tk.W)
        
        self.debug_procedure_label = ttk.Label(debug_frame, text="Procedure: -", font=("Consolas", 8), foreground="gray")
        self.debug_procedure_label.pack(anchor=tk.W)
        
        # Study Type with RVU frame (to align RVU to the right) - separate labels like Recent Studies
        study_type_frame = ttk.Frame(debug_frame)
        study_type_frame.pack(fill=tk.X)
        
        self.debug_study_type_prefix_label = ttk.Label(study_type_frame, text="Study Type: ", font=("Consolas", 8), foreground="gray")
        self.debug_study_type_prefix_label.pack(side=tk.LEFT, anchor=tk.W)
        
        self.debug_study_type_label = ttk.Label(study_type_frame, text="-", font=("Consolas", 8), foreground="gray")
        self.debug_study_type_label.pack(side=tk.LEFT, anchor=tk.W, padx=(0, 0))
        
        # Spacer to push RVU to the right
        spacer = ttk.Frame(study_type_frame)
        spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.debug_study_rvu_label = ttk.Label(study_type_frame, text="", font=("Consolas", 8), foreground="gray")
        self.debug_study_rvu_label.pack(side=tk.LEFT, anchor=tk.W, padx=(0, 0))  # Pack on LEFT right after spacer, no padding
        
        # Store debug_frame reference for resizing
        self.debug_frame = debug_frame
        
        # Recent studies frame - pack after Current Study so it fills remaining space above
        self.recent_frame = tk.LabelFrame(main_frame, text="Recent Studies", bd=1, relief=tk.GROOVE, padx=3, pady=5)
        self.recent_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Canvas with scrollbar for recent studies
        canvas_frame = ttk.Frame(self.recent_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        canvas_bg = self.theme_colors.get("canvas_bg", "#f0f0f0")
        canvas = tk.Canvas(canvas_frame, highlightthickness=0, bd=0, bg=canvas_bg)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        # Use a custom style for the scrollable frame to match canvas_bg
        self.style.configure("StudiesScrollable.TFrame", background=canvas_bg)
        self.studies_scrollable_frame = ttk.Frame(canvas, style="StudiesScrollable.TFrame")
        
        canvas_window = canvas.create_window((0, 0), window=self.studies_scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=0, pady=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Store study widgets for deletion
        self.study_widgets = []
        
        def configure_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Also update canvas height if needed
            canvas.update_idletasks()
        
        def configure_canvas_width(event):
            # Make the canvas window match the canvas width
            canvas.itemconfig(canvas_window, width=event.width)
            # Update scroll region when canvas is configured
            canvas.after_idle(lambda: canvas.configure(scrollregion=canvas.bbox("all")))
        
        self.studies_scrollable_frame.bind("<Configure>", configure_scroll_region)
        canvas.bind("<Configure>", configure_canvas_width)
        # Also bind to parent frame to ensure proper sizing
        self.recent_frame.bind("<Configure>", lambda e: canvas.update_idletasks())
        
        # Store canvas reference for scrolling
        self.studies_canvas = canvas
        
        # Store study widgets for deletion (initialized in create_ui)
        self.study_widgets = []
        
        # Bind resize event to recalculate truncation
        self.root.bind("<Configure>", self._on_window_resize)
        self._last_width = 240  # Track width for resize detection
        
        # Set initial title
        if self.is_running:
            self.root.title("RVU Counter - Running")
        else:
            self.root.title("RVU Counter - Stopped")
        
        # Update display
        self.update_display()
        
        # Set initial undo button state
        if not self.data_manager.data["current_shift"]["records"]:
            self.undo_btn.config(state=tk.DISABLED)
            self.undo_used = True
        
        # Apply theme colors to tk widgets (must be done AFTER widgets are created)
        self._update_tk_widget_colors()
    
    def setup_refresh(self):
        """Setup periodic refresh."""
        # Always refresh to update debug display, but only track if running
        self.refresh_data()
        self.root.after(self.refresh_interval, self.setup_refresh)
    
    def setup_time_sensitive_update(self):
        """Setup periodic update for time-sensitive counters (runs every 5 seconds)."""
        self.update_time_sensitive_stats()
        self.root.after(5000, self.setup_time_sensitive_update)  # 5 seconds
    
    def update_time_sensitive_stats(self):
        """Lightweight update for time-based metrics only (avg/hour, projections).
        
        This runs on a slower timer (5s) and only recalculates values that change
        with time, avoiding expensive full recalculation of all stats.
        """
        if not self.shift_start:
            return
        
        try:
            records = self.data_manager.data["current_shift"]["records"]
            current_time = datetime.now()
            settings = self.data_manager.data["settings"]
            
            # Calculate values that change with time
            total_rvu = sum(r["rvu"] for r in records)
            total_comp = sum(self._calculate_study_compensation(r) for r in records)
            
            # Average per hour (changes as time passes even with no new studies)
            hours_elapsed = (current_time - self.shift_start).total_seconds() / 3600
            avg_per_hour = total_rvu / hours_elapsed if hours_elapsed > 0 else 0.0
            avg_comp_per_hour = total_comp / hours_elapsed if hours_elapsed > 0 else 0.0
            
            # Update avg labels if visible
            if settings.get("show_avg", True):
                self.avg_label.config(text=f"{avg_per_hour:.1f}")
                if settings.get("show_comp_avg", False):
                    self.avg_comp_label.config(text=f"(${avg_comp_per_hour:,.0f})")
            
            # Projected for current hour (changes as time passes)
            current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
            current_hour_records = [r for r in records if datetime.fromisoformat(r["time_finished"]) >= current_hour_start]
            current_hour_rvu = sum(r["rvu"] for r in current_hour_records)
            current_hour_comp = sum(self._calculate_study_compensation(r) for r in current_hour_records)
            
            minutes_into_hour = (current_time - current_hour_start).total_seconds() / 60
            if minutes_into_hour > 0:
                projected = (current_hour_rvu / minutes_into_hour) * 60
                projected_comp = (current_hour_comp / minutes_into_hour) * 60
            else:
                projected = 0.0
                projected_comp = 0.0
            
            # Update projected labels if visible
            if settings.get("show_projected", True):
                self.projected_label.config(text=f"{projected:.1f}")
                if settings.get("show_comp_projected", False):
                    self.projected_comp_label.config(text=f"(${projected_comp:,.0f})")
            
            # Projected shift total (changes as time passes)
            projected_shift_rvu = total_rvu
            projected_shift_comp = total_comp
            
            if self.effective_shift_start and self.projected_shift_end:
                time_remaining = (self.projected_shift_end - current_time).total_seconds()
                
                if time_remaining > 0 and hours_elapsed > 0:
                    rvu_rate_per_hour = avg_per_hour
                    hours_remaining = time_remaining / 3600
                    
                    projected_additional_rvu = rvu_rate_per_hour * hours_remaining
                    projected_shift_rvu = total_rvu + projected_additional_rvu
                    
                    projected_additional_comp = self._calculate_projected_compensation(
                        current_time, 
                        self.projected_shift_end, 
                        rvu_rate_per_hour
                    )
                    projected_shift_comp = total_comp + projected_additional_comp
            
            # Update projected shift labels if visible
            if settings.get("show_projected_shift", True):
                self.projected_shift_label.config(text=f"{projected_shift_rvu:.1f}")
                if settings.get("show_comp_projected_shift", False):
                    self.projected_shift_comp_label.config(text=f"(${projected_shift_comp:,.0f})")
            
            # Update pace car if visible
            if settings.get("show_pace_car", False):
                self.update_pace_car(total_rvu)
        
        except Exception as e:
            logger.debug(f"Error updating time-sensitive stats: {e}")
    
    def update_pace_car(self, current_rvu: float):
        """Update the pace car comparison bar.
        
        Compares current shift RVU vs prior shift RVU at the same elapsed time
        since typical shift start (dynamically calculated from historical data).
        
        Design: Two stacked bars
        - Top bar (current): fills proportionally based on current RVU vs prior total
        - Bottom bar (prior): full width = prior total, with marker at "prior at this time"
        """
        try:
            if not hasattr(self, 'pace_bars_container'):
                return
            
            current_time = datetime.now()
            
            # ALWAYS use reference time (e.g., 11pm) for pace comparison
            # This normalizes all shifts to the same starting point for fair comparison
            reference_start = self._get_reference_shift_start(current_time)
            
            # Elapsed time since reference start (11pm) in minutes
            elapsed_minutes = (current_time - reference_start).total_seconds() / 60
            if elapsed_minutes < 0:
                elapsed_minutes = 0
            
            logger.info(f"[PACE] Current time: {current_time.strftime('%H:%M:%S')}, "
                       f"Reference (11pm): {reference_start.strftime('%H:%M:%S')}, Elapsed: {elapsed_minutes:.1f}min")
            
            # Get prior shift data (returns tuple: rvu_at_elapsed, total_rvu)
            prior_data = self._get_prior_shift_rvu_at_elapsed_time(elapsed_minutes)
            
            if prior_data is None:
                # No prior shift data available
                self.pace_label_now_text.config(text="No prior shift data")
                self.pace_label_now_value.config(text="")
                self.pace_label_separator.config(text="")
                self.pace_label_prior_value.config(text="")
                self.pace_label_time.config(text="")
                self.pace_label_right.config(text="")
                self.pace_bar_current.place_forget()
                self.pace_bar_prior_marker.place_forget()
                return
            
            prior_rvu_at_elapsed, prior_total_rvu = prior_data
            
            # Calculate the difference
            diff = current_rvu - prior_rvu_at_elapsed
            
            # Get container width
            self.pace_bars_container.update_idletasks()
            container_width = self.pace_bars_container.winfo_width()
            if container_width < 10:
                container_width = 200  # Default fallback
            
            # Dynamic scale: use whichever is larger (current or prior total) as 100%
            # This way if you're exceeding prior total, both bars scale down appropriately
            max_scale = max(current_rvu, prior_total_rvu, 1)  # minimum 1 to avoid division by zero
            
            # Calculate widths relative to max_scale
            current_width = int((current_rvu / max_scale) * container_width)
            prior_total_width = int((prior_total_rvu / max_scale) * container_width)
            prior_marker_pos = int((prior_rvu_at_elapsed / max_scale) * container_width)
            
            # Update bar colors based on ahead/behind
            dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
            
            if diff >= 0:
                current_bar_color = "#5DADE2"  # Slightly darker light blue for bar (ahead)
                if dark_mode:
                    current_text_color = "#87CEEB"  # Bright sky blue for dark mode (matches pace bar)
                    status_color = "#87CEEB"  # Bright sky blue for status text
                else:
                    current_text_color = "#2874A6"  # Darker blue for light mode
                    status_color = "#2874A6"  # Darker blue for status text
                status_text = f"▲ +{diff:.1f} ahead"
            else:
                current_bar_color = "#c62828"  # Red for bar (behind)
                if dark_mode:
                    current_text_color = "#ef5350"  # Brighter red for dark mode
                    status_color = "#ef5350"  # Brighter red for status text
                else:
                    current_text_color = "#B71C1C"  # Darker red for light mode
                    status_color = "#B71C1C"  # Darker red for status text
                status_text = f"▼ {diff:.1f} behind"
            
            # Update current bar (top) - fills from left
            self.pace_bar_current.config(bg=current_bar_color)
            self.pace_bar_current.place(x=0, y=0, width=current_width, height=9)
            
            # Update prior bar (bottom) - width scales with prior total relative to max
            # Must set relwidth=0 to clear the initial relwidth=1.0 setting
            self.pace_bar_prior_track.place(x=0, y=11, width=prior_total_width, height=9, relwidth=0)
            
            # Update prior marker (black line on lavender bar showing "prior at this time")
            self.pace_bar_prior_marker.place(x=prior_marker_pos, y=11, width=2, height=9)
            
            # Format time display - show elapsed time for goal mode, otherwise current time
            if self.pace_comparison_mode == 'goal':
                # For goal mode, show elapsed time (e.g., "at 2h 15m")
                elapsed_hours = int(elapsed_minutes // 60)
                elapsed_mins = int(elapsed_minutes % 60)
                if elapsed_hours > 0:
                    time_str = f"{elapsed_hours}h {elapsed_mins}m"
                else:
                    time_str = f"{elapsed_mins}m"
            else:
                # For actual shifts, show current time
                time_str = current_time.strftime("%I:%M %p").lstrip("0").lower()
            
            # Update labels with color-coded RVU values and position precisely
            # Position labels tightly side-by-side by calculating cumulative x positions
            x_pos = 0
            
            # "Now:" - gray
            self.pace_label_now_text.place(x=x_pos, y=0)
            x_pos += self.pace_label_now_text.winfo_reqwidth()
            
            # "XX.X" - colored based on ahead/behind
            self.pace_label_now_value.config(text=f" {current_rvu:.1f}", fg=current_text_color)
            self.pace_label_now_value.place(x=x_pos, y=0)
            x_pos += self.pace_label_now_value.winfo_reqwidth()
            
            # " | " - gray
            self.pace_label_separator.place(x=x_pos, y=0)
            x_pos += self.pace_label_separator.winfo_reqwidth()
            
            # Comparison label - shows what we're comparing to
            compare_label = "Prior:"
            if self.pace_comparison_mode == 'goal':
                compare_label = "Goal:"  # Theoretical pace
            elif self.pace_comparison_mode == 'best_week':
                compare_label = "Week:"  # Week's best
            elif self.pace_comparison_mode == 'best_ever':
                compare_label = "Best:"  # All time best
            elif self.pace_comparison_mode == 'custom':
                compare_label = "Custom:"  # User-selected shift
            elif self.pace_comparison_mode and self.pace_comparison_mode.startswith('week_'):
                # Show 3-letter day abbreviation for specific week shift
                if self.pace_comparison_shift:
                    compare_label = self._format_shift_day_abbrev(self.pace_comparison_shift) + ":"
            
            self.pace_label_prior_text.config(text=compare_label)
            self.pace_label_prior_text.place(x=x_pos, y=0)
            x_pos += self.pace_label_prior_text.winfo_reqwidth()
            
            # "XX.X" - darker lavender
            self.pace_label_prior_value.config(text=f" {prior_rvu_at_elapsed:.1f}", fg="#7070A0")
            self.pace_label_prior_value.place(x=x_pos, y=0)
            x_pos += self.pace_label_prior_value.winfo_reqwidth()
            
            # " at time" - gray
            self.pace_label_time.config(text=f" at {time_str}")
            self.pace_label_time.place(x=x_pos, y=0)
            
            # Status on right
            self.pace_label_right.config(text=status_text, fg=status_color)
            
        except Exception as e:
            logger.debug(f"Error updating pace car: {e}")
    
    def _open_pace_comparison_selector(self, event=None):
        """Open a popup to select which shift to compare against."""
        logger.info("Pace comparison selector clicked!")
        try:
            # Prevent opening multiple popups
            if hasattr(self, '_pace_popup') and self._pace_popup:
                try:
                    if self._pace_popup.winfo_exists():
                        self._pace_popup.destroy()
                except:
                    pass
                self._pace_popup = None
            
            # Create popup window
            popup = tk.Toplevel(self.root)
            self._pace_popup = popup  # Store reference
            popup.title("Compare To...")
            popup.transient(self.root)
            
            # Position near the pace bar
            x = self.pace_bars_container.winfo_rootx()
            y = self.pace_bars_container.winfo_rooty() + self.pace_bars_container.winfo_height() + 5
            popup.geometry(f"220x350+{x}+{y}")
            
            # Apply theme
            dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
            bg_color = "#2d2d2d" if dark_mode else "white"
            fg_color = "#ffffff" if dark_mode else "black"
            border_color = "#555555" if dark_mode else "#cccccc"
            popup.configure(bg=border_color)  # Border effect
            
            frame = tk.Frame(popup, bg=bg_color, padx=8, pady=5)
            frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)  # 1px border
            
            # Get shifts for this week and all time
            shifts_this_week, prior_shift, best_week, best_ever = self._get_pace_comparison_options()
            
            def make_selection(mode, shift=None):
                self.pace_comparison_mode = mode
                self.pace_comparison_shift = shift
                # Save mode to settings (persists between sessions)
                self.data_manager.data["settings"]["pace_comparison_mode"] = mode
                self.data_manager.save()
                popup.destroy()
                self._pace_popup = None
            
            def close_popup(e=None):
                popup.destroy()
                self._pace_popup = None
            
            # Helper to create hover effect
            def add_hover(widget, bg_color, dark_mode):
                widget.bind("<Enter>", lambda e: e.widget.config(bg="#e0e0e0" if not dark_mode else "#404040"))
                widget.bind("<Leave>", lambda e: e.widget.config(bg=bg_color))
            
            # --- TOP SECTION: Prior, Week Best, All Time Best ---
            
            # Prior Shift (most recent valid shift)
            if prior_shift:
                prior_rvu = sum(r.get('rvu', 0) for r in prior_shift.get('records', []))
                prior_date = self._format_shift_label(prior_shift)
                btn = tk.Label(frame, text=f"  Prior: {prior_date} ({prior_rvu:.1f} RVU)", 
                              font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
                btn.pack(fill=tk.X, pady=1)
                btn.bind("<Button-1>", lambda e: make_selection('prior', prior_shift))
                add_hover(btn, bg_color, dark_mode)
            
            # Week Best (best this week)
            if best_week:
                best_week_rvu = sum(r.get('rvu', 0) for r in best_week.get('records', []))
                best_week_date = self._format_shift_label(best_week)
                btn = tk.Label(frame, text=f"  Week: {best_week_date} ({best_week_rvu:.1f} RVU)", 
                              font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
                btn.pack(fill=tk.X, pady=1)
                btn.bind("<Button-1>", lambda e: make_selection('best_week', best_week))
                add_hover(btn, bg_color, dark_mode)
            
            # All Time Best
            if best_ever:
                best_ever_rvu = sum(r.get('rvu', 0) for r in best_ever.get('records', []))
                best_ever_date = self._format_shift_label(best_ever)
                btn = tk.Label(frame, text=f"  Best: {best_ever_date} ({best_ever_rvu:.1f} RVU)", 
                              font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
                btn.pack(fill=tk.X, pady=1)
                btn.bind("<Button-1>", lambda e: make_selection('best_ever', best_ever))
                add_hover(btn, bg_color, dark_mode)
            
            # Custom shift selector
            custom_btn = tk.Label(frame, text=f"  Custom: Select any shift...", 
                                 font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
            custom_btn.pack(fill=tk.X, pady=1)
            custom_btn.bind("<Button-1>", lambda e: self._open_custom_shift_selector(popup, make_selection, close_popup))
            add_hover(custom_btn, bg_color, dark_mode)
            
            # --- THIS WEEK SECTION ---
            if shifts_this_week:
                tk.Label(frame, text="This Week:", font=("Arial", 8, "bold"),
                        bg=bg_color, fg=fg_color, anchor=tk.W).pack(fill=tk.X, pady=(8, 2))
                
                for i, shift in enumerate(shifts_this_week):
                    shift_date = self._format_shift_label(shift)  # Already has 3-letter day abbrev
                    total_rvu = sum(r.get('rvu', 0) for r in shift.get('records', []))
                    btn = tk.Label(frame, text=f"  {shift_date} ({total_rvu:.1f} RVU)", 
                                  font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
                    btn.pack(fill=tk.X, pady=1)
                    btn.bind("<Button-1>", lambda e, s=shift, idx=i: make_selection(f'week_{idx}', s))
                    add_hover(btn, bg_color, dark_mode)
            
            # If no shifts found at all, show a message
            if not prior_shift and not shifts_this_week and not best_week and not best_ever:
                tk.Label(frame, text="No historical shifts found", 
                        font=("Arial", 8), bg=bg_color, fg="gray", anchor=tk.W).pack(fill=tk.X, pady=5)
            
            # Separator before Goal
            tk.Frame(frame, bg=border_color, height=1).pack(fill=tk.X, pady=5)
            
            # --- GOAL SECTION: Theoretical pace with editable parameters ---
            goal_frame = tk.Frame(frame, bg=bg_color)
            goal_frame.pack(fill=tk.X, pady=(0, 5))
            
            # Get current goal settings
            goal_rvu_h = self.data_manager.data["settings"].get("pace_goal_rvu_per_hour", 15.0)
            goal_hours = self.data_manager.data["settings"].get("pace_goal_shift_hours", 9.0)
            goal_total = self.data_manager.data["settings"].get("pace_goal_total_rvu", 135.0)
            
            # Goal label (clickable)
            goal_btn = tk.Label(goal_frame, text=f"  Goal: {goal_rvu_h:.1f}/h × {goal_hours:.0f}h = {goal_total:.0f} RVU", 
                              font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
            goal_btn.pack(fill=tk.X, pady=1)
            goal_btn.bind("<Button-1>", lambda e: make_selection('goal', None))
            add_hover(goal_btn, bg_color, dark_mode)
            
            # Mini editor frame (expandable)
            goal_editor = tk.Frame(frame, bg=bg_color)
            goal_editor.pack(fill=tk.X, padx=(10, 0))
            
            # Variables for goal settings
            rvu_h_var = tk.StringVar(value=f"{goal_rvu_h:.1f}")
            hours_var = tk.StringVar(value=f"{goal_hours:.1f}")
            total_var = tk.StringVar(value=f"{goal_total:.1f}")
            
            def update_total(*args):
                """Recalculate total when RVU/h or hours changes."""
                try:
                    rvu_h = float(rvu_h_var.get())
                    hours = float(hours_var.get())
                    new_total = rvu_h * hours
                    total_var.set(f"{new_total:.1f}")
                    # Save settings
                    self.data_manager.data["settings"]["pace_goal_rvu_per_hour"] = rvu_h
                    self.data_manager.data["settings"]["pace_goal_shift_hours"] = hours
                    self.data_manager.data["settings"]["pace_goal_total_rvu"] = new_total
                    self.data_manager.save()
                    # Update goal label
                    goal_btn.config(text=f"  Goal: {rvu_h:.1f}/h × {hours:.0f}h = {new_total:.0f} RVU")
                except ValueError:
                    pass
            
            def update_rvu_h_from_total(*args):
                """Recalculate RVU/h when total changes directly."""
                try:
                    total = float(total_var.get())
                    hours = float(hours_var.get())
                    if hours > 0:
                        new_rvu_h = total / hours
                        rvu_h_var.set(f"{new_rvu_h:.1f}")
                        # Save settings
                        self.data_manager.data["settings"]["pace_goal_rvu_per_hour"] = new_rvu_h
                        self.data_manager.data["settings"]["pace_goal_total_rvu"] = total
                        self.data_manager.save()
                        # Update goal label
                        goal_btn.config(text=f"  Goal: {new_rvu_h:.1f}/h × {hours:.0f}h = {total:.0f} RVU")
                except ValueError:
                    pass
            
            # RVU/h row
            rvu_h_frame = tk.Frame(goal_editor, bg=bg_color)
            rvu_h_frame.pack(fill=tk.X, pady=1)
            tk.Label(rvu_h_frame, text="RVU/h:", font=("Arial", 7), bg=bg_color, fg="gray", width=6, anchor=tk.E).pack(side=tk.LEFT)
            rvu_h_entry = tk.Entry(rvu_h_frame, textvariable=rvu_h_var, font=("Arial", 7), width=6)
            rvu_h_entry.pack(side=tk.LEFT, padx=2)
            rvu_h_entry.bind("<FocusOut>", update_total)
            rvu_h_entry.bind("<Return>", update_total)
            
            # Hours row
            hours_frame = tk.Frame(goal_editor, bg=bg_color)
            hours_frame.pack(fill=tk.X, pady=1)
            tk.Label(hours_frame, text="Hours:", font=("Arial", 7), bg=bg_color, fg="gray", width=6, anchor=tk.E).pack(side=tk.LEFT)
            hours_entry = tk.Entry(hours_frame, textvariable=hours_var, font=("Arial", 7), width=6)
            hours_entry.pack(side=tk.LEFT, padx=2)
            hours_entry.bind("<FocusOut>", update_total)
            hours_entry.bind("<Return>", update_total)
            
            # Total row
            total_frame = tk.Frame(goal_editor, bg=bg_color)
            total_frame.pack(fill=tk.X, pady=1)
            tk.Label(total_frame, text="Total:", font=("Arial", 7), bg=bg_color, fg="gray", width=6, anchor=tk.E).pack(side=tk.LEFT)
            total_entry = tk.Entry(total_frame, textvariable=total_var, font=("Arial", 7), width=6)
            total_entry.pack(side=tk.LEFT, padx=2)
            total_entry.bind("<FocusOut>", update_rvu_h_from_total)
            total_entry.bind("<Return>", update_rvu_h_from_total)
            
            # Cancel button
            cancel_btn = tk.Label(frame, text="Cancel", font=("Arial", 8), 
                                 bg=bg_color, fg="gray", anchor=tk.CENTER)
            cancel_btn.pack(fill=tk.X, pady=(8, 2))
            cancel_btn.bind("<Button-1>", close_popup)
            cancel_btn.bind("<Enter>", lambda e: e.widget.config(fg=fg_color))
            cancel_btn.bind("<Leave>", lambda e: e.widget.config(fg="gray"))
            
            # Close on Escape key
            popup.bind("<Escape>", close_popup)
            
            # Make sure popup is visible
            popup.lift()
            popup.focus_force()
            
            logger.info(f"Pace popup opened with {len(shifts_this_week)} week shifts, prior={prior_shift is not None}")
            
        except Exception as e:
            logger.error(f"Error opening pace comparison selector: {e}", exc_info=True)
    
    def _open_custom_shift_selector(self, parent_popup, make_selection_callback, close_parent_callback):
        """Open a modal to browse and select any historical shift."""
        try:
            # Create second modal
            custom_popup = tk.Toplevel(self.root)
            custom_popup.title("Select Custom Shift")
            custom_popup.transient(parent_popup)
            
            # Calculate position
            modal_width = 280
            modal_height = 400
            
            # Try to load saved position first
            saved_pos = self.data_manager.data.get("window_positions", {}).get("custom_shift_selector", None)
            
            if saved_pos:
                x = saved_pos.get('x', 0)
                y = saved_pos.get('y', 0)
                logger.debug(f"Loading saved custom shift selector position: ({x}, {y})")
            else:
                # Default: position next to parent popup
                x = parent_popup.winfo_rootx() + parent_popup.winfo_width() + 10
                y = parent_popup.winfo_rooty()
            
            # Get screen dimensions
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            
            # Ensure modal stays on screen
            # If it would go off the right edge, position it to the left of parent instead
            if x + modal_width > screen_width:
                x = parent_popup.winfo_rootx() - modal_width - 10
                # If that's also off-screen (left edge), center it
                if x < 0:
                    x = (screen_width - modal_width) // 2
            
            # Ensure it doesn't go off the bottom
            if y + modal_height > screen_height:
                y = screen_height - modal_height - 40  # Leave room for taskbar
                if y < 0:
                    y = 20  # Minimum top padding
            
            # Ensure it doesn't go off the top
            if y < 0:
                y = 20
            
            custom_popup.geometry(f"{modal_width}x{modal_height}+{x}+{y}")
            
            # Track position for saving
            custom_popup._last_saved_x = x
            custom_popup._last_saved_y = y
            
            # Position saving functions
            def on_configure(event):
                """Track window movement and save position with debouncing."""
                if event.widget == custom_popup:
                    try:
                        current_x = custom_popup.winfo_x()
                        current_y = custom_popup.winfo_y()
                        # Debounce: save after 200ms of no movement
                        if hasattr(custom_popup, '_save_timer'):
                            custom_popup.after_cancel(custom_popup._save_timer)
                        custom_popup._save_timer = custom_popup.after(200, 
                            lambda: save_custom_selector_position(current_x, current_y))
                    except Exception as e:
                        logger.debug(f"Error tracking custom selector position: {e}")
            
            def save_custom_selector_position(x=None, y=None):
                """Save custom shift selector position."""
                try:
                    if x is None:
                        x = custom_popup.winfo_x()
                    if y is None:
                        y = custom_popup.winfo_y()
                    
                    # Only save if position actually changed
                    if hasattr(custom_popup, '_last_saved_x') and hasattr(custom_popup, '_last_saved_y'):
                        if x == custom_popup._last_saved_x and y == custom_popup._last_saved_y:
                            return
                    
                    if "window_positions" not in self.data_manager.data:
                        self.data_manager.data["window_positions"] = {}
                    self.data_manager.data["window_positions"]["custom_shift_selector"] = {
                        "x": x,
                        "y": y
                    }
                    custom_popup._last_saved_x = x
                    custom_popup._last_saved_y = y
                    self.data_manager.save(save_records=False)
                    logger.debug(f"Saved custom shift selector position: ({x}, {y})")
                except Exception as e:
                    logger.error(f"Error saving custom selector position: {e}")
            
            # Bind position tracking
            custom_popup.bind("<Configure>", on_configure)
            
            # Apply theme
            dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
            bg_color = "#2d2d2d" if dark_mode else "white"
            fg_color = "#ffffff" if dark_mode else "black"
            border_color = "#555555" if dark_mode else "#cccccc"
            custom_popup.configure(bg=border_color)
            
            frame = tk.Frame(custom_popup, bg=bg_color, padx=8, pady=5)
            frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
            
            # Title
            tk.Label(frame, text="Select any prior shift:", font=("Arial", 9, "bold"),
                    bg=bg_color, fg=fg_color, anchor=tk.W).pack(fill=tk.X, pady=(0, 5))
            
            # Scrollable list frame
            list_frame = tk.Frame(frame, bg=bg_color)
            list_frame.pack(fill=tk.BOTH, expand=True)
            
            # Canvas for scrolling
            canvas = tk.Canvas(list_frame, bg=bg_color, highlightthickness=0)
            scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
            scrollable_frame = tk.Frame(canvas, bg=bg_color)
            
            scrollable_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            
            canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            
            # Get all historical shifts
            all_shifts = self.data_manager.data.get("shifts", [])
            
            if not all_shifts:
                tk.Label(scrollable_frame, text="No historical shifts found", 
                        font=("Arial", 8), bg=bg_color, fg="gray", anchor=tk.W).pack(fill=tk.X, pady=10)
            else:
                # Sort by date (newest first)
                sorted_shifts = sorted(all_shifts, 
                                      key=lambda s: s.get('shift_start', ''), 
                                      reverse=True)
                
                # Helper for hover effect
                def add_hover(widget):
                    widget.bind("<Enter>", lambda e: e.widget.config(bg="#e0e0e0" if not dark_mode else "#404040"))
                    widget.bind("<Leave>", lambda e: e.widget.config(bg=bg_color))
                
                # Add each shift as a clickable entry
                for shift in sorted_shifts:
                    if not shift.get("records"):
                        continue  # Skip shifts with no records
                    
                    # Calculate shift info
                    try:
                        shift_start = datetime.fromisoformat(shift.get('shift_start', ''))
                        date_str = shift_start.strftime('%a %b %d, %Y')  # "Mon Dec 07, 2025"
                        time_str = shift_start.strftime('%I:%M %p').lstrip('0')  # "11:01 PM"
                    except:
                        date_str = "Unknown date"
                        time_str = ""
                    
                    total_rvu = sum(r.get('rvu', 0) for r in shift.get('records', []))
                    record_count = len(shift.get('records', []))
                    
                    # Create shift button
                    shift_text = f"{date_str} {time_str}\n  ({record_count}, {total_rvu:.1f} RVU)"
                    
                    def make_custom_selection(s=shift):
                        """Close both modals and set custom comparison."""
                        custom_popup.destroy()
                        make_selection_callback('custom', s)
                    
                    btn = tk.Label(scrollable_frame, text=shift_text,
                                  font=("Arial", 8), bg=bg_color, fg=fg_color, 
                                  anchor=tk.W, justify=tk.LEFT, padx=5, pady=3,
                                  relief=tk.FLAT, borderwidth=1)
                    btn.pack(fill=tk.X, pady=1)
                    btn.bind("<Button-1>", lambda e, s=shift: make_custom_selection(s))
                    add_hover(btn)
            
            # Close button
            close_btn = tk.Label(frame, text="Cancel", font=("Arial", 8),
                                bg=bg_color, fg="gray", anchor=tk.CENTER)
            close_btn.pack(fill=tk.X, pady=(8, 0))
            close_btn.bind("<Button-1>", lambda e: custom_popup.destroy())
            
            # Enable mouse wheel scrolling
            def _on_mousewheel(event):
                canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            
            # Cleanup on close
            def on_closing():
                # Cancel any pending save timer
                if hasattr(custom_popup, '_save_timer'):
                    try:
                        custom_popup.after_cancel(custom_popup._save_timer)
                    except:
                        pass
                # Save final position
                save_custom_selector_position()
                # Cleanup bindings
                canvas.unbind_all("<MouseWheel>")
                custom_popup.destroy()
            
            custom_popup.protocol("WM_DELETE_WINDOW", on_closing)
            
        except Exception as e:
            logger.error(f"Error opening custom shift selector: {e}", exc_info=True)
    
    def _get_pace_comparison_options(self):
        """Get shifts available for pace comparison.
        
        For prior/week shifts: Only includes shifts within typical shift window.
        For best ever: Includes any ~9 hour shift regardless of start time.
        Returns: (shifts_this_week, prior_shift, best_week_shift, best_ever_shift)
        """
        historical_shifts = self.data_manager.data.get("shifts", [])
        if not historical_shifts:
            return [], None, None, None
        
        now = datetime.now()
        
        # Find start of current week (Monday at typical shift start hour)
        days_since_monday = now.weekday()  # Monday = 0
        week_start = now - timedelta(days=days_since_monday)
        week_start = week_start.replace(hour=self.typical_shift_start_hour, minute=0, second=0, microsecond=0)
        # If we haven't reached Monday shift start yet, use last week's Monday
        if now < week_start:
            week_start -= timedelta(days=7)
        
        shifts_this_week = []
        prior_shift = None
        best_week = None
        best_ever = None
        best_week_rvu = 0
        best_ever_rvu = 0
        
        for shift in historical_shifts:
            if not shift.get("shift_start") or not shift.get("records"):
                continue
            
            try:
                shift_start = datetime.fromisoformat(shift["shift_start"])
                total_rvu = sum(r.get('rvu', 0) for r in shift.get('records', []))
                
                # Calculate shift duration for best_ever eligibility
                shift_end_str = shift.get("shift_end")
                shift_hours = None
                if shift_end_str:
                    shift_end = datetime.fromisoformat(shift_end_str)
                    shift_hours = (shift_end - shift_start).total_seconds() / 3600
                
                # Best ever: any shift that's approximately 9 hours (7-11 hours)
                if shift_hours and 7 <= shift_hours <= 11:
                    if total_rvu > best_ever_rvu:
                        best_ever_rvu = total_rvu
                        best_ever = shift
                
                # For prior/week: only include shifts within typical shift window
                hour = shift_start.hour
                if not self._is_valid_shift_hour(hour):
                    continue  # Skip shifts outside typical window for prior/week
                
                # Prior shift is the first valid one (most recent night shift)
                if prior_shift is None:
                    prior_shift = shift
                
                # Check if in this week
                if shift_start >= week_start:
                    shifts_this_week.append(shift)
                    if total_rvu > best_week_rvu:
                        best_week_rvu = total_rvu
                        best_week = shift
            except:
                pass
        
        # Sort this week's shifts by date (oldest first for display)
        shifts_this_week.sort(key=lambda s: s.get("shift_start", ""))
        
        return shifts_this_week, prior_shift, best_week, best_ever
    
    def _format_shift_label(self, shift):
        """Format shift as 'Mon 12/2' style."""
        try:
            shift_start = datetime.fromisoformat(shift["shift_start"])
            return shift_start.strftime("%a %m/%d")
        except:
            return "Unknown"
    
    def _format_shift_day_label(self, shift):
        """Format shift as day of week name."""
        try:
            shift_start = datetime.fromisoformat(shift["shift_start"])
            return shift_start.strftime("%A")  # Full day name
        except:
            return "Unknown"
    
    def _format_shift_day_abbrev(self, shift):
        """Format shift as 3-letter day abbreviation (Mon, Tue, Wed, etc.)."""
        try:
            shift_start = datetime.fromisoformat(shift["shift_start"])
            return shift_start.strftime("%a")  # 3-letter day abbreviation
        except:
            return "???"
    
    def _calculate_typical_shift_times(self):
        """Calculate typical shift start and end hours from historical data.
        
        Analyzes completed shifts to find the most common (mode) start and end hours.
        Uses fuzzy matching by rounding to nearest hour.
        Falls back to 11pm-8am if insufficient data.
        """
        historical_shifts = self.data_manager.data.get("shifts", [])
        
        if len(historical_shifts) < 2:
            # Not enough data, keep defaults
            logger.info(f"Using default shift times: {self.typical_shift_start_hour}:00 - {self.typical_shift_end_hour}:00")
            return
        
        start_hours = []
        end_hours = []
        
        for shift in historical_shifts:
            try:
                if not shift.get("shift_start") or not shift.get("shift_end"):
                    continue
                
                start = datetime.fromisoformat(shift["shift_start"])
                end = datetime.fromisoformat(shift["shift_end"])
                
                # Round to nearest hour (fuzzy matching)
                # e.g., 10:45pm -> 11pm, 11:15pm -> 11pm, 8:20am -> 8am
                start_hour = start.hour
                if start.minute >= 30:
                    start_hour = (start_hour + 1) % 24
                
                end_hour = end.hour
                if end.minute >= 30:
                    end_hour = (end_hour + 1) % 24
                
                start_hours.append(start_hour)
                end_hours.append(end_hour)
            except:
                pass
        
        if start_hours and end_hours:
            # Find mode (most common hour) for start and end
            from collections import Counter
            
            start_counter = Counter(start_hours)
            end_counter = Counter(end_hours)
            
            # Get most common
            self.typical_shift_start_hour = start_counter.most_common(1)[0][0]
            self.typical_shift_end_hour = end_counter.most_common(1)[0][0]
            
            logger.info(f"Calculated typical shift times from {len(start_hours)} shifts: "
                       f"{self.typical_shift_start_hour}:00 - {self.typical_shift_end_hour}:00")
        else:
            logger.info(f"Using default shift times: {self.typical_shift_start_hour}:00 - {self.typical_shift_end_hour}:00")
    
    def _is_valid_shift_hour(self, hour: int) -> bool:
        """Check if an hour falls within the typical shift window (with 1-hour fuzzy margin).
        
        Handles overnight shifts where start > end (e.g., 23 to 8).
        """
        start = self.typical_shift_start_hour
        end = self.typical_shift_end_hour
        
        # Add 1-hour margin for fuzzy matching
        # e.g., if typical is 23-8, accept 22-9
        start_fuzzy = (start - 1) % 24
        end_fuzzy = (end + 1) % 24
        
        if start_fuzzy > end_fuzzy:
            # Overnight shift (e.g., 22 to 9)
            return hour >= start_fuzzy or hour <= end_fuzzy
        else:
            # Same-day shift (e.g., 6 to 14)
            return start_fuzzy <= hour <= end_fuzzy
    
    def _get_reference_shift_start(self, current_time: datetime) -> datetime:
        """Get the reference shift start time (typical start hour) for elapsed time calculations.
        
        Returns the most recent occurrence of the typical shift start hour.
        """
        start_hour = self.typical_shift_start_hour
        
        # Create today's reference start time
        reference = current_time.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        
        if current_time.hour < start_hour:
            # We're past midnight, so shift start was yesterday
            reference = reference - timedelta(days=1)
        
        return reference
    
    def _get_prior_shift_rvu_at_elapsed_time(self, elapsed_minutes: float):
        """Get RVU from comparison source at the same elapsed time.
        
        Returns tuple (rvu_at_elapsed, total_rvu) or None if no data available.
        Uses self.pace_comparison_mode to determine comparison source:
        - 'goal': Theoretical pace based on settings (RVU/h × hours)
        - 'prior', 'best_week', 'best_ever', 'week_N': Actual historical shifts
        """
        try:
            logger.info(f"[PACE] _get_prior_shift_rvu_at_elapsed_time: mode={self.pace_comparison_mode}, elapsed={elapsed_minutes:.1f}min, cached_shift={self.pace_comparison_shift is not None}")
            # Handle 'goal' mode - theoretical pace
            if self.pace_comparison_mode == 'goal':
                goal_rvu_h = self.data_manager.data["settings"].get("pace_goal_rvu_per_hour", 15.0)
                goal_hours = self.data_manager.data["settings"].get("pace_goal_shift_hours", 9.0)
                goal_total = goal_rvu_h * goal_hours
                
                # Calculate RVU at current elapsed time
                elapsed_hours = elapsed_minutes / 60.0
                rvu_at_elapsed = goal_rvu_h * elapsed_hours
                
                # Cap at total (in case elapsed exceeds goal hours)
                rvu_at_elapsed = min(rvu_at_elapsed, goal_total)
                
                return (rvu_at_elapsed, goal_total)
            
            # Determine which shift to use for comparison
            comparison_shift = None
            
            if self.pace_comparison_shift and self.pace_comparison_shift.get("records"):
                # Use cached comparison shift (set when user selects from popup) if it has records
                comparison_shift = self.pace_comparison_shift
                logger.info(f"[PACE] Using cached comparison shift: mode={self.pace_comparison_mode}, records={len(comparison_shift.get('records', []))}")
            
            if not comparison_shift:
                # No cached shift or cached shift has no records - find prior shift (most recent valid one)
                historical_shifts = self.data_manager.data.get("shifts", [])
                if not historical_shifts:
                    logger.warning("No historical shifts available for comparison")
                    return None
                
                for shift in historical_shifts:
                    if shift.get("shift_start"):
                        # Check if it's a valid shift hour
                        try:
                            shift_start = datetime.fromisoformat(shift["shift_start"])
                            if self._is_valid_shift_hour(shift_start.hour):
                                # Verify shift has records
                                records = shift.get("records", [])
                                if records:
                                    comparison_shift = shift
                                    logger.debug(f"Found comparison shift: start={shift_start.isoformat()}, records={len(records)}")
                                    break
                                else:
                                    logger.debug(f"Skipping shift with no records: start={shift_start.isoformat()}")
                        except Exception as e:
                            logger.debug(f"Error processing shift: {e}")
                            pass
            
            if not comparison_shift:
                return None
            
            # Get comparison shift's actual start time
            prior_start = datetime.fromisoformat(comparison_shift["shift_start"])
            
            # Use reference time approach: normalize to typical shift start hour (e.g., 11pm)
            # This ensures fair comparison even if shifts started at slightly different times
            start_hour = self.typical_shift_start_hour
            
            # Create reference time for prior shift (e.g., 11pm on the day of shift)
            prior_reference = prior_start.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            
            # Handle case where shift started after midnight (e.g., 1am) but reference is 11pm
            if prior_start.hour < start_hour:
                # Shift started after midnight, so reference 11pm is on previous day
                prior_reference = prior_reference - timedelta(days=1)
            
            # Calculate target time: reference (11pm) + elapsed minutes
            target_time = prior_reference + timedelta(minutes=elapsed_minutes)
            
            logger.info(f"[PACE] Prior shift: actual_start={prior_start.strftime('%Y-%m-%d %H:%M:%S')}, "
                       f"reference_11pm={prior_reference.strftime('%Y-%m-%d %H:%M:%S')}, "
                       f"target_time={target_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Sum RVU for all records finished before target_time
            rvu_at_elapsed = 0.0
            total_rvu = 0.0
            records = comparison_shift.get("records", [])
            
            if not records:
                logger.warning(f"Comparison shift has no records. Shift start: {comparison_shift.get('shift_start')}")
                return None
            
            records_before_target = 0
            records_after_target = 0
            
            for record in records:
                rvu = record.get("rvu", 0) or 0
                total_rvu += rvu  # Always add to total
                
                time_finished_str = record.get("time_finished", "")
                if not time_finished_str:
                    # Skip records without time_finished, but still count toward total
                    logger.debug(f"Record missing time_finished: accession={record.get('accession')}, rvu={rvu}")
                    continue
                
                try:
                    time_finished = datetime.fromisoformat(time_finished_str)
                    if time_finished <= target_time:
                        rvu_at_elapsed += rvu
                        records_before_target += 1
                        logger.info(f"[PACE]   ✓ BEFORE: finished={time_finished.strftime('%H:%M:%S')}, rvu={rvu:.1f}, proc={record.get('procedure', 'N/A')[:40]}")
                    else:
                        records_after_target += 1
                        if records_after_target <= 3:  # Only log first 3 after-target records to avoid spam
                            logger.info(f"[PACE]   ✗ AFTER: finished={time_finished.strftime('%H:%M:%S')}, rvu={rvu:.1f}, proc={record.get('procedure', 'N/A')[:40]}")
                except (ValueError, TypeError) as e:
                    # Skip records with invalid time_finished format
                    logger.debug(f"Failed to parse time_finished '{time_finished_str}': {e}")
                    continue
            
            logger.info(f"[PACE] ═══ RESULT ═══ Elapsed: {elapsed_minutes:.1f}min | Target: {target_time.strftime('%H:%M:%S')} | "
                       f"RVU at elapsed: {rvu_at_elapsed:.1f} | Total RVU: {total_rvu:.1f} | "
                       f"Records before/after: {records_before_target}/{records_after_target}")
            
            return (rvu_at_elapsed, total_rvu)
            
        except Exception as e:
            logger.debug(f"Error getting prior shift RVU: {e}")
            return None
    
    def _record_or_update_study(self, study_record: dict):
        """
        Record a study, or update existing record if same accession already exists.
        If updating, keeps the highest duration among all openings.
        """
        accession = study_record.get("accession", "")
        if not accession:
            return
        
        records = self.data_manager.data["current_shift"]["records"]
        
        # Find existing record with same accession
        existing_index = None
        for i, record in enumerate(records):
            if record.get("accession") == accession:
                existing_index = i
                break
        
        new_duration = study_record.get("duration_seconds", 0)
        
        if existing_index is not None:
            # Update existing record if new duration is higher
            existing_duration = records[existing_index].get("duration_seconds", 0)
            # Ensure it's in seen_accessions (in case it wasn't added before)
            self.tracker.seen_accessions.add(accession)
            if new_duration > existing_duration:
                # Update with higher duration, but keep original time_performed
                records[existing_index]["duration_seconds"] = new_duration
                records[existing_index]["time_finished"] = study_record.get("time_finished")
                # Update other fields that might have changed
                if study_record.get("procedure"):
                    records[existing_index]["procedure"] = study_record["procedure"]
                if study_record.get("patient_class"):
                    records[existing_index]["patient_class"] = study_record["patient_class"]
                if study_record.get("study_type"):
                    records[existing_index]["study_type"] = study_record["study_type"]
                if study_record.get("rvu") is not None:
                    records[existing_index]["rvu"] = study_record["rvu"]
                self.data_manager.save()
                logger.info(f"Updated study duration for {accession}: {existing_duration:.1f}s -> {new_duration:.1f}s (kept higher duration)")
            else:
                logger.debug(f"Study {accession} already recorded with higher duration ({existing_duration:.1f}s >= {new_duration:.1f}s), skipping")
        else:
            # New study - record it
            records.append(study_record)
            self.data_manager.save()
            # Add to seen_accessions so duplicate checks are faster
            self.tracker.seen_accessions.add(accession)
            logger.info(f"Recorded new study: {accession} - {study_record.get('study_type', 'Unknown')} ({study_record.get('rvu', 0):.1f} RVU) - Duration: {new_duration:.1f}s")
    
    def _record_multi_accession_study(self, current_time):
        """Record a completed multi-accession study as SEPARATE individual studies.
        
        Each accession in the multi-accession group gets its own record with:
        - Its own accession number
        - Its own procedure and study type  
        - Its own RVU value
        - Duration split evenly among studies
        - Reference to the multi-accession group for duplicate detection
        """
        # FIRST: Check for any accessions in current_multiple_accessions that weren't collected
        # This handles the case where user didn't click on every accession in the listbox
        if self.current_multiple_accessions:
            classification_rules = self.data_manager.data.get("classification_rules", {})
            direct_lookups = self.data_manager.data.get("direct_lookups", {})
            
            # Get set of accession numbers already in multi_accession_data
            collected_acc_nums = set()
            for entry, data in self.multi_accession_data.items():
                acc_num = data.get("accession_number") or _extract_accession_number(entry)
                collected_acc_nums.add(acc_num)
            
            for acc_entry in self.current_multiple_accessions:
                acc_num = _extract_accession_number(acc_entry)
                
                # Skip if already collected
                if acc_num in collected_acc_nums:
                    continue
                
                # Try to extract procedure from listbox entry format "ACC (PROC)"
                procedure = "Unknown"
                study_type = "Unknown"
                rvu = 0.0
                
                if '(' in acc_entry and ')' in acc_entry:
                    entry_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', acc_entry)
                    if entry_match:
                        procedure = entry_match.group(2).strip()
                        study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                
                # Add to multi_accession_data
                self.multi_accession_data[acc_entry] = {
                    "procedure": procedure,
                    "study_type": study_type,
                    "rvu": rvu,
                    "patient_class": self.current_patient_class or "",
                    "accession_number": acc_num,
                }
                logger.info(f"Auto-collected uncollected accession {acc_num}: {procedure} ({rvu} RVU)")
        
        if not self.multi_accession_data:
            return
        
        all_entries = list(self.multi_accession_data.keys())
        num_studies = len(all_entries)
        
        # Determine total duration and split evenly
        if self.multi_accession_start_time:
            total_duration = (current_time - self.multi_accession_start_time).total_seconds()
        else:
            total_duration = 0
        duration_per_study = total_duration / num_studies if num_studies > 0 else 0
        
        # Get patient class from first entry (applies to all)
        patient_class_val = ""
        for d in self.multi_accession_data.values():
            if d.get("patient_class"):
                patient_class_val = d["patient_class"]
                break
        
        time_performed = self.multi_accession_start_time.isoformat() if self.multi_accession_start_time else current_time.isoformat()
        
        # DEDUPLICATE: Build a map from accession NUMBER to best data
        # This prevents recording the same accession multiple times when entries have different keys
        # (e.g., same accession appears with different procedure text in entry format "ACC (PROC)")
        accession_to_data = {}  # accession_number -> {entry, data}
        
        for entry in all_entries:
            data = self.multi_accession_data[entry]
            
            # Extract pure accession number
            if data.get("accession_number"):
                acc_num = data["accession_number"]
            elif '(' in entry and ')' in entry:
                acc_match = re.match(r'^([^(]+)', entry)
                acc_num = acc_match.group(1).strip() if acc_match else entry.strip()
            else:
                acc_num = entry.strip()
            
            # Check if we already have data for this accession number
            if acc_num in accession_to_data:
                # Keep the entry with more information (non-Unknown study type, higher RVU)
                existing_data = accession_to_data[acc_num]["data"]
                existing_unknown = existing_data.get("study_type", "Unknown") == "Unknown"
                new_unknown = data.get("study_type", "Unknown") == "Unknown"
                
                # Prefer known study type over Unknown
                if existing_unknown and not new_unknown:
                    accession_to_data[acc_num] = {"entry": entry, "data": data}
                    logger.debug(f"Replaced duplicate accession {acc_num}: Unknown -> {data.get('study_type')}")
                # If both known or both unknown, keep higher RVU
                elif existing_unknown == new_unknown and data.get("rvu", 0) > existing_data.get("rvu", 0):
                    accession_to_data[acc_num] = {"entry": entry, "data": data}
                    logger.debug(f"Replaced duplicate accession {acc_num}: higher RVU {data.get('rvu')}")
                else:
                    logger.debug(f"Skipping duplicate accession {acc_num}: keeping existing {existing_data.get('study_type')}")
            else:
                accession_to_data[acc_num] = {"entry": entry, "data": data}
        
        # Get unique accession numbers (deduplicated)
        accession_numbers = list(accession_to_data.keys())
        num_unique_studies = len(accession_numbers)
        
        if num_unique_studies < num_studies:
            logger.info(f"Deduplicated multi-accession: {num_studies} entries -> {num_unique_studies} unique accessions")
        
        # Recalculate duration per study based on unique count
        duration_per_study = total_duration / num_unique_studies if num_unique_studies > 0 else 0
        
        # Generate a unique group ID to link these studies for duplicate detection
        multi_accession_group_id = "_".join(sorted(accession_numbers))
        
        total_rvu = 0
        recorded_count = 0
        
        # Record each UNIQUE study
        for accession in accession_numbers:
            data = accession_to_data[accession]["data"]
            
            study_record = {
                "accession": accession,
                "procedure": data.get("procedure", "Unknown"),
                "patient_class": patient_class_val,
                "study_type": data.get("study_type", "Unknown"),
                "rvu": data.get("rvu", 0),
                "time_performed": time_performed,
                "time_finished": current_time.isoformat(),
                "duration_seconds": duration_per_study,
                # Track that this was from a multi-accession session
                "from_multi_accession": True,
                "multi_accession_group": multi_accession_group_id,
                "multi_accession_count": num_unique_studies,
            }
            
            total_rvu += data.get("rvu", 0)
            
            self._record_or_update_study(study_record)
            self.tracker.mark_seen(accession)
            logger.debug(f"Recorded individual study from multi-accession: {accession}")
            recorded_count += 1
        
        self.undo_used = False
        self.undo_btn.config(state=tk.NORMAL)
        
        logger.info(f"Recorded multi-accession: {recorded_count} individual studies ({total_rvu:.1f} total RVU) - Duration: {total_duration:.1f}s")
        self.update_display()
    
    def _extract_powerscribe_data(self) -> dict:
        """Extract data from PowerScribe. Returns data dict with 'found', 'accession', etc."""
        data = {
            'found': False,
            'procedure': '',
            'accession': '',
            'patient_class': '',
            'accession_title': '',
            'multiple_accessions': [],
            'elements': {},
            'source': 'PowerScribe'
        }
        
        window = self.cached_window
        if not window:
            window = find_powerscribe_window()
        
        if window:
            # Validate window still exists
            try:
                _window_text_with_timeout(window, timeout=0.5, element_name="PowerScribe window validation")
                self.cached_window = window
            except:
                self.cached_window = None
                self.cached_elements = {}
                window = find_powerscribe_window()
        
        if window:
            data['found'] = True
            
            # Smart caching: use cache if available, but invalidate on empty accession
            elements = find_elements_by_automation_id(
                window,
                ["labelProcDescription", "labelAccessionTitle", "labelAccession", "labelPatientClass", "listBoxAccessions"],
                self.cached_elements
            )
            
            data['elements'] = elements
            data['procedure'] = elements.get("labelProcDescription", {}).get("text", "").strip()
            data['patient_class'] = elements.get("labelPatientClass", {}).get("text", "").strip()
            data['accession_title'] = elements.get("labelAccessionTitle", {}).get("text", "").strip()
            data['accession'] = elements.get("labelAccession", {}).get("text", "").strip()
            
            if data['accession']:
                # Study is open - update cache for next poll
                self.cached_elements.update(elements)
            else:
                # No accession - could be stale cache or study closed
                # Clear cache and do ONE fresh search to confirm
                if self.cached_elements:
                    self.cached_elements = {}
                    # Redo search with empty cache
                    elements = find_elements_by_automation_id(
                        window,
                        ["labelProcDescription", "labelAccessionTitle", "labelAccession", "labelPatientClass", "listBoxAccessions"],
                        {}
                    )
                    data['accession'] = elements.get("labelAccession", {}).get("text", "").strip()
                    if data['accession']:
                        # Found it on fresh search - cache was stale
                        data['procedure'] = elements.get("labelProcDescription", {}).get("text", "").strip()
                        data['patient_class'] = elements.get("labelPatientClass", {}).get("text", "").strip()
                        data['accession_title'] = elements.get("labelAccessionTitle", {}).get("text", "").strip()
                        self.cached_elements.update(elements)
            
            # Handle multiple accessions - check listbox if it exists
            # Read listbox even if labelAccession is empty (multi-accession mode may have empty label)
            # Check both: study is open (accession exists) OR multi-accession mode (accession_title is plural)
            is_multi_title = data['accession_title'] in ("Accessions:", "Accessions")
            should_check_listbox = data['accession'] or is_multi_title
            
            if should_check_listbox and elements.get("listBoxAccessions"):
                try:
                    listbox = elements["listBoxAccessions"]["element"]
                    listbox_children = []
                    try:
                        children_gen = listbox.children()
                        count = 0
                        for child_elem in children_gen:
                            listbox_children.append(child_elem)
                            count += 1
                            if count >= 50:
                                break
                    except Exception as e:
                        logger.debug(f"listbox.children() iteration failed: {e}")
                        listbox_children = []
                    
                    for child in listbox_children:
                        try:
                            item_text = _window_text_with_timeout(child, timeout=0.3, element_name="listbox child").strip()
                            if item_text:
                                data['multiple_accessions'].append(item_text)
                        except:
                            pass
                    
                    # In multi-accession mode, if labelAccession is empty but we got listbox items,
                    # use the first listbox item as the accession for tracking purposes
                    if is_multi_title and not data['accession'] and data['multiple_accessions']:
                        first_acc = data['multiple_accessions'][0]
                        # Extract just the accession number if format is "ACC (PROC)"
                        if '(' in first_acc:
                            acc_match = re.match(r'^([^(]+)', first_acc)
                            if acc_match:
                                data['accession'] = acc_match.group(1).strip()
                        else:
                            data['accession'] = first_acc.strip()
                        logger.debug(f"Set accession from listbox in multi-accession mode: {data['accession']}")
                except:
                    pass
        
        return data
    
    def _extract_mosaic_data(self) -> dict:
        """Extract data from Mosaic. Returns data dict with 'found', 'accession', etc.
        
        Extraction strategy (as of v1.4.6):
        1. PRIMARY: Use extract_mosaic_data_v2() with main window descendants
           - More reliable element discovery
           - Better accession pattern matching
        2. FALLBACK: Use legacy extract_mosaic_data() with WebView2 recursion
           - Only used if primary method fails to find accession
        
        NOTE: Multi-accession support is currently limited in Mosaic.
        """
        data = {
            'found': False,
            'procedure': '',
            'accession': '',
            'patient_class': 'Unknown',
            'accession_title': '',
            'multiple_accessions': [],
            'elements': {},
            'source': 'Mosaic'
        }
        
        main_window = find_mosaic_window()
        
        if main_window:
            try:
                # Validate window still exists
                _window_text_with_timeout(main_window, timeout=1.0, element_name="Mosaic window validation")
                data['found'] = True
                
                # =========================================================
                # PRIMARY METHOD (v2): Use main window descendants
                # This is the new, more reliable extraction method
                # =========================================================
                mosaic_data = extract_mosaic_data_v2(main_window)
                extraction_method = mosaic_data.get('extraction_method', '')
                
                if mosaic_data.get('accession'):
                    # Primary method succeeded
                    logger.debug(f"Mosaic v2 extraction succeeded: {extraction_method}")
                    data['procedure'] = mosaic_data.get('procedure', '')
                    data['accession'] = mosaic_data.get('accession', '')
                    
                    # Handle multiple accessions
                    multiple_accessions_data = mosaic_data.get('multiple_accessions', [])
                    if multiple_accessions_data:
                        for acc_data in multiple_accessions_data:
                            acc = acc_data.get('accession', '')
                            proc = acc_data.get('procedure', '')
                            if proc:
                                data['multiple_accessions'].append(f"{acc} ({proc})")
                            else:
                                data['multiple_accessions'].append(acc)
                        
                        # Set first as primary if not already set
                        if not data['accession'] and multiple_accessions_data:
                            data['accession'] = multiple_accessions_data[0].get('accession', '')
                            if not data['procedure'] and multiple_accessions_data[0].get('procedure'):
                                data['procedure'] = multiple_accessions_data[0].get('procedure', '')
                else:
                    # =========================================================
                    # FALLBACK METHOD (v1 legacy): Use WebView2 recursion
                    # Only used if primary method didn't find accession
                    # TODO: Remove this fallback once v2 is proven stable
                    # =========================================================
                    logger.debug("Mosaic v2 extraction failed, trying legacy method")
                    webview = find_mosaic_webview_element(main_window)
                    
                    if webview:
                        mosaic_data = extract_mosaic_data(webview)
                        
                        data['procedure'] = mosaic_data.get('procedure', '')
                        
                        # Handle multiple accessions
                        multiple_accessions_data = mosaic_data.get('multiple_accessions', [])
                        if multiple_accessions_data:
                            for acc_data in multiple_accessions_data:
                                acc = acc_data.get('accession', '')
                                proc = acc_data.get('procedure', '')
                                if proc:
                                    data['multiple_accessions'].append(f"{acc} ({proc})")
                                else:
                                    data['multiple_accessions'].append(acc)
                            
                            if multiple_accessions_data:
                                data['accession'] = multiple_accessions_data[0].get('accession', '')
                                if not data['procedure'] and multiple_accessions_data[0].get('procedure'):
                                    data['procedure'] = multiple_accessions_data[0].get('procedure', '')
                        else:
                            data['accession'] = mosaic_data.get('accession', '')
                            if not data['procedure']:
                                data['procedure'] = mosaic_data.get('procedure', '')
                        
                        if data['accession']:
                            logger.debug("Mosaic legacy extraction succeeded")
                        else:
                            logger.debug("Mosaic legacy extraction also failed - no accession found")
                    else:
                        logger.debug("Mosaic fallback: WebView2 element not found")
                        
            except Exception as e:
                logger.debug(f"Mosaic extraction error: {e}")
                data['found'] = False
        
        return data
    
    def _toggle_data_source(self):
        """Manually toggle between PowerScribe and Mosaic data sources."""
        try:
            # Toggle between the two sources
            if self._primary_source == "PowerScribe":
                new_source = "Mosaic"
            else:
                new_source = "PowerScribe"
            
            self._primary_source = new_source
            self._active_source = new_source
            
            # Update the indicator immediately
            self._update_source_indicator(new_source)
            
            logger.info(f"Manually switched data source to: {new_source}")
        except Exception as e:
            logger.error(f"Error toggling data source: {e}")
    
    def _update_source_indicator(self, source: str):
        """Update the data source indicator in the UI (thread-safe)."""
        try:
            if source:
                text = f"📍 {source}"
            else:
                text = "detecting..."
            self.root.after(0, lambda: self.data_source_indicator.config(text=text))
        except:
            pass
    
    def _update_backup_status_display(self):
        """Update the backup status indicator in the UI."""
        try:
            if not hasattr(self, 'backup_status_label'):
                return
            
            backup_mgr = self.data_manager.backup_manager
            status = backup_mgr.get_backup_status()
            
            if status["enabled"]:
                text = f"{status['status_icon']} {status['time_since_backup'] or 'Ready'}"
                fg_color = "gray" if status["last_backup_status"] == "success" else "orange"
            else:
                text = ""  # Don't show anything if backup is disabled
                fg_color = "gray"
            
            self.backup_status_label.config(text=text, fg=fg_color)
        except Exception as e:
            logger.debug(f"Error updating backup status display: {e}")
    
    def _perform_shift_end_backup(self):
        """Perform backup at shift end if enabled and scheduled."""
        try:
            backup_mgr = self.data_manager.backup_manager
            
            # Check if backup is enabled and scheduled for shift end
            if not self.data_manager.data.get("backup", {}).get("cloud_backup_enabled", False):
                return
            
            schedule = self.data_manager.data.get("backup", {}).get("backup_schedule", "shift_end")
            if schedule != "shift_end":
                return
            
            logger.info("Performing automatic backup at shift end...")
            
            # Perform backup (runs synchronously - quick operation)
            result = backup_mgr.create_backup(force=True)
            
            if result["success"]:
                logger.info(f"Shift-end backup completed: {result['path']}")
            else:
                logger.warning(f"Shift-end backup failed: {result['error']}")
            
            # Save updated backup status
            self.data_manager.save()
            
            # Update UI
            self._update_backup_status_display()
            
        except Exception as e:
            logger.error(f"Error performing shift-end backup: {e}")
    
    def _check_first_time_backup_prompt(self):
        """Check if we should prompt user to enable cloud backup on first run."""
        try:
            backup_settings = self.data_manager.data.get("backup", {})
            
            # Debug logging
            logger.debug(f"Backup prompt check - cloud_backup_enabled: {backup_settings.get('cloud_backup_enabled', False)}, "
                        f"setup_prompt_dismissed: {backup_settings.get('setup_prompt_dismissed', False)}, "
                        f"first_backup_prompt_shown: {backup_settings.get('first_backup_prompt_shown', False)}")
            
            # Don't prompt if:
            # - Backup is already enabled
            # - User has already dismissed the prompt
            # - User has already seen the prompt (first_backup_prompt_shown)
            # - OneDrive is not available
            if backup_settings.get("cloud_backup_enabled", False):
                logger.debug("Skipping backup prompt - backup is already enabled")
                return
            if backup_settings.get("setup_prompt_dismissed", False):
                logger.debug("Skipping backup prompt - user dismissed it")
                return
            if backup_settings.get("first_backup_prompt_shown", False):
                logger.debug("Skipping backup prompt - user has already seen it")
                return
            if not self.data_manager.backup_manager.is_onedrive_available():
                logger.debug("Skipping backup prompt - OneDrive not available")
                return
            
            # Schedule the prompt to appear shortly after app starts
            logger.info("Scheduling backup setup prompt")
            self.root.after(2000, self._show_backup_setup_prompt)
            
        except Exception as e:
            logger.error(f"Error checking backup prompt: {e}")
    
    def _show_backup_setup_prompt(self):
        """Show the first-time backup setup prompt."""
        try:
            backup_mgr = self.data_manager.backup_manager
            onedrive_path = backup_mgr._detect_onedrive_folder()
            
            # Create a simple, non-intrusive dialog
            dialog = tk.Toplevel(self.root)
            dialog.title("☁️ Protect Your Work Data")
            dialog.transient(self.root)
            dialog.grab_set()
            
            # Position near main window
            x = self.root.winfo_x() + 50
            y = self.root.winfo_y() + 50
            dialog.geometry(f"380x220+{x}+{y}")
            dialog.resizable(False, False)
            
            # Content frame
            frame = ttk.Frame(dialog, padding="15")
            frame.pack(fill=tk.BOTH, expand=True)
            
            # Icon and title
            ttk.Label(frame, text="☁️ Enable Cloud Backup?", 
                     font=("Arial", 12, "bold")).pack(anchor=tk.W, pady=(0, 10))
            
            # Description
            ttk.Label(frame, text="OneDrive was detected on your computer.\n"
                                  "Automatic backups protect your work data from loss.",
                     font=("Arial", 9), wraplength=350, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))
            
            # OneDrive path
            ttk.Label(frame, text=f"📁 {onedrive_path}", 
                     font=("Arial", 8), foreground="gray").pack(anchor=tk.W, pady=(0, 15))
            
            # Buttons frame
            btn_frame = ttk.Frame(frame)
            btn_frame.pack(fill=tk.X)
            
            def enable_backup():
                # Enable backup with default settings
                if "backup" not in self.data_manager.data:
                    self.data_manager.data["backup"] = {}
                self.data_manager.data["backup"]["cloud_backup_enabled"] = True
                self.data_manager.data["backup"]["backup_schedule"] = "shift_end"
                # Mark that user has seen and responded to the prompt
                self.data_manager.data["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.data["backup"]["first_backup_prompt_shown"] = True
                
                # Also update BackupManager's settings reference
                if "backup" not in self.data_manager.backup_manager.settings:
                    self.data_manager.backup_manager.settings["backup"] = {}
                self.data_manager.backup_manager.settings["backup"]["cloud_backup_enabled"] = True
                self.data_manager.backup_manager.settings["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.backup_manager.settings["backup"]["first_backup_prompt_shown"] = True
                
                # Save to disk
                self.data_manager.save()
                
                # Verify save
                logger.info(f"Backup enabled - saved. cloud_backup_enabled: {self.data_manager.data.get('backup', {}).get('cloud_backup_enabled', False)}")
                
                # Update UI
                self._update_backup_status_display()
                
                dialog.destroy()
                messagebox.showinfo("Backup Enabled", 
                                   "Cloud backup is now enabled!\n\n"
                                   "Your data will be backed up automatically\n"
                                   "after each shift ends.")
            
            def maybe_later():
                # Mark that user has seen the prompt so it doesn't show again
                if "backup" not in self.data_manager.data:
                    self.data_manager.data["backup"] = {}
                self.data_manager.data["backup"]["first_backup_prompt_shown"] = True
                self.data_manager.save()
                dialog.destroy()
            
            def dont_ask_again():
                # Set flag to not ask again
                if "backup" not in self.data_manager.data:
                    self.data_manager.data["backup"] = {}
                self.data_manager.data["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.save()
                dialog.destroy()
            
            ttk.Button(btn_frame, text="Enable Backup", command=enable_backup).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="Maybe Later", command=maybe_later).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="Don't Ask Again", command=dont_ask_again).pack(side=tk.RIGHT, padx=2)
            
        except Exception as e:
            logger.error(f"Error showing backup setup prompt: {e}")
    
    def _powerscribe_worker(self):
        """Background thread: Continuously poll PowerScribe or Mosaic for data with auto-switching."""
        import time
        
        while self._ps_thread_running:
            poll_start_time = time.time()
            try:
                data = {
                    'found': False,
                    'procedure': '',
                    'accession': '',
                    'patient_class': '',
                    'accession_title': '',
                    'multiple_accessions': [],
                    'elements': {},
                    'source': None
                }
                
                # Auto-switch logic: Check primary source first, then secondary if primary is idle
                primary_data = None
                secondary_data = None
                current_time = time.time()
                
                # Determine which sources are available (quick check)
                ps_available = quick_check_powerscribe()
                mosaic_available = quick_check_mosaic()
                
                # If only one source is available, use it
                if ps_available and not mosaic_available:
                    data = self._extract_powerscribe_data()
                    # Always set source when window is available (even if no study is open)
                    self._active_source = "PowerScribe"
                    if data.get('accession'):
                        self._primary_source = "PowerScribe"
                elif mosaic_available and not ps_available:
                    data = self._extract_mosaic_data()
                    # Always set source when window is available (even if no study is open)
                    self._active_source = "Mosaic"
                    if data.get('accession'):
                        self._primary_source = "Mosaic"
                elif ps_available and mosaic_available:
                    # Both available - use tiered polling
                    # Check primary source first
                    if self._primary_source == "PowerScribe":
                        primary_data = self._extract_powerscribe_data()
                    else:
                        primary_data = self._extract_mosaic_data()
                    
                    if primary_data.get('accession'):
                        # Primary has active study - use it, skip secondary
                        data = primary_data
                        self._active_source = self._primary_source
                    else:
                        # Primary is idle - check secondary
                        # But not too frequently (every 5 seconds when primary is idle)
                        if current_time - self._last_secondary_check >= self._secondary_check_interval:
                            self._last_secondary_check = current_time
                            
                            if self._primary_source == "PowerScribe":
                                secondary_data = self._extract_mosaic_data()
                            else:
                                secondary_data = self._extract_powerscribe_data()
                            
                            if secondary_data.get('accession'):
                                # Secondary has active study - SWITCH!
                                data = secondary_data
                                old_primary = self._primary_source
                                self._primary_source = secondary_data.get('source', self._primary_source)
                                self._active_source = self._primary_source
                                logger.info(f"Auto-switched data source: {old_primary} → {self._active_source}")
                            else:
                                # Neither has active study - use primary's data (still shows window found)
                                data = primary_data
                                self._active_source = self._primary_source
                        else:
                            # Not time to check secondary yet - use primary data
                            data = primary_data
                            self._active_source = self._primary_source
                else:
                    # Neither available
                    self._active_source = None
                
                # Update source indicator
                self._update_source_indicator(self._active_source)
                
                # Update shared data IMMEDIATELY with PowerScribe/Mosaic data (before Clario query)
                # This ensures the display shows data immediately even if Clario is slow
                current_accession = data.get('accession', '').strip()
                current_procedure = data.get('procedure', '').strip()
                is_na_procedure = current_procedure.lower() in ["n/a", "na", "none", ""]
                
                with self._ps_lock:
                    self._ps_data = data.copy()  # Store copy immediately
                    
                    # If we have a valid accession and procedure, store it as pending
                    # This ensures we don't lose studies if procedure changes to N/A before refresh_data
                    if current_accession and current_procedure and not is_na_procedure:
                        self._pending_studies[current_accession] = {
                            'procedure': current_procedure,
                            'patient_class': data.get('patient_class', ''),
                            'detected_at': time.time()
                        }
                        logger.debug(f"Stored pending study: {current_accession} - {current_procedure}")
                
                # Query Clario for patient class only when a new study is detected (accession changed)
                # Do this AFTER storing initial data so display isn't blocked
                multiple_accessions_list = data.get('multiple_accessions', [])
                
                # For multi-accession studies, extract all accession numbers
                all_accessions = set()
                if current_accession:
                    all_accessions.add(current_accession)
                if multiple_accessions_list:
                    for acc_entry in multiple_accessions_list:
                        # Format: "ACC (PROC)" or just "ACC"
                        if '(' in acc_entry and ')' in acc_entry:
                            acc_match = re.match(r'^([^(]+)', acc_entry)
                            if acc_match:
                                all_accessions.add(acc_match.group(1).strip())
                            else:
                                all_accessions.add(acc_entry.strip())
                
                if data.get('found') and all_accessions:
                    # Check if this is a new study (accession changed)
                    # For multi-accession, check if any accession is new
                    with self._ps_lock:
                        is_new_study = not any(acc == self._last_clario_accession for acc in all_accessions)
                        last_accession = self._last_clario_accession
                    
                    logger.debug(f"Checking Clario: current_accession='{current_accession}', all_accessions={list(all_accessions)}, last_clario_accession='{last_accession}', is_new_study={is_new_study}")
                    
                    if is_new_study:
                        # New study detected - query Clario (don't pass target_accession for multi-accession, let it match any)
                        # Query Clario in a separate try block so it doesn't block data display
                        logger.info(f"New study detected, querying Clario. Multi-accession: {len(all_accessions) > 1}, accessions: {list(all_accessions)}")
                        try:
                            # Query Clario without target_accession for multi-accession studies
                            # This allows Clario to match any of the accessions
                            if len(all_accessions) > 1:
                                # Multi-accession: query without target, then check if result matches any
                                clario_data = extract_clario_patient_class(target_accession=None)
                            else:
                                # Single accession: query with target
                                clario_data = extract_clario_patient_class(target_accession=current_accession)
                            
                            if clario_data and clario_data.get('patient_class'):
                                # Verify accession matches (for multi-accession, match any accession)
                                clario_accession = clario_data.get('accession', '').strip()
                                logger.info(f"Clario returned: patient_class='{clario_data.get('patient_class')}', accession='{clario_accession}'")
                                
                                # Check if Clario accession matches any of our accessions
                                accession_matches = clario_accession in all_accessions if clario_accession else False
                                
                                if accession_matches:
                                    # Accession matches - update data with Clario's patient class
                                    with self._ps_lock:
                                        # Update the stored data with Clario patient class
                                        self._ps_data['patient_class'] = clario_data['patient_class']
                                        self._last_clario_accession = clario_accession
                                        # Cache patient class for all accessions in this multi-accession study
                                        for acc in all_accessions:
                                            self._clario_patient_class_cache[acc] = clario_data['patient_class']
                                    logger.info(f"Clario patient class OVERRIDES: {clario_data['patient_class']} for study (matched accession: {clario_accession})")
                                    # Trigger immediate UI refresh to display Clario patient class
                                    self.root.after(0, self.refresh_data)
                            else:
                                # Clario didn't return data - keep existing patient_class from PowerScribe/Mosaic
                                # But still mark this study as seen to prevent repeated queries
                                with self._ps_lock:
                                    if current_accession:
                                        self._last_clario_accession = current_accession
                                if clario_data:
                                    logger.info(f"Clario returned data but no patient_class. Accession='{clario_data.get('accession', '')}'")
                                else:
                                    logger.info(f"Clario did not return any data")
                        except Exception as e:
                            logger.info(f"Clario query error: {e}", exc_info=True)
                            # On error, keep existing patient_class (already stored in _ps_data)
                            # Mark study as seen to prevent repeated queries
                            with self._ps_lock:
                                if current_accession:
                                    self._last_clario_accession = current_accession
                    else:
                        # Same study - check if we have cached Clario patient class for any accession
                        with self._ps_lock:
                            cached_clario_class = None
                            for acc in all_accessions:
                                cached = self._clario_patient_class_cache.get(acc)
                                if cached:
                                    cached_clario_class = cached
                                    break
                        
                        if cached_clario_class:
                            # Update stored data with cached Clario patient class
                            with self._ps_lock:
                                self._ps_data['patient_class'] = cached_clario_class
                            logger.debug(f"Same study (accessions={list(all_accessions)}), using cached Clario patient class: {cached_clario_class}")
                            # Trigger immediate UI refresh to display cached Clario patient class
                            self.root.after(0, self.refresh_data)
                elif data.get('found') and not all_accessions:
                    # No accession - study is closed
                    # Clear last Clario accession so if the same study reopens, it queries Clario again
                    with self._ps_lock:
                        if self._last_clario_accession:
                            logger.debug(f"Study closed - clearing _last_clario_accession (was: {self._last_clario_accession})")
                            self._last_clario_accession = ""
                        # For Mosaic, ensure patient_class is set to 'Unknown' if missing
                        current_source = data.get('source') or self._active_source
                        if current_source == "Mosaic":
                            if not self._ps_data.get('patient_class'):
                                self._ps_data['patient_class'] = 'Unknown'
                    logger.debug(f"No accession found, cannot query Clario")
                
                # Adaptive polling: adjust interval based on activity state
                # Use the stored data from _ps_data for consistency
                with self._ps_lock:
                    current_accession_check = self._ps_data.get('accession', '').strip()
                
                # Detect if accession changed (including going from something to empty)
                accession_changed = current_accession_check != self._last_accession_seen
                study_just_closed = accession_changed and self._last_accession_seen and not current_accession_check
                
                if accession_changed:
                    # Accession changed - use fast polling
                    self._last_accession_seen = current_accession_check
                    self._last_data_change_time = time.time()
                    if study_just_closed:
                        # Study just closed - use very fast polling (300ms) to confirm closure quickly
                        self._current_poll_interval = 0.3
                        logger.debug(f"Study closed - fast polling at 0.3s")
                    else:
                        # New study appeared - use fast polling (500ms)
                        self._current_poll_interval = 0.5
                else:
                    # Check how long since last change
                    time_since_change = time.time() - self._last_data_change_time
                    if current_accession_check:
                        # Active study but no change - moderate polling (1000ms)
                        if time_since_change > 1.0:
                            self._current_poll_interval = 1.0
                        else:
                            self._current_poll_interval = 0.5
                    else:
                        # No active study - keep fast polling for 2 seconds after closure
                        # This ensures quick detection and prevents false re-detection
                        if time_since_change < 2.0:
                            self._current_poll_interval = 0.3
                        else:
                            # After 2 seconds of no study, slow down to 1.5s (not 2.0s)
                            self._current_poll_interval = 1.5
                
                # Clean up stale pending studies (older than 30 seconds)
                current_time_cleanup = time.time()
                with self._ps_lock:
                    stale_accessions = [
                        acc for acc, data in self._pending_studies.items()
                        if current_time_cleanup - data.get('detected_at', 0) > 30
                    ]
                    for acc in stale_accessions:
                        logger.debug(f"Removing stale pending study: {acc}")
                        del self._pending_studies[acc]
                
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)
            
            # Watchdog: detect if polling loop took too long
            poll_duration = time.time() - poll_start_time
            if poll_duration > 5.0:
                logger.warning(f"⚠️  Polling loop took {poll_duration:.1f}s (expected <1s) - UI automation may be hanging")
                # Clear cached windows/elements to force fresh detection next time
                self.cached_window = None
                self.cached_elements = {}
                logger.info("Cleared cached windows due to slow polling - will re-detect next cycle")
            
            # Use adaptive polling interval
            time.sleep(self._current_poll_interval)
    
    def refresh_data(self):
        """Refresh data from PowerScribe - reads from background thread data."""
        try:
            # Get data from background thread (non-blocking)
            with self._ps_lock:
                ps_data = self._ps_data.copy()
            
            # Use auto-detected source instead of settings
            data_source = ps_data.get('source') or self._active_source or "PowerScribe"
            source_name = data_source if data_source else "Unknown"
            
            if not ps_data.get('found', False):
                self.root.title(f"RVU Counter - {source_name} not found")
                
                # If we were in multi-accession mode, record the study before clearing state
                if self.multi_accession_mode and self.multi_accession_data:
                    logger.info("PowerScribe window closed while in multi-accession mode - recording study")
                    self._record_multi_accession_study(datetime.now())
                    self.multi_accession_mode = False
                    self.multi_accession_data = {}
                    self.multi_accession_start_time = None
                    self.multi_accession_last_procedure = ""
                
                self.current_accession = ""
                self.current_procedure = ""
                self.current_study_type = ""
                self.update_debug_display()
                return
        
            self.root.title("RVU Counter")
            
            # Extract data from background thread results
            elements = ps_data.get('elements', {})
            procedure = ps_data.get('procedure', '')
            patient_class = ps_data.get('patient_class', '')
            accession_title = ps_data.get('accession_title', '')
            accession = ps_data.get('accession', '')
            multiple_accessions = ps_data.get('multiple_accessions', [])
            
            # For Mosaic: multiple accessions should be treated as separate studies
            # For PowerScribe: use the existing multi-accession mode logic
            mosaic_multiple_mode = False  # Track if we're in Mosaic multi-accession mode
            
            # Debug: log what we're getting from worker thread (only when there's data)
            if data_source == "Mosaic" and (accession or procedure):
                logger.debug(f"Mosaic data - procedure: '{procedure}', accession: '{accession}', multiple_accessions: {multiple_accessions}")
            
            # For Mosaic, also check if we have multiple active studies that might indicate multi-accession
            # This handles the case where extraction found them separately but they should be displayed together
            if data_source == "Mosaic" and not multiple_accessions:
                # Get all currently active Mosaic studies (check if they were recently added - within last 30 seconds)
                current_time_check = datetime.now()
                active_mosaic_studies = []
                for acc, study in self.tracker.active_studies.items():
                    if acc and study.get('patient_class') == 'Unknown':  # Mosaic studies have Unknown patient class
                        time_since_start = (current_time_check - study['start_time']).total_seconds()
                        if time_since_start < 30:  # Only include recently added studies (within 30 seconds)
                            active_mosaic_studies.append(acc)
                
                if len(active_mosaic_studies) > 1:
                    # We have multiple active studies - construct multiple_accessions for display
                    multiple_accessions = []
                    for acc in active_mosaic_studies:
                        if acc in self.tracker.active_studies:
                            study = self.tracker.active_studies[acc]
                            proc = study.get('procedure', '')
                            if proc:
                                multiple_accessions.append(f"{acc} ({proc})")
                            else:
                                multiple_accessions.append(acc)
                    logger.info(f"Mosaic: Constructed multiple_accessions from {len(active_mosaic_studies)} active studies: {multiple_accessions}")
                    # Also update accession and procedure if not set
                    if not accession and active_mosaic_studies:
                        accession = active_mosaic_studies[0]
                    # Set procedure to "Multiple studies" when we have multiple
                    if len(active_mosaic_studies) > 1:
                        procedure = "Multiple studies"
                    elif not procedure and active_mosaic_studies:
                        # Single study case - get procedure from it
                        if active_mosaic_studies[0] in self.tracker.active_studies:
                            study = self.tracker.active_studies[active_mosaic_studies[0]]
                            procedure = study.get('procedure', '')
            
            if data_source == "Mosaic" and multiple_accessions and len(multiple_accessions) > 1:
                mosaic_multiple_mode = True
                # Mosaic provides one-to-one accession-to-procedure mapping
                # Track each as a separate study that can complete independently
                is_multiple_mode = False  # Don't use PowerScribe multi-accession mode
                is_multi_accession_view = False
                
                # Parse multiple accessions from format "ACC (PROC)" or just "ACC"
                # Extract accession and procedure pairs
                mosaic_accession_procedures = []
                logger.debug(f"Parsing Mosaic multiple accessions: {multiple_accessions}")
                for acc_entry in multiple_accessions:
                    if '(' in acc_entry and ')' in acc_entry:
                        # Format: "ACC (PROC)"
                        acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', acc_entry)
                        if acc_match:
                            acc = acc_match.group(1).strip()
                            proc = acc_match.group(2).strip()
                            mosaic_accession_procedures.append({'accession': acc, 'procedure': proc})
                            logger.debug(f"Parsed: accession='{acc}', procedure='{proc}'")
                    else:
                        # Just accession, use current procedure if available
                        mosaic_accession_procedures.append({'accession': acc_entry, 'procedure': procedure})
                        logger.debug(f"Parsed (no proc): accession='{acc_entry}', using procedure='{procedure}'")
                
                logger.debug(f"Parsed {len(mosaic_accession_procedures)} accession/procedure pairs")
                
                # Track each accession separately (they'll complete when they disappear)
                for acc_data in mosaic_accession_procedures:
                    acc = acc_data['accession']
                    proc = acc_data['procedure']
                    
                    # Only track if not already seen (if ignoring duplicates)
                    # Also check if it was part of a multi-accession study
                    ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
                    if self.tracker.should_ignore(acc, ignore_duplicates, self.data_manager):
                        continue
                    
                    # Track as individual study
                    if acc not in self.tracker.active_studies and proc:
                        classification_rules = self.data_manager.data.get("classification_rules", {})
                        direct_lookups = self.data_manager.data.get("direct_lookups", {})
                        study_type, rvu = match_study_type(proc, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                        
                        current_time_tracking = datetime.now()
                        self.tracker.active_studies[acc] = {
                            "accession": acc,
                            "procedure": proc,
                            "study_type": study_type,
                            "rvu": rvu,
                            "patient_class": patient_class,
                            "start_time": current_time_tracking,
                            "last_seen": current_time_tracking  # Required by tracker
                        }
                        logger.info(f"Started tracking Mosaic study: {acc} - {proc} ({rvu} RVU)")
                
                # Set display to first accession/procedure and calculate study type/RVU
                if mosaic_accession_procedures:
                    accession = mosaic_accession_procedures[0]['accession']
                    # For multiple accessions, show "Multiple studies" instead of first procedure
                    if len(mosaic_accession_procedures) > 1:
                        procedure = "Multiple studies"
                    else:
                        # Single accession - get the procedure
                        first_procedure = None
                        for acc_data in mosaic_accession_procedures:
                            proc = acc_data.get('procedure', '')
                            if proc:
                                first_procedure = proc
                                break
                        procedure = first_procedure or procedure  # Use first valid procedure, or fallback
                    
                    # Always set study type and RVU for display
                    classification_rules = self.data_manager.data.get("classification_rules", {})
                    direct_lookups = self.data_manager.data.get("direct_lookups", {})
                    
                    # If multiple accessions, show summary of all studies
                    if len(mosaic_accession_procedures) > 1:
                        # Calculate total RVU and determine modality from all procedures
                        modalities = set()
                        total_rvu = 0
                        valid_procedures = []
                        
                        for acc_data in mosaic_accession_procedures:
                            proc = acc_data.get('procedure', '')
                            if proc:
                                valid_procedures.append(proc)
                                temp_st, temp_rvu = match_study_type(proc, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                                total_rvu += temp_rvu
                                if temp_st:
                                    parts = temp_st.split()
                                    if parts:
                                        modalities.add(parts[0])
                        
                        if valid_procedures:
                            modality = list(modalities)[0] if modalities else "Studies"
                            self.current_study_type = f"{len(mosaic_accession_procedures)} {modality} studies"
                            self.current_study_rvu = total_rvu
                            # Show "Multiple studies" for procedure when there are multiple accessions
                            procedure = "Multiple studies"
                        else:
                            # No valid procedures yet - set placeholder so display shows something
                            self.current_study_type = f"{len(mosaic_accession_procedures)} studies"
                            self.current_study_rvu = 0.0
                            procedure = "Multiple studies"  # Placeholder to trigger display
                    else:
                        # Single accession - set from first (and only) accession
                        if procedure:
                            study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                            self.current_study_type = study_type
                            self.current_study_rvu = rvu
                else:
                            self.current_study_type = ""
                            self.current_study_rvu = 0.0
                
                # Debug: log what we're setting for display
                logger.debug(f"Mosaic multi-accession display - procedure: '{procedure}', accession: '{accession}', study_type: '{self.current_study_type}', rvu: {self.current_study_rvu}")
            else:
                # PowerScribe logic: Check labelAccessionTitle to determine single vs multiple accession mode
                is_multiple_mode = accession_title == "Accessions:" or accession_title == "Accessions"
                is_multi_accession_view = False  # Flag to prevent normal single-study tracking
            
            # Only process PowerScribe multi-accession mode if not Mosaic
            if data_source != "Mosaic" and is_multiple_mode and multiple_accessions:
                logger.debug(f"PowerScribe multi-accession mode: {len(multiple_accessions)} accessions, already in mode: {self.multi_accession_mode}")
                accession = "Multiple Accessions"
                is_multi_accession_view = True  # Flag to prevent normal single-study tracking
                
                # Check if we're transitioning from single to multi-accession
                if not self.multi_accession_mode:
                    # Check if ALL accessions were already completed (to prevent duplicates)
                    # Extract just accession numbers from multiple_accessions (format: "ACC (PROC)" or "ACC")
                    accession_numbers = []
                    for acc_entry in multiple_accessions:
                        if '(' in acc_entry and ')' in acc_entry:
                            # Format: "ACC (PROC)" - extract just the accession
                            acc_match = re.match(r'^([^(]+)', acc_entry)
                            if acc_match:
                                accession_numbers.append(acc_match.group(1).strip())
                        else:
                            # Just accession number
                            accession_numbers.append(acc_entry.strip())
                    
                    ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
                    
                    # Check ALL accessions against both seen_accessions AND database
                    # This is important because seen_accessions is session-based
                    all_recorded = False
                    recorded_count = 0
                    
                    if ignore_duplicates and accession_numbers:
                        current_shift = None
                        try:
                            current_shift = self.data_manager.db.get_current_shift()
                        except:
                            pass
                        
                        for acc in accession_numbers:
                            is_recorded = False
                            
                            # Check memory cache first
                            if acc in self.tracker.seen_accessions:
                                is_recorded = True
                            # Check database
                            elif current_shift:
                                try:
                                    db_record = self.data_manager.db.find_record_by_accession(
                                        current_shift['id'], acc
                                    )
                                    if db_record:
                                        is_recorded = True
                                        self.tracker.seen_accessions.add(acc)  # Cache for future
                                except:
                                    pass
                            # Check multi-accession history
                            if not is_recorded:
                                if self.tracker._was_part_of_multi_accession(acc, self.data_manager):
                                    is_recorded = True
                            
                            if is_recorded:
                                recorded_count += 1
                        
                        all_recorded = recorded_count == len(accession_numbers)
                    
                    if all_recorded:
                        # All accessions already recorded - DON'T enter multi_accession_mode
                        # Just display as duplicate study
                        logger.info(f"Duplicate multi-accession study detected (all {len(accession_numbers)} accessions already recorded): {accession_numbers}")
                        # Don't enter multi_accession_mode - this prevents re-recording
                        # The display will show "already recorded" via update_debug_display
                    else:
                        # Starting multi-accession mode (some or all are new)
                        self.multi_accession_mode = True
                        self.multi_accession_start_time = datetime.now()
                        self.multi_accession_data = {}
                        self.multi_accession_last_procedure = ""  # Reset so first procedure gets collected
                        
                        if recorded_count > 0:
                            logger.info(f"Starting multi-accession mode with {len(accession_numbers)} accessions ({recorded_count} already recorded)")
                        else:
                            logger.info(f"Starting multi-accession mode with {len(accession_numbers)} accessions")
                    
                    # Clear element cache to ensure fresh listbox data on next poll
                    # This is important for single-to-multi transition where the UI changes
                    self.cached_elements = {}
                    
                    # Check if any of the new accessions were being tracked as single
                    # If so, migrate their data to multi-accession tracking
                    # Must extract accession numbers since multiple_accessions may be "ACC (PROC)" format
                    # Track which accession NUMBERS we've already migrated to prevent duplicates
                    migrated_acc_nums = set()
                    
                    for acc_entry in multiple_accessions:
                        # Extract just the accession number from "ACC (PROC)" format
                        acc_num = _extract_accession_number(acc_entry)
                        
                        # Skip if we've already processed this accession NUMBER
                        # (could appear multiple times with different procedure text)
                        if acc_num in migrated_acc_nums:
                            logger.debug(f"Skipping duplicate accession {acc_num} during initial migration")
                            continue
                        
                        if acc_num in self.tracker.active_studies:
                            study = self.tracker.active_studies[acc_num]
                            # Store with BOTH the raw acc_entry (for listbox matching) and parsed acc_num
                            self.multi_accession_data[acc_entry] = {
                                "procedure": study["procedure"],
                                "study_type": study["study_type"],
                                "rvu": study["rvu"],
                                "patient_class": study.get("patient_class", ""),
                                "accession_number": acc_num,  # Store parsed accession for recording
                            }
                            # Remove from active_studies to prevent completion
                            del self.tracker.active_studies[acc_num]
                            migrated_acc_nums.add(acc_num)
                            logger.info(f"Migrated {acc_num} from single to multi-accession tracking (entry: {acc_entry})")
                    
                    logger.info(f"Started multi-accession mode with {len(multiple_accessions)} accessions")
                else:
                    # ALREADY in multi-accession mode - handle dynamic changes:
                    # 1. Check for NEW accessions added (2→3→4, etc.)
                    # 2. Check for accessions REMOVED from the list
                    
                    # Get current accession numbers from listbox
                    current_acc_nums = set(_extract_accession_number(e) for e in multiple_accessions)
                    
                    # Get tracked accession numbers
                    tracked_acc_nums = set()
                    for entry, data in self.multi_accession_data.items():
                        tracked_acc_nums.add(data.get("accession_number") or _extract_accession_number(entry))
                    
                    # Check for NEW accessions added
                    for acc_entry in multiple_accessions:
                        acc_num = _extract_accession_number(acc_entry)
                        
                        # Skip if already tracked (check by accession number, not entry string)
                        if acc_num in tracked_acc_nums:
                            continue
                        
                        # Check if this was being tracked as a single study
                        if acc_num in self.tracker.active_studies:
                            study = self.tracker.active_studies[acc_num]
                            self.multi_accession_data[acc_entry] = {
                                "procedure": study["procedure"],
                                "study_type": study["study_type"],
                                "rvu": study["rvu"],
                                "patient_class": study.get("patient_class", ""),
                                "accession_number": acc_num,
                            }
                            del self.tracker.active_studies[acc_num]
                            logger.info(f"ADDED {acc_num} to multi-accession (was single study, now {len(multiple_accessions)} total)")
                        else:
                            # New accession not previously tracked - will be collected when user views it
                            logger.debug(f"New accession {acc_num} added to multi-accession (will collect procedure when viewed)")
                    
                    # Check for accessions REMOVED (only if we have data for them)
                    entries_to_remove = []
                    for entry, data in self.multi_accession_data.items():
                        acc_num = data.get("accession_number") or _extract_accession_number(entry)
                        if acc_num not in current_acc_nums:
                            entries_to_remove.append((entry, acc_num, data))
                    
                    if entries_to_remove:
                        for entry, acc_num, data in entries_to_remove:
                            del self.multi_accession_data[entry]
                            logger.info(f"REMOVED {acc_num} from multi-accession (no longer in list, now {len(multiple_accessions)} total)")
                
                # Collect procedure for current view - ONLY when procedure changes
                # This ensures we only collect when user clicks a different accession
                if procedure and procedure.strip().lower() not in ["n/a", "na", "none", ""]:
                    # Check if this is a NEW procedure (different from last seen)
                    procedure_changed = (procedure != self.multi_accession_last_procedure)
                    
                    if procedure_changed:
                        classification_rules = self.data_manager.data.get("classification_rules", {})
                        direct_lookups = self.data_manager.data.get("direct_lookups", {})
                        study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                        
                        # Build set of accession NUMBERS already collected (not entry keys)
                        # This prevents adding the same accession twice under different entry formats
                        collected_acc_nums = set()
                        for entry, data in self.multi_accession_data.items():
                            existing_acc_num = data.get("accession_number") or _extract_accession_number(entry)
                            collected_acc_nums.add(existing_acc_num)
                        
                        # Find which accession this procedure belongs to
                        # Strategy: 
                        # 1. Try to match by procedure name in the listbox entry (format: "ACC (PROC)")
                        # 2. Fall back to first accession without data
                        matched_acc = None
                        matched_acc_num = None
                        
                        # First, try to match by procedure text in the listbox entry
                        for acc_entry in multiple_accessions:
                            # Extract accession number from entry
                            entry_acc_num = _extract_accession_number(acc_entry)
                            
                            # Skip if this accession NUMBER is already collected
                            if entry_acc_num in collected_acc_nums:
                                continue
                                
                            # Check if procedure is embedded in the entry (format: "ACC (PROC)")
                            if '(' in acc_entry and ')' in acc_entry:
                                entry_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', acc_entry)
                                if entry_match:
                                    embedded_proc = entry_match.group(2).strip()
                                    # Check if embedded procedure matches current procedure (case-insensitive partial match)
                                    if (embedded_proc.upper() in procedure.upper() or 
                                        procedure.upper() in embedded_proc.upper()):
                                        matched_acc = acc_entry
                                        matched_acc_num = entry_acc_num
                                        break
                        
                        # Fall back: assign to first accession NUMBER not yet collected
                        if not matched_acc:
                            for acc_entry in multiple_accessions:
                                entry_acc_num = _extract_accession_number(acc_entry)
                                if entry_acc_num not in collected_acc_nums:
                                    matched_acc = acc_entry
                                    matched_acc_num = entry_acc_num
                                    break
                        
                        if matched_acc:
                            # Use the extracted accession number
                            acc_num = matched_acc_num if matched_acc_num else _extract_accession_number(matched_acc)
                            
                            self.multi_accession_data[matched_acc] = {
                                "procedure": procedure,
                                "study_type": study_type,
                                "rvu": rvu,
                                "patient_class": patient_class,
                                "accession_number": acc_num,  # Store parsed accession for recording
                            }
                            logger.info(f"Collected procedure for {acc_num}: {procedure} ({rvu} RVU)")
                        
                        # Update last seen procedure
                        self.multi_accession_last_procedure = procedure
            elif data_source != "Mosaic" and is_multiple_mode:
                # PowerScribe: Multiple accessions but list not loaded yet
                accession = "Multiple (loading...)"
                is_multi_accession_view = True  # Prevent single-study tracking for placeholder
            else:
                # SINGLE ACCESSION mode (PowerScribe) or Mosaic single/multiple handled above
                if data_source == "PowerScribe":
                    # PowerScribe: get from labelAccession
                    accession = elements.get("labelAccession", {}).get("text", "").strip()
                # For Mosaic, accession is already set above
                
                # Handle MULTI→SINGLE transition (PowerScribe only)
                if data_source == "PowerScribe" and self.multi_accession_mode:
                    if self.multi_accession_data:
                        # Check if the remaining single accession was in our multi-accession tracking
                        remaining_acc = accession.strip() if accession else ""
                        migrated_back = False
                        
                        if remaining_acc and len(self.multi_accession_data) > 1:
                            # Multiple accessions were tracked - one is continuing as single
                            # Find and migrate that one back, record the others
                            for entry, data in list(self.multi_accession_data.items()):
                                acc_num = data.get("accession_number") or _extract_accession_number(entry)
                                if acc_num == remaining_acc:
                                    # This accession continues - migrate back to single tracking
                                    # Don't restart timer - use the multi_accession_start_time
                                    self.tracker.active_studies[acc_num] = {
                                        "accession": acc_num,
                                        "procedure": data["procedure"],
                                        "study_type": data["study_type"],
                                        "rvu": data["rvu"],
                                        "patient_class": data.get("patient_class", ""),
                                        "start_time": self.multi_accession_start_time or datetime.now(),
                                        "last_seen": datetime.now(),
                                    }
                                    del self.multi_accession_data[entry]
                                    migrated_back = True
                                    logger.info(f"MIGRATED {acc_num} back to single-accession tracking (multi→single transition)")
                                    break
                            
                            # Record remaining accessions (the ones that were completed/removed)
                            if self.multi_accession_data:
                                logger.info(f"Recording {len(self.multi_accession_data)} completed accessions from multi→single transition")
                                self._record_multi_accession_study(datetime.now())
                        elif remaining_acc and len(self.multi_accession_data) == 1:
                            # Only one accession was in multi-mode, now single - just migrate back
                            entry, data = list(self.multi_accession_data.items())[0]
                            acc_num = data.get("accession_number") or _extract_accession_number(entry)
                            if acc_num == remaining_acc:
                                self.tracker.active_studies[acc_num] = {
                                    "accession": acc_num,
                                    "procedure": data["procedure"],
                                    "study_type": data["study_type"],
                                    "rvu": data["rvu"],
                                    "patient_class": data.get("patient_class", ""),
                                    "start_time": self.multi_accession_start_time or datetime.now(),
                                    "last_seen": datetime.now(),
                                }
                                migrated_back = True
                                logger.info(f"MIGRATED {acc_num} back to single-accession tracking (was only one in multi-mode)")
                            else:
                                # Different accession - record the old one
                                self._record_multi_accession_study(datetime.now())
                        else:
                            # No remaining accession visible or empty multi_accession_data
                            # Record whatever we have
                            self._record_multi_accession_study(datetime.now())
                    
                    # Reset multi-accession state
                    self.multi_accession_mode = False
                    self.multi_accession_data = {}
                    self.multi_accession_start_time = None
                    self.multi_accession_last_procedure = ""
            
            # Update state
            self.current_accession = accession
            # For Mosaic multi-accession (2+ studies), ensure procedure is always set
            if data_source == "Mosaic" and len(multiple_accessions) > 1 and not procedure:
                procedure = "Multiple studies"  # Ensure we have something to display
            self.current_procedure = procedure
            self.current_patient_class = patient_class
            self.current_multiple_accessions = multiple_accessions
            
            # Check if procedure is "n/a" (case-insensitive)
            is_na = procedure and procedure.strip().lower() in ["n/a", "na", "none", ""]
            
            # Determine study type and RVU for display
            # For Mosaic multiple accessions, the values should already be set above
            # Skip if already set for Mosaic multiple accessions
            if mosaic_multiple_mode and hasattr(self, 'current_study_type') and self.current_study_type:
                # Already set above for Mosaic multi-accession - keep it
                pass
            # Skip if already set for duplicate multi-accession
            elif is_multi_accession_view and not self.multi_accession_mode and hasattr(self, 'current_study_type') and self.current_study_type and self.current_study_type.startswith("Multiple"):
                # Already set for duplicate multi-accession - keep it
                pass
            elif self.multi_accession_mode and multiple_accessions:
                # Multi-accession mode display
                collected_count = len(self.multi_accession_data)
                total_count = len(multiple_accessions)
                
                if collected_count < total_count:
                    # Incomplete - show current procedure info but mark as incomplete
                    self.current_study_type = f"incomplete ({collected_count}/{total_count})"
                    self.current_study_rvu = sum(d["rvu"] for d in self.multi_accession_data.values())
                else:
                    # Complete - show "Multiple {modality}"
                    total_rvu = sum(d["rvu"] for d in self.multi_accession_data.values())
                    # Get modality from first study type
                    modalities = set()
                    for d in self.multi_accession_data.values():
                        st = d["study_type"]
                        if st:
                            # Extract modality (first word usually)
                            parts = st.split()
                            if parts:
                                modalities.add(parts[0])
                    modality = list(modalities)[0] if modalities else "Studies"
                    self.current_study_type = f"Multiple {modality}"
                    self.current_study_rvu = total_rvu
            elif procedure and not is_na:
                classification_rules = self.data_manager.data.get("classification_rules", {})
                direct_lookups = self.data_manager.data.get("direct_lookups", {})
                study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                self.current_study_type = study_type
                self.current_study_rvu = rvu
                logger.debug(f"Set current_study_type={study_type}, current_study_rvu={rvu}")
            else:
                self.current_study_type = ""
                self.current_study_rvu = 0.0
            
            self.update_debug_display()
        
            current_time = datetime.now()
            
            # IMPORTANT: If there's a current accession that's NOT yet in active_studies,
            # we need to add it BEFORE handling N/A. This prevents losing studies that were
            # briefly visible before the procedure changed to N/A.
            rvu_table = self.data_manager.data["rvu_table"]
            classification_rules = self.data_manager.data.get("classification_rules", {})
            direct_lookups = self.data_manager.data.get("direct_lookups", {})
            
            if accession and accession not in self.tracker.active_studies:
                # New study detected - add it before any completion logic
                # This ensures we don't lose studies that flash briefly before N/A
                ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicates", True)
                if not self.tracker.should_ignore(accession, ignore_duplicates, self.data_manager):
                    # Get procedure for this study
                    # Priority: current procedure > pending studies cache > current_procedure
                    study_procedure = None
                    pending_patient_class = self.current_patient_class
                    
                    if not is_na and procedure:
                        study_procedure = procedure
                    else:
                        # Check pending studies cache from worker thread
                        with self._ps_lock:
                            if accession in self._pending_studies:
                                pending = self._pending_studies[accession]
                                study_procedure = pending.get('procedure', '')
                                if pending.get('patient_class'):
                                    pending_patient_class = pending.get('patient_class')
                                logger.info(f"Using cached pending study data for {accession}: {study_procedure}")
                    
                    if study_procedure and study_procedure.lower() not in ["n/a", "na", "no report", ""]:
                        # Check if study is already recorded before adding from pending cache
                        if not self.tracker.is_already_recorded(accession, self.data_manager):
                            logger.info(f"Adding study before N/A check: {accession} - {study_procedure}")
                            self.tracker.add_study(accession, study_procedure, current_time, 
                                                 rvu_table, classification_rules, direct_lookups, 
                                                 pending_patient_class)
                            # NOTE: Do NOT call mark_seen here - study is only being TRACKED, not RECORDED
                            # seen_accessions should only contain studies that have been RECORDED to database
                        else:
                            logger.debug(f"Skipping adding {accession} from pending cache - already recorded")
                        # Remove from pending after processing (whether added or skipped)
                        with self._ps_lock:
                            if accession in self._pending_studies:
                                del self._pending_studies[accession]
            
            # If procedure changed to "n/a", complete multi-accession study or all active studies
            if is_na:
                # First, handle multi-accession study completion
                if self.multi_accession_mode and self.multi_accession_data:
                    self._record_multi_accession_study(current_time)
                    
                    # Reset multi-accession tracking
                    self.multi_accession_mode = False
                    self.multi_accession_data = {}
                    self.multi_accession_start_time = None
                    self.multi_accession_last_procedure = ""
                
                # Handle regular single-accession studies when N/A
                if self.tracker.active_studies:
                    logger.info("Procedure changed to N/A - completing all active studies")
                    # Mark all active studies as completed immediately
                    for acc, study in list(self.tracker.active_studies.items()):
                        duration = (current_time - study["start_time"]).total_seconds()
                        if duration >= self.tracker.min_seconds:
                            completed_study = study.copy()
                            completed_study["end_time"] = current_time
                            completed_study["duration"] = duration
                            study_record = {
                                "accession": completed_study["accession"],
                                "procedure": completed_study["procedure"],
                                "patient_class": completed_study.get("patient_class", ""),
                                "study_type": completed_study["study_type"],
                                "rvu": completed_study["rvu"],
                                "time_performed": completed_study["start_time"].isoformat(),
                                "time_finished": completed_study["end_time"].isoformat(),
                                "duration_seconds": completed_study["duration"],
                            }
                            self._record_or_update_study(study_record)
                            self.undo_used = False
                            self.undo_btn.config(state=tk.NORMAL)
                        else:
                            logger.debug(f"Skipping short study: {acc} ({duration:.1f}s < {self.tracker.min_seconds}s)")
                    # Clear all active studies
                    self.tracker.active_studies.clear()
                    self.update_display()
                return  # Return after handling N/A case - don't process normal study tracking
        
            # Skip normal study tracking when viewing a multi-accession study (PowerScribe only)
            # For Mosaic, we track each accession separately, so we need to check completion
            # Also skip if we're viewing a multi-accession that we're ignoring as duplicate (PowerScribe only)
            if (self.multi_accession_mode or is_multi_accession_view) and data_source != "Mosaic":
                return
        
            # Check for completed studies FIRST (before checking if we should ignore)
            # This handles studies that have disappeared
            # For Mosaic multi-accession, we need to check all accessions, not just the current one
            logger.debug(f"Completion check: data_source={data_source}, accession='{accession}', multiple_accessions={multiple_accessions}, active_studies={list(self.tracker.active_studies.keys())}")
            if data_source == "Mosaic":
                if multiple_accessions:
                    # For Mosaic multi-accession, check completion for all accessions
                    # Extract all accession numbers from multiple_accessions
                    all_current_accessions = set()
                    for acc_entry in multiple_accessions:
                        if '(' in acc_entry and ')' in acc_entry:
                            # Format: "ACC (PROC)"
                            acc_match = re.match(r'^([^(]+)', acc_entry)
                            if acc_match:
                                all_current_accessions.add(acc_match.group(1).strip())
                        else:
                            all_current_accessions.add(acc_entry)
                    
                    # Update last_seen for all currently visible Mosaic accessions
                    for acc in all_current_accessions:
                        if acc in self.tracker.active_studies:
                            self.tracker.active_studies[acc]["last_seen"] = current_time
                    
                    # Check completion - any active Mosaic study not in the current accessions list should be completed
                    completed = []
                    for acc, study in list(self.tracker.active_studies.items()):
                        # Only check Mosaic studies (patient_class == "Unknown")
                        if study.get('patient_class') == 'Unknown' and acc not in all_current_accessions:
                            # This accession is no longer visible - mark as completed immediately
                            # Use current_time as end_time since study just disappeared
                            duration = (current_time - study["start_time"]).total_seconds()
                            if duration >= self.tracker.min_seconds:
                                completed_study = study.copy()
                                completed_study["end_time"] = current_time
                                completed_study["duration"] = duration
                                completed.append(completed_study)
                                logger.info(f"Completed Mosaic study: {acc} - {study['study_type']} ({duration:.1f}s)")
                                # Remove from active studies
                                del self.tracker.active_studies[acc]
                elif not accession:
                    # Mosaic but no multiple_accessions and no accession - all active Mosaic studies should be completed
                    # NOTE: Don't filter by patient_class == 'Unknown' because Clario may have updated it
                    # Instead, complete ALL active studies when no accession is visible (they must have closed)
                    completed = []
                    for acc, study in list(self.tracker.active_studies.items()):
                        # No accessions visible - complete immediately
                        # Use current_time as end_time since study just disappeared
                        duration = (current_time - study["start_time"]).total_seconds()
                        if duration >= self.tracker.min_seconds:
                            completed_study = study.copy()
                            completed_study["end_time"] = current_time
                            completed_study["duration"] = duration
                            completed.append(completed_study)
                            logger.info(f"Completed Mosaic study (no accessions visible): {acc} - {study['study_type']} ({duration:.1f}s)")
                            # Remove from active studies
                            del self.tracker.active_studies[acc]
                        else:
                            logger.debug(f"Skipping short Mosaic study: {acc} ({duration:.1f}s < {self.tracker.min_seconds}s)")
                    
                    # Only log the check message if we actually have studies to check
                    if self.tracker.active_studies:
                        logger.debug(f"Mosaic: no accession visible - checking {len(self.tracker.active_studies)} active studies for completion")
                else:
                    # Single Mosaic accession - use normal completion check
                    logger.debug(f"Calling check_completed for single Mosaic: accession='{accession}'")
                    completed = self.tracker.check_completed(current_time, accession)
            else:
                # Normal completion check (PowerScribe or single Mosaic accession)
                logger.debug(f"Calling check_completed for PowerScribe/single: accession='{accession}'")
                completed = self.tracker.check_completed(current_time, accession)
            
            # Only log if we actually found completed studies
            if completed:
                logger.info(f"Processing {len(completed)} completed studies from check_completed")
            else:
                logger.debug(f"Processing {len(completed)} completed studies from check_completed")
            for study in completed:
                logger.info(f"Recording completed study: {study['accession']} - {study.get('study_type', 'Unknown')}")
                study_record = {
                    "accession": study["accession"],
                    "procedure": study["procedure"],
                    "patient_class": study.get("patient_class", ""),
                    "study_type": study["study_type"],
                    "rvu": study["rvu"],
                    "time_performed": study["start_time"].isoformat(),  # Time study was started
                    "time_finished": study["end_time"].isoformat(),    # Time study was finished
                    "duration_seconds": study["duration"],              # Time taken to finish
                }
                self._record_or_update_study(study_record)
                # Reset undo button when new study is added or updated
                self.undo_used = False
                self.undo_btn.config(state=tk.NORMAL)
                self.update_display()
            
            # Now handle current study
            if not accession:
                # No current study - check if we have active Mosaic studies that should be completed
                if data_source == "Mosaic" and self.tracker.active_studies:
                    # All active Mosaic studies should be completed since no study is visible
                    current_time_check = datetime.now()
                    for acc, study in list(self.tracker.active_studies.items()):
                        # Only complete Mosaic studies (patient_class == "Unknown")
                        if study.get('patient_class') == 'Unknown':
                            # No accession visible - complete immediately
                            # Use current_time as end_time since study just disappeared
                            duration = (current_time_check - study["start_time"]).total_seconds()
                            if duration >= self.tracker.min_seconds:
                                completed_study = study.copy()
                                completed_study["end_time"] = current_time_check
                                completed_study["duration"] = duration
                                study_record = {
                                    "accession": completed_study["accession"],
                                    "procedure": completed_study["procedure"],
                                    "patient_class": completed_study.get("patient_class", ""),
                                    "study_type": completed_study["study_type"],
                                    "rvu": completed_study["rvu"],
                                    "time_performed": completed_study["start_time"].isoformat(),
                                    "time_finished": completed_study["end_time"].isoformat(),
                                    "duration_seconds": completed_study["duration"],
                                }
                                self._record_or_update_study(study_record)
                                self.undo_used = False
                                self.undo_btn.config(state=tk.NORMAL)
                                del self.tracker.active_studies[acc]
                    if self.tracker.active_studies:
                        self.update_display()
                # No current study - all active studies should be checked for completion
                # This is already handled above, so just return
                return
            
            # Check if should ignore (only ignore if already completed in this shift)
            ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
            
            # Check if this accession should be ignored (already completed or part of multi-accession)
            if self.tracker.should_ignore(accession, ignore_duplicates, self.data_manager):
                logger.debug(f"Skipping tracking of accession {accession} - already recorded")
                return
            
            # Get classification rules, direct lookups, and RVU table for matching
            classification_rules = self.data_manager.data.get("classification_rules", {})
            direct_lookups = self.data_manager.data.get("direct_lookups", {})
            rvu_table = self.data_manager.data["rvu_table"]
            
            # If study is already active, update it (don't ignore)
            # For Mosaic multi-accession, we handle last_seen updates above, so skip here
            if accession in self.tracker.active_studies:
                if data_source != "Mosaic" or not multiple_accessions:
                    # Normal update for PowerScribe or single Mosaic accession
                    # Update with current patient class (may be from Clario cache)
                    self.tracker.add_study(accession, procedure, current_time, rvu_table, classification_rules, direct_lookups, self.current_patient_class)
                    logger.debug(f"Updated existing study: {accession} with patient_class: {self.current_patient_class}")
                # For Mosaic multi-accession, last_seen is updated above, so just return
                # BUT: Don't return here - we still need to check for completion of OTHER studies
                # The completion check happens above, so we can return now
                return
            
            # Allow study to be tracked again even if previously seen (as long as it wasn't part of multi-accession)
            # When it completes, _record_or_update_study will update existing record with maximum duration
            # NOTE: Do NOT add to seen_accessions here - that should only happen when study is RECORDED
            # Adding it here would cause "already recorded" to show for NEW studies that haven't been recorded yet
            
            # Add or update study tracking (allows reopening of previously seen studies)
            self.tracker.add_study(accession, procedure, current_time, rvu_table, classification_rules, direct_lookups, self.current_patient_class)
            
            if self.is_running:
                self.root.title("RVU Counter - Running")
            
        except Exception as e:
            logger.error(f"Error refreshing data: {e}", exc_info=True)
            self.root.title(f"RVU Counter - Error: {str(e)[:30]}")
            # Clear debug on error
            self.current_accession = ""
            self.current_procedure = ""
            self.current_patient_class = ""
            self.current_study_type = ""
            self.update_debug_display()
    
    def update_shift_start_label(self):
        """Update the shift start time label."""
        if self.shift_start:
            # Format: "Started: HH:MM am/pm"
            time_str = self.shift_start.strftime("%I:%M %p").lower()
            self.shift_start_label.config(text=f"Started: {time_str}")
        else:
            self.shift_start_label.config(text="")
    
    def start_shift(self):
        """Start a new shift."""
        if self.is_running:
            # Stop current shift - archive it immediately
            self.is_running = False
            
            # Determine shift end time: use last study time if there's been a significant gap
            current_time = datetime.now()
            records = self.data_manager.data["current_shift"].get("records", [])
            
            shift_end_time = current_time  # Default to current time
            
            if records:
                # Find the most recent study's time_finished
                try:
                    last_study_times = []
                    for r in records:
                        if r.get("time_finished"):
                            last_study_times.append(datetime.fromisoformat(r["time_finished"]))
                    
                    if last_study_times:
                        last_study_time = max(last_study_times)
                        time_since_last_study = (current_time - last_study_time).total_seconds() / 60  # minutes
                        
                        # If last study was more than 30 minutes ago, use that as shift end
                        if time_since_last_study > 30:
                            shift_end_time = last_study_time
                            logger.info(f"Using last study time as shift end ({time_since_last_study:.1f} min gap): {last_study_time}")
                        else:
                            logger.info(f"Using current time as shift end (last study {time_since_last_study:.1f} min ago)")
                except Exception as e:
                    logger.error(f"Error determining shift end time: {e}")
                    # Fall back to current time
            
            self.data_manager.data["current_shift"]["shift_end"] = shift_end_time.isoformat()
            # Archive the shift to historical shifts
            self.data_manager.end_current_shift()
            # Clear current_shift completely so new studies are truly temporary
            self.data_manager.data["current_shift"]["shift_start"] = None
            self.data_manager.data["current_shift"]["shift_end"] = None
            self.data_manager.data["current_shift"]["records"] = []
            
            self.start_btn.config(text="Start Shift")
            self.root.title("RVU Counter - Stopped")
            self.shift_start = None
            self.effective_shift_start = None
            self.projected_shift_end = None
            self.update_shift_start_label()
            self.update_recent_studies_label()
            # Clear the recent studies display (data is preserved in archived shift)
            for widget in self.study_widgets:
                widget.destroy()
            self.study_widgets.clear()
            self.data_manager.save()
            logger.info("Shift stopped and archived")
            
            # Trigger cloud backup on shift end (if enabled)
            self._perform_shift_end_backup()
            
            # Recalculate typical shift times now that we have new data
            self._calculate_typical_shift_times()
            # Hide pace car when no shift is active
            self.pace_car_frame.pack_forget()
            # Update counters to zero but don't rebuild recent studies list
            self._update_counters_only()
        else:
            # Check for temporary studies (studies recorded without an active shift)
            temp_records = self.data_manager.data["current_shift"].get("records", [])
            has_no_shift = not self.data_manager.data["current_shift"].get("shift_start")
            
            keep_temp_records = False
            if temp_records and has_no_shift:
                # Ask user what to do with temporary studies
                study_count = len(temp_records)
                total_rvu = sum(r.get("rvu", 0) for r in temp_records)
                
                # Create custom dialog with Yes/No/Cancel
                result = messagebox.askyesnocancel(
                    "Temporary Studies Found",
                    f"You have {study_count} temporary studies ({total_rvu:.1f} RVU) recorded without a shift.\n\n"
                    "Would you like to add them to the new shift?\n\n"
                    "• Yes - Add studies to the new shift\n"
                    "• No - Discard temporary studies\n"
                    "• Cancel - Don't start shift",
                    parent=self.root
                )
                
                if result is None:
                    # Cancel - abort, don't start shift
                    logger.info("Shift start cancelled by user")
                    return
                elif result:
                    # Yes - keep the records
                    keep_temp_records = True
                    logger.info(f"User chose to add {study_count} temporary studies to new shift")
                else:
                    # No - discard records
                    keep_temp_records = False
                    logger.info(f"User chose to discard {study_count} temporary studies")
            
            # End previous shift if it exists (shouldn't happen if has_no_shift is True)
            if self.data_manager.data["current_shift"].get("shift_start"):
                self.data_manager.end_current_shift()
            
            # Start new shift
            self.shift_start = datetime.now()
            
            # Calculate effective shift start (rounded to hour if within 15 min)
            minutes_into_hour = self.shift_start.minute
            if minutes_into_hour <= 15:
                # Round down to the hour
                self.effective_shift_start = self.shift_start.replace(minute=0, second=0, microsecond=0)
            else:
                # Use actual start time
                self.effective_shift_start = self.shift_start
            
            # Calculate projected shift end based on shift length setting
            shift_length = self.data_manager.data["settings"].get("shift_length_hours", 9)
            self.projected_shift_end = self.effective_shift_start + timedelta(hours=shift_length)
            
            self.data_manager.data["current_shift"]["shift_start"] = self.shift_start.isoformat()
            self.data_manager.data["current_shift"]["effective_shift_start"] = self.effective_shift_start.isoformat()
            self.data_manager.data["current_shift"]["projected_shift_end"] = self.projected_shift_end.isoformat()
            self.data_manager.data["current_shift"]["shift_end"] = None
            
            # Handle temporary records based on user choice
            if keep_temp_records:
                # Keep existing records, mark their accessions as seen
                for record in temp_records:
                    self.tracker.seen_accessions.add(record.get("accession", ""))
            else:
                # Clear records
                self.data_manager.data["current_shift"]["records"] = []
            
            self.tracker = StudyTracker(
                min_seconds=self.data_manager.data["settings"]["min_study_seconds"]
            )
            if keep_temp_records:
                # Restore seen accessions after tracker recreation
                for record in temp_records:
                    self.tracker.seen_accessions.add(record.get("accession", ""))
            else:
                self.tracker.seen_accessions.clear()
            
            self.is_running = True
            self.start_btn.config(text="Stop Shift")
            self.root.title("RVU Counter - Running")
            # Force widget rebuild by setting last_record_count to -1 (different from 0)
            self.last_record_count = -1
            self.update_shift_start_label()
            self.update_recent_studies_label()
            # Show pace car if enabled in settings
            if self.data_manager.data["settings"].get("show_pace_car", False):
                self.pace_car_frame.pack(fill=tk.X, pady=(0, 2), after=self.counters_frame)
            self.data_manager.save()
            logger.info(f"Shift started at {self.shift_start}")
            self.update_display()
    
    def undo_last(self):
        """Undo the last completed study (only works once per study)."""
        records = self.data_manager.data["current_shift"]["records"]
        if records and not self.undo_used:
            removed = records.pop()
            self.data_manager.save()
            self.undo_used = True
            self.undo_btn.config(state=tk.DISABLED)
            logger.info(f"Undid study: {removed['accession']}")
            self.update_display()
    
    def delete_study_by_index(self, index: int):
        """Delete study by index from records."""
        try:
            logger.info(f"delete_study_by_index called with index: {index}")
            records = self.data_manager.data["current_shift"]["records"]
            logger.info(f"Current records count: {len(records)}")
            
            if 0 <= index < len(records):
                removed = records[index]
                accession = removed.get('accession', '')
                logger.info(f"Attempting to delete study: {accession} at index {index}")
                
                # Delete from database first
                deleted_from_db = False
                if 'id' in removed and removed['id']:
                    try:
                        self.data_manager.db.delete_record(removed['id'])
                        deleted_from_db = True
                        logger.info(f"Deleted study from database: {accession} (ID: {removed['id']})")
                    except Exception as e:
                        logger.error(f"Error deleting study from database: {e}", exc_info=True)
                else:
                    # Record doesn't have ID yet - delete by accession if we have a current shift
                    logger.info(f"Record has no ID, trying to find by accession: {accession}")
                    current_shift = self.data_manager.db.get_current_shift()
                    if current_shift:
                        try:
                            db_record = self.data_manager.db.find_record_by_accession(
                                current_shift['id'], accession
                            )
                            if db_record:
                                self.data_manager.db.delete_record(db_record['id'])
                                deleted_from_db = True
                                logger.info(f"Deleted study from database by accession: {accession} (ID: {db_record['id']})")
                            else:
                                logger.warning(f"Could not find record in database for accession: {accession}, will delete from memory only")
                        except Exception as e:
                            logger.error(f"Error deleting study from database by accession: {e}", exc_info=True)
                    else:
                        logger.warning("No current shift found in database, will delete from memory only")
                
                # Remove from memory
                records.pop(index)
                logger.info(f"Removed study from memory: {accession}, remaining records: {len(records)}")
                
                # Remove from seen_accessions to allow retracking if reopened
                if accession and accession in self.tracker.seen_accessions:
                    self.tracker.seen_accessions.remove(accession)
                    logger.info(f"Removed {accession} from seen_accessions - can be tracked again if reopened")
                
                # Remove from active_studies if currently being tracked
                if accession and accession in self.tracker.active_studies:
                    del self.tracker.active_studies[accession]
                    logger.info(f"Removed {accession} from active_studies tracking")
                
                # Remove from pending_studies cache
                with self._ps_lock:
                    if accession and accession in self._pending_studies:
                        del self._pending_studies[accession]
                        logger.info(f"Removed {accession} from pending_studies cache")
                
                # Save to sync memory changes to database
                self.data_manager.save()
                logger.info(f"Saved changes after deletion")
                
                # Reload data from database to ensure consistency between DB and memory
                if deleted_from_db:
                    try:
                        # Reload records from database
                        self.data_manager.records_data = self.data_manager._load_records_from_db()
                        # Update current_shift in main data structure
                        self.data_manager.data["current_shift"] = self.data_manager.records_data.get("current_shift", {
                            "shift_start": None,
                            "shift_end": None,
                            "records": []
                        })
                        self.data_manager.data["shifts"] = self.data_manager.records_data.get("shifts", [])
                        logger.info(f"Reloaded data from DB, current records count: {len(self.data_manager.data['current_shift']['records'])}")
                    except Exception as e:
                        logger.error(f"Error reloading data from database: {e}", exc_info=True)
                
                # Manually destroy all study widgets immediately
                for widget in list(self.study_widgets):
                    try:
                        widget.destroy()
                    except:
                        pass
                self.study_widgets.clear()
                
                # Also clear ALL children from scrollable frame directly
                for child in list(self.studies_scrollable_frame.winfo_children()):
                    try:
                        child.destroy()
                    except:
                        pass
                
                # Clear time labels too
                if hasattr(self, 'time_labels'):
                    self.time_labels.clear()
                
                logger.info("Manually destroyed all study widgets and cleared scrollable frame")
                
                # Force immediate UI refresh before rebuilding
                self.root.update_idletasks()
                self.root.update()
                
                # Force a rebuild of the recent studies list
                self.last_record_count = -1
                self.update_display()
                
                # Force multiple UI updates to ensure refresh
                self.root.update_idletasks()
                self.root.update()
                logger.info(f"UI updated after deletion, new record count: {len(self.data_manager.data['current_shift']['records'])}")
            else:
                logger.warning(f"Invalid index for deletion: {index} (records count: {len(records)})")
        except Exception as e:
            logger.error(f"Error in delete_study_by_index: {e}", exc_info=True)
    
    def _get_hour_key(self, dt: datetime) -> str:
        """Convert datetime hour to rate lookup key like '2am', '12pm'."""
        hour = dt.hour
        if hour == 0:
            return "12am"
        elif hour < 12:
            return f"{hour}am"
        elif hour == 12:
            return "12pm"
        else:
            return f"{hour - 12}pm"
    
    def _is_weekend(self, dt: datetime) -> bool:
        """Check if date is weekend (Saturday=5, Sunday=6)."""
        return dt.weekday() >= 5
    
    def _get_compensation_rate(self, dt: datetime) -> float:
        """Get compensation rate per RVU for a given datetime."""
        rates = self.data_manager.data.get("compensation_rates", {})
        if not rates:
            logger.warning("No compensation_rates found in data")
            return 0.0
        
        role = self.data_manager.data["settings"].get("role", "Partner").lower()
        # Map role to key in rates
        role_key = "partner" if role == "partner" else "assoc"
        day_type = "weekend" if self._is_weekend(dt) else "weekday"
        hour_key = self._get_hour_key(dt)
        
        try:
            rate = rates[day_type][role_key][hour_key]
            return rate
        except KeyError as e:
            logger.warning(f"KeyError getting rate: {e} - keys: {day_type}/{role_key}/{hour_key}")
            return 0.0
    
    def _calculate_study_compensation(self, record: dict) -> float:
        """Calculate compensation for a single study based on when it was finished."""
        try:
            time_finished = datetime.fromisoformat(record["time_finished"])
            rate = self._get_compensation_rate(time_finished)
            return record["rvu"] * rate
        except (KeyError, ValueError):
            return 0.0
    
    def _calculate_projected_compensation(self, start_time: datetime, end_time: datetime, rvu_rate_per_hour: float) -> float:
        """Calculate projected compensation for remaining shift hours considering hourly rate changes."""
        total_comp = 0.0
        current = start_time
        
        while current < end_time:
            # Calculate how much of this hour is within our range
            hour_start = current.replace(minute=0, second=0, microsecond=0)
            hour_end = hour_start + timedelta(hours=1)
            
            # Clip to our actual range
            effective_start = max(current, hour_start)
            effective_end = min(end_time, hour_end)
            
            # Calculate fraction of hour
            fraction_of_hour = (effective_end - effective_start).total_seconds() / 3600
            
            if fraction_of_hour > 0:
                # Get the rate for this hour
                rate = self._get_compensation_rate(hour_start)
                
                # Calculate RVU for this fraction of hour
                rvu_this_period = rvu_rate_per_hour * fraction_of_hour
                
                # Calculate compensation
                comp_this_period = rvu_this_period * rate
                total_comp += comp_this_period
            
            # Move to next hour
            current = hour_end
        
        return total_comp
    
    def calculate_stats(self) -> dict:
        """Calculate statistics."""
        if not self.shift_start:
            return {
                "total": 0.0,
                "avg_per_hour": 0.0,
                "last_hour": 0.0,
                "last_full_hour": 0.0,
                "last_full_hour_range": "",
                "projected": 0.0,
                "projected_shift": 0.0,
                "comp_total": 0.0,
                "comp_avg": 0.0,
                "comp_last_hour": 0.0,
                "comp_last_full_hour": 0.0,
                "comp_projected": 0.0,
                "comp_projected_shift": 0.0,
            }
        
        records = self.data_manager.data["current_shift"]["records"]
        current_time = datetime.now()
        
        # Total RVU and compensation
        total_rvu = sum(r["rvu"] for r in records)
        total_comp = sum(self._calculate_study_compensation(r) for r in records)
        
        # Average per hour
        hours_elapsed = (current_time - self.shift_start).total_seconds() / 3600
        avg_per_hour = total_rvu / hours_elapsed if hours_elapsed > 0 else 0.0
        avg_comp_per_hour = total_comp / hours_elapsed if hours_elapsed > 0 else 0.0
        
        # Last hour - filter records and calculate both RVU and compensation
        one_hour_ago = current_time - timedelta(hours=1)
        last_hour_records = [r for r in records if datetime.fromisoformat(r["time_finished"]) >= one_hour_ago]
        last_hour_rvu = sum(r["rvu"] for r in last_hour_records)
        last_hour_comp = sum(self._calculate_study_compensation(r) for r in last_hour_records)
        
        # Last full hour (e.g., 2am to 3am)
        current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
        last_full_hour_start = current_hour_start - timedelta(hours=1)
        last_full_hour_end = current_hour_start
        
        last_full_hour_records = [r for r in records 
                                   if last_full_hour_start <= datetime.fromisoformat(r["time_finished"]) < last_full_hour_end]
        last_full_hour_rvu = sum(r["rvu"] for r in last_full_hour_records)
        last_full_hour_comp = sum(self._calculate_study_compensation(r) for r in last_full_hour_records)
        last_full_hour_range = f"{self._format_hour_label(last_full_hour_start)}-{self._format_hour_label(last_full_hour_end)}"
        
        # Projected for current hour - use current hour's rate for projection
        current_hour_records = [r for r in records if datetime.fromisoformat(r["time_finished"]) >= current_hour_start]
        current_hour_rvu = sum(r["rvu"] for r in current_hour_records)
        current_hour_comp = sum(self._calculate_study_compensation(r) for r in current_hour_records)
        
        minutes_into_hour = (current_time - current_hour_start).total_seconds() / 60
        if minutes_into_hour > 0:
            projected = (current_hour_rvu / minutes_into_hour) * 60
            projected_comp = (current_hour_comp / minutes_into_hour) * 60
        else:
            projected = 0.0
            projected_comp = 0.0
        
        # Projected shift total - extrapolate based on RVU rate and remaining time
        projected_shift_rvu = total_rvu
        projected_shift_comp = total_comp
        
        if self.effective_shift_start and self.projected_shift_end:
            # Calculate time remaining in shift
            time_remaining = (self.projected_shift_end - current_time).total_seconds()
            
            if time_remaining > 0 and hours_elapsed > 0:
                # Calculate RVU rate per hour
                rvu_rate_per_hour = avg_per_hour
                hours_remaining = time_remaining / 3600
                
                # Project additional RVU for remaining time
                projected_additional_rvu = rvu_rate_per_hour * hours_remaining
                projected_shift_rvu = total_rvu + projected_additional_rvu
                
                # Calculate projected compensation for remaining hours
                # Consider hourly rate changes throughout remaining shift
                projected_additional_comp = self._calculate_projected_compensation(
                    current_time, 
                    self.projected_shift_end, 
                    rvu_rate_per_hour
                )
                projected_shift_comp = total_comp + projected_additional_comp
        
        return {
            "total": total_rvu,
            "avg_per_hour": avg_per_hour,
            "last_hour": last_hour_rvu,
            "last_full_hour": last_full_hour_rvu,
            "last_full_hour_range": last_full_hour_range,
            "projected": projected,
            "projected_shift": projected_shift_rvu,
            "comp_total": total_comp,
            "comp_avg": avg_comp_per_hour,
            "comp_last_hour": last_hour_comp,
            "comp_last_full_hour": last_full_hour_comp,
            "comp_projected": projected_comp,
            "comp_projected_shift": projected_shift_comp,
        }
    
    def create_tooltip(self, widget, text):
        """Create a tooltip for a widget."""
        def on_enter(event):
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
            label = ttk.Label(tooltip, text=text, background="#ffffe0", relief=tk.SOLID, borderwidth=1, font=("Consolas", 8))
            label.pack()
            widget.tooltip = tooltip
        
        def on_leave(event):
            if hasattr(widget, 'tooltip'):
                widget.tooltip.destroy()
                del widget.tooltip
        
        widget.bind('<Enter>', on_enter)
        widget.bind('<Leave>', on_leave)
    
    def update_recent_studies_label(self):
        """Update the Recent Studies label based on shift status."""
        # Safety check: ensure recent_frame exists (might not be created yet during UI initialization)
        if not hasattr(self, 'recent_frame'):
            return
        
        # Check both in-memory state and data file to ensure consistency
        current_shift = self.data_manager.data.get("current_shift", {})
        shift_start_str = current_shift.get("shift_start")
        shift_end_str = current_shift.get("shift_end")
        
        # Determine if we're in an active shift:
        # 1. In-memory state says we're running AND have a shift_start, OR
        # 2. Data file has shift_start but no shift_end (active shift)
        is_active_shift = (self.is_running and self.shift_start) or (shift_start_str and not shift_end_str)
        
        # Get default foreground color from theme
        default_fg = self.theme_colors.get("fg", "black") if hasattr(self, 'theme_colors') else "black"
        
        if is_active_shift:
            # Count recent studies
            recent_count = len(current_shift.get("records", []))
            # Normal text color
            self.recent_frame.config(text=f"Recent Studies ({recent_count})", fg=default_fg)
        else:
            # Red text to indicate no active shift
            self.recent_frame.config(text="Temporary Recent - No shift started", fg="red")
    
    def _update_counters_only(self):
        """Update just the counter displays to zero (used when shift ends)."""
        self.update_recent_studies_label()
        settings = self.data_manager.data["settings"]
        
        # Set all counters to 0
        if settings.get("show_total", True):
            self.total_label.config(text="0.0")
            self.total_comp_label.config(text="")
        if settings.get("show_avg", True):
            self.avg_label.config(text="0.0")
            self.avg_comp_label.config(text="")
        if settings.get("show_last_hour", True):
            self.last_hour_label.config(text="0.0")
            self.last_hour_comp_label.config(text="")
        if settings.get("show_last_full_hour", True):
            self.last_full_hour_label.config(text="0.0")
            self.last_full_hour_range_label.config(text="")
            self.last_full_hour_label_text.config(text="hour:")
            self.last_full_hour_comp_label.config(text="")
        if settings.get("show_projected", True):
            self.projected_label.config(text="0.0")
            self.projected_comp_label.config(text="")
        if settings.get("show_projected_shift", True):
            self.projected_shift_label.config(text="0.0")
            self.projected_shift_comp_label.config(text="")
    
    def update_display(self):
        """Update the display with current statistics."""
        # Update recent studies label based on shift status
        self.update_recent_studies_label()
        
        # Only rebuild widgets if record count changed or if last_record_count is -1 (forced rebuild)
        current_count = len(self.data_manager.data["current_shift"]["records"])
        rebuild_widgets = (current_count != self.last_record_count) or (self.last_record_count == -1)
        if self.last_record_count != -1:  # Only update if not forcing rebuild
            self.last_record_count = current_count
        else:
            self.last_record_count = current_count  # Reset after forced rebuild
        
        stats = self.calculate_stats()
        settings = self.data_manager.data["settings"]
        
        if settings.get("show_total", True):
            self.total_label_text.grid()
            self.total_value_frame.grid()
            self.total_label.config(text=f"{stats['total']:.1f}")
            if settings.get("show_comp_total", False):
                self.total_comp_label.config(text=f"(${stats['comp_total']:,.0f})")
            else:
                self.total_comp_label.config(text="")
        else:
            self.total_label_text.grid_remove()
            self.total_value_frame.grid_remove()
        
        if settings.get("show_avg", True):
            self.avg_label_text.grid()
            self.avg_value_frame.grid()
            self.avg_label.config(text=f"{stats['avg_per_hour']:.1f}")
            if settings.get("show_comp_avg", False):
                self.avg_comp_label.config(text=f"(${stats['comp_avg']:,.0f})")
            else:
                self.avg_comp_label.config(text="")
        else:
            self.avg_label_text.grid_remove()
            self.avg_value_frame.grid_remove()
        
        if settings.get("show_last_hour", True):
            self.last_hour_label_text.grid()
            self.last_hour_value_frame.grid()
            self.last_hour_label.config(text=f"{stats['last_hour']:.1f}")
            if settings.get("show_comp_last_hour", False):
                self.last_hour_comp_label.config(text=f"(${stats['comp_last_hour']:,.0f})")
            else:
                self.last_hour_comp_label.config(text="")
        else:
            self.last_hour_label_text.grid_remove()
            self.last_hour_value_frame.grid_remove()
        
        if settings.get("show_last_full_hour", True):
            self.last_full_hour_label_frame.grid()
            self.last_full_hour_value_frame.grid()
            self.last_full_hour_label.config(text=f"{stats['last_full_hour']:.1f}")
            range_text = stats.get("last_full_hour_range", "")
            if range_text:
                self.last_full_hour_range_label.config(text=range_text)
                self.last_full_hour_label_text.config(text="hour:")
            else:
                self.last_full_hour_range_label.config(text="")
                self.last_full_hour_label_text.config(text="hour:")
            if settings.get("show_comp_last_full_hour", False):
                self.last_full_hour_comp_label.config(text=f"(${stats['comp_last_full_hour']:,.0f})")
            else:
                self.last_full_hour_comp_label.config(text="")
        else:
            self.last_full_hour_label_frame.grid_remove()
            self.last_full_hour_value_frame.grid_remove()
        
        if settings.get("show_projected", True):
            self.projected_label_text.grid()
            self.projected_value_frame.grid()
            self.projected_label.config(text=f"{stats['projected']:.1f}")
            if settings.get("show_comp_projected", False):
                self.projected_comp_label.config(text=f"(${stats['comp_projected']:,.0f})")
            else:
                self.projected_comp_label.config(text="")
        else:
            self.projected_label_text.grid_remove()
            self.projected_value_frame.grid_remove()
        
        if settings.get("show_projected_shift", True):
            self.projected_shift_label_text.grid()
            self.projected_shift_value_frame.grid()
            self.projected_shift_label.config(text=f"{stats['projected_shift']:.1f}")
            if settings.get("show_comp_projected_shift", False):
                self.projected_shift_comp_label.config(text=f"(${stats['comp_projected_shift']:,.0f})")
            else:
                self.projected_shift_comp_label.config(text="")
        else:
            self.projected_shift_label_text.grid_remove()
            self.projected_shift_value_frame.grid_remove()
        
        # Only rebuild widgets if records changed
        if rebuild_widgets:
            # Update recent studies list with X buttons
            # Clear existing widgets from list
            for widget in list(self.study_widgets):
                try:
                    widget.destroy()
                except:
                    pass
            self.study_widgets.clear()
            # Clear time labels if they exist
            if hasattr(self, 'time_labels'):
                self.time_labels.clear()
            
            # Also clear ALL children from scrollable frame directly to ensure clean slate
            for child in list(self.studies_scrollable_frame.winfo_children()):
                try:
                    child.destroy()
                except:
                    pass
            
            # Calculate how many studies can fit based on canvas height
            canvas_height = self.studies_canvas.winfo_height()
            row_height = 18  # Approximate height per study row
            max_studies = max(3, canvas_height // row_height)  # At least 3
            records = self.data_manager.data["current_shift"]["records"][-max_studies:]
            # Display in reverse order (most recent first)
            for i, record in enumerate(reversed(records)):
                # Calculate actual index in full records list
                actual_index = len(self.data_manager.data["current_shift"]["records"]) - 1 - i
                
                # Create frame for this study (vertical container)
                # Reduce vertical padding when show_time is enabled for tighter spacing
                show_time = self.data_manager.data["settings"].get("show_time", False)
                study_pady = 0 if show_time else 1
                study_frame = ttk.Frame(self.studies_scrollable_frame)
                study_frame.pack(fill=tk.X, pady=study_pady, padx=0)  # No horizontal padding
                
                # Main row frame (horizontal) - contains delete button, procedure, and RVU
                main_row_frame = ttk.Frame(study_frame)
                main_row_frame.pack(fill=tk.X, pady=0, padx=0)
                
                # X button to delete (on the left) - use Label for precise size control
                colors = self.theme_colors
                delete_btn = tk.Label(
                    main_row_frame, 
                    text="×", 
                    font=("Arial", 7),
                    bg=colors["delete_btn_bg"],
                    fg=colors["delete_btn_fg"],
                    cursor="hand2",
                    padx=0,
                    pady=0,
                    width=1,
                    anchor=tk.CENTER
                )
                # Store the actual_index in the button itself to avoid closure issues
                delete_btn.actual_index = actual_index
                # Use a closure to capture the index value
                delete_btn.bind("<Button-1>", lambda e, idx=actual_index: self.delete_study_by_index(idx))
                delete_btn.bind("<Enter>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_hover"]))
                delete_btn.bind("<Leave>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_bg"]))
                delete_btn.pack(side=tk.LEFT, padx=(1, 3), pady=0)
                
                # Study text label (show actual procedure name, or "Multiple XR" for multi-accession)
                is_multi = record.get('is_multi_accession', False)
                if is_multi:
                    # Multi-accession study - show "Multiple {modality}"
                    procedure_name = record.get('study_type', 'Multiple Studies')
                    study_type = procedure_name
                else:
                    procedure_name = record.get('procedure', record.get('study_type', 'Unknown'))
                    study_type = record.get('study_type', 'Unknown')
                
                # Check if study starts with CT, MR, US, XR, NM, or Multiple (case-insensitive)
                procedure_upper = procedure_name.upper().strip()
                study_type_upper = study_type.upper().strip()
                valid_prefixes = ['CT', 'MR', 'US', 'XR', 'NM', 'MULTIPLE']
                starts_with_valid = any(procedure_upper.startswith(prefix) or study_type_upper.startswith(prefix) for prefix in valid_prefixes)
                
                # Dynamic truncation based on window width
                frame_width = self.root.winfo_width()
                max_chars = self._calculate_max_chars(frame_width)
                display_name = self._truncate_text(procedure_name, max_chars)
                
                # Procedure label - left-aligned
                procedure_label = ttk.Label(main_row_frame, text=display_name, font=("Consolas", 8))
                if not starts_with_valid:
                    procedure_label.config(foreground="#8B0000")  # Dark red
                procedure_label.pack(side=tk.LEFT)
                
                # RVU label - stays on the far right
                rvu_text = f"{record['rvu']:.1f} RVU"
                rvu_label = ttk.Label(main_row_frame, text=rvu_text, font=("Consolas", 8))
                if not starts_with_valid:
                    rvu_label.config(foreground="#8B0000")  # Dark red
                rvu_label.pack(side=tk.RIGHT)
                
                # Time information row (if show_time is enabled) - appears BELOW the main row, tightly spaced
                show_time = self.data_manager.data["settings"].get("show_time", False)
                if show_time:
                    # Use regular tk.Frame with minimal height to reduce spacing
                    # Use canvas_bg to match the studies scrollable area background
                    bg_color = self.theme_colors.get("canvas_bg", self.theme_colors.get("bg", "#f0f0f0"))
                    time_row_frame = tk.Frame(study_frame, bg=bg_color, height=12)
                    time_row_frame.pack(fill=tk.X, pady=(0, 0), padx=0)
                    time_row_frame.pack_propagate(False)  # Prevent frame from expanding
                    
                    # Add a small spacer on the left to align with procedure text (accounting for X button width)
                    spacer_label = tk.Label(time_row_frame, text="", width=2, bg=bg_color, height=1)  # Approximate width of X button + padding
                    spacer_label.pack(side=tk.LEFT, pady=0, padx=0)
                    
                    # Time ago label - left-justified, smaller font, lighter color, no padding
                    # Use tk.Label instead of ttk.Label for less padding
                    time_ago_text = self._format_time_ago(record.get("time_finished"))
                    # Use theme color for secondary text
                    text_color = self.theme_colors.get("text_secondary", "gray")
                    time_ago_label = tk.Label(
                        time_row_frame,
                        text=time_ago_text, 
                        font=("Consolas", 7),
                        fg=text_color,
                        bg=bg_color,
                        padx=0,
                        pady=0,
                        anchor=tk.W
                    )
                    time_ago_label.pack(side=tk.LEFT, pady=0, padx=0)
                    
                    # Duration label - right-justified, same style as RVU, no padding
                    duration_seconds = record.get("duration_seconds", 0)
                    duration_text = self._format_duration(duration_seconds)
                    duration_label = tk.Label(
                        time_row_frame,
                        text=duration_text,
                        font=("Consolas", 7),
                        fg=text_color,
                        bg=bg_color,
                        padx=0,
                        pady=0,
                        anchor=tk.E
                    )
                    duration_label.pack(side=tk.RIGHT, pady=0, padx=0)
                    
                    # Store labels for updating
                    if not hasattr(self, 'time_labels'):
                        self.time_labels = []
                    self.time_labels.append({
                        'time_ago_label': time_ago_label,
                        'duration_label': duration_label,
                        'spacer_label': spacer_label,
                        'record': record,
                        'time_row_frame': time_row_frame
                    })
                
                self.study_widgets.append(study_frame)
            
            # Scroll to top to show most recent
            self.studies_canvas.update_idletasks()
            self.studies_canvas.yview_moveto(0)
            
            total_records = len(self.data_manager.data["current_shift"]["records"])
            if total_records > max_studies:
                more_count = total_records - max_studies
                more_label = ttk.Label(self.studies_scrollable_frame, text=f"... {more_count} more", font=("Consolas", 7), foreground="gray")
                more_label.pack()
                self.study_widgets.append(more_label)
    
    def update_debug_display(self):
        """Update the debug display with current PowerScribe or Mosaic data."""
        show_time = self.data_manager.data["settings"].get("show_time", False)
        
        # FIRST: Check if accession is already recorded (even if procedure is N/A)
        # This ensures "already recorded" shows for both PowerScribe and Mosaic
        # when reopening a previously-recorded study
        is_duplicate_for_display = False
        duplicate_count = 0
        total_count = 0
        all_duplicates = False
        some_duplicates = False
        
        # =======================================================================
        # UNIFIED DUPLICATE DETECTION - Same logic for PowerScribe and Mosaic
        # =======================================================================
        # 
        # Duplicate check order (same for all accessions):
        # 1. Check seen_accessions memory cache (fastest)
        # 2. Check database for current shift
        # 3. Check multi-accession history
        #
        # If any check finds the accession was recorded, it's a duplicate.
        # =======================================================================
        
        def _check_accession_duplicate(acc: str, current_shift) -> bool:
            """Check if a single accession is a duplicate. Returns True if duplicate."""
            # 1. Check memory cache first (fastest)
            if acc in self.tracker.seen_accessions:
                return True
            
            # 2. Check database
            if current_shift:
                try:
                    db_record = self.data_manager.db.find_record_by_accession(
                        current_shift['id'], acc
                    )
                    if db_record:
                        # Cache for future checks
                        self.tracker.seen_accessions.add(acc)
                        return True
                except:
                    pass
            
            # 3. Check multi-accession history
            if self.tracker._was_part_of_multi_accession(acc, self.data_manager):
                # Cache for future checks
                self.tracker.seen_accessions.add(acc)
                return True
            
            return False
        
        ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
        current_shift = None
        if ignore_duplicates:
            try:
                current_shift = self.data_manager.db.get_current_shift()
            except:
                pass
        
        # Check for duplicates in multi-accession studies
        if self.current_multiple_accessions:
            # Extract all accession numbers for duplicate checking
            # Format: PowerScribe = ["ACC1", "ACC2"], Mosaic = ["ACC1 (PROC1)", "ACC2 (PROC2)"]
            all_accession_numbers = []
            for acc_entry in self.current_multiple_accessions:
                if '(' in acc_entry and ')' in acc_entry:
                    # Mosaic format: "ACC (PROC)" - extract just the accession
                    acc_match = re.match(r'^([^(]+)', acc_entry)
                    if acc_match:
                        all_accession_numbers.append(acc_match.group(1).strip())
                else:
                    # PowerScribe format: just accession
                    all_accession_numbers.append(acc_entry.strip())
            
            total_count = len(all_accession_numbers)
            
            if ignore_duplicates and all_accession_numbers:
                for acc in all_accession_numbers:
                    if _check_accession_duplicate(acc, current_shift):
                        duplicate_count += 1
                
                all_duplicates = duplicate_count == total_count
                some_duplicates = duplicate_count > 0 and duplicate_count < total_count
                is_duplicate_for_display = all_duplicates
        
        # Check for duplicates in single-accession studies
        elif self.current_accession and ignore_duplicates:
            is_duplicate_for_display = _check_accession_duplicate(self.current_accession, current_shift)
        
        # NOTE: We no longer return early for duplicates. Instead, we show "already recorded"
        # for the accession line but continue displaying the rest (procedure, study type, timer, etc.)
        # The duplicate status (is_duplicate_for_display, all_duplicates, some_duplicates) is used
        # below when displaying the accession line.
        
        # Check if procedure is "n/a" - if so, don't display anything
        is_na = self.current_procedure and self.current_procedure.strip().lower() in ["n/a", "na", "none", ""]
        
        if is_na or not self.current_procedure:
            self.debug_accession_label.config(text="", foreground="gray")
            self.debug_duration_label.config(text="")
            self.debug_procedure_label.config(text="", foreground="gray")
            self.debug_patient_class_label.config(text="")
            self.debug_study_type_prefix_label.config(text="")
            self.debug_study_type_label.config(text="")
            self.debug_study_rvu_label.config(text="")
        else:
            # Handle multi-accession display (2+ accessions only)
            if self.current_multiple_accessions and len(self.current_multiple_accessions) > 1:
                # Multi-accession - either active or duplicate
                # Parse accession display - handle both formats:
                # PowerScribe: ["ACC1", "ACC2"]
                # Mosaic: ["ACC1 (PROC1)", "ACC2 (PROC2)"]
                acc_display_list = []
                accession_numbers = []  # For duplicate checking
                for acc_entry in self.current_multiple_accessions[:2]:
                    if '(' in acc_entry and ')' in acc_entry:
                        # Mosaic format - extract just the accession
                        acc_match = re.match(r'^([^(]+)', acc_entry)
                        if acc_match:
                            acc_num = acc_match.group(1).strip()
                            acc_display_list.append(acc_num)
                            accession_numbers.append(acc_num)
                        else:
                            acc_display_list.append(acc_entry)
                            accession_numbers.append(acc_entry)
                    else:
                        # PowerScribe format - use as-is
                        acc_display_list.append(acc_entry)
                        accession_numbers.append(acc_entry)
                
                # Reuse duplicate checking results computed at the top of the function
                # (all_duplicates, some_duplicates, duplicate_count, total_count already computed)
                
                acc_display = ", ".join(acc_display_list)
                if len(self.current_multiple_accessions) > 2:
                    acc_display += f" (+{len(self.current_multiple_accessions) - 2})"
                
                # Calculate duration for current study (show timer even for duplicates)
                duration_text = ""
                if show_time and (self.current_accession or self.multi_accession_mode):
                    duration_text = self._get_current_study_duration()
                
                # Truncate accession if needed to make room for duration
                if show_time and duration_text:
                    # Calculate available width for accession
                    frame_width = self.root.winfo_width()
                    if frame_width > 100:
                        # Estimate space needed for duration (roughly 8-10 chars like "12m 34s")
                        duration_chars = 8
                        prefix_chars = len("Accession: ")
                        reserved = 95 + (duration_chars * 6)  # Reserve space for duration
                        usable_width = max(frame_width - reserved, 50)
                        char_width = 8 * 0.75
                        max_chars = int(usable_width / char_width)
                        max_chars = max(10, min(max_chars, 100))
                        
                        if len(acc_display) > max_chars:
                            acc_display = self._truncate_text(acc_display, max_chars)
                
                # Display based on duplicate status
                # Show "already recorded" for accession line, but still show duration and other fields
                if all_duplicates:
                    # All accessions already recorded
                    self.debug_accession_label.config(text="already recorded", foreground="#c62828")
                elif some_duplicates:
                    # Partial duplicates - show "X of Y already recorded" in red
                    self.debug_accession_label.config(text=f"{duplicate_count} of {total_count} already recorded", foreground="#c62828")
                else:
                    # No duplicates - show normal accession display
                    self.debug_accession_label.config(text=f"Accession: {acc_display}", foreground="gray")
                
                # Always show duration (even for duplicates - shows how long user has been viewing)
                self.debug_duration_label.config(text=duration_text if show_time else "")
                
                # Check if this is Mosaic multi-accession (no multi_accession_mode, but has multiple)
                data_source = self._active_source or "PowerScribe"
                is_mosaic_multi = data_source == "Mosaic" and len(self.current_multiple_accessions) > 1 and not self.multi_accession_mode
                
                if is_mosaic_multi:
                    # Mosaic multi-accession - show summary
                    if self.current_procedure and self.current_procedure != "Multiple studies":
                        # Show the first procedure
                        self.debug_procedure_label.config(text=f"Procedure: {self.current_procedure}", foreground="gray")
                    else:
                        # Show summary
                        self.debug_procedure_label.config(text=f"Procedure: {len(self.current_multiple_accessions)} studies", foreground="gray")
                elif self.multi_accession_mode:
                    # Active multi-accession tracking
                    collected_count = len(self.multi_accession_data)
                    total_count = len(self.current_multiple_accessions)
                    
                    if collected_count < total_count:
                        # Incomplete - show in red
                        self.debug_procedure_label.config(text=f"Procedure: incomplete ({collected_count}/{total_count})", foreground="red")
                    else:
                        # Complete - show "Multiple" with modality
                        modalities = set()
                        for d in self.multi_accession_data.values():
                            st = d["study_type"]
                            if st:
                                parts = st.split()
                                if parts:
                                    modalities.add(parts[0])
                        modality = list(modalities)[0] if modalities else "Studies"
                        self.debug_procedure_label.config(text=f"Procedure: Multiple {modality}", foreground="gray")
                else:
                    # Duplicate multi-accession - already completed, extract modality from study type
                    if self.current_study_type.startswith("Multiple"):
                        self.debug_procedure_label.config(text=f"Procedure: {self.current_study_type}", foreground="gray")
                    else:
                        # Get modality from current procedure
                        classification_rules = self.data_manager.data.get("classification_rules", {})
                        direct_lookups = self.data_manager.data.get("direct_lookups", {})
                        study_type, _ = match_study_type(self.current_procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                        parts = study_type.split() if study_type else []
                        modality = parts[0] if parts else "Studies"
                        self.debug_procedure_label.config(text=f"Procedure: Multiple {modality}", foreground="gray")
            else:
                # Single accession display
                accession_text = self.current_accession if self.current_accession else '-'
                
                # Use the duplicate check result from the top of the function
                # (is_duplicate_for_display was already computed using unified logic)
                is_duplicate = is_duplicate_for_display
                
                # Calculate duration for current study (show timer even for duplicates)
                duration_text = ""
                if show_time and (self.current_accession or self.multi_accession_mode):
                    duration_text = self._get_current_study_duration()
                
                # Truncate accession if needed to make room for duration
                if show_time and duration_text:
                    # Calculate available width for accession
                    frame_width = self.root.winfo_width()
                    if frame_width > 100:
                        # Estimate space needed for duration (roughly 8-10 chars like "12m 34s")
                        duration_chars = 8
                        prefix_chars = len("Accession: ")
                        reserved = 95 + (duration_chars * 6)  # Reserve space for duration
                        usable_width = max(frame_width - reserved, 50)
                        char_width = 8 * 0.75
                        max_chars = int(usable_width / char_width)
                        max_chars = max(10, min(max_chars, 100))
                        
                        if len(accession_text) > max_chars:
                            accession_text = self._truncate_text(accession_text, max_chars)
                
                # If duplicate, show "already recorded" in red instead of accession
                # But still show duration and other fields normally
                if is_duplicate:
                    self.debug_accession_label.config(text="already recorded", foreground="#c62828")
                else:
                    self.debug_accession_label.config(text=f"Accession: {accession_text}", foreground="gray")
                
                # Always show duration (even for duplicates - shows how long user has been viewing)
                self.debug_duration_label.config(text=duration_text if show_time else "")
                
                # No truncation for procedure - show full name
                procedure_display = self.current_procedure if self.current_procedure else '-'
                self.debug_procedure_label.config(text=f"Procedure: {procedure_display}", foreground="gray")
            
            self.debug_patient_class_label.config(text=f"Patient Class: {self.current_patient_class if self.current_patient_class else '-'}")
            
            # Display study type with RVU on the right (separate labels for alignment)
            if self.current_study_type:
                # Dynamic truncation based on window width to balance readability with RVU visibility
                frame_width = self.root.winfo_width()
                # Calculate available space: window width minus prefix, RVU, and margins
                # Estimate: "Study Type: " (13 chars) + RVU " 99.9 RVU" (9 chars) + margins (30px)
                available_width = max(frame_width - 180, 80)  # At least 80px
                char_width = 7.5  # Average char width in pixels for Arial 7
                max_chars = int(available_width / char_width)
                max_chars = max(15, min(max_chars, 35))  # Between 15-35 chars
                study_type_display = self._truncate_text(self.current_study_type, max_chars)
                # Show prefix
                self.debug_study_type_prefix_label.config(text="Study Type: ", foreground="gray")
                # Check if incomplete (starts with "incomplete") - show in red
                if self.current_study_type.startswith("incomplete"):
                    self.debug_study_type_label.config(text=study_type_display, foreground="red")
                else:
                    self.debug_study_type_label.config(text=study_type_display, foreground="gray")
                rvu_value = self.current_study_rvu if self.current_study_rvu is not None else 0.0
                self.debug_study_rvu_label.config(text=f"{rvu_value:.1f} RVU")
            else:
                self.debug_study_type_prefix_label.config(text="Study Type: ", foreground="gray")
                self.debug_study_type_label.config(text="-", foreground="gray")
                self.debug_study_rvu_label.config(text="")

    def _format_hour_label(self, dt: datetime) -> str:
        """Format datetime into '2am' style label."""
        if not dt:
            return ""
        label = dt.strftime("%I%p").lstrip("0").lower()
        return label or dt.strftime("%I%p").lower()
    
    def open_settings(self):
        """Open settings modal."""
        SettingsWindow(self.root, self.data_manager, self)
    
    def open_statistics(self):
        """Open statistics modal."""
        StatisticsWindow(self.root, self.data_manager, self)
    
    def apply_theme(self):
        """Apply light or dark theme based on settings."""
        dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
        
        # Use 'clam' theme for both modes to ensure consistent layout
        # (Windows native theme ignores background colors and has different sizing)
        self.style.theme_use('clam')
        
        if dark_mode:
            # Dark mode colors
            bg_color = "#1e1e1e"  # Almost black
            fg_color = "#e0e0e0"  # Slightly off white
            entry_bg = "#2d2d2d"
            entry_fg = "#e0e0e0"
            select_bg = "#4a4a4a"
            button_bg = "#3d3d3d"
            button_fg = "#e0e0e0"
            button_active_bg = "#4a4a4a"
            treeview_bg = "#252525"
            treeview_fg = "#e0e0e0"
            canvas_bg = "#1e1e1e"
            comp_color = "#4ec94e"  # Lighter green for dark mode
            delete_btn_bg = "#3d3d3d"
            delete_btn_fg = "#aaaaaa"
            delete_btn_hover = "#ff6b6b"  # Light red for dark mode
            border_color = "#888888"  # Light grey for canvas borders (visible on dark background)
            text_secondary = "#aaaaaa"  # Gray text for secondary info
        else:
            # Light mode colors
            bg_color = "#f0f0f0"
            fg_color = "black"
            entry_bg = "white"
            entry_fg = "black"
            select_bg = "#0078d7"
            button_bg = "#e1e1e1"
            button_fg = "black"
            button_active_bg = "#d0d0d0"
            treeview_bg = "white"
            treeview_fg = "black"
            canvas_bg = "#f0f0f0"
            comp_color = "dark green"
            delete_btn_bg = "#f0f0f0"
            delete_btn_fg = "gray"
            delete_btn_hover = "#ffcccc"  # Light red for light mode
            border_color = "#cccccc"  # Light grey for canvas borders
            text_secondary = "gray"  # Gray text for secondary info
            # Pace car bar colors (light mode)
            pace_container_bg = "#e0e0e0"
            pace_current_track_bg = "#e8e8e8"
            pace_prior_track_bg = "#B8B8DC"  # Lavender
            pace_marker_bg = "#000000"  # Black marker
        
        if dark_mode:
            # Pace car bar colors (dark mode)
            pace_container_bg = "#3d3d3d"
            pace_current_track_bg = "#4a4a4a"
            pace_prior_track_bg = "#5a5a8c"  # Darker lavender
            pace_marker_bg = "#ffffff"  # White marker for visibility
        
        # Store current theme colors for new widgets
        self.theme_colors = {
            "bg": bg_color,
            "fg": fg_color,
            "button_bg": button_bg,
            "button_fg": button_fg,
            "button_active_bg": button_active_bg,
            "entry_bg": entry_bg,
            "entry_fg": entry_fg,
            "comp_color": comp_color,
            "canvas_bg": canvas_bg,
            "delete_btn_bg": delete_btn_bg,
            "delete_btn_fg": delete_btn_fg,
            "delete_btn_hover": delete_btn_hover,
            "border_color": border_color,
            "text_secondary": text_secondary,
            "dark_mode": dark_mode,
            "pace_container_bg": pace_container_bg,
            "pace_current_track_bg": pace_current_track_bg,
            "pace_prior_track_bg": pace_prior_track_bg,
            "pace_marker_bg": pace_marker_bg
        }
        
        # Configure root window
        self.root.configure(bg=bg_color)
        
        # Configure ttk styles
        self.style.configure(".", background=bg_color, foreground=fg_color)
        self.style.configure("TFrame", background=bg_color)
        self.style.configure("TLabel", background=bg_color, foreground=fg_color)
        self.style.configure("TLabelframe", background=bg_color, bordercolor=border_color)
        self.style.configure("TLabelframe.Label", background=bg_color, foreground=fg_color)
        self.style.configure("TButton", background=button_bg, foreground=button_fg, bordercolor=border_color, padding=(5, 2))
        self.style.map("TButton", 
                       background=[("active", button_active_bg), ("pressed", button_active_bg)],
                       foreground=[("active", fg_color), ("pressed", fg_color)])
        self.style.configure("TCheckbutton", background=bg_color, foreground=fg_color)
        self.style.map("TCheckbutton", background=[("active", bg_color)])
        self.style.configure("TRadiobutton", background=bg_color, foreground=fg_color)
        self.style.map("TRadiobutton", background=[("active", bg_color)])
        self.style.configure("TEntry", fieldbackground=entry_bg, foreground=entry_fg, bordercolor=border_color)
        self.style.configure("Treeview", background=treeview_bg, foreground=treeview_fg, fieldbackground=treeview_bg)
        self.style.configure("Treeview.Heading", background=button_bg, foreground=fg_color)
        self.style.map("Treeview", background=[("selected", select_bg)])
        self.style.configure("TScrollbar", background=button_bg, troughcolor=bg_color, bordercolor=border_color)
        self.style.configure("TPanedwindow", background=bg_color)
        
        # Keep red style for temporary recent studies label
        self.style.configure("Red.TLabelframe.Label", foreground="red", background=bg_color)
        
        # Update tk widgets (non-ttk) if they exist
        self._update_tk_widget_colors()
    
    def _update_tk_widget_colors(self):
        """Update colors for tk (non-ttk) widgets."""
        colors = getattr(self, 'theme_colors', None)
        if not colors:
            return
            
        bg_color = colors["bg"]
        fg_color = colors["fg"]
        comp_color = colors["comp_color"]
        canvas_bg = colors["canvas_bg"]
        text_secondary = colors.get("text_secondary", "gray")
        
        # Pace car bar colors
        pace_container_bg = colors.get("pace_container_bg", "#e0e0e0")
        pace_current_track_bg = colors.get("pace_current_track_bg", "#e8e8e8")
        pace_prior_track_bg = colors.get("pace_prior_track_bg", "#B8B8DC")
        pace_marker_bg = colors.get("pace_marker_bg", "#000000")
        
        # Update compensation labels (tk.Label)
        for label in [
            getattr(self, 'total_comp_label', None),
            getattr(self, 'avg_comp_label', None),
            getattr(self, 'last_hour_comp_label', None),
            getattr(self, 'last_full_hour_comp_label', None),
            getattr(self, 'projected_comp_label', None),
            getattr(self, 'projected_shift_comp_label', None),
        ]:
            if label:
                label.configure(bg=bg_color, fg=comp_color)
        
        # Update backup status label (tk.Label)
        backup_label = getattr(self, 'backup_status_label', None)
        if backup_label:
            backup_label.configure(bg=bg_color)
        
        # Update canvas
        canvas = getattr(self, 'studies_canvas', None)
        if canvas:
            canvas.configure(bg=canvas_bg)
        
        # Update counters frame (tk.LabelFrame)
        counters_frame = getattr(self, 'counters_frame', None)
        if counters_frame:
            counters_frame.configure(bg=bg_color, fg=fg_color)
        
        # Update recent studies frame (tk.LabelFrame)
        recent_frame = getattr(self, 'recent_frame', None)
        if recent_frame:
            recent_frame.configure(bg=bg_color, fg=fg_color)
        
        # Update debug/current study frame (tk.LabelFrame)
        debug_frame = getattr(self, 'debug_frame', None)
        if debug_frame:
            debug_frame.configure(bg=bg_color, fg=fg_color)
        
        # Update pace car bar widgets (tk.Frame)
        pace_bars_container = getattr(self, 'pace_bars_container', None)
        if pace_bars_container:
            pace_bars_container.configure(bg=pace_container_bg)
        
        pace_bar_current_track = getattr(self, 'pace_bar_current_track', None)
        if pace_bar_current_track:
            pace_bar_current_track.configure(bg=pace_current_track_bg)
        
        pace_bar_prior_track = getattr(self, 'pace_bar_prior_track', None)
        if pace_bar_prior_track:
            pace_bar_prior_track.configure(bg=pace_prior_track_bg)
        
        pace_bar_prior_marker = getattr(self, 'pace_bar_prior_marker', None)
        if pace_bar_prior_marker:
            pace_bar_prior_marker.configure(bg=pace_marker_bg)
        
        # Update pace car labels (tk.Label)
        pace_labels = [
            getattr(self, 'pace_label_now_text', None),
            getattr(self, 'pace_label_separator', None),
            getattr(self, 'pace_label_time', None),
            getattr(self, 'pace_label_right', None),
            getattr(self, 'pace_label_prior_text', None),
        ]
        for label in pace_labels:
            if label:
                label.configure(bg=bg_color, fg=text_secondary)
        
        # pace_label_now_value and pace_label_prior_value keep their dynamic colors
        if hasattr(self, 'pace_label_now_value') and self.pace_label_now_value:
            self.pace_label_now_value.configure(bg=bg_color)
        if hasattr(self, 'pace_label_prior_value') and self.pace_label_prior_value:
            self.pace_label_prior_value.configure(bg=bg_color)
        
        # Update pace label frame
        if hasattr(self, 'pace_label_frame') and self.pace_label_frame:
            self.pace_label_frame.configure(bg=bg_color)
        
        # Update studies_scrollable_frame style to use canvas_bg
        # ttk.Frame uses TFrame style, but we need a specific style for the scrollable frame
        self.style.configure("StudiesScrollable.TFrame", background=canvas_bg)
        studies_frame = getattr(self, 'studies_scrollable_frame', None)
        if studies_frame:
            studies_frame.configure(style="StudiesScrollable.TFrame")
    
    def get_theme_colors(self):
        """Get current theme colors for use by other windows."""
        return getattr(self, 'theme_colors', {
            "bg": "#f0f0f0",
            "fg": "black",
            "button_bg": "#e1e1e1",
            "button_fg": "black",
            "button_active_bg": "#d0d0d0",
            "delete_btn_bg": "#f0f0f0",
            "delete_btn_fg": "gray",
            "delete_btn_hover": "#ffcccc",
            "canvas_bg": "#f0f0f0",
            "dark_mode": False
        })
    
    def _on_window_resize(self, event):
        """Handle window resize to update truncation and study count."""
        if event.widget == self.root:
            new_width = event.width
            new_height = event.height
            # Only update if size actually changed significantly
            width_changed = abs(new_width - self._last_width) > 5
            height_changed = abs(new_height - getattr(self, '_last_height', 500)) > 15
            if width_changed or height_changed:
                self._last_width = new_width
                self._last_height = new_height
                # Force rebuild of study widgets with new truncation/count
                self.last_record_count = -1
                self.update_display()
    
    def _calculate_max_chars(self, available_width: int, font_size: int = 8) -> int:
        """Calculate max characters that fit in available width."""
        # Use default width if window not yet laid out (winfo returns 1)
        if available_width < 100:
            available_width = 240  # Default window width
        # Approximate character width for Consolas font
        # At font size 8, each character is roughly 6-7 pixels wide
        char_width = font_size * 0.75
        # Reserve space for: delete button (~20px), RVU label (~60px), padding (~15px)
        reserved = 95
        usable_width = max(available_width - reserved, 50)
        max_chars = int(usable_width / char_width)
        return max(10, min(max_chars, 100))  # Clamp between 10 and 100
    
    def _truncate_text(self, text: str, max_chars: int) -> str:
        """Truncate text with ... if needed, no trailing space."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars-3] + "..."
    
    def _format_time_ago(self, time_finished_str: str) -> str:
        """Format how long ago a study was finished.
        
        Returns format like "5 seconds ago", "2 minutes ago", "1 hour ago"
        """
        if not time_finished_str:
            return ""
        try:
            time_finished = datetime.fromisoformat(time_finished_str)
            now = datetime.now()
            delta = now - time_finished
            
            total_seconds = int(delta.total_seconds())
            
            if total_seconds < 60:
                return f"{total_seconds} second{'s' if total_seconds != 1 else ''} ago"
            elif total_seconds < 3600:
                minutes = total_seconds // 60
                return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
            else:
                hours = total_seconds // 3600
                return f"{hours} hour{'s' if hours != 1 else ''} ago"
        except (ValueError, TypeError):
            return ""
    
    def _format_duration(self, duration_seconds: float) -> str:
        """Format study duration in "xxm xxs" format.
        
        Examples: "45s", "1m 11s", "12m 30s"
        Shows minutes only if >= 1 minute.
        """
        if not duration_seconds:
            return "0s"
        
        total_seconds = int(duration_seconds)
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        
        if minutes >= 1:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    
    def _get_current_study_duration(self) -> str:
        """Get the duration of the currently active study.
        
        Returns formatted duration string (e.g., "5m 23s") or empty string if not available.
        """
        # For multi-accession mode, use multi_accession_start_time
        if self.multi_accession_mode and self.multi_accession_start_time:
            current_time = datetime.now()
            duration_seconds = (current_time - self.multi_accession_start_time).total_seconds()
            return self._format_duration(duration_seconds)
        
        # For single accession, check if this study is in active_studies
        if self.current_accession and self.current_accession in self.tracker.active_studies:
            study = self.tracker.active_studies[self.current_accession]
            start_time = study.get("start_time")
            if start_time:
                current_time = datetime.now()
                duration_seconds = (current_time - start_time).total_seconds()
                return self._format_duration(duration_seconds)
        
        return ""
    
    def start_drag(self, event):
        """Start dragging window."""
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        # Initialize last saved position if not set
        if not hasattr(self, '_last_saved_main_x'):
            self._last_saved_main_x = self.root.winfo_x()
            self._last_saved_main_y = self.root.winfo_y()
    
    def on_drag(self, event):
        """Handle window dragging."""
        x = self.root.winfo_x() + event.x - self.drag_start_x
        y = self.root.winfo_y() + event.y - self.drag_start_y
        self.root.geometry(f"+{x}+{y}")
        # Debounce position saving during drag (only if position changed)
        current_x = self.root.winfo_x()
        current_y = self.root.winfo_y()
        if current_x != self._last_saved_main_x or current_y != self._last_saved_main_y:
            if hasattr(self, '_position_save_timer'):
                self.root.after_cancel(self._position_save_timer)
            # Use shorter debounce during drag (100ms) to be more responsive
            self._position_save_timer = self.root.after(100, self.save_window_position)
    
    def _ensure_window_visible(self):
        """Post-mapping validation: ensure window is visible on a monitor.
        
        Called shortly after window is mapped to handle edge cases where:
        - Monitor configuration changed since last run
        - Window was dragged off-screen in a previous session
        - First run on a multi-monitor setup with unusual arrangement
        """
        try:
            self.root.update_idletasks()  # Ensure geometry is updated
            
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            
            # Check if window top-left area is on any monitor
            # Check slightly inward to ensure the window is actually usable
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Window not visible at ({x}, {y}), repositioning...")
                
                # Find the nearest valid position
                new_x, new_y = find_nearest_monitor_for_window(x, y, width, height)
                
                if new_x != x or new_y != y:
                    self.root.geometry(f"{width}x{height}+{new_x}+{new_y}")
                    logger.info(f"Window moved from ({x}, {y}) to ({new_x}, {new_y})")
                    
                    # Save the corrected position
                    self.root.after(100, self.save_window_position)
                else:
                    # Fallback: center on primary monitor
                    primary = get_primary_monitor_bounds()
                    new_x = primary[0] + (primary[2] - primary[0] - width) // 2
                    new_y = primary[1] + (primary[3] - primary[1] - height) // 2
                    self.root.geometry(f"{width}x{height}+{new_x}+{new_y}")
                    logger.info(f"Window centered on primary monitor at ({new_x}, {new_y})")
                    self.root.after(100, self.save_window_position)
            else:
                logger.debug(f"Window visible at ({x}, {y})")
                
        except Exception as e:
            logger.error(f"Error ensuring window visibility: {e}")
    
    def on_drag_end(self, event):
        """Handle end of window dragging - save position immediately."""
        # Cancel any pending debounced save
        if hasattr(self, '_position_save_timer'):
            self.root.after_cancel(self._position_save_timer)
        # Save immediately on mouse release
        self.save_window_position()
    
    def save_window_position(self):
        """Save the main window position and size."""
        try:
            current_x = self.root.winfo_x()
            current_y = self.root.winfo_y()
            
            # Only save if position actually changed
            if hasattr(self, '_last_saved_main_x') and hasattr(self, '_last_saved_main_y'):
                if current_x == self._last_saved_main_x and current_y == self._last_saved_main_y:
                    return  # Position hasn't changed, don't save
            
            if "window_positions" not in self.data_manager.data:
                self.data_manager.data["window_positions"] = {}
            self.data_manager.data["window_positions"]["main"] = {
                "x": current_x,
                "y": current_y,
                "width": self.root.winfo_width(),
                "height": self.root.winfo_height()
            }
            self._last_saved_main_x = current_x
            self._last_saved_main_y = current_y
            # Only save settings (window positions), not records
            self.data_manager.save(save_records=False)
        except Exception as e:
            logger.error(f"Error saving window position: {e}")
    
    def _update_time_display(self):
        """Update time display for recent studies and current study duration every second."""
        show_time = self.data_manager.data["settings"].get("show_time", False)
        
        # Update current study duration if show_time is enabled
        if show_time and hasattr(self, 'debug_duration_label'):
            duration_text = self._get_current_study_duration()
            if duration_text:
                # Update duration
                self.debug_duration_label.config(text=duration_text)
            else:
                self.debug_duration_label.config(text="")
        
        if hasattr(self, 'time_labels') and self.time_labels:
            if show_time:
                for label_info in self.time_labels:
                    try:
                        record = label_info['record']
                        time_ago_label = label_info['time_ago_label']
                        duration_label = label_info.get('duration_label')
                        time_row_frame = label_info.get('time_row_frame')
                        spacer_label = label_info.get('spacer_label')
                        
                        # Update time ago and ensure color matches theme
                        time_ago_text = self._format_time_ago(record.get("time_finished"))
                        text_color = self.theme_colors.get("text_secondary", "gray")
                        # Use canvas_bg to match the studies scrollable area background
                        bg_color = self.theme_colors.get("canvas_bg", self.theme_colors.get("bg", "#f0f0f0"))
                        
                        # Update all labels and frames with theme colors
                        time_ago_label.config(text=time_ago_text, fg=text_color, bg=bg_color)
                        
                        # Update duration label with theme colors
                        if duration_label:
                            duration_seconds = record.get("duration_seconds", 0)
                            duration_text = self._format_duration(duration_seconds)
                            duration_label.config(text=duration_text, fg=text_color, bg=bg_color)
                        
                        # Update spacer label background
                        if spacer_label:
                            spacer_label.config(bg=bg_color)
                        
                        # Update time row frame background
                        if time_row_frame:
                            time_row_frame.config(bg=bg_color)
                    except Exception as e:
                        logger.error(f"Error updating time display: {e}")
        
        # Schedule next update in 1 second for current study duration
        self.root.after(1000, self._update_time_display)
    
    def on_closing(self):
        """Handle window closing - properly cleanup resources."""
        logger.info("Application closing - starting cleanup...")
        
        # Stop the background thread first
        if hasattr(self, '_ps_thread_running'):
            self._ps_thread_running = False
            logger.info("Signaled background thread to stop")
        
        # Wait for thread to terminate (with timeout to prevent hanging)
        if hasattr(self, '_ps_thread') and self._ps_thread.is_alive():
            logger.info("Waiting for background thread to terminate...")
            self._ps_thread.join(timeout=2.0)
            if self._ps_thread.is_alive():
                logger.warning("Background thread did not terminate in time (daemon will be killed on exit)")
            else:
                logger.info("Background thread terminated cleanly")
        
        # Save window position and data
        self.save_window_position()
        self.data_manager.save()
        
        # Close database connection
        if hasattr(self, 'data_manager') and self.data_manager:
            try:
                self.data_manager.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")
        
        logger.info("Application cleanup complete")
        self.root.destroy()


class SettingsWindow:
    """Settings modal window."""
    
    def __init__(self, parent, data_manager: RVUData, app: RVUCounterApp):
        self.parent = parent
        self.data_manager = data_manager
        self.app = app
        
        self.window = tk.Toplevel(parent)
        self.window.title("Settings")
        
        # Load saved window position or use default
        window_pos = self.data_manager.data.get("window_positions", {}).get("settings", None)
        if window_pos:
            x, y = window_pos['x'], window_pos['y']
            # Validate position before applying
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Settings window position ({x}, {y}) is off-screen, finding nearest monitor")
                x, y = find_nearest_monitor_for_window(x, y, 450, 700)
            self.window.geometry(f"450x810+{x}+{y}")
        else:
            # Center on primary monitor
            try:
                primary = get_primary_monitor_bounds()
                x = primary[0] + (primary[2] - primary[0] - 450) // 2
                y = primary[1] + (primary[3] - primary[1] - 700) // 2
                self.window.geometry(f"450x810+{x}+{y}")
            except:
                self.window.geometry("450x810")
        
        self.window.transient(parent)
        self.window.grab_set()
        
        # Track last saved position to avoid excessive saves
        self.last_saved_x = None
        self.last_saved_y = None
        
        # Bind to window movement to save position (debounced)
        self.window.bind("<Configure>", self.on_settings_window_move)
        self.window.bind("<ButtonRelease-1>", self.on_settings_drag_end)
        self.window.protocol("WM_DELETE_WINDOW", self.on_settings_closing)
        
        # Apply theme
        self.apply_theme()
        
        self.create_settings_ui()
    
    def apply_theme(self):
        """Apply theme to settings window."""
        dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
        
        if dark_mode:
            bg_color = "#1e1e1e"
        else:
            bg_color = "SystemButtonFace"
        
        self.window.configure(bg=bg_color)
    
    def create_settings_ui(self):
        """Create settings UI."""
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        settings = self.data_manager.data["settings"]
        
        # Create two-column frame for general settings
        general_settings_frame = ttk.Frame(main_frame)
        general_settings_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Column 1: Auto-resume and Dark Mode
        col1 = ttk.Frame(general_settings_frame)
        col1.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 20))
        
        # Auto-start
        self.auto_start_var = tk.BooleanVar(value=settings["auto_start"])
        ttk.Checkbutton(col1, text="Auto-resume shift on launch", variable=self.auto_start_var).pack(anchor=tk.W, pady=2)
        
        # Dark mode
        self.dark_mode_var = tk.BooleanVar(value=settings.get("dark_mode", False))
        ttk.Checkbutton(col1, text="Dark Mode", variable=self.dark_mode_var).pack(anchor=tk.W, pady=2)
        
        # Column 2: Show time and Stay on top
        col2 = ttk.Frame(general_settings_frame)
        col2.pack(side=tk.LEFT, anchor=tk.N)
        
        # Show time checkbox
        self.show_time_var = tk.BooleanVar(value=settings.get("show_time", False))
        ttk.Checkbutton(col2, text="Show time", variable=self.show_time_var).pack(anchor=tk.W, pady=2)
        
        # Stay on top option
        self.stay_on_top_var = tk.BooleanVar(value=settings.get("stay_on_top", True))
        ttk.Checkbutton(col2, text="Stay on top", variable=self.stay_on_top_var).pack(anchor=tk.W, pady=2)
        
        # Data source radio buttons (PowerScribe or Mosaic)
        data_source_frame = ttk.Frame(main_frame)
        data_source_frame.pack(anchor=tk.W, pady=(10, 5))
        
        ttk.Label(data_source_frame, text="Data Source:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        
        # Auto-detection info (no longer manual selection)
        current_source = self.app._active_source if hasattr(self.app, '_active_source') and self.app._active_source else "auto-detecting"
        ttk.Label(data_source_frame, text=f"Auto-detect ({current_source})", 
                 font=("Arial", 9), foreground="gray").pack(side=tk.LEFT)
        
        # Keep this variable for backwards compatibility but it's not used anymore
        self.data_source_var = tk.StringVar(value="Auto")
        
        # Two-column frame for counters and compensation
        columns_frame = ttk.Frame(main_frame)
        columns_frame.pack(fill=tk.X, pady=(10, 5))
        
        # Column 1: Show Counters
        counters_col = ttk.Frame(columns_frame)
        counters_col.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 20))
        
        ttk.Label(counters_col, text="Show Counters:", font=("Arial", 9, "bold")).pack(anchor=tk.W)
        
        # Counter variables and checkbuttons
        self.show_total_var = tk.BooleanVar(value=settings["show_total"])
        self.total_cb = ttk.Checkbutton(counters_col, text="total", variable=self.show_total_var, 
                                         command=lambda: self.sync_compensation_state("total"))
        self.total_cb.pack(anchor=tk.W, pady=2)
        
        self.show_avg_var = tk.BooleanVar(value=settings["show_avg"])
        self.avg_cb = ttk.Checkbutton(counters_col, text="average per hour", variable=self.show_avg_var,
                                       command=lambda: self.sync_compensation_state("avg"))
        self.avg_cb.pack(anchor=tk.W, pady=2)
        
        self.show_last_hour_var = tk.BooleanVar(value=settings["show_last_hour"])
        self.last_hour_cb = ttk.Checkbutton(counters_col, text="last hour", variable=self.show_last_hour_var,
                                             command=lambda: self.sync_compensation_state("last_hour"))
        self.last_hour_cb.pack(anchor=tk.W, pady=2)
        
        self.show_last_full_hour_var = tk.BooleanVar(value=settings["show_last_full_hour"])
        self.last_full_hour_cb = ttk.Checkbutton(counters_col, text="last full hour", variable=self.show_last_full_hour_var,
                                                  command=lambda: self.sync_compensation_state("last_full_hour"))
        self.last_full_hour_cb.pack(anchor=tk.W, pady=2)
        
        self.show_projected_var = tk.BooleanVar(value=settings["show_projected"])
        self.projected_cb = ttk.Checkbutton(counters_col, text="est this hour", variable=self.show_projected_var,
                                             command=lambda: self.sync_compensation_state("projected"))
        self.projected_cb.pack(anchor=tk.W, pady=2)
        
        self.show_projected_shift_var = tk.BooleanVar(value=settings.get("show_projected_shift", True))
        self.projected_shift_cb = ttk.Checkbutton(counters_col, text="est shift total", variable=self.show_projected_shift_var,
                                             command=lambda: self.sync_compensation_state("projected_shift"))
        self.projected_shift_cb.pack(anchor=tk.W, pady=2)
        
        # Pace car checkbox (compare vs prior shift)
        self.show_pace_car_var = tk.BooleanVar(value=settings.get("show_pace_car", False))
        self.pace_car_cb = ttk.Checkbutton(counters_col, text="pace vs prior shift", variable=self.show_pace_car_var)
        self.pace_car_cb.pack(anchor=tk.W, pady=2)
        
        # Role radio buttons (Partner/Associate)
        role_frame = ttk.Frame(counters_col)
        role_frame.pack(anchor=tk.W, pady=(10, 2))
        
        self.role_var = tk.StringVar(value=settings.get("role", "Partner"))
        ttk.Radiobutton(role_frame, text="Partner", variable=self.role_var, value="Partner").pack(side=tk.LEFT)
        ttk.Radiobutton(role_frame, text="Associate", variable=self.role_var, value="Associate").pack(side=tk.LEFT, padx=(10, 0))
        
        # Column 2: Show Compensation
        comp_col = ttk.Frame(columns_frame)
        comp_col.pack(side=tk.LEFT, anchor=tk.N)
        
        ttk.Label(comp_col, text="Show Compensation:", font=("Arial", 9, "bold")).pack(anchor=tk.W)
        
        # Compensation variables and checkbuttons (initially set based on counter state)
        
        self.show_comp_total_var = tk.BooleanVar(value=settings.get("show_comp_total", False))
        self.comp_total_cb = ttk.Checkbutton(comp_col, text="total", variable=self.show_comp_total_var)
        self.comp_total_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_avg_var = tk.BooleanVar(value=settings.get("show_comp_avg", False))
        self.comp_avg_cb = ttk.Checkbutton(comp_col, text="average per hour", variable=self.show_comp_avg_var)
        self.comp_avg_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_last_hour_var = tk.BooleanVar(value=settings.get("show_comp_last_hour", False))
        self.comp_last_hour_cb = ttk.Checkbutton(comp_col, text="last hour", variable=self.show_comp_last_hour_var)
        self.comp_last_hour_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_last_full_hour_var = tk.BooleanVar(value=settings.get("show_comp_last_full_hour", False))
        self.comp_last_full_hour_cb = ttk.Checkbutton(comp_col, text="last full hour", variable=self.show_comp_last_full_hour_var)
        self.comp_last_full_hour_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_projected_var = tk.BooleanVar(value=settings.get("show_comp_projected", False))
        self.comp_projected_cb = ttk.Checkbutton(comp_col, text="est this hour", variable=self.show_comp_projected_var)
        self.comp_projected_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_projected_shift_var = tk.BooleanVar(value=settings.get("show_comp_projected_shift", True))
        self.comp_projected_shift_cb = ttk.Checkbutton(comp_col, text="est shift total", variable=self.show_comp_projected_shift_var)
        self.comp_projected_shift_cb.pack(anchor=tk.W, pady=2)
        
        # Store mapping for easy sync
        self.comp_mapping = {
            "total": (self.show_total_var, self.show_comp_total_var, self.comp_total_cb),
            "avg": (self.show_avg_var, self.show_comp_avg_var, self.comp_avg_cb),
            "last_hour": (self.show_last_hour_var, self.show_comp_last_hour_var, self.comp_last_hour_cb),
            "last_full_hour": (self.show_last_full_hour_var, self.show_comp_last_full_hour_var, self.comp_last_full_hour_cb),
            "projected": (self.show_projected_var, self.show_comp_projected_var, self.comp_projected_cb),
            "projected_shift": (self.show_projected_shift_var, self.show_comp_projected_shift_var, self.comp_projected_shift_cb),
        }
        
        # Initial sync of compensation state based on counter state
        for key in self.comp_mapping:
            self.sync_compensation_state(key)
        
        # Shift length (hours)
        ttk.Label(main_frame, text="Shift Length (hours):", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10, 5))
        self.shift_length_var = tk.StringVar(value=str(self.data_manager.data["settings"].get("shift_length_hours", 9)))
        ttk.Entry(main_frame, textvariable=self.shift_length_var, width=10).pack(anchor=tk.W, pady=2)
        
        # Min study seconds
        ttk.Label(main_frame, text="Min Study Duration (seconds):", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10, 5))
        self.min_seconds_var = tk.StringVar(value=str(self.data_manager.data["settings"]["min_study_seconds"]))
        ttk.Entry(main_frame, textvariable=self.min_seconds_var, width=10).pack(anchor=tk.W, pady=2)
        
        # Ignore duplicates
        self.ignore_duplicates_var = tk.BooleanVar(value=self.data_manager.data["settings"]["ignore_duplicate_accessions"])
        ttk.Checkbutton(main_frame, text="Ignore duplicate accessions", variable=self.ignore_duplicates_var).pack(anchor=tk.W, pady=2)
        
        # Cloud Backup Section
        backup_frame = ttk.LabelFrame(main_frame, text="☁️ Cloud Backup", padding="5")
        backup_frame.pack(fill=tk.X, pady=(10, 5))
        
        # Check if OneDrive is available
        backup_mgr = self.data_manager.backup_manager
        backup_settings = self.data_manager.data.get("backup", {})
        
        if backup_mgr.is_onedrive_available():
            # Enable checkbox
            self.backup_enabled_var = tk.BooleanVar(value=backup_settings.get("cloud_backup_enabled", False))
            enable_cb = ttk.Checkbutton(backup_frame, text="Enable automatic backup to OneDrive", 
                                        variable=self.backup_enabled_var,
                                        command=self._on_backup_toggle)
            enable_cb.pack(anchor=tk.W, pady=2)
            
            # Show OneDrive path
            onedrive_path = backup_mgr.get_backup_folder()
            if onedrive_path:
                path_label = ttk.Label(backup_frame, text=f"📁 {onedrive_path}", 
                                       font=("Arial", 7), foreground="gray")
                path_label.pack(anchor=tk.W, padx=(20, 0))
            
            # Backup schedule
            schedule_frame = ttk.Frame(backup_frame)
            schedule_frame.pack(anchor=tk.W, pady=(5, 2), padx=(20, 0))
            
            ttk.Label(schedule_frame, text="Backup:", font=("Arial", 8)).pack(side=tk.LEFT)
            
            self.backup_schedule_var = tk.StringVar(value=backup_settings.get("backup_schedule", "shift_end"))
            schedule_options = [
                ("After shift ends", "shift_end"),
                ("Every hour", "hourly"),
                ("Daily", "daily"),
                ("Manual only", "manual")
            ]
            
            for text, value in schedule_options:
                rb = ttk.Radiobutton(schedule_frame, text=text, variable=self.backup_schedule_var, value=value)
                rb.pack(side=tk.LEFT, padx=(10, 0))
            
            # Action buttons row
            action_frame = ttk.Frame(backup_frame)
            action_frame.pack(anchor=tk.W, pady=(5, 2))
            
            ttk.Button(action_frame, text="Backup Now", command=self._do_manual_backup).pack(side=tk.LEFT, padx=2)
            ttk.Button(action_frame, text="View Backups", command=self._show_backup_history).pack(side=tk.LEFT, padx=2)
            ttk.Button(action_frame, text="Restore", command=self._show_restore_dialog).pack(side=tk.LEFT, padx=2)
            
            # Status display
            status = backup_mgr.get_backup_status()
            self.backup_status_label = ttk.Label(backup_frame, 
                                                  text=f"{status['status_icon']} {status['status_text']}", 
                                                  font=("Arial", 8))
            self.backup_status_label.pack(anchor=tk.W, pady=(5, 0))
        else:
            # OneDrive not found
            self.backup_enabled_var = tk.BooleanVar(value=False)
            ttk.Label(backup_frame, text="⚠️ OneDrive not detected", 
                     font=("Arial", 9), foreground="orange").pack(anchor=tk.W)
            ttk.Label(backup_frame, text="Install OneDrive to enable cloud backup", 
                     font=("Arial", 8), foreground="gray").pack(anchor=tk.W)
            self.backup_schedule_var = tk.StringVar(value="shift_end")
            self.backup_status_label = None
        
        # Buttons
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(buttons_frame, text="Clear Current Shift", command=self.clear_current_shift).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons_frame, text="Clear All Data", command=self.clear_all_data).pack(side=tk.LEFT, padx=2)
        
        # Save/Cancel with version on bottom right
        save_cancel_frame = ttk.Frame(main_frame)
        save_cancel_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(save_cancel_frame, text="Save", command=self.save_settings).pack(side=tk.LEFT, padx=2)
        ttk.Button(save_cancel_frame, text="Cancel", command=self.window.destroy).pack(side=tk.LEFT, padx=2)
    
    def sync_compensation_state(self, key):
        """Sync compensation checkbox state based on counter checkbox."""
        counter_var, comp_var, comp_cb = self.comp_mapping[key]
        if counter_var.get():
            # Counter is enabled - enable compensation checkbox
            comp_cb.config(state=tk.NORMAL)
        else:
            # Counter is disabled - disable and uncheck compensation
            comp_var.set(False)
            comp_cb.config(state=tk.DISABLED)
    
    def save_settings(self):
        """Save settings."""
        try:
            self.data_manager.data["settings"]["auto_start"] = self.auto_start_var.get()
            self.data_manager.data["settings"]["dark_mode"] = self.dark_mode_var.get()
            self.data_manager.data["settings"]["show_total"] = self.show_total_var.get()
            self.data_manager.data["settings"]["show_avg"] = self.show_avg_var.get()
            self.data_manager.data["settings"]["show_last_hour"] = self.show_last_hour_var.get()
            self.data_manager.data["settings"]["show_last_full_hour"] = self.show_last_full_hour_var.get()
            self.data_manager.data["settings"]["show_projected"] = self.show_projected_var.get()
            self.data_manager.data["settings"]["show_projected_shift"] = self.show_projected_shift_var.get()
            self.data_manager.data["settings"]["show_comp_total"] = self.show_comp_total_var.get()
            self.data_manager.data["settings"]["show_comp_avg"] = self.show_comp_avg_var.get()
            self.data_manager.data["settings"]["show_comp_last_hour"] = self.show_comp_last_hour_var.get()
            self.data_manager.data["settings"]["show_comp_last_full_hour"] = self.show_comp_last_full_hour_var.get()
            self.data_manager.data["settings"]["show_comp_projected"] = self.show_comp_projected_var.get()
            self.data_manager.data["settings"]["show_comp_projected_shift"] = self.show_comp_projected_shift_var.get()
            self.data_manager.data["settings"]["role"] = self.role_var.get()
            self.data_manager.data["settings"]["data_source"] = self.data_source_var.get()
            self.data_manager.data["settings"]["shift_length_hours"] = int(self.shift_length_var.get())
            self.data_manager.data["settings"]["min_study_seconds"] = int(self.min_seconds_var.get())
            self.data_manager.data["settings"]["ignore_duplicate_accessions"] = self.ignore_duplicates_var.get()
            self.data_manager.data["settings"]["show_time"] = self.show_time_var.get()
            self.data_manager.data["settings"]["stay_on_top"] = self.stay_on_top_var.get()
            self.data_manager.data["settings"]["show_pace_car"] = self.show_pace_car_var.get()
            
            # Save backup settings
            if "backup" not in self.data_manager.data:
                self.data_manager.data["backup"] = {}
            self.data_manager.data["backup"]["cloud_backup_enabled"] = self.backup_enabled_var.get()
            self.data_manager.data["backup"]["backup_schedule"] = self.backup_schedule_var.get()
            # If enabling backup, also set flags to prevent prompt from showing again
            if self.backup_enabled_var.get():
                self.data_manager.data["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.data["backup"]["first_backup_prompt_shown"] = True
            
            # Update tracker min_seconds
            self.app.tracker.min_seconds = self.data_manager.data["settings"]["min_study_seconds"]
            
            # Update stay on top setting
            self.app.root.attributes("-topmost", self.data_manager.data["settings"]["stay_on_top"])
            
            # Update pace car visibility (only show if enabled AND shift is active)
            has_active_shift = self.app.shift_start is not None
            if self.show_pace_car_var.get() and has_active_shift:
                self.app.pace_car_frame.pack(fill=tk.X, pady=(0, 2), after=self.app.counters_frame)
            else:
                self.app.pace_car_frame.pack_forget()
            
            self.data_manager.save()
            self.app.apply_theme()
            self.app._update_tk_widget_colors()
            # Force rebuild of widgets to show/hide time display when setting changes
            self.app.last_record_count = -1
            self.app.update_display()
            self.window.destroy()
            logger.info("Settings saved")
        except Exception as e:
            messagebox.showerror("Error", f"Error saving settings: {e}")
            logger.error(f"Error saving settings: {e}")
    
    def clear_current_shift(self):
        """Clear current shift data."""
        if messagebox.askyesno("Confirm", "Clear current shift data?"):
            self.data_manager.clear_current_shift()
            self.app.update_display()
            logger.info("Current shift cleared from settings")
    
    def clear_all_data(self):
        """Clear all data."""
        if messagebox.askyesno("Confirm", "Clear ALL data? This cannot be undone."):
            self.data_manager.clear_all_data()
            # Clear recent studies from interface by resetting last_record_count and forcing rebuild
            self.app.last_record_count = -1  # Force rebuild by setting to -1
            # Reset tracker to clear any active studies
            self.app.tracker = StudyTracker(
                min_seconds=self.data_manager.data["settings"]["min_study_seconds"]
            )
            self.app.tracker.seen_accessions.clear()
            self.app.tracker.active_studies.clear()
            # Force update display to clear widgets
            self.app.update_display()
            logger.info("All data cleared from settings")
    
    def on_settings_window_move(self, event):
        """Save settings window position when moved."""
        if event.widget == self.window:
            try:
                x = self.window.winfo_x()
                y = self.window.winfo_y()
                # Only save if position actually changed
                if self.last_saved_x != x or self.last_saved_y != y:
                    # Debounce: save after 100ms of no movement (shorter for responsiveness)
                    if hasattr(self, '_save_timer'):
                        self.window.after_cancel(self._save_timer)
                    self._save_timer = self.window.after(100, lambda: self.save_settings_position(x, y))
            except Exception as e:
                logger.error(f"Error saving settings window position: {e}")
    
    def on_settings_drag_end(self, event):
        """Handle end of settings window dragging - save position immediately."""
        # Cancel any pending debounced save
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        # Save immediately on mouse release
        self.save_settings_position()
    
    def save_settings_position(self, x=None, y=None):
        """Save settings window position."""
        try:
            if x is None:
                x = self.window.winfo_x()
            if y is None:
                y = self.window.winfo_y()
            
            if "window_positions" not in self.data_manager.data:
                self.data_manager.data["window_positions"] = {}
            self.data_manager.data["window_positions"]["settings"] = {
                "x": x,
                "y": y
            }
            self.last_saved_x = x
            self.last_saved_y = y
            self.data_manager.save()
        except Exception as e:
            logger.error(f"Error saving settings window position: {e}")
    
    def on_settings_closing(self):
        """Handle settings window closing."""
        # Cancel any pending save timer
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        self.save_settings_position()
        self.window.destroy()
    
    def _on_backup_toggle(self):
        """Handle backup enable/disable toggle."""
        enabled = self.backup_enabled_var.get()
        if enabled:
            # First time enabling - show confirmation
            backup_folder = self.data_manager.backup_manager.get_backup_folder()
            if backup_folder:
                logger.info(f"Cloud backup enabled. Backups will be stored in: {backup_folder}")
                # Mark that user has seen and responded to the prompt (if it was shown)
                if "backup" not in self.data_manager.data:
                    self.data_manager.data["backup"] = {}
                self.data_manager.data["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.data["backup"]["first_backup_prompt_shown"] = True
                self.data_manager.save()
    
    def _do_manual_backup(self):
        """Perform a manual backup."""
        backup_mgr = self.data_manager.backup_manager
        
        # Show progress
        self.backup_status_label.config(text="⏳ Backing up...")
        self.window.update()
        
        # Perform backup
        result = backup_mgr.create_backup(force=True)
        
        if result["success"]:
            self.backup_status_label.config(text=f"☁️ Backup complete ({result.get('record_count', 0)} records)")
            messagebox.showinfo("Backup Complete", 
                               f"Backup created successfully!\n\n"
                               f"Location: {result['path']}\n"
                               f"Records: {result.get('record_count', 0)}")
        else:
            self.backup_status_label.config(text=f"⚠️ Backup failed")
            messagebox.showerror("Backup Failed", f"Backup failed: {result['error']}")
        
        # Save updated status
        self.data_manager.save()
    
    def _show_backup_history(self):
        """Show backup history dialog."""
        backup_mgr = self.data_manager.backup_manager
        backups = backup_mgr.get_backup_history()
        
        if not backups:
            messagebox.showinfo("No Backups", "No backups found yet.\n\nClick 'Backup Now' to create your first backup.")
            return
        
        # Create dialog
        dialog = tk.Toplevel(self.window)
        dialog.title("OneDrive Cloud")
        dialog.transient(self.window)
        dialog.grab_set()
        
        # Apply theme to dialog
        colors = self.app.get_theme_colors()
        dialog.configure(bg=colors["bg"])
        
        # Configure ttk styles for this dialog
        try:
            style = self.app.style
        except:
            style = ttk.Style()
        
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
        style.configure("TButton", background=colors["button_bg"], foreground=colors["button_fg"], 
                       bordercolor=colors.get("border_color", "#cccccc"))
        style.map("TButton", 
                 background=[("active", colors["button_active_bg"]), ("pressed", colors["button_active_bg"])],
                 foreground=[("active", colors["fg"]), ("pressed", colors["fg"])])
        style.configure("TScrollbar", background=colors["button_bg"], troughcolor=colors["bg"], 
                       bordercolor=colors.get("border_color", "#cccccc"))
        
        # Load saved window position or use default
        window_pos = self.data_manager.data.get("window_positions", {}).get("backup_history", None)
        if window_pos:
            x, y = window_pos['x'], window_pos['y']
            # Validate position before applying
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Backup history dialog position ({x}, {y}) is off-screen, finding nearest monitor")
                x, y = find_nearest_monitor_for_window(x, y, 500, 400)
            dialog.geometry(f"500x400+{x}+{y}")
        else:
            # Center on parent window
            dialog.update_idletasks()
            x = self.window.winfo_x() + (self.window.winfo_width() // 2) - 250
            y = self.window.winfo_y() + (self.window.winfo_height() // 2) - 200
            dialog.geometry(f"500x400+{x}+{y}")
        
        dialog.minsize(400, 250)
        
        # Track last saved position to avoid excessive saves
        last_saved_x = None
        last_saved_y = None
        
        def save_backup_history_position(x=None, y=None):
            """Save backup history dialog position."""
            nonlocal last_saved_x, last_saved_y
            try:
                if x is None:
                    x = dialog.winfo_x()
                if y is None:
                    y = dialog.winfo_y()
                
                if "window_positions" not in self.data_manager.data:
                    self.data_manager.data["window_positions"] = {}
                self.data_manager.data["window_positions"]["backup_history"] = {
                    "x": x,
                    "y": y
                }
                last_saved_x = x
                last_saved_y = y
                self.data_manager.save()
            except Exception as e:
                logger.error(f"Error saving backup history dialog position: {e}")
        
        def on_backup_history_move(event):
            """Save backup history dialog position when moved."""
            if event.widget == dialog:
                try:
                    x = dialog.winfo_x()
                    y = dialog.winfo_y()
                    # Only save if position actually changed
                    if last_saved_x != x or last_saved_y != y:
                        # Debounce: save after 100ms of no movement
                        if hasattr(dialog, '_save_timer'):
                            dialog.after_cancel(dialog._save_timer)
                        dialog._save_timer = dialog.after(100, lambda: save_backup_history_position(x, y))
                except Exception as e:
                    logger.error(f"Error saving backup history dialog position: {e}")
        
        def on_backup_history_drag_end(event):
            """Handle end of backup history dialog dragging - save position immediately."""
            # Cancel any pending debounced save
            if hasattr(dialog, '_save_timer'):
                try:
                    dialog.after_cancel(dialog._save_timer)
                except:
                    pass
            # Save immediately on mouse release
            save_backup_history_position()
        
        def on_backup_history_closing():
            """Handle backup history dialog closing."""
            # Cancel any pending save timer
            if hasattr(dialog, '_save_timer'):
                try:
                    dialog.after_cancel(dialog._save_timer)
                except:
                    pass
            save_backup_history_position()
            dialog.destroy()
        
        # Bind to window movement to save position (debounced)
        dialog.bind("<Configure>", on_backup_history_move)
        dialog.bind("<ButtonRelease-1>", on_backup_history_drag_end)
        dialog.protocol("WM_DELETE_WINDOW", on_backup_history_closing)
        
        # Main container frame
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Label
        label = ttk.Label(main_frame, text="Select a backup to restore:", font=("Arial", 10))
        label.pack(anchor=tk.W, pady=(0, 5))
        
        # Frame for scrollable backup list
        list_container = ttk.Frame(main_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        # Create canvas with scrollbar for scrollable list
        canvas_frame = ttk.Frame(list_container)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        # Get theme colors for canvas
        colors = self.app.get_theme_colors()
        canvas = tk.Canvas(canvas_frame, highlightthickness=0, bg=colors["bg"])
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Update canvas window width when canvas is resized
        def update_canvas_window_width(event):
            canvas.itemconfig(canvas_window, width=event.width)
        
        canvas.bind('<Configure>', update_canvas_window_width)
        
        # Update scroll region when scrollable frame changes
        def update_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        scrollable_frame.bind('<Configure>', update_scroll_region)
        
        # Mouse wheel scrolling (bind to canvas and scrollable_frame)
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Store references
        dialog.backup_files = backups
        dialog.scrollable_frame = scrollable_frame
        dialog.canvas = canvas
        dialog.selected_backup = None
        
        def refresh_backup_list():
            """Refresh the backup list display."""
            # Clear existing widgets
            for widget in scrollable_frame.winfo_children():
                widget.destroy()
            
            # Re-fetch backup files
            backups = backup_mgr.get_backup_history()
            dialog.backup_files = backups
            
            # Get theme colors
            colors = self.app.get_theme_colors()
            
            # Populate scrollable frame with backup entries
            for backup in backups:
                backup_frame = ttk.Frame(scrollable_frame)
                backup_frame.pack(fill=tk.X, pady=1, padx=2)
                
                # X button to delete
                delete_btn = tk.Label(
                    backup_frame,
                    text="×",
                    font=("Arial", 8),
                    bg=colors["delete_btn_bg"],
                    fg=colors["delete_btn_fg"],
                    cursor="hand2",
                    padx=2,
                    pady=2,
                    width=2,
                    anchor=tk.CENTER
                )
                delete_btn.backup_path = backup["path"]
                delete_btn.backup_display = backup["timestamp"].strftime("%B %d, %Y at %I:%M %p")
                delete_btn.bind("<Button-1>", lambda e, btn=delete_btn: delete_backup(btn))
                delete_btn.bind("<Enter>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_hover"]))
                delete_btn.bind("<Leave>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_bg"]))
                delete_btn.pack(side=tk.LEFT, padx=(0, 5))
                
                # Backup label (clickable) - format same as local backups
                backup_display = backup["timestamp"].strftime("%B %d, %Y at %I:%M %p")
                # Add record count to display
                if backup.get("record_count", 0) > 0:
                    backup_display += f" ({backup['record_count']} records)"
                backup_label = ttk.Label(
                    backup_frame,
                    text=backup_display,
                    font=("Consolas", 9),
                    cursor="hand2"
                )
                backup_label.backup = backup
                backup_label.bind("<Button-1>", lambda e, lbl=backup_label: select_backup(lbl))
                backup_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
                
                # Highlight selected backup
                if dialog.selected_backup and dialog.selected_backup["path"] == backup["path"]:
                    backup_label.config(background=colors.get("button_bg", "#e1e1e1"))
            
            # Update canvas scroll region
            canvas.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
            
            # If no backups left, close dialog
            if not backups:
                messagebox.showinfo("No Backups", "No backup files found.")
                dialog.destroy()
        
        def select_backup(label):
            """Select a backup file."""
            # Clear previous selection
            for widget in scrollable_frame.winfo_children():
                if isinstance(widget, ttk.Frame):
                    for child in widget.winfo_children():
                        if isinstance(child, ttk.Label) and hasattr(child, 'backup'):
                            child.config(background="")
            
            # Highlight selected
            label.config(background=self.app.get_theme_colors().get("button_bg", "#e1e1e1"))
            dialog.selected_backup = label.backup
        
        def delete_backup(btn):
            """Delete a backup file."""
            backup_path = btn.backup_path
            backup_display = btn.backup_display
            
            # Confirm deletion
            response = messagebox.askyesno(
                "Delete Backup?",
                f"Are you sure you want to delete this backup?\n\n"
                f"Backup: {backup_display}\n\n"
                f"This action cannot be undone.",
                parent=dialog
            )
            
            if response:
                try:
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                        logger.info(f"Backup deleted: {backup_path}")
                        
                        # Clear selection if deleted backup was selected
                        if dialog.selected_backup and dialog.selected_backup["path"] == backup_path:
                            dialog.selected_backup = None
                        
                        # Refresh the list
                        refresh_backup_list()
                    else:
                        messagebox.showwarning("File Not Found", f"Backup file not found:\n{backup_path}")
                        refresh_backup_list()
                except Exception as e:
                    error_msg = f"Error deleting backup: {str(e)}"
                    messagebox.showerror("Delete Failed", error_msg)
                    logger.error(error_msg)
        
        def on_load():
            """Restore selected backup."""
            if not dialog.selected_backup:
                messagebox.showwarning("No Selection", "Please select a backup file.")
                return
            
            selected_backup = dialog.selected_backup
            
            # Confirm overwrite
            date_str = selected_backup["timestamp"].strftime("%B %d, %Y at %I:%M %p").lower()
            response = messagebox.askyesno(
                "Confirm Overwrite",
                f"Are you sure you want to restore this backup?\n\n"
                f"Backup: {date_str}\n\n"
                f"This will REPLACE your current study data. This action cannot be undone.\n\n"
                f"Consider creating a backup of your current data first.",
                icon="warning",
                parent=dialog
            )
            
            if response:
                dialog.destroy()
                self._confirm_restore(selected_backup)
        
        # Initial population
        refresh_backup_list()
        
        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(btn_frame, text="Restore", command=on_load).pack(side=tk.LEFT, padx=2)
        
        # Open folder button
        def open_folder():
            folder = backup_mgr.get_backup_folder()
            if folder:
                os.startfile(folder)
        
        ttk.Button(btn_frame, text="Open Folder", command=open_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Close", command=on_backup_history_closing).pack(side=tk.RIGHT, padx=2)
    
    def _show_restore_dialog(self):
        """Show restore from backup dialog."""
        backup_mgr = self.data_manager.backup_manager
        backups = backup_mgr.get_backup_history()
        
        if not backups:
            messagebox.showinfo("No Backups", "No backups available to restore from.")
            return
        
        # Show backup history and let user select
        self._show_backup_history()
    
    def _confirm_restore(self, backup: dict):
        """Confirm and perform restore from backup."""
        backup_mgr = self.data_manager.backup_manager
        
        # Get current database info for comparison - count from database directly
        try:
            cursor = self.data_manager.db.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM records")
            current_count = cursor.fetchone()[0]
        except:
            current_count = 0
        
        # Confirmation dialog
        date_str = backup["timestamp"].strftime("%B %d, %Y at %I:%M %p").lower()
        msg = (f"Restore from backup?\n\n"
               f"Backup: {date_str}\n"
               f"Contains: {backup['record_count']} records\n"
               f"Size: {backup['size_formatted']}\n\n"
               f"Current database: {current_count} records\n\n"
               f"⚠️ Your current data will be replaced.\n"
               f"A backup of current data will be created first.")
        
        if not messagebox.askyesno("Confirm Restore", msg, icon="warning"):
            return
        
        # Perform restore
        result = backup_mgr.restore_from_backup(backup["path"])
        
        if result["success"]:
            # Reload data from the restored database
            try:
                # Reload records from database
                self.data_manager.records_data = self.data_manager._load_records_from_db()
                # Update data structures
                self.data_manager.data["records"] = self.data_manager.records_data.get("records", [])
                self.data_manager.data["current_shift"] = self.data_manager.records_data.get("current_shift", {
                    "shift_start": None,
                    "shift_end": None,
                    "records": []
                })
                self.data_manager.data["shifts"] = self.data_manager.records_data.get("shifts", [])
                
                # Refresh the app display
                self.app.update_display()
                
                messagebox.showinfo("Restore Complete", 
                                   f"Database restored successfully!\n\n"
                                   f"Data has been reloaded from the backup.")
                logger.info(f"Database restored and reloaded: {backup['path']}")
            except Exception as e:
                logger.error(f"Error reloading data after restore: {e}", exc_info=True)
                messagebox.showwarning("Restore Complete", 
                                      f"Database restored successfully!\n\n"
                                      f"However, there was an error reloading the data.\n"
                                      f"Please restart the application to see the restored data.")
        else:
            messagebox.showerror("Restore Failed", f"Restore failed: {result['error']}")


class CanvasTable:
    """Reusable Canvas-based sortable table widget."""
    
    def _get_theme_colors(self, widget):
        """Get theme colors by traversing widget hierarchy to find app instance."""
        current = widget
        for _ in range(10):  # Limit traversal depth
            if hasattr(current, 'app') and hasattr(current.app, 'theme_colors'):
                return current.app.theme_colors
            if hasattr(current, 'parent'):
                current = current.parent
            elif hasattr(current, 'master'):
                current = current.master
            else:
                break
        # Default fallback colors
        return {
            "canvas_bg": "#f0f0f0",
            "button_bg": "#e1e1e1",
            "entry_bg": "white",
            "fg": "black",
            "border_color": "#acacac"
        }
    
    def __init__(self, parent, columns, sortable_columns=None, row_height=25, header_height=30, app=None):
        """
        Create a Canvas-based sortable table.
        
        Args:
            parent: Parent widget
            columns: List of (name, width, header_text) tuples or dict with 'name', 'width', 'text', 'sortable'
            sortable_columns: Set of column names that are sortable (None = all sortable)
            row_height: Height of each data row
            header_height: Height of header row
            app: Optional app instance for theme colors (if None, will try to find it)
        """
        self.parent = parent
        self.row_height = row_height
        self.header_height = header_height
        self.app = app  # Store app reference for theme colors
        
        # Parse columns
        self.columns = []
        self.column_widths = {}
        self.column_names = []
        self.sortable = sortable_columns if sortable_columns is not None else set()
        
        for col in columns:
            if isinstance(col, dict):
                name = col['name']
                width = col['width']
                text = col.get('text', name)
                sortable = col.get('sortable', True)
            else:
                name, width, text = col
                sortable = True
            
            self.columns.append({'name': name, 'width': width, 'text': text, 'sortable': sortable})
            self.column_widths[name] = width
            self.column_names.append(name)
            if sortable:
                self.sortable.add(name)
        
        # Table dimensions
        self.table_width = sum(self.column_widths.values())
        
        # Data storage
        self.rows_data = []  # List of row dicts: {'cells': {col: value}, 'is_total': bool, 'tags': [], 'cell_text_colors': {}}
        self.sort_column = None
        self.sort_reverse = False
        
        # Get theme colors - use app if provided, otherwise try to find it
        if self.app and hasattr(self.app, 'theme_colors'):
            theme_colors = self.app.theme_colors
        else:
            theme_colors = self._get_theme_colors(parent)
        canvas_bg = theme_colors.get("canvas_bg", "#f0f0f0")
        header_bg = theme_colors.get("button_bg", "#e1e1e1")
        data_bg = theme_colors.get("entry_bg", "white")
        text_fg = theme_colors.get("fg", "black")
        border_color = theme_colors.get("border_color", "#cccccc")  # Light grey for canvas borders
        
        # Store theme colors for use in drawing
        self.theme_colors = theme_colors
        
        # Create frame with scrollbar
        self.frame = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, bg=canvas_bg, highlightthickness=1, highlightbackground=border_color)
        self.scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        
        # Inner frame for content
        self.inner_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")
        
        # Configure scrolling
        def configure_scroll_region(event):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        
        def configure_canvas_width(event):
            canvas_width = event.width
            self.canvas.itemconfig(self.canvas_window, width=canvas_width)
        
        self.inner_frame.bind("<Configure>", configure_scroll_region)
        self.canvas.bind("<Configure>", configure_canvas_width)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # Create header canvas
        self.header_canvas = tk.Canvas(self.inner_frame, width=self.table_width, height=header_height,
                                      bg=header_bg, highlightthickness=0)
        self.header_canvas.pack(fill=tk.X)
        
        # Create data canvas
        self.data_canvas = tk.Canvas(self.inner_frame, width=self.table_width,
                                    bg=data_bg, highlightthickness=0)
        self.data_canvas.pack(fill=tk.BOTH, expand=True)
        
        # Bind mouse wheel scrolling
        def on_mousewheel(event):
            # Windows/Linux: event.delta is in multiples of 120
            # Mac: event.delta is in pixels
            if event.delta:
                delta = -1 * (event.delta / 120) if abs(event.delta) > 1 else -1 * event.delta
            else:
                delta = -1 if event.num == 4 else 1
            self.canvas.yview_scroll(int(delta), "units")
        
        # Bind mouse wheel scrolling to the frame (not individual canvases)
        # This ensures scrolling works even when mouse is over any part of the table
        def bind_mousewheel_to_canvas(widget):
            widget.bind("<MouseWheel>", on_mousewheel)
            widget.bind("<Button-4>", on_mousewheel)  # Linux scroll up
            widget.bind("<Button-5>", on_mousewheel)  # Linux scroll down
        
        # Bind to all components for comprehensive scrolling
        bind_mousewheel_to_canvas(self.frame)
        bind_mousewheel_to_canvas(self.canvas)
        bind_mousewheel_to_canvas(self.inner_frame)
        bind_mousewheel_to_canvas(self.header_canvas)
        bind_mousewheel_to_canvas(self.data_canvas)
        
        # Draw headers
        self._draw_headers()
        
        # Pack widgets
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _draw_headers(self):
        """Draw header row with clickable buttons."""
        self.header_canvas.delete("all")
        x = 0
        
        for col_info in self.columns:
            col_name = col_info['name']
            width = col_info['width']
            text = col_info['text']
            sortable = col_info.get('sortable', True)
            
            # Get theme colors
            header_bg = self.theme_colors.get("button_bg", "#e1e1e1")
            header_fg = self.theme_colors.get("fg", "black")
            border_color = self.theme_colors.get("border_color", "#acacac")
            
            # Draw header rectangle
            rect_id = self.header_canvas.create_rectangle(x, 0, x + width, self.header_height,
                                                         fill=header_bg, outline=border_color, width=1,
                                                         tags=f"header_{col_name}")
            
            # Add sort indicator if sorted
            display_text = text
            if col_name == self.sort_column and col_name in self.sortable:
                indicator = " ▼" if self.sort_reverse else " ▲"
                display_text = text + indicator
            
            # Draw text - left-align text columns, center numeric columns
            if col_name == 'body_part' or col_name == 'study_type' or col_name == 'procedure' or col_name == 'metric' or col_name == 'modality' or col_name == 'patient_class' or col_name == 'category':
                text_anchor = 'w'
                text_x = x + 4  # Small left padding
            else:
                text_anchor = 'center'
                text_x = x + width//2
            self.header_canvas.create_text(text_x, self.header_height//2,
                                         text=display_text, font=('Arial', 9, 'bold'),
                                         anchor=text_anchor, fill=header_fg, tags=f"header_{col_name}")
            
            # Make clickable if sortable
            if sortable and col_name in self.sortable:
                self.header_canvas.tag_bind(f"header_{col_name}", "<Button-1>",
                                          lambda e, c=col_name: self._on_header_click(c))
                self.header_canvas.tag_bind(f"header_{col_name}", "<Enter>",
                                          lambda e: self.header_canvas.config(cursor="hand2"))
                self.header_canvas.tag_bind(f"header_{col_name}", "<Leave>",
                                          lambda e: self.header_canvas.config(cursor=""))
            
            x += width
    
    def _on_header_click(self, col_name):
        """Handle header click for sorting."""
        if col_name not in self.sortable:
            return  # Column is not sortable
        
        if self.sort_column == col_name:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = col_name
            self.sort_reverse = False
        
        # Redraw headers to show sort indicator
        self._draw_headers()
        # Redraw data with new sort order
        self._draw_data()
    
    def _draw_data(self):
        """Draw data rows."""
        self.data_canvas.delete("all")
        
        # Sort rows if needed
        rows_to_draw = list(self.rows_data)
        if self.sort_column and self.sort_column in self.sortable:
            # Separate totals from regular rows
            regular_rows = [r for r in rows_to_draw if not r.get('is_total', False)]
            total_rows = [r for r in rows_to_draw if r.get('is_total', False)]
            
            # Sort regular rows
            def get_sort_value(row):
                val = row['cells'].get(self.sort_column, "")
                # Try numeric sort first
                try:
                    if isinstance(val, str):
                        # Remove parentheses content for duration strings
                        val_clean = re.sub(r'\s*\(\d+\)$', '', val).strip()
                        if val_clean and val_clean != "-":
                            # Try parsing as duration (Xh Ym Zs)
                            total_seconds = 0
                            hours = re.search(r'(\d+)h', val_clean)
                            minutes = re.search(r'(\d+)m', val_clean)
                            seconds = re.search(r'(\d+)s', val_clean)
                            if hours:
                                total_seconds += int(hours.group(1)) * 3600
                            if minutes:
                                total_seconds += int(minutes.group(1)) * 60
                            if seconds:
                                total_seconds += int(seconds.group(1))
                            return total_seconds if total_seconds > 0 else float('inf')
                    return float(val)
                except:
                    pass
                return str(val).lower()
            
            regular_rows.sort(key=get_sort_value, reverse=self.sort_reverse)
            rows_to_draw = regular_rows + total_rows
        else:
            # Keep totals at bottom
            regular_rows = [r for r in rows_to_draw if not r.get('is_total', False)]
            total_rows = [r for r in rows_to_draw if r.get('is_total', False)]
            rows_to_draw = regular_rows + total_rows
        
        # Get theme colors once (cache for performance)
        data_bg = self.theme_colors.get("entry_bg", "white")
        data_fg = self.theme_colors.get("fg", "black")
        border_color = self.theme_colors.get("border_color", "#acacac")
        total_bg = self.theme_colors.get("button_bg", "#e1e1e1")
        
            # Draw rows - draw all rows (for now, optimization can be added later if needed)
        y = 0
        for row in rows_to_draw:
            cells = row['cells']
            is_total = row.get('is_total', False)
            cell_colors = row.get('cell_colors', {})  # Optional per-cell background colors
            cell_text_colors = row.get('cell_text_colors', {})  # Optional per-cell text colors
            
            x = 0
            for col_info in self.columns:
                col_name = col_info['name']
                width = col_info['width']
                value = cells.get(col_name, "")
                
                # Get cell color (for color coding) - use theme colors if not specified
                if col_name not in cell_colors:
                    cell_color = total_bg if is_total else data_bg
                else:
                    cell_color = cell_colors.get(col_name)
                
                # Get text color - use cell_text_colors if specified, otherwise use theme default
                text_color = cell_text_colors.get(col_name, data_fg)
                
                # Draw cell
                self.data_canvas.create_rectangle(x, y, x + width, y + self.row_height,
                                                 fill=cell_color, outline=border_color, width=1)
                
                # Draw text - support partial coloring for dollar amounts
                font = ('Arial', 9, 'bold') if is_total else ('Arial', 9)
                value_str = str(value)
                
                # Check if we need partial coloring (when text_color is specified and value contains $)
                if col_name in cell_text_colors and '$' in value_str:
                    # Parse out the dollar amount and render separately
                    import re
                    # Find dollar amount pattern ($number with optional commas)
                    dollar_match = re.search(r'(\$\d[\d,]*\.?\d*)', value_str)
                    if dollar_match:
                        dollar_amount = dollar_match.group(1)
                        dollar_start = dollar_match.start()
                        dollar_end = dollar_match.end()
                        
                        # Split text into parts
                        before_dollar = value_str[:dollar_start]
                        after_dollar = value_str[dollar_end:]
                        
                        # Get text metrics for positioning
                        test_text = self.data_canvas.create_text(0, 0, text=before_dollar, font=font, anchor='w')
                        before_bbox = self.data_canvas.bbox(test_text)
                        before_width = before_bbox[2] - before_bbox[0] if before_bbox else 0
                        self.data_canvas.delete(test_text)
                        
                        test_text = self.data_canvas.create_text(0, 0, text=dollar_amount, font=font, anchor='w')
                        dollar_bbox = self.data_canvas.bbox(test_text)
                        dollar_width = dollar_bbox[2] - dollar_bbox[0] if dollar_bbox else 0
                        self.data_canvas.delete(test_text)
                        
                        # Calculate starting x position (center alignment)
                        total_width = before_width + dollar_width
                        if after_dollar:
                            test_text = self.data_canvas.create_text(0, 0, text=after_dollar, font=font, anchor='w')
                            after_bbox = self.data_canvas.bbox(test_text)
                            after_width = after_bbox[2] - after_bbox[0] if after_bbox else 0
                            self.data_canvas.delete(test_text)
                            total_width += after_width
                        
                        start_x = x + (width - total_width) // 2
                        text_y = y + self.row_height // 2
                        
                        # Draw text parts
                        if before_dollar:
                            self.data_canvas.create_text(start_x, text_y, text=before_dollar, font=font, anchor='w', fill=data_fg)
                            start_x += before_width
                        
                        self.data_canvas.create_text(start_x, text_y, text=dollar_amount, font=font, anchor='w', fill=text_color)
                        start_x += dollar_width
                        
                        if after_dollar:
                            self.data_canvas.create_text(start_x, text_y, text=after_dollar, font=font, anchor='w', fill=data_fg)
                    else:
                        # No dollar match, render normally
                        # Left-align first column (typically names/categories), center others
                        if col_name == 'body_part' or col_name == 'study_type' or col_name == 'procedure' or col_name == 'metric' or col_name == 'modality' or col_name == 'patient_class' or col_name == 'category':
                            anchor = 'w'
                            text_x = x + 4
                        else:
                            anchor = 'center'
                            text_x = x + width//2
                        self.data_canvas.create_text(text_x, y + self.row_height//2,
                                                   text=value_str, font=font, anchor=anchor, fill=text_color)
                else:
                    # Normal rendering - entire text in one color
                    # Left-align first column (typically names/categories), center others
                    if col_name == 'body_part' or col_name == 'study_type' or col_name == 'procedure' or col_name == 'metric' or col_name == 'modality' or col_name == 'patient_class' or col_name == 'category':
                        anchor = 'w'
                        text_x = x + 4  # Small left padding
                    else:
                        anchor = 'center'
                        text_x = x + width//2
                    self.data_canvas.create_text(text_x, y + self.row_height//2,
                                               text=value_str, font=font, anchor=anchor, fill=text_color)
                x += width
            
            y += self.row_height
        
        # Set canvas height to accommodate all rows
        self.data_canvas.config(height=y)
    
    def add_row(self, cells, is_total=False, cell_colors=None, cell_text_colors=None):
        """Add a row of data (doesn't redraw - call update_data() or _draw_data() when done adding all rows)."""
        self.rows_data.append({
            'cells': cells,
            'is_total': is_total,
            'cell_colors': cell_colors or {},
            'cell_text_colors': cell_text_colors or {}
        })
    
    def update_data(self):
        """Update the display after adding rows - this triggers a single redraw."""
        self._draw_data()
    
    def clear(self):
        """Clear all rows but keep headers visible."""
        self.rows_data = []
        self.sort_column = None
        self.sort_reverse = False
        # Clear only data canvas, keep headers
        self.data_canvas.delete("all")
        # Redraw headers to ensure they're visible
        self._draw_headers()
    
    def update_theme(self):
        """Update theme colors and redraw."""
        # Get fresh theme colors
        if self.app and hasattr(self.app, 'theme_colors'):
            self.theme_colors = self.app.theme_colors
        else:
            self.theme_colors = self._get_theme_colors(self.parent)
        
        # Update canvas backgrounds
        canvas_bg = self.theme_colors.get("canvas_bg", "#f0f0f0")
        header_bg = self.theme_colors.get("button_bg", "#e1e1e1")
        data_bg = self.theme_colors.get("entry_bg", "white")
        border_color = self.theme_colors.get("border_color", "#acacac")
        
        self.canvas.config(bg=canvas_bg, highlightbackground=border_color)
        self.header_canvas.config(bg=header_bg)
        self.data_canvas.config(bg=data_bg)
        
        # Redraw with new colors
        self._draw_headers()
        self._draw_data()
    
    def update_theme(self):
        """Update theme colors and redraw."""
        # Get fresh theme colors
        if self.app and hasattr(self.app, 'theme_colors'):
            self.theme_colors = self.app.theme_colors
        else:
            self.theme_colors = self._get_theme_colors(self.parent)
        
        # Update canvas backgrounds
        canvas_bg = self.theme_colors.get("canvas_bg", "#f0f0f0")
        header_bg = self.theme_colors.get("button_bg", "#e1e1e1")
        data_bg = self.theme_colors.get("entry_bg", "white")
        border_color = self.theme_colors.get("border_color", "#acacac")
        
        self.canvas.config(bg=canvas_bg, highlightbackground=border_color)
        self.header_canvas.config(bg=header_bg)
        self.data_canvas.config(bg=data_bg)
        
        # Redraw with new colors
        self._draw_headers()
        self._draw_data()
    
    def pack(self, **kwargs):
        """Pack the table frame."""
        self.frame.pack(**kwargs)
    
    def pack_forget(self):
        """Unpack the table frame."""
        self.frame.pack_forget()


class StatisticsWindow:
    """Statistics modal window for detailed stats."""
    
    def __init__(self, parent, data_manager: RVUData, app: RVUCounterApp):
        self.parent = parent
        self.data_manager = data_manager
        self.app = app
        
        # Create modal window
        self.window = tk.Toplevel(parent)
        self.window.title("Statistics")
        self.window.transient(parent)
        self.window.grab_set()
        
        # Make window larger for detailed stats
        self.window.geometry("1350x800")
        self.window.minsize(800, 500)
        
        # Restore saved position or center on screen
        positions = self.data_manager.data.get("window_positions", {})
        if "statistics" in positions:
            pos = positions["statistics"]
            x, y = pos['x'], pos['y']
            # Validate position before applying
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Statistics window position ({x}, {y}) is off-screen, finding nearest monitor")
                x, y = find_nearest_monitor_for_window(x, y, 1350, 800)
            self.window.geometry(f"1350x800+{x}+{y}")
        else:
            # Center on primary monitor using Windows API
            try:
                primary = get_primary_monitor_bounds()
                x = primary[0] + (primary[2] - primary[0] - 1350) // 2
                y = primary[1] + (primary[3] - primary[1] - 800) // 2
                self.window.geometry(f"1350x800+{x}+{y}")
            except:
                # Fallback: use parent's screen (old behavior)
                parent.update_idletasks()
                screen_width = parent.winfo_screenwidth()
                screen_height = parent.winfo_screenheight()
                x = (screen_width - 1350) // 2
                y = (screen_height - 800) // 2
                self.window.geometry(f"1350x800+{x}+{y}")
        
        # Track position for saving
        self.last_saved_x = self.window.winfo_x()
        self.last_saved_y = self.window.winfo_y()
        
        # Bind window events
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.window.bind("<Configure>", self.on_configure)
        self.window.bind("<ButtonRelease-1>", self.on_statistics_drag_end)
        
        # Apply theme
        self.apply_theme()
        
        # Create UI
        self.create_ui()
    
    def create_ui(self):
        """Create the statistics UI."""
        # State variables
        self.selected_period = tk.StringVar(value="current_shift")
        self.view_mode = tk.StringVar(value="efficiency")
        self.selected_shift_index = None  # For shift list selection
        
        # Projection variables
        self.projection_days = tk.IntVar(value=14)
        self.projection_extra_days = tk.IntVar(value=0)
        self.projection_extra_hours = tk.IntVar(value=0)
        
        # Comparison mode variables
        self.comparison_shift1_index = None  # Index in shifts list for first shift (current/newer)
        self.comparison_shift2_index = None  # Index in shifts list for second shift (prior/older)
        self.comparison_graph_mode = tk.StringVar(value="accumulation")  # accumulation or average
        
        # Track previous period to show/hide custom date frame
        self.previous_period = "current_shift"
        
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create horizontal paned window (left panel + main content)
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # === LEFT PANEL (Selection) ===
        left_panel = ttk.Frame(paned, padding="5")
        paned.add(left_panel, weight=0)
        
        # Shift Analysis Section
        shift_frame = ttk.LabelFrame(left_panel, text="Shift Analysis", padding="8")
        shift_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Current shift radio - disable if no shift is running
        self.current_shift_radio = ttk.Radiobutton(shift_frame, text="Current Shift", variable=self.selected_period, 
                       value="current_shift", command=self.refresh_data)
        self.current_shift_radio.pack(anchor=tk.W, pady=2)
        
        # Disable current shift option if no shift is running
        if not self.app.is_running:
            self.current_shift_radio.config(state=tk.DISABLED)
            self.selected_period.set("prior_shift")  # Default to prior shift instead
        
        ttk.Radiobutton(shift_frame, text="Prior Shift", variable=self.selected_period,
                       value="prior_shift", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        
        # Projection Section (only visible in compensation view)
        projection_frame = ttk.LabelFrame(left_panel, text="Projection", padding="8")
        projection_frame.pack(fill=tk.X, pady=(0, 10))
        self.projection_frame = projection_frame  # Store reference to show/hide
        
        self.projection_radio = ttk.Radiobutton(projection_frame, text="Monthly Projection", variable=self.selected_period,
                       value="projection", command=self.refresh_data)
        self.projection_radio.pack(anchor=tk.W, pady=2)
        # Initially hide projection section (only show when compensation view is selected)
        projection_frame.pack_forget()
        
        # Historical Section
        history_frame = ttk.LabelFrame(left_panel, text="Historical", padding="8")
        history_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Radiobutton(history_frame, text="This Work Week", variable=self.selected_period,
                       value="this_work_week", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last Work Week", variable=self.selected_period,
                       value="last_work_week", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="This Month", variable=self.selected_period,
                       value="this_month", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last Month", variable=self.selected_period,
                       value="last_month", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last 3 Months", variable=self.selected_period,
                       value="last_3_months", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last Year", variable=self.selected_period,
                       value="last_year", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="All Time", variable=self.selected_period,
                       value="all_time", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        
        # Custom date range option
        ttk.Radiobutton(history_frame, text="Custom Date Range", variable=self.selected_period,
                       value="custom_date_range", command=self.on_custom_date_selected).pack(anchor=tk.W, pady=2)
        
        # Custom date range input frame (hidden by default)
        self.custom_date_frame = ttk.Frame(history_frame)
        
        # Start date with calendar button
        ttk.Label(self.custom_date_frame, text="From:").grid(row=0, column=0, padx=(20, 5), pady=2, sticky=tk.W)
        start_date_frame = ttk.Frame(self.custom_date_frame)
        start_date_frame.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)
        
        self.custom_start_date = tk.StringVar(value=datetime.now().strftime("%m/%d/%Y"))
        self.custom_start_entry = ttk.Entry(start_date_frame, textvariable=self.custom_start_date, width=12)
        self.custom_start_entry.pack(side=tk.LEFT)
        
        if HAS_TKCALENDAR:
            def open_start_calendar():
                cal_dialog = tk.Toplevel(self.window)
                cal_dialog.title("Select Start Date")
                cal_dialog.transient(self.window)
                cal_dialog.grab_set()
                
                try:
                    # Parse current date or use today
                    current_val = self.custom_start_date.get()
                    try:
                        current_dt = datetime.strptime(current_val, "%m/%d/%Y")
                    except:
                        current_dt = datetime.now()
                    
                    # Apply theme colors to calendar
                    colors = self.app.get_theme_colors()
                    is_dark = colors['bg'] == '#2b2b2b'
                    
                    if is_dark:
                        # Dark mode calendar styling
                        cal = Calendar(cal_dialog, selectmode='day', 
                                     year=current_dt.year, month=current_dt.month, day=current_dt.day,
                                     background='#2b2b2b',
                                     foreground='white',
                                     headersbackground='#1e1e1e',
                                     headersforeground='white',
                                     selectbackground='#0078d7',
                                     selectforeground='white',
                                     normalbackground='#2b2b2b',
                                     normalforeground='white',
                                     weekendbackground='#353535',
                                     weekendforeground='white',
                                     othermonthforeground='#666666',
                                     othermonthbackground='#2b2b2b',
                                     othermonthweforeground='#666666',
                                     othermonthwebackground='#2b2b2b')
                    else:
                        # Light mode calendar (default)
                        cal = Calendar(cal_dialog, selectmode='day', 
                                     year=current_dt.year, month=current_dt.month, day=current_dt.day)
                    cal.pack(padx=10, pady=10)
                    
                    def set_start_date():
                        selected_date = cal.selection_get()
                        self.custom_start_date.set(selected_date.strftime("%m/%d/%Y"))
                        cal_dialog.destroy()
                        self.on_date_change()
                    
                    btn_frame = ttk.Frame(cal_dialog)
                    btn_frame.pack(pady=5)
                    ttk.Button(btn_frame, text="OK", command=set_start_date).pack(side=tk.LEFT, padx=5)
                    ttk.Button(btn_frame, text="Cancel", command=cal_dialog.destroy).pack(side=tk.LEFT, padx=5)
                    
                    # Position dialog next to the button
                    cal_dialog.update_idletasks()
                    button_x = start_date_frame.winfo_rootx() + start_cal_btn.winfo_x() + start_cal_btn.winfo_width()
                    button_y = start_date_frame.winfo_rooty() + start_cal_btn.winfo_y()
                    dialog_width = cal_dialog.winfo_width()
                    dialog_height = cal_dialog.winfo_height()
                    
                    # Position to the right of button, or above if not enough space
                    screen_width = self.window.winfo_screenwidth()
                    if button_x + dialog_width + 10 > screen_width:
                        # Position above button
                        cal_dialog.geometry(f"+{button_x - dialog_width // 2}+{button_y - dialog_height - 5}")
                    else:
                        # Position to the right
                        cal_dialog.geometry(f"+{button_x + 5}+{button_y}")
                    
                except Exception as e:
                    logger.error(f"Error opening calendar: {e}")
                    cal_dialog.destroy()
            
            start_cal_btn = ttk.Button(start_date_frame, text="📅", width=3, command=open_start_calendar)
            start_cal_btn.pack(side=tk.LEFT, padx=(2, 0))
        
        self.custom_start_entry.bind("<FocusOut>", lambda e: self.on_date_change())
        
        # End date with calendar button
        ttk.Label(self.custom_date_frame, text="To:").grid(row=1, column=0, padx=(20, 5), pady=2, sticky=tk.W)
        end_date_frame = ttk.Frame(self.custom_date_frame)
        end_date_frame.grid(row=1, column=1, padx=5, pady=2, sticky=tk.W)
        
        self.custom_end_date = tk.StringVar(value=datetime.now().strftime("%m/%d/%Y"))
        self.custom_end_entry = ttk.Entry(end_date_frame, textvariable=self.custom_end_date, width=12)
        self.custom_end_entry.pack(side=tk.LEFT)
        
        if HAS_TKCALENDAR:
            def open_end_calendar():
                cal_dialog = tk.Toplevel(self.window)
                cal_dialog.title("Select End Date")
                cal_dialog.transient(self.window)
                cal_dialog.grab_set()
                
                try:
                    # Parse current date or use today
                    current_val = self.custom_end_date.get()
                    try:
                        current_dt = datetime.strptime(current_val, "%m/%d/%Y")
                    except:
                        current_dt = datetime.now()
                    
                    # Apply theme colors to calendar
                    colors = self.app.get_theme_colors()
                    is_dark = colors['bg'] == '#2b2b2b'
                    
                    if is_dark:
                        # Dark mode calendar styling
                        cal = Calendar(cal_dialog, selectmode='day',
                                     year=current_dt.year, month=current_dt.month, day=current_dt.day,
                                     background='#2b2b2b',
                                     foreground='white',
                                     headersbackground='#1e1e1e',
                                     headersforeground='white',
                                     selectbackground='#0078d7',
                                     selectforeground='white',
                                     normalbackground='#2b2b2b',
                                     normalforeground='white',
                                     weekendbackground='#353535',
                                     weekendforeground='white',
                                     othermonthforeground='#666666',
                                     othermonthbackground='#2b2b2b',
                                     othermonthweforeground='#666666',
                                     othermonthwebackground='#2b2b2b')
                    else:
                        # Light mode calendar (default)
                        cal = Calendar(cal_dialog, selectmode='day',
                                     year=current_dt.year, month=current_dt.month, day=current_dt.day)
                    cal.pack(padx=10, pady=10)
                    
                    def set_end_date():
                        selected_date = cal.selection_get()
                        self.custom_end_date.set(selected_date.strftime("%m/%d/%Y"))
                        cal_dialog.destroy()
                        self.on_date_change()
                    
                    btn_frame = ttk.Frame(cal_dialog)
                    btn_frame.pack(pady=5)
                    ttk.Button(btn_frame, text="OK", command=set_end_date).pack(side=tk.LEFT, padx=5)
                    ttk.Button(btn_frame, text="Cancel", command=cal_dialog.destroy).pack(side=tk.LEFT, padx=5)
                    
                    # Position dialog next to the button
                    cal_dialog.update_idletasks()
                    button_x = end_date_frame.winfo_rootx() + end_cal_btn.winfo_x() + end_cal_btn.winfo_width()
                    button_y = end_date_frame.winfo_rooty() + end_cal_btn.winfo_y()
                    dialog_width = cal_dialog.winfo_width()
                    dialog_height = cal_dialog.winfo_height()
                    
                    # Position to the right of button, or above if not enough space
                    screen_width = self.window.winfo_screenwidth()
                    if button_x + dialog_width + 10 > screen_width:
                        # Position above button
                        cal_dialog.geometry(f"+{button_x - dialog_width // 2}+{button_y - dialog_height - 5}")
                    else:
                        # Position to the right
                        cal_dialog.geometry(f"+{button_x + 5}+{button_y}")
                    
                except Exception as e:
                    logger.error(f"Error opening calendar: {e}")
                    cal_dialog.destroy()
            
            end_cal_btn = ttk.Button(end_date_frame, text="📅", width=3, command=open_end_calendar)
            end_cal_btn.pack(side=tk.LEFT, padx=(2, 0))
        
        self.custom_end_entry.bind("<FocusOut>", lambda e: self.on_date_change())
        
        # Initially hide the custom date frame (don't pack it yet)
        # It will be shown when custom_date_range is selected
        
        # Comparison Section (only visible in comparison view)
        comparison_frame = ttk.LabelFrame(left_panel, text="Shift Comparison", padding="8")
        self.comparison_frame = comparison_frame  # Store reference to show/hide
        
        ttk.Label(comparison_frame, text="Shift 1:", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(0, 2))
        self.comparison_shift1_var = tk.StringVar()
        self.comparison_shift1_combo = ttk.Combobox(comparison_frame, state="readonly", width=25)
        self.comparison_shift1_combo.pack(fill=tk.X, pady=(0, 10))
        self.comparison_shift1_combo.bind("<<ComboboxSelected>>", lambda e: self.on_comparison_shift_selected(e))
        
        ttk.Label(comparison_frame, text="Shift 2:", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(0, 2))
        self.comparison_shift2_var = tk.StringVar()
        self.comparison_shift2_combo = ttk.Combobox(comparison_frame, state="readonly", width=25)
        self.comparison_shift2_combo.pack(fill=tk.X, pady=(0, 10))
        self.comparison_shift2_combo.bind("<<ComboboxSelected>>", lambda e: self.on_comparison_shift_selected(e))
        
        # Initially hide comparison section (only show when comparison view is selected)
        comparison_frame.pack_forget()
        
        # Shifts List Section (with delete capability)
        shifts_frame = ttk.LabelFrame(left_panel, text="All Shifts", padding="8")
        shifts_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Scrollable list of shifts
        canvas_bg = getattr(self, 'theme_canvas_bg', 'SystemButtonFace')
        self.shifts_canvas = tk.Canvas(shifts_frame, width=210, highlightthickness=0, bg=canvas_bg)
        shifts_scrollbar = ttk.Scrollbar(shifts_frame, orient="vertical", command=self.shifts_canvas.yview)
        self.shifts_list_frame = ttk.Frame(self.shifts_canvas)
        
        self.shifts_list_frame.bind("<Configure>", 
            lambda e: self.shifts_canvas.configure(scrollregion=self.shifts_canvas.bbox("all")))
        self.shifts_canvas.create_window((0, 0), window=self.shifts_list_frame, anchor="nw")
        self.shifts_canvas.configure(yscrollcommand=shifts_scrollbar.set)
        
        self.shifts_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        shifts_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # === RIGHT PANEL (Main Content) ===
        right_panel = ttk.Frame(paned, padding="5")
        paned.add(right_panel, weight=1)
        self.right_panel = right_panel  # Store reference for projection settings
        
        # View mode toggle
        view_frame = ttk.Frame(right_panel)
        view_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(view_frame, text="View:", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(view_frame, text="Efficiency", variable=self.view_mode,
                       value="efficiency", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="Compensation", variable=self.view_mode,
                       value="compensation", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Hour", variable=self.view_mode,
                       value="by_hour", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Modality", variable=self.view_mode,
                       value="by_modality", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Patient Class", variable=self.view_mode,
                       value="by_patient_class", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Study Type", variable=self.view_mode,
                       value="by_study_type", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Body Part", variable=self.view_mode,
                       value="by_body_part", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="All Studies", variable=self.view_mode,
                       value="all_studies", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="Comparison", variable=self.view_mode,
                       value="comparison", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="Summary", variable=self.view_mode,
                       value="summary", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        
        # Period label with checkboxes for efficiency view
        period_frame = ttk.Frame(right_panel)
        period_frame.pack(fill=tk.X, pady=(0, 10))
        self.period_label = ttk.Label(period_frame, text="", font=("Arial", 12, "bold"))
        self.period_label.pack(side=tk.LEFT, anchor=tk.W)
        
        # Frame for efficiency view controls (study count mode and color coding)
        efficiency_controls_frame = ttk.Frame(period_frame)
        efficiency_controls_frame.pack(side=tk.RIGHT, anchor=tk.E)
        
        # Study count display mode (average vs total) - to the left of color coding
        self.study_count_mode_frame = ttk.Frame(efficiency_controls_frame)
        self.study_count_mode_frame.pack(side=tk.LEFT, padx=(0, 15))
        
        # Load saved value or default to "average"
        saved_study_count_mode = self.data_manager.data.get("settings", {}).get("efficiency_study_count_mode", "average")
        self.study_count_mode = tk.StringVar(value=saved_study_count_mode)  # Options: "average", "total"
        self.study_count_radio_buttons = []  # Store references to radio buttons
        
        # Checkboxes for efficiency color coding (will be shown/hidden based on view mode)
        self.efficiency_checkboxes_frame = ttk.Frame(efficiency_controls_frame)
        self.efficiency_checkboxes_frame.pack(side=tk.LEFT, anchor=tk.E)
        
        # Efficiency color coding options (created when efficiency view is shown)
        # Load saved value or default to "duration"
        saved_heatmap_mode = self.data_manager.data.get("settings", {}).get("efficiency_heatmap_mode", "duration")
        self.heatmap_mode = tk.StringVar(value=saved_heatmap_mode)  # Options: "none", "duration", "count"
        self.heatmap_radio_buttons = []  # Store references to radio buttons
        
        # Data table frame
        self.table_frame = ttk.Frame(right_panel)
        self.table_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create Treeview for data display (for all views except efficiency)
        self.tree = ttk.Treeview(self.table_frame, show="headings")
        self.tree_scrollbar_y = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
        self.tree_scrollbar_x = ttk.Scrollbar(self.table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.tree_scrollbar_y.set, xscrollcommand=self.tree_scrollbar_x.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree_scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Efficiency trees will be created dynamically when needed
        self.efficiency_night_tree = None
        self.efficiency_day_tree = None
        self.efficiency_frame = None
        
        # Summary frame at bottom
        summary_frame = ttk.LabelFrame(right_panel, text="Summary", padding="10")
        summary_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.summary_label = ttk.Label(summary_frame, text="", font=("Arial", 10))
        self.summary_label.pack(anchor=tk.W)
        
        # Bottom button row
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(button_frame, text="Refresh", command=self.refresh_data, width=12).pack(side=tk.LEFT, padx=2)
        
        # Partial shifts detected button (hidden by default, shown when partial shifts detected)
        self.partial_shifts_btn = ttk.Button(button_frame, text="⚠ Partial Shifts", 
                                             command=self.show_partial_shifts_dialog, width=14)
        # Don't pack yet - will be shown if partial shifts are detected
        
        # Combine shifts button (always visible)
        ttk.Button(button_frame, text="Combine Shifts", 
                  command=self.show_combine_shifts_dialog, width=14).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(button_frame, text="Close", command=self.on_closing, width=12).pack(side=tk.RIGHT, padx=2)
        
        # Center frame for backup buttons
        center_frame = ttk.Frame(button_frame)
        center_frame.pack(expand=True)
        ttk.Button(center_frame, text="Backup Data", command=self.backup_study_data, width=16).pack(side=tk.LEFT, padx=2)
        ttk.Button(center_frame, text="Load Backup Data", command=self.load_backup_data, width=16).pack(side=tk.LEFT, padx=2)
        
        # Initial data load
        self.populate_shifts_list()
        self.refresh_data()
        
        # Check for partial shifts and show button if detected
        self.update_partial_shifts_button()
    
    def get_all_shifts(self) -> List[dict]:
        """Get all shifts from records, sorted by date (newest first)."""
        shifts = []
        
        # Get historical shifts from the "shifts" array
        historical_shifts = self.data_manager.data.get("shifts", [])
        for shift in historical_shifts:
            shift_copy = shift.copy()
            # Extract date from shift_start for display
            try:
                start = datetime.fromisoformat(shift.get("shift_start", ""))
                shift_copy["date"] = start.strftime("%Y-%m-%d")
            except:
                shift_copy["date"] = "Unknown"
            shifts.append(shift_copy)
        
        # Add current shift only if it's actually running (has shift_start but NO shift_end)
        current_shift = self.data_manager.data.get("current_shift", {})
        shift_is_active = current_shift.get("shift_start") and not current_shift.get("shift_end")
        if current_shift.get("records") and shift_is_active:
            shifts.append({
                "date": "current",
                "shift_start": current_shift.get("shift_start", ""),
                "shift_end": current_shift.get("shift_end", ""),
                "records": current_shift.get("records", []),
                "is_current": True
            })
        
        # Sort by shift_start (newest first)
        def sort_key(s):
            if s.get("is_current"):
                return datetime.max
            try:
                return datetime.fromisoformat(s.get("shift_start", ""))
            except:
                return datetime.min
        
        shifts.sort(key=sort_key, reverse=True)
        return shifts
    
    def populate_shifts_list(self):
        """Populate the shifts list in left panel."""
        # Clear existing
        for widget in self.shifts_list_frame.winfo_children():
            widget.destroy()
        
        shifts = self.get_all_shifts()
        now = datetime.now()
        
        for i, shift in enumerate(shifts):
            shift_frame = ttk.Frame(self.shifts_list_frame)
            shift_frame.pack(fill=tk.X, pady=1)
            
            # Format shift label
            if shift.get("is_current"):
                label_text = "Current Shift"
            else:
                try:
                    start = datetime.fromisoformat(shift.get("shift_start", ""))
                    
                    # Round down time to nearest hour (e.g., 10:23pm -> 10pm)
                    start_rounded = start.replace(minute=0, second=0, microsecond=0)
                    
                    # Calculate days ago
                    days_diff = (now.date() - start_rounded.date()).days
                    
                    # Format label based on how recent it is
                    if days_diff == 0:
                        label_text = "Today"
                    elif days_diff == 1:
                        label_text = "Yesterday"
                    elif days_diff <= 7:
                        # Show day name + xd ago (e.g., "Friday 2d ago")
                        day_name = start_rounded.strftime("%A")
                        label_text = f"{day_name} {days_diff}d ago"
                    else:
                        # Older shifts: show date with rounded time
                        label_text = start_rounded.strftime("%m/%d %I%p").lower().replace(":00", "")
                except:
                    label_text = shift.get("date", "Unknown")
            
            # Study count and RVU
            records = shift.get("records", [])
            count = len(records)
            total_rvu = sum(r.get("rvu", 0) for r in records)
            
            # Shift button (clickable frame with left-justified name and right-justified count/RVU)
            btn_frame = ttk.Frame(shift_frame)
            btn_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
            
            # Left-justified shift name
            name_label = ttk.Label(btn_frame, text=label_text, anchor=tk.W, cursor="hand2")
            name_label.pack(side=tk.LEFT)
            
            # Spacer to push count to the right
            spacer = ttk.Frame(btn_frame)
            spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
            
            # Right-justified count and RVU
            count_label = ttk.Label(btn_frame, text=f"({count}, {total_rvu:.1f} RVU)", anchor=tk.E, cursor="hand2")
            count_label.pack(side=tk.RIGHT)
            
            # Make the entire frame clickable
            def select_shift_cmd(event, idx=i):
                self.select_shift(idx)
            btn_frame.bind("<Button-1>", select_shift_cmd)
            name_label.bind("<Button-1>", select_shift_cmd)
            count_label.bind("<Button-1>", select_shift_cmd)
            spacer.bind("<Button-1>", select_shift_cmd)
            
            # Delete button (subtle, small)
            if not shift.get("is_current"):
                colors = self.app.get_theme_colors()
                del_btn = tk.Label(shift_frame, text="×", font=("Arial", 8), 
                                   fg=colors["delete_btn_fg"], bg=colors["delete_btn_bg"],
                                   cursor="hand2", width=2)
                del_btn.shift_idx = i
                del_btn.bind("<Button-1>", lambda e, btn=del_btn: self.confirm_delete_shift(btn.shift_idx))
                del_btn.bind("<Enter>", lambda e, btn=del_btn: btn.config(bg=colors["delete_btn_hover"]))
                del_btn.bind("<Leave>", lambda e, btn=del_btn: btn.config(bg=colors["delete_btn_bg"]))
                del_btn.pack(side=tk.LEFT)
    
    def select_shift(self, shift_index: int):
        """Select a specific shift from the list."""
        self.selected_shift_index = shift_index
        self.selected_period.set("specific_shift")
        self.refresh_data()
    
    def confirm_delete_shift(self, shift_index: int):
        """Confirm and delete a shift."""
        shifts = self.get_all_shifts()
        if shift_index >= len(shifts):
            return
        
        shift = shifts[shift_index]
        if shift.get("is_current"):
            return  # Can't delete current shift from here
        
        # Format confirmation message
        try:
            start = datetime.fromisoformat(shift.get("shift_start", ""))
            date_str = start.strftime("%B %d, %Y at %I:%M %p")
        except:
            date_str = shift.get("date", "Unknown date")
        
        records = shift.get("records", [])
        rvu = sum(r.get("rvu", 0) for r in records)
        
        result = messagebox.askyesno(
            "Delete Shift?",
            f"Delete shift from {date_str}?\n\n"
            f"This will remove {len(records)} studies ({rvu:.1f} RVU).\n\n"
            "This action cannot be undone.",
            parent=self.window
        )
        
        if result:
            self.delete_shift(shift_index)
    
    def delete_shift(self, shift_index: int):
        """Delete a shift from records."""
        shifts = self.get_all_shifts()
        if shift_index >= len(shifts):
            return
        
        shift = shifts[shift_index]
        if shift.get("is_current"):
            return
        
        shift_start = shift.get("shift_start")
        
        # Delete from database first (find by shift_start)
        try:
            cursor = self.data_manager.db.conn.cursor()
            cursor.execute('SELECT id FROM shifts WHERE shift_start = ? AND is_current = 0', (shift_start,))
            row = cursor.fetchone()
            if row:
                self.data_manager.db.delete_shift(row[0])
        except Exception as e:
            logger.error(f"Error deleting shift from database: {e}")
        
        # Find and remove from the in-memory shifts array
        historical_shifts = self.data_manager.data.get("shifts", [])
        for i, s in enumerate(historical_shifts):
            if s.get("shift_start") == shift_start:
                historical_shifts.pop(i)
                # Also update records_data
                if "shifts" in self.data_manager.records_data:
                    for j, rs in enumerate(self.data_manager.records_data["shifts"]):
                        if rs.get("shift_start") == shift_start:
                            self.data_manager.records_data["shifts"].pop(j)
                            break
                logger.info(f"Deleted shift starting {shift_start}")
                break
        
        # Refresh UI
        self.populate_shifts_list()
        self.refresh_data()
    
    def _format_date_range(self, start: datetime, end: datetime) -> str:
        """Format a date range as MM/DD/YYYY - MM/DD/YYYY."""
        start_str = start.strftime("%m/%d/%Y")
        end_str = end.strftime("%m/%d/%Y")
        return f"{start_str} - {end_str}"
    
    def get_records_for_period(self) -> Tuple[List[dict], str]:
        """Get records for the selected period. Returns (records, period_description)."""
        period = self.selected_period.get()
        now = datetime.now()
        
        if period == "current_shift":
            records = self.data_manager.data.get("current_shift", {}).get("records", [])
            # Get shift start time if available
            shift_start_str = self.data_manager.data.get("current_shift", {}).get("shift_start")
            if shift_start_str:
                try:
                    start = datetime.fromisoformat(shift_start_str)
                    date_range = self._format_date_range(start, now)
                    return records, f"Current Shift - {date_range}"
                except:
                    pass
            return records, "Current Shift"
        
        elif period == "prior_shift":
            shifts = self.get_all_shifts()
            # Find the first non-current shift
            for shift in shifts:
                if not shift.get("is_current"):
                    records = shift.get("records", [])
                    try:
                        start = datetime.fromisoformat(shift.get("shift_start", ""))
                        end_str = shift.get("shift_end", "")
                        if end_str:
                            end = datetime.fromisoformat(end_str)
                        else:
                            end = start + timedelta(hours=12)  # Default end if not available
                        date_range = self._format_date_range(start, end)
                        return records, f"Prior Shift - {date_range}"
                    except:
                        pass
                    return records, f"Prior Shift ({shift.get('date', '')})"
            return [], "Prior Shift (none found)"
        
        elif period == "specific_shift":
            shifts = self.get_all_shifts()
            if self.selected_shift_index is not None and self.selected_shift_index < len(shifts):
                shift = shifts[self.selected_shift_index]
                if shift.get("is_current"):
                    # Same as current shift logic
                    shift_start_str = self.data_manager.data.get("current_shift", {}).get("shift_start")
                    if shift_start_str:
                        try:
                            start = datetime.fromisoformat(shift_start_str)
                            date_range = self._format_date_range(start, now)
                            return shift.get("records", []), f"Current Shift - {date_range}"
                        except:
                            pass
                    return shift.get("records", []), "Current Shift"
                try:
                    start = datetime.fromisoformat(shift.get("shift_start", ""))
                    end_str = shift.get("shift_end", "")
                    if end_str:
                        end = datetime.fromisoformat(end_str)
                    else:
                        end = start + timedelta(hours=12)
                    date_range = self._format_date_range(start, end)
                    desc = start.strftime("%B %d, %Y %I:%M %p")
                    return shift.get("records", []), f"Shift: {desc} - {date_range}"
                except:
                    desc = shift.get("date", "")
                    return shift.get("records", []), f"Shift: {desc}"
            return [], "No shift selected"
        
        elif period == "this_work_week":
            # Current work week: Monday at typical shift start to next Monday at shift end
            start, end = self._get_work_week_range(now, "this")
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"This Work Week - {date_range}"
        
        elif period == "last_work_week":
            # Previous work week: Monday at typical shift start to next Monday at shift end
            start, end = self._get_work_week_range(now, "last")
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"Last Work Week - {date_range}"
        
        elif period == "all_time":
            # All records from all time
            start = datetime.min.replace(year=2000)
            records = self._get_records_in_range(start, now)
            date_range = self._format_date_range(start, now)
            return records, f"All Time - {date_range}"
        
        elif period == "projection":
            # Projection - return empty records for now, projection will use historical data
            # This is handled separately in _display_projection
            return [], "Monthly Projection"
        elif period == "custom_date_range":
            # Custom date range - get dates from entry fields
            try:
                start_str = self.custom_start_date.get().strip()
                end_str = self.custom_end_date.get().strip()
                # Parse dates (MM/DD/YYYY format)
                start = datetime.strptime(start_str, "%m/%d/%Y")
                end = datetime.strptime(end_str, "%m/%d/%Y")
                # Set time to start/end of day
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
                
                # Validate: start should be before end
                if start > end:
                    return [], f"Custom Date Range - Invalid (start date must be before end date)"
                
                records = self._get_records_in_range(start, end)
                date_range = self._format_date_range(start, end)
                return records, f"Custom Date Range - {date_range}"
            except ValueError as e:
                return [], f"Custom Date Range - Invalid date format (use MM/DD/YYYY)"
            except Exception as e:
                return [], f"Custom Date Range - Error: {str(e)}"
        
        elif period == "this_month":
            # This month: 1st of current month to end of current month
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # End of current month: first day of next month minus 1 day, at 23:59:59
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)
            end = (next_month - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"This Month - {date_range}"
        
        elif period == "last_month":
            # Last month: 1st of last month to last day of last month (end of last month)
            if now.month == 1:
                # Last month was December of previous year
                start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
                # End of December: first day of current month (Jan 1) minus 1 day
                end = now.replace(month=1, day=1) - timedelta(days=1)
            else:
                # Last month is previous month of current year
                start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
                # End of last month: first day of current month minus 1 day
                end = now.replace(day=1) - timedelta(days=1)
            # Set end to last moment of the last day
            end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"Last Month - {date_range}"
        
        elif period == "last_3_months":
            # Last 3 months: 1st of the month 3 months ago to end of current month
            current_month = now.month
            current_year = now.year
            
            # Calculate month 3 months ago
            months_back = 3
            target_month = current_month - months_back
            target_year = current_year
            
            while target_month <= 0:
                target_month += 12
                target_year -= 1
            
            start = datetime(target_year, target_month, 1, 0, 0, 0, 0)
            
            # End of current month
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)
            end = (next_month - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
            
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"Last 3 Months - {date_range}"
        
        elif period == "last_year":
            start = now - timedelta(days=365)
            records = self._get_records_in_range(start, now)
            date_range = self._format_date_range(start, now)
            return records, f"Last Year - {date_range}"
        
        return [], "Unknown period"
    
    def _get_work_week_range(self, target_date: datetime, which_week: str = "last") -> Tuple[datetime, datetime]:
        """Calculate work week range based on typical shift times.
        
        Uses dynamically calculated typical_shift_start_hour and typical_shift_end_hour.
        E.g., Monday at shift start to next Monday at shift end.
        
        Args:
            target_date: Reference date
            which_week: "this" for current work week, "last" for previous work week
            
        Returns:
            Tuple of (start_datetime, end_datetime) for the work week
        """
        # Get typical shift times from the app object
        start_hour = self.app.typical_shift_start_hour if hasattr(self.app, 'typical_shift_start_hour') else 23
        end_hour = self.app.typical_shift_end_hour if hasattr(self.app, 'typical_shift_end_hour') else 8
        
        # Find the Monday that started the current work week
        days_since_monday = target_date.weekday()  # Monday = 0
        
        # Determine which work week we're in
        if days_since_monday == 0 and target_date.hour < end_hour:
            # It's Monday before shift end - we're still in the previous work week
            work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=8)
        else:
            # After shift end Monday or later in week - find the Monday that started this week
            if days_since_monday == 0:
                # It's Monday after shift end - current work week started last Monday
                work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
            else:
                # It's Tuesday-Sunday - find the most recent Monday
                work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
        
        # Work week starts Monday at typical shift start hour
        work_week_start = work_week_start_monday.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        # Work week ends next Monday at typical shift end hour
        work_week_end = (work_week_start_monday + timedelta(days=7)).replace(hour=end_hour, minute=0, second=0, microsecond=0)
        
        if which_week == "last":
            # Go back one week (7 days)
            work_week_start = work_week_start - timedelta(days=7)
            work_week_end = work_week_end - timedelta(days=7)
        
        return work_week_start, work_week_end
    
    def _get_records_in_range(self, start: datetime, end: datetime) -> List[dict]:
        """Get all records within a date range from the database."""
        # Use database query for accurate results
        start_str = start.isoformat()
        end_str = end.isoformat()
        records = self.data_manager.db.get_records_in_date_range(start_str, end_str)
        return records
    
    def _expand_multi_accession_records(self, records: List[dict]) -> List[dict]:
        """Expand multi-accession records into individual modality records.
        
        For records with study_type like "Multiple XR", expand them into
        multiple XR records based on accession_count.
        """
        expanded_records = []
        for record in records:
            study_type = record.get("study_type", "Unknown")
            
            # Check if this is a multi-accession record
            if study_type.startswith("Multiple ") and record.get("is_multi_accession", False):
                # Extract the actual modality (e.g., "XR" from "Multiple XR")
                modality = study_type.replace("Multiple ", "").strip()
                accession_count = record.get("accession_count", 1)
                
                # Expand into multiple records with the actual modality
                # Split RVU and duration across the accessions
                total_rvu = record.get("rvu", 0)
                total_duration = record.get("duration_seconds", 0)
                rvu_per_study = total_rvu / accession_count if accession_count > 0 else 0
                duration_per_study = total_duration / accession_count if accession_count > 0 else 0
                
                # Get individual data if available (for newer records)
                individual_procedures = record.get("individual_procedures", [])
                individual_study_types = record.get("individual_study_types", [])
                individual_rvus = record.get("individual_rvus", [])
                individual_accessions = record.get("individual_accessions", [])
                
                # Check if we have individual data stored
                has_individual_data = (individual_study_types and individual_rvus and 
                                     len(individual_study_types) == accession_count and 
                                     len(individual_rvus) == accession_count)
                
                for i in range(accession_count):
                    expanded_record = record.copy()
                    
                    if has_individual_data:
                        # Use stored individual data
                        expanded_record["study_type"] = individual_study_types[i]
                        expanded_record["rvu"] = individual_rvus[i]
                        if individual_procedures and i < len(individual_procedures):
                            expanded_record["procedure"] = individual_procedures[i]
                        if individual_accessions and i < len(individual_accessions):
                            expanded_record["accession"] = individual_accessions[i]
                    else:
                        # Fallback: try to classify individual procedures to get study types and RVUs
                        if individual_procedures and i < len(individual_procedures):
                            # Classify the individual procedure to get its study type and RVU
                            # match_study_type is defined at module level in this same file
                            rvu_table = self.data_manager.data.get("rvu_table", {})
                            classification_rules = self.data_manager.data.get("classification_rules", {})
                            direct_lookups = self.data_manager.data.get("direct_lookups", {})
                            
                            if not rvu_table:
                                logger.warning(f"Cannot classify procedure '{individual_procedures[i]}' - no RVU table loaded")
                            
                            procedure = individual_procedures[i]
                            # Call match_study_type which is defined at module level
                            study_type, rvu = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
                            
                            expanded_record["study_type"] = study_type
                            expanded_record["rvu"] = rvu
                            expanded_record["procedure"] = procedure
                        else:
                            # Fallback to generic modality and split RVU
                            expanded_record["study_type"] = modality
                            expanded_record["rvu"] = rvu_per_study
                            # Fall back to showing "1/3", "2/3", etc.
                            original_procedure = record.get("procedure", f"Multiple {modality}")
                            base_procedure = original_procedure.split(" (")[0] if " (" in original_procedure else original_procedure
                            expanded_record["procedure"] = f"{base_procedure} ({i+1}/{accession_count})"
                    
                    expanded_record["duration_seconds"] = duration_per_study
                    expanded_record["is_multi_accession"] = False  # Mark as individual now
                    
                    expanded_records.append(expanded_record)
            else:
                # Regular record, keep as-is
                expanded_records.append(record)
        
        return expanded_records
    
    def on_custom_date_selected(self):
        """Handle when custom date range radio is selected."""
        # Show the custom date frame
        self.custom_date_frame.pack(fill=tk.X, pady=(5, 0))
        # Update the window to ensure DateEntry widgets render properly
        self.window.update_idletasks()
        self.refresh_data()
    
    def on_date_change(self):
        """Handle when custom date entry fields are changed."""
        # Only refresh if custom date range is selected
        if self.selected_period.get() == "custom_date_range":
            self.refresh_data()
    
    def _count_shifts_in_period(self) -> int:
        """Count the number of unique shifts in the selected period."""
        period = self.selected_period.get()
        now = datetime.now()
        
        if period == "current_shift":
            # Current shift counts as 1
            if self.data_manager.data.get("current_shift", {}).get("shift_start"):
                return 1
            return 0
        
        elif period == "prior_shift":
            # Prior shift counts as 1
            shifts = self.get_all_shifts()
            for shift in shifts:
                if not shift.get("is_current"):
                    return 1
            return 0
        
        elif period == "specific_shift":
            # Specific shift counts as 1
            return 1
        
        elif period in ["this_work_week", "last_work_week"]:
            # Count shifts in the work week range
            which_week = "this" if period == "this_work_week" else "last"
            start, end = self._get_work_week_range(now, which_week)
            return self._count_shifts_in_range(start, end)
        
        elif period == "all_time":
            # Count all shifts
            start = datetime.min.replace(year=2000)
            return self._count_shifts_in_range(start, now)
        
        elif period == "custom_date_range":
            # Count shifts in custom date range
            try:
                start_str = self.custom_start_date.get().strip()
                end_str = self.custom_end_date.get().strip()
                start = datetime.strptime(start_str, "%m/%d/%Y")
                end = datetime.strptime(end_str, "%m/%d/%Y")
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
                if start > end:
                    return 0
                return self._count_shifts_in_range(start, end)
            except:
                return 0
        
        elif period in ["this_month", "last_month"]:
            # Count shifts in month range
            if period == "this_month":
                start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                # End of current month
                if now.month == 12:
                    end = now.replace(year=now.year + 1, month=1, day=1, hour=23, minute=59, second=59, microsecond=999999) - timedelta(days=1)
                else:
                    end = now.replace(month=now.month + 1, day=1, hour=23, minute=59, second=59, microsecond=999999) - timedelta(days=1)
            else:  # last_month
                if now.month == 1:
                    start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
                    end = now.replace(day=1, hour=23, minute=59, second=59, microsecond=999999) - timedelta(days=1)
                else:
                    start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
                    end = now.replace(month=now.month, day=1, hour=23, minute=59, second=59, microsecond=999999) - timedelta(days=1)
            return self._count_shifts_in_range(start, end)
        
        elif period == "last_3_months":
            # Count shifts in last 3 months
            start = now - timedelta(days=90)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            return self._count_shifts_in_range(start, now)
        
        elif period == "last_year":
            # Count shifts in last year
            start = now.replace(year=now.year - 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return self._count_shifts_in_range(start, now)
        
        return 0
    
    def _count_shifts_in_range(self, start: datetime, end: datetime) -> int:
        """Count unique shifts that have records within the date range."""
        shift_ids = set()
        
        # Check current shift
        current_shift = self.data_manager.data.get("current_shift", {})
        if current_shift.get("shift_start"):
            try:
                shift_start = datetime.fromisoformat(current_shift.get("shift_start", ""))
                # Check if shift has any records in the range
                for record in current_shift.get("records", []):
                    try:
                        rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                        if start <= rec_time <= end:
                            shift_ids.add("current")
                            break
                    except:
                        pass
            except:
                pass
        
        # Check historical shifts
        for shift in self.data_manager.data.get("shifts", []):
            try:
                shift_start_str = shift.get("shift_start", "")
                if not shift_start_str:
                    continue
                shift_start = datetime.fromisoformat(shift_start_str)
                # Check if shift has any records in the range
                for record in shift.get("records", []):
                    try:
                        rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                        if start <= rec_time <= end:
                            # Use shift_start as unique identifier
                            shift_ids.add(shift_start_str)
                            break
                    except:
                        pass
            except:
                pass
        
        return len(shift_ids)
    
    def refresh_data(self):
        """Refresh the data display based on current selections."""
        current_period = self.selected_period.get()
        
        # Show/hide custom date frame based on selection
        if current_period == "custom_date_range":
            self.custom_date_frame.pack(fill=tk.X, pady=(5, 0))
            # Update the window to ensure widgets render properly
            self.window.update_idletasks()
        else:
            self.custom_date_frame.pack_forget()
        
        records, period_desc = self.get_records_for_period()
        
        # For efficiency view, add shift count in parentheses after date range
        if self.view_mode.get() == "efficiency":
            # Count unique shifts in the date range
            shift_count = self._count_shifts_in_period()
            if shift_count > 0:
                # Find where the date range ends (after the last dash)
                if " - " in period_desc:
                    # Add shift count after the date range
                    period_desc = f"{period_desc} ({shift_count} shift{'s' if shift_count != 1 else ''})"
                else:
                    # No date range, just add shift count
                    period_desc = f"{period_desc} ({shift_count} shift{'s' if shift_count != 1 else ''})"
        
        # Set period label (override for comparison mode later if needed)
        self.period_label.config(text=period_desc)
        
        # Expand multi-accession records into individual modality records for statistics
        records = self._expand_multi_accession_records(records)
        
        view_mode = self.view_mode.get()
        
        # Override period label for comparison mode
        if view_mode == "comparison":
            self.period_label.config(text="Shift Comparison")
        
        # Hide tree for all views (all use Canvas now)
        self.tree.pack_forget()
        self.tree_scrollbar_y.pack_forget()
        self.tree_scrollbar_x.pack_forget()
        
        # Hide all Canvas tables and frames (they will be recreated/shown by each view)
        canvas_tables = ['_summary_table', '_all_studies_table', '_by_modality_table', 
                        '_by_patient_class_table', '_by_study_type_table', '_by_body_part_table', 
                        '_by_hour_table', '_compensation_table', '_projection_table']
        for table_attr in canvas_tables:
            if hasattr(self, table_attr):
                try:
                    table = getattr(self, table_attr)
                    if hasattr(table, 'frame'):
                        table.frame.pack_forget()
                except:
                    pass
        
        # Hide _all_studies_frame (separate frame for all studies view)
        if hasattr(self, '_all_studies_frame'):
            try:
                self._all_studies_frame.pack_forget()
            except:
                pass
        
        # Hide efficiency frame
        if self.efficiency_frame:
            try:
                self.efficiency_frame.pack_forget()
            except:
                pass
        
        # Hide compensation frame
        if hasattr(self, 'compensation_frame') and self.compensation_frame:
            try:
                self.compensation_frame.pack_forget()
            except:
                pass
        
        # Show/hide projection section in left panel based on view mode
        if hasattr(self, 'projection_frame'):
            if view_mode == "compensation":
                # Show projection section when in compensation view
                try:
                    # Find historical frame to pack before it
                    left_panel = self.projection_frame.master
                    for widget in left_panel.winfo_children():
                        if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Historical":
                            self.projection_frame.pack(fill=tk.X, pady=(0, 10), before=widget)
                            break
                except:
                    pass
            else:
                # Hide projection section in other views (efficiency, etc.)
                try:
                    self.projection_frame.pack_forget()
                    # If projection was selected, switch to a default period
                    if current_period == "projection":
                        self.selected_period.set("current_shift")
                        records, period_desc = self.get_records_for_period()
                except:
                    pass
        
        # Show/hide projection settings frame in right panel based on mode
        if hasattr(self, 'projection_settings_frame'):
            try:
                if view_mode == "compensation" and current_period == "projection":
                    # Settings will be shown in _display_projection
                    pass
                else:
                    self.projection_settings_frame.pack_forget()
            except:
                pass
        
        # Show/hide comparison section in left panel based on view mode
        if hasattr(self, 'comparison_frame'):
            if view_mode == "comparison":
                # Show comparison section when in comparison view
                try:
                    # Find shifts list frame to pack before it
                    left_panel = self.comparison_frame.master
                    for widget in left_panel.winfo_children():
                        if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "All Shifts":
                            self.comparison_frame.pack(fill=tk.X, pady=(0, 10), before=widget)
                            break
                    # Populate comparison comboboxes only if not already populated
                    if not hasattr(self, '_comparison_shifts_populated') or not self._comparison_shifts_populated:
                        self._populate_comparison_shifts(preserve_selection=False)
                        self._comparison_shifts_populated = True
                except Exception as e:
                    logger.error(f"Error showing comparison frame: {e}")
            else:
                # Hide comparison section in other views
                try:
                    self.comparison_frame.pack_forget()
                except:
                    pass
                
                # Clean up ALL comparison-related stored data
                cleanup_attrs = [
                    '_comparison_canvas_widgets',
                    '_comparison_data1',
                    '_comparison_data2', 
                    '_comparison_scroll_canvas',
                    '_comparison_scrollable_frame',
                    '_comparison_mousewheel_canvas',
                    '_comparison_mousewheel_frame',
                    '_comparison_mousewheel_callback'
                ]
                
                for attr in cleanup_attrs:
                    if hasattr(self, attr):
                        try:
                            delattr(self, attr)
                        except:
                            pass
        
        # Show/hide efficiency checkboxes based on view mode
        if view_mode == "efficiency":
            # Show study count mode frame
            self.study_count_mode_frame.pack(side=tk.LEFT, padx=(0, 15))
            
            # Make sure study count mode frame is visible and create radio buttons if needed
            if not self.study_count_radio_buttons:
                # Helper function to save study count mode and refresh
                def save_study_count_mode():
                    self.data_manager.data.setdefault("settings", {})["efficiency_study_count_mode"] = self.study_count_mode.get()
                    self.data_manager.save(save_records=False)
                    # Force immediate redraw of efficiency view if it's currently displayed
                    if hasattr(self, '_efficiency_redraw_functions') and self._efficiency_redraw_functions:
                        for redraw_func in self._efficiency_redraw_functions:
                            try:
                                redraw_func()  # This will read the updated radio button value
                            except Exception as e:
                                logger.debug(f"Error calling redraw function: {e}")
                                pass
                    # Always do a full refresh to ensure everything is updated
                    self.refresh_data()
                
                # Create radio buttons for study count display mode
                ttk.Label(self.study_count_mode_frame, text="Study Count:", font=('Arial', 9)).pack(side=tk.LEFT, padx=(0, 5))
                
                self.study_count_radio_buttons.append(ttk.Radiobutton(
                    self.study_count_mode_frame,
                    text="Average",
                    variable=self.study_count_mode,
                    value="average",
                    command=save_study_count_mode
                ))
                self.study_count_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
                
                self.study_count_radio_buttons.append(ttk.Radiobutton(
                    self.study_count_mode_frame,
                    text="Total",
                    variable=self.study_count_mode,
                    value="total",
                    command=save_study_count_mode
                ))
                self.study_count_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
            
            # Make sure radio buttons frame is visible and create radio buttons if needed
            if not self.heatmap_radio_buttons:
                # Helper function to save heatmap mode and refresh
                def save_heatmap_mode():
                    self.data_manager.data.setdefault("settings", {})["efficiency_heatmap_mode"] = self.heatmap_mode.get()
                    self.data_manager.save(save_records=False)
                    # Redraw efficiency view if it's currently displayed
                    if hasattr(self, '_efficiency_redraw_functions') and self._efficiency_redraw_functions:
                        for redraw_func in self._efficiency_redraw_functions:
                            try:
                                redraw_func()
                            except:
                                pass
                    else:
                        # Fallback to full refresh if redraw functions not available
                        self.refresh_data()
                
                # Create radio buttons for heat map mode
                # Pack label on LEFT first
                ttk.Label(self.efficiency_checkboxes_frame, text="Color Coding:", font=('Arial', 9)).pack(side=tk.LEFT, padx=(0, 5))
                
                # Pack buttons on LEFT in order: None, Duration, Study Count
                self.heatmap_radio_buttons.append(ttk.Radiobutton(
                    self.efficiency_checkboxes_frame,
                    text="None",
                    variable=self.heatmap_mode,
                    value="none",
                    command=save_heatmap_mode
                ))
                self.heatmap_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
                
                self.heatmap_radio_buttons.append(ttk.Radiobutton(
                    self.efficiency_checkboxes_frame,
                    text="Duration",
                    variable=self.heatmap_mode,
                    value="duration",
                    command=save_heatmap_mode
                ))
                self.heatmap_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
                
                self.heatmap_radio_buttons.append(ttk.Radiobutton(
                    self.efficiency_checkboxes_frame,
                    text="Study Count",
                    variable=self.heatmap_mode,
                    value="count",
                    command=save_heatmap_mode
                ))
                self.heatmap_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
            self.efficiency_checkboxes_frame.pack(side=tk.LEFT, anchor=tk.E)
        else:
            if self.efficiency_checkboxes_frame:
                self.efficiency_checkboxes_frame.pack_forget()
            if hasattr(self, 'study_count_mode_frame'):
                self.study_count_mode_frame.pack_forget()
        
        if view_mode == "by_hour":
            self._display_by_hour(records)
        elif view_mode == "by_modality":
            self._display_by_modality(records)
        elif view_mode == "by_patient_class":
            self._display_by_patient_class(records)
        elif view_mode == "by_study_type":
            self._display_by_study_type(records)
        elif view_mode == "by_body_part":
            self._display_by_body_part(records)
        elif view_mode == "all_studies":
            self._display_all_studies(records)
        elif view_mode == "efficiency":
            self._display_efficiency(records)
        elif view_mode == "compensation":
            if self.selected_period.get() == "projection":
                self._display_projection(records)
            else:
                self._display_compensation(records)
        elif view_mode == "summary":
            self._display_summary(records)
        elif view_mode == "comparison":
            self._display_comparison()
            # Summary is handled within _display_comparison, skip default summary update
            return
        
        # Update summary
        total_studies = len(records)
        total_rvu = sum(r.get("rvu", 0) for r in records)
        avg_rvu = total_rvu / total_studies if total_studies > 0 else 0
        
        self.summary_label.config(
            text=f"Total: {total_studies} studies  |  {total_rvu:.1f} RVU  |  Avg: {avg_rvu:.2f} RVU/study"
        )
    
    def _display_by_hour(self, records: List[dict]):
        """Display data broken down by hour using Canvas table."""
        # Group by hour and collect all modalities first
        hour_data = {}
        all_modalities = {}
        for record in records:
            try:
                rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                hour = rec_time.hour
            except:
                continue
                
            if hour not in hour_data:
                hour_data[hour] = {"studies": 0, "rvu": 0, "modalities": {}}
            
            hour_data[hour]["studies"] += 1
            hour_data[hour]["rvu"] += record.get("rvu", 0)
            
            # Track modality
            study_type = record.get("study_type", "Unknown")
            modality = study_type.split()[0] if study_type else "Unknown"
            hour_data[hour]["modalities"][modality] = hour_data[hour]["modalities"].get(modality, 0) + 1
            all_modalities[modality] = all_modalities.get(modality, 0) + 1
        
        # Sort modalities by name for consistent column order
        sorted_modalities = sorted(all_modalities.keys())
        
        # Build dynamic columns: Hour, Studies, RVU, Avg/Study, then one column per modality
        columns = [
            {'name': 'hour', 'width': 120, 'text': 'Hour', 'sortable': True},
            {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': True},
            {'name': 'rvu', 'width': 80, 'text': 'RVU', 'sortable': True},
            {'name': 'avg_rvu', 'width': 80, 'text': 'Avg/Study', 'sortable': True}
        ]
        for modality in sorted_modalities:
            columns.append({'name': modality, 'width': 70, 'text': modality, 'sortable': True})
        
        # Clear/create Canvas table
        if hasattr(self, '_by_hour_table'):
            try:
                self._by_hour_table.frame.pack_forget()
                self._by_hour_table.frame.destroy()
            except:
                pass
            delattr(self, '_by_hour_table')
        
        self._by_hour_table = CanvasTable(self.table_frame, columns, app=self.app)
        # Ensure table is visible
        self._by_hour_table.frame.pack_forget()  # Remove any existing packing
        self._by_hour_table.pack(fill=tk.BOTH, expand=True)
        
        # Calculate totals
        total_studies = sum(d["studies"] for d in hour_data.values())
        total_rvu = sum(d["rvu"] for d in hour_data.values())
        
        # Find the earliest time_performed to determine shift start hour
        start_hour = None
        if records:
            earliest_time = None
            for record in records:
                try:
                    rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                    if earliest_time is None or rec_time < earliest_time:
                        earliest_time = rec_time
                except:
                    continue
            if earliest_time:
                start_hour = earliest_time.hour
        
        # Sort hours starting from shift start hour, wrapping around at 24
        if start_hour is not None and hour_data:
            # Create a sorted list starting from start_hour
            sorted_hours = []
            for offset in range(24):
                hour = (start_hour + offset) % 24
                if hour in hour_data:
                    sorted_hours.append(hour)
        else:
            # Fallback to regular chronological sort if no start hour found
            sorted_hours = sorted(hour_data.keys())
        
        # Display hours in order
        for hour in sorted_hours:
            data = hour_data[hour]
            # Format hour
            hour_12 = hour % 12 or 12
            am_pm = "AM" if hour < 12 else "PM"
            next_hour = (hour + 1) % 24
            next_12 = next_hour % 12 or 12
            next_am_pm = "AM" if next_hour < 12 else "PM"
            hour_str = f"{hour_12}{am_pm}-{next_12}{next_am_pm}"
            
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            
            # Build row cells
            row_cells = {
                'hour': hour_str,
                'studies': str(data["studies"]),
                'rvu': f"{data['rvu']:.1f}",
                'avg_rvu': f"{avg_rvu:.2f}"
            }
            
            # Add count for each modality
            for modality in sorted_modalities:
                count = data["modalities"].get(modality, 0)
                row_cells[modality] = str(count) if count > 0 else ""
            
            self._by_hour_table.add_row(row_cells)
        
        # Add totals row
        if hour_data:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            total_row = {
                'hour': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}"
            }
            # Add total counts for each modality
            for modality in sorted_modalities:
                total_count = all_modalities[modality]
                total_row[modality] = str(total_count) if total_count > 0 else ""
            self._by_hour_table.add_row(total_row, is_total=True)
        
        # Update display once after all rows are added
        self._by_hour_table.update_data()
    
    def _display_by_modality(self, records: List[dict]):
        """Display data broken down by modality using Canvas table."""
        # Clear/create Canvas table
        if hasattr(self, '_by_modality_table'):
            try:
                self._by_modality_table.clear()
            except:
                if hasattr(self, '_by_modality_table'):
                    self._by_modality_table.frame.pack_forget()
                    self._by_modality_table.frame.destroy()
                    delattr(self, '_by_modality_table')
        
        if not hasattr(self, '_by_modality_table'):
            columns = [
                {'name': 'modality', 'width': 100, 'text': 'Modality', 'sortable': True},
                {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': True},
                {'name': 'rvu', 'width': 80, 'text': 'RVU', 'sortable': True},
                {'name': 'avg_rvu', 'width': 80, 'text': 'Avg/Study', 'sortable': True},
                {'name': 'pct_studies', 'width': 80, 'text': '% Studies', 'sortable': True},
                {'name': 'pct_rvu', 'width': 80, 'text': '% RVU', 'sortable': True}
            ]
            self._by_modality_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._by_modality_table.frame.pack_forget()  # Remove any existing packing
        self._by_modality_table.pack(fill=tk.BOTH, expand=True)
        self._by_modality_table.clear()
        
        # Group by modality
        modality_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            study_type = record.get("study_type", "Unknown")
            modality = study_type.split()[0] if study_type else "Unknown"
            rvu = record.get("rvu", 0)
            
            # Handle any remaining "Multiple" modality from old records
            # Extract the actual modality (e.g., "XR" from "Multiple XR")
            if modality == "Multiple" and len(study_type.split()) > 1:
                modality = study_type.split()[1]
            
            if modality not in modality_data:
                modality_data[modality] = {"studies": 0, "rvu": 0}
            
            modality_data[modality]["studies"] += 1
            modality_data[modality]["rvu"] += rvu
            total_studies += 1
            total_rvu += rvu
        
        # Sort by RVU (highest first) and display
        for modality in sorted(modality_data.keys(), key=lambda k: modality_data[k]["rvu"], reverse=True):
            data = modality_data[modality]
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            pct_studies = (data["studies"] / total_studies * 100) if total_studies > 0 else 0
            pct_rvu = (data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
            
            self._by_modality_table.add_row({
                'modality': modality,
                'studies': str(data["studies"]),
                'rvu': f"{data['rvu']:.1f}",
                'avg_rvu': f"{avg_rvu:.2f}",
                'pct_studies': f"{pct_studies:.1f}%",
                'pct_rvu': f"{pct_rvu:.1f}%"
            })
        
        # Add totals row
        if modality_data:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self._by_modality_table.add_row({
                'modality': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}",
                'pct_studies': '100%',
                'pct_rvu': '100%'
            }, is_total=True)
        
        # Update display once after all rows are added
        self._by_modality_table.update_data()
    
    def _display_by_patient_class(self, records: List[dict]):
        """Display data broken down by patient class using Canvas table."""
        # Clear/create Canvas table
        if hasattr(self, '_by_patient_class_table'):
            try:
                self._by_patient_class_table.clear()
            except:
                if hasattr(self, '_by_patient_class_table'):
                    self._by_patient_class_table.frame.pack_forget()
                    self._by_patient_class_table.frame.destroy()
                    delattr(self, '_by_patient_class_table')
        
        if not hasattr(self, '_by_patient_class_table'):
            columns = [
                {'name': 'patient_class', 'width': 120, 'text': 'Patient Class', 'sortable': True},
                {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': True},
                {'name': 'rvu', 'width': 80, 'text': 'RVU', 'sortable': True},
                {'name': 'avg_rvu', 'width': 80, 'text': 'Avg/Study', 'sortable': True},
                {'name': 'pct_studies', 'width': 80, 'text': '% Studies', 'sortable': True},
                {'name': 'pct_rvu', 'width': 80, 'text': '% RVU', 'sortable': True}
            ]
            self._by_patient_class_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._by_patient_class_table.frame.pack_forget()  # Remove any existing packing
        self._by_patient_class_table.pack(fill=tk.BOTH, expand=True)
        self._by_patient_class_table.clear()
        
        # Group by patient class
        class_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            # Handle missing patient_class (historical data may not have it)
            patient_class = record.get("patient_class", "").strip()
            if not patient_class:
                patient_class = "(Unknown)"
            rvu = record.get("rvu", 0)
            
            if patient_class not in class_data:
                class_data[patient_class] = {"studies": 0, "rvu": 0}
            
            class_data[patient_class]["studies"] += 1
            class_data[patient_class]["rvu"] += rvu
            total_studies += 1
            total_rvu += rvu
        
        # Sort by RVU (highest first) and display
        for patient_class in sorted(class_data.keys(), key=lambda k: class_data[k]["rvu"], reverse=True):
            data = class_data[patient_class]
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            pct_studies = (data["studies"] / total_studies * 100) if total_studies > 0 else 0
            pct_rvu = (data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
            
            self._by_patient_class_table.add_row({
                'patient_class': patient_class,
                'studies': str(data["studies"]),
                'rvu': f"{data['rvu']:.1f}",
                'avg_rvu': f"{avg_rvu:.2f}",
                'pct_studies': f"{pct_studies:.1f}%",
                'pct_rvu': f"{pct_rvu:.1f}%"
            })
        
        # Add totals row
        if class_data:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self._by_patient_class_table.add_row({
                'patient_class': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}",
                'pct_studies': '100%',
                'pct_rvu': '100%'
            }, is_total=True)
        
        # Update display once after all rows are added
        self._by_patient_class_table.update_data()
    
    def _display_by_study_type(self, records: List[dict]):
        """Display data broken down by study type using Canvas table."""
        # Clear/create Canvas table
        if hasattr(self, '_by_study_type_table'):
            try:
                self._by_study_type_table.clear()
            except:
                if hasattr(self, '_by_study_type_table'):
                    self._by_study_type_table.frame.pack_forget()
                    self._by_study_type_table.frame.destroy()
                    delattr(self, '_by_study_type_table')
        
        if not hasattr(self, '_by_study_type_table'):
            columns = [
                {'name': 'study_type', 'width': 225, 'text': 'Study Type', 'sortable': True},  # Increased by 50% (150 -> 225)
                {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': True},
                {'name': 'rvu', 'width': 80, 'text': 'RVU', 'sortable': True},
                {'name': 'avg_rvu', 'width': 80, 'text': 'Avg/Study', 'sortable': True},
                {'name': 'pct_studies', 'width': 80, 'text': '% Studies', 'sortable': True},
                {'name': 'pct_rvu', 'width': 80, 'text': '% RVU', 'sortable': True}
            ]
            self._by_study_type_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._by_study_type_table.frame.pack_forget()  # Remove any existing packing
        self._by_study_type_table.pack(fill=tk.BOTH, expand=True)
        self._by_study_type_table.clear()
        
        # Group by study type
        type_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            # Handle missing study_type (historical data may not have it)
            study_type = record.get("study_type", "").strip()
            if not study_type:
                study_type = "(Unknown)"
            
            # Handle any remaining "Multiple ..." study types from old records
            # Convert "Multiple XR" -> "XR Other", etc.
            if study_type.startswith("Multiple "):
                modality = study_type.replace("Multiple ", "").strip()
                study_type = f"{modality} Other" if modality else "(Unknown)"
            
            # Group "CT Spine Lumbar" and "CT Spine Lumbar Recon" with "CT Spine" for display purposes
            # Group "CT CAP Angio", "CT CAP Trauma", and "CT CA" with "CT CAP" for display purposes
            # Keep the original RVU value, but group them together
            grouping_key = study_type
            if study_type == "CT Spine Lumbar" or study_type == "CT Spine Lumbar Recon":
                grouping_key = "CT Spine"
            elif study_type == "CT CAP Angio" or study_type == "CT CAP Angio Combined" or study_type == "CT CAP Trauma" or study_type == "CT CA":
                grouping_key = "CT CAP"
            
            rvu = record.get("rvu", 0)
            
            if grouping_key not in type_data:
                type_data[grouping_key] = {"studies": 0, "rvu": 0}
            
            type_data[grouping_key]["studies"] += 1
            type_data[grouping_key]["rvu"] += rvu
            total_studies += 1
            total_rvu += rvu
        
        # Sort by RVU (highest first) and display
        for study_type in sorted(type_data.keys(), key=lambda k: type_data[k]["rvu"], reverse=True):
            data = type_data[study_type]
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            pct_studies = (data["studies"] / total_studies * 100) if total_studies > 0 else 0
            pct_rvu = (data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
            
            self._by_study_type_table.add_row({
                'study_type': study_type,
                'studies': str(data["studies"]),
                'rvu': f"{data['rvu']:.1f}",
                'avg_rvu': f"{avg_rvu:.2f}",
                'pct_studies': f"{pct_studies:.1f}%",
                'pct_rvu': f"{pct_rvu:.1f}%"
            })
        
        # Add totals row
        if type_data:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self._by_study_type_table.add_row({
                'study_type': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}",
                'pct_studies': '100%',
                'pct_rvu': '100%'
            }, is_total=True)
        
        # Update display once after all rows are added
        self._by_study_type_table.update_data()
    
    def _get_body_part_group(self, study_type: str) -> str:
        """Map study types to modality-specific anatomical groups for hierarchical display."""
        study_lower = study_type.lower()
        
        # Determine modality first
        if study_type.startswith('CT ') or study_type.startswith('CTA '):
            # === CT STUDIES ===
            is_cta = study_type.startswith('CTA')
            
            # Check for body region combinations first
            has_chest = ('chest' in study_lower or ' cap' in study_lower or ' ca ' in study_lower or study_lower.endswith(' ca'))
            has_abdomen = ('abdomen' in study_lower or ' ap' in study_lower or ' cap' in study_lower or ' ca ' in study_lower or study_lower.endswith(' ca'))
            has_pelvis = ('pelvis' in study_lower or ' ap' in study_lower or ' cap' in study_lower)
            
            # CTA Runoff with Abdo/Pelvis - special case
            if 'runoff' in study_lower and ('abdomen' in study_lower or 'pelvis' in study_lower or 'abdo' in study_lower):
                return "CTA: Abdomen/Pelvis"
            
            # CT/CTA Body (Chest+Abdomen combinations ± Pelvis)
            elif has_chest and has_abdomen:
                return "CTA: Body" if is_cta else "CT: Body"
            
            # CT/CTA Abdomen/Pelvis (no chest)
            elif has_abdomen and not has_chest:
                return "CTA: Abdomen/Pelvis" if is_cta else "CT: Abdomen/Pelvis"
            
            # CT/CTA Chest alone
            elif has_chest and not has_abdomen:
                return "CTA: Chest" if is_cta else "CT: Chest"
            
            # CT/CTA Brain
            elif any(kw in study_lower for kw in ['brain', 'head', 'face', 'sinus', 'orbit', 'temporal', 'maxillofacial']):
                return "CTA: Brain" if is_cta else "CT: Brain"
            
            # CT/CTA Neck (without brain/head)
            elif 'neck' in study_lower:
                return "CTA: Neck" if is_cta else "CT: Neck"
            
            # CT/CTA Spine
            elif any(kw in study_lower for kw in ['spine', 'cervical', 'thoracic', 'lumbar', 'sacrum', 'coccyx']):
                return "CTA: Spine" if is_cta else "CT: Spine"
            
            # CT/CTA MSK
            elif any(kw in study_lower for kw in ['shoulder', 'arm', 'elbow', 'wrist', 'hand', 'hip', 'femur', 'knee', 'leg', 'ankle', 'foot', 'joint', 'bone']):
                return "CTA: MSK" if is_cta else "CT: MSK"
            
            # CT/CTA Pelvis alone (no abdomen) - MSK
            elif has_pelvis and not has_abdomen:
                return "CTA: MSK" if is_cta else "CT: MSK"
            
            else:
                return "CTA: Other" if is_cta else "CT: Other"
        
        elif study_type.startswith('MR') or study_type.startswith('MRI'):
            # === MRI STUDIES ===
            # MRI Brain
            if any(kw in study_lower for kw in ['brain', 'head', 'face', 'orbit', 'pituitary', 'iap', 'temporal']):
                return "MRI: Brain"
            
            # MRI Spine
            elif any(kw in study_lower for kw in ['spine', 'cervical', 'thoracic', 'lumbar', 'sacrum', 'coccyx']):
                return "MRI: Spine"
            
            # MRI Abdomen/Pelvis
            elif any(kw in study_lower for kw in ['abdomen', 'pelvis', 'liver', 'kidney', 'pancreas', 'mrcp', 'enterography']):
                return "MRI: Abdomen/Pelvis"
            
            # MRI MSK
            elif any(kw in study_lower for kw in ['shoulder', 'arm', 'elbow', 'wrist', 'hand', 'hip', 'femur', 'knee', 'leg', 'ankle', 'foot', 'joint', 'extremity']):
                return "MRI: MSK"
            
            # MRI Neck
            elif 'neck' in study_lower:
                return "MRI: Neck"
            
            # MRI Chest (rare but exists)
            elif 'chest' in study_lower or 'thorax' in study_lower:
                return "MRI: Chest"
            
            else:
                return "MRI: Other"
        
        elif study_type.startswith('XR'):
            # === X-RAY STUDIES ===
            # XR Chest
            if 'chest' in study_lower:
                return "XR: Chest"
            
            # XR Abdomen
            elif 'abdomen' in study_lower:
                return "XR: Abdomen"
            
            # XR MSK
            elif any(kw in study_lower for kw in ['msk', 'bone', 'shoulder', 'arm', 'elbow', 'wrist', 'hand', 'finger', 
                                                    'hip', 'pelvis', 'femur', 'knee', 'leg', 'ankle', 'foot', 'toe',
                                                    'spine', 'cervical', 'thoracic', 'lumbar', 'sacrum', 'coccyx',
                                                    'rib', 'clavicle', 'scapula', 'joint', 'extremity']):
                return "XR: MSK"
            
            else:
                return "XR: Other"
        
        elif study_type.startswith('US'):
            # === ULTRASOUND STUDIES ===
            # US Abdomen/Pelvis
            if any(kw in study_lower for kw in ['abdomen', 'pelvis', 'liver', 'kidney', 'gallbladder', 'spleen', 'pancreas', 'bladder', 'ovary', 'uterus', 'prostate']):
                return "US: Abdomen/Pelvis"
            
            # US Vascular
            elif any(kw in study_lower for kw in ['vascular', 'doppler', 'artery', 'vein', 'vessel', 'dvt', 'carotid']):
                return "US: Vascular"
            
            # US MSK
            elif any(kw in study_lower for kw in ['shoulder', 'elbow', 'wrist', 'hand', 'hip', 'knee', 'ankle', 'foot', 'tendon', 'joint']):
                return "US: MSK"
            
            # US Breast
            elif 'breast' in study_lower:
                return "US: Breast"
            
            # US Thyroid/Neck
            elif 'thyroid' in study_lower or 'neck' in study_lower:
                return "US: Neck"
            
            else:
                return "US: Other"
        
        elif study_type.startswith('NM'):
            # === NUCLEAR MEDICINE STUDIES ===
            # NM Cardiac
            if any(kw in study_lower for kw in ['cardiac', 'heart', 'myocard', 'stress', 'viability']):
                return "NM: Cardiac"
            
            # NM Bone
            elif 'bone' in study_lower:
                return "NM: Bone"
            
            # NM Other organs
            else:
                return "NM: Other"
        
        else:
            # === OTHER MODALITIES ===
            return "Other"
    
    def _display_by_body_part(self, records: List[dict]):
        """Display data grouped by anatomical body part with hierarchical organization."""
        logger.debug(f"_display_by_body_part called with {len(records)} records")
        
        # Clear/create Canvas table (force recreation to pick up any column changes)
        if hasattr(self, '_by_body_part_table'):
            try:
                self._by_body_part_table.clear()
            except:
                if hasattr(self, '_by_body_part_table'):
                    self._by_body_part_table.frame.pack_forget()
                    self._by_body_part_table.frame.destroy()
                    delattr(self, '_by_body_part_table')
        
        # Create table if it doesn't exist
        if not hasattr(self, '_by_body_part_table'):
            columns = [
                {'name': 'body_part', 'width': 250, 'text': 'Body Part / Study Type', 'sortable': False},  # Narrower first column
                {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': False},
                {'name': 'rvu', 'width': 100, 'text': 'Total RVU', 'sortable': False},
                {'name': 'avg_rvu', 'width': 80, 'text': 'Avg RVU', 'sortable': False},
                {'name': 'pct_studies', 'width': 90, 'text': '% Studies', 'sortable': False},
                {'name': 'pct_rvu', 'width': 90, 'text': '% RVU', 'sortable': False}
            ]
            self._by_body_part_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._by_body_part_table.frame.pack_forget()
        self._by_body_part_table.pack(fill=tk.BOTH, expand=True)
        self._by_body_part_table.clear()
        
        # Group by study type first, then by body part
        type_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            study_type = record.get("study_type", "").strip()
            if not study_type:
                study_type = "(Unknown)"
            
            # Handle any remaining "Multiple ..." study types
            if study_type.startswith("Multiple "):
                modality = study_type.replace("Multiple ", "").strip()
                study_type = f"{modality} Other" if modality else "(Unknown)"
            
            rvu = record.get("rvu", 0)
            
            if study_type not in type_data:
                type_data[study_type] = {"studies": 0, "rvu": 0}
            
            type_data[study_type]["studies"] += 1
            type_data[study_type]["rvu"] += rvu
            total_studies += 1
            total_rvu += rvu
        
        # Group study types by body part
        body_part_groups = {}
        for study_type, data in type_data.items():
            body_part = self._get_body_part_group(study_type)
            if body_part not in body_part_groups:
                body_part_groups[body_part] = {"studies": 0, "rvu": 0, "types": {}}
            
            body_part_groups[body_part]["studies"] += data["studies"]
            body_part_groups[body_part]["rvu"] += data["rvu"]
            body_part_groups[body_part]["types"][study_type] = data
        
        logger.debug(f"Created {len(body_part_groups)} body part groups: {list(body_part_groups.keys())}")
        
        # Sort body parts by modality priority, then by specific order
        def body_part_sort_key(body_part):
            """Sort by modality (CT, CTA, XR, US, MRI, NM, other), then by specific body part order."""
            # Determine modality priority based on prefix
            if body_part.startswith('CT:') or body_part.startswith('CTA:'):
                # Treat CTA as immediately after CT (same priority level)
                is_cta = body_part.startswith('CTA:')
                modality_priority = 0
                
                # Special ordering: CT Body, CT Abdomen/Pelvis, CT Chest, then CTA equivalents, then others
                if body_part == 'CT: Body':
                    sub_priority = 0
                elif body_part == 'CT: Abdomen/Pelvis':
                    sub_priority = 1
                elif body_part == 'CT: Chest':
                    sub_priority = 2
                elif body_part == 'CTA: Body':
                    sub_priority = 3
                elif body_part == 'CTA: Abdomen/Pelvis':
                    sub_priority = 4
                elif body_part == 'CTA: Chest':
                    sub_priority = 5
                else:
                    # For other CT/CTA categories, CTA comes after CT
                    base_name = body_part.replace('CTA:', '').replace('CT:', '')
                    if is_cta:
                        sub_priority = 100 + ord(base_name[0]) if base_name else 100  # CTA after CT
                    else:
                        sub_priority = 50 + ord(base_name[0]) if base_name else 50  # CT before CTA
            elif body_part.startswith('XR:'):
                modality_priority = 1
                sub_priority = 0
            elif body_part.startswith('US:'):
                modality_priority = 2
                sub_priority = 0
            elif body_part.startswith('MRI:'):
                modality_priority = 3
                sub_priority = 0
            elif body_part.startswith('NM:'):
                modality_priority = 4
                sub_priority = 0
            else:
                modality_priority = 99  # Everything else
                sub_priority = 0
            
            # Return: (modality, sub_priority, alphabetical name)
            return (modality_priority, sub_priority, body_part)
        
        sorted_body_parts = sorted(body_part_groups.keys(), key=body_part_sort_key)
        
        # Display hierarchically
        for body_part in sorted_body_parts:
            bp_data = body_part_groups[body_part]
            num_children = len(bp_data["types"])
            
            # Sort study types within this body part by RVU
            sorted_types = sorted(bp_data["types"].keys(), 
                                 key=lambda k: bp_data["types"][k]["rvu"], 
                                 reverse=True)
            
            # If only one child study type, skip parent header and just show the study type
            if num_children == 1:
                study_type = sorted_types[0]
                st_data = bp_data["types"][study_type]
                st_avg_rvu = st_data["rvu"] / st_data["studies"] if st_data["studies"] > 0 else 0
                st_pct_studies = (st_data["studies"] / total_studies * 100) if total_studies > 0 else 0
                st_pct_rvu = (st_data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
                
                # Show study type with body part prefix for context
                self._by_body_part_table.add_row({
                    'body_part': f"{body_part} - {study_type}",  # Combined display
                    'studies': str(st_data["studies"]),
                    'rvu': f"{st_data['rvu']:.1f}",
                    'avg_rvu': f"{st_avg_rvu:.2f}",
                    'pct_studies': f"{st_pct_studies:.1f}%",
                    'pct_rvu': f"{st_pct_rvu:.1f}%"
                })
            else:
                # Multiple children - show parent header and children
                avg_rvu = bp_data["rvu"] / bp_data["studies"] if bp_data["studies"] > 0 else 0
                pct_studies = (bp_data["studies"] / total_studies * 100) if total_studies > 0 else 0
                pct_rvu = (bp_data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
                
                # Add body part header row with custom background color (don't use is_total)
                # Use button background color for headers (works in both themes)
                theme_colors = self._by_body_part_table.theme_colors
                header_bg = theme_colors.get("button_bg", "#e1e1e1")
                
                self._by_body_part_table.add_row({
                    'body_part': f"▼ {body_part}",  # Parent category with arrow
                    'studies': str(bp_data["studies"]),
                    'rvu': f"{bp_data['rvu']:.1f}",
                    'avg_rvu': f"{avg_rvu:.2f}",
                    'pct_studies': f"{pct_studies:.1f}%",
                    'pct_rvu': f"{pct_rvu:.1f}%"
                }, cell_colors={col: header_bg for col in ['body_part', 'studies', 'rvu', 'avg_rvu', 'pct_studies', 'pct_rvu']})
                
                # Add individual study types (indented with 5 spaces)
                for study_type in sorted_types:
                    st_data = bp_data["types"][study_type]
                    st_avg_rvu = st_data["rvu"] / st_data["studies"] if st_data["studies"] > 0 else 0
                    st_pct_studies = (st_data["studies"] / total_studies * 100) if total_studies > 0 else 0
                    st_pct_rvu = (st_data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
                    
                    self._by_body_part_table.add_row({
                        'body_part': f"     {study_type}",  # 5 spaces for visual separation
                        'studies': str(st_data["studies"]),
                        'rvu': f"{st_data['rvu']:.1f}",
                        'avg_rvu': f"{st_avg_rvu:.2f}",
                        'pct_studies': f"{st_pct_studies:.1f}%",
                        'pct_rvu': f"{st_pct_rvu:.1f}%"
                    })
        
        # Add totals row
        if body_part_groups:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self._by_body_part_table.add_row({
                'body_part': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}",
                'pct_studies': '100%',
                'pct_rvu': '100%'
            }, is_total=True)
        
        # Update display once after all rows are added
        logger.debug(f"Calling update_data() on body part table with {len(self._by_body_part_table.rows_data)} rows")
        self._by_body_part_table.update_data()
        logger.debug("Body part table update_data() completed")
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to a human-readable string (e.g., '5m 30s', '1h 23m')."""
        if seconds is None or seconds == 0:
            return "N/A"
        
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if secs > 0 and hours == 0:  # Only show seconds if less than an hour
            parts.append(f"{secs}s")
        
        return " ".join(parts) if parts else "0s"
    
    def _display_all_studies(self, records: List[dict]):
        """Display all individual studies with virtual scrolling for performance."""
        # Clear/create frame for virtual table
        if hasattr(self, '_all_studies_frame'):
            try:
                self._all_studies_frame.destroy()
            except:
                pass
        
        if hasattr(self, '_all_studies_table'):
            try:
                self._all_studies_table.frame.pack_forget()
                self._all_studies_table.frame.destroy()
            except:
                pass
            if hasattr(self, '_all_studies_table'):
                delattr(self, '_all_studies_table')
        
        # Filter out multi-accession records (they should have been split into individual records)
        # Exclude records with study_type starting with "Multiple" or with individual_accessions populated
        filtered_records = []
        for record in records:
            study_type = record.get("study_type", "")
            # Skip multi-accession records
            if study_type.startswith("Multiple "):
                continue
            # Skip records with individual_accessions (old format that should have been migrated)
            if record.get("individual_accessions"):
                individual_accessions = record.get("individual_accessions", [])
                if individual_accessions and len(individual_accessions) > 0:
                    continue
            filtered_records.append(record)
        
        # Store filtered records for virtual rendering
        self._all_studies_records = filtered_records
        self._all_studies_row_height = 22
        
        # Clear render state to force fresh render
        if hasattr(self, '_last_render_range'):
            delattr(self, '_last_render_range')
        if hasattr(self, '_rendered_rows'):
            delattr(self, '_rendered_rows')
        
        # Column definitions with widths
        self._all_studies_columns = [
            ('num', 35, '#'),
            ('date', 80, 'Date'),
            ('time', 70, 'Time'),
            ('procedure', 260, 'Procedure'),
            ('study_type', 90, 'Study Type'),  # Reduced from 110 to 90 to ensure RVU stays visible
            ('rvu', 45, 'RVU'),
            ('duration', 70, 'Duration'),
            ('delete', 25, '×')
        ]
        
        # Create frame
        self._all_studies_frame = ttk.Frame(self.table_frame)
        self._all_studies_frame.pack(fill=tk.BOTH, expand=True)
        
        colors = self.app.get_theme_colors()
        canvas_bg = colors.get("entry_bg", "white")
        header_bg = colors.get("button_bg", "#e1e1e1")
        border_color = colors.get("border_color", "#cccccc")  # Light grey for canvas borders
        text_fg = colors.get("fg", "black")
        
        # Calculate total width
        total_width = sum(col[1] for col in self._all_studies_columns)
        
        # Header canvas (fixed)
        header_canvas = tk.Canvas(self._all_studies_frame, height=25, bg=header_bg, 
                                  highlightthickness=1, highlightbackground=border_color)
        header_canvas.pack(fill=tk.X)
        
        # Draw headers with sorting
        x = 0
        # Preserve existing sort state if it exists, otherwise reset
        if not hasattr(self, '_all_studies_sort_column'):
            self._all_studies_sort_column = None
            self._all_studies_sort_reverse = False
        
        for col_name, width, header_text in self._all_studies_columns:
            # Skip delete column for sorting
            if col_name != 'delete':
                # Create clickable header
                rect_id = header_canvas.create_rectangle(x, 0, x + width, 25, fill=header_bg, outline=border_color, tags=f"header_{col_name}")
                text_id = header_canvas.create_text(x + width//2, 12, text=header_text, font=('Arial', 9, 'bold'), fill=text_fg, tags=f"header_{col_name}")
                
                # Bind click event
                header_canvas.tag_bind(f"header_{col_name}", "<Button-1>", 
                                      lambda e, col=col_name: self._sort_all_studies(col))
                header_canvas.tag_bind(f"header_{col_name}", "<Enter>", 
                                      lambda e: header_canvas.config(cursor="hand2"))
                header_canvas.tag_bind(f"header_{col_name}", "<Leave>", 
                                      lambda e: header_canvas.config(cursor=""))
            else:
                # Non-clickable delete header
                header_canvas.create_rectangle(x, 0, x + width, 25, fill=header_bg, outline=border_color)
                header_canvas.create_text(x + width//2, 12, text=header_text, font=('Arial', 9, 'bold'), fill=text_fg)
            x += width
        
        # Store header canvas reference for updating sort indicators
        self._all_studies_header_canvas = header_canvas
        
        # Data canvas with scrollbar
        data_frame = ttk.Frame(self._all_studies_frame)
        data_frame.pack(fill=tk.BOTH, expand=True)
        
        self._all_studies_canvas = tk.Canvas(data_frame, bg=canvas_bg, highlightthickness=0)
        
        # Custom scroll command that also triggers re-render
        def on_scroll(*args):
            self._all_studies_canvas.yview(*args)
            self._render_visible_rows()
        
        scrollbar = ttk.Scrollbar(data_frame, orient="vertical", command=on_scroll)
        
        # Custom yscrollcommand that triggers re-render
        def on_scroll_set(first, last):
            scrollbar.set(first, last)
            self._render_visible_rows()
        
        self._all_studies_canvas.configure(yscrollcommand=on_scroll_set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._all_studies_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Set scroll region based on total rows
        total_height = len(records) * self._all_studies_row_height
        self._all_studies_canvas.configure(scrollregion=(0, 0, total_width, total_height))
        
        # Mouse wheel scrolling
        def on_mousewheel(event):
            self._all_studies_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        self._all_studies_canvas.bind("<MouseWheel>", on_mousewheel)
        self._all_studies_canvas.bind("<Configure>", lambda e: self._render_visible_rows())
        self._all_studies_canvas.bind("<Map>", lambda e: self._render_visible_rows())  # Trigger when widget becomes visible
        
        # Ensure layout is complete before initial render
        self._all_studies_frame.update_idletasks()
        self.window.update_idletasks()  # Also update parent window
        
        # Initial render - use multiple triggers for reliability
        def initial_render():
            try:
                self._all_studies_canvas.update_idletasks()
                # Force render by clearing last range check
                if hasattr(self, '_last_render_range'):
                    delattr(self, '_last_render_range')
                self._render_visible_rows()
            except:
                pass
        
        # Try immediate render first
        try:
            initial_render()
        except:
            pass
        
        # Then schedule delayed renders as backup
        self._all_studies_canvas.after(10, initial_render)
        self._all_studies_canvas.after(50, initial_render)  # Backup trigger in case first one fails
        self._all_studies_canvas.after(100, initial_render)  # Another backup
        
        # Set up delete handler
        self._setup_all_studies_delete_handler()
        
        # If there's a saved sort state, apply it after display
        if hasattr(self, '_all_studies_sort_column') and self._all_studies_sort_column:
            # Use after_idle to ensure display is complete before sorting
            saved_reverse = getattr(self, '_all_studies_sort_reverse', False)
            self._all_studies_frame.after_idle(
                lambda: self._sort_all_studies(self._all_studies_sort_column, force_reverse=saved_reverse)
            )
    
    def _sort_all_studies(self, col_name: str, force_reverse: bool = None):
        """Sort all studies by column.
        
        Args:
            col_name: Column name to sort by
            force_reverse: If provided, use this reverse value instead of toggling
        """
        if not hasattr(self, '_all_studies_records'):
            return
        
        # Toggle sort direction if clicking same column (unless force_reverse is provided)
        if force_reverse is not None:
            self._all_studies_sort_column = col_name
            self._all_studies_sort_reverse = force_reverse
        elif hasattr(self, '_all_studies_sort_column') and self._all_studies_sort_column == col_name:
            self._all_studies_sort_reverse = not self._all_studies_sort_reverse
        else:
            self._all_studies_sort_column = col_name
            self._all_studies_sort_reverse = False
        
        # Sort records based on column
        reverse = self._all_studies_sort_reverse
        
        if col_name == 'num':
            # Sort by original index (no-op, just reverse original order)
            pass  # Don't sort, just reverse if needed
        elif col_name == 'date' or col_name == 'time':
            # Sort by time_performed
            self._all_studies_records.sort(
                key=lambda r: r.get('time_performed', ''),
                reverse=reverse
            )
        elif col_name == 'procedure':
            self._all_studies_records.sort(
                key=lambda r: r.get('procedure', '').lower(),
                reverse=reverse
            )
        elif col_name == 'study_type':
            self._all_studies_records.sort(
                key=lambda r: r.get('study_type', '').lower(),
                reverse=reverse
            )
        elif col_name == 'rvu':
            self._all_studies_records.sort(
                key=lambda r: r.get('rvu', 0),
                reverse=reverse
            )
        elif col_name == 'duration':
            self._all_studies_records.sort(
                key=lambda r: r.get('duration_seconds', 0),
                reverse=reverse
            )
        
        # Update header to show sort indicator
        if hasattr(self, '_all_studies_header_canvas'):
            canvas = self._all_studies_header_canvas
            colors = self.app.get_theme_colors()
            header_bg = colors.get("button_bg", "#e1e1e1")
            border_color = colors.get("border_color", "#cccccc")
            text_fg = colors.get("fg", "black")
            
            # Redraw headers with sort indicators
            canvas.delete("all")
            x = 0
            for col, width, header_text in self._all_studies_columns:
                # Add sort indicator if this is the sorted column
                display_text = header_text
                if col == col_name and col != 'delete':
                    indicator = " ▼" if reverse else " ▲"
                    display_text = header_text + indicator
                
                # Skip delete column for sorting
                if col != 'delete':
                    rect_id = canvas.create_rectangle(x, 0, x + width, 25, fill=header_bg, outline=border_color, tags=f"header_{col}")
                    text_id = canvas.create_text(x + width//2, 12, text=display_text, font=('Arial', 9, 'bold'), fill=text_fg, tags=f"header_{col}")
                    
                    # Bind click event
                    canvas.tag_bind(f"header_{col}", "<Button-1>", 
                                   lambda e, c=col: self._sort_all_studies(c))
                    canvas.tag_bind(f"header_{col}", "<Enter>", 
                                   lambda e: canvas.config(cursor="hand2"))
                    canvas.tag_bind(f"header_{col}", "<Leave>", 
                                   lambda e: canvas.config(cursor=""))
                else:
                    canvas.create_rectangle(x, 0, x + width, 25, fill=header_bg, outline=border_color)
                    canvas.create_text(x + width//2, 12, text=display_text, font=('Arial', 9, 'bold'), fill=text_fg)
                x += width
        
        # Force immediate re-render by clearing cache and rendering
        if hasattr(self, '_last_render_range'):
            delattr(self, '_last_render_range')
        if hasattr(self, '_rendered_rows'):
            self._rendered_rows.clear()
        # Re-render the visible rows immediately
        self._render_visible_rows()
        # Force canvas update to ensure sort is visible
        self._all_studies_canvas.update_idletasks()
        # Also trigger a configure event to force refresh
        self._all_studies_canvas.event_generate("<Configure>")
    
    def _truncate_text(self, text: str, max_chars: int) -> str:
        """Truncate text to fit within max characters, adding ... if needed."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 3] + "..."
    
    def _render_visible_rows(self):
        """Render only the visible rows for virtual scrolling performance."""
        if not hasattr(self, '_all_studies_canvas') or not hasattr(self, '_all_studies_records'):
            return
        
        canvas = self._all_studies_canvas
        records = self._all_studies_records
        row_height = self._all_studies_row_height
        columns = self._all_studies_columns
        
        colors = self.app.get_theme_colors()
        data_bg = colors.get("entry_bg", "white")
        text_fg = colors.get("fg", "black")
        border_color = colors.get("border_color", "#cccccc")  # Light grey for canvas borders
        
        # Get visible range
        canvas.update_idletasks()
        try:
            canvas_height = canvas.winfo_height()
            # If canvas hasn't been laid out yet (height is 0 or very small), use a default height
            if canvas_height < 50:
                # Try to get parent frame height as fallback
                try:
                    parent_height = canvas.master.winfo_height()
                    if parent_height > 50:
                        canvas_height = parent_height
                    else:
                        canvas_height = 400  # Default visible height for initial render
                except:
                    canvas_height = 400  # Default visible height for initial render
            
            y_top = canvas.canvasy(0)
            y_bottom = canvas.canvasy(canvas_height)
            
            # Calculate visible range
            first_visible = max(0, int(y_top // row_height) - 2)
            last_visible = min(len(records), int(y_bottom // row_height) + 3)
        except:
            # If we can't get dimensions, render first 20 rows as fallback
            if len(records) > 0:
                first_visible = 0
                last_visible = min(len(records), 20)
            else:
                return
        
        # Ensure we render at least the first row if we have records
        if len(records) > 0 and last_visible <= first_visible:
            last_visible = min(len(records), first_visible + 20)  # Render at least first 20 rows
        
        # Track what we've rendered to avoid re-rendering
        if not hasattr(self, '_rendered_rows'):
            self._rendered_rows = set()
        
        # Check if we need to re-render (scroll position changed significantly)
        current_range = (first_visible, last_visible)
        
        # Force render if:
        # 1. We have records but range is invalid (last_visible is 0 or <= first_visible when we should have rows)
        # 2. Canvas might not be visible yet (check if it's actually mapped)
        force_render = False
        if len(records) > 0:
            try:
                # Check if canvas is actually visible
                canvas_mapped = canvas.winfo_viewable()
                if not canvas_mapped:
                    force_render = True
                # Also force if range seems invalid
                if last_visible == 0 or (last_visible <= first_visible and len(records) > 0):
                    force_render = True
            except:
                force_render = True
        
        if not force_render and hasattr(self, '_last_render_range') and self._last_render_range == current_range:
            return  # No change, skip
        self._last_render_range = current_range
        
        # Clear canvas and render visible rows
        canvas.delete("all")
        
        for idx in range(first_visible, last_visible):
            if idx >= len(records):
                break
            
            record = records[idx]
            y = idx * row_height
            
            # Parse record data
            procedure = record.get("procedure", "Unknown")
            study_type = record.get("study_type", "Unknown")
            rvu = record.get("rvu", 0.0)
            duration = record.get("duration_seconds", 0)
            duration_str = self._format_duration(duration)
            
            time_performed = record.get("time_performed", "")
            date_str = ""
            time_str = ""
            if time_performed:
                try:
                    dt = datetime.fromisoformat(time_performed)
                    date_str = dt.strftime("%m/%d/%y")
                    time_str = dt.strftime("%I:%M%p").lstrip("0").lower()
                except:
                    pass
            
            # Truncate procedure and study_type to fit column widths - be very aggressive with study_type
            # Use fixed limits to ensure RVU column always stays visible on screen
            procedure_col_width = next((w for name, w, _ in columns if name == 'procedure'), 260)
            study_type_col_width = next((w for name, w, _ in columns if name == 'study_type'), 90)
            
            # Calculate procedure max chars (can be more generous)
            procedure_max_chars = max(15, int((procedure_col_width - 20) / 6))
            
            # For study_type, use a fixed aggressive limit to ensure RVU stays visible
            # 90px column with ~6px per char = ~15 chars, but limit to 8 to be very safe
            study_type_max_chars = 8  # Fixed aggressive limit to ensure RVU column visible
            
            procedure_truncated = self._truncate_text(procedure, procedure_max_chars)
            study_type_truncated = self._truncate_text(study_type, study_type_max_chars)
            
            row_data = [
                str(idx + 1),
                date_str,
                time_str,
                procedure_truncated,
                study_type_truncated,
                f"{rvu:.1f}",
                duration_str,
                "×"
            ]
            
            # Draw row
            x = 0
            for i, (col_name, width, _) in enumerate(columns):
                # Draw cell background
                canvas.create_rectangle(x, y, x + width, y + row_height, 
                                        fill=data_bg, outline=border_color, width=1)
                # Draw text
                cell_text = row_data[i] if i < len(row_data) else ""
                anchor = 'w' if col_name == 'procedure' else 'center'
                text_x = x + 4 if anchor == 'w' else x + width // 2
                canvas.create_text(text_x, y + row_height // 2, text=cell_text, 
                                   font=('Arial', 8), fill=text_fg, anchor=anchor)
                x += width
    
    def _setup_all_studies_delete_handler(self):
        """Set up click handling for the delete column in all studies view."""
        if not hasattr(self, '_all_studies_canvas'):
            return
        
        canvas = self._all_studies_canvas
        row_height = self._all_studies_row_height
        columns = self._all_studies_columns
        
        # Calculate x position of delete column
        delete_col_x = sum(col[1] for col in columns[:-1])  # All columns except last
        delete_col_width = columns[-1][1]  # Last column width
        
        colors = self.app.get_theme_colors()
        hover_color = colors.get("delete_btn_hover", "#ffcccc")
        
        # Track currently hovered row
        self._hover_row_idx = None
        
        def on_motion(event):
            canvas_y = canvas.canvasy(event.y)
            canvas_x = event.x
            
            # Check if in delete column
            if delete_col_x <= canvas_x <= delete_col_x + delete_col_width:
                row_idx = int(canvas_y // row_height)
                if 0 <= row_idx < len(self._all_studies_records):
                    if self._hover_row_idx != row_idx:
                        # Clear previous hover first
                        canvas.delete("hover")
                        self._hover_row_idx = row_idx
                        # Draw new hover overlay
                        y1 = row_idx * row_height
                        canvas.create_rectangle(
                            delete_col_x, y1, delete_col_x + delete_col_width, y1 + row_height,
                            fill=hover_color, outline="", tags="hover"
                        )
                        canvas.create_text(
                            delete_col_x + delete_col_width // 2, y1 + row_height // 2,
                            text="×", font=('Arial', 8), fill=colors.get("fg", "black"), tags="hover"
                        )
                        canvas.config(cursor="hand2")
                    return
            
            # Not hovering over delete column
            if self._hover_row_idx is not None:
                canvas.delete("hover")
                self._hover_row_idx = None
                canvas.config(cursor="")
        
        def on_leave(event):
            if self._hover_row_idx is not None:
                canvas.delete("hover")
                self._hover_row_idx = None
                canvas.config(cursor="")
        
        def on_click(event):
            canvas_y = canvas.canvasy(event.y)
            canvas_x = event.x
            
            # Check if in delete column
            if delete_col_x <= canvas_x <= delete_col_x + delete_col_width:
                row_idx = int(canvas_y // row_height)
                if 0 <= row_idx < len(self._all_studies_records):
                    self._delete_all_studies_record(row_idx)
        
        canvas.bind("<Motion>", on_motion)
        canvas.bind("<Leave>", on_leave)
        canvas.bind("<Button-1>", on_click)
    
    def _delete_all_studies_record(self, row_idx: int):
        """Delete a record from the all studies view."""
        if not hasattr(self, '_all_studies_records') or row_idx >= len(self._all_studies_records):
            return
        
        record = self._all_studies_records[row_idx]
        accession = record.get("accession", "")
        
        # Save current sort state and scroll position before deletion
        saved_sort_column = getattr(self, '_all_studies_sort_column', None)
        saved_sort_reverse = getattr(self, '_all_studies_sort_reverse', False)
        
        # Save scroll position (as fraction of total content)
        saved_scroll_position = None
        if hasattr(self, '_all_studies_canvas'):
            try:
                saved_scroll_position = self._all_studies_canvas.yview()[0]  # Get top position (0.0 to 1.0)
            except:
                pass
        
        # Confirm deletion
        result = messagebox.askyesno(
            "Delete Study?",
            f"Delete this study?\n\n"
            f"Accession: {accession}\n"
            f"Procedure: {record.get('procedure', 'Unknown')}\n"
            f"RVU: {record.get('rvu', 0):.1f}",
            parent=self.window
        )
        
        if result:
            time_performed = record.get("time_performed", "")
            record_id = record.get("id")  # Database ID if available
            
            # Delete from database first
            deleted_from_db = False
            if record_id:
                try:
                    self.data_manager.db.delete_record(record_id)
                    deleted_from_db = True
                    logger.info(f"Deleted study from database: {accession} (ID: {record_id})")
                except Exception as e:
                    logger.error(f"Error deleting study from database: {e}", exc_info=True)
            else:
                # Record doesn't have ID - try to find it in database by accession
                # Check current shift first
                try:
                    current_shift = self.data_manager.db.get_current_shift()
                    if current_shift:
                        db_record = self.data_manager.db.find_record_by_accession(
                            current_shift['id'], accession
                        )
                        if db_record:
                            self.data_manager.db.delete_record(db_record['id'])
                            deleted_from_db = True
                            logger.info(f"Deleted study from database by accession: {accession} (ID: {db_record['id']})")
                except Exception as e:
                    logger.error(f"Error finding/deleting study in database (current shift): {e}", exc_info=True)
                
                # If not found in current shift, check historical shifts
                if not deleted_from_db:
                    try:
                        historical_shifts = self.data_manager.db.get_all_shifts()
                        for shift in historical_shifts:
                            if shift.get('is_current'):
                                continue
                            db_record = self.data_manager.db.find_record_by_accession(
                                shift['id'], accession
                            )
                            if db_record:
                                self.data_manager.db.delete_record(db_record['id'])
                                deleted_from_db = True
                                logger.info(f"Deleted study from database by accession (historical shift): {accession} (ID: {db_record['id']})")
                                break
                    except Exception as e:
                        logger.error(f"Error finding/deleting study in database (historical shifts): {e}", exc_info=True)
            
            # Delete from memory (check current shift first, then historical)
            found_in_memory = False
            current_records = self.data_manager.data.get("current_shift", {}).get("records", [])
            for i, r in enumerate(current_records):
                if r.get("accession") == accession and r.get("time_performed") == time_performed:
                    current_records.pop(i)
                    self.data_manager.save()
                    logger.info(f"Deleted study from current shift memory: {accession}")
                    found_in_memory = True
                    break
            
            # If not found in current shift, check historical shifts
            if not found_in_memory:
                for shift in self.data_manager.data.get("shifts", []):
                    shift_records = shift.get("records", [])
                    for i, r in enumerate(shift_records):
                        if r.get("accession") == accession and r.get("time_performed") == time_performed:
                            shift_records.pop(i)
                            self.data_manager.save()
                            logger.info(f"Deleted study from historical shift memory: {accession}")
                            found_in_memory = True
                            break
                    if found_in_memory:
                        break
            
            if not found_in_memory and not deleted_from_db:
                logger.warning(f"Could not find record to delete in memory or database: {accession}")
            
            # Refresh data and restore state
            self.refresh_data()
            
            # Restore sort state and scroll position after refresh completes
            def restore_state():
                if saved_sort_column:
                    self._sort_all_studies(saved_sort_column, force_reverse=saved_sort_reverse)
                
                # Restore scroll position
                if saved_scroll_position is not None and hasattr(self, '_all_studies_canvas'):
                    try:
                        # Wait a moment for canvas to update, then restore scroll
                        self._all_studies_canvas.update_idletasks()
                        self._all_studies_canvas.yview_moveto(saved_scroll_position)
                    except:
                        pass
            
            self.window.after_idle(restore_state)
    
    def _sort_column(self, col: str, reverse: bool = None):
        """Sort treeview by column. Toggles direction on each click."""
        # Track current sort state
        if not hasattr(self, '_current_sort_col'):
            self._current_sort_col = None
            self._current_sort_reverse = False
        
        # If clicking same column, toggle direction; otherwise sort ascending
        if self._current_sort_col == col:
            reverse = not self._current_sort_reverse
        else:
            reverse = False
        
        self._current_sort_col = col
        self._current_sort_reverse = reverse
        
        # Get all items with their values before clearing
        all_items_data = []
        for item in self.tree.get_children(""):
            values = []
            for c in self.tree["columns"]:
                values.append(self.tree.set(item, c))
            sort_val = self.tree.set(item, col)
            
            # Check if it's a totals/separator row by checking all values
            is_total = False
            for val in values:
                if isinstance(val, str):
                    val_str = val.strip()
                    # Check for separator patterns: "─", dashes, "TOTAL", or all dashes
                    if ("─" in val_str or val_str.startswith("TOTAL") or 
                        (len(val_str) > 0 and all(c in "─-" for c in val_str)) or
                        val_str == "─" * len(val_str)):
                        is_total = True
                        break
            
            all_items_data.append((sort_val, values, is_total))
        
        # Separate regular items and totals/separators
        regular_items = [(val, values) for val, values, is_total in all_items_data if not is_total]
        totals_data = [(val, values) for val, values, is_total in all_items_data if is_total]
        
        # Sort regular items
        try:
            # Check if column contains numeric data
            numeric_cols = ["rvu", "studies", "avg_rvu", "pct_studies", "pct_rvu"]
            if col in numeric_cols or (regular_items and regular_items[0][0] and 
                                       str(regular_items[0][0]).replace(".", "").replace("-", "").replace("%", "").strip().isdigit()):
                # Numeric sort
                regular_items.sort(key=lambda t: float(str(t[0]).replace("%", "").replace(",", "")) if t[0] and str(t[0]).replace(".", "").replace("-", "").replace("%", "").replace(",", "").strip().isdigit() else float('-inf'), reverse=reverse)
            elif col == "time_to_read":
                # Sort by duration - parse time format (e.g., "5m 30s" -> seconds for sorting)
                def parse_duration(val):
                    if not val or val == "N/A":
                        return 0
                    total_seconds = 0
                    val_str = str(val).strip()
                    # Parse format like "1h 23m", "5m 30s", "30s"
                    hours = re.search(r'(\d+)h', val_str)
                    minutes = re.search(r'(\d+)m', val_str)
                    seconds = re.search(r'(\d+)s', val_str)
                    if hours:
                        total_seconds += int(hours.group(1)) * 3600
                    if minutes:
                        total_seconds += int(minutes.group(1)) * 60
                    if seconds:
                        total_seconds += int(seconds.group(1))
                    return total_seconds
                regular_items.sort(key=lambda t: parse_duration(t[0]), reverse=reverse)
            else:
                # String sort
                regular_items.sort(key=lambda t: str(t[0]).lower() if t[0] else "", reverse=reverse)
        except (ValueError, TypeError):
            # Fallback to string sort
            regular_items.sort(key=lambda t: str(t[0]).lower() if t[0] else "", reverse=reverse)
        
        # Clear tree
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        
        # Insert sorted regular items
        for val, values in regular_items:
            self.tree.insert("", tk.END, values=values)
        
        # Insert totals at end
        for val, values in totals_data:
            self.tree.insert("", tk.END, values=values)
        
        # Update column headings to show sort direction (subtle arrows: ▲ ▼)
        for column in self.tree["columns"]:
            heading_text = self.tree.heading(column)["text"]
            # Remove existing sort indicators
            heading_text = heading_text.replace("▲ ", "").replace("▼ ", "").strip()
            
            # Add indicator and command for clicked column
            if column == col:
                indicator = "▼ " if reverse else "▲ "
                self.tree.heading(column, text=indicator + heading_text, 
                                 command=lambda c=column: self._sort_column(c))
            else:
                self.tree.heading(column, text=heading_text,
                                 command=lambda c=column: self._sort_column(c))
    
    def _display_efficiency(self, records: List[dict]):
        """Display efficiency view with Canvas-based spreadsheet showing per-cell color coding.
        Two sections: 11pm-10am (night) and 11am-10pm (day), each with Modality + 12 hour columns.
        """
        # Checkboxes are now shown/hidden in refresh_data() method
        # No need to manage them here
        
        # Ensure efficiency frame exists
        if self.efficiency_frame is None:
            self.efficiency_frame = ttk.Frame(self.table_frame)
        
        # Clear existing widgets and redraw functions
        for widget in list(self.efficiency_frame.winfo_children()):
            try:
                widget.destroy()
            except:
                pass
        # Clear redraw function references when rebuilding
        if hasattr(self, '_efficiency_redraw_functions'):
            self._efficiency_redraw_functions.clear()
        
        # Make sure efficiency frame is packed and visible
        try:
            self.efficiency_frame.pack_forget()
        except:
            pass
        self.efficiency_frame.pack(fill=tk.BOTH, expand=True)
        
        # Define hour ranges
        night_hours = list(range(23, 24)) + list(range(0, 11))  # 11pm-10am (12 hours)
        day_hours = list(range(11, 23))  # 11am-10pm (12 hours)
        
        # Build data structure: modality -> hour -> list of durations and counts
        efficiency_data = {}
        study_count_data = {}  # modality -> hour -> count
        shifts_per_hour = {}  # modality -> hour -> set of shift identifiers (for average calculation)
        
        # Build a mapping of time_performed to shift_id by checking all shifts
        # This helps us calculate averages (studies per hour / number of shifts with data in that hour)
        shift_time_map = {}  # time_performed -> shift_id
        all_shifts = []
        current_shift = self.data_manager.data.get("current_shift", {})
        if current_shift.get("shift_start"):
            all_shifts.append(("current", current_shift))
        for shift in self.data_manager.data.get("shifts", []):
            if shift.get("shift_start"):
                all_shifts.append((shift.get("shift_start"), shift))
        
        # Helper function to track which shift a record belongs to
        def track_shift_for_record(modality, hour, record_time_str):
            """Track which shift this record belongs to for average calculation."""
            if not record_time_str:
                return
            try:
                record_time = datetime.fromisoformat(record_time_str)
                # Find which shift this record belongs to
                for shift_id, shift_data in all_shifts:
                    shift_start_str = shift_data.get("shift_start") if isinstance(shift_data, dict) else None
                    if shift_start_str:
                        try:
                            shift_start = datetime.fromisoformat(shift_start_str)
                            shift_end_str = shift_data.get("shift_end") if isinstance(shift_data, dict) else None
                            if shift_end_str:
                                shift_end = datetime.fromisoformat(shift_end_str)
                            else:
                                # No end time, assume 9 hour shift
                                shift_end = shift_start + timedelta(hours=9)
                            
                            if shift_start <= record_time <= shift_end:
                                # This record belongs to this shift
                                if modality not in shifts_per_hour:
                                    shifts_per_hour[modality] = {}
                                if hour not in shifts_per_hour[modality]:
                                    shifts_per_hour[modality][hour] = set()
                                shifts_per_hour[modality][hour].add(shift_id)
                                break
                        except:
                            pass
            except:
                pass
        
        for record in records:
            study_type = record.get("study_type", "Unknown")
            modality = study_type.split()[0] if study_type else "Unknown"
            
            try:
                rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                hour = rec_time.hour
                record_time_str = record.get("time_performed", "")
            except:
                continue
            
            # Check if this is a "Multiple" modality record that should be expanded
            if modality == "Multiple" or study_type.startswith("Multiple "):
                # Expand into individual studies
                individual_study_types = record.get("individual_study_types", [])
                accession_count = record.get("accession_count", 1)
                duration = record.get("duration_seconds", 0)
                duration_per_study = duration / accession_count if accession_count > 0 else 0
                
                # Check if we have individual data stored
                has_individual_data = individual_study_types and len(individual_study_types) == accession_count
                
                if has_individual_data:
                    # We have individual study types - process each one
                    for i, individual_st in enumerate(individual_study_types):
                        expanded_mod = individual_st.split()[0] if individual_st else "Unknown"
                        
                        # Track duration data (divide total duration equally)
                        if duration_per_study and duration_per_study > 0:
                            if expanded_mod not in efficiency_data:
                                efficiency_data[expanded_mod] = {}
                            if hour not in efficiency_data[expanded_mod]:
                                efficiency_data[expanded_mod][hour] = []
                            efficiency_data[expanded_mod][hour].append(duration_per_study)
                        
                        # Track study count data
                        if expanded_mod not in study_count_data:
                            study_count_data[expanded_mod] = {}
                        if hour not in study_count_data[expanded_mod]:
                            study_count_data[expanded_mod][hour] = 0
                        study_count_data[expanded_mod][hour] += 1
                        track_shift_for_record(expanded_mod, hour, record_time_str)
                else:
                    # No individual data - try to parse from study_type (e.g., "Multiple CT, XR")
                    # Extract modalities after "Multiple "
                    if study_type.startswith("Multiple "):
                        modality_str = study_type.replace("Multiple ", "").strip()
                        modalities_list = [m.strip() for m in modality_str.split(",")]
                        count = len(modalities_list)
                        
                        for mod in modalities_list:
                            # Track duration data (divide total duration equally)
                            if duration and duration > 0:
                                per_study_duration = duration / count
                                if mod not in efficiency_data:
                                    efficiency_data[mod] = {}
                                if hour not in efficiency_data[mod]:
                                    efficiency_data[mod][hour] = []
                                efficiency_data[mod][hour].append(per_study_duration)
                            
                            # Track study count data
                            if mod not in study_count_data:
                                study_count_data[mod] = {}
                            if hour not in study_count_data[mod]:
                                study_count_data[mod][hour] = 0
                            study_count_data[mod][hour] += 1
                            track_shift_for_record(mod, hour, record_time_str)
                    else:
                        # Can't expand - treat as single "Multiple" entry
                        # Track duration data
                        if duration and duration > 0:
                            if modality not in efficiency_data:
                                efficiency_data[modality] = {}
                            if hour not in efficiency_data[modality]:
                                efficiency_data[modality][hour] = []
                            efficiency_data[modality][hour].append(duration)
                        
                        # Track study count data
                        if modality not in study_count_data:
                            study_count_data[modality] = {}
                        if hour not in study_count_data[modality]:
                            study_count_data[modality][hour] = 0
                        study_count_data[modality][hour] += 1
                        track_shift_for_record(modality, hour, record_time_str)
            else:
                # Regular single study - process normally
                # Track duration data
                duration = record.get("duration_seconds", 0)
                if duration and duration > 0:
                    if modality not in efficiency_data:
                        efficiency_data[modality] = {}
                    if hour not in efficiency_data[modality]:
                        efficiency_data[modality][hour] = []
                    efficiency_data[modality][hour].append(duration)
                
                # Track study count data (all studies, not just those with duration)
                if modality not in study_count_data:
                    study_count_data[modality] = {}
                if hour not in study_count_data[modality]:
                    study_count_data[modality][hour] = 0
                study_count_data[modality][hour] += 1
                track_shift_for_record(modality, hour, record_time_str)
        
        # Combine modalities from both data sources
        all_modalities = sorted(set(list(efficiency_data.keys()) + list(study_count_data.keys())))
        
        # Helper function to get color coding (blue=low, red=high by default)
        # Get theme colors for efficiency view
        theme_colors = self.app.theme_colors if hasattr(self, 'app') and hasattr(self.app, 'theme_colors') else {}
        data_bg = theme_colors.get("entry_bg", "white")
        text_fg = theme_colors.get("fg", "black")
        border_color = theme_colors.get("border_color", "#cccccc")  # Light grey for canvas borders
        total_bg = theme_colors.get("button_bg", "#e1e1e1")
        
        def get_heatmap_color(value, min_val, max_val, range_val, reverse=False):
            """Return hex color: light blue (low) to light red (high) by default.
            Set reverse=True to invert (blue=high, red=low).
            Works for both duration and count values.
            """
            if value is None or range_val == 0:
                return data_bg  # Use theme background for empty
            
            normalized = (value - min_val) / range_val
            if reverse:
                normalized = 1.0 - normalized  # Reverse the color mapping
            
            # Light blue: RGB(227, 242, 253) = #E3F2FD
            # Light red: RGB(255, 235, 238) = #FFEBEE
            r = int(227 + (255 - 227) * normalized)
            g = int(242 + (235 - 242) * normalized)
            b = int(253 + (238 - 253) * normalized)
            return f"#{r:02x}{g:02x}{b:02x}"
        
        # Helper to create Canvas-based spreadsheet table
        def create_spreadsheet_table(parent_frame, hours_list, section_title):
            """Create a Canvas-based spreadsheet table with per-cell color coding.
            Supports both duration and study count colors based on checkbox states.
            """
            # Frame with scrollbar
            table_frame = ttk.Frame(parent_frame)
            table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
            
            # Get theme colors from app
            theme_colors = self.app.theme_colors if hasattr(self, 'app') and hasattr(self.app, 'theme_colors') else {}
            canvas_bg = theme_colors.get("canvas_bg", "#f0f0f0")
            border_color = theme_colors.get("border_color", "#cccccc")  # Light grey for canvas borders
            canvas = tk.Canvas(table_frame, bg=canvas_bg, highlightthickness=1, highlightbackground=border_color)
            scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=canvas.yview)
            
            # Inner frame on canvas for content
            inner_frame = ttk.Frame(canvas)
            canvas_window = canvas.create_window((0, 0), window=inner_frame, anchor="nw")
            
            # Configure scrolling
            def configure_scroll_region(event):
                canvas.configure(scrollregion=canvas.bbox("all"))
            
            def configure_canvas_width(event):
                canvas_width = event.width
                canvas.itemconfig(canvas_window, width=canvas_width)
            
            inner_frame.bind("<Configure>", configure_scroll_region)
            canvas.bind("<Configure>", configure_canvas_width)
            canvas.configure(yscrollcommand=scrollbar.set)
            
            # Table dimensions
            modality_col_width = 100
            hour_col_width = 75
            row_height = 25
            header_height = 30
            
            # Calculate table width
            table_width = modality_col_width + (12 * hour_col_width)
            
            # Get theme colors for efficiency view
            header_bg = theme_colors.get("button_bg", "#e1e1e1")
            
            # Create header row with button-style appearance
            header_canvas = tk.Canvas(inner_frame, width=table_width, height=header_height, 
                                     bg=header_bg, highlightthickness=0)
            header_canvas.pack(fill=tk.X)
            
            # Store row data for sorting
            row_data_list = []
            total_row_data = None
            
            # Sort state
            sort_column = None
            sort_reverse = False
            
            def draw_headers():
                """Draw headers with sort indicators."""
                header_canvas.delete("all")
                x = 0
                # Modality header (sortable)
                header_text = "Modality"
                if sort_column == "modality":
                    header_text += " ▼" if sort_reverse else " ▲"
                header_fg = theme_colors.get("fg", "black")
                header_border = theme_colors.get("border_color", "#cccccc")  # Light grey for canvas borders
                rect_id = header_canvas.create_rectangle(x, 0, x + modality_col_width, header_height, 
                                                         fill=header_bg, outline=header_border, width=1,
                                                         tags="header_modality")
                
                header_canvas.create_text(x + modality_col_width//2, header_height//2, 
                                         text=header_text, font=('Arial', 9, 'bold'), anchor='center',
                                         fill=header_fg, tags="header_modality")
                header_canvas.tag_bind("header_modality", "<Button-1>", lambda e: on_modality_click())
                header_canvas.tag_bind("header_modality", "<Enter>", lambda e: header_canvas.config(cursor="hand2"))
                header_canvas.tag_bind("header_modality", "<Leave>", lambda e: header_canvas.config(cursor=""))
                x += modality_col_width
                
                # Hour headers (not sortable)
                for hour in hours_list:
                    hour_12 = hour % 12 or 12
                    am_pm = "AM" if hour < 12 else "PM"
                    hour_label = f"{hour_12}{am_pm}"
                    header_canvas.create_rectangle(x, 0, x + hour_col_width, header_height,
                                                  fill=header_bg, outline=header_border, width=1)
                    header_canvas.create_text(x + hour_col_width//2, header_height//2,
                                             text=hour_label, font=('Arial', 9, 'bold'), anchor='center',
                                             fill=header_fg)
                    x += hour_col_width
            
            def on_modality_click():
                """Handle modality header click for sorting."""
                nonlocal sort_column, sort_reverse
                if sort_column == "modality":
                    sort_reverse = not sort_reverse
                else:
                    sort_column = "modality"
                    sort_reverse = False
                draw_headers()
                draw_rows()
            
            def draw_rows():
                """Draw all rows, sorted if needed."""
                rows_canvas.delete("all")
                
                # Get radio button state - force update to ensure we get current values
                try:
                    heatmap_mode = self.heatmap_mode.get()
                except:
                    heatmap_mode = "duration"
                show_duration = (heatmap_mode == "duration")
                show_count = (heatmap_mode == "count")
                
                # Sort row data if needed
                rows_to_draw = list(row_data_list)
                if sort_column == "modality":
                    rows_to_draw.sort(key=lambda r: r['modality'].lower(), reverse=sort_reverse)
                
                y = 0
                for row_data in rows_to_draw:
                    modality = row_data['modality']
                    row_cell_data = row_data['cell_data']
                    row_count_data = row_data.get('count_data', [])
                    min_duration = row_data['min_duration']
                    max_duration = row_data['max_duration']
                    duration_range = row_data['duration_range']
                    min_count = row_data.get('min_count', 0)
                    max_count = row_data.get('max_count', 0)
                    count_range = row_data.get('count_range', 1)
                    
                    # Draw row
                    x = 0
                    # Modality cell
                    rows_canvas.create_rectangle(x, y, x + modality_col_width, y + row_height,
                                               fill=data_bg, outline=border_color, width=1)
                    rows_canvas.create_text(x + modality_col_width//2, y + row_height//2,
                                           text=modality, font=('Arial', 9), anchor='center',
                                           fill=text_fg)
                    x += modality_col_width
                    
                    # Hour cells with color coding
                    # Get avg_count_data which stores pre-calculated averages (total/shifts)
                    row_avg_count_data = row_data.get('avg_count_data', row_count_data)
                    
                    for idx, (avg_duration, _) in enumerate(row_cell_data):
                        # Rebuild cell text based on current study_count_mode (not the stored one)
                        current_study_count_mode = self.study_count_mode.get() if hasattr(self, 'study_count_mode') else "average"
                        total_count = row_count_data[idx] if idx < len(row_count_data) else 0
                        avg_count = row_avg_count_data[idx] if idx < len(row_avg_count_data) else 0
                        
                        # Build cell text dynamically based on current mode - ALWAYS rebuild, don't use stored text
                        if avg_duration is not None:
                            duration_str = self._format_duration(avg_duration)
                            if current_study_count_mode == "average":
                                # Show average: pre-calculated (total / num_shifts)
                                cell_text = f"{duration_str} ({avg_count})"
                            else:
                                # Show total: use the total study count
                                cell_text = f"{duration_str} ({total_count})"
                        elif total_count > 0:
                            if current_study_count_mode == "average":
                                # Show average
                                cell_text = f"({avg_count})"
                            else:
                                cell_text = f"({total_count})"
                        else:
                            cell_text = "-"
                        
                        # Determine cell color based on active heatmaps
                        cell_color = data_bg  # Default to background
                        
                        # Apply duration colors if enabled (blue=fast, red=slow)
                        if show_duration and avg_duration is not None:
                            cell_color = get_heatmap_color(avg_duration, min_duration, max_duration, duration_range, reverse=False)
                        
                        # Apply study count colors if enabled (blue=high count, red=low count - reversed from duration)
                        if show_count and total_count is not None and total_count > 0:
                            # Only count colors enabled (reversed: blue=high, red=low)
                            cell_color = get_heatmap_color(total_count, min_count, max_count, count_range, reverse=True)
                        
                        rows_canvas.create_rectangle(x, y, x + hour_col_width, y + row_height,
                                                   fill=cell_color, outline=border_color, width=1)
                        
                        # Use dark text for shaded cells (light colored), theme text color for unshaded
                        # Shaded cells are light (blue to red), so use dark text
                        if cell_color != data_bg:
                            # Cell is shaded - use dark text for readability
                            cell_text_color = "#000000"  # Black text for light colored cells
                        else:
                            # Cell is not shaded - use theme text color
                            cell_text_color = text_fg
                        
                        rows_canvas.create_text(x + hour_col_width//2, y + row_height//2,
                                               text=cell_text, font=('Arial', 8), anchor='center',
                                               fill=cell_text_color)
                        x += hour_col_width
                    y += row_height
                
                # Draw TOTAL row with color coding
                if total_row_data:
                    y += 5
                    x = 0
                    rows_canvas.create_rectangle(x, y, x + modality_col_width, y + row_height,
                                               fill=total_bg, outline=border_color, width=1)
                    # Always show "Total" as the label (not based on study count mode)
                    rows_canvas.create_text(x + modality_col_width//2, y + row_height//2,
                                           text="Total", font=('Arial', 9, 'bold'), anchor='center',
                                           fill=text_fg)
                    x += modality_col_width
                    
                    total_hour_cells = total_row_data['hour_cells']
                    total_hour_durations = total_row_data.get('hour_durations', [None] * len(total_hour_cells))
                    total_hour_counts = total_row_data.get('hour_counts', [None] * len(total_hour_cells))
                    total_min_duration = total_row_data.get('min_duration', 0)
                    total_max_duration = total_row_data.get('max_duration', 0)
                    total_duration_range = total_row_data.get('duration_range', 1)
                    total_min_count = total_row_data.get('min_count', 0)
                    total_max_count = total_row_data.get('max_count', 0)
                    total_count_range = total_row_data.get('count_range', 1)
                    
                    # Rebuild cell text dynamically based on current study_count_mode
                    current_study_count_mode = self.study_count_mode.get() if hasattr(self, 'study_count_mode') else "average"
                    
                    # Get average counts for total row
                    total_hour_avg_counts = total_row_data.get('hour_avg_counts', [])
                    
                    for idx in range(len(total_hour_cells)):
                        # Rebuild cell text based on current mode
                        avg_duration = total_hour_durations[idx] if idx < len(total_hour_durations) else None
                        total_count = total_hour_counts[idx] if idx < len(total_hour_counts) else None
                        hour_count = total_count if total_count is not None else 0
                        avg_count = total_hour_avg_counts[idx] if idx < len(total_hour_avg_counts) else 0
                        
                        if avg_duration is not None:
                            duration_str = self._format_duration(avg_duration)
                            if current_study_count_mode == "average":
                                # Show average: pre-calculated (total / num_shifts)
                                cell_text = f"{duration_str} ({avg_count})"
                            else:
                                # Show total study count
                                cell_text = f"{duration_str} ({hour_count})"
                        elif hour_count > 0:
                            if current_study_count_mode == "average":
                                # Show average
                                cell_text = f"({avg_count})"
                            else:
                                cell_text = f"({hour_count})"
                        else:
                            cell_text = "-"
                        
                        # Determine cell color based on active heatmaps
                        cell_color = total_bg  # Default to background
                        
                        # Apply duration colors if enabled (blue=fast, red=slow)
                        if show_duration and avg_duration is not None:
                            cell_color = get_heatmap_color(avg_duration, total_min_duration, total_max_duration, total_duration_range, reverse=False)
                        
                        # Apply study count colors if enabled (blue=high count, red=low count - reversed from duration)
                        if show_count and hour_count > 0:
                            cell_color = get_heatmap_color(hour_count, total_min_count, total_max_count, total_count_range, reverse=True)
                        
                        rows_canvas.create_rectangle(x, y, x + hour_col_width, y + row_height,
                                                   fill=cell_color, outline=border_color, width=1)
                        
                        # Use dark text for shaded cells, theme text color for unshaded
                        if cell_color != total_bg:
                            cell_text_color = "#000000"  # Black text for light colored cells
                        else:
                            cell_text_color = text_fg
                        
                        rows_canvas.create_text(x + hour_col_width//2, y + row_height//2,
                                               text=cell_text, font=('Arial', 8, 'bold'), anchor='center',
                                               fill=cell_text_color)
                        x += hour_col_width
                    y += row_height
                
                rows_canvas.config(height=y + 5)
            
            # Get data canvas background from theme
            data_bg = theme_colors.get("entry_bg", "white")
            
            # Create data rows canvas (must be created before draw_rows is called)
            rows_canvas = tk.Canvas(inner_frame, width=table_width, 
                                   bg=data_bg, highlightthickness=0)
            rows_canvas.pack(fill=tk.BOTH, expand=True)
            
            # Draw initial headers
            draw_headers()
            
            # Get study count mode (average vs total)
            study_count_mode = self.study_count_mode.get() if hasattr(self, 'study_count_mode') else "average"
            
            # Build row data for all modalities
            for modality in all_modalities:
                modality_durations = []
                modality_counts_row = []  # Total counts
                modality_avg_counts_row = []  # Average counts (total / num_shifts)
                row_cell_data = []
                
                for hour in hours_list:
                    # Get duration data
                    avg_duration = None
                    duration_count = 0
                    if modality in efficiency_data and hour in efficiency_data[modality]:
                        durations = efficiency_data[modality][hour]
                        avg_duration = sum(durations) / len(durations)
                        duration_count = len(durations)
                    
                    # Get study count data
                    study_count = study_count_data.get(modality, {}).get(hour, 0) if modality in study_count_data else 0
                    modality_counts_row.append(study_count)
                    
                    # Calculate average: total studies / number of shifts with data in this hour
                    num_shifts_with_data = len(shifts_per_hour.get(modality, {}).get(hour, set())) if modality in shifts_per_hour else 0
                    if num_shifts_with_data == 0:
                        num_shifts_with_data = 1  # Avoid division by zero, assume at least 1 shift
                    avg_studies = round(study_count / num_shifts_with_data) if study_count > 0 else 0
                    modality_avg_counts_row.append(avg_studies)
                    
                    # Build cell text based on study count mode
                    if avg_duration is not None:
                        duration_str = self._format_duration(avg_duration)
                        if study_count_mode == "average":
                            # Show average: studies per hour averaged across shifts
                            cell_text = f"{duration_str} ({avg_studies})"
                        else:
                            # Show total: use the total study count
                            cell_text = f"{duration_str} ({study_count})"
                    elif study_count > 0:
                        if study_count_mode == "average":
                            # Average: studies per hour averaged across shifts
                            cell_text = f"({avg_studies})"
                        else:
                            cell_text = f"({study_count})"
                    else:
                        cell_text = "-"
                    
                    modality_durations.append(avg_duration)
                    row_cell_data.append((avg_duration, cell_text))
                
                # Calculate min/max for duration colors
                valid_durations = [d for d in modality_durations if d is not None]
                if valid_durations:
                    min_duration = min(valid_durations)
                    max_duration = max(valid_durations)
                    duration_range = max_duration - min_duration if max_duration > min_duration else 1
                else:
                    min_duration = max_duration = 0
                    duration_range = 1
                
                # Calculate min/max for count colors for this row (per-row calculation)
                valid_counts = [c for c in modality_counts_row if c > 0]
                if valid_counts:
                    min_count = min(valid_counts)
                    max_count = max(valid_counts)
                    count_range = max_count - min_count if max_count > min_count else 1
                else:
                    min_count = max_count = 0
                    count_range = 1
                
                row_data_list.append({
                    'modality': modality,
                    'cell_data': row_cell_data,
                    'count_data': modality_counts_row,  # Total counts
                    'avg_count_data': modality_avg_counts_row,  # Average counts (total / num_shifts)
                    'min_duration': min_duration,
                    'max_duration': max_duration,
                    'duration_range': duration_range,
                    'min_count': min_count,
                    'max_count': max_count,
                    'count_range': count_range
                })
            
            # Build TOTAL row data with color coding support
            if efficiency_data:
                total_hour_cells = []
                total_hour_durations = []
                total_hour_counts = []  # Total counts
                total_hour_avg_counts = []  # Average counts (total / num_shifts)
                total_shifts_per_hour = []  # Number of shifts with data in each hour
                
                for hour in hours_list:
                    hour_durations = []
                    hour_count = 0
                    hour_duration_count = 0
                    # Track unique shifts for this hour across all modalities
                    hour_shift_ids = set()
                    
                    for mod in efficiency_data.keys():
                        if hour in efficiency_data[mod]:
                            hour_durations.extend(efficiency_data[mod][hour])
                            hour_duration_count += len(efficiency_data[mod][hour])
                        # Count all studies for this hour across all modalities
                        if mod in study_count_data and hour in study_count_data[mod]:
                            hour_count += study_count_data[mod][hour]
                        # Collect shift IDs for this hour
                        if mod in shifts_per_hour and hour in shifts_per_hour[mod]:
                            hour_shift_ids.update(shifts_per_hour[mod][hour])
                    
                    num_shifts = len(hour_shift_ids) if hour_shift_ids else 0
                    if num_shifts == 0:
                        num_shifts = 1  # Avoid division by zero
                    avg_count = round(hour_count / num_shifts) if hour_count > 0 else 0
                    
                    total_hour_counts.append(hour_count if hour_count > 0 else None)
                    total_hour_avg_counts.append(avg_count)
                    total_shifts_per_hour.append(num_shifts)
                    
                    # Build cell text based on study count mode (will be rebuilt in draw_rows)
                    if hour_durations:
                        avg_duration = sum(hour_durations) / len(hour_durations)
                        duration_str = self._format_duration(avg_duration)
                        if study_count_mode == "average":
                            cell_text = f"{duration_str} ({avg_count})"
                        else:
                            cell_text = f"{duration_str} ({hour_count})"
                        total_hour_durations.append(avg_duration)
                    else:
                        if study_count_mode == "average":
                            cell_text = f"({avg_count})" if avg_count > 0 else "-"
                        elif study_count_mode == "total" and hour_count > 0:
                            cell_text = f"({hour_count})"
                        else:
                            cell_text = "-"
                        total_hour_durations.append(None)
                    
                    total_hour_cells.append(cell_text)
                
                # Calculate min/max for total row duration colors
                valid_total_durations = [d for d in total_hour_durations if d is not None]
                if valid_total_durations:
                    total_min_duration = min(valid_total_durations)
                    total_max_duration = max(valid_total_durations)
                    total_duration_range = total_max_duration - total_min_duration if total_max_duration > total_min_duration else 1
                else:
                    total_min_duration = total_max_duration = 0
                    total_duration_range = 1
                
                # Calculate min/max for total row count colors
                valid_total_counts = [c for c in total_hour_counts if c is not None and c > 0]
                if valid_total_counts:
                    total_min_count = min(valid_total_counts)
                    total_max_count = max(valid_total_counts)
                    total_count_range = total_max_count - total_min_count if total_max_count > total_min_count else 1
                else:
                    total_min_count = total_max_count = 0
                    total_count_range = 1
                
                total_row_data = {
                    'hour_cells': total_hour_cells,
                    'hour_durations': total_hour_durations,
                    'hour_counts': total_hour_counts,  # Total counts
                    'hour_avg_counts': total_hour_avg_counts,  # Average counts (total / num_shifts)
                    'min_duration': total_min_duration,
                    'max_duration': total_max_duration,
                    'duration_range': total_duration_range,
                    'min_count': total_min_count,
                    'max_count': total_max_count,
                    'count_range': total_count_range
                }
            
            # Initial draw
            draw_rows()
            
            # Store reference to draw_rows so it can be called when radio buttons change
            # This allows redrawing without full refresh_data() call
            if not hasattr(self, '_efficiency_redraw_functions'):
                self._efficiency_redraw_functions = []
            self._efficiency_redraw_functions.append(draw_rows)
            
            # Pack canvas and scrollbar
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            return canvas
        
        # Create two spreadsheet tables
        create_spreadsheet_table(self.efficiency_frame, night_hours, "Night Shift")
        create_spreadsheet_table(self.efficiency_frame, day_hours, "Day Shift")
    
    def _display_summary(self, records: List[dict]):
        """Display summary statistics using Canvas table."""
        # Clear any existing canvas table
        if hasattr(self, '_summary_table'):
            try:
                self._summary_table.clear()
            except:
                if hasattr(self, '_summary_table'):
                    self._summary_table.frame.pack_forget()
                    self._summary_table.frame.destroy()
                    delattr(self, '_summary_table')
        
        # Create Canvas table if it doesn't exist
        if not hasattr(self, '_summary_table'):
            columns = [
                {'name': 'metric', 'width': 300, 'text': 'Metric', 'sortable': True},
                {'name': 'value', 'width': 300, 'text': 'Value', 'sortable': True}  # Increased by 50% (200 -> 300)
            ]
            self._summary_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._summary_table.frame.pack_forget()  # Remove any existing packing
        self._summary_table.pack(fill=tk.BOTH, expand=True)
        self._summary_table.clear()
        
        total_studies = len(records)
        total_rvu = sum(r.get("rvu", 0) for r in records)
        avg_rvu = total_rvu / total_studies if total_studies > 0 else 0
        
        # Calculate time span - sum of actual shift durations, not time from first to last record
        hours = 0.0
        shifts_with_records = {}  # Initialize outside conditional
        if records:
            # Get all shifts (current and historical)
            all_shifts = []
            current_shift = self.data_manager.data.get("current_shift", {})
            if current_shift.get("shift_start"):
                all_shifts.append(current_shift)
            all_shifts.extend(self.data_manager.data.get("shifts", []))
            
            # Find which shifts contain these records and sum their durations
            record_times = []
            for r in records:
                try:
                    record_times.append(datetime.fromisoformat(r.get("time_performed", "")))
                except:
                    pass
            
            if record_times:
                # Find unique shifts that contain any of these records
                # Use shift_start as unique identifier since each shift has a unique start time
                for record_time in record_times:
                    for shift in all_shifts:
                        try:
                            shift_start_str = shift.get("shift_start")
                            if not shift_start_str:
                                continue
                            
                            shift_start = datetime.fromisoformat(shift_start_str)
                            shift_end_str = shift.get("shift_end")
                            
                            # Check if record falls within this shift
                            if shift_end_str:
                                shift_end = datetime.fromisoformat(shift_end_str)
                                if shift_start <= record_time <= shift_end:
                                    shifts_with_records[shift_start_str] = shift
                            else:
                                # Current shift without end - check if record is after shift start
                                if record_time >= shift_start:
                                    shifts_with_records[shift_start_str] = shift
                        except:
                            continue
                
                # Also check if the selected period spans multiple shifts by checking shift time ranges
                # This ensures we include all shifts in the period, even if they don't have records
                period = self.selected_period.get()
                if period in ["this_work_week", "last_work_week", "last_7_days", "last_30_days", "last_90_days", "custom_date_range", "all_time"]:
                    # For date range periods, also include shifts that fall within the period
                    period_start = None
                    period_end = None
                    now = datetime.now()
                    
                    if period == "this_work_week":
                        period_start, period_end = self._get_work_week_range(now, "this")
                    elif period == "last_work_week":
                        period_start, period_end = self._get_work_week_range(now, "last")
                    elif period == "last_7_days":
                        period_start = now - timedelta(days=7)
                        period_end = now
                    elif period == "last_30_days":
                        period_start = now - timedelta(days=30)
                        period_end = now
                    elif period == "last_90_days":
                        period_start = now - timedelta(days=90)
                        period_end = now
                    elif period == "custom_date_range":
                        try:
                            start_str = self.custom_start_date.get().strip()
                            end_str = self.custom_end_date.get().strip()
                            period_start = datetime.strptime(start_str, "%m/%d/%Y")
                            period_end = datetime.strptime(end_str, "%m/%d/%Y") + timedelta(days=1) - timedelta(seconds=1)
                        except:
                            period_start = None
                            period_end = None
                    elif period == "all_time":
                        period_start = datetime.min.replace(year=2000)
                        period_end = now
                    
                    # Include shifts that overlap with the period
                    if period_start and period_end:
                        for shift in all_shifts:
                            try:
                                shift_start_str = shift.get("shift_start")
                                if not shift_start_str:
                                    continue
                                
                                shift_start = datetime.fromisoformat(shift_start_str)
                                shift_end_str = shift.get("shift_end")
                                
                                # Check if shift overlaps with period
                                if shift_end_str:
                                    shift_end = datetime.fromisoformat(shift_end_str)
                                    # Shift overlaps if it starts before period ends and ends after period starts
                                    if shift_start <= period_end and shift_end >= period_start:
                                        shifts_with_records[shift_start_str] = shift
                                else:
                                    # Current shift - include if it starts before period ends
                                    if shift_start <= period_end:
                                        shifts_with_records[shift_start_str] = shift
                            except:
                                continue
                
                # Sum durations of unique shifts
                for shift_start_str, shift in shifts_with_records.items():
                    try:
                        shift_start = datetime.fromisoformat(shift_start_str)
                        shift_end_str = shift.get("shift_end")
                        
                        if shift_end_str:
                            shift_end = datetime.fromisoformat(shift_end_str)
                            shift_duration = (shift_end - shift_start).total_seconds() / 3600
                            hours += shift_duration
                        else:
                            # Current shift - use latest record time as end
                            if record_times:
                                latest_record_time = max(record_times)
                                if latest_record_time > shift_start:
                                    shift_duration = (latest_record_time - shift_start).total_seconds() / 3600
                                    hours += shift_duration
                    except:
                        continue
                
                rvu_per_hour = total_rvu / hours if hours > 0 else 0
                studies_per_hour = total_studies / hours if hours > 0 else 0
            else:
                rvu_per_hour = 0
                studies_per_hour = 0
        else:
            rvu_per_hour = 0
            studies_per_hour = 0
        
        # Modality breakdown with duration tracking - expand "Multiple" records
        modalities = {}
        modality_durations = {}  # Track durations for each modality
        for r in records:
            try:
                st = r.get("study_type", "Unknown")
                mod = st.split()[0] if st else "Unknown"
                
                # Check if this is a "Multiple" modality record that should be expanded
                if mod == "Multiple" or st.startswith("Multiple "):
                    # Expand into individual studies
                    individual_study_types = r.get("individual_study_types", [])
                    individual_procedures = r.get("individual_procedures", [])
                    accession_count = r.get("accession_count", 1)
                    duration = r.get("duration_seconds", 0)
                    duration_per_study = duration / accession_count if accession_count > 0 else 0
                    
                    # Check if we have individual data stored
                    has_individual_data = individual_study_types and len(individual_study_types) == accession_count
                    
                    if has_individual_data:
                        # Use stored individual data
                        for i in range(accession_count):
                            individual_st = individual_study_types[i] if i < len(individual_study_types) else "Unknown"
                            expanded_mod = individual_st.split()[0] if individual_st else "Unknown"
                            
                            modalities[expanded_mod] = modalities.get(expanded_mod, 0) + 1
                            
                            # Track duration for average calculation
                            if duration > 0:
                                if expanded_mod not in modality_durations:
                                    modality_durations[expanded_mod] = []
                                modality_durations[expanded_mod].append(duration_per_study)
                    elif individual_procedures and len(individual_procedures) == accession_count:
                        # Try to classify individual procedures if we don't have stored study types
                        rvu_table = self.data_manager.data.get("rvu_table", {})
                        classification_rules = self.data_manager.data.get("classification_rules", {})
                        direct_lookups = self.data_manager.data.get("direct_lookups", {})
                        
                        # match_study_type is defined at module level in this file
                        for i in range(accession_count):
                            procedure = individual_procedures[i] if i < len(individual_procedures) else ""
                            study_type, _ = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
                            
                            expanded_mod = study_type.split()[0] if study_type else "Unknown"
                            
                            modalities[expanded_mod] = modalities.get(expanded_mod, 0) + 1
                            
                            # Track duration for average calculation
                            if duration > 0:
                                if expanded_mod not in modality_durations:
                                    modality_durations[expanded_mod] = []
                                modality_durations[expanded_mod].append(duration_per_study)
                    else:
                        # Fallback: if we can't expand, extract modality from "Multiple XR" format
                        # Extract actual modality from "Multiple XR" -> "XR"
                        if st.startswith("Multiple "):
                            actual_modality = st.replace("Multiple ", "").strip()
                            if actual_modality:
                                expanded_mod = actual_modality.split()[0]
                            else:
                                expanded_mod = "Unknown"
                        else:
                            expanded_mod = "Unknown"
                        
                        modalities[expanded_mod] = modalities.get(expanded_mod, 0) + accession_count if accession_count > 0 else 1
                        
                        # Track duration for average calculation (split across accessions)
                        if duration > 0:
                            if expanded_mod not in modality_durations:
                                modality_durations[expanded_mod] = []
                            for _ in range(accession_count if accession_count > 0 else 1):
                                modality_durations[expanded_mod].append(duration_per_study)
                else:
                    # Regular record - not "Multiple"
                    modalities[mod] = modalities.get(mod, 0) + 1
                    
                    # Track duration for average calculation
                    duration = r.get("duration_seconds", 0)
                    if duration and duration > 0:
                        if mod not in modality_durations:
                            modality_durations[mod] = []
                        modality_durations[mod].append(duration)
            except Exception as e:
                # Log error but continue processing other records
                logger.error(f"Error processing record in summary modality breakdown: {e}")
                # Fallback: add as regular record
                try:
                    st = r.get("study_type", "Unknown")
                    mod = st.split()[0] if st else "Unknown"
                    modalities[mod] = modalities.get(mod, 0) + 1
                    duration = r.get("duration_seconds", 0)
                    if duration and duration > 0:
                        if mod not in modality_durations:
                            modality_durations[mod] = []
                        modality_durations[mod].append(duration)
                except:
                    pass
        
        top_modality = max(modalities.keys(), key=lambda k: modalities[k]) if modalities else "N/A"
        
        # Calculate shift-level metrics (1, 2, 6)
        # Use the records parameter and filter by shift, rather than shift.get("records")
        shift_stats = []
        if records and shifts_with_records:
            for shift_start_str, shift in shifts_with_records.items():
                # Filter records that belong to this shift
                shift_records = []
                try:
                    shift_start = datetime.fromisoformat(shift_start_str)
                    shift_end_str = shift.get("shift_end")
                    
                    for r in records:
                        try:
                            record_time = datetime.fromisoformat(r.get("time_performed", ""))
                            if shift_end_str:
                                shift_end = datetime.fromisoformat(shift_end_str)
                                if shift_start <= record_time <= shift_end:
                                    shift_records.append(r)
                            else:
                                # Current shift
                                if record_time >= shift_start:
                                    shift_records.append(r)
                        except:
                            continue
                except Exception as e:
                    logger.error(f"Error filtering records for shift {shift_start_str}: {e}")
                    continue
                
                # Include shift even if no records (for completeness), but skip if we can't calculate stats
                if not shift_records:
                    logger.debug(f"Shift {shift_start_str} has no records after filtering, skipping")
                    continue
                
                shift_rvu = sum(r.get("rvu", 0) for r in shift_records)
                shift_studies = len(shift_records)
                
                # Calculate shift duration
                try:
                    shift_start = datetime.fromisoformat(shift_start_str)
                    shift_end_str = shift.get("shift_end")
                    if shift_end_str:
                        shift_end = datetime.fromisoformat(shift_end_str)
                        shift_duration = (shift_end - shift_start).total_seconds() / 3600
                    else:
                        # Current shift - estimate from records
                        shift_record_times = []
                        for r in shift_records:
                            try:
                                shift_record_times.append(datetime.fromisoformat(r.get("time_performed", "")))
                            except:
                                pass
                        if shift_record_times:
                            latest_time = max(shift_record_times)
                            shift_duration = (latest_time - shift_start).total_seconds() / 3600
                            # Ensure minimum duration of 0.1 hours (6 minutes) for very short shifts
                            if shift_duration < 0.1 and shift_studies > 0:
                                shift_duration = 0.1
                        else:
                            # If no record times but we have studies, use a minimum duration
                            if shift_studies > 0:
                                shift_duration = 0.1  # Minimum 6 minutes
                            else:
                                shift_duration = 0
                    
                    shift_rvu_per_hour = shift_rvu / shift_duration if shift_duration > 0 else 0
                    
                    # Format shift date
                    shift_date = shift_start.strftime("%m/%d/%Y")
                    
                    # Only add shift if it has valid duration (duration > 0 means we can calculate rvu_per_hour)
                    # But we'll still include shifts with 0 duration if they have studies (for tracking)
                    shift_stats.append({
                        'date': shift_date,
                        'rvu': shift_rvu,
                        'rvu_per_hour': shift_rvu_per_hour,
                        'duration': shift_duration,
                        'studies': shift_studies
                    })
                except Exception as e:
                    logger.error(f"Error calculating stats for shift {shift_start_str}: {e}")
                    continue
        
        # Find highest RVU shift (1)
        highest_rvu_shift = None
        if shift_stats:
            highest_rvu_shift = max(shift_stats, key=lambda s: s['rvu'])
        
        # Find most efficient shift (2)
        most_efficient_shift = None
        if shift_stats:
            most_efficient_shift = max(shift_stats, key=lambda s: s['rvu_per_hour'])
        
        # Total shifts completed (6)
        total_shifts_completed = len(shift_stats)
        
        # Average time to read overall (10)
        all_durations = [r.get("duration_seconds", 0) for r in records if r.get("duration_seconds", 0) > 0]
        avg_time_to_read = sum(all_durations) / len(all_durations) if all_durations else 0
        
        # Calculate hourly metrics (11, 12, 13, 14) - averaged across shifts (typically best)
        # First, group records by shift
        records_by_shift = {}
        for r in records:
            # Find which shift this record belongs to
            record_time = None
            try:
                record_time = datetime.fromisoformat(r.get("time_performed", ""))
            except:
                continue
            
            # Find the shift this record belongs to
            record_shift = None
            for shift_start_str, shift in shifts_with_records.items():
                try:
                    shift_start = datetime.fromisoformat(shift_start_str)
                    shift_end_str = shift.get("shift_end")
                    if shift_end_str:
                        shift_end = datetime.fromisoformat(shift_end_str)
                        if shift_start <= record_time <= shift_end:
                            record_shift = shift_start_str
                            break
                    else:
                        # Current shift
                        if record_time >= shift_start:
                            record_shift = shift_start_str
                            break
                except:
                    continue
            
            if record_shift:
                if record_shift not in records_by_shift:
                    records_by_shift[record_shift] = []
                records_by_shift[record_shift].append(r)
        
        # Calculate hourly stats per shift, then average across shifts
        hourly_stats_per_shift = {}  # shift -> hour -> stats
        for shift_start_str, shift_records in records_by_shift.items():
            hourly_stats_per_shift[shift_start_str] = {}
            for r in shift_records:
                try:
                    time_performed = datetime.fromisoformat(r.get("time_performed", ""))
                    hour = time_performed.hour
                    
                    if hour not in hourly_stats_per_shift[shift_start_str]:
                        hourly_stats_per_shift[shift_start_str][hour] = {
                            'studies': 0,
                            'rvu': 0,
                            'durations': []
                        }
                    
                    hourly_stats_per_shift[shift_start_str][hour]['studies'] += 1
                    hourly_stats_per_shift[shift_start_str][hour]['rvu'] += r.get("rvu", 0)
                    duration = r.get("duration_seconds", 0)
                    if duration > 0:
                        hourly_stats_per_shift[shift_start_str][hour]['durations'].append(duration)
                except:
                    continue
        
        # Average hourly stats across all shifts
        hourly_stats = {}  # hour -> averaged stats
        all_hours = set()
        for shift_stats in hourly_stats_per_shift.values():
            all_hours.update(shift_stats.keys())
        
        for hour in all_hours:
            studies_list = []
            rvu_list = []
            durations_list = []
            
            for shift_stats in hourly_stats_per_shift.values():
                if hour in shift_stats:
                    studies_list.append(shift_stats[hour]['studies'])
                    rvu_list.append(shift_stats[hour]['rvu'])
                    durations_list.extend(shift_stats[hour]['durations'])
            
            if studies_list:  # Only include hours that appear in at least one shift
                hourly_stats[hour] = {
                    'studies': sum(studies_list) / len(studies_list) if studies_list else 0,  # Average studies per shift
                    'rvu': sum(rvu_list) / len(rvu_list) if rvu_list else 0,  # Average RVU per shift
                    'durations': durations_list,  # All durations for averaging
                    'total_studies': sum(studies_list),  # Keep total for display
                    'shift_count': len(studies_list)  # How many shifts had this hour
                }
        
        # Find busiest hour (11) - highest average studies per shift
        busiest_hour = None
        if hourly_stats:
            busiest_hour = max(hourly_stats.keys(), key=lambda h: hourly_stats[h]['studies'])
        
        # Find most productive hour (12) - highest average RVU per shift
        most_productive_hour = None
        if hourly_stats:
            most_productive_hour = max(hourly_stats.keys(), key=lambda h: hourly_stats[h]['rvu'])
        
        # Find fastest hour (14) - shortest average time to read (averaged across all studies in that hour)
        fastest_hour = None
        fastest_avg_duration = float('inf')
        if hourly_stats:
            for hour, stats in hourly_stats.items():
                if stats['durations']:
                    avg_duration = sum(stats['durations']) / len(stats['durations'])
                    if avg_duration < fastest_avg_duration:
                        fastest_avg_duration = avg_duration
                        fastest_hour = hour
        
        # Calculate consistency score (20) - Coefficient of Variation
        consistency_score = None
        # Check if we have enough shifts with valid data (need at least 2 shifts with RVU per hour > 0)
        # Filter to only shifts that have duration > 0 and rvu_per_hour > 0
        valid_shift_stats = []
        for s in shift_stats:
            if isinstance(s, dict):
                duration = s.get('duration', 0)
                rvu_ph = s.get('rvu_per_hour', 0)
                studies = s.get('studies', 0)
                # Include shift if it has valid duration and positive RVU per hour
                if duration > 0 and rvu_ph > 0:
                    valid_shift_stats.append(s)
        
        logger.debug(f"Shift stats calculation: total shifts={len(shift_stats)}, valid shifts={len(valid_shift_stats)}, shifts_with_records={len(shifts_with_records)}")
        if len(valid_shift_stats) > 1:
            rvu_per_hour_values = [s['rvu_per_hour'] for s in valid_shift_stats]
            if rvu_per_hour_values and len(rvu_per_hour_values) > 1:
                mean_rvu_per_hour = sum(rvu_per_hour_values) / len(rvu_per_hour_values)
                if mean_rvu_per_hour > 0:
                    variance = sum((x - mean_rvu_per_hour) ** 2 for x in rvu_per_hour_values) / len(rvu_per_hour_values)
                    std_dev = variance ** 0.5
                    coefficient_of_variation = (std_dev / mean_rvu_per_hour) * 100
                    consistency_score = coefficient_of_variation
                else:
                    logger.debug(f"Mean RVU per hour is 0, cannot calculate variability")
            else:
                logger.debug(f"Not enough rvu_per_hour_values: {len(rvu_per_hour_values) if rvu_per_hour_values else 0}")
        else:
            logger.debug(f"Not enough valid shifts: {len(valid_shift_stats)} (need 2+)")
        
        # Helper function to format hour
        def format_hour(h):
            if h is None:
                return "N/A"
            hour_12 = h % 12 or 12
            am_pm = "am" if h < 12 else "pm"
            return f"{hour_12}{am_pm}"
        
        # Add summary rows to Canvas table
        self._summary_table.add_row({'metric': 'Total Studies', 'value': str(total_studies)})
        self._summary_table.add_row({'metric': 'Total RVU', 'value': f"{total_rvu:.1f}"})
        self._summary_table.add_row({'metric': 'Average RVU per Study', 'value': f"{avg_rvu:.2f}"})
        
        # Calculate compensation above average RVU per study (in dollars per hour)
        above_avg_records = [r for r in records if r.get("rvu", 0) > avg_rvu]
        if above_avg_records and hours > 0:
            # Calculate total compensation for above-average studies
            above_avg_compensation = sum(self._calculate_study_compensation(r) for r in above_avg_records)
            # Calculate compensation per hour
            above_avg_comp_per_hour = above_avg_compensation / hours
            self._summary_table.add_row({
                'metric': 'Hourly compensation rate',
                'value': f"${above_avg_comp_per_hour:,.2f}/hr"
            })
        else:
            self._summary_table.add_row({
                'metric': 'Hourly compensation rate',
                'value': 'N/A'
            })
        
        # Calculate XR vs CT efficiency metrics
        xr_records = []
        ct_records = []
        
        for r in records:
            study_type = r.get("study_type", "").upper()
            # Check if it's XR (including CR, X-ray, Radiograph)
            if study_type.startswith("XR") or study_type.startswith("CR") or "X-RAY" in study_type or "RADIOGRAPH" in study_type:
                xr_records.append(r)
            # Check if it's CT (including CTA)
            elif study_type.startswith("CT"):
                ct_records.append(r)
        
        # Get average compensation rate from compensation_rates structure
        # Use a representative rate (e.g., weekday partner 11pm = 40, or average)
        compensation_rates = self.data_manager.data.get("compensation_rates", {})
        compensation_rate = 0
        if compensation_rates:
            # Try to get a representative rate (weekday partner 11pm as default, or average)
            try:
                role = self.data_manager.data["settings"].get("role", "Partner").lower()
                role_key = "partner" if role == "partner" else "assoc"
                # Use 11pm weekday rate as representative, or calculate average
                if "weekday" in compensation_rates and role_key in compensation_rates["weekday"]:
                    rates_dict = compensation_rates["weekday"][role_key]
                    # Use 11pm rate, or calculate average of all rates
                    compensation_rate = rates_dict.get("11pm", 0)
                    if compensation_rate == 0:
                        # Calculate average if 11pm not found
                        all_rates = [v for v in rates_dict.values() if isinstance(v, (int, float)) and v > 0]
                        compensation_rate = sum(all_rates) / len(all_rates) if all_rates else 0
            except Exception as e:
                logger.debug(f"Error getting compensation rate: {e}")
                compensation_rate = 0
        
        # Calculate XR efficiency
        if xr_records:
            xr_total_rvu = sum(r.get("rvu", 0) for r in xr_records)
            xr_total_minutes = sum(r.get("duration_seconds", 0) for r in xr_records) / 60.0
            xr_rvu_per_minute = xr_total_rvu / xr_total_minutes if xr_total_minutes > 0 else 0
            
            # Calculate studies and time to reach $100 compensation
            # Always calculate time, even if compensation rate is 0 (will show N/A for studies)
            if compensation_rate > 0 and xr_rvu_per_minute > 0:
                target_rvu = 100.0 / compensation_rate  # RVU needed for $100
                xr_avg_rvu_per_study = xr_total_rvu / len(xr_records) if xr_records else 0
                xr_studies_for_100 = target_rvu / xr_avg_rvu_per_study if xr_avg_rvu_per_study > 0 else 0
                # Calculate time directly from RVU per minute rate (more accurate)
                xr_time_for_100_minutes = target_rvu / xr_rvu_per_minute if xr_rvu_per_minute > 0 else 0
                xr_time_for_100_formatted = self._format_duration(xr_time_for_100_minutes * 60) if xr_time_for_100_minutes > 0 else "N/A"
            elif xr_rvu_per_minute > 0:
                # Calculate time to reach 100 RVU if compensation rate not set
                target_rvu = 100.0
                xr_time_for_100_minutes = target_rvu / xr_rvu_per_minute if xr_rvu_per_minute > 0 else 0
                xr_time_for_100_formatted = self._format_duration(xr_time_for_100_minutes * 60) if xr_time_for_100_minutes > 0 else "N/A"
                xr_studies_for_100 = 0  # Can't calculate without rate
            else:
                xr_studies_for_100 = 0
                xr_time_for_100_formatted = "N/A"
        else:
            xr_rvu_per_minute = 0
            xr_studies_for_100 = 0
            xr_time_for_100_formatted = "N/A"
        
        # Calculate CT efficiency
        if ct_records:
            ct_total_rvu = sum(r.get("rvu", 0) for r in ct_records)
            ct_total_minutes = sum(r.get("duration_seconds", 0) for r in ct_records) / 60.0
            ct_rvu_per_minute = ct_total_rvu / ct_total_minutes if ct_total_minutes > 0 else 0
            
            # Calculate studies and time to reach $100 compensation
            # Always calculate time, even if compensation rate is 0 (will show N/A for studies)
            if compensation_rate > 0 and ct_rvu_per_minute > 0:
                target_rvu = 100.0 / compensation_rate  # RVU needed for $100
                ct_avg_rvu_per_study = ct_total_rvu / len(ct_records) if ct_records else 0
                ct_studies_for_100 = target_rvu / ct_avg_rvu_per_study if ct_avg_rvu_per_study > 0 else 0
                # Calculate time directly from RVU per minute rate (more accurate)
                ct_time_for_100_minutes = target_rvu / ct_rvu_per_minute if ct_rvu_per_minute > 0 else 0
                ct_time_for_100_formatted = self._format_duration(ct_time_for_100_minutes * 60) if ct_time_for_100_minutes > 0 else "N/A"
            elif ct_rvu_per_minute > 0:
                # Calculate time to reach 100 RVU if compensation rate not set
                target_rvu = 100.0
                ct_time_for_100_minutes = target_rvu / ct_rvu_per_minute if ct_rvu_per_minute > 0 else 0
                ct_time_for_100_formatted = self._format_duration(ct_time_for_100_minutes * 60) if ct_time_for_100_minutes > 0 else "N/A"
                ct_studies_for_100 = 0  # Can't calculate without rate
            else:
                ct_studies_for_100 = 0
                ct_time_for_100_formatted = "N/A"
        else:
            ct_rvu_per_minute = 0
            ct_studies_for_100 = 0
            ct_time_for_100_formatted = "N/A"
        
        # Add XR vs CT efficiency metrics (grouped: RVU/min together, then to $100 together)
        self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
        self._summary_table.add_row({'metric': 'XR vs CT Efficiency:', 'value': ''})
        
        # Group RVU per minute together
        if xr_records:
            self._summary_table.add_row({
                'metric': '  XR RVU per Minute',
                'value': f"{xr_rvu_per_minute:.3f}"
            })
        else:
            self._summary_table.add_row({'metric': '  XR RVU per Minute', 'value': 'N/A (no XR studies)'})
        
        if ct_records:
            self._summary_table.add_row({
                'metric': '  CT RVU per Minute',
                'value': f"{ct_rvu_per_minute:.3f}"
            })
        else:
            self._summary_table.add_row({'metric': '  CT RVU per Minute', 'value': 'N/A (no CT studies)'})
        
        # Group "to $100" together
        if xr_records:
            if compensation_rate > 0 and xr_studies_for_100 > 0 and xr_time_for_100_formatted != "N/A":
                self._summary_table.add_row({
                    'metric': '  XR to $100',
                    'value': f"{xr_studies_for_100:.1f} studies, {xr_time_for_100_formatted}"
                })
            elif xr_time_for_100_formatted != "N/A":
                # Show time even if compensation rate not set
                self._summary_table.add_row({
                    'metric': '  XR to $100',
                    'value': f"{xr_time_for_100_formatted} (rate not set)"
                })
            else:
                self._summary_table.add_row({
                    'metric': '  XR to $100',
                    'value': 'N/A'
                })
        
        if ct_records:
            if compensation_rate > 0 and ct_studies_for_100 > 0 and ct_time_for_100_formatted != "N/A":
                self._summary_table.add_row({
                    'metric': '  CT to $100',
                    'value': f"{ct_studies_for_100:.1f} studies, {ct_time_for_100_formatted}"
                })
            elif ct_time_for_100_formatted != "N/A":
                # Show time even if compensation rate not set
                self._summary_table.add_row({
                    'metric': '  CT to $100',
                    'value': f"{ct_time_for_100_formatted} (rate not set)"
                })
            else:
                self._summary_table.add_row({
                    'metric': '  CT to $100',
                    'value': 'N/A'
                })
        
        self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
        
        # Shift-level metrics section
        self._summary_table.add_row({'metric': 'Time Span', 'value': f"{hours:.1f} hours"})
        self._summary_table.add_row({'metric': 'Studies per Hour', 'value': f"{studies_per_hour:.1f}"})
        self._summary_table.add_row({'metric': 'RVU per Hour', 'value': f"{rvu_per_hour:.1f}"})
        self._summary_table.add_row({'metric': 'Total Shifts Completed', 'value': str(total_shifts_completed)})
        
        # Highest RVU shift (1)
        if highest_rvu_shift:
            self._summary_table.add_row({'metric': 'Highest RVU Shift', 'value': f"{highest_rvu_shift['date']}: {highest_rvu_shift['rvu']:.1f} RVU"})
        else:
            self._summary_table.add_row({'metric': 'Highest RVU Shift', 'value': 'N/A'})
        
        # Most efficient shift (2)
        if most_efficient_shift:
            self._summary_table.add_row({'metric': 'Most Efficient Shift', 'value': f"{most_efficient_shift['date']}: {most_efficient_shift['rvu_per_hour']:.1f} RVU/hr"})
        else:
            self._summary_table.add_row({'metric': 'Most Efficient Shift', 'value': 'N/A'})
        
        self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
        
        # Hourly metrics section
        # Display hourly metrics (averaged across shifts)
        if busiest_hour is not None:
            busiest_stats = hourly_stats[busiest_hour]
            avg_studies = busiest_stats['studies']
            total_studies = busiest_stats.get('total_studies', 0)
            shift_count = busiest_stats.get('shift_count', 0)
            self._summary_table.add_row({'metric': 'Busiest Hour', 'value': f"{format_hour(busiest_hour)} ({avg_studies:.1f} avg studies/shift, {total_studies} total)" if shift_count > 1 else f"{format_hour(busiest_hour)} ({total_studies} studies)"})
        else:
            self._summary_table.add_row({'metric': 'Busiest Hour', 'value': 'N/A'})
        
        if most_productive_hour is not None:
            productive_stats = hourly_stats[most_productive_hour]
            avg_rvu = productive_stats['rvu']
            total_rvu = sum(hourly_stats_per_shift[s].get(most_productive_hour, {}).get('rvu', 0) for s in records_by_shift.keys() if most_productive_hour in hourly_stats_per_shift.get(s, {}))
            shift_count = productive_stats.get('shift_count', 0)
            self._summary_table.add_row({'metric': 'Most Productive Hour', 'value': f"{format_hour(most_productive_hour)} ({avg_rvu:.1f} avg RVU/shift, {total_rvu:.1f} total)" if shift_count > 1 else f"{format_hour(most_productive_hour)} ({total_rvu:.1f} RVU)"})
        else:
            self._summary_table.add_row({'metric': 'Most Productive Hour', 'value': 'N/A'})
        
        # Fastest hour (14)
        if fastest_hour is not None:
            fastest_formatted = self._format_duration(fastest_avg_duration)
            fastest_studies = len(hourly_stats[fastest_hour]['durations'])
            self._summary_table.add_row({'metric': 'Fastest Hour', 'value': f"{format_hour(fastest_hour)} ({fastest_formatted} avg, {fastest_studies} studies)"})
        else:
            self._summary_table.add_row({'metric': 'Fastest Hour', 'value': 'N/A'})
        
        self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
        self._summary_table.add_row({'metric': 'Top Modality', 'value': f"{top_modality} ({modalities.get(top_modality, 0)} studies)"})
        
        # Recalculate total_studies after expanding "Multiple" records
        expanded_total_studies = sum(modalities.values()) if modalities else total_studies
        
        # Modality Breakdown - show each modality with percent volume and study count
        if modalities and expanded_total_studies > 0:
            self._summary_table.add_row({'metric': 'Modality Breakdown', 'value': ''})
            # Sort modalities alphabetically
            sorted_modalities = sorted(modalities.items(), key=lambda x: x[0].lower())
            for mod, count in sorted_modalities:
                percent = (count / expanded_total_studies) * 100
                self._summary_table.add_row({'metric': f"  {mod}", 'value': f"{percent:.1f}% ({count} studies)"})
        else:
            self._summary_table.add_row({'metric': 'Modality Breakdown', 'value': 'N/A'})
        
        # Add average time to read by modality
        if modality_durations:
            self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
            
            # Average time to read (10) - moved to just above "by Modality"
            avg_time_formatted = self._format_duration(avg_time_to_read) if avg_time_to_read > 0 else "N/A"
            self._summary_table.add_row({'metric': 'Average Time to Read', 'value': avg_time_formatted})
            
            self._summary_table.add_row({'metric': 'Average Time to Read by Modality', 'value': ''})
            # Sort modalities alphabetically
            modality_avgs = []
            for mod, durations in modality_durations.items():
                if durations:
                    avg_duration = sum(durations) / len(durations)
                    modality_avgs.append((mod, avg_duration, len(durations)))
            
            modality_avgs.sort(key=lambda x: x[0].lower())
            for mod, avg_duration, count in modality_avgs:
                avg_formatted = self._format_duration(avg_duration)
                self._summary_table.add_row({'metric': f"  {mod}", 'value': f"{avg_formatted} ({count} studies)"})
        
        # Update display once after all rows are added
        self._summary_table.update_data()
    
    def _calculate_study_compensation(self, record: dict) -> float:
        """Calculate compensation for a single study based on when it was finished."""
        try:
            time_finished = datetime.fromisoformat(record.get("time_finished", record.get("time_performed", "")))
            rate = self.app._get_compensation_rate(time_finished)
            return record.get("rvu", 0) * rate
        except (KeyError, ValueError, AttributeError):
            return 0.0
    
    def _display_compensation(self, records: List[dict]):
        """Display compensation view with study count, modality breakdown, and total compensation."""
        # Clear/create Canvas table
        if hasattr(self, '_compensation_table'):
            try:
                self._compensation_table.clear()
            except:
                if hasattr(self, '_compensation_table'):
                    self._compensation_table.frame.pack_forget()
                    self._compensation_table.frame.destroy()
                    delattr(self, '_compensation_table')
        
        if not hasattr(self, '_compensation_table'):
            columns = [
                {'name': 'category', 'width': 300, 'text': 'Category', 'sortable': False},
                {'name': 'value', 'width': 250, 'text': 'Value', 'sortable': False}
            ]
            self._compensation_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._compensation_table.frame.pack_forget()  # Remove any existing packing
        self._compensation_table.pack(fill=tk.BOTH, expand=True)
        self._compensation_table.clear()
        
        # Calculate total compensation
        total_compensation = sum(self._calculate_study_compensation(r) for r in records)
        total_studies = len(records)
        total_rvu = sum(r.get("rvu", 0) for r in records)
        
        # Calculate hours elapsed - sum of actual shift durations, not time from first to last record
        hours_elapsed = 0.0
        if self.selected_period.get() == "current_shift" and self.app and self.app.shift_start:
            # For current shift, use actual elapsed time
            hours_elapsed = (datetime.now() - self.app.shift_start).total_seconds() / 3600
        elif records:
            # For historical periods, sum actual shift durations
            try:
                # Get all shifts (current and historical)
                all_shifts = []
                current_shift = self.data_manager.data.get("current_shift", {})
                if current_shift.get("shift_start"):
                    all_shifts.append(current_shift)
                all_shifts.extend(self.data_manager.data.get("shifts", []))
                
                # Find which shifts contain these records and sum their durations
                record_times = []
                for r in records:
                    try:
                        time_str = r.get("time_finished") or r.get("time_performed", "")
                        if time_str:
                            record_times.append(datetime.fromisoformat(time_str))
                    except:
                        pass
                
                shifts_with_records = {}
                if record_times:
                    # Find unique shifts that contain any of these records
                    for record_time in record_times:
                        for shift in all_shifts:
                            try:
                                shift_start_str = shift.get("shift_start")
                                if not shift_start_str:
                                    continue
                                
                                shift_start = datetime.fromisoformat(shift_start_str)
                                shift_end_str = shift.get("shift_end")
                                
                                # Check if record falls within this shift
                                if shift_end_str:
                                    shift_end = datetime.fromisoformat(shift_end_str)
                                    if shift_start <= record_time <= shift_end:
                                        shifts_with_records[shift_start_str] = shift
                                else:
                                    # Current shift without end - check if record is after shift start
                                    if record_time >= shift_start:
                                        shifts_with_records[shift_start_str] = shift
                            except:
                                continue
                
                # Also check if the selected period spans multiple shifts by checking shift time ranges
                # This ensures we include all shifts in the period, even if they don't have records
                period = self.selected_period.get()
                if period in ["this_work_week", "last_work_week", "last_7_days", "last_30_days", "last_90_days", "custom_date_range", "all_time"]:
                    # For date range periods, also include shifts that fall within the period
                    period_start = None
                    period_end = None
                    now = datetime.now()
                    
                    if period == "this_work_week":
                        period_start, period_end = self._get_work_week_range(now, "this")
                    elif period == "last_work_week":
                        period_start, period_end = self._get_work_week_range(now, "last")
                    elif period == "last_7_days":
                        period_start = now - timedelta(days=7)
                        period_end = now
                    elif period == "last_30_days":
                        period_start = now - timedelta(days=30)
                        period_end = now
                    elif period == "last_90_days":
                        period_start = now - timedelta(days=90)
                        period_end = now
                    elif period == "custom_date_range":
                        try:
                            start_str = self.custom_start_date.get().strip()
                            end_str = self.custom_end_date.get().strip()
                            period_start = datetime.strptime(start_str, "%m/%d/%Y")
                            period_end = datetime.strptime(end_str, "%m/%d/%Y") + timedelta(days=1) - timedelta(seconds=1)
                        except:
                            period_start = None
                            period_end = None
                    elif period == "all_time":
                        period_start = datetime.min.replace(year=2000)
                        period_end = now
                    
                    # Include shifts that overlap with the period
                    if period_start and period_end:
                        for shift in all_shifts:
                            try:
                                shift_start_str = shift.get("shift_start")
                                if not shift_start_str:
                                    continue
                                
                                shift_start = datetime.fromisoformat(shift_start_str)
                                shift_end_str = shift.get("shift_end")
                                
                                # Check if shift overlaps with period
                                if shift_end_str:
                                    shift_end = datetime.fromisoformat(shift_end_str)
                                    # Shift overlaps if it starts before period ends and ends after period starts
                                    if shift_start <= period_end and shift_end >= period_start:
                                        shifts_with_records[shift_start_str] = shift
                                else:
                                    # Current shift - include if it starts before period ends
                                    if shift_start <= period_end:
                                        shifts_with_records[shift_start_str] = shift
                            except:
                                continue
                
                # Sum durations of unique shifts
                for shift_start_str, shift in shifts_with_records.items():
                    try:
                        shift_start = datetime.fromisoformat(shift_start_str)
                        shift_end_str = shift.get("shift_end")
                        
                        if shift_end_str:
                            shift_end = datetime.fromisoformat(shift_end_str)
                            shift_duration = (shift_end - shift_start).total_seconds() / 3600
                            hours_elapsed += shift_duration
                        else:
                            # Current shift - use latest record time as end
                            if record_times:
                                latest_record_time = max(record_times)
                                if latest_record_time > shift_start:
                                    shift_duration = (latest_record_time - shift_start).total_seconds() / 3600
                                    hours_elapsed += shift_duration
                    except:
                        continue
                
                # Fallback if no shifts found - use shift length
                if hours_elapsed == 0.0:
                    hours_elapsed = self.app.data_manager.data["settings"].get("shift_length_hours", 9.0) if self.app else 9.0
            except (ValueError, AttributeError):
                # Fallback to shift length if time parsing fails
                hours_elapsed = self.app.data_manager.data["settings"].get("shift_length_hours", 9.0) if self.app else 9.0
        else:
            # No records - use shift length as fallback
            hours_elapsed = self.app.data_manager.data["settings"].get("shift_length_hours", 9.0) if self.app else 9.0
        
        # Calculate compensation per hour
        comp_per_hour = total_compensation / hours_elapsed if hours_elapsed > 0 else 0.0
        
        # Calculate compensation per RVU
        comp_per_rvu = total_compensation / total_rvu if total_rvu > 0 else 0.0
        
        # Get compensation color from theme (dark green for light mode, lighter green for dark mode)
        comp_color = "dark green"
        if self.app and hasattr(self.app, 'theme_colors'):
            comp_color = self.app.theme_colors.get("comp_color", "dark green")
        
        # Add summary rows
        self._compensation_table.add_row({'category': 'Total Studies', 'value': str(total_studies)})
        self._compensation_table.add_row({'category': 'Total RVU', 'value': f"{total_rvu:.2f}"})
        self._compensation_table.add_row(
            {'category': 'Total Compensation', 'value': f"${total_compensation:,.2f}"},
            cell_text_colors={'value': comp_color}
        )
        self._compensation_table.add_row(
            {'category': 'Compensation per Hour', 'value': f"${comp_per_hour:,.2f}/hr"},
            cell_text_colors={'value': comp_color}
        )
        self._compensation_table.add_row(
            {'category': 'Compensation per RVU', 'value': f"${comp_per_rvu:,.2f}/RVU"},
            cell_text_colors={'value': comp_color}
        )
        self._compensation_table.add_row({'category': '', 'value': ''})  # Spacer
        
        # Modality breakdown - expand "Multiple" records into individual studies
        modality_stats = {}
        for r in records:
            st = r.get("study_type", "Unknown")
            mod = st.split()[0] if st else "Unknown"
            
            # Check if this is a "Multiple" modality record that should be expanded
            if mod == "Multiple" or st.startswith("Multiple "):
                # Expand into individual studies
                individual_study_types = r.get("individual_study_types", [])
                individual_rvus = r.get("individual_rvus", [])
                individual_procedures = r.get("individual_procedures", [])
                accession_count = r.get("accession_count", 1)
                total_rvu = r.get("rvu", 0)
                original_comp = self._calculate_study_compensation(r)
                
                # Check if we have individual data stored
                has_individual_data = (individual_study_types and individual_rvus and 
                                     len(individual_study_types) == accession_count and 
                                     len(individual_rvus) == accession_count)
                
                if has_individual_data:
                    # Use stored individual data
                    for i in range(accession_count):
                        individual_st = individual_study_types[i] if i < len(individual_study_types) else "Unknown"
                        individual_rvu = individual_rvus[i] if i < len(individual_rvus) else 0
                        
                        # Extract modality from individual study type
                        expanded_mod = individual_st.split()[0] if individual_st else "Unknown"
                        
                        # Initialize modality if needed
                        if expanded_mod not in modality_stats:
                            modality_stats[expanded_mod] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
                        
                        # Split compensation proportionally based on RVU
                        if total_rvu > 0:
                            comp_per_study = original_comp * (individual_rvu / total_rvu)
                        else:
                            comp_per_study = original_comp / accession_count if accession_count > 0 else 0
                        
                        modality_stats[expanded_mod]['count'] += 1
                        modality_stats[expanded_mod]['rvu'] += individual_rvu
                        modality_stats[expanded_mod]['compensation'] += comp_per_study
                elif individual_procedures and len(individual_procedures) == accession_count:
                    # Try to classify individual procedures if we don't have stored study types
                    rvu_table = self.data_manager.data.get("rvu_table", {})
                    classification_rules = self.data_manager.data.get("classification_rules", {})
                    direct_lookups = self.data_manager.data.get("direct_lookups", {})
                    
                    # match_study_type is defined at module level in this file
                    
                    rvu_per_study = total_rvu / accession_count if accession_count > 0 else 0
                    comp_per_study = original_comp / accession_count if accession_count > 0 else 0
                    
                    for i in range(accession_count):
                        procedure = individual_procedures[i] if i < len(individual_procedures) else ""
                        study_type, rvu = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
                        
                        expanded_mod = study_type.split()[0] if study_type else "Unknown"
                        
                        if expanded_mod not in modality_stats:
                            modality_stats[expanded_mod] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
                        
                        # Use calculated RVU if available, otherwise split evenly
                        actual_rvu = rvu if rvu > 0 else rvu_per_study
                        # Adjust compensation based on actual RVU if we calculated it
                        if rvu > 0 and total_rvu > 0:
                            actual_comp = original_comp * (actual_rvu / total_rvu)
                        else:
                            actual_comp = comp_per_study
                        
                        modality_stats[expanded_mod]['count'] += 1
                        modality_stats[expanded_mod]['rvu'] += actual_rvu
                        modality_stats[expanded_mod]['compensation'] += actual_comp
                else:
                    # Fallback: if we can't expand, extract modality from "Multiple XR" format
                    # Extract actual modality from "Multiple XR" -> "XR"
                    if st.startswith("Multiple "):
                        actual_modality = st.replace("Multiple ", "").strip()
                        if actual_modality:
                            expanded_mod = actual_modality.split()[0]
                        else:
                            expanded_mod = "Unknown"
                    else:
                        expanded_mod = "Unknown"
                    
                    if expanded_mod not in modality_stats:
                        modality_stats[expanded_mod] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
                    
                    # Split evenly across accession count
                    modality_stats[expanded_mod]['count'] += accession_count if accession_count > 0 else 1
                    modality_stats[expanded_mod]['rvu'] += total_rvu
                    modality_stats[expanded_mod]['compensation'] += original_comp
            else:
                # Regular record - not "Multiple"
                if mod not in modality_stats:
                    modality_stats[mod] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
                modality_stats[mod]['count'] += 1
                modality_stats[mod]['rvu'] += r.get("rvu", 0)
                modality_stats[mod]['compensation'] += self._calculate_study_compensation(r)
        
        # Sort modalities by compensation (highest first)
        sorted_modalities = sorted(modality_stats.items(), key=lambda x: x[1]['compensation'], reverse=True)
        
        self._compensation_table.add_row({'category': 'Modality Breakdown', 'value': ''})
        for mod, stats in sorted_modalities:
            # Format value with dollar amount at the end (will be colored green)
            comp_value = f"${stats['compensation']:,.2f}"
            value_text = f"{stats['count']} studies, {stats['rvu']:.2f} RVU, {comp_value}"
            # cell_text_colors will only color the dollar amount part
            self._compensation_table.add_row({
                'category': f"  {mod}",
                'value': value_text
            }, cell_text_colors={'value': comp_color})
        
        self._compensation_table.update_data()
    
    def _display_projection(self, records: List[dict]):
        """Display projection view with configurable days/hours and projected compensation."""
        # Projection settings frame - place in right panel (period_frame area or above table)
        # First, ensure we have a settings frame in the right panel
        if not hasattr(self, 'projection_settings_frame'):
            # Create settings frame in the right panel, below period_frame
            # We'll need to pack it above the table_frame
            self.projection_settings_frame = ttk.LabelFrame(self.right_panel, text="Projection Settings", padding="10")
        
        # Clear existing widgets in settings frame
        for widget in self.projection_settings_frame.winfo_children():
            widget.destroy()
        
        # Pack settings frame above table (before table_frame)
        self.projection_settings_frame.pack_forget()  # Remove from any previous location
        self.projection_settings_frame.pack(fill=tk.X, pady=(0, 10), before=self.table_frame)
        
        settings_frame = self.projection_settings_frame
        
        # Create or reuse compensation frame for results
        if not hasattr(self, 'compensation_frame') or self.compensation_frame is None:
            self.compensation_frame = ttk.Frame(self.table_frame)
        else:
            # Clear existing widgets
            for widget in self.compensation_frame.winfo_children():
                widget.destroy()
        
        self.compensation_frame.pack(fill=tk.BOTH, expand=True)
        
        # Ensure projection variables are initialized (should already be done in create_ui)
        if not hasattr(self, 'projection_days'):
            self.projection_days = tk.IntVar(value=14)
        if not hasattr(self, 'projection_extra_days'):
            self.projection_extra_days = tk.IntVar(value=0)
        if not hasattr(self, 'projection_extra_hours'):
            self.projection_extra_hours = tk.IntVar(value=0)
        
        ttk.Label(settings_frame, text="Base Days:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        days_spinbox = ttk.Spinbox(settings_frame, from_=1, to=31, width=10, 
                                   textvariable=self.projection_days, command=self.refresh_data)
        days_spinbox.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="Extra Days:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        extra_days_spinbox = ttk.Spinbox(settings_frame, from_=0, to=31, width=10,
                                         textvariable=self.projection_extra_days, command=self.refresh_data)
        extra_days_spinbox.grid(row=0, column=3, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="Extra Hours:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        extra_hours_spinbox = ttk.Spinbox(settings_frame, from_=0, to=100, width=10,
                                          textvariable=self.projection_extra_hours, command=self.refresh_data)
        extra_hours_spinbox.grid(row=1, column=1, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="Hours per Day: 9 (11pm-8am)", font=("Arial", 9)).grid(
            row=1, column=2, columnspan=2, sticky=tk.W, padx=5, pady=5)
        
        # Calculate projection based on historical data
        total_days = self.projection_days.get() + self.projection_extra_days.get()
        base_hours = total_days * 9  # 9 hours per day (11pm-8am)
        total_hours = base_hours + self.projection_extra_hours.get()
        
        # Use historical data to project
        # Get recent historical data (last 3 months or available)
        now = datetime.now()
        start_date = now - timedelta(days=90)  # Last 3 months
        historical_records = self._get_records_in_range(start_date, now)
        
        if not historical_records:
            # No historical data
            results_frame = ttk.LabelFrame(self.compensation_frame, text="Projected Results", padding="10")
            results_frame.pack(fill=tk.BOTH, expand=True)
            ttk.Label(results_frame, text="No historical data available for projection.", 
                     font=("Arial", 10)).pack(pady=20)
            return
        
        # Calculate averages from historical data
        historical_studies = len(historical_records)
        historical_rvu = sum(r.get("rvu", 0) for r in historical_records)
        historical_compensation = sum(self._calculate_study_compensation(r) for r in historical_records)
        
        # Calculate historical hours worked (clipped to the date range)
        historical_hours = self._calculate_historical_hours(historical_records, start_date, now)
        
        if historical_hours > 0:
            rvu_per_hour = historical_rvu / historical_hours
            studies_per_hour = historical_studies / historical_hours
            compensation_per_hour = historical_compensation / historical_hours
        else:
            rvu_per_hour = 0
            studies_per_hour = 0
            compensation_per_hour = 0
        
        # Project for total_hours
        projected_rvu = rvu_per_hour * total_hours
        projected_studies = studies_per_hour * total_hours
        projected_compensation = compensation_per_hour * total_hours
        
        # Project by study type based on historical distribution
        study_type_distribution = {}
        for r in historical_records:
            st = r.get("study_type", "Unknown")
            if st not in study_type_distribution:
                study_type_distribution[st] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
            study_type_distribution[st]['count'] += 1
            study_type_distribution[st]['rvu'] += r.get("rvu", 0)
            study_type_distribution[st]['compensation'] += self._calculate_study_compensation(r)
        
        # Normalize distribution
        if historical_studies > 0:
            for st in study_type_distribution:
                study_type_distribution[st]['percentage'] = study_type_distribution[st]['count'] / historical_studies
        
        # Results frame
        results_frame = ttk.LabelFrame(self.compensation_frame, text="Projected Results", padding="10")
        results_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create Canvas table for projection results
        if hasattr(self, '_projection_table'):
            try:
                self._projection_table.clear()
            except:
                if hasattr(self, '_projection_table'):
                    self._projection_table.frame.pack_forget()
                    self._projection_table.frame.destroy()
                    delattr(self, '_projection_table')
        
        if not hasattr(self, '_projection_table'):
            columns = [
                {'name': 'metric', 'width': 300, 'text': 'Metric', 'sortable': True},
                {'name': 'value', 'width': 250, 'text': 'Projected Value', 'sortable': True}
            ]
            self._projection_table = CanvasTable(results_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._projection_table.frame.pack_forget()  # Remove any existing packing
        self._projection_table.pack(fill=tk.BOTH, expand=True)
        self._projection_table.clear()
        
        # Get compensation color from theme (dark green for light mode, lighter green for dark mode)
        comp_color = "dark green"
        if self.app and hasattr(self.app, 'theme_colors'):
            comp_color = self.app.theme_colors.get("comp_color", "dark green")
        
        # Add projection summary
        self._projection_table.add_row({'metric': 'Projected Hours', 'value': f"{total_hours:.1f} hours ({total_days} days)"})
        self._projection_table.add_row({'metric': 'Projected Studies', 'value': f"{projected_studies:.1f}"})
        self._projection_table.add_row({'metric': 'Projected RVU', 'value': f"{projected_rvu:.2f}"})
        self._projection_table.add_row(
            {'metric': 'Projected Compensation', 'value': f"${projected_compensation:,.2f}"},
            cell_text_colors={'value': comp_color}
        )
        self._projection_table.add_row({'metric': '', 'value': ''})  # Spacer
        
        # Add historical averages used for projection
        self._projection_table.add_row({'metric': 'Based on Historical Data:', 'value': ''})
        self._projection_table.add_row({'metric': '', 'value': f"{historical_studies} studies over {historical_hours:.1f} hours"})
        self._projection_table.add_row({'metric': '', 'value': f"Average: {studies_per_hour:.2f} studies/hour"})
        self._projection_table.add_row({'metric': '', 'value': f"Average: {rvu_per_hour:.2f} RVU/hour"})
        self._projection_table.add_row(
            {'metric': '', 'value': f"Average: ${compensation_per_hour:.2f}/hour"},
            cell_text_colors={'value': comp_color}
        )
        self._projection_table.add_row({'metric': '', 'value': ''})  # Spacer
        
        # Projected study type breakdown
        self._projection_table.add_row({'metric': 'Projected Study Type Breakdown:', 'value': ''})
        sorted_study_types = sorted(study_type_distribution.items(), 
                                   key=lambda x: x[0])  # Sort by study type name
        
        # Show ALL study types, not just top 10
        for st, stats in sorted_study_types:
            projected_count = stats['percentage'] * projected_studies if historical_studies > 0 else 0
            projected_rvu_type = stats['percentage'] * projected_rvu if historical_studies > 0 else 0
            projected_comp_type = stats['percentage'] * projected_compensation if historical_studies > 0 else 0
            # Format with dollar amount - only the dollar amount will be colored green
            self._projection_table.add_row({
                'metric': f"  {st}",
                'value': f"{projected_count:.1f} studies, {projected_rvu_type:.2f} RVU, ${projected_comp_type:,.2f}"
            }, cell_text_colors={'value': comp_color})
        
        self._projection_table.update_data()
    
    def _calculate_historical_hours(self, records: List[dict], date_range_start: datetime = None, date_range_end: datetime = None) -> float:
        """
        Calculate total hours worked from historical records.
        
        Args:
            records: List of records in the date range
            date_range_start: Start of the date range being analyzed (to clip shifts)
            date_range_end: End of the date range being analyzed (to clip shifts)
        """
        # Get all shifts that contain these records
        all_shifts = []
        current_shift = self.data_manager.data.get("current_shift", {})
        if current_shift.get("shift_start"):
            all_shifts.append(current_shift)
        all_shifts.extend(self.data_manager.data.get("shifts", []))
        
        # Find unique shifts
        shifts_with_records = {}
        for r in records:
            try:
                record_time = datetime.fromisoformat(r.get("time_performed", ""))
                for shift in all_shifts:
                    shift_start_str = shift.get("shift_start")
                    if not shift_start_str:
                        continue
                    shift_start = datetime.fromisoformat(shift_start_str)
                    shift_end_str = shift.get("shift_end")
                    if shift_end_str:
                        shift_end = datetime.fromisoformat(shift_end_str)
                        if shift_start <= record_time <= shift_end:
                            shifts_with_records[shift_start_str] = shift
                    else:
                        if record_time >= shift_start:
                            shifts_with_records[shift_start_str] = shift
            except:
                continue
        
        # Sum durations, clipping to date range to avoid counting overlapping/shared time
        total_hours = 0.0
        # Track time periods to merge overlaps
        time_periods = []
        
        for shift_start_str, shift in shifts_with_records.items():
            try:
                shift_start = datetime.fromisoformat(shift_start_str)
                shift_end_str = shift.get("shift_end")
                if shift_end_str:
                    shift_end = datetime.fromisoformat(shift_end_str)
                else:
                    # Current shift - use CURRENT TIME, not last record time
                    # This ensures accurate hours worked for incomplete shifts
                    # (Using last record time would understate hours and inflate RVU/hour rate)
                    shift_end = datetime.now()
                    logger.debug(f"Using current time for incomplete shift duration calculation")
                
                # Clip shift to date range if provided
                if date_range_start is not None:
                    shift_start = max(shift_start, date_range_start)
                if date_range_end is not None:
                    shift_end = min(shift_end, date_range_end)
                
                # Only count if shift still has valid duration after clipping
                if shift_start < shift_end:
                    time_periods.append((shift_start, shift_end))
            except:
                continue
        
        # Merge overlapping time periods to avoid double-counting
        if time_periods:
            # Sort by start time
            time_periods.sort(key=lambda x: x[0])
            
            # Merge overlaps
            merged_periods = []
            current_start, current_end = time_periods[0]
            
            for start, end in time_periods[1:]:
                if start <= current_end:
                    # Overlaps or adjacent - merge
                    current_end = max(current_end, end)
                else:
                    # No overlap - save current and start new
                    merged_periods.append((current_start, current_end))
                    current_start, current_end = start, end
            
            # Don't forget the last period
            merged_periods.append((current_start, current_end))
            
            # Sum the merged periods
            for start, end in merged_periods:
                total_hours += (end - start).total_seconds() / 3600
        
        return total_hours
    
    def _populate_comparison_shifts(self, preserve_selection=True):
        """Populate the comparison shift comboboxes with available shifts.
        
        Args:
            preserve_selection: If True, keeps current selections if they're still valid
        """
        try:
            # Save current selections if preserving
            current_idx1 = self.comparison_shift1_index if preserve_selection else None
            current_idx2 = self.comparison_shift2_index if preserve_selection else None
            
            # Get all shifts from database (including current if it exists)
            all_shifts = []
            
            # Get current shift if it exists
            current_shift = self.data_manager.db.get_current_shift()
            if current_shift:
                all_shifts.append(current_shift)
            
            # Get historical shifts
            historical_shifts = self.data_manager.db.get_all_shifts()
            all_shifts.extend(historical_shifts)
            
            if not all_shifts:
                return
            
            # Format shift options for display
            shift_options = []
            for i, shift in enumerate(all_shifts):
                start = datetime.fromisoformat(shift['shift_start'])
                
                # Label with "Current" or date/time
                if shift.get('is_current'):
                    label = f"Current - {start.strftime('%a %m/%d %I:%M%p')}"
                else:
                    label = start.strftime("%a %m/%d %I:%M%p")
                
                # Get records for this shift and calculate stats
                records = self.data_manager.db.get_records_for_shift(shift['id'])
                # Expand multi-accession records
                records = self._expand_multi_accession_records(records)
                
                study_count = len(records)
                total_rvu = sum(r.get('rvu', 0) for r in records)
                
                display_text = f"{label} - {total_rvu:.1f} RVU ({study_count} studies)"
                shift_options.append(display_text)
            
            # Store the shifts list for later reference
            self.comparison_shifts_list = all_shifts
            
            # Update comboboxes
            self.comparison_shift1_combo['values'] = shift_options
            self.comparison_shift2_combo['values'] = shift_options
            
            # Set selections: use preserved selections if valid, otherwise defaults
            if preserve_selection and current_idx1 is not None and current_idx1 < len(all_shifts):
                self.comparison_shift1_index = current_idx1
            elif len(all_shifts) >= 1:
                self.comparison_shift1_index = 0
            
            if preserve_selection and current_idx2 is not None and current_idx2 < len(all_shifts):
                self.comparison_shift2_index = current_idx2
            elif len(all_shifts) >= 2:
                self.comparison_shift2_index = 1
            
            # Set default selections if not already set
            if self.comparison_shift1_index is None and len(all_shifts) >= 1:
                self.comparison_shift1_index = 0
            if self.comparison_shift2_index is None and len(all_shifts) >= 2:
                self.comparison_shift2_index = 1
            
            # Apply selections to comboboxes
            if self.comparison_shift1_index is not None and self.comparison_shift1_index < len(shift_options):
                self.comparison_shift1_combo.current(self.comparison_shift1_index)
            
            if self.comparison_shift2_index is not None and self.comparison_shift2_index < len(shift_options):
                self.comparison_shift2_combo.current(self.comparison_shift2_index)
            
        except Exception as e:
            logger.error(f"Error populating comparison shifts: {e}")
    
    def on_comparison_shift_selected(self, event=None):
        """Handle shift selection change in comparison mode."""
        try:
            # Get current selections
            idx1 = self.comparison_shift1_combo.current()
            idx2 = self.comparison_shift2_combo.current()
            
            # Only update if valid indices
            if idx1 >= 0:
                self.comparison_shift1_index = idx1
            if idx2 >= 0:
                self.comparison_shift2_index = idx2
            
            # Refresh the comparison view
            if self.view_mode.get() == "comparison":
                self._display_comparison()
                        
        except Exception as e:
            logger.error(f"Error handling comparison shift selection: {e}")
    
    def _update_comparison_graphs(self, changed_element: str):
        """Update comparison graphs with scroll position preservation.
        
        Simple approach: save scroll position, do full redraw, restore position.
        """
        try:
            # Get current scroll position if available
            canvas = getattr(self, '_comparison_scroll_canvas', None)
            scroll_pos = canvas.yview()[0] if canvas else 0
            
            # Do full redraw (simpler and more reliable than incremental)
            self._display_comparison()
            
            # Restore scroll position after a brief delay to ensure widgets are rendered
            if canvas:
                self.window.after(10, lambda: self._restore_scroll_position(scroll_pos))
                
        except Exception as e:
            logger.error(f"Error updating comparison graphs: {e}")
            self._display_comparison()
    
    def _restore_scroll_position(self, position):
        """Restore scroll position after redraw."""
        try:
            canvas = getattr(self, '_comparison_scroll_canvas', None)
            if canvas:
                canvas.yview_moveto(position)
        except:
            pass
    
    def _display_comparison(self):
        """Display shift comparison view with graphs and numerical comparisons."""
        if not HAS_MATPLOTLIB:
            # Show message if matplotlib is not available
            error_label = ttk.Label(self.table_frame, 
                                   text="Matplotlib is required for comparison view.\nPlease install: pip install matplotlib",
                                   font=("Arial", 12), foreground="red")
            error_label.pack(pady=50)
            self.summary_label.config(text="Comparison view unavailable")
            return
        
        # Get the two shifts to compare from the stored list
        shifts = getattr(self, 'comparison_shifts_list', [])
        if not shifts:
            error_label = ttk.Label(self.table_frame, 
                                   text="No shifts available for comparison",
                                   font=("Arial", 12), foreground="red")
            error_label.pack(pady=50)
            self.summary_label.config(text="No shifts available")
            return
        
        if len(shifts) < 2:
            error_label = ttk.Label(self.table_frame, 
                                   text="At least two shifts are required for comparison.\nComplete more shifts to use this feature.",
                                   font=("Arial", 12), foreground="orange")
            error_label.pack(pady=50)
            self.summary_label.config(text="Need at least 2 shifts to compare")
            return
        
        if self.comparison_shift1_index is None or self.comparison_shift2_index is None:
            self.summary_label.config(text="Please select two shifts to compare")
            return
        
        if self.comparison_shift1_index >= len(shifts) or self.comparison_shift2_index >= len(shifts):
            self.summary_label.config(text="Invalid shift selection")
            return
        
        shift1 = shifts[self.comparison_shift1_index]
        shift2 = shifts[self.comparison_shift2_index]
        
        # Get records for each shift
        records1 = self.data_manager.db.get_records_for_shift(shift1['id'])
        records2 = self.data_manager.db.get_records_for_shift(shift2['id'])
        
        # Expand multi-accession records
        records1 = self._expand_multi_accession_records(records1)
        records2 = self._expand_multi_accession_records(records2)
        
        # Clear existing content
        for widget in self.table_frame.winfo_children():
            widget.destroy()
        
        # Create scrollable frame for content
        canvas = tk.Canvas(self.table_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.table_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        # Store references for incremental updates
        self._comparison_scroll_canvas = canvas
        self._comparison_scrollable_frame = scrollable_frame
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Enable mouse wheel scrolling when mouse is over the canvas or its children
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        # Helper to bind mousewheel to a widget and all its children recursively
        def bind_mousewheel_recursive(widget):
            widget.bind("<MouseWheel>", on_mousewheel)
            for child in widget.winfo_children():
                bind_mousewheel_recursive(child)
        
        # Bind to the canvas and all widgets in scrollable_frame
        canvas.bind("<MouseWheel>", on_mousewheel)
        bind_mousewheel_recursive(scrollable_frame)
        
        # Store reference to unbind later if needed
        self._comparison_mousewheel_canvas = canvas
        self._comparison_mousewheel_frame = scrollable_frame
        self._comparison_mousewheel_callback = on_mousewheel
        
        # Initialize modality selection if not exists
        if not hasattr(self, 'comparison_modality_filter'):
            self.comparison_modality_filter = tk.StringVar(value="all")
        
        # Get theme colors for dark mode support
        theme_colors = self.app.get_theme_colors()
        is_dark = theme_colors['bg'] == '#2b2b2b'
        
        # Calculate figure width based on available space (no controls panel anymore)
        window_width = self.window.winfo_width() if self.window.winfo_width() > 1 else 1350
        available_width = (window_width - 320) / 100  # Just left panel + padding
        fig_width = max(8, min(available_width, 12))  # Better width range
        
        selected_modality = self.comparison_modality_filter.get()
        
        # Process data for graphs and store for incremental updates
        data1 = self._process_shift_data_for_comparison(shift1, records1)
        data2 = self._process_shift_data_for_comparison(shift2, records2)
        
        # Store for incremental graph updates
        self._comparison_data1 = data1
        self._comparison_data2 = data2
        
        # Get rounded shift start times for x-axis display
        shift1_start_rounded = data1['shift_start_rounded']
        shift2_start_rounded = data2['shift_start_rounded']
        
        # Determine if shifts have matching times (compare rounded hours)
        use_actual_time = shift1_start_rounded.hour == shift2_start_rounded.hour
        
        # Align time ranges: ignore stragglers and use common max hour
        # For typical night shifts (11pm-8am = 9 hours), cap at 9 hours
        # Otherwise, use min of both max_hours to ignore stragglers
        ideal_max_hour = 9  # 11pm to 8am
        max_hour1 = data1['max_hour']
        max_hour2 = data2['max_hour']
        
        # If both shifts are close to ideal length, use ideal; otherwise use minimum to avoid stragglers
        if max_hour1 >= ideal_max_hour - 1 and max_hour2 >= ideal_max_hour - 1:
            common_max_hour = ideal_max_hour
        else:
            # Use minimum + 1 to allow some flexibility but cut stragglers
            common_max_hour = min(max_hour1, max_hour2) + 1
        
        # Pad/trim data to common length
        self._align_shift_data(data1, common_max_hour)
        self._align_shift_data(data2, common_max_hour)
        
        # Store canvas widgets for cleanup
        canvas_widgets = []
        
        # === FIRST FIGURE: RVU Graphs (Graph 1 & 2) ===
        # Control panel above first two graphs
        toggle_frame1 = ttk.Frame(scrollable_frame)
        toggle_frame1.pack(fill=tk.X, padx=10, pady=(5, 2))
        ttk.Label(toggle_frame1, text="Graph Mode:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(toggle_frame1, text="Accumulation", variable=self.comparison_graph_mode,
                       value="accumulation", command=lambda: self._update_comparison_graphs('mode')).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(toggle_frame1, text="Average", variable=self.comparison_graph_mode,
                       value="average", command=lambda: self._update_comparison_graphs('mode')).pack(side=tk.LEFT, padx=5)
        
        # Create first figure with 2 subplots
        fig1 = Figure(figsize=(fig_width, 8), dpi=100)
        fig1.patch.set_facecolor(theme_colors['bg'])
        
        ax1 = fig1.add_subplot(2, 1, 1)  # RVU accumulation
        ax2 = fig1.add_subplot(2, 1, 2)  # Delta from average RVU
        
        # Apply dark mode colors
        for ax in [ax1, ax2]:
            ax.set_facecolor(theme_colors['bg'])
            ax.tick_params(colors=theme_colors['fg'])
            ax.xaxis.label.set_color(theme_colors['fg'])
            ax.yaxis.label.set_color(theme_colors['fg'])
            ax.title.set_color(theme_colors['fg'])
            for spine in ax.spines.values():
                spine.set_edgecolor(theme_colors['fg'] if is_dark else '#cccccc')
        
        # Plot 1: RVU Accumulation/Average
        self._plot_rvu_progression(ax1, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
        
        # Plot 2: Delta from Average RVU
        self._plot_rvu_delta(ax2, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
        
        fig1.tight_layout(pad=2.5)
        
        # Embed first figure
        canvas_widget1 = FigureCanvasTkAgg(fig1, master=scrollable_frame)
        canvas_widget1.draw()
        canvas_widget1.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))
        canvas_widgets.append(canvas_widget1)
        
        # === SECOND FIGURE: Study Count Graph (Graph 3) ===
        # Control panel above third graph
        toggle_frame2 = ttk.Frame(scrollable_frame)
        toggle_frame2.pack(fill=tk.X, padx=10, pady=(5, 2))
        ttk.Label(toggle_frame2, text="Modality Filter:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
        
        # Get all unique modalities from both shifts
        all_modalities_set = set()
        for mod_dict in [data1['modality_cumulative'], data2['modality_cumulative']]:
            all_modalities_set.update(mod_dict.keys())
        all_modalities = sorted(list(all_modalities_set))
        
        # "All" option
        ttk.Radiobutton(toggle_frame2, text="All", variable=self.comparison_modality_filter,
                       value="all", command=lambda: self._update_comparison_graphs('modality')).pack(side=tk.LEFT, padx=2)
        
        # Individual modality options (limit to 6 for space)
        for modality in all_modalities[:6]:
            ttk.Radiobutton(toggle_frame2, text=modality, variable=self.comparison_modality_filter,
                           value=modality, command=lambda m=modality: self._update_comparison_graphs('modality')).pack(side=tk.LEFT, padx=2)
        
        # Create second figure with 1 subplot
        fig2 = Figure(figsize=(fig_width, 4), dpi=100)
        fig2.patch.set_facecolor(theme_colors['bg'])
        
        ax3 = fig2.add_subplot(1, 1, 1)  # Study count
        
        # Apply dark mode colors
        ax3.set_facecolor(theme_colors['bg'])
        ax3.tick_params(colors=theme_colors['fg'])
        ax3.xaxis.label.set_color(theme_colors['fg'])
        ax3.yaxis.label.set_color(theme_colors['fg'])
        ax3.title.set_color(theme_colors['fg'])
        for spine in ax3.spines.values():
            spine.set_edgecolor(theme_colors['fg'] if is_dark else '#cccccc')
        
        # Plot 3: Study count (summed if "all", by modality if specific)
        if selected_modality == "all":
            # Sum all modalities = total studies
            self._plot_total_studies(ax3, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
        else:
            self._plot_modality_progression(ax3, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, selected_modality, theme_colors)
        
        fig2.tight_layout(pad=2.5)
        
        # Embed second figure
        canvas_widget2 = FigureCanvasTkAgg(fig2, master=scrollable_frame)
        canvas_widget2.draw()
        canvas_widget2.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))
        canvas_widgets.append(canvas_widget2)
        
        # Store references for cleanup
        self._comparison_canvas_widgets = canvas_widgets
        
        # Re-bind mousewheel to newly created widgets (matplotlib canvases)
        if hasattr(self, '_comparison_mousewheel_callback'):
            def bind_mousewheel_recursive(widget):
                widget.bind("<MouseWheel>", self._comparison_mousewheel_callback)
                for child in widget.winfo_children():
                    bind_mousewheel_recursive(child)
            
            bind_mousewheel_recursive(scrollable_frame)
        
        # Numerical comparison below graphs
        comparison_frame = ttk.LabelFrame(scrollable_frame, text="Numerical Comparison", padding="10")
        comparison_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
        
        # Create comparison table (use original shift start times for display)
        shift1_start = datetime.fromisoformat(shift1['shift_start'])
        shift2_start = datetime.fromisoformat(shift2['shift_start'])
        self._create_comparison_table(comparison_frame, shift1, shift2, records1, records2, 
                                     shift1_start, shift2_start)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Update summary
        total1 = len(records1)
        total2 = len(records2)
        rvu1 = sum(r.get("rvu", 0) for r in records1)
        rvu2 = sum(r.get("rvu", 0) for r in records2)
        
        self.summary_label.config(
            text=f"Current: {total1} studies, {rvu1:.1f} RVU  |  Prior: {total2} studies, {rvu2:.1f} RVU"
        )
    
    def _align_shift_data(self, data: dict, target_max_hour: int):
        """Align shift data to a target maximum hour by padding or trimming."""
        current_max = data['max_hour']
        
        if current_max < target_max_hour:
            # Pad with zeros/last values
            padding_needed = target_max_hour - current_max
            last_rvu = data['cumulative_rvu'][-1] if data['cumulative_rvu'] else 0
            last_studies = data['cumulative_studies'][-1] if data['cumulative_studies'] else 0
            
            for _ in range(padding_needed):
                data['cumulative_rvu'].append(last_rvu)
                data['cumulative_studies'].append(last_studies)
                # Pad averages (recalculate)
                hour = len(data['avg_rvu'])
                data['avg_rvu'].append(last_rvu / (hour + 1) if hour >= 0 else 0)
                data['avg_studies'].append(last_studies / (hour + 1) if hour >= 0 else 0)
                
                # Pad modality data
                for modality in data['modality_cumulative']:
                    last_val = data['modality_cumulative'][modality][-1] if data['modality_cumulative'][modality] else 0
                    data['modality_cumulative'][modality].append(last_val)
        
        elif current_max > target_max_hour:
            # Trim excess data
            data['cumulative_rvu'] = data['cumulative_rvu'][:target_max_hour + 1]
            data['cumulative_studies'] = data['cumulative_studies'][:target_max_hour + 1]
            data['avg_rvu'] = data['avg_rvu'][:target_max_hour + 1]
            data['avg_studies'] = data['avg_studies'][:target_max_hour + 1]
            
            for modality in data['modality_cumulative']:
                data['modality_cumulative'][modality] = data['modality_cumulative'][modality][:target_max_hour + 1]
        
        # Update max_hour
        data['max_hour'] = target_max_hour
    
    def _process_shift_data_for_comparison(self, shift: dict, records: List[dict]) -> dict:
        """Process shift data into hourly buckets for comparison graphs."""
        shift_start = datetime.fromisoformat(shift['shift_start'])
        
        # Normalize shift to standard 11pm start
        # If shift starts between 9pm-1am, round to 11pm
        # If shift starts outside this range, just round to nearest hour
        hour = shift_start.hour
        if 21 <= hour <= 23 or 0 <= hour <= 1:
            # Round to 11pm of the appropriate day
            if hour <= 1:  # After midnight, use previous day's 11pm
                shift_start_rounded = shift_start.replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=1)
            else:
                shift_start_rounded = shift_start.replace(hour=23, minute=0, second=0, microsecond=0)
        else:
            # For other start times, just round down to nearest hour
            shift_start_rounded = shift_start.replace(minute=0, second=0, microsecond=0)
        
        # Initialize hourly data structures
        hourly_data = {}
        
        for record in records:
            time_finished = datetime.fromisoformat(record['time_finished'])
            elapsed_hours = (time_finished - shift_start_rounded).total_seconds() / 3600
            hour_bucket = int(elapsed_hours)  # 0, 1, 2, etc.
            
            if hour_bucket not in hourly_data:
                hourly_data[hour_bucket] = {
                    'rvu': 0,
                    'study_count': 0,
                    'modalities': {}
                }
            
            hourly_data[hour_bucket]['rvu'] += record.get('rvu', 0)
            hourly_data[hour_bucket]['study_count'] += 1
            
            # Track by modality - extract from study_type
            study_type = record.get('study_type', 'Unknown')
            modality = study_type.split()[0] if study_type else "Unknown"
            # Handle "Multiple XR" -> extract "XR"
            if modality == "Multiple" and len(study_type.split()) > 1:
                modality = study_type.split()[1]
            
            if modality not in hourly_data[hour_bucket]['modalities']:
                hourly_data[hour_bucket]['modalities'][modality] = 0
            hourly_data[hour_bucket]['modalities'][modality] += 1
        
        # Calculate cumulative and average data
        max_hour = max(hourly_data.keys()) if hourly_data else 0
        cumulative_rvu = []
        cumulative_studies = []
        avg_rvu = []
        avg_studies = []
        modality_cumulative = {}
        
        for hour in range(max_hour + 1):
            if hour in hourly_data:
                # Accumulation
                prev_rvu = cumulative_rvu[-1] if cumulative_rvu else 0
                prev_studies = cumulative_studies[-1] if cumulative_studies else 0
                cumulative_rvu.append(prev_rvu + hourly_data[hour]['rvu'])
                cumulative_studies.append(prev_studies + hourly_data[hour]['study_count'])
                
                # Average (per hour up to this point)
                avg_rvu.append(cumulative_rvu[-1] / (hour + 1))
                avg_studies.append(cumulative_studies[-1] / (hour + 1))
                
                # Modality cumulative
                for modality, count in hourly_data[hour]['modalities'].items():
                    if modality not in modality_cumulative:
                        modality_cumulative[modality] = []
                    prev_count = modality_cumulative[modality][-1] if modality_cumulative[modality] else 0
                    modality_cumulative[modality].append(prev_count + count)
            else:
                # No studies in this hour, carry forward previous values
                cumulative_rvu.append(cumulative_rvu[-1] if cumulative_rvu else 0)
                cumulative_studies.append(cumulative_studies[-1] if cumulative_studies else 0)
                avg_rvu.append(cumulative_rvu[-1] / (hour + 1) if cumulative_rvu else 0)
                avg_studies.append(cumulative_studies[-1] / (hour + 1) if cumulative_studies else 0)
                
                for modality in modality_cumulative:
                    modality_cumulative[modality].append(modality_cumulative[modality][-1] if modality_cumulative[modality] else 0)
        
        # Calculate average RVU per study for the shift
        total_rvu = sum(r.get('rvu', 0) for r in records)
        total_studies = len(records)
        avg_rvu_per_study = total_rvu / total_studies if total_studies > 0 else 0
        
        return {
            'hourly_data': hourly_data,
            'cumulative_rvu': cumulative_rvu,
            'cumulative_studies': cumulative_studies,
            'avg_rvu': avg_rvu,
            'avg_studies': avg_studies,
            'modality_cumulative': modality_cumulative,
            'avg_rvu_per_study': avg_rvu_per_study,
            'max_hour': max_hour,
            'shift_start_rounded': shift_start_rounded
        }
    
    def _plot_rvu_progression(self, ax, data1: dict, data2: dict, shift1_start: datetime, 
                             shift2_start: datetime, use_actual_time: bool, theme_colors: dict = None):
        """Plot RVU accumulation or average progression."""
        mode = self.comparison_graph_mode.get()
        
        if mode == "accumulation":
            y1 = data1['cumulative_rvu']
            y2 = data2['cumulative_rvu']
            ylabel = "Cumulative RVU"
            title = "RVU Accumulation"
        else:
            y1 = data1['avg_rvu']
            y2 = data2['avg_rvu']
            ylabel = "Average RVU per Hour"
            title = "RVU Average per Hour"
        
        hours1 = list(range(len(y1)))
        hours2 = list(range(len(y2)))
        
        if use_actual_time:
            x1 = [(shift1_start + timedelta(hours=h)).strftime("%H:%M") for h in hours1]
            x2 = [(shift2_start + timedelta(hours=h)).strftime("%H:%M") for h in hours2]
        else:
            x1 = [f"Hour {h}" for h in hours1]
            x2 = [f"Hour {h}" for h in hours2]
        
        ax.plot(range(len(y1)), y1, color='#4472C4', linewidth=2, marker='o', label='Shift 1')
        ax.plot(range(len(y2)), y2, color='#9966CC', linewidth=2, marker='s', label='Shift 2')
        
        ax.set_xlabel("Time" if use_actual_time else "Hours from Start", fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Set x-axis to start at zero with no left padding
        max_len = max(len(x1), len(x2))
        if max_len > 0:
            ax.set_xlim(0, max_len - 1)
            ax.margins(x=0.01)
        
        # Set x-axis labels (show every other hour for readability)
        if max_len > 0:
            step = max(1, max_len // 8)
            ax.set_xticks(range(0, max_len, step))
            ax.set_xticklabels([x1[i] if i < len(x1) else x2[i] if i < len(x2) else "" 
                               for i in range(0, max_len, step)], rotation=45, ha='right', fontsize=8)
    
    def _plot_rvu_delta(self, ax, data1: dict, data2: dict, shift1_start: datetime, 
                       shift2_start: datetime, use_actual_time: bool, theme_colors: dict = None):
        """Plot hourly RVU delta from average."""
        # Calculate average RVU per hour for each shift
        total_hours1 = data1['max_hour'] + 1 if data1['max_hour'] >= 0 else 1
        total_hours2 = data2['max_hour'] + 1 if data2['max_hour'] >= 0 else 1
        total_rvu1 = data1['cumulative_rvu'][-1] if data1['cumulative_rvu'] else 0
        total_rvu2 = data2['cumulative_rvu'][-1] if data2['cumulative_rvu'] else 0
        avg_rvu_per_hour1 = total_rvu1 / total_hours1 if total_hours1 > 0 else 0
        avg_rvu_per_hour2 = total_rvu2 / total_hours2 if total_hours2 > 0 else 0
        
        # Calculate hourly RVU (RVU earned in each specific hour)
        delta1 = []
        delta2 = []
        
        for hour in range(len(data1['cumulative_rvu'])):
            if hour == 0:
                hourly_rvu = data1['cumulative_rvu'][0]
            else:
                hourly_rvu = data1['cumulative_rvu'][hour] - data1['cumulative_rvu'][hour-1]
            delta1.append(hourly_rvu - avg_rvu_per_hour1)
        
        for hour in range(len(data2['cumulative_rvu'])):
            if hour == 0:
                hourly_rvu = data2['cumulative_rvu'][0]
            else:
                hourly_rvu = data2['cumulative_rvu'][hour] - data2['cumulative_rvu'][hour-1]
            delta2.append(hourly_rvu - avg_rvu_per_hour2)
        
        hours1 = list(range(len(delta1)))
        hours2 = list(range(len(delta2)))
        
        if use_actual_time:
            x1 = [(shift1_start + timedelta(hours=h)).strftime("%H:%M") for h in hours1]
            x2 = [(shift2_start + timedelta(hours=h)).strftime("%H:%M") for h in hours2]
        else:
            x1 = [f"Hour {h}" for h in hours1]
            x2 = [f"Hour {h}" for h in hours2]
        
        ax.plot(range(len(delta1)), delta1, color='#4472C4', linewidth=2, marker='o', label='Shift 1')
        ax.plot(range(len(delta2)), delta2, color='#9966CC', linewidth=2, marker='s', label='Shift 2')
        ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)
        
        ax.set_xlabel("Time" if use_actual_time else "Hours from Start", fontsize=10)
        ax.set_ylabel("Hourly RVU Delta from Average", fontsize=10)
        ax.set_title("Hourly RVU Delta", fontsize=12, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Set x-axis to start at zero with no left padding
        max_len = max(len(x1), len(x2))
        if max_len > 0:
            ax.set_xlim(0, max_len - 1)
            ax.margins(x=0.01)
        
        # Set x-axis labels
        if max_len > 0:
            step = max(1, max_len // 8)
            ax.set_xticks(range(0, max_len, step))
            ax.set_xticklabels([x1[i] if i < len(x1) else x2[i] if i < len(x2) else "" 
                               for i in range(0, max_len, step)], rotation=45, ha='right', fontsize=8)
    
    def _plot_modality_progression(self, ax, data1: dict, data2: dict, shift1_start: datetime, 
                                   shift2_start: datetime, use_actual_time: bool, modality_filter: str = "all", theme_colors: dict = None):
        """Plot study accumulation by modality."""
        mode = self.comparison_graph_mode.get()
        
        # Determine which modalities to plot based on filter
        if modality_filter == "all":
            # Get all modalities from both shifts, sorted by total count
            all_modalities = {}
            for mod_dict in [data1['modality_cumulative'], data2['modality_cumulative']]:
                for modality, counts in mod_dict.items():
                    if modality not in all_modalities:
                        all_modalities[modality] = 0
                    all_modalities[modality] += max(counts) if counts else 0
            
            # Sort by count and plot all modalities (limit to 8 for readability)
            sorted_modalities = sorted(all_modalities.items(), key=lambda x: x[1], reverse=True)
            modalities_to_plot = [m[0] for m in sorted_modalities[:8]]
            title_suffix = " (All)" if len(sorted_modalities) <= 8 else " (Top 8)"
        else:
            # Plot only the selected modality
            modalities_to_plot = [modality_filter]
            title_suffix = f" ({modality_filter})"
        
        if not modalities_to_plot:
            ax.text(0.5, 0.5, 'No modality data available', ha='center', va='center', transform=ax.transAxes)
            return
        
        # Plot each modality
        colors_current = ['#4472C4', '#70AD47', '#FFC000', '#E74C3C', '#9B59B6']
        colors_prior = ['#9966CC', '#C06090', '#FF9966', '#F39C12', '#3498DB']
        
        for i, modality in enumerate(modalities_to_plot):
            color_idx = i % len(colors_current)
            
            if modality in data1['modality_cumulative']:
                y1 = data1['modality_cumulative'][modality]
                if mode == "average":
                    y1 = [count / (hour + 1) for hour, count in enumerate(y1)]
                ax.plot(range(len(y1)), y1, color=colors_current[color_idx], linewidth=1.5, 
                       marker='o', markersize=4, label=f'{modality} (Shift 1)', alpha=0.8)
            
            if modality in data2['modality_cumulative']:
                y2 = data2['modality_cumulative'][modality]
                if mode == "average":
                    y2 = [count / (hour + 1) for hour, count in enumerate(y2)]
                ax.plot(range(len(y2)), y2, color=colors_prior[color_idx], linewidth=1.5, 
                       marker='s', markersize=4, label=f'{modality} (Shift 2)', alpha=0.8, linestyle='--')
        
        ylabel = "Average Studies per Hour" if mode == "average" else "Cumulative Studies"
        title = f"Study Count by Modality{title_suffix}"
        
        ax.set_xlabel("Time" if use_actual_time else "Hours from Start", fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend(fontsize=8, loc='best')
        grid_color = theme_colors['fg'] if theme_colors else 'gray'
        ax.grid(True, alpha=0.2, color=grid_color)
        
        # Set x-axis to start at zero with no left padding
        max_len = max(len(data1['cumulative_rvu']), len(data2['cumulative_rvu']))
        if max_len > 0:
            ax.set_xlim(0, max_len - 1)
            ax.margins(x=0.01)
        
        # Set x-axis labels
        if max_len > 0:
            step = max(1, max_len // 8)
            ax.set_xticks(range(0, max_len, step))
            if use_actual_time:
                labels = [(shift1_start + timedelta(hours=h)).strftime("%H:%M") for h in range(0, max_len, step)]
            else:
                labels = [f"Hour {h}" for h in range(0, max_len, step)]
            ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    
    def _plot_total_studies(self, ax, data1: dict, data2: dict, shift1_start: datetime, 
                           shift2_start: datetime, use_actual_time: bool, theme_colors: dict = None):
        """Plot total study accumulation or average."""
        mode = self.comparison_graph_mode.get()
        
        if mode == "accumulation":
            y1 = data1['cumulative_studies']
            y2 = data2['cumulative_studies']
            ylabel = "Cumulative Studies"
            title = "Total Study Accumulation"
        else:
            y1 = data1['avg_studies']
            y2 = data2['avg_studies']
            ylabel = "Average Studies per Hour"
            title = "Average Study Rate"
        
        hours1 = list(range(len(y1)))
        hours2 = list(range(len(y2)))
        
        if use_actual_time:
            x1 = [(shift1_start + timedelta(hours=h)).strftime("%H:%M") for h in hours1]
            x2 = [(shift2_start + timedelta(hours=h)).strftime("%H:%M") for h in hours2]
        else:
            x1 = [f"Hour {h}" for h in hours1]
            x2 = [f"Hour {h}" for h in hours2]
        
        ax.plot(range(len(y1)), y1, color='#4472C4', linewidth=2, marker='o', label='Shift 1')
        ax.plot(range(len(y2)), y2, color='#9966CC', linewidth=2, marker='s', label='Shift 2')
        
        ax.set_xlabel("Time" if use_actual_time else "Hours from Start", fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Set x-axis to start at zero with tight margins
        max_len = max(len(x1), len(x2))
        ax.set_xlim(0, max_len - 1)
        ax.margins(x=0.01)
        
        # Set x-axis labels
        if max_len > 0:
            step = max(1, max_len // 8)
            ax.set_xticks(range(0, max_len, step))
            ax.set_xticklabels([x1[i] if i < len(x1) else x2[i] if i < len(x2) else "" 
                               for i in range(0, max_len, step)], rotation=45, ha='right', fontsize=8)
    
    def _create_comparison_table(self, parent: ttk.Frame, shift1: dict, shift2: dict, 
                                records1: List[dict], records2: List[dict],
                                shift1_start: datetime, shift2_start: datetime):
        """Create numerical comparison table below graphs."""
        # Calculate statistics
        total_rvu1 = sum(r.get('rvu', 0) for r in records1)
        total_rvu2 = sum(r.get('rvu', 0) for r in records2)
        total_studies1 = len(records1)
        total_studies2 = len(records2)
        
        # Calculate compensation (reuse the app's calculation if available)
        total_comp1 = sum(self._calculate_study_compensation(r) for r in records1)
        total_comp2 = sum(self._calculate_study_compensation(r) for r in records2)
        
        # Count by modality - extract from study_type
        modality_counts1 = {}
        modality_counts2 = {}
        for r in records1:
            study_type = r.get('study_type', 'Unknown')
            mod = study_type.split()[0] if study_type else "Unknown"
            if mod == "Multiple" and len(study_type.split()) > 1:
                mod = study_type.split()[1]
            modality_counts1[mod] = modality_counts1.get(mod, 0) + 1
        for r in records2:
            study_type = r.get('study_type', 'Unknown')
            mod = study_type.split()[0] if study_type else "Unknown"
            if mod == "Multiple" and len(study_type.split()) > 1:
                mod = study_type.split()[1]
            modality_counts2[mod] = modality_counts2.get(mod, 0) + 1
        
        # Create grid layout
        headers = ["Metric", "Current Shift", "Prior Shift", "Difference"]
        for col, header in enumerate(headers):
            label = ttk.Label(parent, text=header, font=("Arial", 10, "bold"))
            label.grid(row=0, column=col, padx=10, pady=5, sticky=tk.W)
        
        row = 1
        
        # Shift dates
        ttk.Label(parent, text="Date/Time:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=shift1_start.strftime("%a %m/%d %I:%M%p")).grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=shift2_start.strftime("%a %m/%d %I:%M%p")).grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text="-").grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
        row += 1
        
        # Total RVU
        ttk.Label(parent, text="Total RVU:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"{total_rvu1:.2f}").grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"{total_rvu2:.2f}").grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
        diff_rvu = total_rvu1 - total_rvu2
        diff_color = "green" if diff_rvu > 0 else "red" if diff_rvu < 0 else "black"
        ttk.Label(parent, text=f"{diff_rvu:+.2f}", foreground=diff_color).grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
        row += 1
        
        # Total Compensation
        ttk.Label(parent, text="Total Compensation:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"${total_comp1:,.2f}").grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"${total_comp2:,.2f}").grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
        diff_comp = total_comp1 - total_comp2
        diff_color = "green" if diff_comp > 0 else "red" if diff_comp < 0 else "black"
        ttk.Label(parent, text=f"${diff_comp:+,.2f}", foreground=diff_color).grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
        row += 1
        
        # Total Studies
        ttk.Label(parent, text="Total Studies:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"{total_studies1}").grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"{total_studies2}").grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
        diff_studies = total_studies1 - total_studies2
        diff_color = "green" if diff_studies > 0 else "red" if diff_studies < 0 else "black"
        ttk.Label(parent, text=f"{diff_studies:+d}", foreground=diff_color).grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
        row += 1
        
        # Separator
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=4, sticky=tk.EW, pady=10)
        row += 1
        
        # Studies by Modality header
        ttk.Label(parent, text="Studies by Modality:", font=("Arial", 10, "bold")).grid(row=row, column=0, padx=10, pady=5, sticky=tk.W, columnspan=4)
        row += 1
        
        # Get all unique modalities
        all_modalities = sorted(set(list(modality_counts1.keys()) + list(modality_counts2.keys())))
        
        for modality in all_modalities:
            count1 = modality_counts1.get(modality, 0)
            count2 = modality_counts2.get(modality, 0)
            diff = count1 - count2
            
            ttk.Label(parent, text=f"  {modality}:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
            ttk.Label(parent, text=f"{count1}").grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
            ttk.Label(parent, text=f"{count2}").grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
            diff_color = "green" if diff > 0 else "red" if diff < 0 else "black"
            ttk.Label(parent, text=f"{diff:+d}", foreground=diff_color).grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
            row += 1
    
    def backup_study_data(self):
        """Create a backup JSON export of the SQLite database with timestamp."""
        try:
            # Use the SQLite export method to create a JSON backup
            backup_path = self.data_manager.export_records_to_json()
            backup_filename = os.path.basename(backup_path)
            
            messagebox.showinfo("Backup Created", f"Study data backed up successfully!\n\nBackup file: {backup_filename}")
            logger.info(f"Backup created: {backup_path}")
        except Exception as e:
            error_msg = f"Error creating backup: {str(e)}"
            messagebox.showerror("Backup Failed", error_msg)
            logger.error(error_msg)
    
    def load_backup_data(self):
        """Show dialog to select and load a backup file."""
        try:
            records_file = self.data_manager.records_file
            backup_dir = os.path.dirname(records_file)
            
            # Find all backup files
            backup_files = []
            if os.path.exists(backup_dir):
                for filename in os.listdir(backup_dir):
                    if filename.startswith("rvu_records_backup_") and filename.endswith(".json"):
                        backup_path = os.path.join(backup_dir, filename)
                        try:
                            # Try to extract timestamp from filename
                            # Format: rvu_records_backup_YYYY-MM-DD_HH-MM-SS.json
                            timestamp_str = filename.replace("rvu_records_backup_", "").replace(".json", "")
                            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                            # Get file modification time for sorting
                            mtime = os.path.getmtime(backup_path)
                            backup_files.append({
                                "filename": filename,
                                "path": backup_path,
                                "timestamp": timestamp,
                                "mtime": mtime,
                                "display": timestamp.strftime("%B %d, %Y at %I:%M %p")
                            })
                        except:
                            # If we can't parse timestamp, use file mtime
                            mtime = os.path.getmtime(backup_path)
                            backup_files.append({
                                "filename": filename,
                                "path": backup_path,
                                "timestamp": datetime.fromtimestamp(mtime),
                                "mtime": mtime,
                                "display": datetime.fromtimestamp(mtime).strftime("%B %d, %Y at %I:%M %p")
                            })
            
            if not backup_files:
                messagebox.showinfo("No Backups", "No backup files found.")
                return
            
            # Sort by modification time (newest first)
            backup_files.sort(key=lambda x: x["mtime"], reverse=True)
            
            # Create selection dialog
            self._show_backup_selection_dialog(backup_files)
            
        except Exception as e:
            error_msg = f"Error loading backup list: {str(e)}"
            messagebox.showerror("Error", error_msg)
            logger.error(error_msg)
    
    def _show_backup_selection_dialog(self, backup_files: List[dict]):
        """Show a dialog with list of backups to select from."""
        # Create dialog window
        dialog = tk.Toplevel(self.window)
        dialog.title("Local")
        dialog.transient(self.window)
        dialog.grab_set()
        dialog.geometry("500x400")
        
        # Apply theme to dialog
        colors = self.app.get_theme_colors()
        dialog.configure(bg=colors["bg"])
        
        # Configure ttk styles for this dialog
        try:
            style = self.app.style
        except:
            style = ttk.Style()
        
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
        style.configure("TButton", background=colors["button_bg"], foreground=colors["button_fg"], 
                       bordercolor=colors.get("border_color", "#cccccc"))
        style.map("TButton", 
                 background=[("active", colors["button_active_bg"]), ("pressed", colors["button_active_bg"])],
                 foreground=[("active", colors["fg"]), ("pressed", colors["fg"])])
        style.configure("TScrollbar", background=colors["button_bg"], troughcolor=colors["bg"], 
                       bordercolor=colors.get("border_color", "#cccccc"))
        
        # Center dialog on parent window
        dialog.update_idletasks()
        x = self.window.winfo_x() + (self.window.winfo_width() // 2) - (dialog.winfo_width() // 2)
        y = self.window.winfo_y() + (self.window.winfo_height() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
        # Main container frame
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Label
        label = ttk.Label(main_frame, text="Select a backup to restore:", font=("Arial", 10))
        label.pack(anchor=tk.W, pady=(0, 5))
        
        # Frame for scrollable backup list
        list_container = ttk.Frame(main_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        # Create canvas with scrollbar for scrollable list
        canvas_frame = ttk.Frame(list_container)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        # Apply theme colors to canvas (use colors already fetched above)
        canvas = tk.Canvas(canvas_frame, highlightthickness=0, bg=colors["bg"])
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Update canvas window width when canvas is resized
        def update_canvas_window_width(event):
            canvas.itemconfig(canvas_window, width=event.width)
        
        canvas.bind('<Configure>', update_canvas_window_width)
        
        # Update scroll region when scrollable frame changes
        def update_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        scrollable_frame.bind('<Configure>', update_scroll_region)
        
        # Mouse wheel scrolling (bind to canvas and scrollable_frame)
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Store references for refresh
        dialog.backup_files = backup_files
        dialog.scrollable_frame = scrollable_frame
        dialog.canvas = canvas
        dialog.selected_backup = None
        
        def refresh_backup_list():
            """Refresh the backup list display."""
            # Clear existing widgets
            for widget in scrollable_frame.winfo_children():
                widget.destroy()
            
            # Re-fetch backup files
            records_file = self.data_manager.records_file
            backup_dir = os.path.dirname(records_file)
            backup_files = []
            if os.path.exists(backup_dir):
                for filename in os.listdir(backup_dir):
                    if filename.startswith("rvu_records_backup_") and filename.endswith(".json"):
                        backup_path = os.path.join(backup_dir, filename)
                        try:
                            timestamp_str = filename.replace("rvu_records_backup_", "").replace(".json", "")
                            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                            mtime = os.path.getmtime(backup_path)
                            backup_files.append({
                                "filename": filename,
                                "path": backup_path,
                                "timestamp": timestamp,
                                "mtime": mtime,
                                "display": timestamp.strftime("%B %d, %Y at %I:%M %p")
                            })
                        except:
                            mtime = os.path.getmtime(backup_path)
                            backup_files.append({
                                "filename": filename,
                                "path": backup_path,
                                "timestamp": datetime.fromtimestamp(mtime),
                                "mtime": mtime,
                                "display": datetime.fromtimestamp(mtime).strftime("%B %d, %Y at %I:%M %p")
                            })
            
            # Sort by timestamp (newest first)
            backup_files.sort(key=lambda x: x["mtime"], reverse=True)
            dialog.backup_files = backup_files
            
            # Get theme colors
            colors = self.app.get_theme_colors()
            
            # Populate scrollable frame with backup entries
            for i, backup in enumerate(backup_files):
                backup_frame = ttk.Frame(scrollable_frame)
                backup_frame.pack(fill=tk.X, pady=1, padx=2)
                
                # X button to delete
                delete_btn = tk.Label(
                    backup_frame,
                    text="×",
                    font=("Arial", 8),
                    bg=colors["delete_btn_bg"],
                    fg=colors["delete_btn_fg"],
                    cursor="hand2",
                    padx=2,
                    pady=2,
                    width=2,
                    anchor=tk.CENTER
                )
                delete_btn.backup_path = backup["path"]
                delete_btn.backup_display = backup["display"]
                delete_btn.bind("<Button-1>", lambda e, btn=delete_btn: delete_backup(btn))
                delete_btn.bind("<Enter>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_hover"]))
                delete_btn.bind("<Leave>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_bg"]))
                delete_btn.pack(side=tk.LEFT, padx=(0, 5))
                
                # Backup label (clickable)
                backup_label = ttk.Label(
                    backup_frame,
                    text=backup["display"],
                    font=("Consolas", 9),
                    cursor="hand2"
                )
                backup_label.backup = backup
                backup_label.bind("<Button-1>", lambda e, lbl=backup_label: select_backup(lbl))
                backup_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
                
                # Highlight selected backup
                if dialog.selected_backup and dialog.selected_backup["path"] == backup["path"]:
                    backup_label.config(background=colors.get("button_bg", "#e1e1e1"))
            
            # Update canvas scroll region
            canvas.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
            
            # If no backups left, close dialog
            if not backup_files:
                messagebox.showinfo("No Backups", "No backup files found.")
                dialog.destroy()
        
        def select_backup(label):
            """Select a backup file."""
            # Clear previous selection
            for widget in scrollable_frame.winfo_children():
                if isinstance(widget, ttk.Frame):
                    for child in widget.winfo_children():
                        if isinstance(child, ttk.Label) and hasattr(child, 'backup'):
                            child.config(background="")
            
            # Highlight selected
            label.config(background=self.app.get_theme_colors().get("button_bg", "#e1e1e1"))
            dialog.selected_backup = label.backup
        
        def delete_backup(btn):
            """Delete a backup file."""
            backup_path = btn.backup_path
            backup_display = btn.backup_display
            
            # Confirm deletion
            response = messagebox.askyesno(
                "Delete Backup?",
                f"Are you sure you want to delete this backup?\n\n"
                f"Backup: {backup_display}\n\n"
                f"This action cannot be undone.",
                parent=dialog
            )
            
            if response:
                try:
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                        logger.info(f"Backup deleted: {backup_path}")
                        
                        # Clear selection if deleted backup was selected
                        if dialog.selected_backup and dialog.selected_backup["path"] == backup_path:
                            dialog.selected_backup = None
                        
                        # Refresh the list
                        refresh_backup_list()
                    else:
                        messagebox.showwarning("File Not Found", f"Backup file not found:\n{backup_path}")
                        refresh_backup_list()
                except Exception as e:
                    error_msg = f"Error deleting backup: {str(e)}"
                    messagebox.showerror("Delete Failed", error_msg)
                    logger.error(error_msg)
        
        def on_load():
            if not dialog.selected_backup:
                messagebox.showwarning("No Selection", "Please select a backup file.")
                return
            
            selected_backup = dialog.selected_backup
            
            # Confirm overwrite
            response = messagebox.askyesno(
                "Confirm Overwrite",
                f"Are you sure you want to restore this backup?\n\n"
                f"Backup: {selected_backup['display']}\n\n"
                f"This will REPLACE your current study data. This action cannot be undone.\n\n"
                f"Consider creating a backup of your current data first.",
                icon="warning",
                parent=dialog
            )
            
            if response:
                try:
                    # Use the SQLite import method which properly syncs data
                    success = self.data_manager.import_records_from_json(selected_backup["path"])
                    
                    if success:
                        # Refresh the app
                        self.app.update_display()
                        
                        # Refresh statistics window
                        self.populate_shifts_list()
                        self.refresh_data()
                        
                        messagebox.showinfo("Backup Restored", f"Backup restored successfully!\n\nRestored from: {selected_backup['display']}")
                        logger.info(f"Backup restored: {selected_backup['path']}")
                        
                        dialog.destroy()
                    else:
                        messagebox.showerror("Restore Failed", "Failed to import backup data")
                except Exception as e:
                    error_msg = f"Error restoring backup: {str(e)}"
                    messagebox.showerror("Restore Failed", error_msg)
                    logger.error(error_msg)
        
        def on_cancel():
            dialog.destroy()
        
        # Initial population
        refresh_backup_list()
        
        # Buttons frame
        buttons_frame = ttk.Frame(dialog, padding="10")
        buttons_frame.pack(fill=tk.X)
        
        ttk.Button(buttons_frame, text="Restore", command=on_load, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons_frame, text="Cancel", command=on_cancel, width=12).pack(side=tk.RIGHT, padx=5)
    
    def on_configure(self, event):
        """Handle window configuration changes (move/resize)."""
        if event.widget == self.window:
            x = self.window.winfo_x()
            y = self.window.winfo_y()
            
            # Only save if position actually changed
            if x != self.last_saved_x or y != self.last_saved_y:
                # Debounce position saving (shorter for responsiveness)
                if hasattr(self, '_save_timer'):
                    try:
                        self.window.after_cancel(self._save_timer)
                    except:
                        pass
                self._save_timer = self.window.after(100, lambda: self.save_position(x, y))
    
    def on_statistics_drag_end(self, event):
        """Handle end of statistics window dragging - save position immediately."""
        # Cancel any pending debounced save
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        # Save immediately on mouse release
        self.save_position()
    
    def save_position(self, x=None, y=None):
        """Save statistics window position."""
        try:
            if x is None:
                x = self.window.winfo_x()
            if y is None:
                y = self.window.winfo_y()
            
            if "window_positions" not in self.data_manager.data:
                self.data_manager.data["window_positions"] = {}
            self.data_manager.data["window_positions"]["statistics"] = {
                "x": x,
                "y": y
            }
            self.last_saved_x = x
            self.last_saved_y = y
            # Only save settings (window positions), not records
            self.data_manager.save(save_records=False)
        except Exception as e:
            logger.error(f"Error saving statistics window position: {e}")
    
    def apply_theme(self):
        """Apply theme to statistics window."""
        dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
        
        # Get or create style (use app's style if available, otherwise create new one)
        try:
            style = self.app.style
        except:
            style = ttk.Style()
        
        # Use 'clam' theme for consistent styling
        style.theme_use('clam')
        
        if dark_mode:
            bg_color = "#1e1e1e"
            canvas_bg = "#252525"
            fg_color = "#e0e0e0"
            entry_bg = "#2d2d2d"
            entry_fg = "#e0e0e0"
            border_color = "#888888"
        else:
            bg_color = "SystemButtonFace"
            canvas_bg = "SystemButtonFace"
            fg_color = "black"
            entry_bg = "white"
            entry_fg = "black"
            border_color = "#cccccc"
        
        self.window.configure(bg=bg_color)
        self.theme_bg = bg_color
        self.theme_canvas_bg = canvas_bg
        
        # Configure ttk styles for Entry and Spinbox widgets
        style.configure("TEntry", fieldbackground=entry_bg, foreground=entry_fg, bordercolor=border_color)
        style.configure("TSpinbox", fieldbackground=entry_bg, foreground=entry_fg, bordercolor=border_color, 
                       background=entry_bg, arrowcolor=fg_color)
        style.configure("TLabel", background=bg_color, foreground=fg_color)
        style.configure("TFrame", background=bg_color)
        style.configure("TLabelframe", background=bg_color, bordercolor=border_color)
        style.configure("TLabelframe.Label", background=bg_color, foreground=fg_color)
    
    def detect_partial_shifts(self, typical_start_hour: int = None) -> List[List[dict]]:
        """
        Detect 'interrupted' shifts - shifts that started around typical start time, lasted <9 hours,
        and have consecutive shorter shifts that make up the remaining time.
        Returns a list of shift groups that could be combined.
        """
        shifts = self.get_all_shifts()
        # Filter out current shift and sort by start time (oldest first)
        historical = [s for s in shifts if not s.get("is_current") and s.get("shift_start")]
        
        # Use provided typical start hour or default to 23 (11pm)
        if typical_start_hour is None:
            typical_start_hour = 23
        
        def parse_shift(s):
            try:
                start = datetime.fromisoformat(s.get("shift_start", ""))
                end = datetime.fromisoformat(s.get("shift_end", "")) if s.get("shift_end") else start
                return start, end
            except:
                return None, None
        
        # Sort by start time
        historical.sort(key=lambda s: s.get("shift_start", ""))
        
        partial_groups = []
        used_indices = set()
        
        for i, shift in enumerate(historical):
            if i in used_indices:
                continue
                
            start, end = parse_shift(shift)
            if not start:
                continue
            
            # Check if shift started around typical start time (±0.5 hour)
            start_hour = start.hour + start.minute / 60
            typical_low = typical_start_hour - 0.5
            typical_high = typical_start_hour + 0.5
            # Handle wraparound at midnight
            if typical_start_hour >= 23:
                is_typical_start = start_hour >= typical_low or start_hour <= (typical_high % 24)
            else:
                is_typical_start = typical_low <= start_hour <= typical_high
            
            # Calculate duration
            duration_hours = (end - start).total_seconds() / 3600
            
            # If started around typical time and lasted <9 hours, look for continuation shifts
            if is_typical_start and duration_hours < 9:
                group = [shift]
                used_indices.add(i)
                total_duration = duration_hours
                last_end = end
                
                # Look for consecutive shifts within 4 hours of previous ending
                for j in range(i + 1, len(historical)):
                    if j in used_indices:
                        continue
                    
                    next_start, next_end = parse_shift(historical[j])
                    if not next_start:
                        continue
                    
                    # Check if this shift starts within 4 hours of the last one ending
                    gap_hours = (next_start - last_end).total_seconds() / 3600
                    if 0 <= gap_hours <= 4:
                        next_duration = (next_end - next_start).total_seconds() / 3600
                        group.append(historical[j])
                        used_indices.add(j)
                        total_duration += next_duration
                        last_end = next_end
                        
                        # Stop if we've accumulated enough for a full shift
                        if total_duration >= 9:
                            break
                    elif gap_hours > 4:
                        break  # Too big a gap
                
                # If we found multiple shifts that together form a reasonable duration
                if len(group) > 1 and total_duration >= 4:  # At least 4 hours combined
                    partial_groups.append(group)
        
        return partial_groups
    
    def show_partial_shifts_dialog(self):
        """Show dialog to combine detected partial shifts."""
        partial_groups = self.detect_partial_shifts()
        
        if not partial_groups:
            messagebox.showinfo("No Partial Shifts", 
                              "No interrupted/partial shift patterns detected.",
                              parent=self.window)
            return
        
        dialog = tk.Toplevel(self.window)
        dialog.title("Combine Partial Shifts")
        dialog.transient(self.window)
        dialog.grab_set()
        
        # Position near button
        dialog.geometry(f"+{self.window.winfo_x() + 50}+{self.window.winfo_y() + 100}")
        
        ttk.Label(dialog, text="Detected shift groups that may have been interrupted:",
                 font=("Arial", 10, "bold")).pack(padx=15, pady=(15, 10))
        
        # Scrollable frame for groups
        canvas = tk.Canvas(dialog, height=300, width=450)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(15, 0), pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 15), pady=5)
        
        selected_groups = []
        
        for group_idx, group in enumerate(partial_groups):
            group_frame = ttk.LabelFrame(scroll_frame, text=f"Group {group_idx + 1}", padding=5)
            group_frame.pack(fill=tk.X, pady=5, padx=5)
            
            # Calculate combined stats
            total_records = sum(len(s.get("records", [])) for s in group)
            total_rvu = sum(sum(r.get("rvu", 0) for r in s.get("records", [])) for s in group)
            
            first_start = datetime.fromisoformat(group[0].get("shift_start", ""))
            last_end = datetime.fromisoformat(group[-1].get("shift_end", ""))
            total_hours = (last_end - first_start).total_seconds() / 3600
            
            info_text = f"{len(group)} shifts • {total_records} studies • {total_rvu:.1f} RVU • {total_hours:.1f}h total span"
            ttk.Label(group_frame, text=info_text).pack(anchor=tk.W)
            
            # List each shift in the group
            for shift in group:
                try:
                    start = datetime.fromisoformat(shift.get("shift_start", ""))
                    end = datetime.fromisoformat(shift.get("shift_end", ""))
                    dur = (end - start).total_seconds() / 3600
                    shift_info = f"  • {start.strftime('%m/%d %I:%M%p')} - {end.strftime('%I:%M%p')} ({dur:.1f}h, {len(shift.get('records', []))} studies)"
                except:
                    shift_info = "  • Unknown"
                ttk.Label(group_frame, text=shift_info, font=("Arial", 9)).pack(anchor=tk.W)
            
            # Checkbox to select this group
            var = tk.BooleanVar(value=True)
            selected_groups.append((group, var))
            ttk.Checkbutton(group_frame, text="Combine this group", variable=var).pack(anchor=tk.W, pady=(5, 0))
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=15, padx=15)
        
        def do_combine():
            groups_to_combine = [g for g, v in selected_groups if v.get()]
            if groups_to_combine:
                for group in groups_to_combine:
                    self._combine_shift_group(group)
                dialog.destroy()
                self.populate_shifts_list()
                self.refresh_data()
                self.update_partial_shifts_button()
                messagebox.showinfo("Shifts Combined", 
                                  f"Successfully combined {len(groups_to_combine)} shift group(s).",
                                  parent=self.window)
        
        ttk.Button(btn_frame, text="Combine Selected", command=do_combine).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
    
    def show_combine_shifts_dialog(self):
        """Show dialog to manually combine shifts."""
        shifts = self.get_all_shifts()
        historical = [s for s in shifts if not s.get("is_current") and s.get("shift_start")]
        
        if len(historical) < 2:
            messagebox.showinfo("Not Enough Shifts",
                              "You need at least 2 shifts to combine.",
                              parent=self.window)
            return
        
        dialog = tk.Toplevel(self.window)
        dialog.title("Combine Shifts")
        dialog.transient(self.window)
        dialog.grab_set()
        
        # Position near button
        dialog.geometry(f"+{self.window.winfo_x() + 50}+{self.window.winfo_y() + 100}")
        
        ttk.Label(dialog, text="Select shifts to combine (select 2 or more):",
                 font=("Arial", 10, "bold")).pack(padx=15, pady=(15, 10))
        
        # Scrollable frame
        canvas_frame = ttk.Frame(dialog)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        
        canvas = tk.Canvas(canvas_frame, height=350, width=400)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Track how many shifts loaded and selection vars
        shifts_shown = [0]
        max_initial = 20
        selection_vars = []
        
        def add_shift_row(shift, idx):
            frame = ttk.Frame(scroll_frame)
            frame.pack(fill=tk.X, pady=2)
            
            var = tk.BooleanVar()
            selection_vars.append((shift, var))
            
            try:
                start = datetime.fromisoformat(shift.get("shift_start", ""))
                end = datetime.fromisoformat(shift.get("shift_end", ""))
                dur = (end - start).total_seconds() / 3600
                records = shift.get("records", [])
                rvu = sum(r.get("rvu", 0) for r in records)
                text = f"{start.strftime('%m/%d/%Y %I:%M%p')} ({dur:.1f}h, {len(records)} studies, {rvu:.1f} RVU)"
            except:
                text = f"Shift {idx + 1}"
            
            ttk.Checkbutton(frame, text=text, variable=var).pack(anchor=tk.W)
        
        def load_shifts(count):
            current = shifts_shown[0]
            for i in range(current, min(current + count, len(historical))):
                add_shift_row(historical[i], i)
            shifts_shown[0] = min(current + count, len(historical))
            
            # Update load more button visibility
            if shifts_shown[0] < len(historical):
                load_more_btn.pack(pady=5)
            else:
                load_more_btn.pack_forget()
        
        # Load more button
        load_more_btn = ttk.Button(scroll_frame, text="Load More...", 
                                   command=lambda: load_shifts(20))
        
        # Initial load
        load_shifts(max_initial)
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=15, padx=15)
        
        def do_combine():
            selected = [s for s, v in selection_vars if v.get()]
            if len(selected) < 2:
                messagebox.showwarning("Selection Required",
                                      "Please select at least 2 shifts to combine.",
                                      parent=dialog)
                return
            
            # Sort by start time
            selected.sort(key=lambda s: s.get("shift_start", ""))
            
            # Confirm
            first_start = datetime.fromisoformat(selected[0].get("shift_start", ""))
            last_end = datetime.fromisoformat(selected[-1].get("shift_end", ""))
            total_records = sum(len(s.get("records", [])) for s in selected)
            total_rvu = sum(sum(r.get("rvu", 0) for r in s.get("records", [])) for s in selected)
            
            result = messagebox.askyesno(
                "Confirm Combine",
                f"Combine {len(selected)} shifts?\n\n"
                f"Start: {first_start.strftime('%m/%d/%Y %I:%M %p')}\n"
                f"End: {last_end.strftime('%m/%d/%Y %I:%M %p')}\n"
                f"Total: {total_records} studies, {total_rvu:.1f} RVU\n\n"
                "This will merge all studies into a single shift.",
                parent=dialog
            )
            
            if result:
                self._combine_shift_group(selected)
                dialog.destroy()
                self.populate_shifts_list()
                self.refresh_data()
                self.update_partial_shifts_button()
                messagebox.showinfo("Shifts Combined",
                                  f"Successfully combined {len(selected)} shifts.",
                                  parent=self.window)
        
        ttk.Button(btn_frame, text="Combine Selected", command=do_combine).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
    
    def _combine_shift_group(self, shifts: List[dict]):
        """Combine multiple shifts into one. Takes earliest start, latest end, merges all records."""
        if len(shifts) < 2:
            return
        
        # Sort by start time
        shifts.sort(key=lambda s: s.get("shift_start", ""))
        
        # Combine data
        combined_start = shifts[0].get("shift_start")
        combined_end = shifts[-1].get("shift_end")
        combined_records = []
        
        for shift in shifts:
            combined_records.extend(shift.get("records", []))
        
        # Sort records by time_performed
        combined_records.sort(key=lambda r: r.get("time_performed", ""))
        
        # Delete old shifts from database
        for shift in shifts:
            shift_start = shift.get("shift_start")
            try:
                cursor = self.data_manager.db.conn.cursor()
                cursor.execute('SELECT id FROM shifts WHERE shift_start = ? AND is_current = 0', (shift_start,))
                row = cursor.fetchone()
                if row:
                    self.data_manager.db.delete_shift(row[0])
            except Exception as e:
                logger.error(f"Error deleting shift from database during combine: {e}")
            
            # Remove from in-memory data
            historical_shifts = self.data_manager.data.get("shifts", [])
            for i, s in enumerate(historical_shifts):
                if s.get("shift_start") == shift_start:
                    historical_shifts.pop(i)
                    break
            
            if "shifts" in self.data_manager.records_data:
                for i, s in enumerate(self.data_manager.records_data["shifts"]):
                    if s.get("shift_start") == shift_start:
                        self.data_manager.records_data["shifts"].pop(i)
                        break
        
        # Create the combined shift in database
        try:
            cursor = self.data_manager.db.conn.cursor()
            cursor.execute('''
                INSERT INTO shifts (shift_start, shift_end, is_current)
                VALUES (?, ?, 0)
            ''', (combined_start, combined_end))
            self.data_manager.db.conn.commit()
            combined_shift_id = cursor.lastrowid
            
            # Add all records to the combined shift
            for record in combined_records:
                self.data_manager.db.add_record(combined_shift_id, record)
            
            logger.info(f"Created combined shift in database: ID={combined_shift_id}")
        except Exception as e:
            logger.error(f"Error saving combined shift to database: {e}")
            return  # Don't add to memory if database save failed
        
        # Reload data from database to ensure in-memory data matches database
        # This prevents duplicates that could occur if we manually add to in-memory data
        # and then reload from database later
        try:
            self.data_manager.records_data = self.data_manager._load_records_from_db()
            # Update the main data structure as well
            self.data_manager.data["shifts"] = self.data_manager.records_data.get("shifts", [])
            logger.info(f"Reloaded data from database after combining shifts")
        except Exception as e:
            logger.error(f"Error reloading data from database: {e}")
        
        logger.info(f"Combined {len(shifts)} shifts into one ({len(combined_records)} records)")
    
    def update_partial_shifts_button(self):
        """Update visibility of the partial shifts button based on detection."""
        partial_groups = self.detect_partial_shifts()
        if partial_groups:
            self.partial_shifts_btn.pack(side=tk.LEFT, padx=2)
        else:
            self.partial_shifts_btn.pack_forget()
    
    def on_closing(self):
        """Handle window closing."""
        # Cancel any pending save timer
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        self.save_position()
        self.window.destroy()


def main():
    """Main entry point."""
    root = tk.Tk()
    app = RVUCounterApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
