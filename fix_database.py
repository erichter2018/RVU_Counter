#!/usr/bin/env python3
"""
Fix Database Script for RVU Counter

This script identifies and fixes issues in the database:
1. Finds duplicate accession numbers and allows deletion (keeping oldest)
   - Only considers records as duplicates if they have:
     * Same accession number
     * Same study_type
     * Are within 24 hours of each other (based on time_performed)
2. Identifies and fixes incorrectly matched RVU records by comparing stored 
   study_type and rvu values against current rvu_settings.yaml rules.

Usage:
    python fix_database.py

The script will:
1. Check for duplicate accession numbers (matching accession, study_type, and within 24h)
   and ask to delete them (keeping oldest)
2. Load rvu_settings.yaml from the current directory
3. Connect to rvu_records.db in the current directory
4. Identify records with mismatched study_type or rvu values
5. Display a summary and ask for confirmation before fixing
"""

import sqlite3
import json
import os
import sys
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple, Dict, List, Optional

# Try to import the refactored match_study_type function
try:
    # Add src directory to path if running from project root
    if Path(__file__).parent.exists():
        sys.path.insert(0, str(Path(__file__).parent))
    from src.logic.study_matcher import match_study_type as refactored_match_study_type
    USE_REFACTORED = True
except ImportError:
    # Fallback to local copy if import fails
    USE_REFACTORED = False
    print("WARNING: Could not import refactored match_study_type, using local copy")


def load_rvu_settings(settings_path: Path) -> Dict:
    """Load RVU settings from YAML file.
    
    When running as frozen executable, checks sys._MEIPASS first for bundled file,
    then falls back to the settings_path provided.
    """
    # Handle PyInstaller bundled files
    if getattr(sys, 'frozen', False):
        # Running as compiled executable - check bundled location first
        bundled_path = Path(sys._MEIPASS) / "rvu_settings.yaml"
        if bundled_path.exists():
            try:
                with open(bundled_path, 'r', encoding='utf-8') as f:
                    settings = yaml.safe_load(f)
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
            print(f"  Checked bundled location: {Path(sys._MEIPASS) / 'rvu_settings.yaml'}")
            print(f"  sys._MEIPASS: {sys._MEIPASS}")
        sys.exit(1)
    
    try:
        with open(settings_path, 'r', encoding='utf-8') as f:
            settings = yaml.safe_load(f)
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
    
    # Recalculate using current rules - use refactored version if available
    # This should purely follow the rvu_settings rules without any special handling
    if USE_REFACTORED:
        new_study_type, new_rvu = refactored_match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
    else:
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


def find_duplicate_accessions(db_path: Path) -> Dict[str, List[Dict]]:
    """Find duplicate accession numbers in the database.
    
    Only considers records as duplicates if they:
    1. Have the same accession number
    2. Have the same study_type
    3. Are within 24 hours of each other (based on time_performed)
    
    Returns:
        Dictionary mapping "(accession, study_type)" keys to lists of record dictionaries.
        Only includes groups that have duplicates within 24 hours.
    """
    if not db_path.exists():
        print(f"ERROR: {db_path} not found!")
        sys.exit(1)
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all records with accession numbers and timestamps
    cursor.execute('''
        SELECT id, accession, procedure, study_type, rvu, time_performed
        FROM records
        WHERE accession IS NOT NULL AND accession != ''
          AND study_type IS NOT NULL AND study_type != ''
          AND time_performed IS NOT NULL AND time_performed != ''
        ORDER BY id
    ''')
    
    records = cursor.fetchall()
    
    # Group by (accession, study_type) pairs
    group_key_to_records = {}
    for record in records:
        accession = record['accession']
        study_type = record['study_type']
        group_key = (accession, study_type)
        
        if group_key not in group_key_to_records:
            group_key_to_records[group_key] = []
        
        # Parse time_performed
        time_performed_str = record['time_performed']
        try:
            # Parse ISO format: "2025-11-29T18:06:31.846498"
            # Handle timezone indicators if present
            if time_performed_str.endswith('Z'):
                time_performed_str = time_performed_str[:-1] + '+00:00'
            time_performed = datetime.fromisoformat(time_performed_str)
        except (ValueError, AttributeError, TypeError):
            # Skip records with invalid timestamps
            continue
        
        group_key_to_records[group_key].append({
            'id': record['id'],
            'accession': record['accession'],
            'procedure': record['procedure'],
            'study_type': record['study_type'],
            'rvu': record['rvu'],
            'time_performed': time_performed,
            'time_performed_str': time_performed_str
        })
    
    # Filter to groups with multiple records and check 24-hour window
    duplicates = {}
    for group_key, records_list in group_key_to_records.items():
        if len(records_list) < 2:
            continue
        
        # Sort by time_performed
        records_list.sort(key=lambda r: r['time_performed'])
        
        # Check if any records are within 24 hours of each other
        # Group records into clusters where each cluster contains records within 24 hours
        clusters = []
        current_cluster = [records_list[0]]
        
        for i in range(1, len(records_list)):
            prev_time = current_cluster[-1]['time_performed']
            curr_time = records_list[i]['time_performed']
            time_diff = curr_time - prev_time
            
            if time_diff <= timedelta(hours=24):
                # Within 24 hours, add to current cluster
                current_cluster.append(records_list[i])
            else:
                # More than 24 hours apart, start new cluster
                if len(current_cluster) > 1:
                    clusters.append(current_cluster)
                current_cluster = [records_list[i]]
        
        # Don't forget the last cluster
        if len(current_cluster) > 1:
            clusters.append(current_cluster)
        
        # If we have any clusters with duplicates, add them
        for cluster in clusters:
            if len(cluster) > 1:
                # Use a unique key for this cluster
                accession, study_type = group_key
                cluster_key = f"{accession} ({study_type})"
                # If key already exists, append a number
                if cluster_key in duplicates:
                    counter = 2
                    while f"{cluster_key} - Cluster {counter}" in duplicates:
                        counter += 1
                    cluster_key = f"{cluster_key} - Cluster {counter}"
                duplicates[cluster_key] = cluster
    
    conn.close()
    return duplicates


def print_duplicate_summary(duplicates: Dict[str, List[Dict]]) -> bool:
    """Print a summary of duplicate accession numbers.
    
    Returns:
        True if no duplicates found, False if duplicates found.
    """
    if not duplicates:
        print("\n✓ No duplicate accession numbers found!")
        print("   (Duplicates must have same accession, same study type, and be within 24 hours)")
        return True
    
    print(f"\n{'='*80}")
    print(f"FOUND {len(duplicates)} DUPLICATE ACCESSION GROUPS")
    print(f"{'='*80}\n")
    print("Note: Only records with same accession, same study type, and within 24 hours are considered duplicates.")
    print()
    
    total_duplicate_records = sum(len(records) for records in duplicates.values())
    total_to_delete = total_duplicate_records - len(duplicates)  # One record kept per group
    
    print(f"Summary:")
    print(f"  - Duplicate groups found: {len(duplicates)}")
    print(f"  - Total records with duplicates: {total_duplicate_records}")
    print(f"  - Records that would be deleted: {total_to_delete}")
    print(f"  - Records that would be kept: {len(duplicates)} (oldest for each group)")
    print()
    
    # Show first 20 examples
    print("Examples (showing first 20 duplicate groups):")
    print("-" * 80)
    
    shown = 0
    for group_key, records in sorted(duplicates.items()):
        if shown >= 20:
            break
        
        # Sort by time_performed (oldest first)
        records_sorted = sorted(records, key=lambda r: r['time_performed'])
        oldest = records_sorted[0]
        newer = records_sorted[1:]
        
        # Calculate time differences
        oldest_time = oldest['time_performed']
        
        print(f"\n{shown + 1}. {group_key}")
        print(f"   Total occurrences: {len(records)}")
        print(f"   KEEP (oldest):")
        print(f"      ID: {oldest['id']}, Time: {oldest['time_performed_str']}")
        print(f"      Procedure: {oldest['procedure'][:60] if oldest['procedure'] else 'N/A'}...")
        print(f"      Study Type: {oldest['study_type']}, RVU: {oldest['rvu']:.2f}")
        print(f"   DELETE (newer, within 24 hours):")
        for record in newer:
            time_diff = record['time_performed'] - oldest_time
            hours_diff = time_diff.total_seconds() / 3600
            print(f"      ID: {record['id']}, Time: {record['time_performed_str']} ({hours_diff:.1f} hours later)")
            print(f"      Procedure: {record['procedure'][:60] if record['procedure'] else 'N/A'}...")
            print(f"      Study Type: {record['study_type']}, RVU: {record['rvu']:.2f}")
        
        shown += 1
    
    if len(duplicates) > 20:
        print(f"\n... and {len(duplicates) - 20} more duplicate groups")
    
    print(f"\n{'='*80}")
    return False


def delete_duplicate_accessions(db_path: Path, duplicates: Dict[str, List[Dict]]) -> int:
    """Delete duplicate accession records, keeping the oldest (by time_performed) for each group.
    
    Returns:
        Number of records deleted.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    deleted_count = 0
    
    for group_key, records in duplicates.items():
        # Records are already sorted by time_performed (oldest first) from find_duplicate_accessions
        # Keep the first (oldest) record, delete the rest
        oldest = records[0]
        newer_records = records[1:]
        
        # Delete newer records
        for record in newer_records:
            cursor.execute('DELETE FROM records WHERE id = ?', (record['id'],))
            deleted_count += 1
    
    conn.commit()
    conn.close()
    
    return deleted_count


def fix_database(db_path: Path, mismatches: List[Dict]) -> Tuple[int, float]:
    """Fix mismatched records in the database. 
    
    Returns:
        Tuple of (number of records updated, total RVU difference)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    updated_count = 0
    total_rvu_difference = 0.0
    
    for m in mismatches:
        # Calculate RVU difference for this record
        rvu_diff = m['new_rvu'] - m['stored_rvu']
        total_rvu_difference += rvu_diff
        
        cursor.execute('''
            UPDATE records
            SET study_type = ?, rvu = ?
            WHERE id = ?
        ''', (m['new_study_type'], m['new_rvu'], m['id']))
        updated_count += 1
    
    conn.commit()
    conn.close()
    
    return updated_count, total_rvu_difference


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
    settings_path = work_dir / "rvu_settings.yaml"
    db_path = work_dir / "rvu_records.db"
    
    # Check for duplicate accessions first
    print("\n" + "=" * 80)
    print("STEP 1: Checking for duplicate accession numbers...")
    print("(Same accession + same study type + within 24 hours)")
    print("=" * 80)
    
    duplicates = find_duplicate_accessions(db_path)
    no_duplicates = print_duplicate_summary(duplicates)
    
    if not no_duplicates:
        # Ask for confirmation to delete duplicates
        print("\n" + "=" * 80)
        response = input("\nDo you want to delete duplicate accessions? (Y/N): ").strip().upper()
        
        if response == 'Y':
            print("\nDeleting duplicate accessions (keeping oldest for each)...")
            deleted_count = delete_duplicate_accessions(db_path, duplicates)
            
            print(f"\n{'='*80}")
            print(f"DUPLICATE DELETION SUMMARY")
            print(f"{'='*80}")
            print(f"Records deleted: {deleted_count}")
            print(f"Accessions cleaned: {len(duplicates)}")
            print(f"{'='*80}")
            print(f"\n✓ Successfully deleted {deleted_count} duplicate records!")
        else:
            print("\nNo duplicate records deleted.")
    
    # Load settings
    settings = load_rvu_settings(settings_path)
    
    # Analyze database for mismatches
    print("\n" + "=" * 80)
    print("STEP 2: Checking for mismatched RVU records...")
    print("=" * 80)
    
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
        updated_count, total_rvu_difference = fix_database(db_path, mismatches)
        
        print(f"\n{'='*80}")
        print(f"SUMMARY OF CHANGES")
        print(f"{'='*80}")
        print(f"Records updated: {updated_count}")
        if total_rvu_difference > 0:
            print(f"Total RVU change: +{total_rvu_difference:.2f} RVU")
        elif total_rvu_difference < 0:
            print(f"Total RVU change: {total_rvu_difference:.2f} RVU")
        else:
            print(f"Total RVU change: 0.00 RVU (no net change)")
        print(f"{'='*80}")
        print(f"\n✓ Successfully updated {updated_count} records!")
        print(f"\nDatabase has been fixed. You may want to verify the changes.")
    else:
        print("\nNo changes made to the database.")
    
    print("\n" + "=" * 80)
    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()

