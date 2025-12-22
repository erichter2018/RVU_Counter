"""Mosaic Info Hub window detection and data extraction."""

import logging
import re
from typing import Optional, Any, Dict, List

from .window_extraction import get_cached_desktop, _window_text_with_timeout

logger = logging.getLogger(__name__)


def find_mosaic_window() -> Optional[Any]:
    """Find Mosaic Info Hub window - it's a WinForms app with WebView2."""
    desktop = get_cached_desktop()
    
    if desktop is None:
        return None
    
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


def find_mosaic_webview_element(main_window) -> Optional[Any]:
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


def get_mosaic_elements(webview_element, depth=0, max_depth=20) -> List[Dict]:
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


def get_mosaic_elements_via_descendants(main_window, max_elements=5000) -> List[Dict]:
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


def _is_mosaic_accession_like(s: str) -> bool:
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


def extract_mosaic_data_v2(main_window) -> Dict:
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


def extract_mosaic_data(webview_element) -> Dict:
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













