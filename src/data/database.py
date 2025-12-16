"""SQLite database layer for RVU Counter - handles all database operations."""

import sqlite3
import json
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

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


__all__ = ['RecordsDatabase']
