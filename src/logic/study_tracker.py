"""Study tracking - monitors active studies and detects when they're completed."""

import logging
from datetime import datetime
from typing import Dict, List

from .study_matcher import match_study_type

logger = logging.getLogger(__name__)


class StudyTracker:
    """Tracks active studies and detects when they disappear (are completed)."""
    
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


__all__ = ['StudyTracker']
