"""
Test script to display all UI elements from Clario - Worklist Chrome tab.
This shows all displayable items in the Clario worklist web page.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
import re
import time
import json
import subprocess
import socket
import urllib.request
import urllib.error

# Try to import websocket for CDP
try:
    import websocket
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

# Cache for element locations to speed up subsequent searches
_element_cache = {
    'priority_location': None,
    'class_location': None,
    'accession_location': None,
    'last_cache_time': 0,
    'cache_valid_for_seconds': 300  # Cache valid for 5 minutes
}

# Cache for Chrome window and tab references
# These are cached until they fail (e.g., window closed), then we re-search
_chrome_cache = {
    'chrome_window': None,
    'content_area': None,
    'cdp_tab': None,
    'cdp_ws_url': None
}


def find_chrome_window(use_cache=True):
    """Find Chrome window with 'Clario - Worklist' tab.
    
    Uses cache if available and valid, only searches if cache is invalid or missing.
    """
    global _chrome_cache
    
    # Check cache first - only verify when we try to use it
    if use_cache and _chrome_cache['chrome_window']:
        try:
            # Verify the cached window still exists and is valid
            _ = _chrome_cache['chrome_window'].window_text()
            return _chrome_cache['chrome_window']
        except:
            # Cache invalid (window closed?), clear it
            _chrome_cache['chrome_window'] = None
            _chrome_cache['content_area'] = None
    
    # Search for window
    desktop = Desktop(backend="uia")
    
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
                
                # Look for Chrome window with "clario" and "worklist" in title
                if "clario" in window_text and "worklist" in window_text:
                    # Chrome windows typically have "Chrome" or "Google Chrome" in class name
                    try:
                        class_name = window.element_info.class_name.lower()
                        if "chrome" in class_name:
                            # Cache it
                            _chrome_cache['chrome_window'] = window
                            return window
                    except:
                        # If we can't check class name, still return it if title matches
                        _chrome_cache['chrome_window'] = window
                        return window
            except:
                continue
    except:
        pass
    
    return None


def find_chrome_content_area(chrome_window, use_cache=True):
    """Find the Chrome content area (where the web page is rendered).
    
    Uses cache if available and valid, only searches if cache is invalid or missing.
    """
    global _chrome_cache
    
    # Check cache first
    if use_cache and _chrome_cache['content_area']:
        try:
            # Verify the cached content area still exists
            _ = _chrome_cache['content_area'].element_info.control_type
            return _chrome_cache['content_area']
        except:
            # Cache invalid, clear it
            _chrome_cache['content_area'] = None
    
    if not chrome_window:
        return None
    
    try:
        # Chrome's content area is typically a document or web view
        # Look for elements with control_type "Document" or "Pane"
        for child in chrome_window.descendants():
            try:
                control_type = child.element_info.control_type
                # Document or Pane containing web content
                if control_type in ["Document", "Pane"]:
                    # Check if it has meaningful content (not just empty panes)
                    try:
                        name = child.element_info.name or ""
                        # Skip if it's clearly not content (like toolbar, etc.)
                        if name and len(name) > 10:
                            _chrome_cache['content_area'] = child
                            return child
                    except:
                        pass
            except:
                continue
    except:
        pass
    
    # Fallback: try to find by automation_id patterns common in Chrome
    try:
        for child in chrome_window.descendants():
            try:
                automation_id = child.element_info.automation_id or ""
                # Chrome content areas often have specific IDs
                if "content" in automation_id.lower() or "render" in automation_id.lower():
                    _chrome_cache['content_area'] = child
                    return child
            except:
                continue
    except:
        pass
    
    # Last resort: return the window itself and scan all descendants
    _chrome_cache['content_area'] = chrome_window
    return chrome_window


def get_all_elements(element, depth=0, max_depth=10):
    """Recursively get all UI elements from a window."""
    elements = []
    
    if depth > max_depth:
        return elements
    
    try:
        # Get element info
        try:
            automation_id = element.element_info.automation_id or ""
        except:
            automation_id = ""
        
        try:
            control_type = element.element_info.control_type or ""
        except:
            control_type = ""
        
        try:
            name = element.element_info.name or ""
        except:
            name = ""
        
        try:
            text = element.window_text() or ""
        except:
            text = ""
        
        # Only include elements with some meaningful content
        if automation_id or name or text:
            elements.append({
                'depth': depth,
                'automation_id': automation_id,
                'control_type': control_type,
                'name': name,
                'text': text[:100] if text else "",  # Limit text length
                'element': element,  # Store actual element reference for inspection
            })
        
        # Recursively get children
        try:
            children = element.children()
            for child in children:
                elements.extend(get_all_elements(child, depth + 1, max_depth))
        except:
            pass
            
    except Exception as e:
        pass
    
    return elements


def _extract_value_from_element(elem):
    """Extract the actual value from an element, checking element itself and children."""
    if not elem:
        return ''
    
    # Try to get value from the element itself
    name = elem.get('name', '').strip()
    text = elem.get('text', '').strip()
    automation_id = elem.get('automation_id', '').strip()
    
    # Determine what label we're looking for based on automation_id
    label = automation_id.lower() if automation_id else ''
    
    # Check if name/text is the actual value (not a label)
    if name and name.lower() not in [label, f'{label}:']:
        return name
    elif text and text.lower() not in [label, f'{label}:']:
        return text
    
    # Value might be in a child element - try to find it
    try:
        element_obj = elem.get('element')
        if element_obj:
            # Try to get children (limit to immediate children for speed)
            try:
                children = element_obj.children()
                for child in children:
                    try:
                        child_name = child.element_info.name or ""
                        child_text = child.window_text() or ""
                        if child_name and child_name.lower() not in [label, f'{label}:']:
                            return child_name.strip()
                        elif child_text and child_text.lower() not in [label, f'{label}:']:
                            return child_text.strip()
                    except:
                        continue
            except:
                pass
    except:
        pass
    
    return ''


def _combine_priority_and_class(data):
    """Combine Priority and Class into structured patient_class."""
    # Combine Priority and Class intelligently
    # Structure: [urgency terms] + [location]
    # Urgency: STAT, Stroke, Urgent, Routine, etc.
    # Location: Emergency, Inpatient, Outpatient, Observation, Ambulatory, etc.
    
    combined_class = ''
    
    priority_value = data.get('priority', '')
    class_value = data.get('class', '')
    
    # Normalize: Replace ED/ER with "Emergency" in both Priority and Class
    if priority_value:
        priority_value = priority_value.replace('ED', 'Emergency').replace('ER', 'Emergency').replace('ed', 'Emergency').replace('er', 'Emergency')
    if class_value:
        class_value = class_value.replace('ED', 'Emergency').replace('ER', 'Emergency').replace('ed', 'Emergency').replace('er', 'Emergency')
    
    # Define urgency terms and location terms
    urgency_terms = ['STAT', 'Stroke', 'Urgent', 'Routine', 'ASAP', 'CRITICAL', 'IMMEDIATE']
    location_terms = ['Emergency', 'Inpatient', 'Outpatient', 'Observation', 'Ambulatory']
    
    # Extract urgency from Priority
    urgency_parts = []
    location_from_priority = []
    
    if priority_value:
        priority_clean = priority_value.strip()
        priority_parts = priority_clean.split()
        
        for part in priority_parts:
            part_upper = part.upper()
            # Check if it's an urgency term
            is_urgency = any(term.upper() in part_upper for term in urgency_terms)
            # Check if it's a location term
            is_location = any(term.lower() in part.lower() for term in location_terms)
            
            if is_urgency:
                urgency_parts.append(part)
            elif is_location:
                location_from_priority.append(part)
    
    # Extract location from Class
    location_from_class = ''
    if class_value:
        class_clean = class_value.strip()
        # Check if class contains a location term
        for location_term in location_terms:
            if location_term.lower() in class_clean.lower():
                location_from_class = location_term  # Use the standard term
                break
        
        # If no standard location term found, use the class value as-is
        if not location_from_class:
            location_from_class = class_clean
    
    # Determine final location (prefer Class over Priority)
    final_location = location_from_class if location_from_class else ' '.join(location_from_priority) if location_from_priority else ''
    
    # Remove redundant location from urgency parts
    # If location is already in Class, don't include it from Priority
    if final_location:
        final_location_lower = final_location.lower()
        urgency_parts = [part for part in urgency_parts if part.lower() not in final_location_lower]
    
    # Combine: urgency + location
    combined_parts = []
    if urgency_parts:
        combined_parts.extend(urgency_parts)
    if final_location:
        combined_parts.append(final_location)
    
    combined_class = ' '.join(combined_parts).strip()
    
    data['patient_class'] = combined_class


def extract_clario_data(content_element):
    """Extract study data from Clario - Worklist web page.
    
    Looks for Priority, Class, and Accession by their automation_id.
    Uses fast targeted search first, falls back to full scan if needed.
    
    Returns dict with: patient_class (combined), priority, class, accession, scan_path
    """
    data = {
        'patient_class': '',  # Combined Priority + Class
        'priority': '',
        'class': '',
        'accession': '',
        'scan_path': 'unknown',  # Track which scan path succeeded
    }
    
    try:
        import time
        total_start = time.time()
        timing_info = []
        
        # ULTRA-FAST PATH: Try direct lookup by automation_id using pywinauto's native search
        # This avoids scanning the entire DOM tree
        priority_element = None
        class_element = None
        accession_element = None
        
        direct_search_start = time.time()
        try:
            # Try direct lookup - much faster than scanning
            priority_element_obj = content_element.child_window(automation_id="Priority", found_index=0)
            if priority_element_obj.exists():
                priority_element = {
                    'element': priority_element_obj,
                    'automation_id': 'Priority',
                    'name': '',
                    'text': ''
                }
        except:
            pass
        
        try:
            class_element_obj = content_element.child_window(automation_id="Class", found_index=0)
            if class_element_obj.exists():
                class_element = {
                    'element': class_element_obj,
                    'automation_id': 'Class',
                    'name': '',
                    'text': ''
                }
        except:
            pass
        
        try:
            accession_element_obj = content_element.child_window(automation_id="Accession", found_index=0)
            if accession_element_obj.exists():
                accession_element = {
                    'element': accession_element_obj,
                    'automation_id': 'Accession',
                    'name': '',
                    'text': ''
                }
        except:
            pass
        
        direct_search_time = time.time() - direct_search_start
        timing_info.append(f"Direct lookup by automation_id: {direct_search_time:.3f}s")
        
        # If we found all three via direct lookup, extract immediately
        if priority_element and class_element and accession_element:
            extract_start = time.time()
            # Extract values directly from found elements
            data['priority'] = _extract_value_from_element(priority_element)
            data['class'] = _extract_value_from_element(class_element)
            data['accession'] = _extract_value_from_element(accession_element)
            extract_time = time.time() - extract_start
            timing_info.append(f"  Extract values: {extract_time:.3f}s")
            
            # Combine Priority and Class
            combine_start = time.time()
            _combine_priority_and_class(data)
            combine_time = time.time() - combine_start
            timing_info.append(f"  Combine Priority/Class: {combine_time:.3f}s")
            
            total_time = time.time() - total_start
            timing_info.append(f"TOTAL: {total_time:.3f}s")
            data['scan_path'] = 'direct lookup - all found'
            data['timing_info'] = '\n'.join(timing_info)
            return data
        
        # SKIP SLOW RECURSIVE SEARCH - Go straight to shallow scan with label-based search
        # The label-based search is what actually works, so let's do that on a shallow scan
        shallow_scan_start = time.time()
        # Use a shallow scan (depth 15) - elements seem to be between depth 10-20
        shallow_elements = get_all_elements(content_element, max_depth=15)
        shallow_scan_time = time.time() - shallow_scan_start
        timing_info.append(f"Shallow scan (depth 15) for label search: {shallow_scan_time:.3f}s - found {len(shallow_elements)} elements")
        
        # Convert to list and do label-based search immediately
        label_search_start = time.time()
        element_data = []
        for elem in shallow_elements:
            name = elem.get('name', '').strip()
            text = elem.get('text', '').strip()
            automation_id = elem.get('automation_id', '').strip()
            if name or text or automation_id:
                element_data.append({
                    'element': elem.get('element'),
                    'name': name,
                    'text': text,
                    'automation_id': automation_id,
                    'depth': elem.get('depth', 0)
                })
        
        # Label-based search (this is what actually works)
        for i, elem in enumerate(element_data):
            # Early exit if we found everything
            if data['priority'] and data['class'] and data['accession']:
                break
                
            name = elem['name']
            text = elem['text']
            automation_id = elem['automation_id']
            
            # PRIORITY - look for label
            if not data['priority']:
                if automation_id and 'priority' in automation_id.lower():
                    for j in range(i+1, min(i+10, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name']
                        next_text = next_elem['text']
                        if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                            data['priority'] = next_name
                            if next_elem.get('element'):
                                _element_cache['priority_location'] = next_elem['element']
                            break
                        elif next_text and ':' not in next_text and next_text.lower() not in ['priority', 'class', 'accession']:
                            data['priority'] = next_text
                            if next_elem.get('element'):
                                _element_cache['priority_location'] = next_elem['element']
                            break
                elif name and 'priority' in name.lower() and ':' in name:
                    for j in range(i+1, min(i+10, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name']
                        if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                            data['priority'] = next_name
                            if next_elem.get('element'):
                                _element_cache['priority_location'] = next_elem['element']
                            break
            
            # CLASS - look for label
            if not data['class']:
                if automation_id and 'class' in automation_id.lower() and 'priority' not in automation_id.lower():
                    for j in range(i+1, min(i+10, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name']
                        next_text = next_elem['text']
                        if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                            data['class'] = next_name
                            if next_elem.get('element'):
                                _element_cache['class_location'] = next_elem['element']
                            break
                        elif next_text and ':' not in next_text and next_text.lower() not in ['priority', 'class', 'accession']:
                            data['class'] = next_text
                            if next_elem.get('element'):
                                _element_cache['class_location'] = next_elem['element']
                            break
                elif name and 'class' in name.lower() and ':' in name and 'priority' not in name.lower():
                    for j in range(i+1, min(i+10, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name']
                        if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                            data['class'] = next_name
                            if next_elem.get('element'):
                                _element_cache['class_location'] = next_elem['element']
                            break
            
            # ACCESSION - look for label
            if not data['accession']:
                if automation_id and 'accession' in automation_id.lower():
                    for j in range(i+1, min(i+10, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name']
                        next_text = next_elem['text']
                        if next_name and ':' not in next_name and len(next_name) > 5 and ' ' not in next_name:
                            data['accession'] = next_name
                            if next_elem.get('element'):
                                _element_cache['accession_location'] = next_elem['element']
                            break
                        elif next_text and ':' not in next_text and len(next_text) > 5 and ' ' not in next_text:
                            data['accession'] = next_text
                            if next_elem.get('element'):
                                _element_cache['accession_location'] = next_elem['element']
                            break
                elif name and 'accession' in name.lower() and ':' in name:
                    for j in range(i+1, min(i+10, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name']
                        if next_name and ':' not in next_name and len(next_name) > 5:
                            data['accession'] = next_name
                            if next_elem.get('element'):
                                _element_cache['accession_location'] = next_elem['element']
                            break
        
        label_search_time = time.time() - label_search_start
        timing_info.append(f"  Label-based search: {label_search_time:.3f}s")
        
        # Update cache timestamp
        if data['priority'] or data['class'] or data['accession']:
            _element_cache['last_cache_time'] = time.time()
        
        # If we found all three via shallow scan + label search, we're done!
        if data['priority'] and data['class'] and data['accession']:
            combine_start = time.time()
            _combine_priority_and_class(data)
            combine_time = time.time() - combine_start
            timing_info.append(f"  Combine Priority/Class: {combine_time:.3f}s")
            
            total_time = time.time() - total_start
            timing_info.append(f"TOTAL: {total_time:.3f}s")
            data['scan_path'] = 'shallow scan (depth 15) + label search - all found'
            data['timing_info'] = '\n'.join(timing_info)
            return data
        
        # If we still haven't found all, fall back to full scan (last resort)
        if not (priority_element and class_element and accession_element):
            # Track what we found so far
            found = []
            if priority_element:
                found.append('Priority')
            if class_element:
                found.append('Class')
            if accession_element:
                found.append('Accession')
            
            # Only do full scan if we're missing elements
            if not priority_element or not class_element or not accession_element:
                # Full scan as last resort - but use label-based search which is what actually works
                full_scan_start = time.time()
                # Use a more targeted approach - scan but look for labels and nearby values
                all_elements = get_all_elements(content_element, max_depth=20)  # Reduced from 25
                full_scan_time = time.time() - full_scan_start
                timing_info.append(f"Full scan (depth 20) - LAST RESORT: {full_scan_time:.3f}s - found {len(all_elements)} elements")
                data['scan_path'] = 'full scan (depth 20) - fallback'
                
                # Search in full scan results - look for labels and get values
                # OPTIMIZATION: Only process elements that might be relevant (have text/name)
                full_search_start = time.time()
                element_data = []
                for elem in all_elements:
                    name = elem.get('name', '').strip()
                    text = elem.get('text', '').strip()
                    automation_id = elem.get('automation_id', '').strip()
                    # Only add if it has meaningful content
                    if name or text or automation_id:
                        element_data.append({
                            'element': elem.get('element'),
                            'name': name,
                            'text': text,
                            'automation_id': automation_id,
                            'depth': elem.get('depth', 0)
                        })
                
                timing_info.append(f"  Filtered to {len(element_data)} relevant elements")
                
                # Now search for labels and extract values - OPTIMIZED: stop early when all found
                for i, elem in enumerate(element_data):
                    # Early exit if we found everything
                    if data['priority'] and data['class'] and data['accession']:
                        break
                    name = elem['name']
                    text = elem['text']
                    automation_id = elem['automation_id']
                    
                    # PRIORITY - look for label
                    if not data['priority']:
                        if automation_id and 'priority' in automation_id.lower():
                            # Look ahead for value
                            for j in range(i+1, min(i+10, len(element_data))):
                                next_elem = element_data[j]
                                next_name = next_elem['name']
                                next_text = next_elem['text']
                                if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                    data['priority'] = next_name
                                    # Cache the element
                                    if next_elem.get('element'):
                                        _element_cache['priority_location'] = next_elem['element']
                                    break
                                elif next_text and ':' not in next_text and next_text.lower() not in ['priority', 'class', 'accession']:
                                    data['priority'] = next_text
                                    if next_elem.get('element'):
                                        _element_cache['priority_location'] = next_elem['element']
                                    break
                        elif name and 'priority' in name.lower() and ':' in name:
                            for j in range(i+1, min(i+10, len(element_data))):
                                next_elem = element_data[j]
                                next_name = next_elem['name']
                                if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                    data['priority'] = next_name
                                    if next_elem.get('element'):
                                        _element_cache['priority_location'] = next_elem['element']
                                    break
                    
                    # CLASS - look for label
                    if not data['class']:
                        if automation_id and 'class' in automation_id.lower() and 'priority' not in automation_id.lower():
                            for j in range(i+1, min(i+10, len(element_data))):
                                next_elem = element_data[j]
                                next_name = next_elem['name']
                                next_text = next_elem['text']
                                if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                    data['class'] = next_name
                                    if next_elem.get('element'):
                                        _element_cache['class_location'] = next_elem['element']
                                    break
                                elif next_text and ':' not in next_text and next_text.lower() not in ['priority', 'class', 'accession']:
                                    data['class'] = next_text
                                    if next_elem.get('element'):
                                        _element_cache['class_location'] = next_elem['element']
                                    break
                        elif name and 'class' in name.lower() and ':' in name and 'priority' not in name.lower():
                            for j in range(i+1, min(i+10, len(element_data))):
                                next_elem = element_data[j]
                                next_name = next_elem['name']
                                if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                    data['class'] = next_name
                                    if next_elem.get('element'):
                                        _element_cache['class_location'] = next_elem['element']
                                    break
                    
                    # ACCESSION - look for label
                    if not data['accession']:
                        if automation_id and 'accession' in automation_id.lower():
                            for j in range(i+1, min(i+10, len(element_data))):
                                next_elem = element_data[j]
                                next_name = next_elem['name']
                                next_text = next_elem['text']
                                if next_name and ':' not in next_name and len(next_name) > 5 and ' ' not in next_name:
                                    data['accession'] = next_name
                                    if next_elem.get('element'):
                                        _element_cache['accession_location'] = next_elem['element']
                                    break
                                elif next_text and ':' not in next_text and len(next_text) > 5 and ' ' not in next_text:
                                    data['accession'] = next_text
                                    if next_elem.get('element'):
                                        _element_cache['accession_location'] = next_elem['element']
                                    break
                        elif name and 'accession' in name.lower() and ':' in name:
                            for j in range(i+1, min(i+10, len(element_data))):
                                next_elem = element_data[j]
                                next_name = next_elem['name']
                                if next_name and ':' not in next_name and len(next_name) > 5:
                                    data['accession'] = next_name
                                    if next_elem.get('element'):
                                        _element_cache['accession_location'] = next_elem['element']
                                    break
                    
                    if data['priority'] and data['class'] and data['accession']:
                        break
                
                full_search_time = time.time() - full_search_start
                timing_info.append(f"  Label-based search in full scan: {full_search_time:.3f}s")
                
                # Update cache timestamp
                if data['priority'] or data['class'] or data['accession']:
                    _element_cache['last_cache_time'] = time.time()
                
                # Extract from found elements
                full_extract_start = time.time()
                if priority_element:
                    data['priority'] = _extract_value_from_element(priority_element)
                if class_element:
                    data['class'] = _extract_value_from_element(class_element)
                if accession_element:
                    data['accession'] = _extract_value_from_element(accession_element)
                full_extract_time = time.time() - full_extract_start
                timing_info.append(f"  Extract values: {full_extract_time:.3f}s")
                
                # Check if we found all after full scan
                if priority_element and class_element and accession_element:
                    combine_start = time.time()
                    _combine_priority_and_class(data)
                    combine_time = time.time() - combine_start
                    timing_info.append(f"  Combine Priority/Class: {combine_time:.3f}s")
                    
                    total_time = time.time() - total_start
                    timing_info.append(f"TOTAL: {total_time:.3f}s")
                    data['scan_path'] = 'full scan (depth 25) - all found'
                    data['timing_info'] = '\n'.join(timing_info)
                    return data
                else:
                    # Still missing - try label-based search
                    timing_info.append(f"  Still missing after full scan, trying label-based search...")
                    
                    # Convert to list for sequential searching (if not already done)
                    if 'element_data' not in locals():
                        convert_start = time.time()
                        element_data = []
                        for elem in all_elements:
                            name = elem.get('name', '').strip()
                            text = elem.get('text', '').strip()
                            automation_id = elem.get('automation_id', '').strip()
                            if name or text or automation_id:
                                element_data.append({
                                    'element': elem.get('element'),
                                    'name': name,
                                    'text': text,
                                    'automation_id': automation_id,
                                    'depth': elem.get('depth', 0)
                                })
                        convert_time = time.time() - convert_start
                        timing_info.append(f"  Convert to list: {convert_time:.3f}s")
                    
                    fallback_search_start = time.time()
                    for i, elem in enumerate(element_data):
                        name = elem['name']
                        text = elem['text']
                        automation_id = elem['automation_id']
                        
                        # PRIORITY - look for label or automation_id containing "priority"
                        if not data['priority']:
                            if automation_id and 'priority' in automation_id.lower():
                                # Look ahead for value
                                for j in range(i+1, min(i+10, len(element_data))):
                                    next_elem = element_data[j]
                                    next_name = next_elem['name']
                                    next_text = next_elem['text']
                                    # Skip if it's another label
                                    if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                        data['priority'] = next_name
                                        break
                                    elif next_text and ':' not in next_text and next_text.lower() not in ['priority', 'class', 'accession']:
                                        data['priority'] = next_text
                                        break
                            elif name and 'priority' in name.lower() and ':' in name:
                                # Label found, look for value
                                for j in range(i+1, min(i+10, len(element_data))):
                                    next_elem = element_data[j]
                                    next_name = next_elem['name']
                                    if next_name and ':' not in next_name and next_name.lower() not in ['priority', 'class', 'accession']:
                                        data['priority'] = next_name
                                        break
                        
                        # CLASS - look for label or automation_id containing "class" (but not "priority")
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
                        
                        # ACCESSION - look for label or automation_id containing "accession"
                        if not data['accession']:
                            if automation_id and 'accession' in automation_id.lower():
                                for j in range(i+1, min(i+10, len(element_data))):
                                    next_elem = element_data[j]
                                    next_name = next_elem['name']
                                    next_text = next_elem['text']
                                    # Accession is usually alphanumeric, no spaces typically
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
                                    if next_name and ':' not in next_name and len(next_name) > 5:
                                        data['accession'] = next_name
                                        break
                    fallback_search_time = time.time() - fallback_search_start
                    timing_info.append(f"  Fallback label search: {fallback_search_time:.3f}s")
        
        # Combine Priority and Class (if not already done)
        if not data['patient_class']:
            combine_start = time.time()
            _combine_priority_and_class(data)
            combine_time = time.time() - combine_start
            timing_info.append(f"  Combine Priority/Class: {combine_time:.3f}s")
        
        # Update scan_path if we successfully found everything in fallback
        if data['priority'] and data['class'] and data['accession']:
            if 'full scan' in data.get('scan_path', ''):
                data['scan_path'] = 'full scan (depth 20) - all found via fallback'
        
        total_time = time.time() - total_start
        timing_info.append(f"TOTAL: {total_time:.3f}s")
        data['timing_info'] = '\n'.join(timing_info)
        
    except Exception as e:
        print(f"Error extracting Clario data: {e}")
        import traceback
        traceback.print_exc()
    
    return data


# Global variable to store elements
all_elements = []
element_list_items = []  # Store elements in same order as listbox items


def display_elements(elements_to_display=None, search_term=""):
    """Display elements in the left pane search results."""
    global element_list_items
    
    if elements_to_display is None:
        elements_to_display = all_elements
    
    # Clear the listbox
    left_listbox.delete(0, tk.END)
    element_list_items.clear()
    
    # Update header
    if search_term:
        header_text = f"Search results for '{search_term}': {len(elements_to_display)} of {len(all_elements)} elements"
    else:
        header_text = f"Found {len(elements_to_display)} elements"
    
    left_header.config(text=header_text)
    
    # Display elements as individual list items
    for idx, elem in enumerate(elements_to_display):
        indent = "  " * elem['depth']
        line = f"{indent}Depth {elem['depth']}: "
        
        # Add source indicator if available
        source = elem.get('source', '')
        if source:
            line += f"[{source}] "
        
        if elem['automation_id']:
            line += f"ID='{elem['automation_id']}' "
        if elem['control_type']:
            line += f"Type={elem['control_type']} "
        if elem['name']:
            line += f"Name='{elem['name']}' "
        if elem['text']:
            line += f"Text='{elem['text']}'"
        
        # Add to listbox
        left_listbox.insert(tk.END, line)
        # Store element reference by index
        element_list_items.append(elem)


def refresh_elements():
    """Refresh the element list and extract data."""
    global all_elements
    
    import time
    total_refresh_start = time.time()
    refresh_timing = []
    
    left_listbox.delete(0, tk.END)
    left_header.config(text="Searching for Chrome window with 'Clario - Worklist' tab...")
    root.update()
    
    find_window_start = time.time()
    chrome_window = find_chrome_window(use_cache=True)  # Use cache if available
    find_window_time = time.time() - find_window_start
    refresh_timing.append(f"Find Chrome window: {find_window_time:.3f}s")
    
    if not chrome_window:
        # Cache miss or invalid - try once more without cache
        chrome_window = find_chrome_window(use_cache=False)
        if not chrome_window:
            left_header.config(text="ERROR: Chrome window with 'Clario - Worklist' tab not found! Please make sure Chrome is open with the Clario - Worklist tab active.")
            all_elements = []
            return
    
    try:
        window_text = chrome_window.window_text()
        left_header.config(text=f"Found Chrome window: {window_text} - Looking for content area...")
        root.update()
        
        # Find the Chrome content area (uses cache if available)
        find_content_start = time.time()
        content_area = find_chrome_content_area(chrome_window, use_cache=True)
        find_content_time = time.time() - find_content_start
        refresh_timing.append(f"Find content area: {find_content_time:.3f}s")
        
        if content_area:
            left_header.config(text=f"Found content area - Extracting data...")
            root.update()
            
            # Extract structured data from web content (this is the fast path)
            start_time = time.time()
            extracted_data = extract_clario_data(content_area)
            extraction_time = time.time() - start_time
            refresh_timing.append(f"Extract data: {extraction_time:.3f}s")
            
            # Show extracted data in right pane
            right_text.config(state=tk.NORMAL)
            right_text.delete(1.0, tk.END)
            right_text.insert(tk.END, "=" * 70 + "\n")
            right_text.insert(tk.END, "EXTRACTED CLARIO DATA\n")
            right_text.insert(tk.END, "=" * 70 + "\n\n")
            
            # Highlight Patient Class as primary target
            right_text.insert(tk.END, f"*** PATIENT CLASS (COMBINED): {extracted_data['patient_class'] or 'NOT FOUND'} ***\n\n")
            
            # Show individual components
            right_text.insert(tk.END, f"Priority: {extracted_data['priority'] or 'NOT FOUND'}\n")
            right_text.insert(tk.END, f"Class: {extracted_data['class'] or 'NOT FOUND'}\n")
            right_text.insert(tk.END, f"Accession: {extracted_data['accession'] or 'NOT FOUND'}\n")
            total_refresh_time = time.time() - total_refresh_start
            refresh_timing.append(f"TOTAL REFRESH: {total_refresh_time:.3f}s")
            
            right_text.insert(tk.END, f"\n(Extraction took {extraction_time:.2f} seconds)\n")
            right_text.insert(tk.END, f"Scan path: {extracted_data.get('scan_path', 'unknown')}\n\n")
            right_text.insert(tk.END, "INITIAL SETUP TIMING:\n")
            right_text.insert(tk.END, '\n'.join(refresh_timing) + "\n\n")
            right_text.insert(tk.END, "EXTRACTION TIMING BREAKDOWN:\n")
            right_text.insert(tk.END, extracted_data.get('timing_info', 'No timing info available') + "\n")
            right_text.config(state=tk.DISABLED)
            
            # Get web content elements for display (with meaningful data only)
            # Use shallow scan for display to speed things up
            left_header.config(text=f"Scanning web content elements for display (shallow scan)...")
            root.update()
            display_start = time.time()
            content_elements = get_all_elements(content_area, max_depth=8)  # Reduced from 25 to 8 for speed
            display_time = time.time() - display_start
            
            # Filter to only show elements with meaningful data
            meaningful = [e for e in content_elements if (e.get('name', '').strip() and len(e.get('name', '').strip()) > 2) or 
                                                          (e.get('automation_id', '').strip()) or
                                                          (e.get('text', '').strip() and len(e.get('text', '').strip()) > 2)]
            
            all_elements = meaningful
            left_header.config(text=f"Found {len(meaningful)} elements (of {len(content_elements)} total) - Display scan: {display_time:.2f}s")
            
            # Display all elements
            display_elements()
            
        else:
            left_header.config(text=f"Found window but could not locate content area - Scanning entire window (shallow scan)...")
            root.update()
            all_elements = get_all_elements(chrome_window, max_depth=8)  # Reduced from 15 to 8 for speed
            left_header.config(text=f"Found {len(all_elements)} elements from Chrome window (shallow scan)")
            display_elements()
        
    except Exception as e:
        left_header.config(text=f"ERROR: {str(e)}")
        import traceback
        print(traceback.format_exc())
        all_elements = []


def search_elements():
    """Search through elements."""
    global all_elements
    
    if not all_elements:
        left_header.config(text="No elements loaded. Please click 'Refresh Elements' first.")
        return
    
    search_term = search_entry.get().strip().lower()
    
    if not search_term:
        # If search is empty, show all elements
        display_elements()
        return
    
    # Filter elements based on search term
    filtered = []
    for elem in all_elements:
        # Search in automation_id, control_type, name, and text
        if (search_term in elem['automation_id'].lower() or
            search_term in elem['control_type'].lower() or
            search_term in elem['name'].lower() or
            search_term in elem['text'].lower()):
            filtered.append(elem)
    
    display_elements(filtered, search_term)


def on_search_key(event):
    """Handle Enter key in search box."""
    search_elements()


def get_element_children(element, depth=0, max_depth=3):
    """Get children of an element recursively."""
    children_data = []
    
    if depth > max_depth:
        return children_data
    
    try:
        children = element.children()
        for child in children:
            try:
                automation_id = child.element_info.automation_id or ""
            except:
                automation_id = ""
            
            try:
                control_type = child.element_info.control_type or ""
            except:
                control_type = ""
            
            try:
                name = child.element_info.name or ""
            except:
                name = ""
            
            try:
                text = child.window_text() or ""
            except:
                text = ""
            
            if automation_id or name or text:
                child_data = {
                    'depth': depth,
                    'automation_id': automation_id,
                    'control_type': control_type,
                    'name': name,
                    'text': text[:100] if text else "",
                    'element': child,
                }
                children_data.append(child_data)
                
                # Recursively get grandchildren
                grandchildren = get_element_children(child, depth + 1, max_depth)
                children_data.extend(grandchildren)
    except Exception as e:
        pass
    
    return children_data


def inspect_element(element_data):
    """Show detailed information about an element in the right pane."""
    if not element_data or 'element' not in element_data:
        return
    
    element = element_data['element']
    
    # Enable right pane for editing
    right_text.config(state=tk.NORMAL)
    
    # Clear right pane
    right_text.delete(1.0, tk.END)
    
    # Gather all available information
    info_lines = []
    info_lines.append("=" * 70)
    info_lines.append("ELEMENT DETAILS")
    info_lines.append("=" * 70)
    info_lines.append("")
    
    try:
        info_lines.append(f"Automation ID: {element_data.get('automation_id', 'N/A')}")
    except:
        pass
    
    try:
        info_lines.append(f"Control Type: {element_data.get('control_type', 'N/A')}")
    except:
        pass
    
    try:
        info_lines.append(f"Name: {element_data.get('name', 'N/A')}")
    except:
        pass
    
    try:
        full_text = element.window_text() if hasattr(element, 'window_text') else element_data.get('text', 'N/A')
        info_lines.append(f"Text: {full_text}")
    except:
        info_lines.append(f"Text: {element_data.get('text', 'N/A')}")
    
    info_lines.append("")
    info_lines.append("-" * 70)
    info_lines.append("ELEMENT INFO PROPERTIES")
    info_lines.append("-" * 70)
    info_lines.append("")
    
    # Try to get element_info properties
    try:
        ei = element.element_info
        info_lines.append(f"Class Name: {getattr(ei, 'class_name', 'N/A')}")
        info_lines.append(f"Control Type: {getattr(ei, 'control_type', 'N/A')}")
        info_lines.append(f"Automation ID: {getattr(ei, 'automation_id', 'N/A')}")
        info_lines.append(f"Name: {getattr(ei, 'name', 'N/A')}")
        info_lines.append(f"Framework ID: {getattr(ei, 'framework_id', 'N/A')}")
        info_lines.append(f"Runtime ID: {getattr(ei, 'runtime_id', 'N/A')}")
    except Exception as e:
        info_lines.append(f"Error accessing element_info: {e}")
    
    info_lines.append("")
    info_lines.append("-" * 70)
    info_lines.append("ELEMENT PROPERTIES")
    info_lines.append("-" * 70)
    info_lines.append("")
    
    # Try to get rectangle (position/size)
    try:
        rect = element.rectangle()
        info_lines.append(f"Rectangle: {rect}")
        info_lines.append(f"  Left: {rect.left}, Top: {rect.top}")
        info_lines.append(f"  Right: {rect.right}, Bottom: {rect.bottom}")
        info_lines.append(f"  Width: {rect.width()}, Height: {rect.height()}")
    except Exception as e:
        info_lines.append(f"Rectangle: Error - {e}")
    
    # Try to get other properties
    try:
        info_lines.append(f"Is Enabled: {element.is_enabled()}")
    except:
        pass
    
    try:
        info_lines.append(f"Is Visible: {element.is_visible()}")
    except:
        pass
    
    try:
        info_lines.append(f"Has Keyboard Focus: {element.has_keyboard_focus()}")
    except:
        pass
    
    info_lines.append("")
    info_lines.append("=" * 70)
    info_lines.append("CHILDREN")
    info_lines.append("=" * 70)
    info_lines.append("")
    
    # Get and display children
    try:
        children = get_element_children(element, max_depth=3)
        info_lines.append(f"Number of children: {len(children)}\n")
        
        if children:
            for child in children:
                indent = "  " * child['depth']
                child_line = f"{indent}Depth {child['depth']}: "
                
                if child['automation_id']:
                    child_line += f"ID='{child['automation_id']}' "
                if child['control_type']:
                    child_line += f"Type={child['control_type']} "
                if child['name']:
                    child_line += f"Name='{child['name']}' "
                if child['text']:
                    child_line += f"Text='{child['text']}'"
                
                info_lines.append(child_line)
        else:
            info_lines.append("No children found")
    except Exception as e:
        info_lines.append(f"Error getting children: {e}")
    
    # Display all info
    right_text.insert(tk.END, "\n".join(info_lines))
    right_text.config(state=tk.DISABLED)  # Make read-only


def on_listbox_select(event):
    """Handle selection in listbox."""
    selection = left_listbox.curselection()
    if selection:
        idx = selection[0]
        if 0 <= idx < len(element_list_items):
            inspect_element(element_list_items[idx])


# Create GUI
root = tk.Tk()
root.title("Clario - Worklist UI Elements Viewer")
root.geometry("1400x800")

# Frame for buttons
button_frame = ttk.Frame(root, padding="10")
button_frame.pack(fill=tk.X)

# Refresh button
refresh_btn = ttk.Button(button_frame, text="Refresh Elements", command=refresh_elements)
refresh_btn.pack(side=tk.LEFT, padx=5)

# Search frame
search_frame = ttk.Frame(button_frame)
search_frame.pack(side=tk.LEFT, padx=10)

ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 5))
search_entry = ttk.Entry(search_frame, width=30)
search_entry.pack(side=tk.LEFT, padx=(0, 5))
search_entry.bind('<Return>', on_search_key)

search_btn = ttk.Button(search_frame, text="Search", command=search_elements)
search_btn.pack(side=tk.LEFT, padx=(0, 5))

clear_btn = ttk.Button(search_frame, text="Clear", command=lambda: (search_entry.delete(0, tk.END), display_elements() if all_elements else None))
clear_btn.pack(side=tk.LEFT)

# Instructions
instructions = ttk.Label(button_frame, text="Refresh to scan, then search by ID, Type, Name, or Text")
instructions.pack(side=tk.LEFT, padx=10)

# Create paned window for dual pane
paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

# Left pane - Search results
left_frame = ttk.Frame(paned)
paned.add(left_frame, weight=1)

left_label = ttk.Label(left_frame, text="Search Results (Click to inspect)", font=("Arial", 10, "bold"))
left_label.pack(pady=5)

left_header = ttk.Label(left_frame, text="Click 'Refresh Elements' to start scanning.", font=("Arial", 9))
left_header.pack(pady=2)

# Listbox with scrollbar
listbox_frame = ttk.Frame(left_frame)
listbox_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

left_listbox = tk.Listbox(listbox_frame, font=("Consolas", 9), selectmode=tk.SINGLE)
left_scrollbar = ttk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=left_listbox.yview)
left_listbox.config(yscrollcommand=left_scrollbar.set)

left_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

left_listbox.bind("<<ListboxSelect>>", on_listbox_select)

# Right pane - Element details
right_frame = ttk.Frame(paned)
paned.add(right_frame, weight=1)

right_label = ttk.Label(right_frame, text="Element Details & Children", font=("Arial", 10, "bold"))
right_label.pack(pady=5)

right_text = scrolledtext.ScrolledText(right_frame, wrap=tk.WORD, font=("Consolas", 9))
right_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

# Initial message is shown in left_header

right_text.insert(tk.END, "Element details will appear here when you click an element.\n")

# Start the GUI
root.mainloop()

