import tkinter as tk
from tkinter import ttk
import pyautogui
import win32gui
import win32con
import keyboard
import time # Added for sleep
import win32api # Added for monitor info

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

class FloatingButtons:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Floating Buttons")
        self.root.config(bg='black') # Set root background
        
        # --- Set window size and calculate position for Monitor #3 --- #
        window_width = 120
        window_height = 180
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
        
        # Configure grid layout within the main frame for 3 rows
        self.frame.grid_rowconfigure(0, weight=1)
        self.frame.grid_rowconfigure(1, weight=1)
        self.frame.grid_rowconfigure(2, weight=1) # Add third row configuration
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_columnconfigure(1, weight=1)
        
        # --- Create buttons with explicit frame borders --- #
        button_font = ('Segoe UI Symbol', 16, 'bold') # Increased size, added bold
        off_white_color = '#CCCCCC' # Less bright white
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
                border_frame.grid(row=r, column=c, padx=1, pady=1, sticky='nsew') # padx/y here is space *between* borders
                
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
        border_frame5.grid(row=2, column=0, columnspan=2, padx=1, pady=1, sticky='nsew')

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

if __name__ == "__main__":
    app = FloatingButtons()
    app.run() 