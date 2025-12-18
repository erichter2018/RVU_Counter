"""Study type matching logic - maps procedure text to study types and RVU values."""

import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def match_study_type(procedure_text: str, rvu_table: dict = None, classification_rules: dict = None, direct_lookups: dict = None) -> Tuple[str, float]:
    """Match procedure text to RVU table entry using best match.
    
    Args:
        procedure_text: The procedure text to match
        rvu_table: RVU table dictionary (REQUIRED - must be provided from rvu_settings.yaml)
        classification_rules: Classification rules dictionary (optional)
        direct_lookups: Direct lookup dictionary (optional)
    
    Returns:
        Tuple of (study_type, rvu_value)
    """
    if not procedure_text:
        return "Unknown", 0.0
    
    # Require rvu_table - it must be provided from loaded settings
    if rvu_table is None:
        logger.error("match_study_type called without rvu_table parameter. RVU table must be loaded from rvu_settings.yaml")
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
        
        # Special handling for "CTA Brain with Perfusion" - don't match via partial matching
        # unless it has CTA/angio indicators (classification rules should handle it if it does)
        if study_lower == "cta brain with perfusion":
            # Only match if it has CTA/angio indicators (otherwise classification rules would have matched it)
            has_cta_indicator = ("cta" in procedure_lower or "angio" in procedure_lower or "angiography" in procedure_lower)
            if not has_cta_indicator:
                continue  # Skip partial matching for this study type if no CTA indicators
        
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


__all__ = ['match_study_type']
