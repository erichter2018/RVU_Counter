import tkinter as tk
from tkinter import ttk
import pyautogui
import win32gui
import win32con
import keyboard
import time # Added for sleep
import win32api # Added for monitor info
import win32clipboard # For RTF clipboard
import io # For BytesIO with RTF data
import win32com.client # For WScript.Shell activation

# --- OCR Imports  ---
try:
    import pytesseract
    from PIL import ImageGrab, Image, ImageEnhance, ImageFilter
except ImportError:
    print("Error: Missing required OCR libraries. Please install pytesseract and Pillow.")
    print("You can typically install them using: pip install pytesseract Pillow")
    # Optionally, you could disable OCR functionality here or exit.
    # For now, we'll let it potentially fail later if not installed.
    pass

# --- OCR Configuration ---
SCREEN_REGION_TO_CAPTURE = (6642, -200, 6900, 59)  # User's last known good coordinates
EMPTY_CHECK_REGION_COORDINATES = (5201, -327, 5268, -255) # User's coordinates
TESSERACT_CMD_PATH = r'C:\Users\erik.richter\AppData\Local\Programs\Tesseract-OCR\tesseract.exe' # User's path

MSK_XR_IDENTIFIERS = [
    "XR RIGHT KNEE", "XR LEFT KNEE", "XR KNEE BILATERAL", "XR KNEES BILATERAL", "XR KNEE",
    "XR RIGHT WRIST", "XR LEFT WRIST", "XR WRIST BILATERAL", "XR WRIST",
    "XR RIGHT HAND", "XR LEFT HAND", "XR HAND BILATERAL", "XR HAND",
    "XR RIGHT THUMB", "XR LEFT THUMB", "XR THUMB",
    "XR RIGHT INDEX FINGER", "XR LEFT INDEX FINGER", "XR INDEX FINGER",
    "XR RIGHT MIDDLE FINGER", "XR LEFT MIDDLE FINGER", "XR MIDDLE FINGER",
    "XR RIGHT RING FINGER", "XR LEFT RING FINGER", "XR RING FINGER",
    "XR RIGHT SMALL FINGER", "XR LEFT SMALL FINGER", "XR SMALL FINGER",
    "XR FINGERS",
    "XR PELVIS",
    "XR RIGHT HIP", "XR LEFT HIP", "XR HIP BILATERAL", "XR HIPS BILATERAL", "XR HIP",
    "XR BILATERAL SI JOINTS", "XR SI JOINTS",
    "XR RIGHT ANKLE", "XR LEFT ANKLE", "XR ANKLE BILATERAL", "XR ANKLE",
    "XR RIGHT FOOT", "XR LEFT FOOT", "XR FOOT BILATERAL", "XR FOOT",
    "XR RIGHT HUMERUS", "XR LEFT HUMERUS", "XR HUMERUS",
    "XR RIGHT FOREARM", "XR LEFT FOREARM", "XR FOREARM",
    "XR RIGHT TIBIA-FIBULA", "XR LEFT TIBIA-FIBULA", "XR TIBIA/FIBULA", "XR TIB/FIB", "XR TIBIA", "XR FIBULA",
    "XR RIGHT ELBOW", "XR LEFT ELBOW", "XR ELBOW BILATERAL", "XR ELBOW",
    "XR RIGHT SHOULDER", "XR LEFT SHOULDER", "XR SHOULDER BILATERAL", "XR SHOULDER",
    "XR CERVICAL SPINE", "XR C-SPINE",
    "XR THORACIC SPINE", "XR T-SPINE",
    "XR LUMBAR SPINE", "XR L-SPINE",
    "XR LUMBOSACRAL SPINE", "XR LS-SPINE",
    "XR SPINE",
    "XR SCOLIOSIS",
    "XR LEG LENGTH", "XR LOWER EXTREMITY LEG LENGTH",
    "XR BONE SURVEY",
    "XR RIBS", "XR RIGHT RIB", "XR LEFT RIB", "XR RIB",
    "XR CLAVICLE", "XR RIGHT CLAVICLE", "XR LEFT CLAVICLE",
    "XR SCAPULA", "XR RIGHT SCAPULA", "XR LEFT SCAPULA",
    "XR CALCANEUS", "XR RIGHT CALCANEUS", "XR LEFT CALCANEUS",
    "XR FEMUR", "XR RIGHT FEMUR", "XR LEFT FEMUR",
    "XR SKULL", "XR MANDIBLE", "XR FACIAL BONES", "XR NASAL BONES", "XR ORBITS", "XR TMJ",
    "XR STERNUM", "XR SACRUM", "XR COCCYX", "XR SACRUM AND COCCYX"
]
# NON_MSK_XR_IDENTIFIERS is not strictly needed for the new display format,
# but can be kept for other logic if desired.

# --- Auto-Paste Configuration ---
ENABLE_AUTO_PASTE = False # Default state for the application
RTF_FILE_PATH = r'C:\\Users\\erik.richter\\Desktop\\e_tools\\bone xray.rtf' # Raw string, updated path
AUTOTEXT_NAME_FOR_MSK = "bone xray" # The name of the AutoText to trigger
LAST_PASTE_TIME = 0
PASTE_COOLDOWN_SECONDS = 10 # Cooldown in seconds
LAST_PASTED_ORDER_DESCRIPTION = None # To prevent re-pasting for the same study too quickly

# --- OCR Helper Functions (from ocr_screen_section.py) ---
def configure_tesseract():
    if TESSERACT_CMD_PATH:
        try:
            pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD_PATH
        except NameError: # pytesseract might not be imported if ImportError occurred
            print("Pytesseract not available for configuration.")

def capture_screen_region_ocr(region_coordinates):
    try:
        if not (region_coordinates[0] >= 0 and region_coordinates[1] >= 0 and \
                region_coordinates[2] > region_coordinates[0] and region_coordinates[3] > region_coordinates[1]):
            pass # Allow attempt, ImageGrab will likely fail
        screenshot = ImageGrab.grab(bbox=region_coordinates, all_screens=True)
        return screenshot
    except Exception:
        return None

def preprocess_image_ocr(image):
    if image is None: return None
    try:
        img = image.convert('L')
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
        return img
    except Exception:
        return None

def ocr_image_to_text_ocr(image):
    if image is None: return ""
    processed_image = preprocess_image_ocr(image)
    if processed_image is None: return ""
    try:
        custom_config = r'-l eng --psm 6' # Raw string for config
        text = pytesseract.image_to_string(processed_image, config=custom_config)
        return text.strip()
    except NameError: # pytesseract might not be imported
        return "OCR Lib Missing"
    except pytesseract.TesseractNotFoundError:
        print("TesseractNotFoundError: Tesseract is not installed or not in your PATH/TESSERACT_CMD_PATH.")
        return "Tesseract Missing"
    except Exception:
        return ""

def parse_ocr_text_for_description_ocr(ocr_text):
    if not ocr_text: return None
    lines = ocr_text.split('\n') # Standard newline
    for line in lines:
        line_lower = line.lower()
        if line_lower.startswith("description:") or line_lower.startswith("exam code:"):
            parts = line.split(":", 1)
            if len(parts) > 1:
                description_value = parts[1].strip()
                if description_value:
                    return description_value
    return None
# --- End OCR Helper Functions ---

# --- Helper function to find window handle --- #
def find_window_by_title_substring(substring, exclude_substring=None):
    hwnd = None
    def enum_handler(found_hwnd, lParam):
        nonlocal hwnd
        try:
            window_text = win32gui.GetWindowText(found_hwnd)
            # Check if window is visible and has a title containing the substring
            is_visible = win32gui.IsWindowVisible(found_hwnd)
            contains_target = substring.lower() in window_text.lower()
            # Check exclusion condition
            is_excluded = False
            if exclude_substring and exclude_substring.lower() in window_text.lower():
                is_excluded = True

            if is_visible and contains_target and not is_excluded:
                hwnd = found_hwnd
                return False # Stop enumeration once found
        except Exception:
            # Some windows might raise errors on GetWindowText (e.g., system processes)
            pass # Ignore errors for specific windows
        return True
    try:
        win32gui.EnumWindows(enum_handler, None)
    except Exception as e:
        print(f"Error enumerating windows: {e}")
        return None
    return hwnd
# --- End Helper --- #

# --- Helper function to get monitor geometry --- #
def get_monitor_geometry(monitor_index):
    """Gets the virtual screen coordinates (left, top) of the specified monitor index."""
    try:
        monitors = win32api.EnumDisplayMonitors()
        if monitor_index < 0 or monitor_index >= len(monitors):
            print(f"Error: Monitor index {monitor_index} out of range (0-{len(monitors)-1}).")
            return None
        
        # Get monitor handle and info
        monitor_handle = monitors[monitor_index][0]
        monitor_info = win32api.GetMonitorInfo(monitor_handle)
        
        # Extract the 'Monitor' rectangle (virtual screen coords)
        monitor_rect = monitor_info['Monitor']
        left, top, _, _ = monitor_rect
        # print(f"Monitor {monitor_index} geometry: Left={left}, Top={top}") # Debug
        return left, top
    except Exception as e:
        print(f"Error getting monitor info: {e}")
        return None
# --- End Helper --- #

# --- RTF Handling and Pasting Functions ---
def read_rtf_file(file_path):
    """Reads an RTF file and returns its content as bytes."""
    try:
        with open(file_path, 'rb') as f: # Read in binary mode
            return f.read()
    except FileNotFoundError:
        print(f"Error: RTF file not found at {file_path}")
        return None
    except Exception as e:
        print(f"Error reading RTF file: {e}")
        return None

def copy_rtf_to_clipboard(rtf_content_bytes):
    """Copies RTF content (bytes) to the clipboard."""
    if not rtf_content_bytes:
        return False
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        rtf_format_id = win32clipboard.RegisterClipboardFormat("Rich Text Format")
        if rtf_format_id == 0:
            print("Error: Failed to get/register 'Rich Text Format' clipboard ID.")
            win32clipboard.CloseClipboard()
            return False
        
        # print(f"Using RTF Format ID: {rtf_format_id}") # Debug
        win32clipboard.SetClipboardData(rtf_format_id, rtf_content_bytes)
        win32clipboard.CloseClipboard()
        # print("RTF content copied to clipboard.") # Debug
        return True
    except Exception as e:
        # Attempt to close clipboard if it was opened
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
        print(f"Error copying RTF to clipboard: {e}")
        return False

def trigger_autotext_by_double_click(x_coord: int, y_coord: int):
    """Finds PowerScribe, activates it, and performs a double-click at the specified coordinates."""
    powerscribe_hwnd = find_window_by_title_substring("Reporting")
    if not powerscribe_hwnd:
        powerscribe_hwnd = find_window_by_title_substring("PowerScribe")

    if powerscribe_hwnd:
        try:
            try:
                shell = win32com.client.Dispatch("WScript.Shell")
                shell.SendKeys('%') # Send ALT to clear any menu state
                time.sleep(0.1) # Brief pause after ALT
            except Exception as e_shell:
                print(f"Error with WScript.Shell (ensure pywin32 is fully installed): {e_shell}")

            placement = win32gui.GetWindowPlacement(powerscribe_hwnd)
            current_show_cmd = placement[1]

            if current_show_cmd == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(powerscribe_hwnd, win32con.SW_RESTORE)
            
            win32gui.SetForegroundWindow(powerscribe_hwnd)
            time.sleep(0.2) # Pause for focus switch

            original_mouse_pos = pyautogui.position() # Save current mouse position
            pyautogui.doubleClick(x=x_coord, y=y_coord, interval=0.1)
            pyautogui.moveTo(original_mouse_pos[0], original_mouse_pos[1]) # Restore mouse position
            # print(f"Double-clicked at ({x_coord}, {y_coord}) with interval and restored mouse to {original_mouse_pos}") # Debug
            return True
        except Exception as e:
            print(f"Error activating PowerScribe or double-clicking: {e}")
            # Fallback to pyautogui activation if direct win32 fails
            try:
                if find_window_by_title_substring("Reporting"):
                    target_windows = pyautogui.getWindowsWithTitle("Reporting")
                    if target_windows: target_windows[0].activate()
                elif find_window_by_title_substring("PowerScribe"):
                    target_windows = pyautogui.getWindowsWithTitle("PowerScribe")
                    if target_windows: target_windows[0].activate()
                else:
                    return False

                time.sleep(0.2)
                original_mouse_pos = pyautogui.position() # Save current mouse position
                pyautogui.doubleClick(x=x_coord, y=y_coord, interval=0.1)
                pyautogui.moveTo(original_mouse_pos[0], original_mouse_pos[1]) # Restore mouse position
                # print(f"Double-clicked (pyautogui fallback) at ({x_coord}, {y_coord}) with interval and restored mouse.") # Debug
                return True
            except Exception as e_pyauto:
                print(f"Error with pyautogui fallback for double-clicking: {e_pyauto}")
                return False
    else:
        # print("PowerScribe window not found for double-clicking.") # Debug
        return False
# --- End RTF Handling and Pasting Functions ---

class FloatingButtons:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Floating Buttons")
        self.root.config(bg='black') # Set root background
        
        # --- Set window size and calculate position for Monitor #3 --- #
        window_width = 120
        window_height = 210 # Increased height for the status label
        target_monitor_index = 2 # User wants monitor #3 (0-indexed)
        relative_x = 15
        relative_y = 620
        
        monitor_pos = get_monitor_geometry(target_monitor_index)
        
        if monitor_pos:
            monitor_left, monitor_top = monitor_pos
            target_x = monitor_left + relative_x
            target_y = monitor_top + relative_y
            geometry_string = f"{window_width}x{window_height}+{target_x}+{target_y}"
            # print(f"Calculated geometry: {geometry_string}") # Debug
        else:
            print(f"Could not get geometry for monitor {target_monitor_index}. Using default position.")
            # Fallback position (e.g., near top-left of primary)
            geometry_string = f"{window_width}x{window_height}+100+100"
        
        self.root.geometry(geometry_string)
        # --- End position calculation --- #
        
        # Make window stay on top and remove window decorations
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)
        
        # Create the main content frame using tk.Frame
        self.frame = tk.Frame(self.root, bg='black')
        self.frame.pack(expand=True, fill='both') # Pack main frame
        
        # Configure grid layout for status label + 3 rows of buttons
        self.frame.grid_rowconfigure(0, weight=0) # Row for status label (fixed height)
        self.frame.grid_rowconfigure(1, weight=1) # Row 1 for buttons
        self.frame.grid_rowconfigure(2, weight=1) # Row 2 for buttons
        self.frame.grid_rowconfigure(3, weight=1) # Row 3 for zoom button
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_columnconfigure(1, weight=1)
        
        # --- Status Label and Auto-Paste Checkbox ---
        # BooleanVar for the Checkbutton state
        self.auto_paste_var = tk.BooleanVar()
        self.auto_paste_var.set(ENABLE_AUTO_PASTE) # Initialize from global

        # Frame to hold checkbox and status label
        top_controls_frame = tk.Frame(self.frame, bg='black')
        top_controls_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(2,2))

        off_white_color = '#CCCCCC' # Defined here for use by checkbutton too

        self.auto_paste_checkbutton = tk.Checkbutton(
            top_controls_frame,
            variable=self.auto_paste_var,
            command=self.toggle_auto_paste,
            bg='black',
            fg=off_white_color,
            selectcolor='#333333', # Background of the check square when checked
            activebackground='black',
            activeforeground=off_white_color,
            highlightthickness=0,
            borderwidth=0,
            takefocus=False
        )
        self.auto_paste_checkbutton.pack(side=tk.LEFT, padx=(2, 5)) # Small padding

        self.status_label = tk.Label(top_controls_frame, text="MSK", font=("Segoe UI", 8), bg="black", fg="red") # Initial state
        self.status_label.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0,2))
        
        # --- Create buttons with explicit frame borders --- #
        button_font = ('Segoe UI Symbol', 16, 'bold') # Increased size, added bold
        button_options = {
            'bg': 'black',
            'fg': off_white_color, # Use off-white
            'font': button_font,
            'borderwidth': 0,
            'relief': 'flat',
            'activebackground': '#333333',
            'activeforeground': off_white_color, # Use off-white
            'takefocus': False
        }
        border_color = off_white_color # Use off-white for border
        border_thickness = 1 # Padding inside the border_frame creates the border effect
        
        self.buttons = [] # List to hold buttons for context menu binding
        
        # Define button specs (text, command) - SWAPPED SECOND ROW COMMANDS/POSITIONS
        button_specs = [
            [ ("↕", lambda: self.send_key_to_inteleviewer('ctrl', 'v')), ("↔", lambda: self.send_key_to_inteleviewer('ctrl', 'h')) ],
            [ ("↺", lambda: self.send_key_to_inteleviewer(',')),        ("↻", lambda: self.send_key_to_inteleviewer('.')) ] # Swapped this row
        ]
        
        # Create buttons in a loop using the frame-as-border technique
        for r, row_specs in enumerate(button_specs):
            for c, (text, command) in enumerate(row_specs):
                # 1. Create the white border frame (tk.Frame)
                border_frame = tk.Frame(self.frame, bg=border_color)
                # 2. Grid the border frame in the main frame grid
                border_frame.grid(row=r+1, column=c, padx=1, pady=1, sticky='nsew') # padx/y here is space *between* borders
                
                # 3. Create the actual button
                button = tk.Button(border_frame, text=text, command=command, **button_options)
                
                # 4. Pack the button *inside* the border frame with internal padding
                button.pack(expand=True, fill='both', padx=border_thickness, pady=border_thickness)
                
                self.buttons.append(button)

        # --- Add the 5th button spanning the bottom --- #
        zoom_out_text = "Zoom Out"
        zoom_out_command = lambda: self.send_key_to_inteleviewer('-')

        # 1. Create the white border frame
        border_frame5 = tk.Frame(self.frame, bg=border_color)
        # 2. Grid the border frame in the main frame grid, spanning 2 columns
        border_frame5.grid(row=3, column=0, columnspan=2, padx=1, pady=1, sticky='nsew')

        # 3. Create the actual button (adjust font size if needed for longer text)
        zoom_button_options = button_options.copy()
        # Update zoom button font and foreground
        zoom_button_options['font'] = ('Segoe UI', 11, 'bold') # Slightly larger, bold
        zoom_button_options['fg'] = off_white_color
        zoom_button_options['activeforeground'] = off_white_color
        button5 = tk.Button(border_frame5, text=zoom_out_text, command=zoom_out_command, **zoom_button_options)

        # 4. Pack the button *inside* the border frame with internal padding
        button5.pack(expand=True, fill='both', padx=border_thickness, pady=border_thickness)

        self.buttons.append(button5)
        # --- End of button creation ---
        
        # Make window draggable (bind to main frame and root background)
        self.frame.bind('<Button-1>', self.start_move)
        self.frame.bind('<B1-Motion>', self.on_move)
        self.root.bind('<Button-1>', self.start_move)
        self.root.bind('<B1-Motion>', self.on_move)
        
        # Add right-click menu for closing - bind after buttons exist
        self.create_context_menu()
        
        # --- Initialize OCR and start update loop ---
        configure_tesseract() # Call this once
        self.run_ocr_update() # Start the OCR update process
        
    def toggle_auto_paste(self):
        global ENABLE_AUTO_PASTE
        ENABLE_AUTO_PASTE = self.auto_paste_var.get()
        # print(f"Auto-paste Toggled: {ENABLE_AUTO_PASTE}") # For debugging
        
    def send_key(self, key):
        # This method is now potentially unused, depending on future needs.
        # Keeping it for now in case you want other global hotkeys later.
        pyautogui.hotkey(*key.split('+'))
        
    def start_move(self, event):
        self.x = event.x
        self.y = event.y
        
    def on_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")
        
    def create_context_menu(self):
        off_white_color = '#CCCCCC' # Define color here too or pass it
        self.context_menu = tk.Menu(self.root, tearoff=0, bg='black', fg=off_white_color) # Use off-white
        self.context_menu.add_command(label="Close", command=self.root.quit)
        # Bind context menu to frame and root
        self.frame.bind('<Button-3>', self.show_context_menu)
        self.root.bind('<Button-3>', self.show_context_menu)
        # Also bind to buttons so right-clicking a button shows the menu
        # Check if self.buttons exists (it should now)
        if hasattr(self, 'buttons'):
             for btn in self.buttons:
                 # Check if it's a tk.Button before binding
                 if isinstance(btn, tk.Button):
                     btn.bind('<Button-3>', self.show_context_menu)
        
    def show_context_menu(self, event):
        self.context_menu.post(event.x_root, event.y_root)
        
    def run(self):
        self.root.mainloop()

    # --- New method for sending keys to specific window ---
    def send_key_to_inteleviewer(self, *keys):
        target_title_part = "InteleViewer"
        exclude_title_part = "Search Tool"
        inteleviewer_hwnd = find_window_by_title_substring(target_title_part, exclude_substring=exclude_title_part)

        if inteleviewer_hwnd:
            try:
                # Activate the target window using PyGetWindow/pyautogui
                target_windows = pyautogui.getWindowsWithTitle(target_title_part)
                if target_windows:
                    target_windows[0].activate()
                    # Short pause for focus switch
                    time.sleep(0.1)

                    # Send keys using pyautogui now that window should be active
                    if len(keys) > 1:
                        pyautogui.hotkey(*keys)
                    elif len(keys) == 1:
                        pyautogui.press(keys[0])
                    #print(f"Sent keys {keys} to window HWND {inteleviewer_hwnd}") # Debug
                else:
                    print(f"Found HWND but couldn't activate window containing '{target_title_part}'") # Debug

            except Exception as e:
                print(f"Error activating/sending key after finding window: {e}")
        #else:
            #print(f"Window containing '{target_title_part}' not found.") # Debug
    # --- End of new method ---

    # --- New OCR Update Method ---
    def run_ocr_update(self):
        global LAST_PASTE_TIME, LAST_PASTED_ORDER_DESCRIPTION # Declare globals

        report_is_empty = False
        is_target_msk_study = False
        order_description = None # Initialize
        off_white_color = '#CCCCCC' # For neutral display

        # 1. OCR for the empty check region FIRST
        empty_check_image = capture_screen_region_ocr(EMPTY_CHECK_REGION_COORDINATES)
        if empty_check_image:
            empty_check_text = ocr_image_to_text_ocr(empty_check_image)
            if empty_check_text is not None and ("Missing" not in empty_check_text):
                 if not empty_check_text or len(empty_check_text) < 5:
                    report_is_empty = True
        
        # 2. If report is empty, then OCR for the main description and determine MSK Study status
        if report_is_empty:
            desc_image = capture_screen_region_ocr(SCREEN_REGION_TO_CAPTURE)
            if desc_image:
                raw_desc_text = ocr_image_to_text_ocr(desc_image)
                if raw_desc_text and "Missing" not in raw_desc_text : # Basic check for OCR errors
                    order_description = parse_ocr_text_for_description_ocr(raw_desc_text)
                    if order_description:
                        order_description_upper = order_description.upper()
                        for msk_id in MSK_XR_IDENTIFIERS:
                            if order_description_upper.startswith(msk_id.upper()):
                                is_target_msk_study = True
                                break
        
        # 3. Update GUI Label based on the new logic
        status_text = ""
        fg_color = "red" # Default

        if not report_is_empty: # Report IS present
            status_text = "R"
            fg_color = off_white_color # Neutral color like off-white
        else: # Report is NOT present (empty)
            if is_target_msk_study:
                status_text = "MSK NR"
                fg_color = "#00B050"  # Green
            else:
                # This covers cases where it's empty but not MSK, or study type couldn't be determined
                status_text = "NOT MSK NR"
                # fg_color remains red
            
        # Ensure self.status_label is updated
        if hasattr(self, 'status_label'): 
            self.status_label.config(text=status_text, fg=fg_color)

        # --- Auto-Pasting Logic ---
        current_time = time.time()
        if ENABLE_AUTO_PASTE and is_target_msk_study and report_is_empty:
            # Cooldown check:
            # 1. Different study: paste if cooldown met (allows quick paste for new study)
            # 2. Same study: paste only if cooldown met (prevents re-pasting on same empty report)
            #    AND if the description is different from the last one we pasted for (or if it's the first paste)
            
            can_paste = False
            time_since_last_paste = current_time - LAST_PASTE_TIME

            if order_description != LAST_PASTED_ORDER_DESCRIPTION: # New or different study
                if time_since_last_paste > PASTE_COOLDOWN_SECONDS:
                    can_paste = True
            else: # Same study as last paste attempt
                # Allow re-paste for the same study if user cleared it, after cooldown
                if time_since_last_paste > PASTE_COOLDOWN_SECONDS:
                    can_paste = True
            
            # More direct logic: only paste if current order_description is new OR cooldown elapsed
            # This prevents rapid re-pasting for the *exact same detected study description*
            # unless the cooldown period has passed.
            if order_description != LAST_PASTED_ORDER_DESCRIPTION or \
               (current_time - LAST_PASTE_TIME > PASTE_COOLDOWN_SECONDS):

                # print(f"Attempting auto-trigger for MSK Study. MSK: {is_target_msk_study}, Empty: {report_is_empty}, Cooldown: {current_time - LAST_PASTE_TIME:.1f}s") # Debug
                if trigger_autotext_by_double_click(x_coord=5079, y_coord=555):
                    LAST_PASTE_TIME = current_time
                    LAST_PASTED_ORDER_DESCRIPTION = order_description # Store the description of this study
                    print(f"Attempted AutoText insertion via double-click for: {order_description}")
                # else:
                    # print("Failed to trigger AutoText in PowerScribe via double-click.") # Debug
                # pass # Placeholder if the above block is entirely commented out - REMOVED
        # --- End Auto-Pasting Logic ---

        # 5. Reschedule
        self.root.after(2000, self.run_ocr_update) # Run again after 2 seconds

if __name__ == "__main__":
    app = FloatingButtons()
    app.run() 