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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import threading
import re


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
    "CT Thoracic and Lumbar Spine": 2.0,
}


def find_powerscribe_window():
    """Find PowerScribe 360 window by title."""
    desktop = Desktop(backend="uia")
    
    possible_titles = [
        "PowerScribe 360 | Reporting",
        "PowerScribe 360",
        "PowerScribe",
        "Reporting",
        "PowerScribe 360 - Reporting"
    ]
    
    for title in possible_titles:
        try:
            windows = desktop.windows(title_re=title, visible_only=True)
            for window in windows:
                try:
                    window_text = window.window_text()
                    if "RVU Counter" not in window_text:
                        return window
                except:
                    continue
        except:
            continue
    
    # Fallback search
    try:
        all_windows = desktop.windows(visible_only=True)
        for window in all_windows:
            try:
                window_text = window.window_text().lower()
                if ("powerscribe" in window_text or "reporting" in window_text) and "rvu counter" not in window_text:
                    return window
            except:
                continue
    except:
        pass
    
    return None


def find_elements_by_automation_id(window, automation_ids: List[str]) -> Dict[str, any]:
    """Find elements by Automation ID."""
    found_elements = {}
    remaining_ids = set(automation_ids)
    
    def search_by_id(element):
        nonlocal remaining_ids
        if not remaining_ids:
            return
        
        try:
            automation_id = element.element_info.automation_id
            if automation_id and automation_id in remaining_ids:
                try:
                    text_content = element.window_text()
                except:
                    text_content = ""

                found_elements[automation_id] = {
                    'element': element,
                    'text': text_content.strip() if text_content else '',
                }
                remaining_ids.remove(automation_id)
                if not remaining_ids:
                    return
        except:
            pass

        try:
            children = element.children()
            for child in children:
                if remaining_ids:
                    search_by_id(child)
        except:
            pass
    
    search_by_id(window)
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
    
    # FIRST: Check direct/exact lookups (exact procedure name matches)
    if direct_lookups:
        # Try exact match (case-insensitive)
        for lookup_procedure, rvu_value in direct_lookups.items():
            if lookup_procedure.lower().strip() == procedure_lower:
                direct_match_rvu = rvu_value
                direct_match_name = lookup_procedure
                logger.info(f"Matched direct lookup: {procedure_text} -> {rvu_value} RVU")
                break
    
    # SECOND: Check user-defined classification rules
    # Rules are grouped by study_type, each group contains a list of rule definitions
    for study_type, rules_list in classification_rules.items():
        if not isinstance(rules_list, list):
            continue
        
        for rule in rules_list:
            required_keywords = rule.get("required_keywords", [])
            excluded_keywords = rule.get("excluded_keywords", [])
            
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
            if required_keywords:
                all_present = all(keyword.lower() in procedure_lower for keyword in required_keywords)
                if all_present:
                    # Get RVU from rvu_table
                    rvu = rvu_table.get(study_type, 0.0)
                    classification_match_name = study_type
                    classification_match_rvu = rvu
                    logger.info(f"Matched classification rule for '{study_type}': {procedure_text} -> {study_type}")
                    break  # Found a classification match, stop searching rules for this study_type
        
        # If we found a classification match, stop searching other study_types
        if classification_match_name:
            break
    
    # If both direct lookup and classification rule match, prefer classification name but use direct lookup RVU
    if direct_match_rvu is not None and classification_match_name:
        logger.info(f"Both direct lookup and classification rule matched. Using '{classification_match_name}' name with {direct_match_rvu} RVU from direct lookup")
        return classification_match_name, direct_match_rvu
    
    # If only direct lookup matched
    if direct_match_rvu is not None:
        return direct_match_name, direct_match_rvu
    
    # If only classification rule matched
    if classification_match_name:
        return classification_match_name, classification_match_rvu
    
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
    
    # Try keyword matching
    keywords = {
        "mri": ("MRI Other", 1.75),
        "mr ": ("MRI Other", 1.75),
        "ct cap": ("CT CAP", 3.06),
        "ct ap": ("CT AP", 1.68),
        "cta": ("CTA Brain", 1.75),  # Default CTA
        "pet": ("PET CT", 3.6),
        "ultrasound": ("US Other", 0.68),
        "us ": ("US Other", 0.68),
        "x-ray": ("XR Other", 0.3),
        "xr ": ("XR Other", 0.3),
        "nuclear": ("NM Other", 1.0),
        "nm ": ("NM Other", 1.0),
    }
    
    for keyword, (study_type, rvu) in keywords.items():
        if keyword in procedure_lower:
            return study_type, rvu
    
    # Default fallback: Use first two letters to determine generic type
    if len(procedure_lower) >= 2:
        first_two = procedure_lower[:2]
        
        # Generic type defaults based on first two letters
        generic_defaults = {
            "ct": ("CT Other", 1.0),
            "mr": ("MRI Other", 1.75),
            "us": ("US Other", 0.68),
            "nm": ("NM Other", 1.0),
            "pe": ("PET CT", 3.6),  # PET
            "xr": ("XR Other", 0.3),
            "x-": ("XR Other", 0.3),
        }
        
        if first_two in generic_defaults:
            study_type, rvu = generic_defaults[first_two]
            logger.info(f"Using generic type '{study_type}' for procedure starting with '{first_two}': {procedure_text}")
            return study_type, rvu
    
    return "Unknown", 0.0


class RVUData:
    """Manages data persistence with separate files for settings and records."""
    
    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.dirname(__file__)
        self.settings_file = os.path.join(base_dir, "rvu_settings.json")
        self.records_file = os.path.join(base_dir, "rvu_records.json")
        self.old_data_file = os.path.join(base_dir, "rvu_data.json")  # For migration
        
        # Load data from both files
        self.settings_data = self.load_settings()
        self.records_data = self.load_records()
        
        # Migrate old file if it exists
        self.migrate_old_file()
        
        # Merge into single data structure for compatibility
        self.data = {
            "settings": self.settings_data.get("settings", {}),
            "direct_lookups": self.settings_data.get("direct_lookups", {}),
            "rvu_table": self.settings_data.get("rvu_table", {}),
            "classification_rules": self.settings_data.get("classification_rules", {}),
            "window_positions": self.settings_data.get("window_positions", {}),
            "records": self.records_data.get("records", []),
            "current_shift": self.records_data.get("current_shift", {
                "shift_start": None,
                "shift_end": None,
                "records": []
            }),
            "shifts": self.records_data.get("shifts", [])
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
        
        # Default settings structure
        return {
            "settings": {
                "auto_start": False,
                "show_total": True,
                "show_avg": True,
                "show_last_hour": True,
                "show_last_full_hour": True,
                "show_projected": True,
                "min_study_seconds": 5,
                "ignore_duplicate_accessions": True,
            },
            "direct_lookups": {},
            "rvu_table": RVU_TABLE.copy(),
            "classification_rules": {},
            "window_positions": {
                "main": {"x": 100, "y": 100},
                "settings": {"x": 200, "y": 200}
            }
        }
    
    def load_records(self) -> dict:
        """Load records, current shift, and historical shifts."""
        try:
            if os.path.exists(self.records_file):
                with open(self.records_file, 'r') as f:
                    data = json.load(f)
                    logger.info(f"Loaded records from {self.records_file}")
                    return data
        except Exception as e:
            logger.error(f"Error loading records: {e}")
        
        # Default records structure
        return {
            "records": [],
            "current_shift": {
                "shift_start": None,
                "shift_end": None,
                "records": []
            },
            "shifts": []
        }
    
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
    
    def save(self):
        """Save data to appropriate files."""
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
        
        if "records" in self.data:
            self.records_data["records"] = self.data["records"]
        if "current_shift" in self.data:
            self.records_data["current_shift"] = self.data["current_shift"]
        if "shifts" in self.data:
            self.records_data["shifts"] = self.data["shifts"]
        
        # Save settings file
        try:
            settings_to_save = {
                "settings": self.settings_data.get("settings", {}),
                "direct_lookups": self.settings_data.get("direct_lookups", {}),
                "rvu_table": self.settings_data.get("rvu_table", {}),
                "classification_rules": self.settings_data.get("classification_rules", {}),
                "window_positions": self.settings_data.get("window_positions", {})
            }
            with open(self.settings_file, 'w') as f:
                json.dump(settings_to_save, f, indent=2, default=str)
            logger.info(f"Saved settings to {self.settings_file}")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
        
        # Save records file
        try:
            records_to_save = {
                "records": self.records_data.get("records", []),
                "current_shift": self.records_data.get("current_shift", {
                    "shift_start": None,
                    "shift_end": None,
                    "records": []
                }),
                "shifts": self.records_data.get("shifts", [])
            }
            with open(self.records_file, 'w') as f:
                json.dump(records_to_save, f, indent=2, default=str)
            logger.info(f"Saved records to {self.records_file}")
        except Exception as e:
            logger.error(f"Error saving records: {e}")
    
    def end_current_shift(self):
        """End the current shift and move it to historical shifts."""
        if self.data["current_shift"]["shift_start"]:
            current_shift = self.data["current_shift"].copy()
            current_shift["shift_end"] = datetime.now().isoformat()
            self.data["shifts"].append(current_shift)
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
        self.save()
        logger.info("Cleared all data")


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
        
        for accession, study in list(self.active_studies.items()):
            # If this accession is currently visible, it's not completed
            if accession == current_accession:
                continue
            
            # If current_accession is empty or different, this study has disappeared
            # Mark it as completed immediately (don't wait for 1 second)
            time_since_last_seen = (current_time - study["last_seen"]).total_seconds()
            
            # Study is considered completed if:
            # 1. A different study is now visible (current_accession is set and different), OR
            # 2. No study is visible (current_accession is empty) and it hasn't been seen for > 1 second
            if current_accession or time_since_last_seen > 1.0:
                duration = (study["last_seen"] - study["start_time"]).total_seconds()
                
                # Only count if duration >= min_seconds
                if duration >= self.min_seconds:
                    completed_study = study.copy()
                    completed_study["end_time"] = study["last_seen"]
                    completed_study["duration"] = duration
                    completed.append(completed_study)
                    logger.info(f"Completed study: {accession} - {study['study_type']} ({duration:.1f}s)")
                else:
                    logger.debug(f"Ignored short study: {accession} ({duration:.1f}s < {self.min_seconds}s)")
                
                to_remove.append(accession)
        
        for accession in to_remove:
            if accession in self.active_studies:
                del self.active_studies[accession]
        
        return completed
    
    def should_ignore(self, accession: str, ignore_duplicates: bool) -> bool:
        """Check if study should be ignored (only if already completed, not if currently active)."""
        if not accession:
            return True
        
        # Only ignore if it was already completed (in seen_accessions) AND ignore_duplicates is True
        # Don't ignore if it's currently active
        if ignore_duplicates and accession in self.seen_accessions and accession not in self.active_studies:
            logger.debug(f"Ignoring duplicate completed accession: {accession}")
            return True
        
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
        self.root.geometry("240x500")  # 40% narrower (400 * 0.6 = 240)
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)  # Keep window on top
        
        # Window dragging state
        self.drag_start_x = 0
        self.drag_start_y = 0
        
        # Data management
        self.data_manager = RVUData()
        
        # Load saved window position or use default (after data_manager is initialized)
        window_pos = self.data_manager.data.get("window_positions", {}).get("main", None)
        if window_pos:
            self.root.geometry(f"240x500+{window_pos['x']}+{window_pos['y']}")
        self.tracker = StudyTracker(
            min_seconds=self.data_manager.data["settings"]["min_study_seconds"]
        )
        
        # State
        self.shift_start: Optional[datetime] = None
        self.is_running = False
        self.current_window = None
        self.refresh_interval = 1000  # 1 second
            
        # Current detected data (must be initialized before create_ui)
        self.current_accession = ""
        self.current_procedure = ""
        self.current_patient_class = ""
        self.current_study_type = ""
        self.current_study_rvu = 0.0
        
        # Cache for performance
        self.cached_window = None
        self.cached_elements = {}  # automation_id -> element reference
        self.last_record_count = 0  # Track when to rebuild widgets
        
        # Create UI
        self.create_ui()
            
        # Load shift start if auto-start enabled (after UI is created)
        if self.data_manager.data["settings"].get("auto_start", False):
            if self.data_manager.data["current_shift"].get("shift_start"):
                try:
                    self.shift_start = datetime.fromisoformat(self.data_manager.data["current_shift"]["shift_start"])
                    self.is_running = True
                    # Update button and UI to reflect running state
                    self.start_btn.config(text="Stop Shift")
                    self.root.title("RVU Counter - Running")
                    self.update_shift_start_label()
                    self.update_recent_studies_label()
                    # Update display to show correct counters
                    self.update_display()
                    logger.info(f"Auto-resumed shift started at {self.shift_start}")
                except Exception as e:
                    logger.error(f"Error parsing shift_start: {e}")
                    # If parsing fails, start a new shift
                    self.shift_start = datetime.now()
                    self.is_running = True
                    self.data_manager.data["current_shift"]["shift_start"] = self.shift_start.isoformat()
                    self.data_manager.save()
                    self.start_btn.config(text="Stop Shift")
                    self.root.title("RVU Counter - Running")
                    self.update_shift_start_label()
                    self.update_recent_studies_label()
                    self.update_display()
        else:
                # No existing shift, start a new one
                self.shift_start = datetime.now()
                self.is_running = True
                self.data_manager.data["current_shift"]["shift_start"] = self.shift_start.isoformat()
                self.data_manager.save()
                self.start_btn.config(text="Stop Shift")
                self.root.title("RVU Counter - Running")
                self.update_shift_start_label()
                self.update_recent_studies_label()
                self.update_display()
        
        self.setup_refresh()
        
        logger.info("RVU Counter application started")
    
    def create_ui(self):
        """Create the user interface."""
        # Create style for red label frame
        style = ttk.Style()
        style.configure("Red.TLabelframe.Label", foreground="red")
        
        # Main frame
        main_frame = ttk.Frame(self.root, padding="5")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title bar is draggable (bind to main frame)
        main_frame.bind("<Button-1>", self.start_drag)
        main_frame.bind("<B1-Motion>", self.on_drag)
        
        # Counters frame with shift start time
        counters_label_frame = ttk.Frame(main_frame)
        counters_label_frame.pack(fill=tk.X, pady=(5, 0))
        
        counters_title_label = ttk.Label(counters_label_frame, text="Counters", font=("Arial", 10, "bold"))
        counters_title_label.pack(side=tk.LEFT)
        
        self.shift_start_label = ttk.Label(counters_label_frame, text="", font=("Arial", 8), foreground="gray")
        self.shift_start_label.pack(side=tk.LEFT, padx=(10, 0))
        
        counters_frame = ttk.LabelFrame(main_frame, padding="5")
        counters_frame.pack(fill=tk.X, pady=(0, 5))
        
        # Use grid for aligned columns
        counters_frame.columnconfigure(1, weight=1)
        
        # Counter labels with aligned columns
        row = 0
        
        # Total
        self.total_label_text = ttk.Label(counters_frame, text="Total wRVU:", font=("Arial", 10), anchor=tk.E)
        self.total_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        self.total_label = ttk.Label(counters_frame, text="0.0", font=("Arial", 10), anchor=tk.W)
        self.total_label.grid(row=row, column=1, sticky=tk.W)
        row += 1
        
        # Average per hour
        self.avg_label_text = ttk.Label(counters_frame, text="Avg/Hour:", font=("Arial", 10), anchor=tk.E)
        self.avg_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        self.avg_label = ttk.Label(counters_frame, text="0.0", font=("Arial", 10), anchor=tk.W)
        self.avg_label.grid(row=row, column=1, sticky=tk.W)
        row += 1
        
        # Last hour
        self.last_hour_label_text = ttk.Label(counters_frame, text="Last Hour:", font=("Arial", 10), anchor=tk.E)
        self.last_hour_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        self.last_hour_label = ttk.Label(counters_frame, text="0.0", font=("Arial", 10), anchor=tk.W)
        self.last_hour_label.grid(row=row, column=1, sticky=tk.W)
        row += 1
        
        # Last full hour
        self.last_full_hour_label_text = ttk.Label(counters_frame, text="Last Full Hour:", font=("Arial", 10), anchor=tk.E)
        self.last_full_hour_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        last_full_hour_value_frame = ttk.Frame(counters_frame)
        last_full_hour_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.last_full_hour_label = ttk.Label(last_full_hour_value_frame, text="0.0", font=("Arial", 10), anchor=tk.W)
        self.last_full_hour_label.pack(side=tk.LEFT)
        self.last_full_hour_range_label = ttk.Label(last_full_hour_value_frame, text="", font=("Arial", 7), foreground="gray")
        self.last_full_hour_range_label.pack(side=tk.LEFT, padx=(5, 0))
        self.last_full_hour_value_frame = last_full_hour_value_frame
        row += 1
        
        # Projected
        self.projected_label_text = ttk.Label(counters_frame, text="Projected This Hour:", font=("Arial", 10), anchor=tk.E)
        self.projected_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        self.projected_label = ttk.Label(counters_frame, text="0.0", font=("Arial", 10), anchor=tk.W)
        self.projected_label.grid(row=row, column=1, sticky=tk.W)
        
        # Buttons frame
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(fill=tk.X, pady=5)
        
        self.start_btn = ttk.Button(buttons_frame, text="Start Shift", command=self.start_shift, width=12)
        self.start_btn.pack(side=tk.LEFT, padx=2)
        
        self.undo_btn = ttk.Button(buttons_frame, text="Undo", command=self.undo_last, width=12, state=tk.DISABLED)
        self.undo_btn.pack(side=tk.LEFT, padx=2)
        
        # Track if undo has been used
        self.undo_used = False
        
        self.settings_btn = ttk.Button(buttons_frame, text="Settings", command=self.open_settings, width=12)
        self.settings_btn.pack(side=tk.LEFT, padx=2)
        
        # Recent studies frame
        self.recent_frame = ttk.LabelFrame(main_frame, text="Recent Studies", padding=(3, 5, 3, 5))  # Small padding all around
        self.recent_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Canvas with scrollbar for recent studies
        canvas_frame = ttk.Frame(self.recent_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        canvas = tk.Canvas(canvas_frame, height=100, highlightthickness=0, bd=0)  # No border/highlight
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        self.studies_scrollable_frame = ttk.Frame(canvas)
        
        canvas_window = canvas.create_window((0, 0), window=self.studies_scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=0, pady=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Store study widgets for deletion
        self.study_widgets = []
        
        def configure_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        def configure_canvas_width(event):
            # Make the canvas window match the canvas width
            canvas.itemconfig(canvas_window, width=event.width)
        
        self.studies_scrollable_frame.bind("<Configure>", configure_scroll_region)
        canvas.bind("<Configure>", configure_canvas_width)
        
        # Store canvas reference for scrolling
        self.studies_canvas = canvas
        
        # Current Study frame at bottom
        debug_frame = ttk.LabelFrame(main_frame, text="Current Study", padding="3")
        debug_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.debug_accession_label = ttk.Label(debug_frame, text="Accession: -", font=("Consolas", 8), foreground="gray")
        self.debug_accession_label.pack(anchor=tk.W)
        
        self.debug_patient_class_label = ttk.Label(debug_frame, text="Patient Class: -", font=("Consolas", 8), foreground="gray")
        self.debug_patient_class_label.pack(anchor=tk.W)
        
        self.debug_procedure_label = ttk.Label(debug_frame, text="Procedure: -", font=("Consolas", 8), foreground="gray")
        self.debug_procedure_label.pack(anchor=tk.W)
        
        # Study Type with RVU frame (to align RVU to the right)
        study_type_frame = ttk.Frame(debug_frame)
        study_type_frame.pack(fill=tk.X)
        
        self.debug_study_type_label = ttk.Label(study_type_frame, text="Study Type: -", font=("Consolas", 8), foreground="gray")
        self.debug_study_type_label.pack(side=tk.LEFT, anchor=tk.W)
        
        self.debug_study_rvu_label = ttk.Label(study_type_frame, text="", font=("Consolas", 8), foreground="gray")
        self.debug_study_rvu_label.pack(side=tk.RIGHT, anchor=tk.E)
        
        # Store study widgets for deletion (initialized in create_ui)
        self.study_widgets = []
        
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
    
    def refresh_data(self):
        """Refresh data from PowerScribe - optimized with caching."""
        try:
            # Try to use cached window, only search if invalid
            window = self.cached_window
            if not window:
                window = find_powerscribe_window()
            if not window:
                self.root.title("RVU Counter - PowerScribe not found")
                self.current_accession = ""
                self.current_procedure = ""
                self.current_study_type = ""
                self.update_debug_display()
                self.cached_window = None
                self.cached_elements = {}
                return
            self.cached_window = window
            
            # Validate cached window is still valid
            try:
                window.window_text()  # Test if window still exists
            except:
                # Window closed, clear cache and search again
                self.cached_window = None
                self.cached_elements = {}
                window = find_powerscribe_window()
                if not window:
                    self.root.title("RVU Counter - PowerScribe not found")
                    self.current_accession = ""
                    self.current_procedure = ""
                    self.current_study_type = ""
                    self.update_debug_display()
                    return
                self.cached_window = window
            
            self.current_window = window
            
            # Find elements - try cached first, but always validate
            accession = ""
            procedure = ""
            
            # Try to use cached elements if available
            patient_class = ""
            if self.cached_elements.get("labelAccession") and self.cached_elements.get("labelProcDescription"):
                try:
                    # Validate cached elements are still valid
                    accession = self.cached_elements["labelAccession"].window_text().strip()
                    procedure = self.cached_elements["labelProcDescription"].window_text().strip()
                    if self.cached_elements.get("labelPatientClass"):
                        patient_class = self.cached_elements["labelPatientClass"].window_text().strip()
                except:
                    # Elements invalid, clear cache and re-search
                    self.cached_elements = {}
            
            # If cache is empty or invalid, search for elements (only the ones we need)
            if not self.cached_elements:
                # Perform search for only the specific elements we need
                elements = find_elements_by_automation_id(
                    window,
                    ["labelProcDescription", "labelAccession", "labelPatientClass"]
                )
                # Cache element references if found
                if elements.get("labelAccession") and elements.get("labelProcDescription"):
                    self.cached_elements = {
                        "labelAccession": elements.get("labelAccession", {}).get("element"),
                        "labelProcDescription": elements.get("labelProcDescription", {}).get("element"),
                    }
                    # Cache patient class if found
                    if elements.get("labelPatientClass"):
                        self.cached_elements["labelPatientClass"] = elements.get("labelPatientClass", {}).get("element")
                    
                    # Get text from newly found elements
                    try:
                        accession = elements.get("labelAccession", {}).get("text", "").strip()
                        procedure = elements.get("labelProcDescription", {}).get("text", "").strip()
                        patient_class = elements.get("labelPatientClass", {}).get("text", "").strip() if elements.get("labelPatientClass") else ""
                    except:
                        accession = ""
                        procedure = ""
                        patient_class = ""
                else:
                    # Elements not found - that's fine, just use empty values
                    # Don't cache "not found" state, always check next time
                    accession = ""
                    procedure = ""
                    patient_class = ""
            
            # Update debug display
            self.current_accession = accession
            self.current_procedure = procedure
            self.current_patient_class = patient_class
            
            # Check if procedure is "n/a" (case-insensitive)
            is_na = procedure and procedure.strip().lower() in ["n/a", "na", "none", ""]
            
            if procedure and not is_na:
                classification_rules = self.data_manager.data.get("classification_rules", {})
                direct_lookups = self.data_manager.data.get("direct_lookups", {})
                study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                # Store type and RVU separately for display
                self.current_study_type = study_type
                self.current_study_rvu = rvu
                logger.debug(f"Set current_study_type={study_type}, current_study_rvu={rvu}")
            else:
                self.current_study_type = ""
                self.current_study_rvu = 0.0
            
            self.update_debug_display()
            
            # Only process if shift is running
            if not self.is_running:
                return
        
            current_time = datetime.now()
            
            # If procedure changed to "n/a", immediately complete all active studies
            if is_na and self.tracker.active_studies:
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
                        self.data_manager.data["current_shift"]["records"].append(study_record)
                        self.data_manager.save()
                        self.undo_used = False
                        self.undo_btn.config(state=tk.NORMAL)
                        logger.info(f"Recorded completed study (N/A trigger): {completed_study['accession']} - {completed_study['study_type']} ({completed_study['rvu']} RVU) - Duration: {duration:.1f}s")
                # Clear all active studies
                self.tracker.active_studies.clear()
                self.update_display()
                return  # Return after handling N/A case
        
            # Check for completed studies FIRST (before checking if we should ignore)
            # This handles studies that have disappeared
            completed = self.tracker.check_completed(current_time, accession)
            
            for study in completed:
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
                self.data_manager.data["current_shift"]["records"].append(study_record)
                self.data_manager.save()
                # Reset undo button when new study is added
                self.undo_used = False
                self.undo_btn.config(state=tk.NORMAL)
                logger.info(f"Recorded completed study: {study['accession']} - {study['study_type']} ({study['rvu']} RVU) - Duration: {study['duration']:.1f}s")
                self.update_display()
            
            # Now handle current study
            if not accession:
                # No current study - all active studies should be checked for completion
                # This is already handled above, so just return
                return
            
            # Check if should ignore (only ignore if already completed in this shift)
            ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
            
            # Get classification rules, direct lookups, and RVU table for matching
            classification_rules = self.data_manager.data.get("classification_rules", {})
            direct_lookups = self.data_manager.data.get("direct_lookups", {})
            rvu_table = self.data_manager.data["rvu_table"]
            
            # If study is already active, update it (don't ignore)
            if accession in self.tracker.active_studies:
                self.tracker.add_study(accession, procedure, current_time, rvu_table, classification_rules, direct_lookups, self.current_patient_class)
                return
            
            # If study was already completed in this shift, ignore it
            if self.tracker.should_ignore(accession, ignore_duplicates):
                logger.debug(f"Ignoring duplicate accession: {accession}")
                return
            
            # New study - add it
            self.tracker.add_study(accession, procedure, current_time, rvu_table, classification_rules, direct_lookups, self.current_patient_class)
            self.tracker.mark_seen(accession)
            
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
            # Stop current shift
            self.is_running = False
            self.data_manager.data["current_shift"]["shift_end"] = datetime.now().isoformat()
            self.start_btn.config(text="Start Shift")
            self.root.title("RVU Counter - Stopped")
            self.shift_start = None
            self.update_shift_start_label()
            self.update_recent_studies_label()
            self.data_manager.save()
            logger.info("Shift stopped")
        else:
            # End previous shift if it exists
            if self.data_manager.data["current_shift"].get("shift_start"):
                self.data_manager.end_current_shift()
            
            # Start new shift
            self.shift_start = datetime.now()
            self.data_manager.data["current_shift"]["shift_start"] = self.shift_start.isoformat()
            self.data_manager.data["current_shift"]["shift_end"] = None
            self.data_manager.data["current_shift"]["records"] = []
            self.tracker = StudyTracker(
                min_seconds=self.data_manager.data["settings"]["min_study_seconds"]
            )
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
        records = self.data_manager.data["current_shift"]["records"]
        if 0 <= index < len(records):
            removed = records.pop(index)
            self.data_manager.save()
            logger.info(f"Deleted study: {removed['accession']}")
            self.update_display()
    
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
            }
        
        records = self.data_manager.data["current_shift"]["records"]
        current_time = datetime.now()
        
        # Total
        total_rvu = sum(r["rvu"] for r in records)
        
        # Average per hour
        hours_elapsed = (current_time - self.shift_start).total_seconds() / 3600
        avg_per_hour = total_rvu / hours_elapsed if hours_elapsed > 0 else 0.0
        
        # Last hour
        one_hour_ago = current_time - timedelta(hours=1)
        last_hour_rvu = sum(
            r["rvu"] for r in records
            if datetime.fromisoformat(r["time_finished"]) >= one_hour_ago
        )
        
        # Last full hour (e.g., 2am to 3am)
        current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
        last_full_hour_start = current_hour_start - timedelta(hours=1)
        last_full_hour_end = current_hour_start
        
        last_full_hour_rvu = sum(
            r["rvu"] for r in records
            if last_full_hour_start <= datetime.fromisoformat(r["time_finished"]) < last_full_hour_end
        )
        last_full_hour_range = f"{self._format_hour_label(last_full_hour_start)}-{self._format_hour_label(last_full_hour_end)}"
        
        # Projected for current hour
        current_hour_rvu = sum(
            r["rvu"] for r in records
            if datetime.fromisoformat(r["time_finished"]) >= current_hour_start
        )
        minutes_into_hour = (current_time - current_hour_start).total_seconds() / 60
        if minutes_into_hour > 0:
            projected = (current_hour_rvu / minutes_into_hour) * 60
        else:
            projected = 0.0
        
        return {
            "total": total_rvu,
            "avg_per_hour": avg_per_hour,
            "last_hour": last_hour_rvu,
            "last_full_hour": last_full_hour_rvu,
            "last_full_hour_range": last_full_hour_range,
            "projected": projected,
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
        if self.is_running and self.shift_start:
            self.recent_frame.config(text="Recent Studies", style="TLabelframe")
        else:
            self.recent_frame.config(text="Temporary Recent - No shift started", style="Red.TLabelframe")
    
    def update_display(self):
        """Update the display with current statistics."""
        # Update recent studies label based on shift status
        self.update_recent_studies_label()
        
        # Only rebuild widgets if record count changed
        current_count = len(self.data_manager.data["current_shift"]["records"])
        rebuild_widgets = (current_count != self.last_record_count)
        self.last_record_count = current_count
        
        stats = self.calculate_stats()
        settings = self.data_manager.data["settings"]
        
        if settings.get("show_total", True):
            self.total_label_text.grid()
            self.total_label.grid()
            self.total_label.config(text=f"{stats['total']:.1f}")
        else:
            self.total_label_text.grid_remove()
            self.total_label.grid_remove()
        
        if settings.get("show_avg", True):
            self.avg_label_text.grid()
            self.avg_label.grid()
            self.avg_label.config(text=f"{stats['avg_per_hour']:.1f}")
        else:
            self.avg_label_text.grid_remove()
            self.avg_label.grid_remove()
        
        if settings.get("show_last_hour", True):
            self.last_hour_label_text.grid()
            self.last_hour_label.grid()
            self.last_hour_label.config(text=f"{stats['last_hour']:.1f}")
        else:
            self.last_hour_label_text.grid_remove()
            self.last_hour_label.grid_remove()
        
        if settings.get("show_last_full_hour", True):
            self.last_full_hour_label_text.grid()
            self.last_full_hour_value_frame.grid()
            self.last_full_hour_label.config(text=f"{stats['last_full_hour']:.1f}")
            range_text = stats.get("last_full_hour_range", "")
            self.last_full_hour_range_label.config(text=f"({range_text})" if range_text else "")
        else:
            self.last_full_hour_label_text.grid_remove()
            self.last_full_hour_value_frame.grid_remove()
        
        if settings.get("show_projected", True):
            self.projected_label_text.grid()
            self.projected_label.grid()
            self.projected_label.config(text=f"{stats['projected']:.1f}")
        else:
            self.projected_label_text.grid_remove()
            self.projected_label.grid_remove()
        
        # Only rebuild widgets if records changed
        if rebuild_widgets:
            # Update recent studies list with X buttons
            # Clear existing widgets
            for widget in self.study_widgets:
                widget.destroy()
            self.study_widgets.clear()
            
            records = self.data_manager.data["current_shift"]["records"][-6:]  # Last 6 studies
            # Display in reverse order (most recent first)
            for i, record in enumerate(reversed(records)):
                # Calculate actual index in full records list
                actual_index = len(self.data_manager.data["current_shift"]["records"]) - 1 - i
                
                # Create frame for this study
                study_frame = ttk.Frame(self.studies_scrollable_frame)
                study_frame.pack(fill=tk.X, pady=1, padx=0)  # No horizontal padding
                
                # X button to delete (on the left)
                delete_btn = ttk.Button(
                    study_frame, 
                    text="×", 
                    width=2,
                    command=lambda idx=actual_index: self.delete_study_by_index(idx)
                )
                delete_btn.pack(side=tk.LEFT, padx=(2, 5))
                
                # Study text label (show actual procedure name, not normalized type, no accession)
                procedure_name = record.get('procedure', record.get('study_type', 'Unknown'))
                study_type = record.get('study_type', 'Unknown')
                
                # Check if study starts with CT, MR, US, XR, or NM (case-insensitive)
                procedure_upper = procedure_name.upper().strip()
                study_type_upper = study_type.upper().strip()
                valid_prefixes = ['CT', 'MR', 'US', 'XR', 'NM']
                starts_with_valid = any(procedure_upper.startswith(prefix) or study_type_upper.startswith(prefix) for prefix in valid_prefixes)
                
                # Truncate long procedure names to fit narrow window
                # With font size 8, need shorter length to accommodate RVU on the right
                max_length = 23
                if len(procedure_name) > max_length:
                    procedure_name = procedure_name[:max_length-3] + "..."
                
                # Create separate labels for procedure name and RVU
                procedure_label = ttk.Label(study_frame, text=procedure_name, font=("Consolas", 8))
                # Color dark red if study doesn't start with valid prefixes
                if not starts_with_valid:
                    procedure_label.config(foreground="#8B0000")  # Dark red
                procedure_label.pack(side=tk.LEFT, padx=(0, 2))  # Minimal right padding
                
                # RVU label (right-justified)
                rvu_text = f"{record['rvu']:.1f} RVU"
                rvu_label = ttk.Label(study_frame, text=rvu_text, font=("Consolas", 8))
                if not starts_with_valid:
                    rvu_label.config(foreground="#8B0000")  # Dark red
                rvu_label.pack(side=tk.RIGHT, padx=(0, 0))  # No padding - flush to edge
                
                self.study_widgets.append(study_frame)
            
            # Scroll to top to show most recent
            self.studies_canvas.update_idletasks()
            self.studies_canvas.yview_moveto(0)
            
            if len(self.data_manager.data["current_shift"]["records"]) > 10:
                more_label = ttk.Label(self.studies_scrollable_frame, text=f"... {len(self.data_manager.data['current_shift']['records']) - 10} more", font=("Consolas", 7), foreground="gray")
                more_label.pack()
                self.study_widgets.append(more_label)
    
    def update_debug_display(self):
        """Update the debug display with current PowerScribe data."""
        # Check if procedure is "n/a" - if so, don't display anything
        is_na = self.current_procedure and self.current_procedure.strip().lower() in ["n/a", "na", "none", ""]
        
        if is_na or not self.current_procedure:
            self.debug_accession_label.config(text="")
            self.debug_procedure_label.config(text="")
            self.debug_patient_class_label.config(text="")
            self.debug_study_type_label.config(text="")
            self.debug_study_rvu_label.config(text="")
        else:
            self.debug_accession_label.config(text=f"Accession: {self.current_accession if self.current_accession else '-'}")
            # Truncate procedure to fit
            procedure_display = self.current_procedure[:35] + "..." if len(self.current_procedure) > 35 else self.current_procedure
            self.debug_procedure_label.config(text=f"Procedure: {procedure_display if procedure_display else '-'}")
            self.debug_patient_class_label.config(text=f"Patient Class: {self.current_patient_class if self.current_patient_class else '-'}")
            # Display study type with RVU on the right (separate labels for alignment)
            if self.current_study_type:
                study_type_display = self.current_study_type[:13] + "..." if len(self.current_study_type) > 13 else self.current_study_type
                self.debug_study_type_label.config(text=f"Study Type: {study_type_display}")
                rvu_value = self.current_study_rvu if self.current_study_rvu is not None else 0.0
                self.debug_study_rvu_label.config(text=f"{rvu_value:.1f} RVU")
            else:
                self.debug_study_type_label.config(text=f"Study Type: -")
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
    
    def start_drag(self, event):
        """Start dragging window."""
        self.drag_start_x = event.x
        self.drag_start_y = event.y
    
    def on_drag(self, event):
        """Handle window dragging."""
        x = self.root.winfo_x() + event.x - self.drag_start_x
        y = self.root.winfo_y() + event.y - self.drag_start_y
        self.root.geometry(f"+{x}+{y}")
        # Debounce position saving during drag
        if hasattr(self, '_position_save_timer'):
            self.root.after_cancel(self._position_save_timer)
        self._position_save_timer = self.root.after(500, self.save_window_position)
    
    def save_window_position(self):
        """Save the main window position."""
        try:
            if "window_positions" not in self.data_manager.data:
                self.data_manager.data["window_positions"] = {}
            self.data_manager.data["window_positions"]["main"] = {
                "x": self.root.winfo_x(),
                "y": self.root.winfo_y()
            }
            self.data_manager.save()
        except Exception as e:
            logger.error(f"Error saving window position: {e}")
    
    def on_closing(self):
        """Handle window closing."""
        self.save_window_position()
        self.data_manager.save()
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
            self.window.geometry(f"350x400+{window_pos['x']}+{window_pos['y']}")
        else:
            self.window.geometry("350x400")
        
        self.window.transient(parent)
        self.window.grab_set()
        
        # Track last saved position to avoid excessive saves
        self.last_saved_x = None
        self.last_saved_y = None
        
        # Bind to window movement to save position (debounced)
        self.window.bind("<Configure>", self.on_settings_window_move)
        self.window.protocol("WM_DELETE_WINDOW", self.on_settings_closing)
        
        self.create_settings_ui()
    
    def create_settings_ui(self):
        """Create settings UI."""
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Auto-start
        self.auto_start_var = tk.BooleanVar(value=self.data_manager.data["settings"]["auto_start"])
        ttk.Checkbutton(main_frame, text="Auto-resume shift on launch", variable=self.auto_start_var).pack(anchor=tk.W, pady=2)
        
        # Counter visibility
        ttk.Label(main_frame, text="Show Counters:", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10, 5))
        
        self.show_total_var = tk.BooleanVar(value=self.data_manager.data["settings"]["show_total"])
        ttk.Checkbutton(main_frame, text="Total", variable=self.show_total_var).pack(anchor=tk.W, pady=2)
        
        self.show_avg_var = tk.BooleanVar(value=self.data_manager.data["settings"]["show_avg"])
        ttk.Checkbutton(main_frame, text="Average per Hour", variable=self.show_avg_var).pack(anchor=tk.W, pady=2)
        
        self.show_last_hour_var = tk.BooleanVar(value=self.data_manager.data["settings"]["show_last_hour"])
        ttk.Checkbutton(main_frame, text="Last Hour", variable=self.show_last_hour_var).pack(anchor=tk.W, pady=2)
        
        self.show_last_full_hour_var = tk.BooleanVar(value=self.data_manager.data["settings"]["show_last_full_hour"])
        ttk.Checkbutton(main_frame, text="Last Full Hour", variable=self.show_last_full_hour_var).pack(anchor=tk.W, pady=2)
        
        self.show_projected_var = tk.BooleanVar(value=self.data_manager.data["settings"]["show_projected"])
        ttk.Checkbutton(main_frame, text="Projected This Hour", variable=self.show_projected_var).pack(anchor=tk.W, pady=2)
        
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
        
        # Save/Cancel
        save_cancel_frame = ttk.Frame(main_frame)
        save_cancel_frame.pack(fill=tk.X, pady=10)
        
        ttk.Button(save_cancel_frame, text="Save", command=self.save_settings).pack(side=tk.LEFT, padx=2)
        ttk.Button(save_cancel_frame, text="Cancel", command=self.window.destroy).pack(side=tk.LEFT, padx=2)
    
    def save_settings(self):
        """Save settings."""
        try:
            self.data_manager.data["settings"]["auto_start"] = self.auto_start_var.get()
            self.data_manager.data["settings"]["show_total"] = self.show_total_var.get()
            self.data_manager.data["settings"]["show_avg"] = self.show_avg_var.get()
            self.data_manager.data["settings"]["show_last_hour"] = self.show_last_hour_var.get()
            self.data_manager.data["settings"]["show_last_full_hour"] = self.show_last_full_hour_var.get()
            self.data_manager.data["settings"]["show_projected"] = self.show_projected_var.get()
            self.data_manager.data["settings"]["min_study_seconds"] = int(self.min_seconds_var.get())
            self.data_manager.data["settings"]["ignore_duplicate_accessions"] = self.ignore_duplicates_var.get()
            
            # Update tracker min_seconds
            self.app.tracker.min_seconds = self.data_manager.data["settings"]["min_study_seconds"]
            
            self.data_manager.save()
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
                    # Debounce: save after 500ms of no movement
                    if hasattr(self, '_save_timer'):
                        self.window.after_cancel(self._save_timer)
                    self._save_timer = self.window.after(500, lambda: self.save_settings_position(x, y))
            except Exception as e:
                logger.error(f"Error saving settings window position: {e}")
    
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


def main():
    """Main entry point."""
    root = tk.Tk()
    app = RVUCounterApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
