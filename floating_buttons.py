import tkinter as tk
import win32gui
import win32con
import json
import os

def find_window_by_title_substring(substring, exclude_substring=None):
    """Find a window by substring match in title."""
    def callback(hwnd, windows):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if substring.lower() in title.lower():
                if exclude_substring is None or exclude_substring.lower() not in title.lower():
                    windows.append(hwnd)
    
    windows = []
    win32gui.EnumWindows(callback, windows)
    return windows[0] if windows else None

def get_monitor_geometry(monitor_index):
    """Get the geometry (top-left coordinates) for a specific monitor."""
    import win32api
    try:
        monitors = win32api.EnumDisplayMonitors()
        if 0 <= monitor_index < len(monitors):
            monitor_info = win32api.GetMonitorInfo(monitors[monitor_index][0])
        monitor_rect = monitor_info['Monitor']
            return (monitor_rect[0], monitor_rect[1])  # (left, top)
    except Exception as e:
        print(f"Error getting monitor geometry: {e}")
        return None

# Position saving/loading
POSITIONS_FILE = "floating_buttons_positions.json"

def load_positions():
    """Load saved window positions from file."""
    try:
        if os.path.exists(POSITIONS_FILE):
            with open(POSITIONS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"Error loading positions: {e}")
    return {}

def save_positions(positions):
    """Save window positions to file."""
    try:
        with open(POSITIONS_FILE, 'w') as f:
            json.dump(positions, f, indent=2)
    except Exception as e:
        print(f"Error saving positions: {e}")

class FloatingButtons:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Floating Buttons")
        self.root.config(bg='black')
        
        # Load saved positions
        self.positions = load_positions()
        
        # Set window size and calculate position for Monitor #3
        window_width = 120
        window_height = 175  # Height for drag bar + 2 rows of buttons + zoom button
        target_monitor_index = 2  # Monitor #3 (0-indexed)
        relative_x = 15
        relative_y = 620
        
        # Use saved position if available, otherwise calculate default
        if 'main' in self.positions:
            saved_pos = self.positions['main']
            geometry_string = f"{window_width}x{window_height}+{saved_pos['x']}+{saved_pos['y']}"
        else:
        monitor_pos = get_monitor_geometry(target_monitor_index)
        
        if monitor_pos:
            monitor_left, monitor_top = monitor_pos
            target_x = monitor_left + relative_x
            target_y = monitor_top + relative_y
            geometry_string = f"{window_width}x{window_height}+{target_x}+{target_y}"
        else:
            print(f"Could not get geometry for monitor {target_monitor_index}. Using default position.")
            geometry_string = f"{window_width}x{window_height}+100+100"
        
        self.root.geometry(geometry_string)
        
        # Save position when window is moved
        self.root.bind("<ButtonRelease-1>", self.save_window_positions)
        
        # Make window stay on top and remove window decorations
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)
        
        # Create the main content frame
        self.frame = tk.Frame(self.root, bg='black')
        self.frame.pack(expand=True, fill='both')
        
        # Configure grid layout: drag handle row + 3 rows of buttons
        self.frame.grid_rowconfigure(0, weight=0)  # Row 0 for drag handle (fixed height)
        self.frame.grid_rowconfigure(1, weight=1)  # Row 1 for buttons
        self.frame.grid_rowconfigure(2, weight=1)  # Row 2 for buttons
        self.frame.grid_rowconfigure(3, weight=1)  # Row 3 for zoom button
        self.frame.grid_columnconfigure(0, weight=1)
        self.frame.grid_columnconfigure(1, weight=1)
        
        # Button styling
        off_white_color = '#CCCCCC'
        button_font = ('Segoe UI Symbol', 16, 'bold')
        button_options = {
            'bg': 'black',
            'fg': off_white_color,
            'font': button_font,
            'borderwidth': 0,
            'relief': 'flat',
            'activebackground': '#333333',
            'activeforeground': off_white_color,
            'takefocus': False
        }
        border_color = off_white_color
        border_thickness = 1
        
        # Create drag handle bar spanning full width at top
        drag_bar_frame = tk.Frame(self.frame, bg='black', height=15)
        drag_bar_frame.grid(row=0, column=0, columnspan=2, sticky='ew', padx=2, pady=(2, 2))
        drag_bar_frame.grid_propagate(False)  # Prevent frame from expanding
        
        # Visual indicator for drag bar (horizontal dots in corner)
        self.drag_indicator = tk.Label(drag_bar_frame, text="⋯", font=("Segoe UI", 10), 
                                       bg='black', fg='#666666')
        self.drag_indicator.pack(side=tk.RIGHT, padx=2)
        
        # Make window draggable from the drag bar
        drag_bar_frame.bind("<Button-1>", self.start_drag)
        drag_bar_frame.bind("<B1-Motion>", self.on_drag)
        self.drag_indicator.bind("<Button-1>", self.start_drag)
        self.drag_indicator.bind("<B1-Motion>", self.on_drag)
        
        self.buttons = []
        
        # Define button specs (text, command)
        button_specs = [
            [("↕", lambda: self.send_key_to_inteleviewer('ctrl', 'v')), 
             ("↔", lambda: self.send_key_to_inteleviewer('ctrl', 'h'))],
            [("↺", lambda: self.send_key_to_inteleviewer(',')), 
             ("↻", lambda: self.send_key_to_inteleviewer('.'))]
        ]
        
        # Create buttons in a loop using frame-as-border technique
        # Start at row 1 (after drag handle row)
        for r, row_specs in enumerate(button_specs):
            for c, (text, command) in enumerate(row_specs):
                # Create border frame
                border_frame = tk.Frame(self.frame, bg=border_color)
                border_frame.grid(row=r+1, column=c, padx=1, pady=1, sticky='nsew')
                
                # Create button inside border
                button = tk.Button(border_frame, text=text, command=command, **button_options)
                button.pack(expand=True, fill='both', padx=border_thickness, pady=border_thickness)
                
                # Add hover effects
                button.bind("<Enter>", lambda e, b=button: b.config(fg='white'))
                button.bind("<Leave>", lambda e, b=button: b.config(fg=off_white_color))
                
                self.buttons.append(button)

        # Add 5th button (Zoom Out) spanning bottom
        zoom_out_text = "Zoom Out"
        zoom_out_command = lambda: self.send_key_to_inteleviewer('-')

        border_frame5 = tk.Frame(self.frame, bg=border_color)
        border_frame5.grid(row=3, column=0, columnspan=2, padx=1, pady=1, sticky='nsew')

        zoom_button_options = button_options.copy()
        zoom_button_options['font'] = ('Segoe UI', 11, 'bold')
        button5 = tk.Button(border_frame5, text=zoom_out_text, command=zoom_out_command, 
                           **zoom_button_options)
        button5.pack(expand=True, fill='both', padx=border_thickness, pady=border_thickness)
        
        # Add hover effects to zoom button
        button5.bind("<Enter>", lambda e: button5.config(fg='white'))
        button5.bind("<Leave>", lambda e: button5.config(fg=off_white_color))

        self.buttons.append(button5)
        
        # Initialize drag state
        self._drag_start_x = 0
        self._drag_start_y = 0
        
        # Add right-click menu to close window
        self.create_context_menu()
        
        print("✅ Floating Buttons (main) initialized")
        print("   Buttons: ↕ ↔ ↺ ↻ Zoom Out")
        print("   Drag from corner handle (⋯) to move")
        print("   Right-click for menu")
        
        # Create second window (Copy | Prior)
        self.create_copy_prior_window()
    
    def create_copy_prior_window(self):
        """Create the second window with Copy | Prior controls."""
        self.copy_prior_window = tk.Toplevel(self.root)
        self.copy_prior_window.title("Copy Prior")
        self.copy_prior_window.config(bg='black')
        
        # Window size - narrow and just tall enough for text
        cp_width = 120
        cp_height = 30
        
        # Use saved position if available, otherwise default
        if 'copy_prior' in self.positions:
            saved_pos = self.positions['copy_prior']
            cp_geometry = f"{cp_width}x{cp_height}+{saved_pos['x']}+{saved_pos['y']}"
        else:
            # Default: position below the main window
            main_x = self.root.winfo_x()
            main_y = self.root.winfo_y()
            cp_geometry = f"{cp_width}x{cp_height}+{main_x}+{main_y + 185}"
        
        self.copy_prior_window.geometry(cp_geometry)
        self.copy_prior_window.attributes('-topmost', True)
        self.copy_prior_window.overrideredirect(True)
        
        # Main frame
        cp_frame = tk.Frame(self.copy_prior_window, bg='black')
        cp_frame.pack(fill=tk.BOTH, expand=True)
        
        # Configure grid: one row with 4 columns (copy, separator, prior, drag handle)
        cp_frame.grid_columnconfigure(0, weight=1)  # copy
        cp_frame.grid_columnconfigure(1, weight=0)  # separator
        cp_frame.grid_columnconfigure(2, weight=1)  # prior
        cp_frame.grid_columnconfigure(3, weight=0)  # drag handle
        
        off_white = '#CCCCCC'
        
        # "copy" label (clickable)
        self.copy_label = tk.Label(cp_frame, text="copy", font=("Segoe UI", 11, "bold"), 
                                   bg='black', fg=off_white, cursor='hand2')
        self.copy_label.grid(row=0, column=0, sticky='ew', padx=5)
        self.copy_label.bind("<Button-1>", lambda e: self.on_copy_click(e))
        self.copy_label.bind("<Enter>", lambda e: self.copy_label.config(fg='white'))
        self.copy_label.bind("<Leave>", lambda e: self.copy_label.config(fg=off_white))
        
        # Separator "|"
        separator = tk.Label(cp_frame, text="|", font=("Segoe UI", 11, "bold"), 
                           bg='black', fg='#666666')
        separator.grid(row=0, column=1)
        
        # "prior" label (clickable)
        self.prior_label = tk.Label(cp_frame, text="prior", font=("Segoe UI", 11, "bold"), 
                                    bg='black', fg=off_white, cursor='hand2')
        self.prior_label.grid(row=0, column=2, sticky='ew', padx=5)
        self.prior_label.bind("<Button-1>", lambda e: self.on_prior_click(e))
        self.prior_label.bind("<Enter>", lambda e: self.prior_label.config(fg='white'))
        self.prior_label.bind("<Leave>", lambda e: self.prior_label.config(fg=off_white))
        
        # Small drag handle on the right
        drag_handle = tk.Label(cp_frame, text="⋯", font=("Segoe UI", 8), 
                              bg='black', fg='#666666', cursor='fleur')
        drag_handle.grid(row=0, column=3, padx=(2, 4))
        drag_handle.bind("<Button-1>", self.start_drag_cp)
        drag_handle.bind("<B1-Motion>", self.on_drag_cp)
        
        # Save position when moved
        self.copy_prior_window.bind("<ButtonRelease-1>", self.save_window_positions)
        
        # Initialize drag state for copy_prior window
        self._drag_cp_start_x = 0
        self._drag_cp_start_y = 0
        
        print("✅ Copy | Prior window initialized")
    
    def on_copy_click(self, event):
        """Handle copy button click - double-click offset, return mouse, then send keystrokes."""
        import pyautogui
        import time
        try:
            # Get absolute screen position of the original click
            original_x = event.widget.winfo_rootx() + event.x
            original_y = event.widget.winfo_rooty() + event.y
            
            # Calculate target position (100 down, 100 left)
            target_x = original_x - 100
            target_y = original_y + 100
            
            # Simulate two clicks at target position (50ms apart)
            pyautogui.click(target_x, target_y)
            time.sleep(0.05)  # 50ms delay
            pyautogui.click(target_x, target_y)
            print(f"🔘 Copy clicked at ({original_x}, {original_y}) - double-clicked at ({target_x}, {target_y})")
            
            # Return mouse to original position
            pyautogui.moveTo(original_x, original_y)
            print(f"   Mouse returned to ({original_x}, {original_y})")
            
            # Send Ctrl+Shift+R
            pyautogui.hotkey('ctrl', 'shift', 'r')
            print(f"   Sent: Ctrl+Shift+R")
            
            # Wait 250ms
            time.sleep(0.25)
            
            # Send Alt+Shift+F3
            pyautogui.hotkey('alt', 'shift', 'f3')
            print(f"   Sent: Alt+Shift+F3")
        except Exception as e:
            print(f"Error in copy click: {e}")
    
    def on_prior_click(self, event):
        """Handle prior button click - double-click offset, return mouse, then send keystrokes."""
        import pyautogui
        import time
        try:
            # Get absolute screen position of the original click
            original_x = event.widget.winfo_rootx() + event.x
            original_y = event.widget.winfo_rooty() + event.y
            
            # Calculate target position (100 down, 100 right)
            target_x = original_x + 100
            target_y = original_y + 100
            
            # Simulate two clicks at target position (50ms apart)
            pyautogui.click(target_x, target_y)
            time.sleep(0.05)  # 50ms delay
            pyautogui.click(target_x, target_y)
            print(f"🔘 Prior clicked at ({original_x}, {original_y}) - double-clicked at ({target_x}, {target_y})")
            
            # Return mouse to original position
            pyautogui.moveTo(original_x, original_y)
            print(f"   Mouse returned to ({original_x}, {original_y})")
            
            # Send Ctrl+Shift+R
            pyautogui.hotkey('ctrl', 'shift', 'r')
            print(f"   Sent: Ctrl+Shift+R")
            
            # Wait 250ms
            time.sleep(0.25)
            
            # Send Alt+Shift+F3
            pyautogui.hotkey('alt', 'shift', 'f3')
            print(f"   Sent: Alt+Shift+F3")
        except Exception as e:
            print(f"Error in prior click: {e}")
    
    def start_drag_cp(self, event):
        """Start dragging the copy_prior window."""
        self._drag_cp_start_x = event.x
        self._drag_cp_start_y = event.y
    
    def on_drag_cp(self, event):
        """Handle copy_prior window dragging."""
        x = self.copy_prior_window.winfo_x() + event.x - self._drag_cp_start_x
        y = self.copy_prior_window.winfo_y() + event.y - self._drag_cp_start_y
        self.copy_prior_window.geometry(f"+{x}+{y}")
    
    def save_window_positions(self, event=None):
        """Save positions of both windows."""
        try:
            self.positions['main'] = {
                'x': self.root.winfo_x(),
                'y': self.root.winfo_y()
            }
            self.positions['copy_prior'] = {
                'x': self.copy_prior_window.winfo_x(),
                'y': self.copy_prior_window.winfo_y()
            }
            save_positions(self.positions)
        except Exception as e:
            print(f"Error saving positions: {e}")
    
    def create_context_menu(self):
        """Create a right-click context menu for window management."""
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Close", command=self.quit_app)
        
        # Bind right-click on all buttons, frame, and drag bar
        for button in self.buttons:
            button.bind("<Button-3>", self.show_context_menu)
        self.frame.bind("<Button-3>", self.show_context_menu)
        self.drag_indicator.bind("<Button-3>", self.show_context_menu)
        
    def show_context_menu(self, event):
        """Show the context menu."""
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()
    
    def quit_app(self):
        """Quit the application."""
        print("Closing Floating Buttons...")
        self.root.quit()
    
    def start_drag(self, event):
        """Start dragging the window."""
        self._drag_start_x = event.x
        self._drag_start_y = event.y
    
    def on_drag(self, event):
        """Handle window dragging."""
        x = self.root.winfo_x() + event.x - self._drag_start_x
        y = self.root.winfo_y() + event.y - self._drag_start_y
        self.root.geometry(f"+{x}+{y}")
    
    def send_key_to_inteleviewer(self, *keys):
        """Send keypress(es) to InteleViewer window."""
        inteleviewer_hwnd = find_window_by_title_substring("InteleViewer")

        if inteleviewer_hwnd:
            # Bring InteleViewer to foreground
            win32gui.ShowWindow(inteleviewer_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(inteleviewer_hwnd)
            
            # Small delay to ensure window is ready
            import time
            time.sleep(0.05)
            
            # Send the key(s)
            import pyautogui
            if len(keys) == 1:
                # Single key
                pyautogui.press(keys[0])
            else:
                # Key combination (e.g., ctrl+v)
                pyautogui.hotkey(*keys)
            
            print(f"Sent key(s) to InteleViewer: {'+'.join(keys)}")
        else:
            print("InteleViewer window not found!")
    
    def run(self):
        """Start the application."""
        self.root.mainloop()

if __name__ == "__main__":
    print("=" * 60)
    print("🔘 FLOATING BUTTONS - InteleViewer Controls")
    print("=" * 60)
    print("📋 Features:")
    print("   • InteleViewer image controls (rotate, flip, zoom)")
    print("   • Copy | Prior window for quick actions")
    print("   • Drag from corner handles (⋯) to reposition")
    print("   • Auto-saves window positions")
    print("   • Right-click for menu")
    print()
    print("🎮 Main Window Controls:")
    print("   ↕  = Vertical flip (Ctrl+V)")
    print("   ↔  = Horizontal flip (Ctrl+H)")
    print("   ↺  = Rotate left (,)")
    print("   ↻  = Rotate right (.)")
    print("   Zoom Out = Zoom out (-)")
    print()
    print("🎮 Copy | Prior Window:")
    print("   copy  = (functionality to be defined)")
    print("   prior = (functionality to be defined)")
    print()
    print("🚀 Starting application...")
    print("=" * 60)
    
    app = FloatingButtons()
    app.run() 
