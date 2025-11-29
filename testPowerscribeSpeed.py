"""
PowerScribe Speed Test - Compare different UI automation approaches

Button 1: Current approach (pywinauto descendants iteration)
Button 2: Direct UIA via comtypes (bypassing pywinauto)
Button 3: UIA Event Subscription (event-driven)
Button 4: child_window() direct lookup (pywinauto optimized)
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import time
import threading

# =============================================================================
# APPROACH 1: Current pywinauto approach (descendants iteration)
# =============================================================================

def approach1_current():
    """Current approach - pywinauto with descendants() iteration."""
    from pywinauto import Desktop
    
    start = time.perf_counter()
    result = {
        'accession': '',
        'procedure': '',
        'patient_class': '',
        'window_found': False,
        'error': None
    }
    
    try:
        # Find window
        t1 = time.perf_counter()
        desktop = Desktop(backend="uia")
        window = None
        
        for title in ["PowerScribe 360 | Reporting", "PowerScribe 360", "PowerScribe 360 - Reporting"]:
            try:
                windows = desktop.windows(title=title, visible_only=True)
                if windows:
                    window = windows[0]
                    break
            except:
                continue
        
        window_time = time.perf_counter() - t1
        
        if not window:
            result['error'] = "PowerScribe window not found"
            return result, time.perf_counter() - start, {'window': window_time}
        
        result['window_found'] = True
        
        # Get descendants
        t2 = time.perf_counter()
        descendants_list = []
        try:
            descendants_gen = window.descendants()
            count = 0
            for elem in descendants_gen:
                descendants_list.append(elem)
                count += 1
                if count >= 1000:
                    break
        except Exception as e:
            result['error'] = f"descendants() failed: {e}"
            return result, time.perf_counter() - start, {'window': window_time}
        
        descendants_time = time.perf_counter() - t2
        
        # Find elements by automation ID
        t3 = time.perf_counter()
        target_ids = ["labelAccession", "labelProcDescription", "labelPatientClass"]
        found = {}
        
        for elem in descendants_list:
            try:
                auto_id = elem.element_info.automation_id
                if auto_id in target_ids:
                    text = elem.window_text() or ""
                    found[auto_id] = text.strip()
                    if len(found) == len(target_ids):
                        break
            except:
                continue
        
        read_time = time.perf_counter() - t3
        
        result['accession'] = found.get('labelAccession', '')
        result['procedure'] = found.get('labelProcDescription', '')
        result['patient_class'] = found.get('labelPatientClass', '')
        
        total_time = time.perf_counter() - start
        timings = {
            'window': window_time,
            'descendants': descendants_time,
            'read_text': read_time,
            'total': total_time
        }
        
        return result, total_time, timings
        
    except Exception as e:
        result['error'] = str(e)
        return result, time.perf_counter() - start, {}


# =============================================================================
# APPROACH 2: Direct UIA via comtypes (bypassing pywinauto abstraction)
# =============================================================================

def approach2_direct_uia():
    """Direct UIA via comtypes - lower level access."""
    import comtypes.client
    from comtypes import COMError
    
    start = time.perf_counter()
    result = {
        'accession': '',
        'procedure': '',
        'patient_class': '',
        'window_found': False,
        'error': None
    }
    
    try:
        # Initialize UIA
        t1 = time.perf_counter()
        uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}")  # CUIAutomation
        
        # Get UIA interfaces
        IUIAutomation = uia.QueryInterface(comtypes.client.GetModule("UIAutomationCore.dll").IUIAutomation)
        
        init_time = time.perf_counter() - t1
        
        # Find PowerScribe window
        t2 = time.perf_counter()
        root = IUIAutomation.GetRootElement()
        
        # Create condition for window name containing "PowerScribe"
        condition = IUIAutomation.CreatePropertyCondition(
            30005,  # UIA_NamePropertyId
            "PowerScribe 360 | Reporting"
        )
        
        ps_window = root.FindFirst(2, condition)  # TreeScope_Children = 2
        
        if not ps_window:
            # Try alternate names
            for name in ["PowerScribe 360", "PowerScribe 360 - Reporting"]:
                condition = IUIAutomation.CreatePropertyCondition(30005, name)
                ps_window = root.FindFirst(2, condition)
                if ps_window:
                    break
        
        window_time = time.perf_counter() - t2
        
        if not ps_window:
            result['error'] = "PowerScribe window not found"
            return result, time.perf_counter() - start, {'init': init_time, 'window': window_time}
        
        result['window_found'] = True
        
        # Find elements by automation ID using UIA's FindFirst with condition
        t3 = time.perf_counter()
        
        target_ids = {
            "labelAccession": 'accession',
            "labelProcDescription": 'procedure',
            "labelPatientClass": 'patient_class'
        }
        
        for auto_id, result_key in target_ids.items():
            try:
                # Create condition for automation ID
                condition = IUIAutomation.CreatePropertyCondition(
                    30011,  # UIA_AutomationIdPropertyId
                    auto_id
                )
                element = ps_window.FindFirst(7, condition)  # TreeScope_Descendants = 4, TreeScope_Subtree = 7
                
                if element:
                    # Get the Name property (which usually contains the text)
                    name = element.CurrentName
                    result[result_key] = name.strip() if name else ""
            except Exception as e:
                pass
        
        find_time = time.perf_counter() - t3
        
        total_time = time.perf_counter() - start
        timings = {
            'init': init_time,
            'window': window_time,
            'find_elements': find_time,
            'total': total_time
        }
        
        return result, total_time, timings
        
    except Exception as e:
        import traceback
        result['error'] = f"{str(e)}\n{traceback.format_exc()}"
        return result, time.perf_counter() - start, {}


# =============================================================================
# APPROACH 3: Limited descendants search (faster than full scan)
# =============================================================================

def approach3_limited_descendants():
    """Limited descendants - search only first 300 elements."""
    from pywinauto import Desktop
    
    start = time.perf_counter()
    result = {
        'accession': '',
        'procedure': '',
        'patient_class': '',
        'window_found': False,
        'elements_searched': 0,
        'error': None
    }
    
    try:
        # Find window
        t1 = time.perf_counter()
        desktop = Desktop(backend="uia")
        window = None
        
        for title in ["PowerScribe 360 | Reporting", "PowerScribe 360", "PowerScribe 360 - Reporting"]:
            try:
                windows = desktop.windows(title=title, visible_only=True)
                if windows:
                    window = windows[0]
                    break
            except:
                continue
        
        window_time = time.perf_counter() - t1
        
        if not window:
            result['error'] = "PowerScribe window not found"
            return result, time.perf_counter() - start, {'window': window_time}
        
        result['window_found'] = True
        
        # Get LIMITED descendants - only first 300 elements (much faster)
        t2 = time.perf_counter()
        descendants_list = []
        try:
            descendants_gen = window.descendants()
            count = 0
            for elem in descendants_gen:
                descendants_list.append(elem)
                count += 1
                if count >= 300:  # LIMIT to 300 instead of 1000
                    break
        except Exception as e:
            result['error'] = f"descendants() failed: {e}"
            return result, time.perf_counter() - start, {'window': window_time}
        
        result['elements_searched'] = len(descendants_list)
        descendants_time = time.perf_counter() - t2
        
        # Find elements by automation ID
        t3 = time.perf_counter()
        target_ids = ["labelAccession", "labelProcDescription", "labelPatientClass"]
        found = {}
        
        for elem in descendants_list:
            try:
                auto_id = elem.element_info.automation_id
                if auto_id in target_ids:
                    text = elem.window_text() or ""
                    found[auto_id] = text.strip()
                    if len(found) == len(target_ids):
                        break
            except:
                continue
        
        read_time = time.perf_counter() - t3
        
        result['accession'] = found.get('labelAccession', '')
        result['procedure'] = found.get('labelProcDescription', '')
        result['patient_class'] = found.get('labelPatientClass', '')
        
        total_time = time.perf_counter() - start
        timings = {
            'window': window_time,
            'descendants_300': descendants_time,
            'read_text': read_time,
            'total': total_time,
            'note': f"Searched {len(descendants_list)} elements (limited to 300)"
        }
        
        return result, total_time, timings
        
    except Exception as e:
        import traceback
        result['error'] = f"{str(e)}\n{traceback.format_exc()}"
        return result, time.perf_counter() - start, {}


# =============================================================================
# APPROACH 4: Recursive children search (faster than descendants)
# =============================================================================

def _find_by_auto_id_recursive(element, target_ids, found, max_depth=10, current_depth=0):
    """Recursively search children for elements by automation ID."""
    if current_depth > max_depth or len(found) == len(target_ids):
        return
    
    try:
        children = element.children()
        for child in children:
            try:
                auto_id = child.element_info.automation_id
                if auto_id and auto_id in target_ids and auto_id not in found:
                    text = child.window_text() or ""
                    found[auto_id] = text.strip()
                    if len(found) == len(target_ids):
                        return
            except:
                pass
            
            # Recurse into children
            _find_by_auto_id_recursive(child, target_ids, found, max_depth, current_depth + 1)
            if len(found) == len(target_ids):
                return
    except:
        pass


def approach4_recursive_children():
    """Recursive children search - faster than full descendants."""
    from pywinauto import Desktop
    
    start = time.perf_counter()
    result = {
        'accession': '',
        'procedure': '',
        'patient_class': '',
        'window_found': False,
        'error': None
    }
    
    try:
        # Find window
        t1 = time.perf_counter()
        desktop = Desktop(backend="uia")
        window = None
        
        for title in ["PowerScribe 360 | Reporting", "PowerScribe 360", "PowerScribe 360 - Reporting"]:
            try:
                windows = desktop.windows(title=title, visible_only=True)
                if windows:
                    window = windows[0]
                    break
            except:
                continue
        
        window_time = time.perf_counter() - t1
        
        if not window:
            result['error'] = "PowerScribe window not found"
            return result, time.perf_counter() - start, {'window': window_time}
        
        result['window_found'] = True
        
        # Recursive children search - faster because it doesn't build full list
        t2 = time.perf_counter()
        
        target_ids = {"labelAccession", "labelProcDescription", "labelPatientClass"}
        found = {}
        
        _find_by_auto_id_recursive(window, target_ids, found, max_depth=15)
        
        search_time = time.perf_counter() - t2
        
        result['accession'] = found.get('labelAccession', '')
        result['procedure'] = found.get('labelProcDescription', '')
        result['patient_class'] = found.get('labelPatientClass', '')
        
        total_time = time.perf_counter() - start
        timings = {
            'window': window_time,
            'recursive_search': search_time,
            'total': total_time,
            'note': f"Found {len(found)}/3 elements using recursive children search (max depth 15)"
        }
        
        return result, total_time, timings
        
    except Exception as e:
        import traceback
        result['error'] = f"{str(e)}\n{traceback.format_exc()}"
        return result, time.perf_counter() - start, {}


# =============================================================================
# GUI Application
# =============================================================================

class PowerScribeSpeedTest:
    def __init__(self, root):
        self.root = root
        self.root.title("PowerScribe Speed Test - Compare UI Automation Approaches")
        self.root.geometry("900x700")
        
        self.create_ui()
    
    def create_ui(self):
        # Title
        title_label = ttk.Label(self.root, text="PowerScribe UI Automation Speed Comparison", 
                               font=("Arial", 14, "bold"))
        title_label.pack(pady=10)
        
        # Description
        desc_text = """Compare different approaches to reading PowerScribe UI elements.
Click each button to test that approach and see timing breakdown."""
        desc_label = ttk.Label(self.root, text=desc_text, font=("Arial", 10))
        desc_label.pack(pady=5)
        
        # Button frame
        button_frame = ttk.Frame(self.root)
        button_frame.pack(pady=10, fill=tk.X, padx=20)
        
        # Approach buttons
        self.btn1 = ttk.Button(button_frame, text="1. Current\n(descendants)", 
                              command=lambda: self.run_test(1), width=20)
        self.btn1.pack(side=tk.LEFT, padx=5, expand=True)
        
        self.btn2 = ttk.Button(button_frame, text="2. Direct UIA\n(comtypes)", 
                              command=lambda: self.run_test(2), width=20)
        self.btn2.pack(side=tk.LEFT, padx=5, expand=True)
        
        self.btn3 = ttk.Button(button_frame, text="3. Limited\n(300 elements)", 
                              command=lambda: self.run_test(3), width=20)
        self.btn3.pack(side=tk.LEFT, padx=5, expand=True)
        
        self.btn4 = ttk.Button(button_frame, text="4. Recursive\n(children search)", 
                              command=lambda: self.run_test(4), width=20)
        self.btn4.pack(side=tk.LEFT, padx=5, expand=True)
        
        # Run All button
        run_all_frame = ttk.Frame(self.root)
        run_all_frame.pack(pady=5)
        
        self.run_all_btn = ttk.Button(run_all_frame, text="Run All Tests", 
                                     command=self.run_all_tests, width=30)
        self.run_all_btn.pack()
        
        # Results area
        results_frame = ttk.LabelFrame(self.root, text="Results", padding=10)
        results_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        
        self.results_text = scrolledtext.ScrolledText(results_frame, font=("Consolas", 10), 
                                                      wrap=tk.WORD, height=25)
        self.results_text.pack(fill=tk.BOTH, expand=True)
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready - Click a button to test an approach")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        
        # Initial message
        self.results_text.insert(tk.END, "=" * 70 + "\n")
        self.results_text.insert(tk.END, "POWERSCRIBE SPEED TEST\n")
        self.results_text.insert(tk.END, "=" * 70 + "\n\n")
        self.results_text.insert(tk.END, "Approaches being tested:\n\n")
        self.results_text.insert(tk.END, "1. CURRENT (descendants 1000)\n")
        self.results_text.insert(tk.END, "   - Uses pywinauto's descendants() to iterate up to 1000 elements\n")
        self.results_text.insert(tk.END, "   - Searches for elements by automation ID\n")
        self.results_text.insert(tk.END, "   - This is what RVUCounter currently uses\n\n")
        self.results_text.insert(tk.END, "2. DIRECT UIA (comtypes)\n")
        self.results_text.insert(tk.END, "   - Uses UI Automation COM interfaces directly\n")
        self.results_text.insert(tk.END, "   - Bypasses pywinauto's abstraction layer\n")
        self.results_text.insert(tk.END, "   - Uses FindFirst with property conditions\n\n")
        self.results_text.insert(tk.END, "3. LIMITED (descendants 300)\n")
        self.results_text.insert(tk.END, "   - Same as approach 1, but limited to 300 elements\n")
        self.results_text.insert(tk.END, "   - Faster if elements are found early in the tree\n")
        self.results_text.insert(tk.END, "   - May miss elements if they're beyond position 300\n\n")
        self.results_text.insert(tk.END, "4. RECURSIVE (children search)\n")
        self.results_text.insert(tk.END, "   - Recursively searches children() instead of descendants()\n")
        self.results_text.insert(tk.END, "   - Stops searching a branch when all elements found\n")
        self.results_text.insert(tk.END, "   - Max depth 15 levels\n\n")
        self.results_text.insert(tk.END, "-" * 70 + "\n")
        self.results_text.insert(tk.END, "Click a button above to test an approach...\n")
    
    def run_test(self, approach_num):
        """Run a single test."""
        self.status_var.set(f"Running approach {approach_num}...")
        self.root.update()
        
        approaches = {
            1: ("Current (descendants 1000)", approach1_current),
            2: ("Direct UIA (comtypes)", approach2_direct_uia),
            3: ("Limited (descendants 300)", approach3_limited_descendants),
            4: ("Recursive (children search)", approach4_recursive_children)
        }
        
        name, func = approaches[approach_num]
        
        try:
            result, total_time, timings = func()
            self.display_result(approach_num, name, result, total_time, timings)
        except Exception as e:
            import traceback
            self.results_text.insert(tk.END, f"\n{'='*70}\n")
            self.results_text.insert(tk.END, f"APPROACH {approach_num}: {name}\n")
            self.results_text.insert(tk.END, f"{'='*70}\n")
            self.results_text.insert(tk.END, f"ERROR: {str(e)}\n")
            self.results_text.insert(tk.END, f"{traceback.format_exc()}\n")
        
        self.status_var.set("Ready")
        self.results_text.see(tk.END)
    
    def display_result(self, num, name, result, total_time, timings):
        """Display test result."""
        self.results_text.insert(tk.END, f"\n{'='*70}\n")
        self.results_text.insert(tk.END, f"APPROACH {num}: {name}\n")
        self.results_text.insert(tk.END, f"{'='*70}\n\n")
        
        # Total time (highlighted)
        self.results_text.insert(tk.END, f"⏱️  TOTAL TIME: {total_time*1000:.1f} ms ({total_time:.3f} seconds)\n\n")
        
        # Timing breakdown
        if timings:
            self.results_text.insert(tk.END, "Timing Breakdown:\n")
            for key, value in timings.items():
                if key != 'total' and key != 'note' and isinstance(value, (int, float)):
                    self.results_text.insert(tk.END, f"  - {key}: {value*1000:.1f} ms\n")
            if 'note' in timings:
                self.results_text.insert(tk.END, f"  Note: {timings['note']}\n")
            self.results_text.insert(tk.END, "\n")
        
        # Results
        self.results_text.insert(tk.END, "Data Retrieved:\n")
        self.results_text.insert(tk.END, f"  Window Found: {'✓ Yes' if result.get('window_found') else '✗ No'}\n")
        self.results_text.insert(tk.END, f"  Accession: '{result.get('accession', '')}'\n")
        self.results_text.insert(tk.END, f"  Procedure: '{result.get('procedure', '')}'\n")
        self.results_text.insert(tk.END, f"  Patient Class: '{result.get('patient_class', '')}'\n")
        
        if result.get('error'):
            self.results_text.insert(tk.END, f"\n  ⚠️ Error: {result['error']}\n")
        
        if result.get('events_supported'):
            self.results_text.insert(tk.END, f"  Events Supported: ✓ Yes\n")
        
        self.results_text.insert(tk.END, "\n")
    
    def run_all_tests(self):
        """Run all tests and compare."""
        self.results_text.delete(1.0, tk.END)
        self.results_text.insert(tk.END, "=" * 70 + "\n")
        self.results_text.insert(tk.END, "RUNNING ALL TESTS - COMPARISON\n")
        self.results_text.insert(tk.END, "=" * 70 + "\n")
        
        results = []
        
        for i in range(1, 5):
            self.run_test(i)
            self.root.update()
            time.sleep(0.5)  # Small delay between tests
        
        # Summary
        self.results_text.insert(tk.END, "\n" + "=" * 70 + "\n")
        self.results_text.insert(tk.END, "SUMMARY - Compare timing above to find the fastest approach\n")
        self.results_text.insert(tk.END, "=" * 70 + "\n")


def main():
    root = tk.Tk()
    app = PowerScribeSpeedTest(root)
    root.mainloop()


if __name__ == "__main__":
    main()

