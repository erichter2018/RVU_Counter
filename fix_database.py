#!/usr/bin/env python3
"""
Fix Database Script for RVU Counter

This script identifies and fixes incorrectly matched RVU records in the database
by comparing stored study_type and rvu values against current rvu_settings.json rules.

Usage:
    python fix_database.py

The script will:
1. Load rvu_settings.json from the current directory
2. Connect to rvu_records.db in the current directory
3. Identify records with mismatched study_type or rvu values
4. Display a summary and ask for confirmation before fixing
"""

import sqlite3
import json
import os
import sys
from pathlib import Path
from typing import Tuple, Dict, List, Optional


def load_rvu_settings(settings_path: Path) -> Dict:
    """Load RVU settings from JSON file.
    
    When running as frozen executable, checks sys._MEIPASS first for bundled file,
    then falls back to the settings_path provided.
    """
    # Handle PyInstaller bundled files
    if getattr(sys, 'frozen', False):
        # Running as compiled executable - check bundled location first
        bundled_path = Path(sys._MEIPASS) / "rvu_settings.json"
        if bundled_path.exists():
            try:
                with open(bundled_path, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                print(f"Loaded bundled settings from {bundled_path}")
                return settings
            except Exception as e:
                print(f"WARNING: Failed to load bundled settings: {e}")
                print(f"  Falling back to: {settings_path}")
    
    # Check provided path (or fallback if bundled file not found)
    if not settings_path.exists():
        print(f"ERROR: {settings_path} not found!")
        if getattr(sys, 'frozen', False):
            print(f"  Running as frozen executable")
            print(f"  Checked bundled location: {Path(sys._MEIPASS) / 'rvu_settings.json'}")
            print(f"  sys._MEIPASS: {sys._MEIPASS}")
        sys.exit(1)
    
    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            settings = json.load(f)
        print(f"Loaded settings from {settings_path}")
        return settings
    except Exception as e:
        print(f"ERROR: Failed to load settings: {e}")
        sys.exit(1)


def match_study_type(procedure_text: str, rvu_table: dict = None, classification_rules: dict = None, direct_lookups: dict = None) -> Tuple[str, float]:
    """
    Match procedure text to RVU table entry using best match.
    
    This is a copy of the match_study_type function from RVUCounter.pyw.
    Must stay in sync with the main application for consistent matching.
    """
    if not procedure_text:
        return "Unknown", 0.0
    
    # Require rvu_table - it must be provided from loaded settings
    if rvu_table is None:
        print(f"WARNING: match_study_type called without rvu_table parameter")
        rvu_table = {}
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
                break  # Found a classification match, stop searching rules for this study_type
        
        # If we found a classification match, stop searching other study_types
        if classification_match_name:
            break
    
    # If classification rule matched, return it immediately
    if classification_match_name:
        return classification_match_name, classification_match_rvu
    
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
        return other_matches[0][1], other_matches[0][2]
    
    # Absolute last resort: PET CT (only if both "pet" and "ct" appear together)
    if pet_ct_match:
        return pet_ct_match
    
    return "Unknown", 0.0


def check_record(record: tuple, rvu_table: dict, classification_rules: dict, direct_lookups: dict) -> Optional[Dict]:
    """
    Check a single database record for mismatches.
    
    Returns a dictionary with mismatch info if found, None otherwise.
    """
    record_id, accession, procedure, stored_study_type, stored_rvu = record
    
    if not procedure:
        return None  # Skip records without procedures
    
    # Recalculate using current rules
    new_study_type, new_rvu = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
    
    # Check for mismatch (accounting for floating point precision)
    mismatch = False
    if stored_study_type != new_study_type:
        mismatch = True
    elif abs(stored_rvu - new_rvu) > 0.001:  # Allow small floating point differences
        mismatch = True
    
    if mismatch:
        return {
            'id': record_id,
            'accession': accession,
            'procedure': procedure,
            'stored_study_type': stored_study_type,
            'stored_rvu': stored_rvu,
            'new_study_type': new_study_type,
            'new_rvu': new_rvu
        }
    
    return None


def analyze_database(db_path: Path, settings: dict) -> List[Dict]:
    """Analyze database and return list of mismatched records."""
    if not db_path.exists():
        print(f"ERROR: {db_path} not found!")
        sys.exit(1)
    
    print(f"\nConnecting to database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all records
    cursor.execute('''
        SELECT id, accession, procedure, study_type, rvu
        FROM records
        WHERE procedure IS NOT NULL AND procedure != ''
        ORDER BY id
    ''')
    
    records = cursor.fetchall()
    print(f"Found {len(records)} records with procedures to check")
    
    # Load settings
    rvu_table = settings.get('rvu_table', {})
    classification_rules = settings.get('classification_rules', {})
    direct_lookups = settings.get('direct_lookups', {})
    
    # Check each record
    mismatches = []
    for record in records:
        mismatch = check_record(
            (record['id'], record['accession'], record['procedure'], 
             record['study_type'], record['rvu']),
            rvu_table, classification_rules, direct_lookups
        )
        if mismatch:
            mismatches.append(mismatch)
    
    conn.close()
    return mismatches


def print_summary(mismatches: List[Dict]) -> bool:
    """Print a summary of mismatched records.
    
    Returns:
        True if no mismatches found, False if mismatches found.
    """
    if not mismatches:
        print("\n✓ No mismatches found! All records match current rules.")
        return True  # Return True to indicate no mismatches
    
    print(f"\n{'='*80}")
    print(f"FOUND {len(mismatches)} RECORDS WITH MISMATCHES")
    print(f"{'='*80}\n")
    
    # Group by type of mismatch
    study_type_mismatches = []
    rvu_mismatches = []
    both_mismatches = []
    
    for m in mismatches:
        if m['stored_study_type'] != m['new_study_type'] and abs(m['stored_rvu'] - m['new_rvu']) > 0.001:
            both_mismatches.append(m)
        elif m['stored_study_type'] != m['new_study_type']:
            study_type_mismatches.append(m)
        else:
            rvu_mismatches.append(m)
    
    print(f"Summary:")
    print(f"  - Study type AND RVU mismatches: {len(both_mismatches)}")
    print(f"  - Study type only mismatches: {len(study_type_mismatches)}")
    print(f"  - RVU only mismatches: {len(rvu_mismatches)}")
    print()
    
    # Show first 20 examples
    print("Examples (showing first 20):")
    print("-" * 80)
    for i, m in enumerate(mismatches[:20], 1):
        print(f"\n{i}. Record ID: {m['id']}")
        print(f"   Accession: {m['accession']}")
        print(f"   Procedure: {m['procedure'][:70]}...")
        print(f"   Stored:    {m['stored_study_type']} ({m['stored_rvu']:.2f} RVU)")
        print(f"   New:       {m['new_study_type']} ({m['new_rvu']:.2f} RVU)")
    
    if len(mismatches) > 20:
        print(f"\n... and {len(mismatches) - 20} more mismatches")
    
    print(f"\n{'='*80}")
    return False  # Return False to indicate mismatches found


def fix_database(db_path: Path, mismatches: List[Dict]) -> int:
    """Fix mismatched records in the database. Returns number of records updated."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    updated_count = 0
    for m in mismatches:
        cursor.execute('''
            UPDATE records
            SET study_type = ?, rvu = ?
            WHERE id = ?
        ''', (m['new_study_type'], m['new_rvu'], m['id']))
        updated_count += 1
    
    conn.commit()
    conn.close()
    
    return updated_count


def main():
    """Main function."""
    print("=" * 80)
    print("RVU Counter Database Fix Script")
    print("=" * 80)
    
    # Determine working directory
    if getattr(sys, 'frozen', False):
        # Running as executable
        work_dir = Path(sys.executable).parent
    else:
        # Running as script
        work_dir = Path(__file__).parent.absolute()
    
    print(f"\nWorking directory: {work_dir}")
    
    # Find required files
    settings_path = work_dir / "rvu_settings.json"
    db_path = work_dir / "rvu_records.db"
    
    # Load settings
    settings = load_rvu_settings(settings_path)
    
    # Analyze database
    mismatches = analyze_database(db_path, settings)
    
    # Print summary
    no_mismatches = print_summary(mismatches)
    
    if no_mismatches:
        input("\nPress Enter to exit...")
        return
    
    # Ask for confirmation
    print("\n" + "=" * 80)
    response = input("\nDo you want to fix these mismatches? (Y/N): ").strip().upper()
    
    if response == 'Y':
        print("\nFixing database...")
        updated_count = fix_database(db_path, mismatches)
        print(f"\n✓ Successfully updated {updated_count} records!")
        print(f"\nDatabase has been fixed. You may want to verify the changes.")
    else:
        print("\nNo changes made to the database.")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()

