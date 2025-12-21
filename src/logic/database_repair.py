"""Database repair logic - identifies and fixes mismatches in stored records."""

import logging
from typing import List, Dict, Tuple, Optional
from .study_matcher import match_study_type

logger = logging.getLogger(__name__)

class DatabaseRepair:
    """Fixes discrepancies in the SQLite database based on current RVU rules."""
    
    def __init__(self, data_manager):
        self.data_manager = data_manager
        self.db = data_manager.db
        self.rvu_table = data_manager.data.get("rvu_table", {})
        self.classification_rules = data_manager.data.get("classification_rules", {})
        self.direct_lookups = data_manager.data.get("direct_lookups", {})
        
    def find_mismatches(self, progress_callback=None) -> List[dict]:
        """Scan all records in the database and find ones that don't match current rules."""
        mismatches = []
        try:
            # Get all records from database
            cursor = self.db.conn.cursor()
            cursor.execute('SELECT id, procedure, study_type, rvu FROM records')
            records = cursor.fetchall()
            
            total = len(records)
            for i, rec in enumerate(records):
                if progress_callback:
                    progress_callback(i + 1, total)
                    
                rec_id, proc_name, stored_type, stored_rvu = rec
                
                new_type, new_rvu = match_study_type(
                    proc_name,
                    self.rvu_table,
                    self.classification_rules,
                    self.direct_lookups
                )
                
                # Check for mismatch (with small epsilon for float comparison)
                if new_type != stored_type or abs(float(stored_rvu) - new_rvu) > 0.01:
                    mismatches.append({
                        "id": rec_id,
                        "procedure": proc_name,
                        "old_type": stored_type,
                        "old_rvu": stored_rvu,
                        "new_type": new_type,
                        "new_rvu": new_rvu
                    })
                    
            return mismatches
            
        except Exception as e:
            logger.error(f"Error finding mismatches: {e}")
            return []

    def fix_mismatches(self, mismatches: List[dict], progress_callback=None) -> int:
        """Update records in the database to match current rules.
        
        Returns:
            Number of records updated
        """
        count = 0
        try:
            cursor = self.db.conn.cursor()
            total = len(mismatches)
            
            for i, m in enumerate(mismatches):
                if progress_callback:
                    progress_callback(i + 1, total)
                    
                cursor.execute('''
                    UPDATE records 
                    SET study_type = ?, rvu = ? 
                    WHERE id = ?
                ''', (m["new_type"], m["new_rvu"], m["id"]))
                count += 1
                
            self.db.conn.commit()
            logger.info(f"Database repair complete: updated {count} records")
            
            # Reload memory cache in data manager
            if self.data_manager:
                self.data_manager.records_data = self.data_manager._load_records_from_db()
                self.data_manager.data["records"] = self.data_manager.records_data.get("records", [])
                self.data_manager.data["shifts"] = self.data_manager.records_data.get("shifts", [])
                
            return count
            
        except Exception as e:
            logger.error(f"Error fixing mismatches: {e}")
            self.db.conn.rollback()
            return 0

__all__ = ['DatabaseRepair']
