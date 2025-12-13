"""
Test script to display all UI elements from Mosaic Info Hub or Mosaic Reporting interface.
This shows all displayable items in the Mosaic window.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
import re


def find_mosaic_window():
    """Find Mosaic Info Hub or Mosaic Reporting window - it's a WinForms app with WebView2."""
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
                
                # Look for Mosaic Info Hub or Mosaic Reporting window
                if ("mosaic" in window_text and "info hub" in window_text) or \
                   ("mosaic" in window_text and "reporting" in window_text):
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


def find_webview_element(main_window):
    """Find the WebView2 control inside the main window."""
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


def get_all_elements(element, depth=0, max_depth=15):
    """Recursively get all UI elements from a window using children()."""
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
        
        try:
            framework_id = element.element_info.framework_id or ""
        except:
            framework_id = ""
        
        # Only include elements with some meaningful content
        if automation_id or name or text:
            elements.append({
                'depth': depth,
                'automation_id': automation_id,
                'control_type': control_type,
                'name': name,
                'text': text[:200] if text else "",  # Limit text length
                'element': element,  # Store actual element reference for inspection
                'framework_id': framework_id,
            })
        
        # Recursively get children - NO LIMIT
        try:
            children = element.children()
            for child in children:
                elements.extend(get_all_elements(child, depth + 1, max_depth))
        except:
            pass
            
    except Exception as e:
        pass
    
    return elements


def get_all_elements_descendants(window, max_elements=5000):
    """Get all elements using descendants() - exhaustive search."""
    elements = []
    
    try:
        count = 0
        for elem in window.descendants():
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
                text = elem.window_text() or ""
            except:
                text = ""
            
            try:
                framework_id = elem.element_info.framework_id or ""
            except:
                framework_id = ""
            
            if automation_id or name or text:
                elements.append({
                    'depth': 0,  # descendants() doesn't track depth
                    'automation_id': automation_id,
                    'control_type': control_type,
                    'name': name,
                    'text': text[:200] if text else "",
                    'element': elem,
                    'framework_id': framework_id,
                })
            
            count += 1
            if count >= max_elements:
                break
    except Exception as e:
        print(f"descendants() error: {e}")
    
    return elements


def get_elements_via_uia(window_title_contains):
    """Get elements using direct UIA via comtypes - bypasses pywinauto."""
    elements = []
    
    try:
        import comtypes.client
        
        # Initialize UIA
        uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}")
        
        # Get the IUIAutomation interface
        from comtypes.gen import UIAutomationClient
        IUIAutomation = uia.QueryInterface(UIAutomationClient.IUIAutomation)
        
        # Get root element
        root = IUIAutomation.GetRootElement()
        
        # Find window containing the title
        # Use subtree search with name condition
        all_windows = []
        
        # Create a true condition to get all children
        true_condition = IUIAutomation.CreateTrueCondition()
        
        # Get all top-level windows
        children = root.FindAll(2, true_condition)  # TreeScope_Children = 2
        
        target_window = None
        for i in range(children.Length):
            child = children.GetElement(i)
            try:
                name = child.CurrentName
                if name and window_title_contains.lower() in name.lower():
                    target_window = child
                    break
            except:
                continue
        
        if not target_window:
            return elements, "Window not found"
        
        # Get ALL descendants of the target window
        all_descendants = target_window.FindAll(7, true_condition)  # TreeScope_Subtree = 7
        
        for i in range(all_descendants.Length):
            try:
                elem = all_descendants.GetElement(i)
                automation_id = elem.CurrentAutomationId or ""
                control_type = elem.CurrentControlType
                name = elem.CurrentName or ""
                
                # Map control type ID to string
                control_type_map = {
                    50000: "Button", 50001: "Calendar", 50002: "CheckBox",
                    50003: "ComboBox", 50004: "Edit", 50005: "Hyperlink",
                    50006: "Image", 50007: "ListItem", 50008: "List",
                    50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
                    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
                    50015: "Slider", 50016: "Spinner", 50017: "StatusBar",
                    50018: "Tab", 50019: "TabItem", 50020: "Text",
                    50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
                    50024: "TreeItem", 50025: "Custom", 50026: "Group",
                    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem",
                    50030: "Document", 50031: "SplitButton", 50032: "Window",
                    50033: "Pane", 50034: "Header", 50035: "HeaderItem",
                    50036: "Table", 50037: "TitleBar", 50038: "Separator",
                }
                control_type_str = control_type_map.get(control_type, f"Type_{control_type}")
                
                if automation_id or name:
                    elements.append({
                        'depth': 0,
                        'automation_id': automation_id,
                        'control_type': control_type_str,
                        'name': name,
                        'text': name,  # UIA uses Name for text
                        'element': None,  # No pywinauto element
                    })
            except Exception as e:
                continue
        
        return elements, None
        
    except ImportError as e:
        return elements, f"comtypes not available: {e}"
    except Exception as e:
        import traceback
        return elements, f"UIA error: {e}\n{traceback.format_exc()}"


def extract_mosaic_data(webview_element):
    """Extract study data from Mosaic Info Hub WebView2 content.
    
    Looks for elements with labels like 'Accession(s):', 'MRN:', 'Modality:', etc.
    and extracts the associated values.
    
    Returns dict with: procedure, accession, mrn, patient_name, patient_class, study_date, modality, multiple_accessions
    """
    data = {
        'procedure': '',
        'accession': '',
        'mrn': '',
        'patient_name': '',
        'patient_class': '',  # Might be Gender (Male/Female) in Mosaic
        'study_date': '',
        'modality': '',
        'multiple_accessions': [],  # List of {accession, procedure} dicts
        'debug_info': {
            'accession_candidates': []  # List of potential accessions found
        }
    }
    
    try:
        # Get all elements from WebView2 with deep scan
        all_elements = get_all_elements(webview_element, max_depth=20)
        
        # Convert to list - prioritize name, but also include text for broader search
        element_data = []
        all_text_data = []  # Separate list for text-only elements
        for elem in all_elements:
            name = elem.get('name', '').strip()
            text = elem.get('text', '').strip()
            auto_id = elem.get('automation_id', '').strip()
            elem_obj = elem.get('element')
            
            if name:
                element_data.append({
                    'element': elem_obj,
                    'name': name,
                    'text': text,  # Keep text for reference
                    'automation_id': auto_id,
                    'depth': elem.get('depth', 0)
                })
            elif text:  # Elements with only text (no name)
                all_text_data.append({
                    'element': elem_obj,
                    'name': '',
                    'text': text,
                    'automation_id': auto_id,
                    'depth': elem.get('depth', 0)
                })
            elif auto_id:  # Elements with only automation_id
                all_text_data.append({
                    'element': elem_obj,
                    'name': '',
                    'text': '',
                    'automation_id': auto_id,
                    'depth': elem.get('depth', 0)
                })
        
        # Append text-only and automation_id-only elements at the end
        element_data.extend(all_text_data)
        
        # Helper function to check if a string looks like an accession
        def is_accession_like(s):
            """Check if string looks like an accession number."""
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
                           # Mosaic labeled fields - these are NOT accessions
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
            # MRN values should NOT be treated as accessions
            if 'mrn' in lower_s:
                return False
            
            # Reject common body parts and anatomy terms that might match patterns
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
        
        # Helper function to extract accession from text and record candidates
        def extract_accession_from_text(text_str, source="unknown", field="unknown"):
            """Extract accession(s) from a text string and record candidates."""
            if not text_str:
                return []
            results = []
            # Pattern 1: "ACC1 (PROC1), ACC2 (PROC2)" format
            if ',' in text_str and '(' in text_str:
                parts = text_str.split(',')
                for part in parts:
                    part = part.strip()
                    if '(' in part and ')' in part:
                        acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', part)
                        if acc_match:
                            acc = acc_match.group(1).strip()
                            proc = acc_match.group(2).strip()
                            if is_accession_like(acc):
                                data['debug_info']['accession_candidates'].append({
                                    'value': acc,
                                    'source': source,
                                    'field': field,
                                    'full_text': part
                                })
                                results.append({'accession': acc, 'procedure': proc})
                    elif is_accession_like(part):
                        data['debug_info']['accession_candidates'].append({
                            'value': part,
                            'source': source,
                            'field': field,
                            'full_text': part
                        })
                        results.append({'accession': part, 'procedure': ''})
            # Pattern 2: Single accession in parentheses with procedure
            elif '(' in text_str and ')' in text_str:
                acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', text_str)
                if acc_match:
                    acc = acc_match.group(1).strip()
                    proc = acc_match.group(2).strip()
                    if is_accession_like(acc):
                        data['debug_info']['accession_candidates'].append({
                            'value': acc,
                            'source': source,
                            'field': field,
                            'full_text': text_str
                        })
                        results.append({'accession': acc, 'procedure': proc})
            # Pattern 3: Just an accession-like string
            elif is_accession_like(text_str):
                data['debug_info']['accession_candidates'].append({
                    'value': text_str,
                    'source': source,
                    'field': field,
                    'full_text': text_str
                })
                results.append({'accession': text_str, 'procedure': ''})
            return results
        
        # Helper to check if an element is a labeled field value (not an accession)
        labeled_field_markers = ['study date:', 'body part:', 'mrn:', 'ordering:', 'site group:',
                                 'site code:', 'description:', 'reason for visit:', 'modality:',
                                 'gender:', 'patient name:', 'accession']
        
        def is_labeled_field_value(elem_index):
            """Check if this element is likely a value for a labeled field (not an accession)."""
            if elem_index <= 0:
                return False
            # Check if the previous element was a label
            prev_elem = element_data[elem_index - 1]
            prev_name = prev_elem.get('name', '').lower()
            for marker in labeled_field_markers:
                if marker in prev_name:
                    return True
            return False
        
        # First pass: Look for "Current Study" labels - accession is right below
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
                        extracted = extract_accession_from_text(next_name, source=f"current_study_elem_{j}", field="name")
                        if extracted:
                            data['multiple_accessions'].extend(extracted)
                            if not data['accession']:
                                data['accession'] = extracted[0]['accession']
                                data['debug_info']['extraction_methods']['accession'] = 'Current Study label'
                                if extracted[0]['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                                    if not data['debug_info']['extraction_methods'].get('procedure'):
                                        data['debug_info']['extraction_methods']['procedure'] = 'Current Study label (from accession)'
                            break
        
        # Second pass: Look for explicit "Accession" labels
        if not data['accession']:
            for i, elem in enumerate(element_data):
                name = elem['name']
                text = elem['text']
                combined = f"{name} {text}".strip()
                
                # Accession - look for label "Accession(s):" and get next element(s)
                if 'accession' in combined.lower() and ':' in combined:
                    # Look at nearby elements for the accession value(s) - search deeper
                    for j in range(i+1, min(i+30, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name'].strip()
                        next_text = next_elem.get('text', '').strip()
                        next_combined = f"{next_name} {next_text}".strip()
                        
                        # Skip MRN values
                        if 'mrn' in next_name.lower():
                            continue
                        
                        # Try to extract from name
                        if next_name:
                            extracted = extract_accession_from_text(next_name, source=f"element_{j}", field="name")
                            if extracted:
                                data['multiple_accessions'].extend(extracted)
                                if not data['accession']:
                                    data['accession'] = extracted[0]['accession']
                                    if extracted[0]['procedure']:
                                        data['procedure'] = extracted[0]['procedure']
                                break
                        
                        # Try to extract from text
                        if next_text:
                            extracted = extract_accession_from_text(next_text, source=f"element_{j}", field="text")
                            if extracted:
                                data['multiple_accessions'].extend(extracted)
                                if not data['accession']:
                                    data['accession'] = extracted[0]['accession']
                                    if extracted[0]['procedure']:
                                        data['procedure'] = extracted[0]['procedure']
                                break
                        
                        # Try to extract from combined
                        if next_combined and next_combined != next_name and next_combined != next_text:
                            extracted = extract_accession_from_text(next_combined, source=f"element_{j}", field="combined")
                            if extracted:
                                data['multiple_accessions'].extend(extracted)
                                if not data['accession']:
                                    data['accession'] = extracted[0]['accession']
                                    if extracted[0]['procedure']:
                                        data['procedure'] = extracted[0]['procedure']
                                break
                        
                        # Also check automation_id field
                        try:
                            auto_id = next_elem['element'].element_info.automation_id if 'element' in next_elem else None
                            if auto_id:
                                extracted = extract_accession_from_text(auto_id, source=f"element_{j}", field="automation_id")
                                if extracted:
                                    data['multiple_accessions'].extend(extracted)
                                    if not data['accession']:
                                        data['accession'] = extracted[0]['accession']
                                        if extracted[0]['procedure']:
                                            data['procedure'] = extracted[0]['procedure']
                                    break
                        except:
                            pass
        
        # Third pass: Look for accession-like patterns anywhere in all elements (if not found yet)
        # This is more aggressive - searches all fields for accession patterns
        if not data['accession']:
            for i, elem in enumerate(element_data):
                name = elem['name']
                text = elem['text']
                
                # Skip MRN values explicitly
                if 'mrn' in name.lower() or 'mrn' in text.lower():
                    continue
                
                # Skip labeled field values (e.g., values after "Body Part:", "Study Date:", etc.)
                if is_labeled_field_value(i):
                    continue
                
                # Skip if it's clearly a label (but only skip the name, still check text)
                skip_name = False
                if name and (name.endswith(':') or name.lower() in ['accession', 'accessions', 'mrn', 'study date', 'modality', 'gender', 'patient name', 'patient']):
                    skip_name = True
                
                # Check name field (if not a label)
                if name and not skip_name:
                    extracted = extract_accession_from_text(name, source=f"second_pass_elem_{i}", field="name")
                    if extracted:
                        data['multiple_accessions'].extend(extracted)
                        if not data['accession']:
                            data['accession'] = extracted[0]['accession']
                            if extracted[0]['procedure']:
                                data['procedure'] = extracted[0]['procedure']
                        continue
                
                # Check text field (always check text, even if name was a label)
                if text:
                    # Skip if text looks like a label too
                    if not (text.endswith(':') or text.lower().strip() in ['accession', 'accessions', 'mrn', 'study date', 'modality', 'gender']):
                        extracted = extract_accession_from_text(text, source=f"second_pass_elem_{i}", field="text")
                        if extracted:
                            data['multiple_accessions'].extend(extracted)
                            if not data['accession']:
                                data['accession'] = extracted[0]['accession']
                                if extracted[0]['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                            continue
                
                # Check automation_id if available
                try:
                    if 'element' in elem:
                        auto_id = elem['element'].element_info.automation_id
                        if auto_id:
                            extracted = extract_accession_from_text(auto_id, source=f"second_pass_elem_{i}", field="automation_id")
                            if extracted:
                                data['multiple_accessions'].extend(extracted)
                                if not data['accession']:
                                    data['accession'] = extracted[0]['accession']
                                    if extracted[0]['procedure']:
                                        data['procedure'] = extracted[0]['procedure']
                                continue
                except:
                    pass
        
        # Fourth pass: Search all automation_ids specifically (if still not found)
        if not data['accession']:
            for i, elem in enumerate(element_data):
                auto_id = elem.get('automation_id', '').strip()
                # Skip MRN
                if 'mrn' in auto_id.lower():
                    continue
                if auto_id and len(auto_id) > 5:
                    # Check if automation_id itself looks like an accession
                    extracted = extract_accession_from_text(auto_id, source=f"automation_id_pass_elem_{i}", field="automation_id")
                    if extracted:
                        data['multiple_accessions'].extend(extracted)
                        if not data['accession']:
                            data['accession'] = extracted[0]['accession']
                            if extracted[0]['procedure']:
                                data['procedure'] = extracted[0]['procedure']
        
        # Helper to clean procedure text (remove "Description: " prefix)
        def clean_procedure(proc):
            if not proc:
                return proc
            # Remove "Description: " prefix if present
            if proc.lower().startswith('description:'):
                proc = proc[12:].strip()
            return proc
        
        # Procedure extraction: Look for "Description:" label (primary method)
        if not data['procedure']:
            for i, elem in enumerate(element_data):
                name = elem['name']
                if name and not (',' in name and '(' in name):
                    # Check for "Description:" label and get value
                    if 'description:' in name.lower():
                        # Value might be after the colon in the same element
                        if ':' in name:
                            proc_value = name.split(':', 1)[1].strip()
                            if proc_value:
                                data['procedure'] = proc_value
                                data['debug_info']['extraction_methods']['procedure'] = 'Description label (inline)'
                                break
                        # Or look at next element
                        for j in range(i+1, min(i+3, len(element_data))):
                            next_name = element_data[j]['name'].strip()
                            if next_name and not next_name.endswith(':'):
                                data['procedure'] = next_name
                                data['debug_info']['extraction_methods']['procedure'] = 'Description label (next element)'
                                break
                        break
        
    except Exception as e:
        print(f"Error extracting Mosaic data: {e}")
        import traceback
        traceback.print_exc()
    
    return data


def extract_mosaic_data_from_elements(element_list):
    """Extract study data from a list of pre-extracted elements.
    
    This is used when we've already extracted elements via multiple approaches
    and want to parse them for study data.
    """
    data = {
        'procedure': '',
        'accession': '',
        'multiple_accessions': [],
        'debug_info': {
            'accession_candidates': [],
            'extraction_methods': {
                'accession': '',
                'procedure': ''
            }
        }
    }
    
    try:
        # Convert to element_data format
        element_data = []
        for elem in element_list:
            name = elem.get('name', '').strip()
            text = elem.get('text', '').strip()
            auto_id = elem.get('automation_id', '').strip()
            source = elem.get('source', '')
            framework = elem.get('framework_id', '').strip()
            control_type = elem.get('control_type', '').strip()
            
            element_data.append({
                'name': name,
                'text': text,
                'automation_id': auto_id,
                'source': source,
                'depth': elem.get('depth', 0),
                'framework_id': framework,
                'control_type': control_type
            })
        
        # Helper function to check if string looks like accession
        def is_accession_like(s):
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
                           # Mosaic labeled fields - these are NOT accessions
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
            # MRN values should NOT be treated as accessions
            if 'mrn' in lower_s:
                return False
            
            # Reject common body parts and anatomy terms that might match patterns
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
        
        # Helper function to extract accession from text
        def extract_accession_from_text(text_str, source="unknown", field="unknown"):
            if not text_str:
                return []
            results = []
            # Pattern 1: Multiple accessions "ACC1 (PROC1), ACC2 (PROC2)"
            if ',' in text_str and '(' in text_str:
                parts = text_str.split(',')
                for part in parts:
                    part = part.strip()
                    if '(' in part and ')' in part:
                        acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', part)
                        if acc_match:
                            acc = acc_match.group(1).strip()
                            proc = acc_match.group(2).strip()
                            if is_accession_like(acc):
                                data['debug_info']['accession_candidates'].append({
                                    'value': acc, 'source': source, 'field': field, 'full_text': part
                                })
                                results.append({'accession': acc, 'procedure': proc})
                    elif is_accession_like(part):
                        data['debug_info']['accession_candidates'].append({
                            'value': part, 'source': source, 'field': field, 'full_text': part
                        })
                        results.append({'accession': part, 'procedure': ''})
            # Pattern 2: Single with procedure "ACC (PROC)"
            elif '(' in text_str and ')' in text_str:
                acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', text_str)
                if acc_match:
                    acc = acc_match.group(1).strip()
                    proc = acc_match.group(2).strip()
                    if is_accession_like(acc):
                        data['debug_info']['accession_candidates'].append({
                            'value': acc, 'source': source, 'field': field, 'full_text': text_str
                        })
                        results.append({'accession': acc, 'procedure': proc})
            # Pattern 3: Just accession-like string
            elif is_accession_like(text_str):
                data['debug_info']['accession_candidates'].append({
                    'value': text_str, 'source': source, 'field': field, 'full_text': text_str
                })
                results.append({'accession': text_str, 'procedure': ''})
            return results
        
        # Helper to check if an element is a labeled field value (not an accession)
        labeled_field_markers = ['study date:', 'body part:', 'mrn:', 'ordering:', 'site group:',
                                 'site code:', 'description:', 'reason for visit:', 'modality:',
                                 'gender:', 'patient name:', 'accession']
        
        def is_labeled_field_value(elem_index):
            """Check if this element is likely a value for a labeled field (not an accession)."""
            if elem_index <= 0:
                return False
            # Check if the previous element was a label
            prev_elem = element_data[elem_index - 1]
            prev_name = prev_elem.get('name', '').lower()
            for marker in labeled_field_markers:
                if marker in prev_name:
                    return True
            return False
        
        # First pass: Look for "Current Study" labels - accession is right below
        for i, elem in enumerate(element_data):
            name = elem['name']
            text = elem['text']
            combined = f"{name} {text}".strip().lower()
            
            if 'current study' in combined:
                # Look at nearby elements for the accession (should be right below)
                for j in range(i+1, min(i+15, len(element_data))):
                    next_elem = element_data[j]
                    next_name = next_elem['name'].strip()
                    next_source = next_elem.get('source', 'unknown')
                    
                    # Skip if it looks like a label
                    if next_name and not next_name.endswith(':') and 'mrn' not in next_name.lower():
                        extracted = extract_accession_from_text(next_name, source=f"current_study_elem_{j}", field="name")
                        if extracted:
                            data['multiple_accessions'].extend(extracted)
                            if not data['accession']:
                                data['accession'] = extracted[0]['accession']
                                data['debug_info']['extraction_methods']['accession'] = 'Current Study label'
                                if extracted[0]['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                                    if not data['debug_info']['extraction_methods'].get('procedure'):
                                        data['debug_info']['extraction_methods']['procedure'] = 'Current Study label (from accession)'
                            break
        
        # Second pass: Look for explicit "Accession" labels
        if not data['accession']:
            for i, elem in enumerate(element_data):
                name = elem['name']
                text = elem['text']
                auto_id = elem['automation_id']
                source = elem.get('source', 'unknown')
                
                combined = f"{name} {text}".strip()
                
                if 'accession' in combined.lower() and ':' in combined:
                    for j in range(i+1, min(i+30, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name'].strip()
                        next_text = next_elem.get('text', '').strip()
                        next_auto_id = next_elem.get('automation_id', '').strip()
                        next_source = next_elem.get('source', 'unknown')
                        
                        # Skip MRN values
                        if 'mrn' in next_name.lower():
                            continue
                        
                        # Try name
                    if next_name:
                        extracted = extract_accession_from_text(next_name, source=f"{next_source}_elem_{j}", field="name")
                        if extracted:
                            data['multiple_accessions'].extend(extracted)
                            if not data['accession']:
                                data['accession'] = extracted[0]['accession']
                                data['debug_info']['extraction_methods']['accession'] = 'Accession label'
                                if extracted[0]['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                                    if not data['debug_info']['extraction_methods'].get('procedure'):
                                        data['debug_info']['extraction_methods']['procedure'] = 'Accession label (from accession)'
                                break
                        
                        # Try text
                        if next_text and next_text != next_name:
                            extracted = extract_accession_from_text(next_text, source=f"{next_source}_elem_{j}", field="text")
                            if extracted:
                                data['multiple_accessions'].extend(extracted)
                                if not data['accession']:
                                    data['accession'] = extracted[0]['accession']
                                    if extracted[0]['procedure']:
                                        data['procedure'] = extracted[0]['procedure']
                                break
                        
                        # Try automation_id
                        if next_auto_id:
                            extracted = extract_accession_from_text(next_auto_id, source=f"{next_source}_elem_{j}", field="automation_id")
                            if extracted:
                                data['multiple_accessions'].extend(extracted)
                                if not data['accession']:
                                    data['accession'] = extracted[0]['accession']
                                    if extracted[0]['procedure']:
                                        data['procedure'] = extracted[0]['procedure']
                                break
        
        # Third pass: Look at Chrome/WebView2 Text elements specifically
        # These contain the actual content from the web view
        if not data['accession']:
            for i, elem in enumerate(element_data):
                framework = elem.get('framework_id', '').lower()
                control_type = elem.get('control_type', '').lower()
                name = elem['name']
                
                # Skip MRN values
                if 'mrn' in name.lower():
                    continue
                
                # Focus on Chrome framework Text elements
                if framework == 'chrome' and 'text' in control_type:
                    if name and len(name) >= 8:
                        extracted = extract_accession_from_text(name, source=f"chrome_text_elem_{i}", field="name")
                        if extracted:
                            data['multiple_accessions'].extend(extracted)
                            if not data['accession']:
                                data['accession'] = extracted[0]['accession']
                                data['debug_info']['extraction_methods']['accession'] = 'Fallback: Chrome Text elements'
                                if extracted[0]['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                                    if not data['debug_info']['extraction_methods'].get('procedure'):
                                        data['debug_info']['extraction_methods']['procedure'] = 'Pass 3: Chrome Text elements (from accession)'
                                break
        
        # Fourth pass: Pattern matching across all elements
        if not data['accession']:
            for i, elem in enumerate(element_data):
                name = elem['name']
                text = elem['text']
                auto_id = elem['automation_id']
                source = elem.get('source', 'unknown')
                
                # Skip MRN values explicitly
                if 'mrn' in name.lower() or 'mrn' in text.lower():
                    continue
                
                # Skip labeled field values (e.g., values after "Body Part:", "Study Date:", etc.)
                if is_labeled_field_value(i):
                    continue
                
                # Skip labels
                skip_name = name and (name.endswith(':') or name.lower() in ['accession', 'accessions', 'mrn', 'study date', 'modality', 'gender'])
                
                if name and not skip_name:
                    extracted = extract_accession_from_text(name, source=f"{source}_elem_{i}", field="name")
                    if extracted:
                        data['multiple_accessions'].extend(extracted)
                        if not data['accession']:
                            data['accession'] = extracted[0]['accession']
                            data['debug_info']['extraction_methods']['accession'] = 'Fallback: Pattern matching'
                            if extracted[0]['procedure']:
                                data['procedure'] = extracted[0]['procedure']
                                if not data['debug_info']['extraction_methods'].get('procedure'):
                                    data['debug_info']['extraction_methods']['procedure'] = 'Pass 4: Pattern matching (from accession)'
                        continue
                
                if text and text != name:
                    if not (text.endswith(':') or text.lower().strip() in ['accession', 'accessions', 'mrn', 'study date', 'modality', 'gender']):
                        extracted = extract_accession_from_text(text, source=f"{source}_elem_{i}", field="text")
                        if extracted:
                            data['multiple_accessions'].extend(extracted)
                            if not data['accession']:
                                data['accession'] = extracted[0]['accession']
                                if extracted[0]['procedure']:
                                    data['procedure'] = extracted[0]['procedure']
                            continue
                
                if auto_id:
                    extracted = extract_accession_from_text(auto_id, source=f"{source}_elem_{i}", field="automation_id")
                    if extracted:
                        data['multiple_accessions'].extend(extracted)
                        if not data['accession']:
                            data['accession'] = extracted[0]['accession']
                            if extracted[0]['procedure']:
                                data['procedure'] = extracted[0]['procedure']
                        continue
        
        # Procedure extraction: Look for "Description:" label (primary method)
        if not data['procedure']:
            for i, elem in enumerate(element_data):
                name = elem['name']
                if name and not (',' in name and '(' in name):
                    # Check for "Description:" label and get value
                    if 'description:' in name.lower():
                        # Value might be after the colon in the same element
                        if ':' in name:
                            proc_value = name.split(':', 1)[1].strip()
                            if proc_value:
                                data['procedure'] = proc_value
                                data['debug_info']['extraction_methods']['procedure'] = 'Description label (inline)'
                                break
                        # Or look at next element
                        for j in range(i+1, min(i+3, len(element_data))):
                            next_name = element_data[j]['name'].strip()
                            if next_name and not next_name.endswith(':'):
                                data['procedure'] = next_name
                                data['debug_info']['extraction_methods']['procedure'] = 'Description label (next element)'
                                break
                        break
    
    except Exception as e:
        print(f"Error extracting data from elements: {e}")
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
        
        # Add framework indicator (Chrome = WebView2 content)
        framework = elem.get('framework_id', '')
        if framework:
            line += f"({framework}) "
        
        if elem['automation_id']:
            line += f"ID='{elem['automation_id']}' "
        if elem.get('control_type'):
            line += f"Type={elem['control_type']} "
        if elem['name']:
            line += f"Name='{elem['name']}' "
        if elem['text'] and elem['text'] != elem['name']:
            line += f"Text='{elem['text']}'"
        
        # Add to listbox
        left_listbox.insert(tk.END, line)
        # Store element reference by index
        element_list_items.append(elem)


def refresh_elements():
    """Refresh the element list using multiple approaches to find all elements."""
    global all_elements
    
    left_listbox.delete(0, tk.END)
    left_header.config(text="Searching for Mosaic window...")
    root.update()
    
    main_window = find_mosaic_window()
    
    if not main_window:
        left_header.config(text="ERROR: Mosaic window (Info Hub or Reporting) not found! Please make sure Mosaic is running.")
        all_elements = []
        return
    
    try:
        window_text = main_window.window_text()
        
        # Show extraction progress in right pane
        right_text.config(state=tk.NORMAL)
        right_text.delete(1.0, tk.END)
        right_text.insert(tk.END, "=" * 70 + "\n")
        right_text.insert(tk.END, "ELEMENT EXTRACTION\n")
        right_text.insert(tk.END, "=" * 70 + "\n\n")
        right_text.insert(tk.END, f"Window: {window_text}\n\n")
        root.update()
        
        all_found_elements = []
        
        # =====================================================================
        # Extract elements using main window descendants (working method)
        # =====================================================================
        left_header.config(text="Extracting elements from main window...")
        root.update()
        
        descendants_elements = get_all_elements_descendants(main_window, max_elements=5000)
        for e in descendants_elements:
            e['source'] = 'descendants'
        right_text.insert(tk.END, f"Elements found: {len(descendants_elements)}\n")
        root.update()
        
        # Filter to meaningful elements
        meaningful = [e for e in descendants_elements if 
                      (e.get('name', '').strip() and len(e.get('name', '').strip()) > 1) or 
                      (e.get('automation_id', '').strip() and len(e.get('automation_id', '').strip()) > 1) or
                      (e.get('text', '').strip() and len(e.get('text', '').strip()) > 1)]
        
        all_elements = meaningful
        
        right_text.insert(tk.END, f"Meaningful elements: {len(meaningful)}\n")
        right_text.insert(tk.END, "\n" + "=" * 70 + "\n")
        
        # =====================================================================
        # Now extract data using combined elements
        # =====================================================================
        right_text.insert(tk.END, "\nEXTRACTED DATA:\n")
        right_text.insert(tk.END, "-" * 70 + "\n\n")
        
        # Create a fake webview-like element data structure for extract_mosaic_data
        # by building all_elements list
        extracted_data = extract_mosaic_data_from_elements(meaningful)
        
        debug_info = extracted_data.get('debug_info', {})
        
        # Show debug info if available
        if debug_info:
            candidates = debug_info.get('accession_candidates', [])
            if candidates:
                right_text.insert(tk.END, f"Found {len(candidates)} potential accession candidates:\n")
                for idx, cand in enumerate(candidates[:20], 1):
                    right_text.insert(tk.END, f"  {idx}. '{cand.get('value', '')}' (from: {cand.get('source', 'unknown')}, field: {cand.get('field', 'unknown')})\n")
                right_text.insert(tk.END, "\n")
            else:
                right_text.insert(tk.END, "No accession candidates found.\n\n")
        
        # Show only accession and procedure with extraction methods
        methods = debug_info.get('extraction_methods', {})
        
        proc = extracted_data.get('procedure', '')
        proc_method = methods.get('procedure', 'Not found')
        if proc:
            right_text.insert(tk.END, f"Procedure: {proc}\n")
            right_text.insert(tk.END, f"  [Method: {proc_method}]\n")
        else:
            right_text.insert(tk.END, f"Procedure: NOT FOUND\n")
        
        acc = extracted_data.get('accession', '')
        acc_method = methods.get('accession', 'Not found')
        if acc:
            right_text.insert(tk.END, f"\nAccession: {acc}\n")
            right_text.insert(tk.END, f"  [Method: {acc_method}]\n")
        else:
            right_text.insert(tk.END, f"\nAccession: NOT FOUND - Check elements list\n")
        right_text.config(state=tk.DISABLED)
        
        # Display elements in left pane
        display_elements()
        
    except Exception as e:
        left_header.config(text=f"ERROR: {str(e)}")
        import traceback
        print(traceback.format_exc())
        right_text.config(state=tk.NORMAL)
        right_text.delete(1.0, tk.END)
        right_text.insert(tk.END, f"ERROR: {str(e)}\n\n{traceback.format_exc()}")
        right_text.config(state=tk.DISABLED)
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
root.title("Mosaic UI Elements Viewer")
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
