"""Clario worklist window detection and patient class extraction."""

import logging
from typing import Optional, Any, Dict, List

try:
    from pywinauto import Desktop
except ImportError:
    Desktop = None

from .window_extraction import _window_text_with_timeout

logger = logging.getLogger(__name__)

# Clario cache
_clario_cache = {
    'chrome_window': None,
    'content_area': None
}


def find_clario_chrome_window(use_cache=True) -> Optional[Any]:
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
    if Desktop is None:
        return None
    
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


def find_clario_content_area(chrome_window, use_cache=True) -> Optional[Any]:
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


def _combine_priority_and_class_clario(data: Dict) -> None:
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


def extract_clario_patient_class(target_accession=None) -> Optional[Dict]:
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













