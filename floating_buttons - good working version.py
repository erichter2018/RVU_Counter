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
import hashlib # For change detection
import threading # For background processing
import os # Added for file operations

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
EMPTY_CHECK_REGION_COORDINATES = (5201, -327, 5268, -300) # User's coordinates
TESSERACT_CMD_PATH = r'C:\Users\erik.richter\AppData\Local\Programs\Tesseract-OCR\tesseract.exe' # User's path

# --- Change Detection Configuration ---
CHANGE_DETECTION_ENABLED = True
HASH_CACHE_SIZE = 10  # Keep last 10 hashes for each region
PIXEL_CHANGE_THRESHOLD = 5  # Percentage of pixels that must change
DEBOUNCE_TIME = 0.5  # Seconds to wait before confirming change
MIN_POLL_INTERVAL = 200  # Minimum polling interval in ms
MAX_POLL_INTERVAL = 5000  # Maximum polling interval in ms
ACTIVITY_DECAY_RATE = 1.2  # How quickly polling slows down

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
ENABLE_AUTO_PASTE = True # Default state for the application
RTF_FILE_PATH = r'C:\\Users\\erik.richter\\Desktop\\e_tools\\bone xray.rtf' # Raw string, updated path
AUTOTEXT_NAME_FOR_MSK = "bone xray" # The name of the AutoText to trigger
LAST_PASTE_TIME = 0
PASTE_COOLDOWN_SECONDS = 10 # Cooldown in seconds
LAST_PASTED_ORDER_DESCRIPTION = None # To prevent re-pasting for the same study too quickly

# --- Dynamic MSK Identifiers File Management ---
MSK_IDENTIFIERS_FILE = "MSK_IDENTIFIERS.txt"
MSK_IDENTIFIERS_FILE_LAST_MODIFIED = 0

def get_body_part_category(identifier):
    """Categorize an MSK identifier by body part for organization"""
    identifier_upper = identifier.upper()
    
    # Define body part categories and their keywords
    categories = {
        "KNEE": ["KNEE", "KNEES"],
        "WRIST": ["WRIST"],
        "HAND": ["HAND", "THUMB", "INDEX FINGER", "MIDDLE FINGER", "RING FINGER", "SMALL FINGER", "FINGERS"],
        "PELVIS": ["PELVIS", "SI JOINTS", "SACRUM", "COCCYX"],
        "HIP": ["HIP", "HIPS"],
        "ANKLE": ["ANKLE"],
        "FOOT": ["FOOT", "CALCANEUS"],
        "HUMERUS": ["HUMERUS"],
        "FOREARM": ["FOREARM"],
        "TIBIA_FIBULA": ["TIBIA", "FIBULA", "TIB/FIB"],
        "ELBOW": ["ELBOW"],
        "SHOULDER": ["SHOULDER"],
        "SPINE": ["SPINE", "CERVICAL", "THORACIC", "LUMBAR", "LUMBOSACRAL", "SCOLIOSIS"],
        "LEG_LENGTH": ["LEG LENGTH", "LOWER EXTREMITY"],
        "BONE_SURVEY": ["BONE SURVEY"],
        "RIBS": ["RIB", "RIBS"],
        "CLAVICLE": ["CLAVICLE"],
        "SCAPULA": ["SCAPULA"],
        "FEMUR": ["FEMUR"],
        "SKULL": ["SKULL", "MANDIBLE", "FACIAL BONES", "NASAL BONES", "ORBITS", "TMJ"],
        "STERNUM": ["STERNUM"]
    }
    
    for category, keywords in categories.items():
        for keyword in keywords:
            if keyword in identifier_upper:
                return category
    
    return "OTHER"  # Default category for unrecognized identifiers

def organize_identifiers_by_category(identifiers):
    """Organize identifiers by body part category"""
    categorized = {}
    for identifier in identifiers:
        category = get_body_part_category(identifier)
        if category not in categorized:
            categorized[category] = []
        categorized[category].append(identifier)
    
    # Sort identifiers within each category
    for category in categorized:
        categorized[category].sort()
    
    return categorized

def create_msk_identifiers_file():
    """Create the MSK identifiers file with current hardcoded identifiers"""
    try:
        # Organize current identifiers by category
        categorized = organize_identifiers_by_category(MSK_XR_IDENTIFIERS)
        
        with open(MSK_IDENTIFIERS_FILE, 'w') as f:
            f.write("# ==========================================\n")
            f.write("# USER ADDITIONS SECTION - ADD NEW IDENTIFIERS BELOW\n")
            f.write("# ==========================================\n")
            f.write("# Instructions: Add one MSK identifier per line in the section below.\n")
            f.write("# The app will automatically move them to the appropriate category.\n")
            f.write("# Examples: XR RIGHT SHOULDER, XR LEFT KNEE, XR CERVICAL SPINE\n")
            f.write("# ==========================================\n")
            f.write("\n")
            f.write("# ==========================================\n")
            f.write("# ORGANIZED IDENTIFIERS (DO NOT EDIT BELOW THIS LINE)\n")
            f.write("# ==========================================\n")
            f.write("\n")
            
            # Write organized identifiers
            for category, identifiers in sorted(categorized.items()):
                f.write(f"# --- {category} ---\n")
                for identifier in identifiers:
                    f.write(f"{identifier}\n")
                f.write("\n")
        
        print(f"✅ Created {MSK_IDENTIFIERS_FILE} with current MSK identifiers")
        return True
    except Exception as e:
        print(f"❌ Error creating MSK identifiers file: {e}")
        return False

def read_msk_identifiers_file():
    """Read and parse the MSK identifiers file"""
    try:
        if not os.path.exists(MSK_IDENTIFIERS_FILE):
            print(f"📄 {MSK_IDENTIFIERS_FILE} not found, creating it...")
            if not create_msk_identifiers_file():
                return MSK_XR_IDENTIFIERS  # Fallback to hardcoded list
        
        # Check if file has been modified
        file_mod_time = os.path.getmtime(MSK_IDENTIFIERS_FILE)
        global MSK_IDENTIFIERS_FILE_LAST_MODIFIED
        if file_mod_time <= MSK_IDENTIFIERS_FILE_LAST_MODIFIED:
            return None  # File hasn't changed
        
        MSK_IDENTIFIERS_FILE_LAST_MODIFIED = file_mod_time
        
        with open(MSK_IDENTIFIERS_FILE, 'r') as f:
            lines = f.readlines()
        
        user_additions = []
        organized_identifiers = []
        in_user_section = False
        in_organized_section = False
        
        for line in lines:
            line = line.strip()
            
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                if "# USER ADDITIONS SECTION" in line:
                    in_user_section = True
                    in_organized_section = False
                elif "# ORGANIZED IDENTIFIERS" in line:
                    in_user_section = False
                    in_organized_section = True
                continue
            
            if in_user_section:
                user_additions.append(line)
            elif in_organized_section:
                organized_identifiers.append(line)
        
        return user_additions, organized_identifiers
        
    except Exception as e:
        print(f"❌ Error reading MSK identifiers file: {e}")
        return None

def process_user_additions_and_update_file(user_additions, organized_identifiers):
    """Process user additions and update the file"""
    if not user_additions:
        return organized_identifiers
    
    print(f"🔄 Processing {len(user_additions)} new user additions...")
    
    # Add user additions to organized list
    all_identifiers = organized_identifiers + user_additions
    
    # Remove duplicates while preserving order
    seen = set()
    unique_identifiers = []
    for identifier in all_identifiers:
        if identifier not in seen:
            seen.add(identifier)
            unique_identifiers.append(identifier)
    
    # Organize by category
    categorized = organize_identifiers_by_category(unique_identifiers)
    
    # Recreate the file with organized structure
    try:
        with open(MSK_IDENTIFIERS_FILE, 'w') as f:
            f.write("# ==========================================\n")
            f.write("# USER ADDITIONS SECTION - ADD NEW IDENTIFIERS BELOW\n")
            f.write("# ==========================================\n")
            f.write("# Instructions: Add one MSK identifier per line in the section below.\n")
            f.write("# The app will automatically move them to the appropriate category.\n")
            f.write("# Examples: XR RIGHT SHOULDER, XR LEFT KNEE, XR CERVICAL SPINE\n")
            f.write("# ==========================================\n")
            f.write("\n")
            f.write("# ==========================================\n")
            f.write("# ORGANIZED IDENTIFIERS (DO NOT EDIT BELOW THIS LINE)\n")
            f.write("# ==========================================\n")
            f.write("\n")
            
            # Write organized identifiers
            for category, identifiers in sorted(categorized.items()):
                f.write(f"# --- {category} ---\n")
                for identifier in identifiers:
                    f.write(f"{identifier}\n")
                f.write("\n")
        
        print(f"✅ Updated {MSK_IDENTIFIERS_FILE} - moved {len(user_additions)} identifiers to organized sections")
        
        # Return the complete list of unique identifiers
        return unique_identifiers
        
    except Exception as e:
        print(f"❌ Error updating MSK identifiers file: {e}")
        return organized_identifiers

def get_dynamic_msk_identifiers():
    """Get the complete list of MSK identifiers (hardcoded + file-based)"""
    # Start with hardcoded identifiers
    all_identifiers = MSK_XR_IDENTIFIERS.copy()
    
    # Check file for updates
    file_result = read_msk_identifiers_file()
    if file_result is not None:
        user_additions, organized_identifiers = file_result
        
        # Process any user additions
        if user_additions:
            file_identifiers = process_user_additions_and_update_file(user_additions, organized_identifiers)
        else:
            file_identifiers = organized_identifiers
        
        # Merge with hardcoded identifiers, avoiding duplicates
        for identifier in file_identifiers:
            if identifier not in all_identifiers:
                all_identifiers.append(identifier)
    
    return all_identifiers

# --- End Dynamic MSK Identifiers File Management ---

# --- Change Detection Classes ---
class RegionChangeDetector:
    def __init__(self, region_coords, name):
        self.region_coords = region_coords
        self.name = name
        self.hash_history = []
        self.last_change_time = 0
        self.pending_change = False
        self.pending_change_time = 0
        self.last_ocr_result = None
        self.last_ocr_hash = None
        
    def get_region_hash(self, image):
        """Generate MD5 hash of image pixel data"""
        if image is None:
            return None
        try:
            # Convert to bytes and hash
            image_bytes = image.tobytes()
            return hashlib.md5(image_bytes).hexdigest()
        except Exception:
            return None
    
    def has_changed(self, current_hash):
        """Check if current hash differs from recent history"""
        if not current_hash:
            return False
        
        # Keep only recent hashes
        if len(self.hash_history) > HASH_CACHE_SIZE:
            self.hash_history = self.hash_history[-HASH_CACHE_SIZE:]
        
        # Check if this hash is new
        if current_hash not in self.hash_history:
            self.hash_history.append(current_hash)
            return True
        
        return False
    
    def calculate_pixel_change_percentage(self, old_image, new_image):
        """Calculate percentage of pixels that changed between two images"""
        if old_image is None or new_image is None:
            return 100  # Assume significant change if we can't compare
        
        try:
            # Convert to grayscale for comparison
            old_gray = old_image.convert('L')
            new_gray = new_image.convert('L')
            
            # Try numpy first (faster)
            try:
                import numpy as np
                old_array = np.array(old_gray)
                new_array = np.array(new_gray)
                
                # Calculate difference
                diff = np.abs(old_array - new_array)
                changed_pixels = np.sum(diff > 10)  # Threshold for "changed" pixel
                total_pixels = old_array.size
                
                return (changed_pixels / total_pixels) * 100
            except ImportError:
                # Fallback to PIL-only comparison (slower but works)
                width, height = old_gray.size
                total_pixels = width * height
                changed_pixels = 0
                
                for x in range(width):
                    for y in range(height):
                        old_pixel = old_gray.getpixel((x, y))
                        new_pixel = new_gray.getpixel((x, y))
                        if abs(old_pixel - new_pixel) > 10:
                            changed_pixels += 1
                
                return (changed_pixels / total_pixels) * 100
        except Exception:
            return 100  # Assume significant change on error

class ChangeDetectionManager:
    def __init__(self):
        self.empty_check_detector = RegionChangeDetector(EMPTY_CHECK_REGION_COORDINATES, "empty_check")
        self.description_detector = RegionChangeDetector(SCREEN_REGION_TO_CAPTURE, "description")
        self.current_poll_interval = MIN_POLL_INTERVAL
        self.last_activity_time = time.time()
        self.cached_images = {}
        
    def update_activity(self):
        """Update activity timestamp and reset polling interval"""
        self.last_activity_time = time.time()
        self.current_poll_interval = MIN_POLL_INTERVAL
        
    def get_adaptive_poll_interval(self):
        """Calculate adaptive polling interval based on recent activity"""
        time_since_activity = time.time() - self.last_activity_time
        
        if time_since_activity < 2:  # High activity - last 2 seconds
            interval = MIN_POLL_INTERVAL
        elif time_since_activity < 10:  # Medium activity - last 10 seconds
            interval = MIN_POLL_INTERVAL * 2
        elif time_since_activity < 30:  # Low activity - last 30 seconds
            interval = MIN_POLL_INTERVAL * 5
        else:  # Idle - more than 30 seconds
            interval = MAX_POLL_INTERVAL
            
        self.current_poll_interval = min(interval, MAX_POLL_INTERVAL)
        return self.current_poll_interval
    
    def should_trigger_ocr(self, detector, current_image):
        """Determine if OCR should be triggered for a detector"""
        if not current_image:
            return False
            
        current_hash = detector.get_region_hash(current_image)
        if not current_hash:
            return False
            
        # Check if visual change detected
        if detector.has_changed(current_hash):
            current_time = time.time()
            
            # Check if we need to calculate pixel change percentage
            if detector.name in self.cached_images:
                old_image = self.cached_images[detector.name]
                change_percentage = detector.calculate_pixel_change_percentage(old_image, current_image)
                
                # Only trigger OCR if significant change
                if change_percentage > PIXEL_CHANGE_THRESHOLD:
                    if detector.pending_change:
                        # Check if debounce time has passed
                        if current_time - detector.pending_change_time > DEBOUNCE_TIME:
                            detector.pending_change = False
                            detector.last_change_time = current_time
                            self.update_activity()
                            self.cached_images[detector.name] = current_image
                            return True
                    else:
                        # Start debounce timer
                        detector.pending_change = True
                        detector.pending_change_time = current_time
                        self.cached_images[detector.name] = current_image
                        return False
                else:
                    # Minor change, probably noise
                    detector.pending_change = False
                    return False
            else:
                # First time seeing this region
                self.cached_images[detector.name] = current_image
                return True
        
        return False

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
        # Simple preprocessing - just basic grayscale
        img = image.convert('L')
        return img
    except Exception:
        return None

def has_any_content(image):
    """
    Simple function to detect if there's ANY content in an image region.
    Returns True if there appears to be text/content, False if empty.
    """
    if image is None:
        return False
    
    try:
        # Convert to grayscale
        gray = image.convert('L')
        pixels = list(gray.getdata())
        
        # Calculate basic statistics
        total_pixels = len(pixels)
        if total_pixels == 0:
            return False
        
        # Find the most common pixel value (likely background)
        from collections import Counter
        pixel_counts = Counter(pixels)
        most_common_pixel = pixel_counts.most_common(1)[0][0]
        
        # Count pixels that differ significantly from background
        threshold = 30  # Adjust if needed
        different_pixels = sum(1 for p in pixels if abs(p - most_common_pixel) > threshold)
        
        # If more than 10% of pixels are different from background, assume content
        content_ratio = different_pixels / total_pixels
        return content_ratio > 0.1
        
    except Exception:
        return False

def ocr_image_to_text_ocr(image):
    if image is None: return ""
    
    # First do a quick check - is there anything there at all?
    if not has_any_content(image):
        return ""
    
    processed_image = preprocess_image_ocr(image)
    if processed_image is None: return ""
    
    try:
        # Simple OCR with basic config
        custom_config = r'-l eng --psm 6'
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
        
        # --- Initialize Change Detection System ---
        self.change_detector = ChangeDetectionManager()
        
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
        
        # Create a small drag handle in the top right corner
        self.drag_handle = tk.Frame(self.frame, bg='#666666', width=15, height=15)
        self.drag_handle.grid(row=0, column=1, sticky='ne', padx=(0, 2), pady=(2, 0))
        
        # Add a visual indicator (small dots) to show it's draggable
        self.drag_indicator = tk.Label(self.drag_handle, text="⋮", font=("Segoe UI", 8), 
                                      bg='#666666', fg='#CCCCCC')
        self.drag_indicator.pack(expand=True)
        
        # Make window draggable only from the handle
        self.drag_handle.bind('<Button-1>', self.start_move)
        self.drag_handle.bind('<B1-Motion>', self.on_move)
        self.drag_indicator.bind('<Button-1>', self.start_move)
        self.drag_indicator.bind('<B1-Motion>', self.on_move)
        
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
        # Bind context menu to frame, root, and drag handle
        self.frame.bind('<Button-3>', self.show_context_menu)
        self.root.bind('<Button-3>', self.show_context_menu)
        self.drag_handle.bind('<Button-3>', self.show_context_menu)
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

    # --- Auto-Paste Retry Method ---
    def retry_ocr_for_na(self, desc_image):
        """
        Retry OCR when we get "n/a" result to handle timing issues.
        Returns the best OCR result found, or None if all attempts failed.
        """
        MAX_NA_RETRIES = 3
        RETRY_DELAYS = [0.5, 1.0, 1.5]  # Increasing delays between retries
        
        # First attempt
        raw_desc_text = ocr_image_to_text_ocr(desc_image)
        if raw_desc_text and "Missing" not in raw_desc_text:
            order_description = parse_ocr_text_for_description_ocr(raw_desc_text)
            if order_description and order_description.lower() != "n/a":
                return order_description
        
        # If we got "n/a" or no result, retry with delays
        print(f"🔄 Initial OCR returned 'n/a' - retrying up to {MAX_NA_RETRIES} times...")
        
        for attempt in range(MAX_NA_RETRIES):
            delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
            print(f"   Retry {attempt + 1}/{MAX_NA_RETRIES} after {delay}s delay...")
            
            time.sleep(delay)
            
            # Re-capture the screen region (in case content changed)
            new_desc_image = capture_screen_region_ocr(SCREEN_REGION_TO_CAPTURE)
            if new_desc_image:
                raw_desc_text = ocr_image_to_text_ocr(new_desc_image)
                if raw_desc_text and "Missing" not in raw_desc_text:
                    order_description = parse_ocr_text_for_description_ocr(raw_desc_text)
                    if order_description and order_description.lower() != "n/a":
                        print(f"   ✅ Retry {attempt + 1} successful: '{order_description}'")
                        return order_description
                    else:
                        print(f"   ❌ Retry {attempt + 1} still returned 'n/a'")
                else:
                    print(f"   ❌ Retry {attempt + 1} OCR failed")
            else:
                print(f"   ❌ Retry {attempt + 1} capture failed")
        
        print(f"   ❌ All {MAX_NA_RETRIES} retries failed - keeping 'n/a' result")
        # Return the original "n/a" result if all retries failed
        return parse_ocr_text_for_description_ocr(raw_desc_text) if raw_desc_text else None

    def attempt_autotext_insertion_with_retry(self, order_description):
        """
        Attempts to insert AutoText with retry mechanism.
        Tries up to 5 times, checking after each attempt if text appeared.
        Returns True if successful, False if all attempts failed.
        """
        MAX_ATTEMPTS = 5
        CHECK_DELAY = 0.5  # 500ms delay between attempts
        
        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"AutoText insertion attempt {attempt}/{MAX_ATTEMPTS} for: {order_description}")
            
            # Perform the double-click
            if trigger_autotext_by_double_click(x_coord=5079, y_coord=555):
                # Wait for template to be inserted
                time.sleep(CHECK_DELAY)
                
                # Check if content appeared in the empty region
                empty_check_image = capture_screen_region_ocr(EMPTY_CHECK_REGION_COORDINATES)
                if empty_check_image:
                    # No OCR needed - just check if there's any content
                    if has_any_content(empty_check_image):
                        print(f"AutoText insertion successful on attempt {attempt}")
                        return True
                    else:
                        print(f"AutoText insertion failed on attempt {attempt} - no content detected")
                else:
                    print(f"AutoText insertion failed on attempt {attempt} - couldn't capture region")
            else:
                print(f"AutoText insertion failed on attempt {attempt} - double-click failed")
            
            # If not the last attempt, wait a bit before retrying
            if attempt < MAX_ATTEMPTS:
                time.sleep(0.2)  # Brief pause before next attempt
        
        print(f"AutoText insertion failed after {MAX_ATTEMPTS} attempts. Will wait for cooldown.")
        return False

    # --- Simplified OCR Update Method ---
    def run_ocr_update(self):
        global LAST_PASTE_TIME, LAST_PASTED_ORDER_DESCRIPTION # Declare globals

        # Initialize variables
        report_is_empty = False
        is_target_msk_study = False
        order_description = None
        off_white_color = '#CCCCCC'
        
        # Track previous state to detect transitions
        if not hasattr(self, 'last_report_was_empty'):
            self.last_report_was_empty = None
        
        # STEP 1: Always check empty region (cheap pixel check)
        empty_check_image = capture_screen_region_ocr(EMPTY_CHECK_REGION_COORDINATES)
        if empty_check_image:
            # Simple pixel-based check - no OCR needed
            if has_any_content(empty_check_image):
                report_is_empty = False
            else:
                report_is_empty = True
        
        # STEP 2: Only if report is empty, check if description region changed
        if report_is_empty:
            desc_image = capture_screen_region_ocr(SCREEN_REGION_TO_CAPTURE)
            
            # Check if report just became empty (transition from not empty to empty)
            report_just_became_empty = (self.last_report_was_empty == False and report_is_empty == True)
            
            # Force OCR if report just became empty, otherwise use change detection
            if report_just_became_empty:
                should_ocr_desc = True
                print("Report just became empty - forcing OCR of description region")
            elif CHANGE_DETECTION_ENABLED:
                should_ocr_desc = self.change_detector.should_trigger_ocr(
                    self.change_detector.description_detector, desc_image
                )
            else:
                should_ocr_desc = True
            
            # Only do OCR if description region changed or report just became empty
            if should_ocr_desc or not CHANGE_DETECTION_ENABLED:
                if desc_image:
                    # Retry OCR if we get "n/a" result
                    order_description = self.retry_ocr_for_na(desc_image)
                    if order_description:
                        order_description_upper = order_description.upper()
                        print(f"🔍 OCR detected study description: '{order_description}'")
                        print(f"   Uppercase version: '{order_description_upper}'")
                        
                        # Get dynamic MSK identifiers (hardcoded + file-based)
                        dynamic_msk_identifiers = get_dynamic_msk_identifiers()
                        
                        for msk_id in dynamic_msk_identifiers:
                            if order_description_upper.startswith(msk_id.upper()):
                                is_target_msk_study = True
                                print(f"   ✅ MATCHED MSK identifier: '{msk_id}'")
                                break
                        
                        if not is_target_msk_study:
                            print(f"   ❌ NO MSK match found - checking against {len(dynamic_msk_identifiers)} identifiers")
                            # Show first few potential matches for debugging
                            potential_matches = [id for id in dynamic_msk_identifiers if id.upper() in order_description_upper]
                            if potential_matches:
                                print(f"   🔍 Potential partial matches: {potential_matches[:3]}")
                            else:
                                print(f"   🔍 No partial matches found")
                        
                        # Store result in detector for caching
                        self.change_detector.description_detector.last_ocr_result = (order_description, is_target_msk_study)
                    else:
                        # Clear cached result if OCR failed
                        self.change_detector.description_detector.last_ocr_result = None
            else:
                # Use cached result if available
                if self.change_detector.description_detector.last_ocr_result:
                    order_description, is_target_msk_study = self.change_detector.description_detector.last_ocr_result
                    
            # OCR call saved when using cached result
        else:
            # Report not empty - clear any cached description results
            self.change_detector.description_detector.last_ocr_result = None
        
        # Update GUI Label based on the logic
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
        
        # Removed efficiency indicator from status text
            
        # Ensure self.status_label is updated
        if hasattr(self, 'status_label'): 
            self.status_label.config(text=status_text, fg=fg_color)

        # --- Auto-Pasting Logic ---
        current_time = time.time()
        if ENABLE_AUTO_PASTE and is_target_msk_study and report_is_empty:
            # More direct logic: only paste if current order_description is new OR cooldown elapsed
            if order_description != LAST_PASTED_ORDER_DESCRIPTION or \
               (current_time - LAST_PASTE_TIME > PASTE_COOLDOWN_SECONDS):

                success = self.attempt_autotext_insertion_with_retry(order_description)
                if success:
                    LAST_PASTE_TIME = current_time
                    LAST_PASTED_ORDER_DESCRIPTION = order_description
        # --- End Auto-Pasting Logic ---

        # Removed efficiency and debug stats printing

        # Update state tracking for next cycle
        self.last_report_was_empty = report_is_empty

        # Check for MSK identifiers file updates every 10 seconds (20 cycles)
        if not hasattr(self, 'msk_check_counter'):
            self.msk_check_counter = 0
        self.msk_check_counter += 1
        
        if self.msk_check_counter >= 20:  # Every 10 seconds
            self.msk_check_counter = 0
            # This will trigger file check and processing if needed
            get_dynamic_msk_identifiers()
        
        # Always reschedule every 500ms
        self.root.after(500, self.run_ocr_update)

if __name__ == "__main__":
    # Display startup information
    print("=" * 60)
    print("🔘 FLOATING BUTTONS - Medical Imaging Automation Tool")
    print("=" * 60)
    print("📋 Features:")
    print("   • InteleViewer image controls (rotate, flip, zoom)")
    print("   • MSK X-ray study detection via OCR")
    print("   • Auto-paste templates for MSK studies")
    print("   • Dynamic MSK identifiers file support")
    print("   • Change detection for CPU efficiency")
    print("   • Multi-monitor support")
    print()
    print("📊 Status Indicators:")
    print("   • MSK NR   = MSK study with No Report (auto-paste ready)")
    print("   • NOT MSK NR = Non-MSK study with No Report")
    print("   • R        = Report present")
    print("   • (XX%)    = OCR efficiency percentage")
    print()
    print("🎯 OCR Monitoring:")
    print("   • Empty check region:", EMPTY_CHECK_REGION_COORDINATES)
    print("   • Description region:", SCREEN_REGION_TO_CAPTURE)
    print("   • Auto-paste enabled:", ENABLE_AUTO_PASTE)
    print("   • Change detection enabled:", CHANGE_DETECTION_ENABLED)
    print("   • MSK identifiers file:", MSK_IDENTIFIERS_FILE)
    print()
    print("🚀 Starting application...")
    print("=" * 60)
    
    app = FloatingButtons()
    app.run() 