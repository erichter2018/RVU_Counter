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
import re

# Try to import tkcalendar for date picker
try:
    from tkcalendar import DateEntry, Calendar
    HAS_TKCALENDAR = True
except ImportError as e:
    HAS_TKCALENDAR = False
    print(f"Warning: tkcalendar not available: {e}")


# Configure logging
log_file = os.path.join(os.path.dirname(__file__), "rvu_counter.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# Version information
VERSION = "1.2.1"
VERSION_DATE = "2025-12-05"


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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (shift_id) REFERENCES shifts(id) ON DELETE CASCADE
            )
        ''')
        
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


# RVU Lookup Table
RVU_TABLE = {
    "MRI Brain": 2.3,
    "MRI Other": 1.75,
    "CT CAP": 3.06,
    "CT AP": 1.68,
    "CT Other": 1.0,
    "NM Myocardial stress": 1.62,
    "NM Other": 1.0,
    "PET CT": 3.6,
    "Ultrasound transvaginal complete": 1.38,
    "US Arterial Lower Extremity": 1.2,
    "US Other": 0.68,
    "XR": 0.3,
    "CT Brain": 0.9,
    "CTA Brain": 1.75,
    "CTA Neck": 1.75,
    "CT Neck": 1.5,
    "CTA Runoff with Abdo/Pelvis": 2.75,
    "Bone Survey": 1.0,
    "CTA Brain and Neck": 3.5,
    "CT Brain and Cervical": 1.9,
    "CT TL Spine": 2.0,
    "CT Face": 1.0,
    "XR Abdomen": 0.3,
    "XR MSK": 0.3,
}


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
                # Check for MosaicInfoHub variations
                if ("mosaicinfohub" in title_lower or 
                    "mosaic info hub" in title_lower or 
                    "mosaic infohub" in title_lower):
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
                # "MosaicInfoHub", "Mosaic Info Hub", "Mosaic InfoHub"
                is_mosaic = ("mosaicinfohub" in window_text or 
                            ("mosaic" in window_text and "info" in window_text and "hub" in window_text))
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


def extract_mosaic_data(webview_element):
    """Extract study data from Mosaic Info Hub WebView2 content.
    
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
    """Match procedure text to RVU table entry using best match."""
    if not procedure_text:
        return "Unknown", 0.0
    
    # Use provided tables or defaults
    if rvu_table is None:
        rvu_table = RVU_TABLE
    if classification_rules is None:
        classification_rules = {}
    if direct_lookups is None:
        direct_lookups = {}
    
    procedure_lower = procedure_text.lower().strip()
    procedure_stripped = procedure_text.strip()
    
    # Check both direct lookup and classification rules
    direct_match_rvu = None
    direct_match_name = None
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
    
    # If classification rule matched, return it immediately (highest priority)
    if classification_match_name:
        logger.debug(f"Matched classification rule: {procedure_text} -> {classification_match_name} ({classification_match_rvu} RVU)")
        return classification_match_name, classification_match_rvu
    
    # SECOND: Check direct/exact lookups (exact procedure name matches)
    if direct_lookups:
        # Try exact match (case-insensitive)
        for lookup_procedure, rvu_value in direct_lookups.items():
            if lookup_procedure.lower().strip() == procedure_lower:
                direct_match_rvu = rvu_value
                direct_match_name = lookup_procedure
                logger.debug(f"Matched direct lookup: {procedure_text} -> {rvu_value} RVU")
                break
    
    # If direct lookup matched, return it
    if direct_match_rvu is not None:
        return direct_match_name, direct_match_rvu
    
    # Check for modality keywords and use "Other" types as fallback before partial matching
    modality_fallbacks = {
        "ct": ("CT Other", rvu_table.get("CT Other", 1.0)),
        "mri": ("MRI Other", rvu_table.get("MRI Other", 1.75)),
        "mr ": ("MRI Other", rvu_table.get("MRI Other", 1.75)),
        "us ": ("US Other", rvu_table.get("US Other", 0.68)),
        "ultrasound": ("US Other", rvu_table.get("US Other", 0.68)),
        "xr ": ("XR Other", rvu_table.get("XR Other", 0.3)),
        "x-ray": ("XR Other", rvu_table.get("XR Other", 0.3)),
        "nm ": ("NM Other", rvu_table.get("NM Other", 1.0)),
        "nuclear": ("NM Other", rvu_table.get("NM Other", 1.0)),
    }
    
    # Check for modality keywords (case-insensitive)
    for keyword, (study_type, rvu) in modality_fallbacks.items():
        if keyword in procedure_lower:
            # Only use as fallback if the study_type exists in rvu_table
            if study_type in rvu_table:
                logger.info(f"Using modality fallback '{study_type}' for procedure containing '{keyword}': {procedure_text}")
            return study_type, rvu
    
    # Try exact match first
    for study_type, rvu in rvu_table.items():
        if study_type.lower() == procedure_lower:
            return study_type, rvu
    
    # Try keyword matching FIRST (before partial matching) to correctly identify modality
    keywords = {
        "ct cap": ("CT CAP", 3.06),
        "ct ap": ("CT AP", 1.68),
        "cta": ("CTA Brain", 1.75),  # Default CTA
        "pet": ("PET CT", 3.6),
        "mri": ("MRI Other", 1.75),
        "mr ": ("MRI Other", 1.75),
        "ultrasound": ("US Other", 0.68),
        "us ": ("US Other", 0.68),
        "x-ray": ("XR Other", 0.3),
        "xr ": ("XR Other", 0.3),
        "xr\t": ("XR Other", 0.3),  # XR with tab
        "nuclear": ("NM Other", 1.0),
        "nm ": ("NM Other", 1.0),
    }
    
    # Check for keywords - prioritize longer/more specific keywords first
    for keyword in sorted(keywords.keys(), key=len, reverse=True):
        if keyword in procedure_lower:
            study_type, rvu = keywords[keyword]
            logger.info(f"Matched keyword '{keyword}' to '{study_type}' for: {procedure_text}")
            return study_type, rvu
    
    # Also check if procedure starts with modality prefix (case-insensitive)
    if len(procedure_lower) >= 2:
        first_two = procedure_lower[:2]
        prefix_keywords = {
            "xr": ("XR Other", 0.3),
            "x-": ("XR Other", 0.3),
            "ct": ("CT Other", 1.0),
            "mr": ("MRI Other", 1.75),
            "us": ("US Other", 0.68),
            "nm": ("NM Other", 1.0),
            "pe": ("PET CT", 3.6),  # PET
        }
        if first_two in prefix_keywords:
            study_type, rvu = prefix_keywords[first_two]
            logger.info(f"Matched prefix '{first_two}' to '{study_type}' for: {procedure_text}")
            return study_type, rvu
    
    # Try partial matches (most specific first), but exclude "Other" types initially
    matches = []
    other_matches = []
    for study_type, rvu in rvu_table.items():
        study_lower = study_type.lower()
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
            "records": self.records_data.get("records", []),
            "current_shift": self.records_data.get("current_shift", {
                "shift_start": None,
                "shift_end": None,
                "records": []
            }),
            "shifts": self.records_data.get("shifts", [])
        }
    
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
        """Load settings, RVU table, classification rules, and window positions."""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    data = json.load(f)
                    logger.info(f"Loaded settings from {self.settings_file}")
                    return data
        except Exception as e:
            logger.error(f"Error loading settings: {e}")
        
        # If settings file doesn't exist, try to copy from bundled version (for frozen apps)
        if self.is_frozen:
            try:
                bundled_settings_file = os.path.join(sys._MEIPASS, "rvu_settings.json")
                if os.path.exists(bundled_settings_file):
                    logger.info(f"Copying bundled settings from {bundled_settings_file} to {self.settings_file}")
                    shutil.copy2(bundled_settings_file, self.settings_file)
                    # Now load the copied file
                    with open(self.settings_file, 'r') as f:
                        data = json.load(f)
                        logger.info(f"Loaded settings from bundled file (copied to {self.settings_file})")
                        return data
            except Exception as e:
                logger.error(f"Error copying bundled settings file: {e}")
        
        # Default settings structure (fallback if bundled file also doesn't exist)
        return {
            "settings": {
                "auto_start": False,
                "show_total": True,
                "show_avg": True,
                "show_last_hour": True,
                "show_last_full_hour": True,
                "show_projected": True,
                "show_projected_shift": True,
                "show_comp_projected_shift": True,
                "show_pace_car": False,  # Show pace comparison bar vs prior shift
                "shift_length_hours": 9,
                "min_study_seconds": 5,
                "ignore_duplicate_accessions": True,
                "data_source": "PowerScribe",  # "PowerScribe" or "Mosaic"
                "show_time": False,  # Show time information in recent studies
                "stay_on_top": True,  # Keep window on top of other windows
            },
            "direct_lookups": {},
            "rvu_table": RVU_TABLE.copy(),
            "classification_rules": {},
            "window_positions": {
                "main": {"x": 50, "y": 50},
                "settings": {"x": 100, "y": 100},
                "statistics": {"x": 150, "y": 150}
            }
        }
    
    def _validate_window_positions(self, data: dict) -> dict:
        """Validate window positions and reset invalid ones to safe defaults.
        
        Returns data dict with validated/reset window positions.
        Handles multi-monitor setups by using virtual screen dimensions.
        """
        try:
            # Try to get virtual screen dimensions (includes all monitors)
            temp_root = None
            try:
                temp_root = tk.Tk()
                temp_root.withdraw()  # Hide the window
                # Use virtual screen dimensions for multi-monitor support
                # Virtual screen includes all monitors, so coordinates can be negative or beyond primary monitor
                virtual_width = temp_root.winfo_vrootwidth()
                virtual_height = temp_root.winfo_vrootheight()
                # Get virtual screen origin (usually negative if monitors extend left/up)
                virtual_x = temp_root.winfo_vrootx()
                virtual_y = temp_root.winfo_vrooty()
            except:
                # Fallback: use reasonable defaults if tkinter not available yet
                virtual_width = 3840  # Assume dual 1920x1080 monitors
                virtual_height = 1080
                virtual_x = 0
                virtual_y = 0
            finally:
                if temp_root:
                    temp_root.destroy()
            
            # Default safe positions (low x, y with small offsets) - on primary monitor
            default_positions = {
                "main": {"x": 50, "y": 50, "width": 240, "height": 500},
                "settings": {"x": 100, "y": 100},
                "statistics": {"x": 150, "y": 150}
            }
            
            # Window size constraints (minimum visible area)
            window_sizes = {
                "main": {"width": 240, "height": 500},
                "settings": {"width": 450, "height": 580},
                "statistics": {"width": 1350, "height": 800}
            }
            
            if "window_positions" not in data:
                data["window_positions"] = default_positions.copy()
                return data
            
            positions = data["window_positions"]
            positions_updated = False
            
            # Calculate virtual screen bounds
            virtual_left = virtual_x
            virtual_right = virtual_x + virtual_width
            virtual_top = virtual_y
            virtual_bottom = virtual_y + virtual_height
            
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
                min_width = window_size["width"]
                min_height = window_size["height"]
                
                # Validate position against virtual screen (all monitors)
                # Window must be at least partially within the virtual screen bounds
                # Allow windows that extend slightly beyond (up to 50% can be off-screen)
                window_right = x + min_width
                window_bottom = y + min_height
                
                # Check if window is completely off-screen
                if window_right < virtual_left or x > virtual_right or window_bottom < virtual_top or y > virtual_bottom:
                    logger.warning(f"{window_type} window completely off virtual screen (x={x}, y={y}), resetting to default")
                    positions[window_type] = default_positions[window_type].copy()
                    positions_updated = True
                    continue
                
                # Check if window is mostly off-screen (less than 50% visible)
                # Calculate visible portion
                visible_left = max(x, virtual_left)
                visible_right = min(window_right, virtual_right)
                visible_top = max(y, virtual_top)
                visible_bottom = min(window_bottom, virtual_bottom)
                
                visible_width = max(0, visible_right - visible_left)
                visible_height = max(0, visible_bottom - visible_top)
                
                if visible_width < min_width * 0.5 or visible_height < min_height * 0.5:
                    logger.warning(f"{window_type} window mostly off-screen (only {visible_width}x{visible_height} visible), resetting to default")
                    positions[window_type] = default_positions[window_type].copy()
                    positions_updated = True
                    continue
            
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
                    settings_data = {
                        "settings": old_data.get("settings", {}),
                        "rvu_table": old_data.get("rvu_table", RVU_TABLE.copy()),
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
        
        if save_records:
            if "records" in self.data:
                self.records_data["records"] = self.data["records"]
            if "current_shift" in self.data:
                self.records_data["current_shift"] = self.data["current_shift"]
            if "shifts" in self.data:
                self.records_data["shifts"] = self.data["shifts"]
        
        # Save settings file (everything - settings, RVU tables, rules, window positions)
        try:
            settings_to_save = {
                "settings": self.settings_data.get("settings", {}),
                "direct_lookups": self.settings_data.get("direct_lookups", {}),
                "rvu_table": self.settings_data.get("rvu_table", {}),
                "classification_rules": self.settings_data.get("classification_rules", {}),
                "compensation_rates": self.settings_data.get("compensation_rates", {}),
                "window_positions": self.settings_data.get("window_positions", {})
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
        """Check if study should be ignored (only if already completed, not if currently active).
        
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
        
        # Check in-memory cache first (faster)
        if accession in self.seen_accessions:
            logger.debug(f"Ignoring duplicate completed accession (in memory): {accession}")
            return True
        
        # Check database for duplicates in current shift
        if data_manager:
            try:
                # Check if this exact accession exists in the current shift's database records
                if hasattr(data_manager, 'db') and data_manager.db:
                    current_shift = data_manager.db.get_current_shift()
                    if current_shift:
                        db_record = data_manager.db.find_record_by_accession(
                            current_shift['id'], accession
                        )
                        if db_record:
                            logger.info(f"Ignoring duplicate accession (found in database): {accession}")
                            # Add to memory cache so we don't need to query DB again
                            self.seen_accessions.add(accession)
                            return True
            except Exception as e:
                logger.debug(f"Error checking database for duplicate: {e}")
            
            # Also check if this accession was part of a multi-accession study that was already recorded
            if self._was_part_of_multi_accession(accession, data_manager):
                logger.debug(f"Ignoring accession {accession} - it was already recorded as part of a multi-accession study")
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
            self.root.geometry(f"{width}x{height}+{window_pos['x']}+{window_pos['y']}")
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
        
        # Version info on the right
        version_text = f"v{VERSION} ({VERSION_DATE})"
        self.version_label = ttk.Label(top_section, text=version_text, font=("Arial", 7), foreground="gray")
        self.version_label.grid(row=1, column=1, sticky=tk.E, padx=(0, 2), pady=(0, 0))
        
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
        
        # Pace car comparison state: 'prior', 'best_week', 'best_ever', or shift_index
        self.pace_comparison_mode = 'prior'
        self.pace_comparison_shift = None  # Cache of the shift data being compared
        
        # Container for both bars (stacked) - clickable to change comparison
        self.pace_bars_container = tk.Frame(self.pace_car_frame, bg="#e0e0e0", height=20)
        self.pace_bars_container.pack(fill=tk.X, padx=2, pady=1)
        self.pace_bars_container.pack_propagate(False)
        
        # Bind click to open comparison selector (no cursor change)
        self.pace_bars_container.bind("<Button-1>", self._open_pace_comparison_selector)
        
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
        
        # Show pace car if enabled in settings
        if self.data_manager.data["settings"].get("show_pace_car", False):
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
        since 11pm (reference shift start time).
        
        Design: Two stacked bars
        - Top bar (current): fills proportionally based on current RVU vs prior total
        - Bottom bar (prior): full width = prior total, with marker at "prior at this time"
        """
        try:
            if not hasattr(self, 'pace_bars_container'):
                return
            
            current_time = datetime.now()
            
            # Calculate reference 11pm for current shift
            # If it's before 11pm, use yesterday's 11pm
            today_11pm = current_time.replace(hour=23, minute=0, second=0, microsecond=0)
            if current_time.hour < 23:
                # We're past midnight, so 11pm was yesterday
                reference_11pm = today_11pm - timedelta(days=1)
            else:
                reference_11pm = today_11pm
            
            # Elapsed time since 11pm in minutes
            elapsed_minutes = (current_time - reference_11pm).total_seconds() / 60
            if elapsed_minutes < 0:
                elapsed_minutes = 0
            
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
            if diff >= 0:
                current_bar_color = "#5DADE2"  # Slightly darker light blue for bar (ahead)
                current_text_color = "#2874A6"  # Darker blue for text
                status_text = f"▲ +{diff:.1f} ahead"
                status_color = "#2874A6"  # Darker blue for status text
            else:
                current_bar_color = "#c62828"  # Red for bar (behind)
                current_text_color = "#B71C1C"  # Slightly darker red for text
                status_text = f"▼ {diff:.1f} behind"
                status_color = "#B71C1C"  # Darker red for status text
            
            # Update current bar (top) - fills from left
            self.pace_bar_current.config(bg=current_bar_color)
            self.pace_bar_current.place(x=0, y=0, width=current_width, height=9)
            
            # Update prior bar (bottom) - width scales with prior total relative to max
            self.pace_bar_prior_track.place(x=0, y=11, width=prior_total_width, height=9)
            
            # Update prior marker (black line on lavender bar showing "prior at this time")
            self.pace_bar_prior_marker.place(x=prior_marker_pos, y=11, width=2, height=9)
            
            # Format current time as H:MM am/pm
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
            if self.pace_comparison_mode == 'best_week':
                compare_label = "Week:"  # Week's best
            elif self.pace_comparison_mode == 'best_ever':
                compare_label = "Best:"  # All time best
            elif self.pace_comparison_mode.startswith('week_'):
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
        try:
            # Create popup window
            popup = tk.Toplevel(self.root)
            popup.title("Compare To...")
            popup.transient(self.root)
            popup.grab_set()
            
            # Position near the pace bar
            x = self.pace_bars_container.winfo_rootx()
            y = self.pace_bars_container.winfo_rooty() + self.pace_bars_container.winfo_height()
            popup.geometry(f"+{x}+{y}")
            
            # Apply theme
            dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
            bg_color = "#2d2d2d" if dark_mode else "white"
            fg_color = "#ffffff" if dark_mode else "black"
            popup.configure(bg=bg_color)
            
            frame = tk.Frame(popup, bg=bg_color, padx=10, pady=5)
            frame.pack(fill=tk.BOTH, expand=True)
            
            # Get shifts for this week and all time
            shifts_this_week, prior_shift, best_week, best_ever = self._get_pace_comparison_options()
            
            # Variable for selection
            selected = tk.StringVar(value=self.pace_comparison_mode)
            
            def make_selection(mode, shift=None):
                self.pace_comparison_mode = mode
                self.pace_comparison_shift = shift
                popup.destroy()
            
            # Prior Shift option (default)
            if prior_shift:
                prior_label = self._format_shift_label(prior_shift)
                btn = tk.Radiobutton(frame, text=f"Prior Shift ({prior_label})", 
                                    variable=selected, value='prior',
                                    bg=bg_color, fg=fg_color, selectcolor=bg_color,
                                    activebackground=bg_color, activeforeground=fg_color,
                                    command=lambda: make_selection('prior', prior_shift))
                btn.pack(anchor=tk.W, pady=1)
            
            # This week's shifts header
            if shifts_this_week:
                tk.Label(frame, text="This Week:", font=("Arial", 8, "bold"),
                        bg=bg_color, fg=fg_color).pack(anchor=tk.W, pady=(5, 2))
                
                for i, shift in enumerate(shifts_this_week):
                    day_label = self._format_shift_day_label(shift)
                    total_rvu = sum(r.get('rvu', 0) for r in shift.get('records', []))
                    btn = tk.Radiobutton(frame, text=f"  {day_label} ({total_rvu:.1f} RVU)", 
                                        variable=selected, value=f'week_{i}',
                                        bg=bg_color, fg=fg_color, selectcolor=bg_color,
                                        activebackground=bg_color, activeforeground=fg_color,
                                        command=lambda s=shift, idx=i: make_selection(f'week_{idx}', s))
                    btn.pack(anchor=tk.W, pady=1)
            
            # Best this week
            if best_week and best_week != prior_shift:
                best_week_rvu = sum(r.get('rvu', 0) for r in best_week.get('records', []))
                best_week_label = self._format_shift_day_label(best_week)
                btn = tk.Radiobutton(frame, text=f"Best This Week: {best_week_label} ({best_week_rvu:.1f} RVU)", 
                                    variable=selected, value='best_week',
                                    bg=bg_color, fg=fg_color, selectcolor=bg_color,
                                    activebackground=bg_color, activeforeground=fg_color,
                                    command=lambda: make_selection('best_week', best_week))
                btn.pack(anchor=tk.W, pady=(5, 1))
            
            # Best ever
            if best_ever:
                best_ever_rvu = sum(r.get('rvu', 0) for r in best_ever.get('records', []))
                best_ever_label = self._format_shift_label(best_ever)
                btn = tk.Radiobutton(frame, text=f"Best Ever: {best_ever_label} ({best_ever_rvu:.1f} RVU)", 
                                    variable=selected, value='best_ever',
                                    bg=bg_color, fg=fg_color, selectcolor=bg_color,
                                    activebackground=bg_color, activeforeground=fg_color,
                                    command=lambda: make_selection('best_ever', best_ever))
                btn.pack(anchor=tk.W, pady=1)
            
            # Close on click outside
            popup.bind("<FocusOut>", lambda e: popup.destroy())
            
        except Exception as e:
            logger.debug(f"Error opening pace comparison selector: {e}")
    
    def _get_pace_comparison_options(self):
        """Get shifts available for pace comparison.
        
        Returns: (shifts_this_week, prior_shift, best_week_shift, best_ever_shift)
        """
        historical_shifts = self.data_manager.data.get("shifts", [])
        if not historical_shifts:
            return [], None, None, None
        
        now = datetime.now()
        
        # Find start of current week (Monday at 11pm for night shifts)
        days_since_monday = now.weekday()  # Monday = 0
        week_start = now - timedelta(days=days_since_monday)
        week_start = week_start.replace(hour=23, minute=0, second=0, microsecond=0)
        # If we haven't reached Monday 11pm yet, use last week's Monday 11pm
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
                
                # Prior shift is the first one (most recent)
                if prior_shift is None:
                    prior_shift = shift
                
                # Check if in this week
                if shift_start >= week_start:
                    shifts_this_week.append(shift)
                    if total_rvu > best_week_rvu:
                        best_week_rvu = total_rvu
                        best_week = shift
                
                # Best ever
                if total_rvu > best_ever_rvu:
                    best_ever_rvu = total_rvu
                    best_ever = shift
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
    
    def _get_prior_shift_rvu_at_elapsed_time(self, elapsed_minutes: float):
        """Get RVU from comparison shift at the same elapsed time since 11pm.
        
        Returns tuple (rvu_at_elapsed, total_rvu) or None if no shift data available.
        Uses self.pace_comparison_mode to determine which shift to compare against.
        """
        try:
            # Determine which shift to use for comparison
            comparison_shift = None
            
            if self.pace_comparison_shift and self.pace_comparison_mode != 'prior':
                # Use cached comparison shift
                comparison_shift = self.pace_comparison_shift
            else:
                # Default: use prior shift (most recent)
                historical_shifts = self.data_manager.data.get("shifts", [])
                if not historical_shifts:
                    return None
                
                for shift in historical_shifts:
                    if shift.get("shift_start") and shift.get("records"):
                        comparison_shift = shift
                        break
            
            if not comparison_shift:
                return None
            
            # Get comparison shift's reference 11pm
            prior_start = datetime.fromisoformat(comparison_shift["shift_start"])
            
            # Calculate prior shift's 11pm reference
            # If shift started before 11pm, use that day's 11pm
            # If shift started after 11pm, use that 11pm
            prior_11pm = prior_start.replace(hour=23, minute=0, second=0, microsecond=0)
            if prior_start.hour < 23:
                # Started after midnight, so reference is previous day's 11pm
                prior_11pm = prior_11pm - timedelta(days=1)
            
            # Calculate what time in the prior shift corresponds to our elapsed time
            target_time = prior_11pm + timedelta(minutes=elapsed_minutes)
            
            # Sum RVU for all records finished before target_time
            rvu_at_elapsed = 0.0
            total_rvu = 0.0
            for record in comparison_shift.get("records", []):
                try:
                    rvu = record.get("rvu", 0)
                    total_rvu += rvu  # Always add to total
                    time_finished = datetime.fromisoformat(record.get("time_finished", ""))
                    if time_finished <= target_time:
                        rvu_at_elapsed += rvu
                except:
                    total_rvu += record.get("rvu", 0)  # Still count toward total even if time parse fails
            
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
        
        # Extract pure accession numbers for group ID and recording
        # Handle both old format (key is accession number) and new format (key has "accession_number" field)
        accession_numbers = []
        for entry in all_entries:
            data = self.multi_accession_data[entry]
            if data.get("accession_number"):
                # New format: explicit accession_number field
                accession_numbers.append(data["accession_number"])
            elif '(' in entry and ')' in entry:
                # Entry is "ACC (PROC)" format - parse it
                acc_match = re.match(r'^([^(]+)', entry)
                if acc_match:
                    accession_numbers.append(acc_match.group(1).strip())
                else:
                    accession_numbers.append(entry.strip())
            else:
                # Entry is already just the accession number
                accession_numbers.append(entry.strip())
        
        # Generate a unique group ID to link these studies for duplicate detection
        multi_accession_group_id = "_".join(sorted(accession_numbers))
        
        total_rvu = 0
        recorded_count = 0
        
        # Record each study individually
        for i, entry in enumerate(all_entries):
            data = self.multi_accession_data[entry]
            
            # Get the pure accession number
            accession = accession_numbers[i] if i < len(accession_numbers) else entry
            
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
                "multi_accession_count": num_studies,
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
        """Extract data from Mosaic. Returns data dict with 'found', 'accession', etc."""
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
                
                # Find WebView2 control
                webview = find_mosaic_webview_element(main_window)
                
                if webview:
                    # Extract data from Mosaic
                    mosaic_data = extract_mosaic_data(webview)
                    
                    data['procedure'] = mosaic_data.get('procedure', '')
                    
                    # Handle multiple accessions - convert to format expected by refresh_data
                    multiple_accessions_data = mosaic_data.get('multiple_accessions', [])
                    if multiple_accessions_data:
                        # Store accession/procedure pairs
                        for acc_data in multiple_accessions_data:
                            acc = acc_data.get('accession', '')
                            proc = acc_data.get('procedure', '')
                            if proc:
                                data['multiple_accessions'].append(f"{acc} ({proc})")
                            else:
                                data['multiple_accessions'].append(acc)
                        
                        # Set first as primary
                        if multiple_accessions_data:
                            data['accession'] = multiple_accessions_data[0].get('accession', '')
                            if not data['procedure'] and multiple_accessions_data[0].get('procedure'):
                                data['procedure'] = multiple_accessions_data[0].get('procedure', '')
                    else:
                        # Single accession
                        data['accession'] = mosaic_data.get('accession', '')
                        if not data['procedure']:
                            data['procedure'] = mosaic_data.get('procedure', '')
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
                    if data.get('accession'):
                        self._active_source = "PowerScribe"
                        self._primary_source = "PowerScribe"
                elif mosaic_available and not ps_available:
                    data = self._extract_mosaic_data()
                    if data.get('accession'):
                        self._active_source = "Mosaic"
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
            
            if data_source == "Mosaic" and multiple_accessions:
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
                    # Check if ALL accessions are already seen (order doesn't matter - using set membership)
                    all_seen = ignore_duplicates and all(acc in self.tracker.seen_accessions for acc in accession_numbers)
                    
                    if all_seen:
                        # All accessions already completed - don't track again, but still display normally
                        # Don't show "(done)" - just display it as a normal multi-accession study
                        logger.info(f"Duplicate multi-accession study detected (all {len(accession_numbers)} accessions already seen): {accession_numbers}")
                        # Continue to enter multi-accession mode for display, but duplicate detection will prevent re-recording
                        # Fall through to start multi-accession mode below
                    
                    # Starting multi-accession mode (whether duplicate or not - display it normally)
                    self.multi_accession_mode = True
                    self.multi_accession_start_time = datetime.now()
                    self.multi_accession_data = {}
                    self.multi_accession_last_procedure = ""  # Reset so first procedure gets collected
                    
                    # Clear element cache to ensure fresh listbox data on next poll
                    # This is important for single-to-multi transition where the UI changes
                    self.cached_elements = {}
                    
                    # Check if any of the new accessions were being tracked as single
                    # If so, migrate their data to multi-accession tracking
                    # Must extract accession numbers since multiple_accessions may be "ACC (PROC)" format
                    for acc_entry in multiple_accessions:
                        # Extract just the accession number from "ACC (PROC)" format
                        if '(' in acc_entry and ')' in acc_entry:
                            acc_match = re.match(r'^([^(]+)', acc_entry)
                            if acc_match:
                                acc_num = acc_match.group(1).strip()
                            else:
                                acc_num = acc_entry.strip()
                        else:
                            acc_num = acc_entry.strip()
                        
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
                        
                        # Find which accession this procedure belongs to
                        # Strategy: 
                        # 1. Try to match by procedure name in the listbox entry (format: "ACC (PROC)")
                        # 2. Fall back to first accession without data
                        matched_acc = None
                        
                        # First, try to match by procedure text in the listbox entry
                        for acc_entry in multiple_accessions:
                            if acc_entry not in self.multi_accession_data:
                                # Check if procedure is embedded in the entry (format: "ACC (PROC)")
                                if '(' in acc_entry and ')' in acc_entry:
                                    entry_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', acc_entry)
                                    if entry_match:
                                        embedded_proc = entry_match.group(2).strip()
                                        # Check if embedded procedure matches current procedure (case-insensitive partial match)
                                        if (embedded_proc.upper() in procedure.upper() or 
                                            procedure.upper() in embedded_proc.upper()):
                                            matched_acc = acc_entry
                                            break
                        
                        # Fall back: assign to first accession without data
                        if not matched_acc:
                            for acc_entry in multiple_accessions:
                                if acc_entry not in self.multi_accession_data:
                                    matched_acc = acc_entry
                                    break
                        
                        if matched_acc:
                            # Extract pure accession number for recording
                            if '(' in matched_acc and ')' in matched_acc:
                                acc_match = re.match(r'^([^(]+)', matched_acc)
                                acc_num = acc_match.group(1).strip() if acc_match else matched_acc.strip()
                            else:
                                acc_num = matched_acc.strip()
                            
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
            # For Mosaic multi-accession, ensure procedure is always set
            if data_source == "Mosaic" and multiple_accessions and not procedure:
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
                        logger.info(f"Adding study before N/A check: {accession} - {study_procedure}")
                        self.tracker.add_study(accession, study_procedure, current_time, 
                                             rvu_table, classification_rules, direct_lookups, 
                                             pending_patient_class)
                        self.tracker.mark_seen(accession)
                        # Remove from pending after adding
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
            logger.info(f"Completion check: data_source={data_source}, accession='{accession}', multiple_accessions={multiple_accessions}, active_studies={list(self.tracker.active_studies.keys())}")
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
                    logger.info(f"Mosaic: no accession and no multiple_accessions - completing all active studies")
                    completed = []
                    for acc, study in list(self.tracker.active_studies.items()):
                        # No accessions visible - complete immediately
                        # Use current_time as end_time since study just disappeared
                        duration = (current_time - study["start_time"]).total_seconds()
                        logger.info(f"Mosaic completion check: {acc}, patient_class={study.get('patient_class')}, duration={duration:.1f}s, min_seconds={self.tracker.min_seconds}")
                        if duration >= self.tracker.min_seconds:
                            completed_study = study.copy()
                            completed_study["end_time"] = current_time
                            completed_study["duration"] = duration
                            completed.append(completed_study)
                            logger.info(f"Completed Mosaic study (no accessions visible): {acc} - {study['study_type']} ({duration:.1f}s)")
                            # Remove from active studies
                            del self.tracker.active_studies[acc]
                        else:
                            logger.info(f"Skipping short Mosaic study: {acc} ({duration:.1f}s < {self.tracker.min_seconds}s)")
                else:
                    # Single Mosaic accession - use normal completion check
                    logger.info(f"Calling check_completed for single Mosaic: accession='{accession}', data_source={data_source}")
                    completed = self.tracker.check_completed(current_time, accession)
            else:
                # Normal completion check (PowerScribe or single Mosaic accession)
                logger.info(f"Calling check_completed for PowerScribe/single: accession='{accession}', data_source={data_source}")
                completed = self.tracker.check_completed(current_time, accession)
            
            logger.info(f"Processing {len(completed)} completed studies from check_completed")
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
            # Mark as seen to track that we've encountered this accession
            if accession not in self.tracker.seen_accessions:
                self.tracker.mark_seen(accession)
            
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
            # Format: "Started: HH:MM AM/PM"
            time_str = self.shift_start.strftime("%I:%M %p")
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
        """Update the debug display with current PowerScribe data."""
        # Check if procedure is "n/a" - if so, don't display anything
        is_na = self.current_procedure and self.current_procedure.strip().lower() in ["n/a", "na", "none", ""]
        
        show_time = self.data_manager.data["settings"].get("show_time", False)
        
        if is_na or not self.current_procedure:
            self.debug_accession_label.config(text="")
            self.debug_duration_label.config(text="")
            self.debug_procedure_label.config(text="", foreground="gray")
            self.debug_patient_class_label.config(text="")
            self.debug_study_type_prefix_label.config(text="")
            self.debug_study_type_label.config(text="")
            self.debug_study_rvu_label.config(text="")
        else:
            # Handle multi-accession display
            if self.current_multiple_accessions:
                # Multi-accession - either active or duplicate
                # Parse accession display - handle both formats:
                # PowerScribe: ["ACC1", "ACC2"]
                # Mosaic: ["ACC1 (PROC1)", "ACC2 (PROC2)"]
                acc_display_list = []
                for acc_entry in self.current_multiple_accessions[:2]:
                    if '(' in acc_entry and ')' in acc_entry:
                        # Mosaic format - extract just the accession
                        acc_match = re.match(r'^([^(]+)', acc_entry)
                        if acc_match:
                            acc_display_list.append(acc_match.group(1).strip())
                        else:
                            acc_display_list.append(acc_entry)
                    else:
                        # PowerScribe format - use as-is
                        acc_display_list.append(acc_entry)
                
                acc_display = ", ".join(acc_display_list)
                if len(self.current_multiple_accessions) > 2:
                    acc_display += f" (+{len(self.current_multiple_accessions) - 2})"
                
                # Calculate duration for current study
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
                
                self.debug_accession_label.config(text=f"Accession: {acc_display}")
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
                accession_text = self.current_accession if self.current_accession else '-'
                
                # Calculate duration for current study
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
                
                self.debug_accession_label.config(text=f"Accession: {accession_text}")
                self.debug_duration_label.config(text=duration_text if show_time else "")
                
                # No truncation for procedure - show full name
                procedure_display = self.current_procedure if self.current_procedure else '-'
                self.debug_procedure_label.config(text=f"Procedure: {procedure_display}", foreground="gray")
            
            self.debug_patient_class_label.config(text=f"Patient Class: {self.current_patient_class if self.current_patient_class else '-'}")
            
            # Display study type with RVU on the right (separate labels for alignment)
            if self.current_study_type:
                # Aggressive truncation for Current Study to ensure RVU label always stays visible
                # Truncate very aggressively so "..." is right next to RVU with minimal space
                study_type_display = self._truncate_text(self.current_study_type, 10)  # Very aggressive to fit RVU
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
            "dark_mode": dark_mode
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
        
        # Update pace car labels (tk.Label)
        pace_labels = [
            getattr(self, 'pace_label_now_text', None),
            getattr(self, 'pace_label_separator', None),
            getattr(self, 'pace_label_time', None),
            getattr(self, 'pace_label_right', None),
        ]
        for label in pace_labels:
            if label:
                label.configure(bg=bg_color)
        
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
            self.window.geometry(f"450x590+{window_pos['x']}+{window_pos['y']}")
        else:
            self.window.geometry("450x590")
        
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
            
            # Update tracker min_seconds
            self.app.tracker.min_seconds = self.data_manager.data["settings"]["min_study_seconds"]
            
            # Update stay on top setting
            self.app.root.attributes("-topmost", self.data_manager.data["settings"]["stay_on_top"])
            
            # Update pace car visibility
            if self.show_pace_car_var.get():
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
            
            # Draw text
            self.header_canvas.create_text(x + width//2, self.header_height//2,
                                         text=display_text, font=('Arial', 9, 'bold'),
                                         anchor='center', fill=header_fg, tags=f"header_{col_name}")
            
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
                        anchor = 'center'
                        self.data_canvas.create_text(x + width//2, y + self.row_height//2,
                                                   text=value_str, font=font, anchor=anchor, fill=text_color)
                else:
                    # Normal rendering - entire text in one color
                    anchor = 'center'
                    self.data_canvas.create_text(x + width//2, y + self.row_height//2,
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
            self.window.geometry(f"1350x800+{pos['x']}+{pos['y']}")
        else:
            # Center on screen
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
        
        # Shifts List Section (with delete capability)
        shifts_frame = ttk.LabelFrame(left_panel, text="All Shifts", padding="8")
        shifts_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Scrollable list of shifts
        canvas_bg = getattr(self, 'theme_canvas_bg', 'SystemButtonFace')
        self.shifts_canvas = tk.Canvas(shifts_frame, width=180, highlightthickness=0, bg=canvas_bg)
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
        ttk.Radiobutton(view_frame, text="All Studies", variable=self.view_mode,
                       value="all_studies", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="Summary", variable=self.view_mode,
                       value="summary", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        
        # Period label with checkboxes for efficiency view
        period_frame = ttk.Frame(right_panel)
        period_frame.pack(fill=tk.X, pady=(0, 10))
        self.period_label = ttk.Label(period_frame, text="", font=("Arial", 12, "bold"))
        self.period_label.pack(side=tk.LEFT, anchor=tk.W)
        
        # Checkboxes for efficiency color coding (will be shown/hidden based on view mode)
        self.efficiency_checkboxes_frame = ttk.Frame(period_frame)
        self.efficiency_checkboxes_frame.pack(side=tk.RIGHT, anchor=tk.E)
        
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
            
            # Study count
            records = shift.get("records", [])
            count = len(records)
            total_rvu = sum(r.get("rvu", 0) for r in records)
            
            # Shift button (clickable)
            btn = ttk.Button(shift_frame, text=f"{label_text} ({count})", width=18,
                           command=lambda idx=i: self.select_shift(idx))
            btn.pack(side=tk.LEFT, padx=(0, 2))
            
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
            # Current work week: Monday 11pm to next Monday 8am
            start, end = self._get_work_week_range(now, "this")
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"This Work Week - {date_range}"
        
        elif period == "last_work_week":
            # Previous work week: Monday 11pm to next Monday 8am
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
        """Calculate work week range (Monday 11pm to next Monday 8am).
        
        Args:
            target_date: Reference date
            which_week: "this" for current work week, "last" for previous work week
            
        Returns:
            Tuple of (start_datetime, end_datetime) for the work week
        """
        # Work week: Monday 11pm to next Monday 8am
        # Find the Monday that started the current work week
        days_since_monday = target_date.weekday()  # Monday = 0
        
        # Determine which work week we're in
        if days_since_monday == 0 and target_date.hour < 8:
            # It's Monday before 8am - we're still in the previous work week
            # Find last Monday 11pm (which is 8 days ago Monday 11pm)
            work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=8)
        else:
            # After 8am Monday or later in week - find the Monday that started this week
            if days_since_monday == 0:
                # It's Monday after 8am - current work week started yesterday (last Monday 11pm)
                work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
            else:
                # It's Tuesday-Sunday - find the most recent Monday
                work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
        
        # Work week starts Monday 11pm (23:00)
        work_week_start = work_week_start_monday.replace(hour=23, minute=0, second=0, microsecond=0)
        # Work week ends next Monday 8am
        work_week_end = (work_week_start_monday + timedelta(days=7)).replace(hour=8, minute=0, second=0, microsecond=0)
        
        if which_week == "last":
            # Go back one week (7 days)
            work_week_start = work_week_start - timedelta(days=7)
            work_week_end = work_week_end - timedelta(days=7)
        
        return work_week_start, work_week_end
    
    def _get_records_in_range(self, start: datetime, end: datetime) -> List[dict]:
        """Get all records within a date range."""
        records = []
        
        # Check current shift
        current_shift = self.data_manager.data.get("current_shift", {})
        for record in current_shift.get("records", []):
            try:
                rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                if start <= rec_time <= end:
                    records.append(record)
            except:
                pass
        
        # Check historical shifts from the "shifts" array
        historical_shifts = self.data_manager.data.get("shifts", [])
        for shift in historical_shifts:
            for record in shift.get("records", []):
                try:
                    rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                    if start <= rec_time <= end:
                        records.append(record)
                except:
                    pass
        
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
                            rvu_table = self.data_manager.data.get("rvu_table", RVU_TABLE)
                            classification_rules = self.data_manager.data.get("classification_rules", {})
                            direct_lookups = self.data_manager.data.get("direct_lookups", {})
                            
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
        self.period_label.config(text=period_desc)
        
        # Expand multi-accession records into individual modality records for statistics
        records = self._expand_multi_accession_records(records)
        
        view_mode = self.view_mode.get()
        
        # Hide tree for all views (all use Canvas now)
        self.tree.pack_forget()
        self.tree_scrollbar_y.pack_forget()
        self.tree_scrollbar_x.pack_forget()
        
        # Hide all Canvas tables and frames (they will be recreated/shown by each view)
        canvas_tables = ['_summary_table', '_all_studies_table', '_by_modality_table', 
                        '_by_patient_class_table', '_by_study_type_table', '_by_hour_table',
                        '_compensation_table', '_projection_table']
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
        
        # Show/hide efficiency checkboxes based on view mode
        if view_mode == "efficiency":
            # Make sure radio buttons frame is visible and create radio buttons if needed
            if not self.heatmap_radio_buttons:
                # Helper function to save heatmap mode and refresh
                def save_heatmap_mode():
                    self.data_manager.data.setdefault("settings", {})["efficiency_heatmap_mode"] = self.heatmap_mode.get()
                    self.data_manager.save(save_records=False)
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
            self.efficiency_checkboxes_frame.pack(side=tk.RIGHT, anchor=tk.E)
        else:
            if self.efficiency_checkboxes_frame:
                self.efficiency_checkboxes_frame.pack_forget()
        
        if view_mode == "by_hour":
            self._display_by_hour(records)
        elif view_mode == "by_modality":
            self._display_by_modality(records)
        elif view_mode == "by_patient_class":
            self._display_by_patient_class(records)
        elif view_mode == "by_study_type":
            self._display_by_study_type(records)
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
                {'name': 'study_type', 'width': 150, 'text': 'Study Type', 'sortable': True},
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
            
            rvu = record.get("rvu", 0)
            
            if study_type not in type_data:
                type_data[study_type] = {"studies": 0, "rvu": 0}
            
            type_data[study_type]["studies"] += 1
            type_data[study_type]["rvu"] += rvu
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
        
        # Store records for virtual rendering
        self._all_studies_records = records
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
    
    def _sort_all_studies(self, col_name: str):
        """Sort all studies by column."""
        if not hasattr(self, '_all_studies_records'):
            return
        
        # Toggle sort direction if clicking same column
        if hasattr(self, '_all_studies_sort_column') and self._all_studies_sort_column == col_name:
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
        
        # Re-render the visible rows
        self._render_visible_rows()
    
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
            
            # Check current shift records
            current_records = self.data_manager.data.get("current_shift", {}).get("records", [])
            for i, r in enumerate(current_records):
                if r.get("accession") == accession and r.get("time_performed") == time_performed:
                    current_records.pop(i)
                    self.data_manager.save()
                    logger.info(f"Deleted study from current shift: {accession}")
                    self.refresh_data()
                    return
            
            # Check historical shifts
            for shift in self.data_manager.data.get("shifts", []):
                shift_records = shift.get("records", [])
                for i, r in enumerate(shift_records):
                    if r.get("accession") == accession and r.get("time_performed") == time_performed:
                        shift_records.pop(i)
                        self.data_manager.save()
                        logger.info(f"Deleted study from historical shift: {accession}")
                        self.refresh_data()
                        return
            
            logger.warning(f"Could not find record to delete: {accession}")
    
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
        
        # Clear existing widgets
        for widget in list(self.efficiency_frame.winfo_children()):
            try:
                widget.destroy()
            except:
                pass
        
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
        
        for record in records:
            study_type = record.get("study_type", "Unknown")
            modality = study_type.split()[0] if study_type else "Unknown"
            
            try:
                rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                hour = rec_time.hour
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
                                                         fill='#d0d0d0', outline=header_border, width=1,
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
                
                # Get radio button state
                heatmap_mode = self.heatmap_mode.get()
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
                    for idx, (avg_duration, cell_text) in enumerate(row_cell_data):
                        # Determine cell color based on active heatmaps
                        cell_color = data_bg  # Default to background
                        
                        # Apply duration colors if enabled (blue=fast, red=slow)
                        if show_duration and avg_duration is not None:
                            cell_color = get_heatmap_color(avg_duration, min_duration, max_duration, duration_range, reverse=False)
                        
                        # Apply study count colors if enabled (blue=high count, red=low count - reversed from duration)
                        if show_count and idx < len(row_count_data):
                            count = row_count_data[idx]
                            if count is not None and count > 0:
                                # Only count colors enabled (reversed: blue=high, red=low)
                                cell_color = get_heatmap_color(count, min_count, max_count, count_range, reverse=True)
                        
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
                    rows_canvas.create_text(x + modality_col_width//2, y + row_height//2,
                                           text="average", font=('Arial', 9, 'bold'), anchor='center',
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
                    
                    for idx, cell_text in enumerate(total_hour_cells):
                        # Determine cell color based on active heatmaps
                        cell_color = total_bg  # Default to background
                        
                        # Apply duration colors if enabled (blue=fast, red=slow)
                        if show_duration and idx < len(total_hour_durations):
                            avg_duration = total_hour_durations[idx]
                            if avg_duration is not None:
                                cell_color = get_heatmap_color(avg_duration, total_min_duration, total_max_duration, total_duration_range, reverse=False)
                        
                        # Apply study count colors if enabled (blue=high count, red=low count - reversed from duration)
                        if show_count and idx < len(total_hour_counts):
                            count = total_hour_counts[idx]
                            if count is not None and count > 0:
                                cell_color = get_heatmap_color(count, total_min_count, total_max_count, total_count_range, reverse=True)
                        
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
            
            # Build row data for all modalities
            for modality in all_modalities:
                modality_durations = []
                modality_counts_row = []
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
                    
                    # Build cell text
                    if avg_duration is not None:
                        duration_str = self._format_duration(avg_duration)
                        cell_text = f"{duration_str} ({duration_count})"
                    elif study_count > 0:
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
                    'count_data': modality_counts_row,
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
                total_hour_counts = []
                for hour in hours_list:
                    hour_durations = []
                    hour_count = 0
                    for mod in efficiency_data.keys():
                        if hour in efficiency_data[mod]:
                            hour_durations.extend(efficiency_data[mod][hour])
                        # Count all studies for this hour across all modalities
                        if mod in study_count_data and hour in study_count_data[mod]:
                            hour_count += study_count_data[mod][hour]
                    
                    if hour_durations:
                        avg_duration = sum(hour_durations) / len(hour_durations)
                        count = len(hour_durations)
                        duration_str = self._format_duration(avg_duration)
                        cell_text = f"{duration_str} ({count})"
                        total_hour_durations.append(avg_duration)
                    else:
                        cell_text = "-"
                        total_hour_durations.append(None)
                    
                    total_hour_counts.append(hour_count if hour_count > 0 else None)
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
                    'hour_counts': total_hour_counts,
                    'min_duration': total_min_duration,
                    'max_duration': total_max_duration,
                    'duration_range': total_duration_range,
                    'min_count': total_min_count,
                    'max_count': total_max_count,
                    'count_range': total_count_range
                }
            
            # Initial draw
            draw_rows()
            
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
                {'name': 'value', 'width': 200, 'text': 'Value', 'sortable': True}
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
            am_pm = "AM" if h < 12 else "PM"
            return f"{hour_12}{am_pm}"
        
        # Add summary rows to Canvas table
        self._summary_table.add_row({'metric': 'Total Studies', 'value': str(total_studies)})
        self._summary_table.add_row({'metric': 'Total RVU', 'value': f"{total_rvu:.1f}"})
        self._summary_table.add_row({'metric': 'Average RVU per Study', 'value': f"{avg_rvu:.2f}"})
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
                                   key=lambda x: x[1]['compensation'] * (x[1]['percentage'] if historical_studies > 0 else 0), 
                                   reverse=True)
        
        for st, stats in sorted_study_types[:10]:  # Top 10 study types
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
        dialog.title("Select Backup to Load")
        dialog.transient(self.window)
        dialog.grab_set()
        dialog.geometry("500x400")
        
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
        
        canvas = tk.Canvas(canvas_frame, highlightthickness=0)
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
        
        ttk.Button(buttons_frame, text="Load Selected Backup", command=on_load, width=20).pack(side=tk.LEFT, padx=5)
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
        
        if dark_mode:
            bg_color = "#1e1e1e"
            canvas_bg = "#252525"
        else:
            bg_color = "SystemButtonFace"
            canvas_bg = "SystemButtonFace"
        
        self.window.configure(bg=bg_color)
        self.theme_bg = bg_color
        self.theme_canvas_bg = canvas_bg
    
    def detect_partial_shifts(self) -> List[List[dict]]:
        """
        Detect 'interrupted' shifts - shifts that started around 11pm, lasted <9 hours,
        and have consecutive shorter shifts that make up the remaining time.
        Returns a list of shift groups that could be combined.
        """
        shifts = self.get_all_shifts()
        # Filter out current shift and sort by start time (oldest first)
        historical = [s for s in shifts if not s.get("is_current") and s.get("shift_start")]
        
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
            
            # Check if shift started around 11pm (10:30pm - 11:30pm)
            start_hour = start.hour + start.minute / 60
            is_evening_start = 22.5 <= start_hour <= 23.5 or (0 <= start_hour <= 0.5)  # 10:30pm-11:30pm or 00:00-00:30
            
            # Calculate duration
            duration_hours = (end - start).total_seconds() / 3600
            
            # If started around 11pm and lasted <9 hours, look for continuation shifts
            if is_evening_start and duration_hours < 9:
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
