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
    "CT TL Spine": 2.0,
    "CT Face": 1.0,
    "XR Abdomen": 0.3,
    "XR MSK": 0.3,
}


# Global cached desktop object - creating Desktop() is slow
_cached_desktop = None

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
    except:
        pass
    
    # Try other common titles
    for title in ["PowerScribe 360", "PowerScribe 360 - Reporting"]:
        try:
            windows = desktop.windows(title=title, visible_only=True)
            for window in windows:
                try:
                    if "RVU Counter" not in window.window_text():
                        return window
                except:
                    continue
        except:
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
                window_text = window.window_text().lower()
                # Exclude test/viewer windows and RVU Counter
                if ("rvu counter" in window_text or 
                    "test" in window_text or 
                    "viewer" in window_text or 
                    "ui elements" in window_text or
                    "diagnostic" in window_text):
                    continue
                
                # Look for Mosaic Info Hub window
                if "mosaic" in window_text and "info hub" in window_text:
                    # Verify it has the MainForm automation ID
                    try:
                        automation_id = window.element_info.automation_id
                        if automation_id == "MainForm":
                            return window
                    except:
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
        children = main_window.children()
        for child in children:
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
        for child in main_window.descendants():
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
            text = webview_element.window_text() or ""
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
            children = webview_element.children()
                    for child in children:
                elements.extend(get_mosaic_elements(child, depth + 1, max_depth))
                except:
                    pass
            except:
                pass
    
    return elements


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
    """
    found_elements = {}
    ids_needing_search = []
    
    for auto_id in automation_ids:
        # Try cache first (instant)
        if cached_elements and auto_id in cached_elements:
            try:
                cached_elem = cached_elements[auto_id]['element']
                text_content = cached_elem.window_text()
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
            for element in window.descendants():
                if not remaining:
                    break
                try:
                    elem_auto_id = element.element_info.automation_id
                    if elem_auto_id and elem_auto_id in remaining:
                        text_content = element.window_text()
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
                logger.info(f"Matched classification rule for '{study_type}': {procedure_text} -> {study_type}")
                break  # Found a classification match, stop searching rules for this study_type
        
        # If we found a classification match, stop searching other study_types
        if classification_match_name:
            break
    
    # If classification rule matched, return it immediately (highest priority)
    if classification_match_name:
        logger.info(f"Matched classification rule: {procedure_text} -> {classification_match_name} ({classification_match_rvu} RVU)")
        return classification_match_name, classification_match_rvu
    
    # SECOND: Check direct/exact lookups (exact procedure name matches)
    if direct_lookups:
        # Try exact match (case-insensitive)
        for lookup_procedure, rvu_value in direct_lookups.items():
            if lookup_procedure.lower().strip() == procedure_lower:
                direct_match_rvu = rvu_value
                direct_match_name = lookup_procedure
                logger.info(f"Matched direct lookup: {procedure_text} -> {rvu_value} RVU")
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
    """Manages data persistence with separate files for settings and records."""
    
    def __init__(self, base_dir: str = None):
        settings_dir, data_dir = get_app_paths()
        
        # Settings file (RVU tables, rules, rates, user preferences, window positions)
        self.settings_file = os.path.join(data_dir, "rvu_settings.json")
        # Records file - persistent data (store next to exe or script)
        self.records_file = os.path.join(data_dir, "rvu_records.json")
        self.old_data_file = os.path.join(data_dir, "rvu_data.json")  # For migration
        
        # Track if running as frozen app
        self.is_frozen = getattr(sys, 'frozen', False)
        
        logger.info(f"Settings file: {self.settings_file}")
        logger.info(f"Records file: {self.records_file}")
        
        # Load data from files
        self.settings_data = self.load_settings()
        # Validate and fix window positions after loading
        self.settings_data = self._validate_window_positions(self.settings_data)
        self.records_data = self.load_records()
        
        # Migrate old file if it exists
        self.migrate_old_file()
        
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
                "shift_length_hours": 9,
                "min_study_seconds": 5,
                "ignore_duplicate_accessions": True,
                "data_source": "PowerScribe",  # "PowerScribe" or "Mosaic"
                "show_time": False,  # Show time information in recent studies
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
                "statistics": {"width": 1200, "height": 700}
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
        self.root.geometry("240x500")  # Default size
        self.root.minsize(200, 350)  # Minimum size
        self.root.resizable(True, True)
        self.root.attributes("-topmost", True)  # Keep window on top
        
        # Window dragging state
        self.drag_start_x = 0
        self.drag_start_y = 0
        
        # Data management
        self.data_manager = RVUData()
        
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
            self.refresh_interval = 1000  # 1 second
            
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
        
        self.setup_refresh()
        
        logger.info("RVU Counter application started")
    
    def create_ui(self):
        """Create the user interface."""
        # Create style
        self.style = ttk.Style()
        self.style.configure("Red.TLabelframe.Label", foreground="red")
        
        # Apply theme based on settings
        self.apply_theme()
        
        # Main frame
        main_frame = ttk.Frame(self.root, padding="5")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Title bar is draggable (bind to main frame)
        main_frame.bind("<Button-1>", self.start_drag)
        main_frame.bind("<B1-Motion>", self.on_drag)
        main_frame.bind("<ButtonRelease-1>", self.on_drag_end)
        
        # Top bar with Start/Stop Shift button and shift start time
        top_bar_frame = ttk.Frame(main_frame)
        top_bar_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.start_btn = ttk.Button(top_bar_frame, text="Start Shift", command=self.start_shift, width=12)
        self.start_btn.pack(side=tk.LEFT)
        
        self.shift_start_label = ttk.Label(top_bar_frame, text="", font=("Arial", 8), foreground="gray")
        self.shift_start_label.pack(side=tk.LEFT, padx=(10, 0))
        
        counters_frame = ttk.LabelFrame(main_frame, padding="5")
        counters_frame.pack(fill=tk.X, pady=(0, 5))  # Fill X for full-width border
        
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
        
        # Recent studies frame
        self.recent_frame = ttk.LabelFrame(main_frame, text="Recent Studies", padding=(3, 5, 3, 5))  # Small padding all around
        self.recent_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Canvas with scrollbar for recent studies
        canvas_frame = ttk.Frame(self.recent_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        canvas_bg = self.theme_colors.get("canvas_bg", "#f0f0f0")
        canvas = tk.Canvas(canvas_frame, height=100, highlightthickness=0, bd=0, bg=canvas_bg)
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
        
        # Study Type with RVU frame (to align RVU to the right) - separate labels like Recent Studies
        study_type_frame = ttk.Frame(debug_frame)
        study_type_frame.pack(fill=tk.X)
        
        self.debug_study_type_prefix_label = ttk.Label(study_type_frame, text="Study Type: ", font=("Consolas", 8), foreground="gray")
        self.debug_study_type_prefix_label.pack(side=tk.LEFT, anchor=tk.W)
        
        self.debug_study_type_label = ttk.Label(study_type_frame, text="-", font=("Consolas", 8), foreground="gray")
        self.debug_study_type_label.pack(side=tk.LEFT, anchor=tk.W)
        
        self.debug_study_rvu_label = ttk.Label(study_type_frame, text="", font=("Consolas", 8), foreground="gray")
        self.debug_study_rvu_label.pack(side=tk.RIGHT, anchor=tk.E)
        
        # Store study widgets for deletion (initialized in create_ui)
        self.study_widgets = []
        
        # Store debug_frame reference for resizing
        self.debug_frame = debug_frame
        
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
    
    def _record_multi_accession_study(self, current_time):
        """Record a completed multi-accession study."""
        if not self.multi_accession_data:
            return
        
        total_rvu = sum(d["rvu"] for d in self.multi_accession_data.values())
        
        # Get modality from collected studies
        modalities = set()
        for d in self.multi_accession_data.values():
            st = d["study_type"]
            if st:
                parts = st.split()
                if parts:
                    modalities.add(parts[0])
        modality = list(modalities)[0] if modalities else "Studies"
        
        # Determine duration
        if self.multi_accession_start_time:
            duration = (current_time - self.multi_accession_start_time).total_seconds()
        else:
            duration = 0
        
        # Get all accession numbers
        all_accessions = list(self.multi_accession_data.keys())
        accession_str = ", ".join(all_accessions)
        
        # Get all procedures
        all_procedures = [d["procedure"] for d in self.multi_accession_data.values()]
        
        # Get patient class from first entry
        patient_class_val = ""
        for d in self.multi_accession_data.values():
            if d.get("patient_class"):
                patient_class_val = d["patient_class"]
                break
        
        study_record = {
            "accession": accession_str,
            "procedure": f"Multiple {modality} ({len(all_accessions)} studies)",
            "patient_class": patient_class_val,
            "study_type": f"Multiple {modality}",
            "rvu": total_rvu,
            "time_performed": self.multi_accession_start_time.isoformat() if self.multi_accession_start_time else current_time.isoformat(),
            "time_finished": current_time.isoformat(),
            "duration_seconds": duration,
            "is_multi_accession": True,
            "accession_count": len(all_accessions),
            "individual_procedures": all_procedures,
        }
        
        self.data_manager.data["current_shift"]["records"].append(study_record)
        self.data_manager.save()
        self.undo_used = False
        self.undo_btn.config(state=tk.NORMAL)
        
        # Mark each individual accession as seen to prevent duplicates
        for acc in all_accessions:
            self.tracker.mark_seen(acc)
            logger.debug(f"Marked accession as seen: {acc}")
        
        logger.info(f"Recorded multi-accession study: {len(all_accessions)} accessions - Multiple {modality} ({total_rvu:.1f} RVU) - Duration: {duration:.1f}s")
        self.update_display()
    
    def _powerscribe_worker(self):
        """Background thread: Continuously poll PowerScribe or Mosaic for data."""
        import time
        
        while self._ps_thread_running:
            try:
                data_source = self.data_manager.data["settings"].get("data_source", "PowerScribe")
                
                data = {
                    'found': False,
                    'procedure': '',
                    'accession': '',
                    'patient_class': '',
                    'accession_title': '',
                    'multiple_accessions': [],
                    'elements': {}
                }
                
                if data_source == "PowerScribe":
                # Find PowerScribe window
                    window = self.cached_window
                    if not window:
                        window = find_powerscribe_window()
                    
                    if window:
                        # Validate window still exists
                        try:
                            window.window_text()
                            self.cached_window = window
                        except:
                            self.cached_window = None
                            self.cached_elements = {}
                            window = find_powerscribe_window()
                    
                    if window:
                        data['found'] = True
                        
                        # Find elements with caching
                        elements = find_elements_by_automation_id(
                            window,
                            ["labelProcDescription", "labelAccessionTitle", "labelAccession", "labelPatientClass", "listBoxAccessions"],
                            self.cached_elements
                        )
                        self.cached_elements.update(elements)
                        
                        data['elements'] = elements
                        data['procedure'] = elements.get("labelProcDescription", {}).get("text", "").strip()
                        data['patient_class'] = elements.get("labelPatientClass", {}).get("text", "").strip()
                        data['accession_title'] = elements.get("labelAccessionTitle", {}).get("text", "").strip()
                        data['accession'] = elements.get("labelAccession", {}).get("text", "").strip()
                        
                        # Handle multiple accessions
                        if elements.get("listBoxAccessions"):
                            try:
                                listbox = elements["listBoxAccessions"]["element"]
                                for child in listbox.children():
                                    try:
                                        item_text = child.window_text().strip()
                                        if item_text:
                                            data['multiple_accessions'].append(item_text)
                                    except:
                                        pass
                            except:
                                pass
                
                elif data_source == "Mosaic":
                    # Find Mosaic window
                    main_window = find_mosaic_window()
                    
                    if main_window:
                        try:
                            # Validate window still exists
                            main_window.window_text()
                            data['found'] = True
                            
                            # Find WebView2 control
                            webview = find_mosaic_webview_element(main_window)
                            
                            if webview:
                                # Extract data from Mosaic
                                mosaic_data = extract_mosaic_data(webview)
                                
                                data['procedure'] = mosaic_data.get('procedure', '')
                                data['patient_class'] = mosaic_data.get('patient_class', 'Unknown')
                                
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
                
                # Update shared data (thread-safe)
                with self._ps_lock:
                    self._ps_data = data
                    
            except Exception as e:
                logger.debug(f"Worker error: {e}")
            
            time.sleep(0.5)  # Poll every 500ms
    
    def refresh_data(self):
        """Refresh data from PowerScribe - reads from background thread data."""
        try:
            # Get data from background thread (non-blocking)
            with self._ps_lock:
                ps_data = self._ps_data.copy()
            
            data_source = self.data_manager.data["settings"].get("data_source", "PowerScribe")
            source_name = "PowerScribe" if data_source == "PowerScribe" else "Mosaic"
            
            if not ps_data.get('found', False):
                self.root.title(f"RVU Counter - {source_name} not found")
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
            
            # Debug: log what we're getting from worker thread
            if data_source == "Mosaic":
                logger.info(f"Mosaic data - procedure: '{procedure}', accession: '{accession}', multiple_accessions: {multiple_accessions}")
            
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
                    ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
                    if ignore_duplicates and acc in self.tracker.seen_accessions:
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
                accession = "Multiple Accessions"
                is_multi_accession_view = True  # Flag to prevent normal single-study tracking
                
                # Check if we're transitioning from single to multi-accession
                if not self.multi_accession_mode:
                    # Check if ALL accessions were already completed (to prevent duplicates)
                    ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
                    all_seen = ignore_duplicates and all(acc in self.tracker.seen_accessions for acc in multiple_accessions)
                    
                    if all_seen:
                        # All accessions already completed - don't track again, but still display properly
                        logger.info(f"Ignoring duplicate multi-accession study: all {len(multiple_accessions)} accessions already seen")
                        
                        # Set display to show this is a completed multi-accession
                        # Get modality from current procedure for display
                        if procedure and procedure.strip().lower() not in ["n/a", "na", "none", ""]:
                            classification_rules = self.data_manager.data.get("classification_rules", {})
                            direct_lookups = self.data_manager.data.get("direct_lookups", {})
                            study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                            parts = study_type.split() if study_type else []
                            modality = parts[0] if parts else "Studies"
                            self.current_study_type = f"Multiple {modality} (done)"
                            self.current_study_rvu = 0.0  # Already counted
                    else:
                        # Starting multi-accession mode
                        self.multi_accession_mode = True
                        self.multi_accession_start_time = datetime.now()
                        self.multi_accession_data = {}
                        self.multi_accession_last_procedure = ""  # Reset so first procedure gets collected
                        
                        # Check if any of the new accessions were being tracked as single
                        # If so, migrate their data to multi-accession tracking
                        for acc in multiple_accessions:
                            if acc in self.tracker.active_studies:
                                study = self.tracker.active_studies[acc]
                                self.multi_accession_data[acc] = {
                                    "procedure": study["procedure"],
                                    "study_type": study["study_type"],
                                    "rvu": study["rvu"],
                                    "patient_class": study.get("patient_class", ""),
                                }
                                # Remove from active_studies to prevent completion
                                del self.tracker.active_studies[acc]
                                logger.info(f"Migrated {acc} from single to multi-accession tracking")
                        
                        logger.info(f"Started multi-accession mode with {len(multiple_accessions)} accessions")
                
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
                        # Assign to the first accession that doesn't have data yet
                        for acc in multiple_accessions:
                            if acc not in self.multi_accession_data:
                                self.multi_accession_data[acc] = {
                                    "procedure": procedure,
                                    "study_type": study_type,
                                    "rvu": rvu,
                                    "patient_class": patient_class,
                                }
                                logger.info(f"Collected procedure for {acc}: {procedure} ({rvu} RVU)")
                                break
                        
                        # Update last seen procedure
                        self.multi_accession_last_procedure = procedure
            elif data_source != "Mosaic" and is_multiple_mode:
                # PowerScribe: Multiple accessions but list not loaded yet
                accession = "Multiple (loading...)"
            else:
                # SINGLE ACCESSION mode (PowerScribe) or Mosaic single/multiple handled above
                if data_source == "PowerScribe":
                    # PowerScribe: get from labelAccession
                    accession = elements.get("labelAccession", {}).get("text", "").strip()
                # For Mosaic, accession is already set above
                
                # If we were in multi-accession mode but now we're not (PowerScribe only), record and reset
                if data_source == "PowerScribe" and self.multi_accession_mode and self.multi_accession_data:
                    # Record the multi-accession study before exiting
                    current_time = datetime.now()
                    self._record_multi_accession_study(current_time)
                
                # Reset multi-accession tracking (PowerScribe only)
                if data_source == "PowerScribe" and self.multi_accession_mode:
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
            return
        
            # Handle regular single-accession studies
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
        
            # Skip normal study tracking when viewing a multi-accession study (PowerScribe only)
            # For Mosaic, we track each accession separately, so we need to check completion
            # Also skip if we're viewing a multi-accession that we're ignoring as duplicate (PowerScribe only)
            if (self.multi_accession_mode or is_multi_accession_view) and data_source != "Mosaic":
                return
            
            # Check for completed studies FIRST (before checking if we should ignore)
            # This handles studies that have disappeared
            # For Mosaic multi-accession, we need to check all accessions, not just the current one
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
                            # This accession is no longer visible - mark as completed
                            time_since_last_seen = (current_time - study["last_seen"]).total_seconds()
                            if time_since_last_seen > 1.0:
                                duration = (study["last_seen"] - study["start_time"]).total_seconds()
                                if duration >= self.tracker.min_seconds:
                                    completed_study = study.copy()
                                    completed_study["end_time"] = study["last_seen"]
                                    completed_study["duration"] = duration
                                    completed.append(completed_study)
                                    logger.info(f"Completed Mosaic study: {acc} - {study['study_type']} ({duration:.1f}s)")
                                    # Remove from active studies
                                    del self.tracker.active_studies[acc]
                elif not accession:
                    # Mosaic but no multiple_accessions and no accession - all active Mosaic studies should be completed
                    completed = []
                    for acc, study in list(self.tracker.active_studies.items()):
                        # Only check Mosaic studies (patient_class == "Unknown")
                        if study.get('patient_class') == 'Unknown':
                            time_since_last_seen = (current_time - study["last_seen"]).total_seconds()
                            if time_since_last_seen > 1.0:
                                duration = (study["last_seen"] - study["start_time"]).total_seconds()
                                if duration >= self.tracker.min_seconds:
                                    completed_study = study.copy()
                                    completed_study["end_time"] = study["last_seen"]
                                    completed_study["duration"] = duration
                                    completed.append(completed_study)
                                    logger.info(f"Completed Mosaic study (no accessions visible): {acc} - {study['study_type']} ({duration:.1f}s)")
                                    # Remove from active studies
                                    del self.tracker.active_studies[acc]
                else:
                    # Single Mosaic accession - use normal completion check
                    completed = self.tracker.check_completed(current_time, accession)
            else:
                # Normal completion check (PowerScribe or single Mosaic accession)
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
                # No current study - check if we have active Mosaic studies that should be completed
                if data_source == "Mosaic" and self.tracker.active_studies:
                    # All active Mosaic studies should be completed since no study is visible
                    current_time_check = datetime.now()
                    for acc, study in list(self.tracker.active_studies.items()):
                        # Only complete Mosaic studies (patient_class == "Unknown")
                        if study.get('patient_class') == 'Unknown':
                            time_since_last_seen = (current_time_check - study["last_seen"]).total_seconds()
                            if time_since_last_seen > 1.0:
                                duration = (study["last_seen"] - study["start_time"]).total_seconds()
                                if duration >= self.tracker.min_seconds:
                                    completed_study = study.copy()
                                    completed_study["end_time"] = study["last_seen"]
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
                                    logger.info(f"Recorded completed Mosaic study (no study visible): {completed_study['accession']} - {completed_study['study_type']} ({completed_study['rvu']} RVU) - Duration: {duration:.1f}s")
                                    del self.tracker.active_studies[acc]
                    if self.tracker.active_studies:
                        self.update_display()
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
            # For Mosaic multi-accession, we handle last_seen updates above, so skip here
            if accession in self.tracker.active_studies:
                if data_source != "Mosaic" or not multiple_accessions:
                    # Normal update for PowerScribe or single Mosaic accession
                    self.tracker.add_study(accession, procedure, current_time, rvu_table, classification_rules, direct_lookups, self.current_patient_class)
                # For Mosaic multi-accession, last_seen is updated above, so just return
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
            # Stop current shift - archive it immediately
            self.is_running = False
            self.data_manager.data["current_shift"]["shift_end"] = datetime.now().isoformat()
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
            # End previous shift if it exists
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
        if self.is_running and self.shift_start:
            self.recent_frame.config(text="Recent Studies")
        else:
            self.recent_frame.config(text="Temporary Recent - No shift started", style="Red.TLabelframe")
    
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
            # Clear existing widgets
            for widget in self.study_widgets:
                widget.destroy()
            self.study_widgets.clear()
            # Clear time labels if they exist
            if hasattr(self, 'time_labels'):
                self.time_labels.clear()
            
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
                delete_btn.bind("<Button-1>", lambda e, btn=delete_btn: self.delete_study_by_index(btn.actual_index))
                delete_btn.bind("<Enter>", lambda e, btn=delete_btn: btn.config(bg=colors["button_active_bg"]))
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
                    bg_color = self.theme_colors.get("bg_color", "#f0f0f0")
                    time_row_frame = tk.Frame(study_frame, bg=bg_color, height=12)
                    time_row_frame.pack(fill=tk.X, pady=(0, 0), padx=0)
                    time_row_frame.pack_propagate(False)  # Prevent frame from expanding
                    
                    # Add a small spacer on the left to align with procedure text (accounting for X button width)
                    spacer_label = tk.Label(time_row_frame, text="", width=2, bg=bg_color, height=1)  # Approximate width of X button + padding
                    spacer_label.pack(side=tk.LEFT, pady=0, padx=0)
                    
                    # Time ago label - left-justified, smaller font, lighter color, no padding
                    # Use tk.Label instead of ttk.Label for less padding
                    time_ago_text = self._format_time_ago(record.get("time_finished"))
                    time_ago_label = tk.Label(
                        time_row_frame, 
                        text=time_ago_text, 
                        font=("Consolas", 7),
                        fg="gray",
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
                        fg="gray",
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
        
        if is_na or not self.current_procedure:
            self.debug_accession_label.config(text="")
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
                self.debug_accession_label.config(text=f"Accession: {acc_display}")
                
                # Check if this is Mosaic multi-accession (no multi_accession_mode, but has multiple)
                data_source = self.data_manager.data["settings"].get("data_source", "PowerScribe")
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
                        self.debug_procedure_label.config(text=f"Procedure: Multiple {modality} (done)", foreground="gray")
            else:
                self.debug_accession_label.config(text=f"Accession: {self.current_accession if self.current_accession else '-'}")
                # No truncation for procedure - show full name
                procedure_display = self.current_procedure if self.current_procedure else '-'
                self.debug_procedure_label.config(text=f"Procedure: {procedure_display}", foreground="gray")
            
            self.debug_patient_class_label.config(text=f"Patient Class: {self.current_patient_class if self.current_patient_class else '-'}")
            
            # Display study type with RVU on the right (separate labels for alignment)
            if self.current_study_type:
                # Dynamic truncation - same logic as Recent Studies
                frame_width = self.root.winfo_width()
                max_chars = self._calculate_max_chars(frame_width)
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
            border_color = "#555555"
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
            border_color = "#acacac"
        
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
            "border_color": border_color,
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
            self.data_manager.save()
        except Exception as e:
            logger.error(f"Error saving window position: {e}")
    
    def _update_time_display(self):
        """Update time display for recent studies every 5 seconds."""
        if hasattr(self, 'time_labels') and self.time_labels:
            show_time = self.data_manager.data["settings"].get("show_time", False)
            if show_time:
                for label_info in self.time_labels:
                    try:
                        record = label_info['record']
                        time_ago_label = label_info['time_ago_label']
                        
                        # Update time ago
                        time_ago_text = self._format_time_ago(record.get("time_finished"))
                        time_ago_label.config(text=time_ago_text)
                    except Exception as e:
                        logger.error(f"Error updating time display: {e}")
        
        # Schedule next update in 5 seconds
        self.root.after(5000, self._update_time_display)
    
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
            self.window.geometry(f"450x620+{window_pos['x']}+{window_pos['y']}")
        else:
            self.window.geometry("450x620")
        
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
        
        # Auto-start
        self.auto_start_var = tk.BooleanVar(value=settings["auto_start"])
        ttk.Checkbutton(main_frame, text="Auto-resume shift on launch", variable=self.auto_start_var).pack(anchor=tk.W, pady=2)
        
        # Dark mode
        self.dark_mode_var = tk.BooleanVar(value=settings.get("dark_mode", False))
        ttk.Checkbutton(main_frame, text="Dark Mode", variable=self.dark_mode_var).pack(anchor=tk.W, pady=2)
        
        # Show time checkbox
        self.show_time_var = tk.BooleanVar(value=settings.get("show_time", False))
        ttk.Checkbutton(main_frame, text="Show time", variable=self.show_time_var).pack(anchor=tk.W, pady=2)
        
        # Data source radio buttons (PowerScribe or Mosaic)
        data_source_frame = ttk.Frame(main_frame)
        data_source_frame.pack(anchor=tk.W, pady=(10, 5))
        
        ttk.Label(data_source_frame, text="Data Source:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        
        self.data_source_var = tk.StringVar(value=settings.get("data_source", "PowerScribe"))
        ttk.Radiobutton(data_source_frame, text="PowerScribe", variable=self.data_source_var, value="PowerScribe").pack(side=tk.LEFT)
        ttk.Radiobutton(data_source_frame, text="Mosaic", variable=self.data_source_var, value="Mosaic").pack(side=tk.LEFT, padx=(10, 0))
        
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
        
        # Save/Cancel
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
            
            # Update tracker min_seconds
            self.app.tracker.min_seconds = self.data_manager.data["settings"]["min_study_seconds"]
            
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
        self.window.geometry("1200x700")
        self.window.minsize(800, 500)
        
        # Restore saved position or center on screen
        positions = self.data_manager.data.get("window_positions", {})
        if "statistics" in positions:
            pos = positions["statistics"]
            self.window.geometry(f"1200x700+{pos['x']}+{pos['y']}")
        else:
            # Center on screen
            parent.update_idletasks()
            screen_width = parent.winfo_screenwidth()
            screen_height = parent.winfo_screenheight()
            x = (screen_width - 1200) // 2
            y = (screen_height - 700) // 2
            self.window.geometry(f"1200x700+{x}+{y}")
        
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
        self.view_mode = tk.StringVar(value="by_hour")
        self.selected_shift_index = None  # For shift list selection
        
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
        
        # Historical Section
        history_frame = ttk.LabelFrame(left_panel, text="Historical", padding="8")
        history_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Radiobutton(history_frame, text="Last Work Week", variable=self.selected_period,
                       value="last_work_week", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last 2 Work Weeks", variable=self.selected_period,
                       value="last_2_weeks", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last Month", variable=self.selected_period,
                       value="last_month", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last 3 Months", variable=self.selected_period,
                       value="last_3_months", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last Year", variable=self.selected_period,
                       value="last_year", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        
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
        
        # View mode toggle
        view_frame = ttk.Frame(right_panel)
        view_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(view_frame, text="View By:", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(0, 10))
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
        
        # Period label
        self.period_label = ttk.Label(right_panel, text="", font=("Arial", 12, "bold"))
        self.period_label.pack(anchor=tk.W, pady=(0, 10))
        
        # Data table frame
        table_frame = ttk.Frame(right_panel)
        table_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create Treeview for data display
        self.tree = ttk.Treeview(table_frame, show="headings")
        tree_scrollbar_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        tree_scrollbar_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scrollbar_y.set, xscrollcommand=tree_scrollbar_x.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Summary frame at bottom
        summary_frame = ttk.LabelFrame(right_panel, text="Summary", padding="10")
        summary_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.summary_label = ttk.Label(summary_frame, text="", font=("Arial", 10))
        self.summary_label.pack(anchor=tk.W)
        
        # Bottom button row
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(button_frame, text="Refresh", command=self.refresh_data, width=12).pack(side=tk.LEFT, padx=2)
        ttk.Button(button_frame, text="Close", command=self.on_closing, width=12).pack(side=tk.RIGHT, padx=2)
        
        # Initial data load
        self.populate_shifts_list()
        self.refresh_data()
    
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
        
        for i, shift in enumerate(shifts):
            shift_frame = ttk.Frame(self.shifts_list_frame)
            shift_frame.pack(fill=tk.X, pady=1)
            
            # Format shift label
            if shift.get("is_current"):
                label_text = "Current Shift"
            else:
                try:
                    start = datetime.fromisoformat(shift.get("shift_start", ""))
                    label_text = start.strftime("%m/%d %I:%M%p")
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
                del_btn = tk.Button(shift_frame, text="×", font=("Arial", 8), 
                                   fg=colors["delete_btn_fg"], bg=colors["delete_btn_bg"],
                                   activeforeground=colors["fg"], activebackground=colors["button_bg"],
                                   relief=tk.FLAT, width=2, height=1,
                                   command=lambda idx=i: self.confirm_delete_shift(idx))
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
        
        # Find and remove from the shifts array
        historical_shifts = self.data_manager.data.get("shifts", [])
        for i, s in enumerate(historical_shifts):
            if s.get("shift_start") == shift_start:
                historical_shifts.pop(i)
                self.data_manager.save()
                logger.info(f"Deleted shift starting {shift_start}")
                break
        
        # Refresh UI
        self.populate_shifts_list()
        self.refresh_data()
    
    def get_records_for_period(self) -> Tuple[List[dict], str]:
        """Get records for the selected period. Returns (records, period_description)."""
        period = self.selected_period.get()
        now = datetime.now()
        
        if period == "current_shift":
            records = self.data_manager.data.get("current_shift", {}).get("records", [])
            return records, "Current Shift"
        
        elif period == "prior_shift":
            shifts = self.get_all_shifts()
            # Find the first non-current shift
            for shift in shifts:
                if not shift.get("is_current"):
                    return shift.get("records", []), f"Prior Shift ({shift.get('date', '')})"
            return [], "Prior Shift (none found)"
        
        elif period == "specific_shift":
            shifts = self.get_all_shifts()
            if self.selected_shift_index is not None and self.selected_shift_index < len(shifts):
                shift = shifts[self.selected_shift_index]
                if shift.get("is_current"):
                    return shift.get("records", []), "Current Shift"
                try:
                    start = datetime.fromisoformat(shift.get("shift_start", ""))
                    desc = start.strftime("%B %d, %Y %I:%M %p")
                except:
                    desc = shift.get("date", "")
                return shift.get("records", []), f"Shift: {desc}"
            return [], "No shift selected"
        
        elif period == "last_work_week":
            # Work week: Monday night to next Monday morning (7 on, 7 off)
            # Find the most recent Monday that started a work week
            records = self._get_records_in_range(now - timedelta(days=14), now)
            return records, "Last Work Week"
        
        elif period == "last_2_weeks":
            records = self._get_records_in_range(now - timedelta(days=28), now)
            return records, "Last 2 Work Weeks"
        
        elif period == "last_month":
            records = self._get_records_in_range(now - timedelta(days=30), now)
            return records, "Last Month"
        
        elif period == "last_3_months":
            records = self._get_records_in_range(now - timedelta(days=90), now)
            return records, "Last 3 Months"
        
        elif period == "last_year":
            records = self._get_records_in_range(now - timedelta(days=365), now)
            return records, "Last Year"
        
        return [], "Unknown period"
    
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
    
    def refresh_data(self):
        """Refresh the data display based on current selections."""
        records, period_desc = self.get_records_for_period()
        self.period_label.config(text=period_desc)
        
        view_mode = self.view_mode.get()
        
        # Clear existing tree
        for item in self.tree.get_children():
            self.tree.delete(item)
        
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
        else:  # summary
            self._display_summary(records)
        
        # Update summary
        total_studies = len(records)
        total_rvu = sum(r.get("rvu", 0) for r in records)
        avg_rvu = total_rvu / total_studies if total_studies > 0 else 0
        
        self.summary_label.config(
            text=f"Total: {total_studies} studies  |  {total_rvu:.1f} RVU  |  Avg: {avg_rvu:.2f} RVU/study"
        )
    
    def _display_by_hour(self, records: List[dict]):
        """Display data broken down by hour."""
        # Configure columns
        self.tree["columns"] = ("hour", "studies", "rvu", "avg_rvu", "top_modality")
        self.tree.heading("hour", text="Hour", command=lambda: self._sort_column("hour"))
        self.tree.heading("studies", text="Studies", command=lambda: self._sort_column("studies"))
        self.tree.heading("rvu", text="RVU", command=lambda: self._sort_column("rvu"))
        self.tree.heading("avg_rvu", text="Avg/Study", command=lambda: self._sort_column("avg_rvu"))
        self.tree.heading("top_modality", text="Top Modality", command=lambda: self._sort_column("top_modality"))
        
        self.tree.column("hour", width=120, anchor=tk.CENTER)
        self.tree.column("studies", width=80, anchor=tk.CENTER)
        self.tree.column("rvu", width=80, anchor=tk.CENTER)
        self.tree.column("avg_rvu", width=80, anchor=tk.CENTER)
        self.tree.column("top_modality", width=120, anchor=tk.CENTER)
        
        # Group by hour
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
        
        # Calculate totals
        total_studies = sum(d["studies"] for d in hour_data.values())
        total_rvu = sum(d["rvu"] for d in hour_data.values())
        
        # Sort by hour and display
        for hour in sorted(hour_data.keys()):
            data = hour_data[hour]
            # Format hour
            hour_12 = hour % 12 or 12
            am_pm = "AM" if hour < 12 else "PM"
            next_hour = (hour + 1) % 24
            next_12 = next_hour % 12 or 12
            next_am_pm = "AM" if next_hour < 12 else "PM"
            hour_str = f"{hour_12}{am_pm}-{next_12}{next_am_pm}"
            
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            
            # Top modality
            modalities = data["modalities"]
            top_mod = max(modalities.keys(), key=lambda k: modalities[k]) if modalities else "N/A"
            
            self.tree.insert("", tk.END, values=(
                hour_str,
                data["studies"],
                f"{data['rvu']:.1f}",
                f"{avg_rvu:.2f}",
                top_mod
            ))
        
        # Add totals row
        if hour_data:
            self.tree.insert("", tk.END, values=("─" * 10, "─" * 6, "─" * 6, "─" * 6, "─" * 10))
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            top_overall = max(all_modalities.keys(), key=lambda k: all_modalities[k]) if all_modalities else "N/A"
            self.tree.insert("", tk.END, values=(
                "TOTAL",
                total_studies,
                f"{total_rvu:.1f}",
                f"{total_avg:.2f}",
                top_overall
            ))
    
    def _display_by_modality(self, records: List[dict]):
        """Display data broken down by modality."""
        # Configure columns
        self.tree["columns"] = ("modality", "studies", "rvu", "avg_rvu", "pct_studies", "pct_rvu")
        self.tree.heading("modality", text="Modality", command=lambda: self._sort_column("modality"))
        self.tree.heading("studies", text="Studies", command=lambda: self._sort_column("studies"))
        self.tree.heading("rvu", text="RVU", command=lambda: self._sort_column("rvu"))
        self.tree.heading("avg_rvu", text="Avg/Study", command=lambda: self._sort_column("avg_rvu"))
        self.tree.heading("pct_studies", text="% Studies", command=lambda: self._sort_column("pct_studies"))
        self.tree.heading("pct_rvu", text="% RVU", command=lambda: self._sort_column("pct_rvu"))
        
        self.tree.column("modality", width=100, anchor=tk.CENTER)
        self.tree.column("studies", width=80, anchor=tk.CENTER)
        self.tree.column("rvu", width=80, anchor=tk.CENTER)
        self.tree.column("avg_rvu", width=80, anchor=tk.CENTER)
        self.tree.column("pct_studies", width=80, anchor=tk.CENTER)
        self.tree.column("pct_rvu", width=80, anchor=tk.CENTER)
        
        # Group by modality
        modality_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            study_type = record.get("study_type", "Unknown")
            modality = study_type.split()[0] if study_type else "Unknown"
            rvu = record.get("rvu", 0)
            
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
            
            self.tree.insert("", tk.END, values=(
                modality,
                data["studies"],
                f"{data['rvu']:.1f}",
                f"{avg_rvu:.2f}",
                f"{pct_studies:.1f}%",
                f"{pct_rvu:.1f}%"
            ))
        
        # Add totals row
        if modality_data:
            self.tree.insert("", tk.END, values=("─" * 8, "─" * 6, "─" * 6, "─" * 6, "─" * 6, "─" * 6))
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self.tree.insert("", tk.END, values=(
                "TOTAL",
                total_studies,
                f"{total_rvu:.1f}",
                f"{total_avg:.2f}",
                "100%",
                "100%"
            ))
    
    def _display_by_patient_class(self, records: List[dict]):
        """Display data broken down by patient class."""
        # Configure columns
        self.tree["columns"] = ("patient_class", "studies", "rvu", "avg_rvu", "pct_studies", "pct_rvu")
        self.tree.heading("patient_class", text="Patient Class", command=lambda: self._sort_column("patient_class"))
        self.tree.heading("studies", text="Studies", command=lambda: self._sort_column("studies"))
        self.tree.heading("rvu", text="RVU", command=lambda: self._sort_column("rvu"))
        self.tree.heading("avg_rvu", text="Avg/Study", command=lambda: self._sort_column("avg_rvu"))
        self.tree.heading("pct_studies", text="% Studies", command=lambda: self._sort_column("pct_studies"))
        self.tree.heading("pct_rvu", text="% RVU", command=lambda: self._sort_column("pct_rvu"))
        
        self.tree.column("patient_class", width=120, anchor=tk.CENTER)
        self.tree.column("studies", width=80, anchor=tk.CENTER)
        self.tree.column("rvu", width=80, anchor=tk.CENTER)
        self.tree.column("avg_rvu", width=80, anchor=tk.CENTER)
        self.tree.column("pct_studies", width=80, anchor=tk.CENTER)
        self.tree.column("pct_rvu", width=80, anchor=tk.CENTER)
        
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
            
            self.tree.insert("", tk.END, values=(
                patient_class,
                data["studies"],
                f"{data['rvu']:.1f}",
                f"{avg_rvu:.2f}",
                f"{pct_studies:.1f}%",
                f"{pct_rvu:.1f}%"
            ))
        
        # Add totals row
        if class_data:
            self.tree.insert("", tk.END, values=("─" * 10, "─" * 6, "─" * 6, "─" * 6, "─" * 6, "─" * 6))
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self.tree.insert("", tk.END, values=(
                "TOTAL",
                total_studies,
                f"{total_rvu:.1f}",
                f"{total_avg:.2f}",
                "100%",
                "100%"
            ))
    
    def _display_by_study_type(self, records: List[dict]):
        """Display data broken down by study type."""
        # Configure columns
        self.tree["columns"] = ("study_type", "studies", "rvu", "avg_rvu", "pct_studies", "pct_rvu")
        self.tree.heading("study_type", text="Study Type", command=lambda: self._sort_column("study_type"))
        self.tree.heading("studies", text="Studies", command=lambda: self._sort_column("studies"))
        self.tree.heading("rvu", text="RVU", command=lambda: self._sort_column("rvu"))
        self.tree.heading("avg_rvu", text="Avg/Study", command=lambda: self._sort_column("avg_rvu"))
        self.tree.heading("pct_studies", text="% Studies", command=lambda: self._sort_column("pct_studies"))
        self.tree.heading("pct_rvu", text="% RVU", command=lambda: self._sort_column("pct_rvu"))
        
        self.tree.column("study_type", width=150, anchor=tk.CENTER)
        self.tree.column("studies", width=80, anchor=tk.CENTER)
        self.tree.column("rvu", width=80, anchor=tk.CENTER)
        self.tree.column("avg_rvu", width=80, anchor=tk.CENTER)
        self.tree.column("pct_studies", width=80, anchor=tk.CENTER)
        self.tree.column("pct_rvu", width=80, anchor=tk.CENTER)
        
        # Group by study type
        type_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            # Handle missing study_type (historical data may not have it)
            study_type = record.get("study_type", "").strip()
            if not study_type:
                study_type = "(Unknown)"
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
            
            self.tree.insert("", tk.END, values=(
                study_type,
                data["studies"],
                f"{data['rvu']:.1f}",
                f"{avg_rvu:.2f}",
                f"{pct_studies:.1f}%",
                f"{pct_rvu:.1f}%"
            ))
        
        # Add totals row
        if type_data:
            self.tree.insert("", tk.END, values=("─" * 12, "─" * 6, "─" * 6, "─" * 6, "─" * 6, "─" * 6))
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self.tree.insert("", tk.END, values=(
                "TOTAL",
                total_studies,
                f"{total_rvu:.1f}",
                f"{total_avg:.2f}",
                "100%",
                "100%"
            ))
    
    def _display_all_studies(self, records: List[dict]):
        """Display all individual studies with Procedure, Study Type, RVU columns."""
        # Configure columns
        self.tree["columns"] = ("procedure", "study_type", "rvu")
        self.tree.heading("procedure", text="Procedure", command=lambda: self._sort_column("procedure"))
        self.tree.heading("study_type", text="Study Type", command=lambda: self._sort_column("study_type"))
        self.tree.heading("rvu", text="RVU", command=lambda: self._sort_column("rvu"))
        
        self.tree.column("procedure", width=400, anchor=tk.W)
        self.tree.column("study_type", width=200, anchor=tk.CENTER)
        self.tree.column("rvu", width=100, anchor=tk.CENTER)
        
        # Display all studies
        for record in records:
            procedure = record.get("procedure", "Unknown")
            study_type = record.get("study_type", "Unknown")
            rvu = record.get("rvu", 0.0)
            
            self.tree.insert("", tk.END, values=(
                procedure,
                study_type,
                f"{rvu:.1f}"
            ))
    
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
    
    def _display_summary(self, records: List[dict]):
        """Display summary statistics."""
        # Configure columns
        self.tree["columns"] = ("metric", "value")
        self.tree.heading("metric", text="Metric")
        self.tree.heading("value", text="Value")
        
        self.tree.column("metric", width=250, anchor=tk.CENTER)
        self.tree.column("value", width=150, anchor=tk.CENTER)
        
        total_studies = len(records)
        total_rvu = sum(r.get("rvu", 0) for r in records)
        avg_rvu = total_rvu / total_studies if total_studies > 0 else 0
        
        # Calculate time span - sum of actual shift durations, not time from first to last record
        hours = 0.0
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
                shifts_with_records = {}
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
        
        # Modality breakdown
        modalities = {}
        for r in records:
            st = r.get("study_type", "Unknown")
            mod = st.split()[0] if st else "Unknown"
            modalities[mod] = modalities.get(mod, 0) + 1
        
        top_modality = max(modalities.keys(), key=lambda k: modalities[k]) if modalities else "N/A"
        
        # Insert summary rows
        self.tree.insert("", tk.END, values=("Total Studies", str(total_studies)))
        self.tree.insert("", tk.END, values=("Total RVU", f"{total_rvu:.1f}"))
        self.tree.insert("", tk.END, values=("Average RVU per Study", f"{avg_rvu:.2f}"))
        self.tree.insert("", tk.END, values=("", ""))  # Spacer
        self.tree.insert("", tk.END, values=("Time Span", f"{hours:.1f} hours"))
        self.tree.insert("", tk.END, values=("Studies per Hour", f"{studies_per_hour:.1f}"))
        self.tree.insert("", tk.END, values=("RVU per Hour", f"{rvu_per_hour:.1f}"))
        self.tree.insert("", tk.END, values=("", ""))  # Spacer
        self.tree.insert("", tk.END, values=("Top Modality", f"{top_modality} ({modalities.get(top_modality, 0)} studies)"))
        self.tree.insert("", tk.END, values=("Unique Modalities", str(len(modalities))))
    
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
            self.data_manager.save()
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
