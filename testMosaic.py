"""
Test script to display all UI elements from Mosaic Info Hub interface.
This shows all displayable items in the Mosaic Info Hub window.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from pywinauto import Desktop
from pywinauto.findwindows import ElementNotFoundError
import re


def find_mosaic_window():
    """Find Mosaic Info Hub window - it's a WinForms app with WebView2."""
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
        'multiple_accessions': []  # List of {accession, procedure} dicts
    }
    
    try:
        # Get all elements from WebView2 with deep scan
        all_elements = get_all_elements(webview_element, max_depth=20)
        
        # Convert to list of (element, name, text) tuples for easier searching
        element_data = []
        for elem in all_elements:
            name = elem.get('name', '').strip()
            text = elem.get('text', '').strip()
            if name or text:
                element_data.append({
                    'element': elem.get('element'),
                    'name': name,
                    'text': text,
                    'depth': elem.get('depth', 0)
                })
        
        # Find elements by label and get their values
        for i, elem in enumerate(element_data):
            name = elem['name']
            text = elem['text']
            
            # Check if this element itself contains multiple accessions (not just a label)
            # Format: "ACCESSION1 (PROC1), ACCESSION2 (PROC2)"
            if name and ',' in name and '(' in name and not 'accession' in name.lower():
                # This might be the accession element itself with multiple accessions
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
            
            # Patient name - usually appears as a name (capital letters, spaces)
            if not data['patient_name']:
                # Look for pattern like "FIRST MIDDLE LAST" (all caps, multiple words)
                if name and len(name.split()) >= 2 and name.isupper():
                    # Make sure it's not a label (doesn't end with :)
                    if not name.endswith(':'):
                        data['patient_name'] = name
            
            # Procedure - look for CT/MR/XR etc. procedures (but skip if it's part of accession format)
            if not data['procedure'] and not (',' in name and '(' in name):
                if name:
                    # Check if it looks like a procedure description
                    proc_keywords = ['CT ', 'MR ', 'XR ', 'US ', 'NM ', 'PET', 'MRI', 'ULTRASOUND']
                    if any(keyword in name.upper() for keyword in proc_keywords):
                        data['procedure'] = name
            
            # Accession - look for label "Accession(s):" and get next element(s)
            if 'accession' in name.lower() and ':' in name:
                # Look at nearby elements for the accession value(s)
                for j in range(i+1, min(i+10, len(element_data))):
                    next_elem = element_data[j]
                    next_name = next_elem['name'].strip()
                    next_text = next_elem.get('text', '').strip()
                    
                    # Check if it contains multiple accessions in format:
                    # "ACCESSION1 (PROC1), ACCESSION2 (PROC2)"
                    if next_name and ',' in next_name and '(' in next_name:
                        # Parse multiple accessions
                        accession_parts = next_name.split(',')
                        for part in accession_parts:
                            part = part.strip()
                            # Extract accession and procedure: "ACC (PROC)" or "ACC"
                            if '(' in part and ')' in part:
                                # Has procedure in parentheses
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
                                # Just accession, no procedure
                                if part and len(part) > 5:
                                    data['multiple_accessions'].append({
                                        'accession': part,
                                        'procedure': ''
                                    })
                        
                        # Set first accession as primary
                        if data['multiple_accessions']:
                            data['accession'] = data['multiple_accessions'][0]['accession']
                            if not data['procedure'] and data['multiple_accessions'][0]['procedure']:
                                data['procedure'] = data['multiple_accessions'][0]['procedure']
                        break
                    # Single accession - alphanumeric, no spaces
                    elif next_name and len(next_name) > 5 and ' ' not in next_name and '(' not in next_name:
                        data['accession'] = next_name
                        break
            
            # MRN - look for label "MRN:" and get next element
            if 'mrn' in name.lower() and ':' in name:
                for j in range(i+1, min(i+5, len(element_data))):
                    next_elem = element_data[j]
                    next_name = next_elem['name'].strip()
                    if next_name and len(next_name) > 5:
                        data['mrn'] = next_name
                        break
            
            # Study Date - look for label "Study Date:" and get next element
            if 'study date' in name.lower() and ':' in name:
                for j in range(i+1, min(i+5, len(element_data))):
                    next_elem = element_data[j]
                    next_name = next_elem['name'].strip()
                    # Date format MM/DD/YYYY
                    if next_name and '/' in next_name:
                        data['study_date'] = next_name.split()[0] if ' ' in next_name else next_name
                        break
            
            # Modality - look for label "Modality:" and get next element
            if 'modality' in name.lower() and ':' in name:
                for j in range(i+1, min(i+5, len(element_data))):
                    next_elem = element_data[j]
                    next_name = next_elem['name'].strip()
                    if next_name:
                        data['modality'] = next_name
                        break
            
            # Patient Class/Gender - look for "Male" or "Female"
            if not data['patient_class']:
                if name.upper() in ['MALE', 'FEMALE']:
                    data['patient_class'] = name
                elif 'gender' in name.lower() and ':' in name:
                    # Look for next element with Male/Female
                    for j in range(i+1, min(i+3, len(element_data))):
                        next_elem = element_data[j]
                        next_name = next_elem['name'].strip().upper()
                        if next_name in ['MALE', 'FEMALE']:
                            data['patient_class'] = next_name
                            break
        
    except Exception as e:
        print(f"Error extracting Mosaic data: {e}")
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
    
    left_listbox.delete(0, tk.END)
    left_header.config(text="Searching for Mosaic Info Hub window...")
    root.update()
    
    main_window = find_mosaic_window()
    
    if not main_window:
        left_header.config(text="ERROR: Mosaic Info Hub window not found! Please make sure Mosaic Info Hub is running.")
        all_elements = []
        return
    
    try:
        window_text = main_window.window_text()
        left_header.config(text=f"Found Mosaic Info Hub: {window_text} - Looking for WebView2...")
        root.update()
        
        # Find the WebView2 control
        webview = find_webview_element(main_window)
        
        if webview:
            left_header.config(text=f"Found WebView2 control - Extracting data...")
            root.update()
            
            # Extract structured data from WebView2
            extracted_data = extract_mosaic_data(webview)
            
            # Show extracted data in right pane
            right_text.config(state=tk.NORMAL)
            right_text.delete(1.0, tk.END)
            right_text.insert(tk.END, "=" * 70 + "\n")
            right_text.insert(tk.END, "EXTRACTED MOSAIC DATA\n")
            right_text.insert(tk.END, "=" * 70 + "\n\n")
            
            # Show procedures and accessions - different format for single vs multiple
            if extracted_data.get('multiple_accessions') and len(extracted_data['multiple_accessions']) > 1:
                # Multiple accessions: show all procedures and all accessions
                procedures = [acc_data.get('procedure', '') for acc_data in extracted_data['multiple_accessions'] if acc_data.get('procedure')]
                if procedures:
                    right_text.insert(tk.END, f"Procedures ({len(procedures)}):\n")
                    for idx, proc in enumerate(procedures, 1):
                        right_text.insert(tk.END, f"  {idx}. {proc}\n")
                
                right_text.insert(tk.END, f"\nAccessions ({len(extracted_data['multiple_accessions'])}):\n")
                for idx, acc_data in enumerate(extracted_data['multiple_accessions'], 1):
                    proc = acc_data.get('procedure', '')
                    if proc:
                        right_text.insert(tk.END, f"  {idx}. {acc_data['accession']} ({proc})\n")
                    else:
                        right_text.insert(tk.END, f"  {idx}. {acc_data['accession']}\n")
            else:
                # Single accession: show simple format
                right_text.insert(tk.END, f"Procedure: {extracted_data['procedure']}\n")
                right_text.insert(tk.END, f"Accession: {extracted_data['accession']}\n")
            
            right_text.insert(tk.END, f"\nMRN: {extracted_data['mrn']}\n")
            right_text.insert(tk.END, f"Patient Name: {extracted_data['patient_name']}\n")
            right_text.insert(tk.END, f"Patient Class/Gender: {extracted_data['patient_class']}\n")
            right_text.insert(tk.END, f"Study Date: {extracted_data['study_date']}\n")
            right_text.insert(tk.END, f"Modality: {extracted_data['modality']}\n")
            right_text.config(state=tk.DISABLED)
            
            # Get WebView2 elements for display (with meaningful data only)
            left_header.config(text=f"Scanning WebView2 elements...")
            root.update()
            webview_elements = get_all_elements(webview, max_depth=20)
            
            # Filter to only show elements with meaningful data
            meaningful = [e for e in webview_elements if (e.get('name', '').strip() and len(e.get('name', '').strip()) > 2) or 
                                                          (e.get('automation_id', '').strip()) or
                                                          (e.get('text', '').strip() and len(e.get('text', '').strip()) > 2)]
            
            all_elements = meaningful
            left_header.config(text=f"Found {len(meaningful)} elements with meaningful data (of {len(webview_elements)} total)")
            
            # Display all elements
            display_elements()
        else:
            left_header.config(text=f"Found window but could not locate WebView2 control - Scanning main window only...")
            root.update()
            all_elements = get_all_elements(main_window, max_depth=10)
            left_header.config(text=f"Found {len(all_elements)} elements from main window")
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
root.title("Mosaic Info Hub UI Elements Viewer")
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
