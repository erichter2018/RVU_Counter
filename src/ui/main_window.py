"""Main application window for RVU Counter."""

import tkinter as tk
from tkinter import ttk, messagebox
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, TYPE_CHECKING
import threading
import time

try:
    from pywinauto import Desktop
except ImportError:
    Desktop = None  # Will handle this gracefully

from ..core.config import APP_VERSION, DEFAULT_SHIFT_LENGTH_HOURS, DEFAULT_MIN_STUDY_SECONDS
from ..core.platform_utils import (
    get_all_monitor_bounds,
    get_primary_monitor_bounds,
    is_point_on_any_monitor,
    find_nearest_monitor_for_window
)
from ..data import RVUData
from ..logic import StudyTracker
from ..logic.study_matcher import match_study_type

# Import extraction utilities
from ..utils.window_extraction import (
    _window_text_with_timeout,
    find_elements_by_automation_id
)
from ..utils.powerscribe_extraction import find_powerscribe_window
from ..utils.mosaic_extraction import (
    find_mosaic_window,
    find_mosaic_webview_element,
    extract_mosaic_data_v2,
    extract_mosaic_data
)
from ..utils.clario_extraction import extract_clario_patient_class

# Lazy imports to avoid circular dependencies
if TYPE_CHECKING:
    from .settings_window import SettingsWindow
    from .statistics_window import StatisticsWindow

logger = logging.getLogger(__name__)

# Module-level globals for PowerScribe/Mosaic detection
_cached_desktop = None
_timeout_thread_count = 0


def _extract_accession_number(entry: str) -> str:
    """Extract pure accession number from entry string.
    
    Handles formats like "ACC1234 (CT HEAD)" -> "ACC1234" or just "ACC1234" -> "ACC1234".
    Used by multi-accession tracking logic.
    
    Args:
        entry: Raw listbox entry or accession string
        
    Returns:
        Stripped accession number
    """
    if '(' in entry and ')' in entry:
        m = re.match(r'^([^(]+)', entry)
        return m.group(1).strip() if m else entry.strip()
    return entry.strip()


def quick_check_powerscribe() -> bool:
    """Quick check if PowerScribe window exists (fast, no deep inspection)."""
    global _cached_desktop
    
    if Desktop is None:
        return False
    
    if _cached_desktop is None:
        _cached_desktop = Desktop(backend="uia")
    desktop = _cached_desktop
    
    # Just check if window with PowerScribe title exists
    for title in ["PowerScribe 360 | Reporting", "PowerScribe 360", "PowerScribe 360 - Reporting", 
                  "Nuance PowerScribe 360", "Powerscribe 360"]:
        try:
            windows = desktop.windows(title=title, visible_only=True)
            if windows:
                return True
        except Exception as e:
            logger.debug(f"Error checking PowerScribe window '{title}': {e}")
            continue
    return False


def quick_check_mosaic() -> bool:
    """Quick check if Mosaic window exists (fast, no deep inspection)."""
    global _cached_desktop
    
    if Desktop is None:
        return False
    
    if _cached_desktop is None:
        _cached_desktop = Desktop(backend="uia")
    desktop = _cached_desktop
    
    try:
        all_windows = desktop.windows(visible_only=True)
        for window in all_windows:
            try:
                # Quick title check without deep inspection
                title = window.window_text()
                title_lower = title.lower()
                # Check for MosaicInfoHub variations and Mosaic Reporting
                if ("mosaicinfohub" in title_lower or 
                    "mosaic info hub" in title_lower or 
                    "mosaic infohub" in title_lower or
                    ("mosaic" in title_lower and "reporting" in title_lower)):
                    # Exclude test windows
                    if not any(x in title_lower for x in ["rvu counter", "test", "viewer", "diagnostic"]):
                        return True
            except Exception as e:
                logger.debug(f"Error checking Mosaic window: {e}")
                continue
    except Exception as e:
        logger.debug(f"Error iterating windows for Mosaic check: {e}")
    return False

class RVUCounterApp:
    """Main application class."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("RVU Counter")
        self.root.geometry("240x500")  # Default size
        self.root.minsize(200, 350)  # Minimum size
        self.root.resizable(True, True)
        
        # Window dragging state
        self.drag_start_x = 0
        self.drag_start_y = 0
        
        # Data management
        self.data_manager = RVUData()
        
        # Update manager
        from ..core.update_manager import UpdateManager
        self.update_manager = UpdateManager()
        self.update_info = None
        
        # Set borderless mode based on settings (default False)
        borderless_mode = self.data_manager.data["settings"].get("borderless_mode", False)
        if borderless_mode:
            self.root.overrideredirect(True)
        
        # Set stay on top based on settings (default True if not set)
        stay_on_top = self.data_manager.data["settings"].get("stay_on_top", True)
        self.root.attributes("-topmost", stay_on_top)
        
        # Load saved window position and size or use default (after data_manager is initialized)
        window_pos = self.data_manager.data.get("window_positions", {}).get("main", None)
        if window_pos:
            width = window_pos.get('width', 240)
            height = window_pos.get('height', 500)
            x = window_pos['x']
            y = window_pos['y']
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        else:
            # First run: center on primary monitor
            try:
                primary = get_primary_monitor_bounds()
                primary_width = primary[2] - primary[0]
                primary_height = primary[3] - primary[1]
                x = primary[0] + (primary_width - 240) // 2
                y = primary[1] + (primary_height - 500) // 2
                self.root.geometry(f"240x500+{x}+{y}")
                logger.info(f"First run: positioning window at ({x}, {y}) on primary monitor")
            except Exception as e:
                logger.error(f"Error positioning window on first run: {e}")
        
        # Schedule post-mapping validation to ensure window is visible
        self.root.after(100, self._ensure_window_visible)
        
        # Initialize last saved position tracking
        self._last_saved_main_x = self.root.winfo_x()
        self._last_saved_main_y = self.root.winfo_y()
        self.tracker = StudyTracker(
            min_seconds=self.data_manager.data["settings"]["min_study_seconds"]
        )
        
        # State
        self.shift_start: Optional[datetime] = None
        self.effective_shift_start: Optional[datetime] = None
        self.projected_shift_end: Optional[datetime] = None
        self.is_running = False
        self.current_window = None
        self.refresh_interval = 300  # 300ms for faster completion detection
        
        # Typical shift times (calculated from historical data, defaults to 11pm-8am)
        self.typical_shift_start_hour = 23  # 11pm default
        self.typical_shift_end_hour = 8     # 8am default
        self._calculate_typical_shift_times()  # Update from historical data
        
        # Check for updates
        self._check_for_updates()
        
        # Check if this is first run after update (show What's New)
        self._check_version_and_show_whats_new()
        
        # Adaptive polling variables for PowerScribe worker thread
        import time
        self._last_accession_seen = ""
        self._last_data_change_time = time.time()  # Initialize to current time
        self._current_poll_interval = 1.0  # Start with moderate polling
        
        # Current detected data (must be initialized before create_ui)
        self.current_accession = ""
        self.current_procedure = ""
        self.current_patient_class = ""
        self.current_study_type = ""
        self.current_study_rvu = 0.0
        self.current_multiple_accessions = []  # List of accession numbers when multiple
        
        # Multi-accession tracking
        self.multi_accession_data = {}  # accession -> {procedure, study_type, rvu, patient_class}
        self.multi_accession_mode = False  # True when tracking a multi-accession study
        self.multi_accession_start_time = None  # When we started tracking this multi-accession study
        self.multi_accession_last_procedure = ""  # Last procedure seen in multi-accession mode
        
        # Cache for performance
        self.cached_window = None
        self.cached_elements = {}  # automation_id -> element reference
        self.last_record_count = 0  # Track when to rebuild widgets
        self.no_report_skip_count = 0  # Skip expensive searches when no report is open
        
        # Background thread for PowerScribe operations
        self._ps_lock = threading.Lock()
        self._ps_data = {}  # Data from PowerScribe (updated by background thread)
        self._last_clario_accession = ""  # Track last accession we queried Clario for
        self._clario_patient_class_cache = {}  # Cache Clario patient class by accession
        self._pending_studies = {}  # Track accession -> procedure for studies detected but not yet added
        
        # Auto-switch data source detection
        self._active_source = None  # "PowerScribe" or "Mosaic" - currently active source
        self._primary_source = "PowerScribe"  # Which source to check first
        self._last_secondary_check = 0  # Timestamp of last secondary source check
        self._secondary_check_interval = 5.0  # How often to check secondary when primary is idle (seconds)
        
        # Inactivity auto-end shift tracker
        self.last_activity_time = datetime.now()
        self._auto_end_prompt_shown = False
        
        self._ps_thread_running = True
        self._ps_thread = threading.Thread(target=self._powerscribe_worker, daemon=True)
        self._ps_thread.start()
        
        # Create UI
        self.create_ui()
        
        # Initialize time labels list for time display updates
        self.time_labels = []
        
        # Start timer to update time display every 5 seconds if show_time is enabled
        self._update_time_display()
        
        # Auto-resume shift if enabled and shift was running (no shift_end means it was interrupted)
        if self.data_manager.data["settings"].get("auto_start", False):
            current_shift = self.data_manager.data["current_shift"]
            shift_start = current_shift.get("shift_start")
            shift_end = current_shift.get("shift_end")
            
            # Only resume if there's a shift_start but NO shift_end (app crashed while running)
            if shift_start and not shift_end:
                try:
                    self.shift_start = datetime.fromisoformat(shift_start)
                    # Restore effective shift start and projected end if available
                    effective_start = current_shift.get("effective_shift_start")
                    projected_end = current_shift.get("projected_shift_end")
                    if effective_start:
                        self.effective_shift_start = datetime.fromisoformat(effective_start)
                    else:
                        # Fall back to calculating it
                        minutes_into_hour = self.shift_start.minute
                        if minutes_into_hour <= 15:
                            self.effective_shift_start = self.shift_start.replace(minute=0, second=0, microsecond=0)
                        else:
                            self.effective_shift_start = self.shift_start
                    if projected_end:
                        self.projected_shift_end = datetime.fromisoformat(projected_end)
                    else:
                        # Fall back to calculating it
                        shift_length = self.data_manager.data["settings"].get("shift_length_hours", 9)
                        self.projected_shift_end = self.effective_shift_start + timedelta(hours=shift_length)
                    
                    self.is_running = True
                    # Update button and UI to reflect running state
                    self.start_btn.config(text="Stop Shift")
                    self.root.title("RVU Counter - Running")
                    self.update_shift_start_label()
                    self.update_recent_studies_label()
                    # Update display to show correct counters
                    self.update_display()
                    logger.info(f"Auto-resumed shift from {self.shift_start} (app was interrupted)")
                except Exception as e:
                    logger.error(f"Error parsing shift_start for auto-resume: {e}")
            # If shift_end exists, the shift was properly stopped - don't auto-resume
            elif shift_start and shift_end:
                logger.info("Auto-resume skipped: shift was properly stopped")
        else:
            # Auto-resume is disabled, but check if we're in a running state anyway
            # This handles cases where the state might be inconsistent
            current_shift = self.data_manager.data["current_shift"]
            shift_start = current_shift.get("shift_start")
            shift_end = current_shift.get("shift_end")
            if shift_start and not shift_end:
                # There's an active shift but auto-resume is disabled
                # Still update the label to show the correct state
                try:
                    self.shift_start = datetime.fromisoformat(shift_start)
                    self.is_running = True
                    self.start_btn.config(text="Stop Shift")
                    self.root.title("RVU Counter - Running")
                    self.update_shift_start_label()
                    self.update_recent_studies_label()
                except Exception as e:
                    logger.error(f"Error updating UI for active shift: {e}")
        
        # Always ensure label is updated based on current state (fallback)
        self.update_recent_studies_label()
        
        self.setup_refresh()
        self.setup_time_sensitive_update()  # Start time-sensitive counter updates (5s interval)
        
        # Check if we should prompt for cloud backup setup
        self._check_first_time_backup_prompt()
        
        logger.info("RVU Counter application started")
    
    def create_ui(self):
        """Create the user interface."""
        # Create style
        self.style = ttk.Style()
        self.style.configure("Red.TLabelframe.Label", foreground="red")
        
        # Apply theme based on settings
        self.apply_theme()
        
        # Main frame - minimal top/bottom padding, normal sides
        main_frame = ttk.Frame(self.root, padding=(5, 2, 5, 5))  # left, top, right, bottom
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Store reference to main_frame for later use
        self.main_frame = main_frame
        
        # Mini window reference
        self.mini_window = None
        
        # Add close button if in borderless mode
        borderless_mode = self.data_manager.data["settings"].get("borderless_mode", False)
        if borderless_mode:
            dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
            
            self.close_btn_main = tk.Label(
                main_frame,
                text="X",
                font=("Arial", 7, "bold"),
                bg="#ff6666" if dark_mode else "#ffcccc",  # Light red background
                fg="#ffffff" if dark_mode else "#cc0000",  # White or dark red text
                cursor="hand2",
                padx=2,
                pady=0,
                relief=tk.RAISED,
                borderwidth=1
            )
            self.close_btn_main.place(relx=1.0, x=-1, y=1, anchor=tk.NE)
            self.close_btn_main.bind("<Button-1>", lambda e: self.root.quit())
            self.close_btn_main.bind("<Enter>", lambda e: self.close_btn_main.config(bg="#ff0000", fg="#ffffff"))
            self.close_btn_main.bind("<Leave>", lambda e: self.close_btn_main.config(
                bg="#ff6666" if dark_mode else "#ffcccc",
                fg="#ffffff" if dark_mode else "#cc0000"
            ))
            self.close_btn_main.tkraise()  # Ensure it's on top
        
        # Title bar is draggable (bind to main frame)
        main_frame.bind("<Button-1>", self.start_drag)
        main_frame.bind("<B1-Motion>", self.on_drag)
        main_frame.bind("<ButtonRelease-1>", self.on_drag_end)
        main_frame.bind("<Double-Button-1>", self.on_double_click)
        
        # Top section using grid for precise vertical control
        top_section = ttk.Frame(main_frame)
        top_section.pack(fill=tk.X)
        top_section.columnconfigure(0, weight=0)
        top_section.columnconfigure(1, weight=1)
        
        # Row 0: Button and shift start time
        self.start_btn = ttk.Button(top_section, text="Start Shift", command=self.start_shift, width=12)
        self.start_btn.grid(row=0, column=0, sticky=tk.W, pady=(0, 0))
        
        self.shift_start_label = ttk.Label(top_section, text="", font=("Arial", 8), foreground="gray")
        self.shift_start_label.grid(row=0, column=1, sticky=tk.W, padx=(8, 0), pady=(0, 0))
        
        # Row 0: Tools icon in very top right corner
        tools_frame = ttk.Frame(top_section)
        tools_frame.grid(row=0, column=1, sticky=tk.NE, padx=(0, 2), pady=(2, 0))
        
        self.tools_icon = tk.Label(tools_frame, text="🔧", font=("Arial", 12), 
                                   fg="gray", cursor="hand2",
                                   bg=self.root.cget('bg'))
        self.tools_icon.pack()
        self.tools_icon.bind("<Button-1>", lambda e: self.open_tools())
        
        # Row 1: Data source indicator (left) and version (right)
        self.data_source_indicator = ttk.Label(top_section, text="detecting...", 
                                               font=("Arial", 7), foreground="gray", cursor="hand2")
        self.data_source_indicator.grid(row=1, column=0, sticky=tk.W, padx=(2, 0), pady=(0, 0))
        self.data_source_indicator.bind("<Button-1>", lambda e: self._toggle_data_source())
        
        # Version info on the right with backup status
        version_frame = ttk.Frame(top_section)
        version_frame.grid(row=1, column=1, sticky=tk.E, padx=(0, 2), pady=(0, 0))
        
        # Backup status indicator (clickable to open settings)
        self.backup_status_label = tk.Label(version_frame, text="", font=("Arial", 7), 
                                            fg="gray", cursor="hand2",
                                            bg=self.root.cget('bg'))
        self.backup_status_label.pack(side=tk.LEFT, padx=(0, 5))
        self.backup_status_label.bind("<Button-1>", lambda e: self.open_settings())
        
        # Update backup status display
        self._update_backup_status_display()
        
        version_text = f"v{APP_VERSION}"
        self.version_label = ttk.Label(version_frame, text=version_text, font=("Arial", 7), foreground="gray")
        self.version_label.pack(side=tk.LEFT)
        
        # Update available button (hidden by default)
        self.update_btn = tk.Label(version_frame, text="Update!", font=("Arial", 8, "bold"),
                                   fg="white", bg="#ff6b00", cursor="hand2", padx=5, pady=2)
        self.update_btn.bind("<Button-1>", lambda e: self._handle_update_click())
        # Don't pack yet, wait for check
        
        # Counters frame - use tk.LabelFrame with explicit border control for tighter spacing
        self.counters_frame = tk.LabelFrame(main_frame, bd=1, relief=tk.GROOVE, padx=2, pady=2)
        self.counters_frame.pack(fill=tk.X, pady=(0, 3))
        counters_frame = self.counters_frame  # Keep local reference for code below
        
        # Inner frame to center the content
        counters_inner = ttk.Frame(counters_frame)
        counters_inner.pack(expand=True)  # Centers horizontally
        
        # Counter labels with aligned columns (inside centered inner frame)
        row = 0
        
        # Total
        self.total_label_text = ttk.Label(counters_inner, text="total wRVU:", font=("Arial", 9), anchor=tk.E)
        self.total_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        total_value_frame = ttk.Frame(counters_inner)
        total_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.total_label = ttk.Label(total_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.total_label.pack(side=tk.LEFT)
        self.total_comp_label = tk.Label(total_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.total_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.total_value_frame = total_value_frame
        row += 1
        
        # Average per hour
        self.avg_label_text = ttk.Label(counters_inner, text="avg/hour:", font=("Arial", 9), anchor=tk.E)
        self.avg_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        avg_value_frame = ttk.Frame(counters_inner)
        avg_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.avg_label = ttk.Label(avg_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.avg_label.pack(side=tk.LEFT)
        self.avg_comp_label = tk.Label(avg_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.avg_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.avg_value_frame = avg_value_frame
        row += 1
        
        # Last hour
        self.last_hour_label_text = ttk.Label(counters_inner, text="last hour:", font=("Arial", 9), anchor=tk.E)
        self.last_hour_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        last_hour_value_frame = ttk.Frame(counters_inner)
        last_hour_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.last_hour_label = ttk.Label(last_hour_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.last_hour_label.pack(side=tk.LEFT)
        self.last_hour_comp_label = tk.Label(last_hour_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.last_hour_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.last_hour_value_frame = last_hour_value_frame
        row += 1
        
        # Last full hour - format: "8pm-9pm hour: x.x"
        # Use a frame to hold both the time range (smaller font) and "hour:" text
        self.last_full_hour_label_frame = ttk.Frame(counters_inner)
        self.last_full_hour_label_frame.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        self.last_full_hour_range_label = ttk.Label(self.last_full_hour_label_frame, text="", font=("Arial", 8), anchor=tk.E)
        self.last_full_hour_range_label.pack(side=tk.LEFT)
        self.last_full_hour_label_text = ttk.Label(self.last_full_hour_label_frame, text="hour:", font=("Arial", 9), anchor=tk.E)
        self.last_full_hour_label_text.pack(side=tk.LEFT, padx=(2, 0))
        last_full_hour_value_frame = ttk.Frame(counters_inner)
        last_full_hour_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.last_full_hour_label = ttk.Label(last_full_hour_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.last_full_hour_label.pack(side=tk.LEFT)
        self.last_full_hour_comp_label = tk.Label(last_full_hour_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.last_full_hour_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.last_full_hour_value_frame = last_full_hour_value_frame
        row += 1
        
        # Projected This Hour
        self.projected_label_text = ttk.Label(counters_inner, text="est this hour:", font=("Arial", 9), anchor=tk.E)
        self.projected_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        projected_value_frame = ttk.Frame(counters_inner)
        projected_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.projected_label = ttk.Label(projected_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.projected_label.pack(side=tk.LEFT)
        self.projected_comp_label = tk.Label(projected_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.projected_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.projected_value_frame = projected_value_frame
        row += 1
        
        # Projected Shift Total
        self.projected_shift_label_text = ttk.Label(counters_inner, text="est shift total:", font=("Arial", 9), anchor=tk.E)
        self.projected_shift_label_text.grid(row=row, column=0, sticky=tk.E, padx=(0, 5))
        projected_shift_value_frame = ttk.Frame(counters_inner)
        projected_shift_value_frame.grid(row=row, column=1, sticky=tk.W)
        self.projected_shift_label = ttk.Label(projected_shift_value_frame, text="0.0", font=("Arial", 8), anchor=tk.W)
        self.projected_shift_label.pack(side=tk.LEFT)
        self.projected_shift_comp_label = tk.Label(projected_shift_value_frame, text="", font=("Arial", 8), fg="dark green", bg=self.root.cget('bg'))
        self.projected_shift_comp_label.pack(side=tk.LEFT, padx=(3, 0))
        self.projected_shift_value_frame = projected_shift_value_frame
        
        # Pace Car bar - comparison vs prior shift (initially hidden)
        self.pace_car_frame = ttk.Frame(main_frame)
        # Don't pack yet - will be shown/hidden based on settings
        
        # Pace car comparison state: 'prior', 'goal', 'best_week', 'best_ever', or 'week_N'
        # Load from settings (persists between sessions)
        self.pace_comparison_mode = self.data_manager.data["settings"].get("pace_comparison_mode", "prior")
        self.pace_comparison_shift = None  # Cache of the shift data being compared (not persisted)
        
        # Container for both bars (stacked) - clickable to change comparison
        self.pace_bars_container = tk.Frame(self.pace_car_frame, bg="#e0e0e0", height=20)
        self.pace_bars_container.pack(fill=tk.X, padx=2, pady=1)
        self.pace_bars_container.pack_propagate(False)
        
        # Current bar (top) - background track
        self.pace_bar_current_track = tk.Frame(self.pace_bars_container, bg="#e8e8e8", height=9)
        self.pace_bar_current_track.place(x=0, y=1, relwidth=1.0)
        
        # Current bar fill (grows with current RVU)
        self.pace_bar_current = tk.Frame(self.pace_bar_current_track, bg="#87CEEB", height=9)  # Sky blue
        self.pace_bar_current.place(x=0, y=0, width=0)
        
        # Prior bar (bottom) - full width background
        self.pace_bar_prior_track = tk.Frame(self.pace_bars_container, bg="#B8B8DC", height=9)  # Darker lavender
        self.pace_bar_prior_track.place(x=0, y=11, relwidth=1.0)
        
        # Prior bar marker (where prior was at this time)
        self.pace_bar_prior_marker = tk.Frame(self.pace_bars_container, bg="#000000", width=2, height=9)
        
        # Bind click to all bar widgets to open comparison selector
        for widget in [self.pace_bars_container, self.pace_bar_current_track, 
                       self.pace_bar_current, self.pace_bar_prior_track, self.pace_bar_prior_marker]:
            widget.bind("<Button-1>", self._open_pace_comparison_selector)
        
        # Labels showing the comparison (using place for precise positioning)
        self.pace_label_frame = tk.Frame(self.pace_car_frame, bg=self.root.cget('bg'), height=12)
        self.pace_label_frame.pack(fill=tk.X, padx=2)
        self.pace_label_frame.pack_propagate(False)
        
        # Left side: Build string with colored numbers using place() for tight spacing
        # We'll position labels precisely to eliminate gaps
        self.pace_label_now_text = tk.Label(self.pace_label_frame, text="Now:", font=("Arial", 7), bg=self.root.cget('bg'), fg="gray", padx=0, pady=0, bd=0)
        self.pace_label_now_text.place(x=0, y=0)
        
        self.pace_label_now_value = tk.Label(self.pace_label_frame, text="", font=("Arial", 7, "bold"), bg=self.root.cget('bg'), padx=0, pady=0, bd=0)
        # Will be positioned after measuring text width
        
        self.pace_label_separator = tk.Label(self.pace_label_frame, text=" | ", font=("Arial", 7), bg=self.root.cget('bg'), fg="gray", padx=0, pady=0, bd=0)
        
        self.pace_label_prior_text = tk.Label(self.pace_label_frame, text="Prior:", font=("Arial", 7), bg=self.root.cget('bg'), fg="gray", padx=0, pady=0, bd=0)
        
        self.pace_label_prior_value = tk.Label(self.pace_label_frame, text="", font=("Arial", 7, "bold"), bg=self.root.cget('bg'), fg="#9090C0", padx=0, pady=0, bd=0)
        
        self.pace_label_time = tk.Label(self.pace_label_frame, text="", font=("Arial", 7), bg=self.root.cget('bg'), fg="gray", padx=0, pady=0, bd=0)
        
        # Right side: status
        self.pace_label_right = tk.Label(self.pace_label_frame, text="", font=("Arial", 7), bg=self.root.cget('bg'), bd=0)
        self.pace_label_right.pack(side=tk.RIGHT, padx=0, pady=0)
        
        # Show pace car if enabled in settings AND there's an active shift
        has_active_shift = self.data_manager.data["current_shift"].get("shift_start") is not None
        if self.data_manager.data["settings"].get("show_pace_car", False) and has_active_shift:
            self.pace_car_frame.pack(fill=tk.X, pady=(0, 2))
        
        # Buttons frame - centered
        buttons_frame = ttk.Frame(main_frame)
        buttons_frame.pack(pady=5)
        
        self.stats_btn = ttk.Button(buttons_frame, text="Statistics", command=self.open_statistics, width=8)
        self.stats_btn.pack(side=tk.LEFT, padx=3)
        
        self.undo_btn = ttk.Button(buttons_frame, text="Undo", command=self.undo_last, width=6, state=tk.DISABLED)
        self.undo_btn.pack(side=tk.LEFT, padx=3)
        
        # Track undo/redo state
        self.undo_used = False
        self.last_undone_study = None  # Store the last undone study for redo
        
        self.settings_btn = ttk.Button(buttons_frame, text="Settings", command=self.open_settings, width=8)
        self.settings_btn.pack(side=tk.LEFT, padx=3)
        
        # Current Study frame - pack first so it reserves space at bottom
        debug_frame = tk.LabelFrame(main_frame, text="Current Study", bd=1, relief=tk.GROOVE, padx=3, pady=3)
        debug_frame.pack(fill=tk.X, pady=(5, 0), side=tk.BOTTOM)
        
        # Accession row with duration on the right
        accession_frame = ttk.Frame(debug_frame)
        accession_frame.pack(fill=tk.X)
        
        self.debug_accession_label = ttk.Label(accession_frame, text="Accession: -", font=("Consolas", 8), foreground="gray")
        self.debug_accession_label.pack(side=tk.LEFT, anchor=tk.W)
        
        self.debug_duration_label = ttk.Label(accession_frame, text="", font=("Consolas", 8), foreground="gray")
        self.debug_duration_label.pack(side=tk.RIGHT, anchor=tk.E)
        
        self.debug_patient_class_label = ttk.Label(debug_frame, text="Patient Class: -", font=("Consolas", 8), foreground="gray")
        self.debug_patient_class_label.pack(anchor=tk.W)
        
        self.debug_procedure_label = ttk.Label(debug_frame, text="Procedure: -", font=("Consolas", 8), foreground="gray")
        self.debug_procedure_label.pack(anchor=tk.W)
        
        # Study Type with RVU frame (to align RVU to the right) - separate labels like Recent Studies
        study_type_frame = ttk.Frame(debug_frame)
        study_type_frame.pack(fill=tk.X)
        
        self.debug_study_type_prefix_label = ttk.Label(study_type_frame, text="Study Type: ", font=("Consolas", 8), foreground="gray")
        self.debug_study_type_prefix_label.pack(side=tk.LEFT, anchor=tk.W)
        
        self.debug_study_type_label = ttk.Label(study_type_frame, text="-", font=("Consolas", 8), foreground="gray")
        self.debug_study_type_label.pack(side=tk.LEFT, anchor=tk.W, padx=(0, 0))
        
        # Spacer to push RVU to the right
        spacer = ttk.Frame(study_type_frame)
        spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        self.debug_study_rvu_label = ttk.Label(study_type_frame, text="", font=("Consolas", 8), foreground="gray")
        self.debug_study_rvu_label.pack(side=tk.LEFT, anchor=tk.W, padx=(0, 0))  # Pack on LEFT right after spacer, no padding
        
        # Store debug_frame reference for resizing
        self.debug_frame = debug_frame
        
        # Recent studies frame - pack after Current Study so it fills remaining space above
        self.recent_frame = tk.LabelFrame(main_frame, text="Recent Studies", bd=1, relief=tk.GROOVE, padx=3, pady=5)
        self.recent_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Canvas with scrollbar for recent studies
        canvas_frame = ttk.Frame(self.recent_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        canvas_bg = self.theme_colors.get("canvas_bg", "#f0f0f0")
        canvas = tk.Canvas(canvas_frame, highlightthickness=0, bd=0, bg=canvas_bg)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        # Use a custom style for the scrollable frame to match canvas_bg
        self.style.configure("StudiesScrollable.TFrame", background=canvas_bg)
        self.studies_scrollable_frame = ttk.Frame(canvas, style="StudiesScrollable.TFrame")
        
        canvas_window = canvas.create_window((0, 0), window=self.studies_scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=0, pady=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Store study widgets for deletion
        self.study_widgets = []
        
        def configure_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Also update canvas height if needed
            canvas.update_idletasks()
        
        def configure_canvas_width(event):
            # Make the canvas window match the canvas width
            canvas.itemconfig(canvas_window, width=event.width)
            # Update scroll region when canvas is configured
            canvas.after_idle(lambda: canvas.configure(scrollregion=canvas.bbox("all")))
        
        self.studies_scrollable_frame.bind("<Configure>", configure_scroll_region)
        canvas.bind("<Configure>", configure_canvas_width)
        # Also bind to parent frame to ensure proper sizing
        self.recent_frame.bind("<Configure>", lambda e: canvas.update_idletasks())
        
        # Store canvas reference for scrolling
        self.studies_canvas = canvas
        
        # Store study widgets for deletion (initialized in create_ui)
        self.study_widgets = []
        
        # Bind resize event to recalculate truncation
        self.root.bind("<Configure>", self._on_window_resize)
        self._last_width = 240  # Track width for resize detection
        
        # Set initial title
        if self.is_running:
            self.root.title("RVU Counter - Running")
        else:
            self.root.title("RVU Counter - Stopped")
        
        # Update display
        self.update_display()
        
        # Set initial undo button state
        if not self.data_manager.data["current_shift"]["records"]:
            self.undo_btn.config(state=tk.DISABLED)
            self.undo_used = True
        
        # Apply theme colors to tk widgets (must be done AFTER widgets are created)
        self._update_tk_widget_colors()
        
        # Bind drag and double-click to all widgets (for borderless mode)
        self._bind_drag_to_all_widgets()
    
    def _bind_drag_to_all_widgets(self):
        """Recursively bind drag and double-click events to all widgets except buttons."""
        def bind_widget(widget):
            # Skip buttons and other interactive widgets
            widget_class = widget.winfo_class()
            if widget_class in ('Button', 'TButton', 'Entry', 'TEntry', 'Text', 'Scrollbar'):
                return
            
            # Bind drag events
            widget.bind("<Button-1>", self.start_drag, add="+")
            widget.bind("<B1-Motion>", self.on_drag, add="+")
            widget.bind("<ButtonRelease-1>", self.on_drag_end, add="+")
            widget.bind("<Double-Button-1>", self.on_double_click, add="+")
            
            # Recursively bind to all children
            for child in widget.winfo_children():
                bind_widget(child)
        
        # Start from main_frame
        bind_widget(self.main_frame)
    
    def _check_for_updates(self):
        """Check for updates in a background thread."""
        def run_check():
            try:
                available, release_info = self.update_manager.check_for_updates()
                if available:
                    # Check if this version has been skipped
                    skipped_version = self.data_manager.data["settings"].get("skipped_update_version", "")
                    new_version = release_info.get("tag_name", "").lstrip('v')
                    
                    if skipped_version and skipped_version == new_version:
                        logger.info(f"Update {new_version} was previously skipped by user")
                        return
                    
                    self.update_info = release_info
                    # Update UI in main thread
                    self.root.after(0, self._show_update_available)
            except Exception as e:
                logger.error(f"Error in update check thread: {e}")
        
        threading.Thread(target=run_check, daemon=True).start()

    def _show_update_available(self):
        """Show the update available notification."""
        self.update_btn.pack(side=tk.LEFT, padx=(5, 0))
        logger.info("Update available UI shown")

    def _handle_update_click(self):
        """Handle click on update button - show update dialog with details."""
        if not self.update_info:
            return
        
        self._show_update_dialog()
    
    def _show_update_dialog(self):
        """Show detailed update dialog with release notes and options."""
        if not self.update_info:
            return
        
        # Create dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Update Available")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Apply theme
        colors = self.get_theme_colors()
        dialog.configure(bg=colors["bg"])
        
        # Center on main window
        dialog_width = 500
        dialog_height = 400
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (dialog_width // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (dialog_height // 2)
        dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")
        dialog.minsize(400, 300)
        
        # Main container
        main_frame = tk.Frame(dialog, bg=colors["bg"], padx=15, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Version info
        version = self.update_info.get("tag_name", "Unknown")
        current_version = f"v{APP_VERSION}"
        
        title_label = tk.Label(
            main_frame,
            text=f"New Version Available: {version}",
            font=("Arial", 12, "bold"),
            bg=colors["bg"],
            fg=colors["fg"]
        )
        title_label.pack(anchor=tk.W, pady=(0, 5))
        
        current_label = tk.Label(
            main_frame,
            text=f"Current version: {current_version}",
            font=("Arial", 9),
            bg=colors["bg"],
            fg="gray"
        )
        current_label.pack(anchor=tk.W, pady=(0, 10))
        
        # Release notes frame with scrollbar
        notes_label = tk.Label(
            main_frame,
            text="What's New:",
            font=("Arial", 10, "bold"),
            bg=colors["bg"],
            fg=colors["fg"]
        )
        notes_label.pack(anchor=tk.W, pady=(0, 5))
        
        # Scrollable text area for release notes
        text_frame = tk.Frame(main_frame, bg=colors["bg"])
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 15))
        
        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        release_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg=colors["bg"] if colors["dark_mode"] else "white",
            fg=colors["fg"],
            relief=tk.SUNKEN,
            borderwidth=1,
            yscrollcommand=scrollbar.set,
            padx=10,
            pady=10
        )
        release_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=release_text.yview)
        
        # Get release notes
        body = self.update_info.get("body", "No release notes available.")
        release_text.insert("1.0", body)
        release_text.config(state=tk.DISABLED)  # Make read-only
        
        # Buttons frame
        button_frame = tk.Frame(main_frame, bg=colors["bg"])
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        def on_update():
            dialog.destroy()
            self._start_update()
        
        def on_skip():
            # Save skipped version
            self.data_manager.data["settings"]["skipped_update_version"] = version.lstrip('v')
            self.data_manager.save()
            self.update_btn.pack_forget()  # Hide update button
            logger.info(f"User skipped update {version}")
            dialog.destroy()
        
        def on_later():
            # Just close dialog, will show again next time
            logger.info("User chose to be reminded later")
            dialog.destroy()
        
        # Update Now button (prominent)
        update_btn = tk.Button(
            button_frame,
            text="Update Now",
            command=on_update,
            font=("Arial", 9, "bold"),
            bg="#ff6b00",
            fg="white",
            padx=15,
            pady=5,
            cursor="hand2",
            relief=tk.RAISED
        )
        update_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Skip This Version button
        skip_btn = tk.Button(
            button_frame,
            text="Skip This Version",
            command=on_skip,
            font=("Arial", 9),
            bg=colors["button_bg"],
            fg=colors["button_fg"],
            padx=10,
            pady=5,
            cursor="hand2"
        )
        skip_btn.pack(side=tk.LEFT, padx=(0, 10))
        
        # Remind Me Later button
        later_btn = tk.Button(
            button_frame,
            text="Remind Me Later",
            command=on_later,
            font=("Arial", 9),
            bg=colors["button_bg"],
            fg=colors["button_fg"],
            padx=10,
            pady=5,
            cursor="hand2"
        )
        later_btn.pack(side=tk.LEFT)

    def _start_update(self):
        """Start the update download and application."""
        # Create progress window with dark mode
        progress_win = tk.Toplevel(self.root)
        progress_win.title("Updating...")
        progress_win.geometry("350x120")
        progress_win.transient(self.root)
        progress_win.grab_set()
        progress_win.configure(bg='#2b2b2b')  # Dark background
        
        # Center on parent
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 175
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 60
        progress_win.geometry(f"350x120+{x}+{y}")
        
        status_label = ttk.Label(progress_win, text="Downloading update...", padding=10,
                                 background='#2b2b2b', foreground='white')
        status_label.pack()
        
        # Progress bar
        progress = ttk.Progressbar(progress_win, mode='determinate', maximum=100)
        progress.pack(fill=tk.X, padx=20, pady=5)
        
        # Percentage label
        percent_label = ttk.Label(progress_win, text="0%", padding=5,
                                  background='#2b2b2b', foreground='white')
        percent_label.pack()
        
        def update_progress(current, total):
            """Update progress bar from background thread."""
            if total > 0:
                percentage = int((current / total) * 100)
                self.root.after(0, lambda: progress.configure(value=percentage))
                self.root.after(0, lambda: percent_label.config(text=f"{percentage}%"))
        
        def do_download():
            try:
                new_exe = self.update_manager.download_update(self.update_info, progress_callback=update_progress)
                if new_exe:
                    # Success - launch updater
                    self.root.after(0, lambda: status_label.config(text="Installing update..."))
                    self.root.after(0, lambda: self.update_manager.start_update_process(new_exe))
                else:
                    self.root.after(0, lambda: messagebox.showerror("Update Error", "Failed to download update."))
                    self.root.after(0, progress_win.destroy)
            except Exception as e:
                logger.error(f"Error downloading update: {e}")
                self.root.after(0, lambda: messagebox.showerror("Update Error", f"An error occurred: {e}"))
                self.root.after(0, progress_win.destroy)
        
        threading.Thread(target=do_download, daemon=True).start()

    def _check_version_and_show_whats_new(self):
        """Check if this is first run after update and show What's New."""
        current_version = APP_VERSION.split(' ')[0]  # e.g., "1.7"
        last_version = self.data_manager.data["settings"].get("last_seen_version", "")
        
        if last_version != current_version:
            # Version changed - show What's New
            logger.info(f"Version changed from {last_version} to {current_version}, showing What's New")
            self.data_manager.data["settings"]["last_seen_version"] = current_version
            self.data_manager.save_data(save_records=False)
            
            # Show What's New after a short delay (let UI settle)
            self.root.after(1000, self.open_whats_new)

    def setup_refresh(self):
        """Setup periodic refresh."""
        # Always refresh to update debug display, but only track if running
        self.refresh_data()
        self.root.after(self.refresh_interval, self.setup_refresh)
    
    def setup_time_sensitive_update(self):
        """Setup periodic update for time-sensitive counters (runs every 5 seconds)."""
        self.update_time_sensitive_stats()
        self.root.after(5000, self.setup_time_sensitive_update)  # 5 seconds
    
    def update_time_sensitive_stats(self):
        """Lightweight update for time-based metrics only (avg/hour, projections).
        
        This runs on a slower timer (5s) and only recalculates values that change
        with time, avoiding expensive full recalculation of all stats.
        """
        if not self.shift_start:
            return
        
        try:
            records = self.data_manager.data["current_shift"]["records"]
            current_time = datetime.now()
            settings = self.data_manager.data["settings"]
            
            # Calculate values that change with time
            total_rvu = sum(r["rvu"] for r in records)
            total_comp = sum(self._calculate_study_compensation(r) for r in records)
            
            # Average per hour (changes as time passes even with no new studies)
            hours_elapsed = (current_time - self.shift_start).total_seconds() / 3600
            avg_per_hour = total_rvu / hours_elapsed if hours_elapsed > 0 else 0.0
            avg_comp_per_hour = total_comp / hours_elapsed if hours_elapsed > 0 else 0.0
            
            # Update avg labels if visible
            if settings.get("show_avg", True):
                self.avg_label.config(text=f"{avg_per_hour:.1f}")
                if settings.get("show_comp_avg", False):
                    self.avg_comp_label.config(text=f"(${avg_comp_per_hour:,.0f})")
            
            # Projected for current hour (changes as time passes)
            current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
            current_hour_records = [r for r in records if datetime.fromisoformat(r["time_finished"]) >= current_hour_start]
            current_hour_rvu = sum(r["rvu"] for r in current_hour_records)
            current_hour_comp = sum(self._calculate_study_compensation(r) for r in current_hour_records)
            
            minutes_into_hour = (current_time - current_hour_start).total_seconds() / 60
            if minutes_into_hour > 0:
                projected = (current_hour_rvu / minutes_into_hour) * 60
                projected_comp = (current_hour_comp / minutes_into_hour) * 60
            else:
                projected = 0.0
                projected_comp = 0.0
            
            # Update projected labels if visible
            if settings.get("show_projected", True):
                self.projected_label.config(text=f"{projected:.1f}")
                if settings.get("show_comp_projected", False):
                    self.projected_comp_label.config(text=f"(${projected_comp:,.0f})")
            
            # Projected shift total (changes as time passes)
            projected_shift_rvu = total_rvu
            projected_shift_comp = total_comp
            
            if self.effective_shift_start and self.projected_shift_end:
                time_remaining = (self.projected_shift_end - current_time).total_seconds()
                
                if time_remaining > 0 and hours_elapsed > 0:
                    rvu_rate_per_hour = avg_per_hour
                    hours_remaining = time_remaining / 3600
                    
                    projected_additional_rvu = rvu_rate_per_hour * hours_remaining
                    projected_shift_rvu = total_rvu + projected_additional_rvu
                    
                    projected_additional_comp = self._calculate_projected_compensation(
                        current_time, 
                        self.projected_shift_end, 
                        rvu_rate_per_hour
                    )
                    projected_shift_comp = total_comp + projected_additional_comp
            
            # Update projected shift labels if visible
            if settings.get("show_projected_shift", True):
                self.projected_shift_label.config(text=f"{projected_shift_rvu:.1f}")
                if settings.get("show_comp_projected_shift", False):
                    self.projected_shift_comp_label.config(text=f"(${projected_shift_comp:,.0f})")
            
            # Update pace car if visible
            if settings.get("show_pace_car", False):
                self.update_pace_car(total_rvu)
        
        except Exception as e:
            logger.debug(f"Error updating time-sensitive stats: {e}")
    
    def update_pace_car(self, current_rvu: float):
        """Update the pace car comparison bar.
        
        Compares current shift RVU vs prior shift RVU at the same elapsed time
        since typical shift start (dynamically calculated from historical data).
        
        Design: Two stacked bars
        - Top bar (current): fills proportionally based on current RVU vs prior total
        - Bottom bar (prior): full width = prior total, with marker at "prior at this time"
        """
        try:
            if not hasattr(self, 'pace_bars_container'):
                return
            
            current_time = datetime.now()
            
            # Get actual shift start
            shift_start_str = self.data_manager.data["current_shift"].get("shift_start")
            if not shift_start_str:
                logger.warning("[PACE] No shift_start found")
                return
            shift_start = datetime.fromisoformat(shift_start_str)
            
            # Determine comparison method based on mode and shift start time
            use_elapsed_time = False
            
            if self.pace_comparison_mode == 'goal':
                # Goal mode: always use elapsed time
                use_elapsed_time = True
            else:
                # For other modes: check if shift started near typical start time
                # If shift start is more than 30 minutes from typical start, use elapsed time
                typical_start_hour = self.typical_shift_start_hour
                
                # Calculate minute difference from typical start time
                typical_start = shift_start.replace(hour=typical_start_hour, minute=0, second=0, microsecond=0)
                # Handle case where typical start is previous day (e.g., shift at 1am, typical is 11pm yesterday)
                if shift_start.hour < typical_start_hour:
                    typical_start = typical_start - timedelta(days=1)
                
                minutes_diff = abs((shift_start - typical_start).total_seconds() / 60)
                
                # If started >30 minutes from typical, treat as elapsed time comparison
                if minutes_diff > 30:
                    use_elapsed_time = True
                    logger.info(f"[PACE] Shift started {minutes_diff:.0f}min from typical ({typical_start_hour}:00), using elapsed time comparison")
            
            if use_elapsed_time:
                # Elapsed time mode: calculate from actual shift start
                elapsed_minutes = (current_time - shift_start).total_seconds() / 60
                if elapsed_minutes < 0:
                    elapsed_minutes = 0
                logger.info(f"[PACE] Elapsed mode - Current: {current_time.strftime('%H:%M:%S')}, "
                           f"Shift start: {shift_start.strftime('%H:%M:%S')}, Elapsed: {elapsed_minutes:.1f}min")
            else:
                # Reference time mode: use 11pm reference for time-of-day comparison
                reference_start = self._get_reference_shift_start(current_time)
                elapsed_minutes = (current_time - reference_start).total_seconds() / 60
                if elapsed_minutes < 0:
                    elapsed_minutes = 0
                logger.info(f"[PACE] Reference mode - Current: {current_time.strftime('%H:%M:%S')}, "
                           f"Reference (11pm): {reference_start.strftime('%H:%M:%S')}, Elapsed: {elapsed_minutes:.1f}min")
            
            # Get prior shift data (returns tuple: rvu_at_elapsed, total_rvu)
            prior_data = self._get_prior_shift_rvu_at_elapsed_time(elapsed_minutes, use_elapsed_time)
            
            if prior_data is None:
                # No prior shift data available
                logger.warning(f"[PACE] No pace data available for mode: {self.pace_comparison_mode}")
                self.pace_label_now_text.config(text=f"No data ({self.pace_comparison_mode})")
                self.pace_label_now_value.config(text="")
                self.pace_label_separator.config(text="")
                self.pace_label_prior_text.config(text="")
                self.pace_label_prior_value.config(text="")
                self.pace_label_time.config(text="")
                self.pace_label_right.config(text="")
                self.pace_bar_current.place_forget()
                self.pace_bar_prior_marker.place_forget()
                return
            
            prior_rvu_at_elapsed, prior_total_rvu = prior_data
            
            # Calculate the difference
            diff = current_rvu - prior_rvu_at_elapsed
            
            # Get container width
            self.pace_bars_container.update_idletasks()
            container_width = self.pace_bars_container.winfo_width()
            if container_width < 10:
                container_width = 200  # Default fallback
            
            # Dynamic scale: use whichever is larger (current or prior total) as 100%
            # This way if you're exceeding prior total, both bars scale down appropriately
            max_scale = max(current_rvu, prior_total_rvu, 1)  # minimum 1 to avoid division by zero
            
            # Calculate widths relative to max_scale
            current_width = int((current_rvu / max_scale) * container_width)
            prior_total_width = int((prior_total_rvu / max_scale) * container_width)
            prior_marker_pos = int((prior_rvu_at_elapsed / max_scale) * container_width)
            
            # Update bar colors based on ahead/behind
            dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
            
            if diff >= 0:
                current_bar_color = "#5DADE2"  # Slightly darker light blue for bar (ahead)
                if dark_mode:
                    current_text_color = "#87CEEB"  # Bright sky blue for dark mode (matches pace bar)
                    status_color = "#87CEEB"  # Bright sky blue for status text
                else:
                    current_text_color = "#2874A6"  # Darker blue for light mode
                    status_color = "#2874A6"  # Darker blue for status text
                status_text = f"▲ +{diff:.1f} ahead"
            else:
                current_bar_color = "#c62828"  # Red for bar (behind)
                if dark_mode:
                    current_text_color = "#ef5350"  # Brighter red for dark mode
                    status_color = "#ef5350"  # Brighter red for status text
                else:
                    current_text_color = "#B71C1C"  # Darker red for light mode
                    status_color = "#B71C1C"  # Darker red for status text
                status_text = f"▼ {diff:.1f} behind"
            
            # Update current bar (top) - fills from left
            self.pace_bar_current.config(bg=current_bar_color)
            self.pace_bar_current.place(x=0, y=0, width=current_width, height=9)
            
            # Update prior bar (bottom) - width scales with prior total relative to max
            # Must set relwidth=0 to clear the initial relwidth=1.0 setting
            self.pace_bar_prior_track.place(x=0, y=11, width=prior_total_width, height=9, relwidth=0)
            
            # Update prior marker (black line on lavender bar showing "prior at this time")
            self.pace_bar_prior_marker.place(x=prior_marker_pos, y=11, width=2, height=9)
            
            # Format time display - show elapsed time for goal mode, otherwise current time
            if self.pace_comparison_mode == 'goal':
                # For goal mode, show elapsed time (e.g., "at 2h 15m")
                elapsed_hours = int(elapsed_minutes // 60)
                elapsed_mins = int(elapsed_minutes % 60)
                if elapsed_hours > 0:
                    time_str = f"{elapsed_hours}h {elapsed_mins}m"
                else:
                    time_str = f"{elapsed_mins}m"
            else:
                # For actual shifts, show current time
                time_str = current_time.strftime("%I:%M %p").lstrip("0").lower()
            
            # Update labels with color-coded RVU values and position precisely
            # Position labels tightly side-by-side by calculating cumulative x positions
            x_pos = 0
            
            # "Now:" - gray
            self.pace_label_now_text.place(x=x_pos, y=0)
            x_pos += self.pace_label_now_text.winfo_reqwidth()
            
            # "XX.X" - colored based on ahead/behind
            self.pace_label_now_value.config(text=f" {current_rvu:.1f}", fg=current_text_color)
            self.pace_label_now_value.place(x=x_pos, y=0)
            x_pos += self.pace_label_now_value.winfo_reqwidth()
            
            # " | " - gray
            self.pace_label_separator.place(x=x_pos, y=0)
            x_pos += self.pace_label_separator.winfo_reqwidth()
            
            # Comparison label - shows what we're comparing to
            compare_label = "Prior:"
            if self.pace_comparison_mode == 'goal':
                compare_label = "Goal:"  # Theoretical pace
            elif self.pace_comparison_mode == 'best_week':
                compare_label = "Week:"  # Week's best
            elif self.pace_comparison_mode == 'best_ever':
                compare_label = "Best:"  # All time best
            elif self.pace_comparison_mode == 'custom':
                compare_label = "Custom:"  # User-selected shift
            elif self.pace_comparison_mode and self.pace_comparison_mode.startswith('week_'):
                # Show 3-letter day abbreviation for specific week shift
                if self.pace_comparison_shift:
                    compare_label = self._format_shift_day_abbrev(self.pace_comparison_shift) + ":"
            
            self.pace_label_prior_text.config(text=compare_label)
            self.pace_label_prior_text.place(x=x_pos, y=0)
            x_pos += self.pace_label_prior_text.winfo_reqwidth()
            
            # "XX.X" - darker lavender
            self.pace_label_prior_value.config(text=f" {prior_rvu_at_elapsed:.1f}", fg="#7070A0")
            self.pace_label_prior_value.place(x=x_pos, y=0)
            x_pos += self.pace_label_prior_value.winfo_reqwidth()
            
            # " at time" - gray
            self.pace_label_time.config(text=f" at {time_str}")
            self.pace_label_time.place(x=x_pos, y=0)
            
            # Status on right
            self.pace_label_right.config(text=status_text, fg=status_color)
            
        except Exception as e:
            logger.debug(f"Error updating pace car: {e}")
    
    def _open_pace_comparison_selector(self, event=None):
        """Open a popup to select which shift to compare against."""
        logger.info("Pace comparison selector clicked!")
        try:
            # Prevent opening multiple popups
            if hasattr(self, '_pace_popup') and self._pace_popup:
                try:
                    if self._pace_popup.winfo_exists():
                        self._pace_popup.destroy()
                except:
                    pass
                self._pace_popup = None
            
            # Create popup window
            popup = tk.Toplevel(self.root)
            self._pace_popup = popup  # Store reference
            popup.title("Compare To...")
            popup.transient(self.root)
            
            # Position near the pace bar
            x = self.pace_bars_container.winfo_rootx()
            y = self.pace_bars_container.winfo_rooty() + self.pace_bars_container.winfo_height() + 5
            popup.geometry(f"220x350+{x}+{y}")
            
            # Apply theme
            dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
            bg_color = "#2d2d2d" if dark_mode else "white"
            fg_color = "#ffffff" if dark_mode else "black"
            border_color = "#555555" if dark_mode else "#cccccc"
            popup.configure(bg=border_color)  # Border effect
            
            frame = tk.Frame(popup, bg=bg_color, padx=8, pady=5)
            frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)  # 1px border
            
            # Get shifts for this week and all time
            shifts_this_week, prior_shift, best_week, best_ever = self._get_pace_comparison_options()
            
            def make_selection(mode, shift=None):
                self.pace_comparison_mode = mode
                self.pace_comparison_shift = shift
                # Save mode to settings (persists between sessions)
                self.data_manager.data["settings"]["pace_comparison_mode"] = mode
                self.data_manager.save()
                popup.destroy()
                self._pace_popup = None
            
            def close_popup(e=None):
                popup.destroy()
                self._pace_popup = None
            
            # Helper to create hover effect
            def add_hover(widget, bg_color, dark_mode):
                widget.bind("<Enter>", lambda e: e.widget.config(bg="#e0e0e0" if not dark_mode else "#404040"))
                widget.bind("<Leave>", lambda e: e.widget.config(bg=bg_color))
            
            # --- TOP SECTION: Prior, Week Best, All Time Best ---
            
            # Prior Shift (most recent valid shift)
            if prior_shift:
                prior_rvu = sum(r.get('rvu', 0) for r in prior_shift.get('records', []))
                prior_date = self._format_shift_label(prior_shift)
                btn = tk.Label(frame, text=f"  Prior: {prior_date} ({prior_rvu:.1f} RVU)", 
                              font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
                btn.pack(fill=tk.X, pady=1)
                btn.bind("<Button-1>", lambda e: make_selection('prior', prior_shift))
                add_hover(btn, bg_color, dark_mode)
            
            # Week Best (best this week)
            if best_week:
                best_week_rvu = sum(r.get('rvu', 0) for r in best_week.get('records', []))
                best_week_date = self._format_shift_label(best_week)
                btn = tk.Label(frame, text=f"  Week: {best_week_date} ({best_week_rvu:.1f} RVU)", 
                              font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
                btn.pack(fill=tk.X, pady=1)
                btn.bind("<Button-1>", lambda e: make_selection('best_week', best_week))
                add_hover(btn, bg_color, dark_mode)
            
            # All Time Best
            if best_ever:
                best_ever_rvu = sum(r.get('rvu', 0) for r in best_ever.get('records', []))
                best_ever_date = self._format_shift_label(best_ever)
                btn = tk.Label(frame, text=f"  Best: {best_ever_date} ({best_ever_rvu:.1f} RVU)", 
                              font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
                btn.pack(fill=tk.X, pady=1)
                btn.bind("<Button-1>", lambda e: make_selection('best_ever', best_ever))
                add_hover(btn, bg_color, dark_mode)
            
            # Custom shift selector
            custom_btn = tk.Label(frame, text=f"  Custom: Select any shift...", 
                                 font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
            custom_btn.pack(fill=tk.X, pady=1)
            custom_btn.bind("<Button-1>", lambda e: self._open_custom_shift_selector(popup, make_selection, close_popup))
            add_hover(custom_btn, bg_color, dark_mode)
            
            # --- THIS WEEK SECTION ---
            if shifts_this_week:
                tk.Label(frame, text="This Week:", font=("Arial", 8, "bold"),
                        bg=bg_color, fg=fg_color, anchor=tk.W).pack(fill=tk.X, pady=(8, 2))
                
                for i, shift in enumerate(shifts_this_week):
                    shift_date = self._format_shift_label(shift)  # Already has 3-letter day abbrev
                    total_rvu = sum(r.get('rvu', 0) for r in shift.get('records', []))
                    btn = tk.Label(frame, text=f"  {shift_date} ({total_rvu:.1f} RVU)", 
                                  font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
                    btn.pack(fill=tk.X, pady=1)
                    btn.bind("<Button-1>", lambda e, s=shift, idx=i: make_selection(f'week_{idx}', s))
                    add_hover(btn, bg_color, dark_mode)
            
            # If no shifts found at all, show a message
            if not prior_shift and not shifts_this_week and not best_week and not best_ever:
                tk.Label(frame, text="No historical shifts found", 
                        font=("Arial", 8), bg=bg_color, fg="gray", anchor=tk.W).pack(fill=tk.X, pady=5)
            
            # Separator before Goal
            tk.Frame(frame, bg=border_color, height=1).pack(fill=tk.X, pady=5)
            
            # --- GOAL SECTION: Theoretical pace with editable parameters ---
            goal_frame = tk.Frame(frame, bg=bg_color)
            goal_frame.pack(fill=tk.X, pady=(0, 5))
            
            # Get current goal settings
            goal_rvu_h = self.data_manager.data["settings"].get("pace_goal_rvu_per_hour", 15.0)
            goal_hours = self.data_manager.data["settings"].get("pace_goal_shift_hours", 9.0)
            goal_total = self.data_manager.data["settings"].get("pace_goal_total_rvu", 135.0)
            
            # Goal label (clickable)
            goal_btn = tk.Label(goal_frame, text=f"  Goal: {goal_rvu_h:.1f}/h × {goal_hours:.0f}h = {goal_total:.0f} RVU", 
                              font=("Arial", 8), bg=bg_color, fg=fg_color, anchor=tk.W)
            goal_btn.pack(fill=tk.X, pady=1)
            goal_btn.bind("<Button-1>", lambda e: make_selection('goal', None))
            add_hover(goal_btn, bg_color, dark_mode)
            
            # Mini editor frame (expandable)
            goal_editor = tk.Frame(frame, bg=bg_color)
            goal_editor.pack(fill=tk.X, padx=(10, 0))
            
            # Variables for goal settings
            rvu_h_var = tk.StringVar(value=f"{goal_rvu_h:.1f}")
            hours_var = tk.StringVar(value=f"{goal_hours:.1f}")
            total_var = tk.StringVar(value=f"{goal_total:.1f}")
            
            def update_total(*args):
                """Recalculate total when RVU/h or hours changes."""
                try:
                    rvu_h = float(rvu_h_var.get())
                    hours = float(hours_var.get())
                    new_total = rvu_h * hours
                    total_var.set(f"{new_total:.1f}")
                    # Save settings
                    self.data_manager.data["settings"]["pace_goal_rvu_per_hour"] = rvu_h
                    self.data_manager.data["settings"]["pace_goal_shift_hours"] = hours
                    self.data_manager.data["settings"]["pace_goal_total_rvu"] = new_total
                    self.data_manager.save()
                    # Update goal label
                    goal_btn.config(text=f"  Goal: {rvu_h:.1f}/h × {hours:.0f}h = {new_total:.0f} RVU")
                except ValueError:
                    pass
            
            def update_rvu_h_from_total(*args):
                """Recalculate RVU/h when total changes directly."""
                try:
                    total = float(total_var.get())
                    hours = float(hours_var.get())
                    if hours > 0:
                        new_rvu_h = total / hours
                        rvu_h_var.set(f"{new_rvu_h:.1f}")
                        # Save settings
                        self.data_manager.data["settings"]["pace_goal_rvu_per_hour"] = new_rvu_h
                        self.data_manager.data["settings"]["pace_goal_total_rvu"] = total
                        self.data_manager.save()
                        # Update goal label
                        goal_btn.config(text=f"  Goal: {new_rvu_h:.1f}/h × {hours:.0f}h = {total:.0f} RVU")
                except ValueError:
                    pass
            
            # RVU/h row
            rvu_h_frame = tk.Frame(goal_editor, bg=bg_color)
            rvu_h_frame.pack(fill=tk.X, pady=1)
            tk.Label(rvu_h_frame, text="RVU/h:", font=("Arial", 7), bg=bg_color, fg="gray", width=6, anchor=tk.E).pack(side=tk.LEFT)
            rvu_h_entry = tk.Entry(rvu_h_frame, textvariable=rvu_h_var, font=("Arial", 7), width=6)
            rvu_h_entry.pack(side=tk.LEFT, padx=2)
            rvu_h_entry.bind("<FocusOut>", update_total)
            rvu_h_entry.bind("<Return>", update_total)
            
            # Hours row
            hours_frame = tk.Frame(goal_editor, bg=bg_color)
            hours_frame.pack(fill=tk.X, pady=1)
            tk.Label(hours_frame, text="Hours:", font=("Arial", 7), bg=bg_color, fg="gray", width=6, anchor=tk.E).pack(side=tk.LEFT)
            hours_entry = tk.Entry(hours_frame, textvariable=hours_var, font=("Arial", 7), width=6)
            hours_entry.pack(side=tk.LEFT, padx=2)
            hours_entry.bind("<FocusOut>", update_total)
            hours_entry.bind("<Return>", update_total)
            
            # Total row
            total_frame = tk.Frame(goal_editor, bg=bg_color)
            total_frame.pack(fill=tk.X, pady=1)
            tk.Label(total_frame, text="Total:", font=("Arial", 7), bg=bg_color, fg="gray", width=6, anchor=tk.E).pack(side=tk.LEFT)
            total_entry = tk.Entry(total_frame, textvariable=total_var, font=("Arial", 7), width=6)
            total_entry.pack(side=tk.LEFT, padx=2)
            total_entry.bind("<FocusOut>", update_rvu_h_from_total)
            total_entry.bind("<Return>", update_rvu_h_from_total)
            
            # Cancel button
            cancel_btn = tk.Label(frame, text="Cancel", font=("Arial", 8), 
                                 bg=bg_color, fg="gray", anchor=tk.CENTER)
            cancel_btn.pack(fill=tk.X, pady=(8, 2))
            cancel_btn.bind("<Button-1>", close_popup)
            cancel_btn.bind("<Enter>", lambda e: e.widget.config(fg=fg_color))
            cancel_btn.bind("<Leave>", lambda e: e.widget.config(fg="gray"))
            
            # Close on Escape key
            popup.bind("<Escape>", close_popup)
            
            # Make sure popup is visible
            popup.lift()
            popup.focus_force()
            
            logger.info(f"Pace popup opened with {len(shifts_this_week)} week shifts, prior={prior_shift is not None}")
            
        except Exception as e:
            logger.error(f"Error opening pace comparison selector: {e}", exc_info=True)
    
    def _open_custom_shift_selector(self, parent_popup, make_selection_callback, close_parent_callback):
        """Open a modal to browse and select any historical shift."""
        try:
            # Create second modal
            custom_popup = tk.Toplevel(self.root)
            custom_popup.title("Select Custom Shift")
            custom_popup.transient(parent_popup)
            
            # Calculate position
            modal_width = 280
            modal_height = 400
            
            # Try to load saved position first
            saved_pos = self.data_manager.data.get("window_positions", {}).get("custom_shift_selector", None)
            
            if saved_pos:
                x = saved_pos.get('x', 0)
                y = saved_pos.get('y', 0)
                logger.debug(f"Loading saved custom shift selector position: ({x}, {y})")
            else:
                # Default: position next to parent popup
                x = parent_popup.winfo_rootx() + parent_popup.winfo_width() + 10
                y = parent_popup.winfo_rooty()
            
            # Get screen dimensions
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            
            # Ensure modal stays on screen
            # If it would go off the right edge, position it to the left of parent instead
            if x + modal_width > screen_width:
                x = parent_popup.winfo_rootx() - modal_width - 10
                # If that's also off-screen (left edge), center it
                if x < 0:
                    x = (screen_width - modal_width) // 2
            
            # Ensure it doesn't go off the bottom
            if y + modal_height > screen_height:
                y = screen_height - modal_height - 40  # Leave room for taskbar
                if y < 0:
                    y = 20  # Minimum top padding
            
            # Ensure it doesn't go off the top
            if y < 0:
                y = 20
            
            custom_popup.geometry(f"{modal_width}x{modal_height}+{x}+{y}")
            
            # Track position for saving
            custom_popup._last_saved_x = x
            custom_popup._last_saved_y = y
            
            # Position saving functions
            def on_configure(event):
                """Track window movement and save position with debouncing."""
                if event.widget == custom_popup:
                    try:
                        current_x = custom_popup.winfo_x()
                        current_y = custom_popup.winfo_y()
                        # Debounce: save after 200ms of no movement
                        if hasattr(custom_popup, '_save_timer'):
                            custom_popup.after_cancel(custom_popup._save_timer)
                        custom_popup._save_timer = custom_popup.after(200, 
                            lambda: save_custom_selector_position(current_x, current_y))
                    except Exception as e:
                        logger.debug(f"Error tracking custom selector position: {e}")
            
            def save_custom_selector_position(x=None, y=None):
                """Save custom shift selector position."""
                try:
                    if x is None:
                        x = custom_popup.winfo_x()
                    if y is None:
                        y = custom_popup.winfo_y()
                    
                    # Only save if position actually changed
                    if hasattr(custom_popup, '_last_saved_x') and hasattr(custom_popup, '_last_saved_y'):
                        if x == custom_popup._last_saved_x and y == custom_popup._last_saved_y:
                            return
                    
                    if "window_positions" not in self.data_manager.data:
                        self.data_manager.data["window_positions"] = {}
                    self.data_manager.data["window_positions"]["custom_shift_selector"] = {
                        "x": x,
                        "y": y
                    }
                    custom_popup._last_saved_x = x
                    custom_popup._last_saved_y = y
                    self.data_manager.save(save_records=False)
                    logger.debug(f"Saved custom shift selector position: ({x}, {y})")
                except Exception as e:
                    logger.error(f"Error saving custom selector position: {e}")
            
            # Bind position tracking
            custom_popup.bind("<Configure>", on_configure)
            
            # Apply theme
            dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
            bg_color = "#2d2d2d" if dark_mode else "white"
            fg_color = "#ffffff" if dark_mode else "black"
            border_color = "#555555" if dark_mode else "#cccccc"
            custom_popup.configure(bg=border_color)
            
            frame = tk.Frame(custom_popup, bg=bg_color, padx=8, pady=5)
            frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
            
            # Title
            tk.Label(frame, text="Select any prior shift:", font=("Arial", 9, "bold"),
                    bg=bg_color, fg=fg_color, anchor=tk.W).pack(fill=tk.X, pady=(0, 5))
            
            # Scrollable list frame
            list_frame = tk.Frame(frame, bg=bg_color)
            list_frame.pack(fill=tk.BOTH, expand=True)
            
            # Canvas for scrolling
            canvas = tk.Canvas(list_frame, bg=bg_color, highlightthickness=0)
            scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
            scrollable_frame = tk.Frame(canvas, bg=bg_color)
            
            scrollable_frame.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
            )
            
            canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
            canvas.configure(yscrollcommand=scrollbar.set)
            
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            
            # Get all historical shifts
            all_shifts = self.data_manager.data.get("shifts", [])
            
            if not all_shifts:
                tk.Label(scrollable_frame, text="No historical shifts found", 
                        font=("Arial", 8), bg=bg_color, fg="gray", anchor=tk.W).pack(fill=tk.X, pady=10)
            else:
                # Sort by date (newest first)
                sorted_shifts = sorted(all_shifts, 
                                      key=lambda s: s.get('shift_start', ''), 
                                      reverse=True)
                
                # Helper for hover effect
                def add_hover(widget):
                    widget.bind("<Enter>", lambda e: e.widget.config(bg="#e0e0e0" if not dark_mode else "#404040"))
                    widget.bind("<Leave>", lambda e: e.widget.config(bg=bg_color))
                
                # Add each shift as a clickable entry
                for shift in sorted_shifts:
                    if not shift.get("records"):
                        continue  # Skip shifts with no records
                    
                    # Calculate shift info
                    try:
                        shift_start = datetime.fromisoformat(shift.get('shift_start', ''))
                        date_str = shift_start.strftime('%a %b %d, %Y')  # "Mon Dec 07, 2025"
                        time_str = shift_start.strftime('%I:%M %p').lstrip('0')  # "11:01 PM"
                    except:
                        date_str = "Unknown date"
                        time_str = ""
                    
                    total_rvu = sum(r.get('rvu', 0) for r in shift.get('records', []))
                    record_count = len(shift.get('records', []))
                    
                    # Create shift button
                    shift_text = f"{date_str} {time_str}\n  ({record_count}, {total_rvu:.1f} RVU)"
                    
                    def make_custom_selection(s=shift):
                        """Close both modals and set custom comparison."""
                        custom_popup.destroy()
                        make_selection_callback('custom', s)
                    
                    btn = tk.Label(scrollable_frame, text=shift_text,
                                  font=("Arial", 8), bg=bg_color, fg=fg_color, 
                                  anchor=tk.W, justify=tk.LEFT, padx=5, pady=3,
                                  relief=tk.FLAT, borderwidth=1)
                    btn.pack(fill=tk.X, pady=1)
                    btn.bind("<Button-1>", lambda e, s=shift: make_custom_selection(s))
                    add_hover(btn)
            
            # Close button
            close_btn = tk.Label(frame, text="Cancel", font=("Arial", 8),
                                bg=bg_color, fg="gray", anchor=tk.CENTER)
            close_btn.pack(fill=tk.X, pady=(8, 0))
            close_btn.bind("<Button-1>", lambda e: custom_popup.destroy())
            
            # Enable mouse wheel scrolling
            def _on_mousewheel(event):
                canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            
            # Cleanup on close
            def on_closing():
                # Cancel any pending save timer
                if hasattr(custom_popup, '_save_timer'):
                    try:
                        custom_popup.after_cancel(custom_popup._save_timer)
                    except:
                        pass
                # Save final position
                save_custom_selector_position()
                # Cleanup bindings
                canvas.unbind_all("<MouseWheel>")
                custom_popup.destroy()
            
            custom_popup.protocol("WM_DELETE_WINDOW", on_closing)
            
        except Exception as e:
            logger.error(f"Error opening custom shift selector: {e}", exc_info=True)
    
    def _get_pace_comparison_options(self):
        """Get shifts available for pace comparison.
        
        For prior/week shifts: Only includes shifts within typical shift window.
        For best ever: Includes any ~9 hour shift regardless of start time.
        Returns: (shifts_this_week, prior_shift, best_week_shift, best_ever_shift)
        """
        historical_shifts = self.data_manager.data.get("shifts", [])
        if not historical_shifts:
            return [], None, None, None
        
        now = datetime.now()
        
        # Find start of current week (Monday at typical shift start hour)
        days_since_monday = now.weekday()  # Monday = 0
        week_start = now - timedelta(days=days_since_monday)
        week_start = week_start.replace(hour=self.typical_shift_start_hour, minute=0, second=0, microsecond=0)
        # If we haven't reached Monday shift start yet, use last week's Monday
        if now < week_start:
            week_start -= timedelta(days=7)
        
        shifts_this_week = []
        prior_shift = None
        best_week = None
        best_ever = None
        best_week_rvu = 0
        best_ever_rvu = 0
        
        for shift in historical_shifts:
            if not shift.get("shift_start") or not shift.get("records"):
                continue
            
            try:
                shift_start = datetime.fromisoformat(shift["shift_start"])
                total_rvu = sum(r.get('rvu', 0) for r in shift.get('records', []))
                
                # Calculate shift duration for best_ever eligibility
                shift_end_str = shift.get("shift_end")
                shift_hours = None
                if shift_end_str:
                    shift_end = datetime.fromisoformat(shift_end_str)
                    shift_hours = (shift_end - shift_start).total_seconds() / 3600
                
                # Best ever: any shift that's approximately 9 hours (7-11 hours)
                if shift_hours and 7 <= shift_hours <= 11:
                    if total_rvu > best_ever_rvu:
                        best_ever_rvu = total_rvu
                        best_ever = shift
                
                # For prior/week: only include shifts within typical shift window
                hour = shift_start.hour
                if not self._is_valid_shift_hour(hour):
                    continue  # Skip shifts outside typical window for prior/week
                
                # Prior shift is the first valid one (most recent night shift)
                if prior_shift is None:
                    prior_shift = shift
                
                # Check if in this week
                if shift_start >= week_start:
                    shifts_this_week.append(shift)
                    if total_rvu > best_week_rvu:
                        best_week_rvu = total_rvu
                        best_week = shift
            except:
                pass
        
        # Sort this week's shifts by date (oldest first for display)
        shifts_this_week.sort(key=lambda s: s.get("shift_start", ""))
        
        return shifts_this_week, prior_shift, best_week, best_ever
    
    def _format_shift_label(self, shift):
        """Format shift as 'Mon 12/2' style."""
        try:
            shift_start = datetime.fromisoformat(shift["shift_start"])
            return shift_start.strftime("%a %m/%d")
        except:
            return "Unknown"
    
    def _format_shift_day_label(self, shift):
        """Format shift as day of week name."""
        try:
            shift_start = datetime.fromisoformat(shift["shift_start"])
            return shift_start.strftime("%A")  # Full day name
        except:
            return "Unknown"
    
    def _format_shift_day_abbrev(self, shift):
        """Format shift as 3-letter day abbreviation (Mon, Tue, Wed, etc.)."""
        try:
            shift_start = datetime.fromisoformat(shift["shift_start"])
            return shift_start.strftime("%a")  # 3-letter day abbreviation
        except:
            return "???"
    
    def _calculate_typical_shift_times(self):
        """Calculate typical shift start and end hours from historical data.
        
        Analyzes completed shifts to find the most common (mode) start and end hours.
        Uses fuzzy matching by rounding to nearest hour.
        Falls back to 11pm-8am if insufficient data.
        """
        historical_shifts = self.data_manager.data.get("shifts", [])
        
        if len(historical_shifts) < 2:
            # Not enough data, keep defaults
            logger.info(f"Using default shift times: {self.typical_shift_start_hour}:00 - {self.typical_shift_end_hour}:00")
            return
        
        start_hours = []
        end_hours = []
        
        for shift in historical_shifts:
            try:
                if not shift.get("shift_start") or not shift.get("shift_end"):
                    continue
                
                start = datetime.fromisoformat(shift["shift_start"])
                end = datetime.fromisoformat(shift["shift_end"])
                
                # Round to nearest hour (fuzzy matching)
                # e.g., 10:45pm -> 11pm, 11:15pm -> 11pm, 8:20am -> 8am
                start_hour = start.hour
                if start.minute >= 30:
                    start_hour = (start_hour + 1) % 24
                
                end_hour = end.hour
                if end.minute >= 30:
                    end_hour = (end_hour + 1) % 24
                
                start_hours.append(start_hour)
                end_hours.append(end_hour)
            except:
                pass
        
        if start_hours and end_hours:
            # Find mode (most common hour) for start and end
            from collections import Counter
            
            start_counter = Counter(start_hours)
            end_counter = Counter(end_hours)
            
            # Get most common
            self.typical_shift_start_hour = start_counter.most_common(1)[0][0]
            self.typical_shift_end_hour = end_counter.most_common(1)[0][0]
            
            logger.info(f"Calculated typical shift times from {len(start_hours)} shifts: "
                       f"{self.typical_shift_start_hour}:00 - {self.typical_shift_end_hour}:00")
        else:
            logger.info(f"Using default shift times: {self.typical_shift_start_hour}:00 - {self.typical_shift_end_hour}:00")
    
    def _is_valid_shift_hour(self, hour: int) -> bool:
        """Check if an hour falls within the typical shift window (with 1-hour fuzzy margin).
        
        Handles overnight shifts where start > end (e.g., 23 to 8).
        """
        start = self.typical_shift_start_hour
        end = self.typical_shift_end_hour
        
        # Add 1-hour margin for fuzzy matching
        # e.g., if typical is 23-8, accept 22-9
        start_fuzzy = (start - 1) % 24
        end_fuzzy = (end + 1) % 24
        
        if start_fuzzy > end_fuzzy:
            # Overnight shift (e.g., 22 to 9)
            return hour >= start_fuzzy or hour <= end_fuzzy
        else:
            # Same-day shift (e.g., 6 to 14)
            return start_fuzzy <= hour <= end_fuzzy
    
    def _get_reference_shift_start(self, current_time: datetime) -> datetime:
        """Get the reference shift start time (typical start hour) for elapsed time calculations.
        
        Returns the most recent occurrence of the typical shift start hour.
        """
        start_hour = self.typical_shift_start_hour
        
        # Create today's reference start time
        reference = current_time.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        
        if current_time.hour < start_hour:
            # We're past midnight, so shift start was yesterday
            reference = reference - timedelta(days=1)
        
        return reference
    
    def _get_prior_shift_rvu_at_elapsed_time(self, elapsed_minutes: float, use_elapsed_time: bool = False):
        """Get RVU from comparison source at the same elapsed time.
        
        Returns tuple (rvu_at_elapsed, total_rvu) or None if no data available.
        Uses self.pace_comparison_mode to determine comparison source:
        - 'goal': Theoretical pace based on settings (RVU/h × hours)
        - 'prior', 'best_week', 'best_ever', 'week_N': Actual historical shifts
        
        Args:
            elapsed_minutes: Time elapsed (from shift start or reference time)
            use_elapsed_time: If True, compare based on elapsed time from shift start.
                            If False, compare based on reference time (11pm).
        """
        try:
            logger.debug(f"[PACE] _get_prior_shift_rvu_at_elapsed_time: mode={self.pace_comparison_mode}, elapsed={elapsed_minutes:.1f}min, cached_shift={self.pace_comparison_shift is not None}")
            # Handle 'goal' mode - theoretical pace
            if self.pace_comparison_mode == 'goal':
                try:
                    goal_rvu_h = float(self.data_manager.data["settings"].get("pace_goal_rvu_per_hour", 15.0))
                    goal_hours = float(self.data_manager.data["settings"].get("pace_goal_shift_hours", 9.0))
                    goal_total = goal_rvu_h * goal_hours
                    
                    # Calculate RVU at current elapsed time
                    elapsed_hours = elapsed_minutes / 60.0
                    rvu_at_elapsed = goal_rvu_h * elapsed_hours
                    
                    # Cap at total (in case elapsed exceeds goal hours)
                    rvu_at_elapsed = min(rvu_at_elapsed, goal_total)
                    
                    logger.debug(f"[PACE] Goal mode: {goal_rvu_h:.1f} RVU/h × {elapsed_hours:.2f}h = {rvu_at_elapsed:.1f} RVU (total: {goal_total:.1f})")
                    return (rvu_at_elapsed, goal_total)
                except Exception as e:
                    logger.error(f"[PACE] Error in goal mode calculation: {e}")
                    return None
            
            # Determine which shift to use for comparison
            comparison_shift = None
            
            if self.pace_comparison_shift and self.pace_comparison_shift.get("records"):
                # Use cached comparison shift (set when user selects from popup) if it has records
                comparison_shift = self.pace_comparison_shift
                logger.debug(f"[PACE] Using cached comparison shift: mode={self.pace_comparison_mode}, records={len(comparison_shift.get('records', []))}")
            
            if not comparison_shift:
                # No cached shift or cached shift has no records - find prior shift (most recent valid one)
                historical_shifts = self.data_manager.data.get("shifts", [])
                if not historical_shifts:
                    logger.warning("No historical shifts available for comparison")
                    return None
                
                for shift in historical_shifts:
                    if shift.get("shift_start"):
                        # Check if it's a valid shift hour
                        try:
                            shift_start = datetime.fromisoformat(shift["shift_start"])
                            if self._is_valid_shift_hour(shift_start.hour):
                                # Verify shift has records
                                records = shift.get("records", [])
                                if records:
                                    comparison_shift = shift
                                    logger.debug(f"Found comparison shift: start={shift_start.isoformat()}, records={len(records)}")
                                    break
                                else:
                                    logger.debug(f"Skipping shift with no records: start={shift_start.isoformat()}")
                        except Exception as e:
                            logger.debug(f"Error processing shift: {e}")
                            pass
            
            if not comparison_shift:
                return None
            
            # Get comparison shift's actual start time
            prior_start = datetime.fromisoformat(comparison_shift["shift_start"])
            
            # Calculate target time based on comparison mode
            if use_elapsed_time:
                # Elapsed time mode: compare X minutes into each shift
                target_time = prior_start + timedelta(minutes=elapsed_minutes)
                logger.debug(f"[PACE] Elapsed comparison - Prior start={prior_start.strftime('%Y-%m-%d %H:%M:%S')}, "
                           f"elapsed={elapsed_minutes:.1f}min, target={target_time.strftime('%Y-%m-%d %H:%M:%S')}")
            else:
                # Reference time mode: normalize to typical shift start hour (e.g., 11pm)
                # This ensures fair comparison for shifts that started at similar times of day
                start_hour = self.typical_shift_start_hour
                
                # Create reference time for prior shift (e.g., 11pm on the day of shift)
                prior_reference = prior_start.replace(hour=start_hour, minute=0, second=0, microsecond=0)
                
                # Handle case where shift started after midnight (e.g., 1am) but reference is 11pm
                if prior_start.hour < start_hour:
                    # Shift started after midnight, so reference 11pm is on previous day
                    prior_reference = prior_reference - timedelta(days=1)
                
                # Calculate target time: reference (11pm) + elapsed minutes
                target_time = prior_reference + timedelta(minutes=elapsed_minutes)
                
                logger.debug(f"[PACE] Reference comparison - Prior start={prior_start.strftime('%Y-%m-%d %H:%M:%S')}, "
                           f"reference_11pm={prior_reference.strftime('%Y-%m-%d %H:%M:%S')}, "
                           f"target={target_time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Sum RVU for all records finished before target_time
            rvu_at_elapsed = 0.0
            total_rvu = 0.0
            records = comparison_shift.get("records", [])
            
            if not records:
                logger.warning(f"Comparison shift has no records. Shift start: {comparison_shift.get('shift_start')}")
                return None
            
            records_before_target = 0
            records_after_target = 0
            
            for record in records:
                rvu = record.get("rvu", 0) or 0
                total_rvu += rvu  # Always add to total
                
                time_finished_str = record.get("time_finished", "")
                if not time_finished_str:
                    # Skip records without time_finished, but still count toward total
                    logger.debug(f"Record missing time_finished: accession={record.get('accession')}, rvu={rvu}")
                    continue
                
                try:
                    time_finished = datetime.fromisoformat(time_finished_str)
                    if time_finished <= target_time:
                        rvu_at_elapsed += rvu
                        records_before_target += 1
                        logger.debug(f"[PACE]   ✓ BEFORE: finished={time_finished.strftime('%H:%M:%S')}, rvu={rvu:.1f}, proc={record.get('procedure', 'N/A')[:40]}")
                    else:
                        records_after_target += 1
                        if records_after_target <= 3:  # Only log first 3 after-target records to avoid spam
                            logger.debug(f"[PACE]   ✗ AFTER: finished={time_finished.strftime('%H:%M:%S')}, rvu={rvu:.1f}, proc={record.get('procedure', 'N/A')[:40]}")
                except (ValueError, TypeError) as e:
                    # Skip records with invalid time_finished format
                    logger.debug(f"Failed to parse time_finished '{time_finished_str}': {e}")
                    continue
            
            logger.debug(f"[PACE] ═══ RESULT ═══ Elapsed: {elapsed_minutes:.1f}min | Target: {target_time.strftime('%H:%M:%S')} | "
                       f"RVU at elapsed: {rvu_at_elapsed:.1f} | Total RVU: {total_rvu:.1f} | "
                       f"Records before/after: {records_before_target}/{records_after_target}")
            
            return (rvu_at_elapsed, total_rvu)
            
        except Exception as e:
            logger.debug(f"Error getting prior shift RVU: {e}")
            return None
    
    def _record_or_update_study(self, study_record: dict):
        """
        Record a study, or update existing record if same accession already exists.
        If updating, keeps the highest duration among all openings.
        """
        accession = study_record.get("accession", "")
        if not accession:
            return
        
        records = self.data_manager.data["current_shift"]["records"]
        
        # Find existing record with same accession
        existing_index = None
        for i, record in enumerate(records):
            if record.get("accession") == accession:
                existing_index = i
                break
        
        new_duration = study_record.get("duration_seconds", 0)
        
        if existing_index is not None:
            # Update existing record if new duration is higher
            existing_duration = records[existing_index].get("duration_seconds", 0)
            # Ensure it's in seen_accessions (in case it wasn't added before)
            self.tracker.seen_accessions.add(accession)
            if new_duration > existing_duration:
                # Update with higher duration, but keep original time_performed
                records[existing_index]["duration_seconds"] = new_duration
                records[existing_index]["time_finished"] = study_record.get("time_finished")
                # Update other fields that might have changed
                if study_record.get("procedure"):
                    records[existing_index]["procedure"] = study_record["procedure"]
                if study_record.get("patient_class"):
                    records[existing_index]["patient_class"] = study_record["patient_class"]
                if study_record.get("study_type"):
                    records[existing_index]["study_type"] = study_record["study_type"]
                if study_record.get("rvu") is not None:
                    records[existing_index]["rvu"] = study_record["rvu"]
                self.data_manager.save()
                logger.info(f"Updated study duration for {accession}: {existing_duration:.1f}s -> {new_duration:.1f}s (kept higher duration)")
            else:
                logger.debug(f"Study {accession} already recorded with higher duration ({existing_duration:.1f}s >= {new_duration:.1f}s), skipping")
        else:
            # New study - record it
            records.append(study_record)
            self.data_manager.save()
            
            # Reset inactivity timer on new study
            self.last_activity_time = datetime.now()
            self._auto_end_prompt_shown = False
            
            # Add to seen_accessions so duplicate checks are faster
            self.tracker.seen_accessions.add(accession)
            logger.info(f"Recorded new study: {accession} - {study_record.get('study_type', 'Unknown')} ({study_record.get('rvu', 0):.1f} RVU) - Duration: {new_duration:.1f}s")
        
        # Reset inactivity timer on ANY update or new study to be safe
        self.last_activity_time = datetime.now()
        self._auto_end_prompt_shown = False
    
    def _record_multi_accession_study(self, current_time):
        """Record a completed multi-accession study as SEPARATE individual studies.
        
        Each accession in the multi-accession group gets its own record with:
        - Its own accession number
        - Its own procedure and study type  
        - Its own RVU value
        - Duration split evenly among studies
        - Reference to the multi-accession group for duplicate detection
        """
        # FIRST: Check for any accessions in current_multiple_accessions that weren't collected
        # This handles the case where user didn't click on every accession in the listbox
        if self.current_multiple_accessions:
            classification_rules = self.data_manager.data.get("classification_rules", {})
            direct_lookups = self.data_manager.data.get("direct_lookups", {})
            
            # Get set of accession numbers already in multi_accession_data
            collected_acc_nums = set()
            for entry, data in self.multi_accession_data.items():
                acc_num = data.get("accession_number") or _extract_accession_number(entry)
                collected_acc_nums.add(acc_num)
            
            for acc_entry in self.current_multiple_accessions:
                acc_num = _extract_accession_number(acc_entry)
                
                # Skip if already collected
                if acc_num in collected_acc_nums:
                    continue
                
                # Try to extract procedure from listbox entry format "ACC (PROC)"
                procedure = "Unknown"
                study_type = "Unknown"
                rvu = 0.0
                
                if '(' in acc_entry and ')' in acc_entry:
                    entry_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', acc_entry)
                    if entry_match:
                        procedure = entry_match.group(2).strip()
                        study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                
                # Add to multi_accession_data
                self.multi_accession_data[acc_entry] = {
                    "procedure": procedure,
                    "study_type": study_type,
                    "rvu": rvu,
                    "patient_class": self.current_patient_class or "",
                    "accession_number": acc_num,
                }
                logger.info(f"Auto-collected uncollected accession {acc_num}: {procedure} ({rvu} RVU)")
        
        if not self.multi_accession_data:
            return
        
        all_entries = list(self.multi_accession_data.keys())
        num_studies = len(all_entries)
        
        # Determine total duration and split evenly
        if self.multi_accession_start_time:
            total_duration = (current_time - self.multi_accession_start_time).total_seconds()
        else:
            total_duration = 0
        duration_per_study = total_duration / num_studies if num_studies > 0 else 0
        
        # Get patient class from first entry (applies to all)
        patient_class_val = ""
        for d in self.multi_accession_data.values():
            if d.get("patient_class"):
                patient_class_val = d["patient_class"]
                break
        
        time_performed = self.multi_accession_start_time.isoformat() if self.multi_accession_start_time else current_time.isoformat()
        
        # DEDUPLICATE: Build a map from accession NUMBER to best data
        # This prevents recording the same accession multiple times when entries have different keys
        # (e.g., same accession appears with different procedure text in entry format "ACC (PROC)")
        accession_to_data = {}  # accession_number -> {entry, data}
        
        for entry in all_entries:
            data = self.multi_accession_data[entry]
            
            # Extract pure accession number
            if data.get("accession_number"):
                acc_num = data["accession_number"]
            elif '(' in entry and ')' in entry:
                acc_match = re.match(r'^([^(]+)', entry)
                acc_num = acc_match.group(1).strip() if acc_match else entry.strip()
            else:
                acc_num = entry.strip()
            
            # Check if we already have data for this accession number
            if acc_num in accession_to_data:
                # Keep the entry with more information (non-Unknown study type, higher RVU)
                existing_data = accession_to_data[acc_num]["data"]
                existing_unknown = existing_data.get("study_type", "Unknown") == "Unknown"
                new_unknown = data.get("study_type", "Unknown") == "Unknown"
                
                # Prefer known study type over Unknown
                if existing_unknown and not new_unknown:
                    accession_to_data[acc_num] = {"entry": entry, "data": data}
                    logger.debug(f"Replaced duplicate accession {acc_num}: Unknown -> {data.get('study_type')}")
                # If both known or both unknown, keep higher RVU
                elif existing_unknown == new_unknown and data.get("rvu", 0) > existing_data.get("rvu", 0):
                    accession_to_data[acc_num] = {"entry": entry, "data": data}
                    logger.debug(f"Replaced duplicate accession {acc_num}: higher RVU {data.get('rvu')}")
                else:
                    logger.debug(f"Skipping duplicate accession {acc_num}: keeping existing {existing_data.get('study_type')}")
            else:
                accession_to_data[acc_num] = {"entry": entry, "data": data}
        
        # Get unique accession numbers (deduplicated)
        accession_numbers = list(accession_to_data.keys())
        num_unique_studies = len(accession_numbers)
        
        if num_unique_studies < num_studies:
            logger.info(f"Deduplicated multi-accession: {num_studies} entries -> {num_unique_studies} unique accessions")
        
        # Recalculate duration per study based on unique count
        duration_per_study = total_duration / num_unique_studies if num_unique_studies > 0 else 0
        
        # Generate a unique group ID to link these studies for duplicate detection
        multi_accession_group_id = "_".join(sorted(accession_numbers))
        
        total_rvu = 0
        recorded_count = 0
        
        # Record each UNIQUE study
        for accession in accession_numbers:
            data = accession_to_data[accession]["data"]
            
            study_record = {
                "accession": accession,
                "procedure": data.get("procedure", "Unknown"),
                "patient_class": patient_class_val,
                "study_type": data.get("study_type", "Unknown"),
                "rvu": data.get("rvu", 0),
                "time_performed": time_performed,
                "time_finished": current_time.isoformat(),
                "duration_seconds": duration_per_study,
                # Track that this was from a multi-accession session
                "from_multi_accession": True,
                "multi_accession_group": multi_accession_group_id,
                "multi_accession_count": num_unique_studies,
            }
            
            total_rvu += data.get("rvu", 0)
            
            self._record_or_update_study(study_record)
            self.tracker.mark_seen(accession)
            logger.debug(f"Recorded individual study from multi-accession: {accession}")
            recorded_count += 1
        
        # Clear redo buffer when new study is added
        self.last_undone_study = None
        self.undo_used = False
        self.undo_btn.config(state=tk.NORMAL, text="Undo")
        
        # Update mini window button if it exists
        if self.mini_window and self.mini_window.undo_btn:
            self.mini_window.undo_btn.config(text="U")
        
        logger.info(f"Recorded multi-accession: {recorded_count} individual studies ({total_rvu:.1f} total RVU) - Duration: {total_duration:.1f}s")
        self.update_display()
    
    def _extract_powerscribe_data(self) -> dict:
        """Extract data from PowerScribe. Returns data dict with 'found', 'accession', etc."""
        data = {
            'found': False,
            'procedure': '',
            'accession': '',
            'patient_class': '',
            'accession_title': '',
            'multiple_accessions': [],
            'elements': {},
            'source': 'PowerScribe'
        }
        
        window = self.cached_window
        if not window:
            window = find_powerscribe_window()
        
        if window:
            # Validate window still exists
            try:
                _window_text_with_timeout(window, timeout=0.5, element_name="PowerScribe window validation")
                self.cached_window = window
            except:
                self.cached_window = None
                self.cached_elements = {}
                window = find_powerscribe_window()
        
        if window:
            data['found'] = True
            
            # Smart caching: use cache if available, but invalidate on empty accession
            elements = find_elements_by_automation_id(
                window,
                ["labelProcDescription", "labelAccessionTitle", "labelAccession", "labelPatientClass", "listBoxAccessions"],
                self.cached_elements
            )
            
            data['elements'] = elements
            data['procedure'] = elements.get("labelProcDescription", {}).get("text", "").strip()
            data['patient_class'] = elements.get("labelPatientClass", {}).get("text", "").strip()
            data['accession_title'] = elements.get("labelAccessionTitle", {}).get("text", "").strip()
            data['accession'] = elements.get("labelAccession", {}).get("text", "").strip()
            
            if data['accession']:
                # Study is open - update cache for next poll
                self.cached_elements.update(elements)
            else:
                # No accession - could be stale cache or study closed
                # Clear cache and do ONE fresh search to confirm
                if self.cached_elements:
                    self.cached_elements = {}
                    # Redo search with empty cache
                    elements = find_elements_by_automation_id(
                        window,
                        ["labelProcDescription", "labelAccessionTitle", "labelAccession", "labelPatientClass", "listBoxAccessions"],
                        {}
                    )
                    data['accession'] = elements.get("labelAccession", {}).get("text", "").strip()
                    if data['accession']:
                        # Found it on fresh search - cache was stale
                        data['procedure'] = elements.get("labelProcDescription", {}).get("text", "").strip()
                        data['patient_class'] = elements.get("labelPatientClass", {}).get("text", "").strip()
                        data['accession_title'] = elements.get("labelAccessionTitle", {}).get("text", "").strip()
                        self.cached_elements.update(elements)
            
            # Handle multiple accessions - check listbox if it exists
            # Read listbox even if labelAccession is empty (multi-accession mode may have empty label)
            # Check both: study is open (accession exists) OR multi-accession mode (accession_title is plural)
            is_multi_title = data['accession_title'] in ("Accessions:", "Accessions")
            should_check_listbox = data['accession'] or is_multi_title
            
            if should_check_listbox and elements.get("listBoxAccessions"):
                try:
                    listbox = elements["listBoxAccessions"]["element"]
                    listbox_children = []
                    try:
                        children_gen = listbox.children()
                        count = 0
                        for child_elem in children_gen:
                            listbox_children.append(child_elem)
                            count += 1
                            if count >= 50:
                                break
                    except Exception as e:
                        logger.debug(f"listbox.children() iteration failed: {e}")
                        listbox_children = []
                    
                    for child in listbox_children:
                        try:
                            item_text = _window_text_with_timeout(child, timeout=0.3, element_name="listbox child").strip()
                            if item_text:
                                data['multiple_accessions'].append(item_text)
                        except:
                            pass
                    
                    # In multi-accession mode, if labelAccession is empty but we got listbox items,
                    # use the first listbox item as the accession for tracking purposes
                    if is_multi_title and not data['accession'] and data['multiple_accessions']:
                        first_acc = data['multiple_accessions'][0]
                        # Extract just the accession number if format is "ACC (PROC)"
                        if '(' in first_acc:
                            acc_match = re.match(r'^([^(]+)', first_acc)
                            if acc_match:
                                data['accession'] = acc_match.group(1).strip()
                        else:
                            data['accession'] = first_acc.strip()
                        logger.debug(f"Set accession from listbox in multi-accession mode: {data['accession']}")
                except:
                    pass
        
        return data
    
    def _extract_mosaic_data(self) -> dict:
        """Extract data from Mosaic. Returns data dict with 'found', 'accession', etc.
        
        Extraction strategy (as of v1.4.6):
        1. PRIMARY: Use extract_mosaic_data_v2() with main window descendants
           - More reliable element discovery
           - Better accession pattern matching
        2. FALLBACK: Use legacy extract_mosaic_data() with WebView2 recursion
           - Only used if primary method fails to find accession
        
        NOTE: Multi-accession support is currently limited in Mosaic.
        """
        data = {
            'found': False,
            'procedure': '',
            'accession': '',
            'patient_class': 'Unknown',
            'accession_title': '',
            'multiple_accessions': [],
            'elements': {},
            'source': 'Mosaic'
        }
        
        main_window = find_mosaic_window()
        
        if main_window:
            try:
                # Validate window still exists
                _window_text_with_timeout(main_window, timeout=1.0, element_name="Mosaic window validation")
                data['found'] = True
                
                # =========================================================
                # PRIMARY METHOD (v2): Use main window descendants
                # This is the new, more reliable extraction method
                # =========================================================
                mosaic_data = extract_mosaic_data_v2(main_window)
                extraction_method = mosaic_data.get('extraction_method', '')
                
                if mosaic_data.get('accession'):
                    # Primary method succeeded
                    logger.debug(f"Mosaic v2 extraction succeeded: {extraction_method}")
                    data['procedure'] = mosaic_data.get('procedure', '')
                    data['accession'] = mosaic_data.get('accession', '')
                    
                    # Handle multiple accessions
                    multiple_accessions_data = mosaic_data.get('multiple_accessions', [])
                    if multiple_accessions_data:
                        for acc_data in multiple_accessions_data:
                            acc = acc_data.get('accession', '')
                            proc = acc_data.get('procedure', '')
                            if proc:
                                data['multiple_accessions'].append(f"{acc} ({proc})")
                            else:
                                data['multiple_accessions'].append(acc)
                        
                        # Set first as primary if not already set
                        if not data['accession'] and multiple_accessions_data:
                            data['accession'] = multiple_accessions_data[0].get('accession', '')
                            if not data['procedure'] and multiple_accessions_data[0].get('procedure'):
                                data['procedure'] = multiple_accessions_data[0].get('procedure', '')
                else:
                    # =========================================================
                    # FALLBACK METHOD (v1 legacy): Use WebView2 recursion
                    # Only used if primary method didn't find accession
                    # TODO: Remove this fallback once v2 is proven stable
                    # =========================================================
                    logger.debug("Mosaic v2 extraction failed, trying legacy method")
                    webview = find_mosaic_webview_element(main_window)
                    
                    if webview:
                        mosaic_data = extract_mosaic_data(webview)
                        
                        data['procedure'] = mosaic_data.get('procedure', '')
                        
                        # Handle multiple accessions
                        multiple_accessions_data = mosaic_data.get('multiple_accessions', [])
                        if multiple_accessions_data:
                            for acc_data in multiple_accessions_data:
                                acc = acc_data.get('accession', '')
                                proc = acc_data.get('procedure', '')
                                if proc:
                                    data['multiple_accessions'].append(f"{acc} ({proc})")
                                else:
                                    data['multiple_accessions'].append(acc)
                            
                            if multiple_accessions_data:
                                data['accession'] = multiple_accessions_data[0].get('accession', '')
                                if not data['procedure'] and multiple_accessions_data[0].get('procedure'):
                                    data['procedure'] = multiple_accessions_data[0].get('procedure', '')
                        else:
                            data['accession'] = mosaic_data.get('accession', '')
                            if not data['procedure']:
                                data['procedure'] = mosaic_data.get('procedure', '')
                        
                        if data['accession']:
                            logger.debug("Mosaic legacy extraction succeeded")
                        else:
                            logger.debug("Mosaic legacy extraction also failed - no accession found")
                    else:
                        logger.debug("Mosaic fallback: WebView2 element not found")
                        
            except Exception as e:
                logger.debug(f"Mosaic extraction error: {e}")
                data['found'] = False
        
        return data
    
    def _toggle_data_source(self):
        """Manually toggle between PowerScribe and Mosaic data sources."""
        try:
            # Toggle between the two sources
            if self._primary_source == "PowerScribe":
                new_source = "Mosaic"
            else:
                new_source = "PowerScribe"
            
            self._primary_source = new_source
            self._active_source = new_source
            
            # Update the indicator immediately
            self._update_source_indicator(new_source)
            
            logger.info(f"Manually switched data source to: {new_source}")
        except Exception as e:
            logger.error(f"Error toggling data source: {e}")
    
    def _update_source_indicator(self, source: str):
        """Update the data source indicator in the UI (thread-safe)."""
        try:
            if source:
                text = f"📍 {source}"
            else:
                text = "detecting..."
            self.root.after(0, lambda: self.data_source_indicator.config(text=text))
        except:
            pass
    
    def _update_backup_status_display(self):
        """Update the backup status indicator in the UI."""
        try:
            if not hasattr(self, 'backup_status_label'):
                return
            
            backup_mgr = self.data_manager.backup_manager
            status = backup_mgr.get_backup_status()
            
            if status["enabled"]:
                text = f"{status['status_icon']} {status['time_since_backup'] or 'Ready'}"
                fg_color = "gray" if status["last_backup_status"] == "success" else "orange"
            else:
                text = ""  # Don't show anything if backup is disabled
                fg_color = "gray"
            
            self.backup_status_label.config(text=text, fg=fg_color)
        except Exception as e:
            logger.debug(f"Error updating backup status display: {e}")
    
    def _perform_shift_end_backup(self):
        """Perform backup at shift end if enabled and scheduled."""
        try:
            backup_mgr = self.data_manager.backup_manager
            
            # Check if backup is enabled and scheduled for shift end
            if not self.data_manager.data.get("backup", {}).get("cloud_backup_enabled", False):
                return
            
            schedule = self.data_manager.data.get("backup", {}).get("backup_schedule", "shift_end")
            if schedule != "shift_end":
                return
            
            logger.info("Performing automatic backup at shift end...")
            
            # Perform backup (runs synchronously - quick operation)
            result = backup_mgr.create_backup(force=True)
            
            if result["success"]:
                logger.info(f"Shift-end backup completed: {result['path']}")
            else:
                logger.warning(f"Shift-end backup failed: {result['error']}")
            
            # Save updated backup status
            self.data_manager.save()
            
            # Update UI
            self._update_backup_status_display()
            
        except Exception as e:
            logger.error(f"Error performing shift-end backup: {e}")
    
    def _check_first_time_backup_prompt(self):
        """Check if we should prompt user to enable cloud backup on first run."""
        try:
            backup_settings = self.data_manager.data.get("backup", {})
            
            # Debug logging
            logger.debug(f"Backup prompt check - cloud_backup_enabled: {backup_settings.get('cloud_backup_enabled', False)}, "
                        f"setup_prompt_dismissed: {backup_settings.get('setup_prompt_dismissed', False)}, "
                        f"first_backup_prompt_shown: {backup_settings.get('first_backup_prompt_shown', False)}")
            
            # Don't prompt if:
            # - Backup is already enabled
            # - User has already dismissed the prompt
            # - User has already seen the prompt (first_backup_prompt_shown)
            # - OneDrive is not available
            if backup_settings.get("cloud_backup_enabled", False):
                logger.debug("Skipping backup prompt - backup is already enabled")
                return
            if backup_settings.get("setup_prompt_dismissed", False):
                logger.debug("Skipping backup prompt - user dismissed it")
                return
            if backup_settings.get("first_backup_prompt_shown", False):
                logger.debug("Skipping backup prompt - user has already seen it")
                return
            if not self.data_manager.backup_manager.is_onedrive_available():
                logger.debug("Skipping backup prompt - OneDrive not available")
                return
            
            # Schedule the prompt to appear shortly after app starts
            logger.info("Scheduling backup setup prompt")
            self.root.after(2000, self._show_backup_setup_prompt)
            
        except Exception as e:
            logger.error(f"Error checking backup prompt: {e}")
    
    def _show_backup_setup_prompt(self):
        """Show the first-time backup setup prompt."""
        try:
            backup_mgr = self.data_manager.backup_manager
            onedrive_path = backup_mgr._detect_onedrive_folder()
            
            # Create a simple, non-intrusive dialog
            dialog = tk.Toplevel(self.root)
            dialog.title("☁️ Protect Your Work Data")
            dialog.transient(self.root)
            dialog.grab_set()
            
            # Position near main window
            x = self.root.winfo_x() + 50
            y = self.root.winfo_y() + 50
            dialog.geometry(f"380x220+{x}+{y}")
            dialog.resizable(False, False)
            
            # Content frame
            frame = ttk.Frame(dialog, padding="15")
            frame.pack(fill=tk.BOTH, expand=True)
            
            # Icon and title
            ttk.Label(frame, text="☁️ Enable Cloud Backup?", 
                     font=("Arial", 12, "bold")).pack(anchor=tk.W, pady=(0, 10))
            
            # Description
            ttk.Label(frame, text="OneDrive was detected on your computer.\n"
                                  "Automatic backups protect your work data from loss.",
                     font=("Arial", 9), wraplength=350, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 10))
            
            # OneDrive path
            ttk.Label(frame, text=f"📁 {onedrive_path}", 
                     font=("Arial", 8), foreground="gray").pack(anchor=tk.W, pady=(0, 15))
            
            # Buttons frame
            btn_frame = ttk.Frame(frame)
            btn_frame.pack(fill=tk.X)
            
            def enable_backup():
                # Enable backup with default settings
                if "backup" not in self.data_manager.data:
                    self.data_manager.data["backup"] = {}
                self.data_manager.data["backup"]["cloud_backup_enabled"] = True
                self.data_manager.data["backup"]["backup_schedule"] = "shift_end"
                # Mark that user has seen and responded to the prompt
                self.data_manager.data["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.data["backup"]["first_backup_prompt_shown"] = True
                
                # Also update BackupManager's settings reference
                if "backup" not in self.data_manager.backup_manager.settings:
                    self.data_manager.backup_manager.settings["backup"] = {}
                self.data_manager.backup_manager.settings["backup"]["cloud_backup_enabled"] = True
                self.data_manager.backup_manager.settings["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.backup_manager.settings["backup"]["first_backup_prompt_shown"] = True
                
                # Save to disk
                self.data_manager.save()
                
                # Verify save
                logger.info(f"Backup enabled - saved. cloud_backup_enabled: {self.data_manager.data.get('backup', {}).get('cloud_backup_enabled', False)}")
                
                # Update UI
                self._update_backup_status_display()
                
                dialog.destroy()
                messagebox.showinfo("Backup Enabled", 
                                   "Cloud backup is now enabled!\n\n"
                                   "Your data will be backed up automatically\n"
                                   "after each shift ends.")
            
            def maybe_later():
                # Mark that user has seen the prompt so it doesn't show again
                if "backup" not in self.data_manager.data:
                    self.data_manager.data["backup"] = {}
                self.data_manager.data["backup"]["first_backup_prompt_shown"] = True
                self.data_manager.save()
                dialog.destroy()
            
            def dont_ask_again():
                # Set flag to not ask again
                if "backup" not in self.data_manager.data:
                    self.data_manager.data["backup"] = {}
                self.data_manager.data["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.save()
                dialog.destroy()
            
            ttk.Button(btn_frame, text="Enable Backup", command=enable_backup).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="Maybe Later", command=maybe_later).pack(side=tk.LEFT, padx=2)
            ttk.Button(btn_frame, text="Don't Ask Again", command=dont_ask_again).pack(side=tk.RIGHT, padx=2)
            
        except Exception as e:
            logger.error(f"Error showing backup setup prompt: {e}")
    
    def _powerscribe_worker(self):
        """Background thread: Continuously poll PowerScribe or Mosaic for data with auto-switching."""
        import time
        
        while self._ps_thread_running:
            poll_start_time = time.time()
            try:
                data = {
                    'found': False,
                    'procedure': '',
                    'accession': '',
                    'patient_class': '',
                    'accession_title': '',
                    'multiple_accessions': [],
                    'elements': {},
                    'source': None
                }
                
                # Auto-switch logic: Check primary source first, then secondary if primary is idle
                primary_data = None
                secondary_data = None
                current_time = time.time()
                
                # Determine which sources are available (quick check)
                ps_available = quick_check_powerscribe()
                mosaic_available = quick_check_mosaic()
                
                # If only one source is available, use it
                if ps_available and not mosaic_available:
                    data = self._extract_powerscribe_data()
                    # Always set source when window is available (even if no study is open)
                    self._active_source = "PowerScribe"
                    if data.get('accession'):
                        self._primary_source = "PowerScribe"
                elif mosaic_available and not ps_available:
                    data = self._extract_mosaic_data()
                    # Always set source when window is available (even if no study is open)
                    self._active_source = "Mosaic"
                    if data.get('accession'):
                        self._primary_source = "Mosaic"
                elif ps_available and mosaic_available:
                    # Both available - use tiered polling
                    # Check primary source first
                    if self._primary_source == "PowerScribe":
                        primary_data = self._extract_powerscribe_data()
                    else:
                        primary_data = self._extract_mosaic_data()
                    
                    if primary_data.get('accession'):
                        # Primary has active study - use it, skip secondary
                        data = primary_data
                        self._active_source = self._primary_source
                    else:
                        # Primary is idle - check secondary
                        # But not too frequently (every 5 seconds when primary is idle)
                        if current_time - self._last_secondary_check >= self._secondary_check_interval:
                            self._last_secondary_check = current_time
                            
                            if self._primary_source == "PowerScribe":
                                secondary_data = self._extract_mosaic_data()
                            else:
                                secondary_data = self._extract_powerscribe_data()
                            
                            if secondary_data.get('accession'):
                                # Secondary has active study - SWITCH!
                                data = secondary_data
                                old_primary = self._primary_source
                                self._primary_source = secondary_data.get('source', self._primary_source)
                                self._active_source = self._primary_source
                                logger.info(f"Auto-switched data source: {old_primary} → {self._active_source}")
                            else:
                                # Neither has active study - use primary's data (still shows window found)
                                data = primary_data
                                self._active_source = self._primary_source
                        else:
                            # Not time to check secondary yet - use primary data
                            data = primary_data
                            self._active_source = self._primary_source
                else:
                    # Neither available
                    self._active_source = None
                
                # Update source indicator
                self._update_source_indicator(self._active_source)
                
                # Update shared data IMMEDIATELY with PowerScribe/Mosaic data (before Clario query)
                # This ensures the display shows data immediately even if Clario is slow
                current_accession = data.get('accession', '').strip()
                current_procedure = data.get('procedure', '').strip()
                is_na_procedure = current_procedure.lower() in ["n/a", "na", "none", ""]
                
                with self._ps_lock:
                    self._ps_data = data.copy()  # Store copy immediately
                    
                    # If we have a valid accession and procedure, store it as pending
                    # This ensures we don't lose studies if procedure changes to N/A before refresh_data
                    if current_accession and current_procedure and not is_na_procedure:
                        self._pending_studies[current_accession] = {
                            'procedure': current_procedure,
                            'patient_class': data.get('patient_class', ''),
                            'detected_at': time.time()
                        }
                        logger.debug(f"Stored pending study: {current_accession} - {current_procedure}")
                
                # Query Clario for patient class only when a new study is detected (accession changed)
                # Do this AFTER storing initial data so display isn't blocked
                multiple_accessions_list = data.get('multiple_accessions', [])
                
                # For multi-accession studies, extract all accession numbers
                all_accessions = set()
                if current_accession:
                    all_accessions.add(current_accession)
                if multiple_accessions_list:
                    for acc_entry in multiple_accessions_list:
                        # Format: "ACC (PROC)" or just "ACC"
                        if '(' in acc_entry and ')' in acc_entry:
                            acc_match = re.match(r'^([^(]+)', acc_entry)
                            if acc_match:
                                all_accessions.add(acc_match.group(1).strip())
                            else:
                                all_accessions.add(acc_entry.strip())
                
                if data.get('found') and all_accessions:
                    # Check if this is a new study (accession changed)
                    # For multi-accession, check if any accession is new
                    with self._ps_lock:
                        is_new_study = not any(acc == self._last_clario_accession for acc in all_accessions)
                        last_accession = self._last_clario_accession
                    
                    logger.debug(f"Checking Clario: current_accession='{current_accession}', all_accessions={list(all_accessions)}, last_clario_accession='{last_accession}', is_new_study={is_new_study}")
                    
                    if is_new_study:
                        # New study detected - query Clario (don't pass target_accession for multi-accession, let it match any)
                        # Query Clario in a separate try block so it doesn't block data display
                        logger.info(f"New study detected, querying Clario. Multi-accession: {len(all_accessions) > 1}, accessions: {list(all_accessions)}")
                        try:
                            # Query Clario without target_accession for multi-accession studies
                            # This allows Clario to match any of the accessions
                            if len(all_accessions) > 1:
                                # Multi-accession: query without target, then check if result matches any
                                clario_data = extract_clario_patient_class(target_accession=None)
                            else:
                                # Single accession: query with target
                                clario_data = extract_clario_patient_class(target_accession=current_accession)
                            
                            if clario_data and clario_data.get('patient_class'):
                                # Verify accession matches (for multi-accession, match any accession)
                                clario_accession = clario_data.get('accession', '').strip()
                                logger.info(f"Clario returned: patient_class='{clario_data.get('patient_class')}', accession='{clario_accession}'")
                                
                                # Check if Clario accession matches any of our accessions
                                accession_matches = clario_accession in all_accessions if clario_accession else False
                                
                                if accession_matches:
                                    # Accession matches - update data with Clario's patient class
                                    with self._ps_lock:
                                        # Update the stored data with Clario patient class
                                        self._ps_data['patient_class'] = clario_data['patient_class']
                                        self._last_clario_accession = clario_accession
                                        # Cache patient class for all accessions in this multi-accession study
                                        for acc in all_accessions:
                                            self._clario_patient_class_cache[acc] = clario_data['patient_class']
                                    logger.info(f"Clario patient class OVERRIDES: {clario_data['patient_class']} for study (matched accession: {clario_accession})")
                                    # Trigger immediate UI refresh to display Clario patient class
                                    self.root.after(0, self.refresh_data)
                            else:
                                # Clario didn't return data - keep existing patient_class from PowerScribe/Mosaic
                                # But still mark this study as seen to prevent repeated queries
                                with self._ps_lock:
                                    if current_accession:
                                        self._last_clario_accession = current_accession
                                if clario_data:
                                    logger.info(f"Clario returned data but no patient_class. Accession='{clario_data.get('accession', '')}'")
                                else:
                                    logger.info(f"Clario did not return any data")
                        except Exception as e:
                            logger.info(f"Clario query error: {e}", exc_info=True)
                            # On error, keep existing patient_class (already stored in _ps_data)
                            # Mark study as seen to prevent repeated queries
                            with self._ps_lock:
                                if current_accession:
                                    self._last_clario_accession = current_accession
                    else:
                        # Same study - check if we have cached Clario patient class for any accession
                        with self._ps_lock:
                            cached_clario_class = None
                            for acc in all_accessions:
                                cached = self._clario_patient_class_cache.get(acc)
                                if cached:
                                    cached_clario_class = cached
                                    break
                        
                        if cached_clario_class:
                            # Update stored data with cached Clario patient class
                            with self._ps_lock:
                                self._ps_data['patient_class'] = cached_clario_class
                            logger.debug(f"Same study (accessions={list(all_accessions)}), using cached Clario patient class: {cached_clario_class}")
                            # Trigger immediate UI refresh to display cached Clario patient class
                            self.root.after(0, self.refresh_data)
                elif data.get('found') and not all_accessions:
                    # No accession - study is closed
                    # Clear last Clario accession so if the same study reopens, it queries Clario again
                    with self._ps_lock:
                        if self._last_clario_accession:
                            logger.debug(f"Study closed - clearing _last_clario_accession (was: {self._last_clario_accession})")
                            self._last_clario_accession = ""
                        # For Mosaic, ensure patient_class is set to 'Unknown' if missing
                        current_source = data.get('source') or self._active_source
                        if current_source == "Mosaic":
                            if not self._ps_data.get('patient_class'):
                                self._ps_data['patient_class'] = 'Unknown'
                    logger.debug(f"No accession found, cannot query Clario")
                
                # Adaptive polling: adjust interval based on activity state
                # Use the stored data from _ps_data for consistency
                with self._ps_lock:
                    current_accession_check = self._ps_data.get('accession', '').strip()
                
                # Detect if accession changed (including going from something to empty)
                accession_changed = current_accession_check != self._last_accession_seen
                study_just_closed = accession_changed and self._last_accession_seen and not current_accession_check
                
                if accession_changed:
                    # Accession changed - use fast polling
                    self._last_accession_seen = current_accession_check
                    self._last_data_change_time = time.time()
                    if study_just_closed:
                        # Study just closed - use very fast polling (300ms) to confirm closure quickly
                        self._current_poll_interval = 0.3
                        logger.debug(f"Study closed - fast polling at 0.3s")
                    else:
                        # New study appeared - use fast polling (500ms)
                        self._current_poll_interval = 0.5
                else:
                    # Check how long since last change
                    time_since_change = time.time() - self._last_data_change_time
                    if current_accession_check:
                        # Active study but no change - moderate polling (1000ms)
                        if time_since_change > 1.0:
                            self._current_poll_interval = 1.0
                        else:
                            self._current_poll_interval = 0.5
                    else:
                        # No active study - keep fast polling for 2 seconds after closure
                        # This ensures quick detection and prevents false re-detection
                        if time_since_change < 2.0:
                            self._current_poll_interval = 0.3
                        else:
                            # After 2 seconds of no study, slow down to 1.5s (not 2.0s)
                            self._current_poll_interval = 1.5
                
                # Clean up stale pending studies (older than 30 seconds)
                current_time_cleanup = time.time()
                with self._ps_lock:
                    stale_accessions = [
                        acc for acc, data in self._pending_studies.items()
                        if current_time_cleanup - data.get('detected_at', 0) > 30
                    ]
                    for acc in stale_accessions:
                        logger.debug(f"Removing stale pending study: {acc}")
                        del self._pending_studies[acc]
                
            except Exception as e:
                logger.error(f"Worker error: {e}", exc_info=True)
            
            # Watchdog: detect if polling loop took too long
            poll_duration = time.time() - poll_start_time
            if poll_duration > 5.0:
                logger.warning(f"⚠️  Polling loop took {poll_duration:.1f}s (expected <1s) - UI automation may be hanging")
                # Clear cached windows/elements to force fresh detection next time
                self.cached_window = None
                self.cached_elements = {}
                logger.info("Cleared cached windows due to slow polling - will re-detect next cycle")
            
            # Use adaptive polling interval
            time.sleep(self._current_poll_interval)
    
    def refresh_data(self):
        """Refresh data from PowerScribe - reads from background thread data."""
        try:
            # Get data from background thread (non-blocking)
            with self._ps_lock:
                ps_data = self._ps_data.copy()
            
            # Use auto-detected source instead of settings
            data_source = ps_data.get('source') or self._active_source or "PowerScribe"
            source_name = data_source if data_source else "Unknown"
            
            if not ps_data.get('found', False):
                self.root.title(f"RVU Counter - {source_name} not found")
                
                # If we were in multi-accession mode, record the study before clearing state
                if self.multi_accession_mode and self.multi_accession_data:
                    logger.info("PowerScribe window closed while in multi-accession mode - recording study")
                    self._record_multi_accession_study(datetime.now())
                    self.multi_accession_mode = False
                    self.multi_accession_data = {}
                    self.multi_accession_start_time = None
                    self.multi_accession_last_procedure = ""
                
                self.current_accession = ""
                self.current_procedure = ""
                self.current_study_type = ""
                self.update_debug_display()
                return
        
            self.root.title("RVU Counter")
            
            # Extract data from background thread results
            elements = ps_data.get('elements', {})
            procedure = ps_data.get('procedure', '')
            patient_class = ps_data.get('patient_class', '')
            accession_title = ps_data.get('accession_title', '')
            accession = ps_data.get('accession', '')
            multiple_accessions = ps_data.get('multiple_accessions', [])
            
            # For Mosaic: multiple accessions should be treated as separate studies
            # For PowerScribe: use the existing multi-accession mode logic
            mosaic_multiple_mode = False  # Track if we're in Mosaic multi-accession mode
            
            # Debug: log what we're getting from worker thread (only when there's data)
            if data_source == "Mosaic" and (accession or procedure):
                logger.debug(f"Mosaic data - procedure: '{procedure}', accession: '{accession}', multiple_accessions: {multiple_accessions}")
            
            # For Mosaic, also check if we have multiple active studies that might indicate multi-accession
            # This handles the case where extraction found them separately but they should be displayed together
            if data_source == "Mosaic" and not multiple_accessions:
                # Get all currently active Mosaic studies (check if they were recently added - within last 30 seconds)
                current_time_check = datetime.now()
                active_mosaic_studies = []
                for acc, study in self.tracker.active_studies.items():
                    if acc and study.get('patient_class') == 'Unknown':  # Mosaic studies have Unknown patient class
                        time_since_start = (current_time_check - study['start_time']).total_seconds()
                        if time_since_start < 30:  # Only include recently added studies (within 30 seconds)
                            active_mosaic_studies.append(acc)
                
                if len(active_mosaic_studies) > 1:
                    # We have multiple active studies - construct multiple_accessions for display
                    multiple_accessions = []
                    for acc in active_mosaic_studies:
                        if acc in self.tracker.active_studies:
                            study = self.tracker.active_studies[acc]
                            proc = study.get('procedure', '')
                            if proc:
                                multiple_accessions.append(f"{acc} ({proc})")
                            else:
                                multiple_accessions.append(acc)
                    logger.info(f"Mosaic: Constructed multiple_accessions from {len(active_mosaic_studies)} active studies: {multiple_accessions}")
                    # Also update accession and procedure if not set
                    if not accession and active_mosaic_studies:
                        accession = active_mosaic_studies[0]
                    # Set procedure to "Multiple studies" when we have multiple
                    if len(active_mosaic_studies) > 1:
                        procedure = "Multiple studies"
                    elif not procedure and active_mosaic_studies:
                        # Single study case - get procedure from it
                        if active_mosaic_studies[0] in self.tracker.active_studies:
                            study = self.tracker.active_studies[active_mosaic_studies[0]]
                            procedure = study.get('procedure', '')
            
            if data_source == "Mosaic" and multiple_accessions and len(multiple_accessions) > 1:
                mosaic_multiple_mode = True
                # Mosaic provides one-to-one accession-to-procedure mapping
                # Track each as a separate study that can complete independently
                is_multiple_mode = False  # Don't use PowerScribe multi-accession mode
                is_multi_accession_view = False
                
                # Parse multiple accessions from format "ACC (PROC)" or just "ACC"
                # Extract accession and procedure pairs
                mosaic_accession_procedures = []
                logger.debug(f"Parsing Mosaic multiple accessions: {multiple_accessions}")
                for acc_entry in multiple_accessions:
                    if '(' in acc_entry and ')' in acc_entry:
                        # Format: "ACC (PROC)"
                        acc_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', acc_entry)
                        if acc_match:
                            acc = acc_match.group(1).strip()
                            proc = acc_match.group(2).strip()
                            mosaic_accession_procedures.append({'accession': acc, 'procedure': proc})
                            logger.debug(f"Parsed: accession='{acc}', procedure='{proc}'")
                    else:
                        # Just accession, use current procedure if available
                        mosaic_accession_procedures.append({'accession': acc_entry, 'procedure': procedure})
                        logger.debug(f"Parsed (no proc): accession='{acc_entry}', using procedure='{procedure}'")
                
                logger.debug(f"Parsed {len(mosaic_accession_procedures)} accession/procedure pairs")
                
                # Track each accession separately (they'll complete when they disappear)
                for acc_data in mosaic_accession_procedures:
                    acc = acc_data['accession']
                    proc = acc_data['procedure']
                    
                    # Only track if not already seen (if ignoring duplicates)
                    # Also check if it was part of a multi-accession study
                    ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
                    if self.tracker.should_ignore(acc, ignore_duplicates, self.data_manager):
                        continue
                    
                    # Track as individual study
                    if acc not in self.tracker.active_studies and proc:
                        classification_rules = self.data_manager.data.get("classification_rules", {})
                        direct_lookups = self.data_manager.data.get("direct_lookups", {})
                        study_type, rvu = match_study_type(proc, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                        
                        current_time_tracking = datetime.now()
                        self.tracker.active_studies[acc] = {
                            "accession": acc,
                            "procedure": proc,
                            "study_type": study_type,
                            "rvu": rvu,
                            "patient_class": patient_class,
                            "start_time": current_time_tracking,
                            "last_seen": current_time_tracking  # Required by tracker
                        }
                        logger.info(f"Started tracking Mosaic study: {acc} - {proc} ({rvu} RVU)")
                
                # Set display to first accession/procedure and calculate study type/RVU
                if mosaic_accession_procedures:
                    accession = mosaic_accession_procedures[0]['accession']
                    # For multiple accessions, show "Multiple studies" instead of first procedure
                    if len(mosaic_accession_procedures) > 1:
                        procedure = "Multiple studies"
                    else:
                        # Single accession - get the procedure
                        first_procedure = None
                        for acc_data in mosaic_accession_procedures:
                            proc = acc_data.get('procedure', '')
                            if proc:
                                first_procedure = proc
                                break
                        procedure = first_procedure or procedure  # Use first valid procedure, or fallback
                    
                    # Always set study type and RVU for display
                    classification_rules = self.data_manager.data.get("classification_rules", {})
                    direct_lookups = self.data_manager.data.get("direct_lookups", {})
                    
                    # If multiple accessions, show summary of all studies
                    if len(mosaic_accession_procedures) > 1:
                        # Calculate total RVU and determine modality from all procedures
                        modalities = set()
                        total_rvu = 0
                        valid_procedures = []
                        
                        for acc_data in mosaic_accession_procedures:
                            proc = acc_data.get('procedure', '')
                            if proc:
                                valid_procedures.append(proc)
                                temp_st, temp_rvu = match_study_type(proc, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                                total_rvu += temp_rvu
                                if temp_st:
                                    parts = temp_st.split()
                                    if parts:
                                        modalities.add(parts[0])
                        
                        if valid_procedures:
                            modality = list(modalities)[0] if modalities else "Studies"
                            self.current_study_type = f"{len(mosaic_accession_procedures)} {modality} studies"
                            self.current_study_rvu = total_rvu
                            # Show "Multiple studies" for procedure when there are multiple accessions
                            procedure = "Multiple studies"
                        else:
                            # No valid procedures yet - set placeholder so display shows something
                            self.current_study_type = f"{len(mosaic_accession_procedures)} studies"
                            self.current_study_rvu = 0.0
                            procedure = "Multiple studies"  # Placeholder to trigger display
                    else:
                        # Single accession - set from first (and only) accession
                        if procedure:
                            study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                            self.current_study_type = study_type
                            self.current_study_rvu = rvu
                else:
                            self.current_study_type = ""
                            self.current_study_rvu = 0.0
                
                # Debug: log what we're setting for display
                logger.debug(f"Mosaic multi-accession display - procedure: '{procedure}', accession: '{accession}', study_type: '{self.current_study_type}', rvu: {self.current_study_rvu}")
            else:
                # PowerScribe logic: Check labelAccessionTitle to determine single vs multiple accession mode
                is_multiple_mode = accession_title == "Accessions:" or accession_title == "Accessions"
                is_multi_accession_view = False  # Flag to prevent normal single-study tracking
            
            # Only process PowerScribe multi-accession mode if not Mosaic
            if data_source != "Mosaic" and is_multiple_mode and multiple_accessions:
                logger.debug(f"PowerScribe multi-accession mode: {len(multiple_accessions)} accessions, already in mode: {self.multi_accession_mode}")
                accession = "Multiple Accessions"
                is_multi_accession_view = True  # Flag to prevent normal single-study tracking
                
                # Check if we're transitioning from single to multi-accession
                if not self.multi_accession_mode:
                    # Check if ALL accessions were already completed (to prevent duplicates)
                    # Extract just accession numbers from multiple_accessions (format: "ACC (PROC)" or "ACC")
                    accession_numbers = []
                    for acc_entry in multiple_accessions:
                        if '(' in acc_entry and ')' in acc_entry:
                            # Format: "ACC (PROC)" - extract just the accession
                            acc_match = re.match(r'^([^(]+)', acc_entry)
                            if acc_match:
                                accession_numbers.append(acc_match.group(1).strip())
                        else:
                            # Just accession number
                            accession_numbers.append(acc_entry.strip())
                    
                    ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
                    
                    # Check ALL accessions against both seen_accessions AND database
                    # This is important because seen_accessions is session-based
                    all_recorded = False
                    recorded_count = 0
                    
                    if ignore_duplicates and accession_numbers:
                        current_shift = None
                        try:
                            current_shift = self.data_manager.db.get_current_shift()
                        except:
                            pass
                        
                        for acc in accession_numbers:
                            is_recorded = False
                            
                            # Check memory cache first
                            if acc in self.tracker.seen_accessions:
                                is_recorded = True
                            # Check database
                            elif current_shift:
                                try:
                                    db_record = self.data_manager.db.find_record_by_accession(
                                        current_shift['id'], acc
                                    )
                                    if db_record:
                                        is_recorded = True
                                        self.tracker.seen_accessions.add(acc)  # Cache for future
                                except:
                                    pass
                            # Check multi-accession history
                            if not is_recorded:
                                if self.tracker._was_part_of_multi_accession(acc, self.data_manager):
                                    is_recorded = True
                            
                            if is_recorded:
                                recorded_count += 1
                        
                        all_recorded = recorded_count == len(accession_numbers)
                    
                    if all_recorded:
                        # All accessions already recorded - DON'T enter multi_accession_mode
                        # Just display as duplicate study
                        logger.info(f"Duplicate multi-accession study detected (all {len(accession_numbers)} accessions already recorded): {accession_numbers}")
                        # Don't enter multi_accession_mode - this prevents re-recording
                        # The display will show "already recorded" via update_debug_display
                    else:
                        # Starting multi-accession mode (some or all are new)
                        self.multi_accession_mode = True
                        self.multi_accession_start_time = datetime.now()
                        self.multi_accession_data = {}
                        self.multi_accession_last_procedure = ""  # Reset so first procedure gets collected
                        
                        if recorded_count > 0:
                            logger.info(f"Starting multi-accession mode with {len(accession_numbers)} accessions ({recorded_count} already recorded)")
                        else:
                            logger.info(f"Starting multi-accession mode with {len(accession_numbers)} accessions")
                    
                    # Clear element cache to ensure fresh listbox data on next poll
                    # This is important for single-to-multi transition where the UI changes
                    self.cached_elements = {}
                    
                    # Check if any of the new accessions were being tracked as single
                    # If so, migrate their data to multi-accession tracking
                    # Must extract accession numbers since multiple_accessions may be "ACC (PROC)" format
                    # Track which accession NUMBERS we've already migrated to prevent duplicates
                    migrated_acc_nums = set()
                    
                    for acc_entry in multiple_accessions:
                        # Extract just the accession number from "ACC (PROC)" format
                        acc_num = _extract_accession_number(acc_entry)
                        
                        # Skip if we've already processed this accession NUMBER
                        # (could appear multiple times with different procedure text)
                        if acc_num in migrated_acc_nums:
                            logger.debug(f"Skipping duplicate accession {acc_num} during initial migration")
                            continue
                        
                        if acc_num in self.tracker.active_studies:
                            study = self.tracker.active_studies[acc_num]
                            # Store with BOTH the raw acc_entry (for listbox matching) and parsed acc_num
                            self.multi_accession_data[acc_entry] = {
                                "procedure": study["procedure"],
                                "study_type": study["study_type"],
                                "rvu": study["rvu"],
                                "patient_class": study.get("patient_class", ""),
                                "accession_number": acc_num,  # Store parsed accession for recording
                            }
                            # Remove from active_studies to prevent completion
                            del self.tracker.active_studies[acc_num]
                            migrated_acc_nums.add(acc_num)
                            logger.info(f"Migrated {acc_num} from single to multi-accession tracking (entry: {acc_entry})")
                    
                    logger.info(f"Started multi-accession mode with {len(multiple_accessions)} accessions")
                else:
                    # ALREADY in multi-accession mode - handle dynamic changes:
                    # 1. Check for NEW accessions added (2→3→4, etc.)
                    # 2. Check for accessions REMOVED from the list
                    
                    # Get current accession numbers from listbox
                    current_acc_nums = set(_extract_accession_number(e) for e in multiple_accessions)
                    
                    # Get tracked accession numbers
                    tracked_acc_nums = set()
                    for entry, data in self.multi_accession_data.items():
                        tracked_acc_nums.add(data.get("accession_number") or _extract_accession_number(entry))
                    
                    # Check for NEW accessions added
                    for acc_entry in multiple_accessions:
                        acc_num = _extract_accession_number(acc_entry)
                        
                        # Skip if already tracked (check by accession number, not entry string)
                        if acc_num in tracked_acc_nums:
                            continue
                        
                        # Check if this was being tracked as a single study
                        if acc_num in self.tracker.active_studies:
                            study = self.tracker.active_studies[acc_num]
                            self.multi_accession_data[acc_entry] = {
                                "procedure": study["procedure"],
                                "study_type": study["study_type"],
                                "rvu": study["rvu"],
                                "patient_class": study.get("patient_class", ""),
                                "accession_number": acc_num,
                            }
                            del self.tracker.active_studies[acc_num]
                            logger.info(f"ADDED {acc_num} to multi-accession (was single study, now {len(multiple_accessions)} total)")
                        else:
                            # New accession not previously tracked - will be collected when user views it
                            logger.debug(f"New accession {acc_num} added to multi-accession (will collect procedure when viewed)")
                    
                    # Check for accessions REMOVED (only if we have data for them)
                    entries_to_remove = []
                    for entry, data in self.multi_accession_data.items():
                        acc_num = data.get("accession_number") or _extract_accession_number(entry)
                        if acc_num not in current_acc_nums:
                            entries_to_remove.append((entry, acc_num, data))
                    
                    if entries_to_remove:
                        for entry, acc_num, data in entries_to_remove:
                            del self.multi_accession_data[entry]
                            logger.info(f"REMOVED {acc_num} from multi-accession (no longer in list, now {len(multiple_accessions)} total)")
                
                # Collect procedure for current view - ONLY when procedure changes
                # This ensures we only collect when user clicks a different accession
                if procedure and procedure.strip().lower() not in ["n/a", "na", "none", ""]:
                    # Check if this is a NEW procedure (different from last seen)
                    procedure_changed = (procedure != self.multi_accession_last_procedure)
                    
                    if procedure_changed:
                        classification_rules = self.data_manager.data.get("classification_rules", {})
                        direct_lookups = self.data_manager.data.get("direct_lookups", {})
                        study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                        
                        # Build set of accession NUMBERS already collected (not entry keys)
                        # This prevents adding the same accession twice under different entry formats
                        collected_acc_nums = set()
                        for entry, data in self.multi_accession_data.items():
                            existing_acc_num = data.get("accession_number") or _extract_accession_number(entry)
                            collected_acc_nums.add(existing_acc_num)
                        
                        # Find which accession this procedure belongs to
                        # Strategy: 
                        # 1. Try to match by procedure name in the listbox entry (format: "ACC (PROC)")
                        # 2. Fall back to first accession without data
                        matched_acc = None
                        matched_acc_num = None
                        
                        # First, try to match by procedure text in the listbox entry
                        for acc_entry in multiple_accessions:
                            # Extract accession number from entry
                            entry_acc_num = _extract_accession_number(acc_entry)
                            
                            # Skip if this accession NUMBER is already collected
                            if entry_acc_num in collected_acc_nums:
                                continue
                                
                            # Check if procedure is embedded in the entry (format: "ACC (PROC)")
                            if '(' in acc_entry and ')' in acc_entry:
                                entry_match = re.match(r'^([^(]+)\s*\(([^)]+)\)', acc_entry)
                                if entry_match:
                                    embedded_proc = entry_match.group(2).strip()
                                    # Check if embedded procedure matches current procedure (case-insensitive partial match)
                                    if (embedded_proc.upper() in procedure.upper() or 
                                        procedure.upper() in embedded_proc.upper()):
                                        matched_acc = acc_entry
                                        matched_acc_num = entry_acc_num
                                        break
                        
                        # Fall back: assign to first accession NUMBER not yet collected
                        if not matched_acc:
                            for acc_entry in multiple_accessions:
                                entry_acc_num = _extract_accession_number(acc_entry)
                                if entry_acc_num not in collected_acc_nums:
                                    matched_acc = acc_entry
                                    matched_acc_num = entry_acc_num
                                    break
                        
                        if matched_acc:
                            # Use the extracted accession number
                            acc_num = matched_acc_num if matched_acc_num else _extract_accession_number(matched_acc)
                            
                            self.multi_accession_data[matched_acc] = {
                                "procedure": procedure,
                                "study_type": study_type,
                                "rvu": rvu,
                                "patient_class": patient_class,
                                "accession_number": acc_num,  # Store parsed accession for recording
                            }
                            logger.info(f"Collected procedure for {acc_num}: {procedure} ({rvu} RVU)")
                        
                        # Update last seen procedure
                        self.multi_accession_last_procedure = procedure
            elif data_source != "Mosaic" and is_multiple_mode:
                # PowerScribe: Multiple accessions but list not loaded yet
                accession = "Multiple (loading...)"
                is_multi_accession_view = True  # Prevent single-study tracking for placeholder
            else:
                # SINGLE ACCESSION mode (PowerScribe) or Mosaic single/multiple handled above
                if data_source == "PowerScribe":
                    # PowerScribe: get from labelAccession
                    accession = elements.get("labelAccession", {}).get("text", "").strip()
                # For Mosaic, accession is already set above
                
                # Handle MULTI→SINGLE transition (PowerScribe only)
                if data_source == "PowerScribe" and self.multi_accession_mode:
                    if self.multi_accession_data:
                        # Check if the remaining single accession was in our multi-accession tracking
                        remaining_acc = accession.strip() if accession else ""
                        migrated_back = False
                        
                        if remaining_acc and len(self.multi_accession_data) > 1:
                            # Multiple accessions were tracked - one is continuing as single
                            # Find and migrate that one back, record the others
                            for entry, data in list(self.multi_accession_data.items()):
                                acc_num = data.get("accession_number") or _extract_accession_number(entry)
                                if acc_num == remaining_acc:
                                    # This accession continues - migrate back to single tracking
                                    # Don't restart timer - use the multi_accession_start_time
                                    self.tracker.active_studies[acc_num] = {
                                        "accession": acc_num,
                                        "procedure": data["procedure"],
                                        "study_type": data["study_type"],
                                        "rvu": data["rvu"],
                                        "patient_class": data.get("patient_class", ""),
                                        "start_time": self.multi_accession_start_time or datetime.now(),
                                        "last_seen": datetime.now(),
                                    }
                                    del self.multi_accession_data[entry]
                                    migrated_back = True
                                    logger.info(f"MIGRATED {acc_num} back to single-accession tracking (multi→single transition)")
                                    break
                            
                            # Record remaining accessions (the ones that were completed/removed)
                            if self.multi_accession_data:
                                logger.info(f"Recording {len(self.multi_accession_data)} completed accessions from multi→single transition")
                                self._record_multi_accession_study(datetime.now())
                        elif remaining_acc and len(self.multi_accession_data) == 1:
                            # Only one accession was in multi-mode, now single - just migrate back
                            entry, data = list(self.multi_accession_data.items())[0]
                            acc_num = data.get("accession_number") or _extract_accession_number(entry)
                            if acc_num == remaining_acc:
                                self.tracker.active_studies[acc_num] = {
                                    "accession": acc_num,
                                    "procedure": data["procedure"],
                                    "study_type": data["study_type"],
                                    "rvu": data["rvu"],
                                    "patient_class": data.get("patient_class", ""),
                                    "start_time": self.multi_accession_start_time or datetime.now(),
                                    "last_seen": datetime.now(),
                                }
                                migrated_back = True
                                logger.info(f"MIGRATED {acc_num} back to single-accession tracking (was only one in multi-mode)")
                            else:
                                # Different accession - record the old one
                                self._record_multi_accession_study(datetime.now())
                        else:
                            # No remaining accession visible or empty multi_accession_data
                            # Record whatever we have
                            self._record_multi_accession_study(datetime.now())
                    
                    # Reset multi-accession state
                    self.multi_accession_mode = False
                    self.multi_accession_data = {}
                    self.multi_accession_start_time = None
                    self.multi_accession_last_procedure = ""
            
            # Update state
            self.current_accession = accession
            # For Mosaic multi-accession (2+ studies), ensure procedure is always set
            if data_source == "Mosaic" and len(multiple_accessions) > 1 and not procedure:
                procedure = "Multiple studies"  # Ensure we have something to display
            self.current_procedure = procedure
            self.current_patient_class = patient_class
            self.current_multiple_accessions = multiple_accessions
            
            # Check if procedure is "n/a" (case-insensitive)
            is_na = procedure and procedure.strip().lower() in ["n/a", "na", "none", ""]
            
            # Determine study type and RVU for display
            # For Mosaic multiple accessions, the values should already be set above
            # Skip if already set for Mosaic multiple accessions
            if mosaic_multiple_mode and hasattr(self, 'current_study_type') and self.current_study_type:
                # Already set above for Mosaic multi-accession - keep it
                pass
            # Skip if already set for duplicate multi-accession
            elif is_multi_accession_view and not self.multi_accession_mode and hasattr(self, 'current_study_type') and self.current_study_type and self.current_study_type.startswith("Multiple"):
                # Already set for duplicate multi-accession - keep it
                pass
            elif self.multi_accession_mode and multiple_accessions:
                # Multi-accession mode display
                collected_count = len(self.multi_accession_data)
                total_count = len(multiple_accessions)
                
                if collected_count < total_count:
                    # Incomplete - show current procedure info but mark as incomplete
                    self.current_study_type = f"incomplete ({collected_count}/{total_count})"
                    self.current_study_rvu = sum(d["rvu"] for d in self.multi_accession_data.values())
                else:
                    # Complete - show "Multiple {modality}"
                    total_rvu = sum(d["rvu"] for d in self.multi_accession_data.values())
                    # Get modality from first study type
                    modalities = set()
                    for d in self.multi_accession_data.values():
                        st = d["study_type"]
                        if st:
                            # Extract modality (first word usually)
                            parts = st.split()
                            if parts:
                                modalities.add(parts[0])
                    modality = list(modalities)[0] if modalities else "Studies"
                    self.current_study_type = f"Multiple {modality}"
                    self.current_study_rvu = total_rvu
            elif procedure and not is_na:
                classification_rules = self.data_manager.data.get("classification_rules", {})
                direct_lookups = self.data_manager.data.get("direct_lookups", {})
                study_type, rvu = match_study_type(procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                self.current_study_type = study_type
                self.current_study_rvu = rvu
                logger.debug(f"Set current_study_type={study_type}, current_study_rvu={rvu}")
            else:
                self.current_study_type = ""
                self.current_study_rvu = 0.0
            
            self.update_debug_display()
        
            current_time = datetime.now()
            
            # IMPORTANT: If there's a current accession that's NOT yet in active_studies,
            # we need to add it BEFORE handling N/A. This prevents losing studies that were
            # briefly visible before the procedure changed to N/A.
            rvu_table = self.data_manager.data["rvu_table"]
            classification_rules = self.data_manager.data.get("classification_rules", {})
            direct_lookups = self.data_manager.data.get("direct_lookups", {})
            
            if accession and accession not in self.tracker.active_studies:
                # New study detected - add it before any completion logic
                # This ensures we don't lose studies that flash briefly before N/A
                ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicates", True)
                if not self.tracker.should_ignore(accession, ignore_duplicates, self.data_manager):
                    # Get procedure for this study
                    # Priority: current procedure > pending studies cache > current_procedure
                    study_procedure = None
                    pending_patient_class = self.current_patient_class
                    
                    if not is_na and procedure:
                        study_procedure = procedure
                    else:
                        # Check pending studies cache from worker thread
                        with self._ps_lock:
                            if accession in self._pending_studies:
                                pending = self._pending_studies[accession]
                                study_procedure = pending.get('procedure', '')
                                if pending.get('patient_class'):
                                    pending_patient_class = pending.get('patient_class')
                                logger.info(f"Using cached pending study data for {accession}: {study_procedure}")
                    
                    if study_procedure and study_procedure.lower() not in ["n/a", "na", "no report", ""]:
                        # Check if study is already recorded before adding from pending cache
                        if not self.tracker.is_already_recorded(accession, self.data_manager):
                            logger.info(f"Adding study before N/A check: {accession} - {study_procedure}")
                            self.tracker.add_study(accession, study_procedure, current_time, 
                                                 rvu_table, classification_rules, direct_lookups, 
                                                 pending_patient_class)
                            # NOTE: Do NOT call mark_seen here - study is only being TRACKED, not RECORDED
                            # seen_accessions should only contain studies that have been RECORDED to database
                        else:
                            logger.debug(f"Skipping adding {accession} from pending cache - already recorded")
                        # Remove from pending after processing (whether added or skipped)
                        with self._ps_lock:
                            if accession in self._pending_studies:
                                del self._pending_studies[accession]
            
            # If procedure changed to "n/a", complete multi-accession study or all active studies
            if is_na:
                # First, handle multi-accession study completion
                if self.multi_accession_mode and self.multi_accession_data:
                    self._record_multi_accession_study(current_time)
                    
                    # Reset multi-accession tracking
                    self.multi_accession_mode = False
                    self.multi_accession_data = {}
                    self.multi_accession_start_time = None
                    self.multi_accession_last_procedure = ""
                
                # Handle regular single-accession studies when N/A
                if self.tracker.active_studies:
                    logger.info("Procedure changed to N/A - completing all active studies")
                    # Mark all active studies as completed immediately
                    for acc, study in list(self.tracker.active_studies.items()):
                        duration = (current_time - study["start_time"]).total_seconds()
                        if duration >= self.tracker.min_seconds:
                            completed_study = study.copy()
                            completed_study["end_time"] = current_time
                            completed_study["duration"] = duration
                            study_record = {
                                "accession": completed_study["accession"],
                                "procedure": completed_study["procedure"],
                                "patient_class": completed_study.get("patient_class", ""),
                                "study_type": completed_study["study_type"],
                                "rvu": completed_study["rvu"],
                                "time_performed": completed_study["start_time"].isoformat(),
                                "time_finished": completed_study["end_time"].isoformat(),
                                "duration_seconds": completed_study["duration"],
                            }
                            self._record_or_update_study(study_record)
                            # Clear redo buffer when new study is added
                            self.last_undone_study = None
                            self.undo_used = False
                            self.undo_btn.config(state=tk.NORMAL, text="Undo")
                            
                            # Update mini window button if it exists
                            if self.mini_window and self.mini_window.undo_btn:
                                self.mini_window.undo_btn.config(text="U")
                        else:
                            logger.debug(f"Skipping short study: {acc} ({duration:.1f}s < {self.tracker.min_seconds}s)")
                    # Clear all active studies
                    self.tracker.active_studies.clear()
                    self.update_display()
                return  # Return after handling N/A case - don't process normal study tracking
        
            # Skip normal study tracking when viewing a multi-accession study (PowerScribe only)
            # For Mosaic, we track each accession separately, so we need to check completion
            # Also skip if we're viewing a multi-accession that we're ignoring as duplicate (PowerScribe only)
            if (self.multi_accession_mode or is_multi_accession_view) and data_source != "Mosaic":
                return
        
            # Check for completed studies FIRST (before checking if we should ignore)
            # This handles studies that have disappeared
            # For Mosaic multi-accession, we need to check all accessions, not just the current one
            logger.debug(f"Completion check: data_source={data_source}, accession='{accession}', multiple_accessions={multiple_accessions}, active_studies={list(self.tracker.active_studies.keys())}")
            if data_source == "Mosaic":
                if multiple_accessions:
                    # For Mosaic multi-accession, check completion for all accessions
                    # Extract all accession numbers from multiple_accessions
                    all_current_accessions = set()
                    for acc_entry in multiple_accessions:
                        if '(' in acc_entry and ')' in acc_entry:
                            # Format: "ACC (PROC)"
                            acc_match = re.match(r'^([^(]+)', acc_entry)
                            if acc_match:
                                all_current_accessions.add(acc_match.group(1).strip())
                        else:
                            all_current_accessions.add(acc_entry)
                    
                    # Update last_seen for all currently visible Mosaic accessions
                    for acc in all_current_accessions:
                        if acc in self.tracker.active_studies:
                            self.tracker.active_studies[acc]["last_seen"] = current_time
                    
                    # Check completion - any active Mosaic study not in the current accessions list should be completed
                    completed = []
                    for acc, study in list(self.tracker.active_studies.items()):
                        # Only check Mosaic studies (patient_class == "Unknown")
                        if study.get('patient_class') == 'Unknown' and acc not in all_current_accessions:
                            # This accession is no longer visible - mark as completed immediately
                            # Use current_time as end_time since study just disappeared
                            duration = (current_time - study["start_time"]).total_seconds()
                            if duration >= self.tracker.min_seconds:
                                completed_study = study.copy()
                                completed_study["end_time"] = current_time
                                completed_study["duration"] = duration
                                completed.append(completed_study)
                                logger.info(f"Completed Mosaic study: {acc} - {study['study_type']} ({duration:.1f}s)")
                                # Remove from active studies
                                del self.tracker.active_studies[acc]
                elif not accession:
                    # Mosaic but no multiple_accessions and no accession - all active Mosaic studies should be completed
                    # NOTE: Don't filter by patient_class == 'Unknown' because Clario may have updated it
                    # Instead, complete ALL active studies when no accession is visible (they must have closed)
                    completed = []
                    for acc, study in list(self.tracker.active_studies.items()):
                        # No accessions visible - complete immediately
                        # Use current_time as end_time since study just disappeared
                        duration = (current_time - study["start_time"]).total_seconds()
                        if duration >= self.tracker.min_seconds:
                            completed_study = study.copy()
                            completed_study["end_time"] = current_time
                            completed_study["duration"] = duration
                            completed.append(completed_study)
                            logger.info(f"Completed Mosaic study (no accessions visible): {acc} - {study['study_type']} ({duration:.1f}s)")
                            # Remove from active studies
                            del self.tracker.active_studies[acc]
                        else:
                            logger.debug(f"Skipping short Mosaic study: {acc} ({duration:.1f}s < {self.tracker.min_seconds}s)")
                    
                    # Only log the check message if we actually have studies to check
                    if self.tracker.active_studies:
                        logger.debug(f"Mosaic: no accession visible - checking {len(self.tracker.active_studies)} active studies for completion")
                else:
                    # Single Mosaic accession - use normal completion check
                    logger.debug(f"Calling check_completed for single Mosaic: accession='{accession}'")
                    completed = self.tracker.check_completed(current_time, accession)
            else:
                # Normal completion check (PowerScribe or single Mosaic accession)
                logger.debug(f"Calling check_completed for PowerScribe/single: accession='{accession}'")
                completed = self.tracker.check_completed(current_time, accession)
            
            # Only log if we actually found completed studies
            if completed:
                logger.info(f"Processing {len(completed)} completed studies from check_completed")
            else:
                logger.debug(f"Processing {len(completed)} completed studies from check_completed")
            for study in completed:
                logger.info(f"Recording completed study: {study['accession']} - {study.get('study_type', 'Unknown')}")
                study_record = {
                    "accession": study["accession"],
                    "procedure": study["procedure"],
                    "patient_class": study.get("patient_class", ""),
                    "study_type": study["study_type"],
                    "rvu": study["rvu"],
                    "time_performed": study["start_time"].isoformat(),  # Time study was started
                    "time_finished": study["end_time"].isoformat(),    # Time study was finished
                    "duration_seconds": study["duration"],              # Time taken to finish
                }
                self._record_or_update_study(study_record)
                # Clear redo buffer when new study is added or updated
                self.last_undone_study = None
                self.undo_used = False
                self.undo_btn.config(state=tk.NORMAL, text="Undo")
                
                # Update mini window button if it exists
                if self.mini_window and self.mini_window.undo_btn:
                    self.mini_window.undo_btn.config(text="U")
                    
                self.update_display()
            
            # Now handle current study
            if not accession:
                # No current study - check if we have active Mosaic studies that should be completed
                if data_source == "Mosaic" and self.tracker.active_studies:
                    # All active Mosaic studies should be completed since no study is visible
                    current_time_check = datetime.now()
                    for acc, study in list(self.tracker.active_studies.items()):
                        # Only complete Mosaic studies (patient_class == "Unknown")
                        if study.get('patient_class') == 'Unknown':
                            # No accession visible - complete immediately
                            # Use current_time as end_time since study just disappeared
                            duration = (current_time_check - study["start_time"]).total_seconds()
                            if duration >= self.tracker.min_seconds:
                                completed_study = study.copy()
                                completed_study["end_time"] = current_time_check
                                completed_study["duration"] = duration
                                study_record = {
                                    "accession": completed_study["accession"],
                                    "procedure": completed_study["procedure"],
                                    "patient_class": completed_study.get("patient_class", ""),
                                    "study_type": completed_study["study_type"],
                                    "rvu": completed_study["rvu"],
                                    "time_performed": completed_study["start_time"].isoformat(),
                                    "time_finished": completed_study["end_time"].isoformat(),
                                    "duration_seconds": completed_study["duration"],
                                }
                                self._record_or_update_study(study_record)
                                # Clear redo buffer when new study is added
                                self.last_undone_study = None
                                self.undo_used = False
                                self.undo_btn.config(state=tk.NORMAL, text="Undo")
                                
                                # Update mini window button if it exists
                                if self.mini_window and self.mini_window.undo_btn:
                                    self.mini_window.undo_btn.config(text="U")
                                    
                                del self.tracker.active_studies[acc]
                    if self.tracker.active_studies:
                        self.update_display()
                # No current study - all active studies should be checked for completion
                # This is already handled above, so just return
                return
            
            # Check if should ignore (only ignore if already completed in this shift)
            ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
            
            # Check if this accession should be ignored (already completed or part of multi-accession)
            if self.tracker.should_ignore(accession, ignore_duplicates, self.data_manager):
                logger.debug(f"Skipping tracking of accession {accession} - already recorded")
                return
            
            # Get classification rules, direct lookups, and RVU table for matching
            classification_rules = self.data_manager.data.get("classification_rules", {})
            direct_lookups = self.data_manager.data.get("direct_lookups", {})
            rvu_table = self.data_manager.data["rvu_table"]
            
            # If study is already active, update it (don't ignore)
            # For Mosaic multi-accession, we handle last_seen updates above, so skip here
            if accession in self.tracker.active_studies:
                if data_source != "Mosaic" or not multiple_accessions:
                    # Normal update for PowerScribe or single Mosaic accession
                    # Update with current patient class (may be from Clario cache)
                    self.tracker.add_study(accession, procedure, current_time, rvu_table, classification_rules, direct_lookups, self.current_patient_class)
                    logger.debug(f"Updated existing study: {accession} with patient_class: {self.current_patient_class}")
                # For Mosaic multi-accession, last_seen is updated above, so just return
                # BUT: Don't return here - we still need to check for completion of OTHER studies
                # The completion check happens above, so we can return now
                return
            
            # Allow study to be tracked again even if previously seen (as long as it wasn't part of multi-accession)
            # When it completes, _record_or_update_study will update existing record with maximum duration
            # NOTE: Do NOT add to seen_accessions here - that should only happen when study is RECORDED
            # Adding it here would cause "already recorded" to show for NEW studies that haven't been recorded yet
            
            # Add or update study tracking (allows reopening of previously seen studies)
            self.tracker.add_study(accession, procedure, current_time, rvu_table, classification_rules, direct_lookups, self.current_patient_class)
            
            if self.is_running:
                self.root.title("RVU Counter - Running")
            
        except Exception as e:
            logger.error(f"Error refreshing data: {e}", exc_info=True)
            self.root.title(f"RVU Counter - Error: {str(e)[:30]}")
            # Clear debug on error
            self.current_accession = ""
            self.current_procedure = ""
            self.current_patient_class = ""
            self.current_study_type = ""
            self.update_debug_display()
    
    def update_shift_start_label(self):
        """Update the shift start time label."""
        if self.shift_start:
            # Format: "Started: HH:MM am/pm"
            time_str = self.shift_start.strftime("%I:%M %p").lower()
            self.shift_start_label.config(text=f"Started: {time_str}")
        else:
            self.shift_start_label.config(text="")
    
    def start_shift(self):
        """Start a new shift."""
        if self.is_running:
            # Stop current shift - archive it immediately
            self.is_running = False
            
            # Determine shift end time: use last study time if there's been a significant gap
            current_time = datetime.now()
            records = self.data_manager.data["current_shift"].get("records", [])
            
            shift_end_time = current_time  # Default to current time
            
            if records:
                # Find the most recent study's time_finished
                try:
                    last_study_times = []
                    for r in records:
                        if r.get("time_finished"):
                            last_study_times.append(datetime.fromisoformat(r["time_finished"]))
                    
                    if last_study_times:
                        last_study_time = max(last_study_times)
                        time_since_last_study = (current_time - last_study_time).total_seconds() / 60  # minutes
                        
                        # If last study was more than 30 minutes ago, use that as shift end
                        if time_since_last_study > 30:
                            shift_end_time = last_study_time
                            logger.info(f"Using last study time as shift end ({time_since_last_study:.1f} min gap): {last_study_time}")
                        else:
                            logger.info(f"Using current time as shift end (last study {time_since_last_study:.1f} min ago)")
                except Exception as e:
                    logger.error(f"Error determining shift end time: {e}")
                    # Fall back to current time
            
            self.data_manager.data["current_shift"]["shift_end"] = shift_end_time.isoformat()
            # Archive the shift to historical shifts
            self.data_manager.end_current_shift()
            # Clear current_shift completely so new studies are truly temporary
            self.data_manager.data["current_shift"]["shift_start"] = None
            self.data_manager.data["current_shift"]["shift_end"] = None
            self.data_manager.data["current_shift"]["records"] = []
            
            self.start_btn.config(text="Start Shift")
            self.root.title("RVU Counter - Stopped")
            self.shift_start = None
            self.effective_shift_start = None
            self.projected_shift_end = None
            self.update_shift_start_label()
            self.update_recent_studies_label()
            # Clear the recent studies display (data is preserved in archived shift)
            for widget in self.study_widgets:
                widget.destroy()
            self.study_widgets.clear()
            self.data_manager.save()
            logger.info("Shift stopped and archived")
            
            # Trigger cloud backup on shift end (if enabled)
            self._perform_shift_end_backup()
            
            # Recalculate typical shift times now that we have new data
            self._calculate_typical_shift_times()
            # Hide pace car when no shift is active
            self.pace_car_frame.pack_forget()
            # Update counters to zero but don't rebuild recent studies list
            self._update_counters_only()
        else:
            # Check for temporary studies (studies recorded without an active shift)
            temp_records = self.data_manager.data["current_shift"].get("records", [])
            has_no_shift = not self.data_manager.data["current_shift"].get("shift_start")
            
            keep_temp_records = False
            if temp_records and has_no_shift:
                # Ask user what to do with temporary studies
                study_count = len(temp_records)
                total_rvu = sum(r.get("rvu", 0) for r in temp_records)
                
                # Create custom dialog with Yes/No/Cancel
                result = messagebox.askyesnocancel(
                    "Temporary Studies Found",
                    f"You have {study_count} temporary studies ({total_rvu:.1f} RVU) recorded without a shift.\n\n"
                    "Would you like to add them to the new shift?\n\n"
                    "• Yes - Add studies to the new shift\n"
                    "• No - Discard temporary studies\n"
                    "• Cancel - Don't start shift",
                    parent=self.root
                )
                
                if result is None:
                    # Cancel - abort, don't start shift
                    logger.info("Shift start cancelled by user")
                    return
                elif result:
                    # Yes - keep the records
                    keep_temp_records = True
                    logger.info(f"User chose to add {study_count} temporary studies to new shift")
                else:
                    # No - discard records
                    keep_temp_records = False
                    logger.info(f"User chose to discard {study_count} temporary studies")
            
            # End previous shift if it exists (shouldn't happen if has_no_shift is True)
            if self.data_manager.data["current_shift"].get("shift_start"):
                self.data_manager.end_current_shift()
            
            # Determine shift start time
            # If keeping temp records with >5 RVU, retroactively extend shift to earliest study time
            retroactive_shift_start = None
            if keep_temp_records and total_rvu > 5.0:
                # Find the earliest time_performed from temporary records
                earliest_time = None
                for record in temp_records:
                    time_performed_str = record.get("time_performed")
                    if time_performed_str:
                        try:
                            time_performed = datetime.fromisoformat(time_performed_str)
                            if earliest_time is None or time_performed < earliest_time:
                                earliest_time = time_performed
                        except (ValueError, TypeError):
                            continue
                
                if earliest_time:
                    # Set shift start to earliest study time (rounded down to nearest minute for cleanliness)
                    retroactive_shift_start = earliest_time.replace(second=0, microsecond=0)
                    logger.info(f"Retroactively extending shift start to {retroactive_shift_start} based on {total_rvu:.1f} RVU in temporary studies")
            
            # Start new shift - use retroactive time if available, otherwise use current time
            if retroactive_shift_start:
                self.shift_start = retroactive_shift_start
            else:
                self.shift_start = datetime.now()
            
            # Calculate effective shift start (rounded to hour if within 15 min)
            minutes_into_hour = self.shift_start.minute
            if minutes_into_hour <= 15:
                # Round down to the hour
                self.effective_shift_start = self.shift_start.replace(minute=0, second=0, microsecond=0)
            else:
                # Use actual start time
                self.effective_shift_start = self.shift_start
            
            # Calculate projected shift end based on shift length setting
            shift_length = self.data_manager.data["settings"].get("shift_length_hours", 9)
            self.projected_shift_end = self.effective_shift_start + timedelta(hours=shift_length)
            
            self.data_manager.data["current_shift"]["shift_start"] = self.shift_start.isoformat()
            self.data_manager.data["current_shift"]["effective_shift_start"] = self.effective_shift_start.isoformat()
            self.data_manager.data["current_shift"]["projected_shift_end"] = self.projected_shift_end.isoformat()
            self.data_manager.data["current_shift"]["shift_end"] = None
            
            # Handle temporary records based on user choice
            if keep_temp_records:
                # Keep existing records, mark their accessions as seen
                # Records already have their correct time_performed values, so they'll be placed at the right times
                for record in temp_records:
                    self.tracker.seen_accessions.add(record.get("accession", ""))
            else:
                # Clear records
                self.data_manager.data["current_shift"]["records"] = []
            
            self.tracker = StudyTracker(
                min_seconds=self.data_manager.data["settings"]["min_study_seconds"]
            )
            if keep_temp_records:
                # Restore seen accessions after tracker recreation
                for record in temp_records:
                    self.tracker.seen_accessions.add(record.get("accession", ""))
            else:
                self.tracker.seen_accessions.clear()
            
            self.is_running = True
            self.start_btn.config(text="Stop Shift")
            self.root.title("RVU Counter - Running")
            # Force widget rebuild by setting last_record_count to -1 (different from 0)
            self.last_record_count = -1
            self.update_shift_start_label()
            self.update_recent_studies_label()
            # Show pace car if enabled in settings
            if self.data_manager.data["settings"].get("show_pace_car", False):
                self.pace_car_frame.pack(fill=tk.X, pady=(0, 2), after=self.counters_frame)
            self.data_manager.save()
            logger.info(f"Shift started at {self.shift_start}")
            
            # Reset inactivity tracker on shift start
            self.last_activity_time = datetime.now()
            self._auto_end_prompt_shown = False
            
            self.update_display()
    
    def undo_last(self):
        """Toggle between undo and redo for the last study."""
        records = self.data_manager.data["current_shift"]["records"]
        
        if self.undo_used and self.last_undone_study:
            # Redo - restore the last undone study
            records.append(self.last_undone_study)
            self.data_manager.save()
            logger.info(f"Redid study: {self.last_undone_study['accession']}")
            
            # Clear redo state
            self.last_undone_study = None
            self.undo_used = False
            self.undo_btn.config(text="Undo")
            
            # Update mini window button if it exists
            if self.mini_window and self.mini_window.undo_btn:
                self.mini_window.undo_btn.config(text="U")
            
        elif records and not self.undo_used:
            # Undo - remove the last study
            removed = records.pop()
            self.last_undone_study = removed
            self.data_manager.save()
            logger.info(f"Undid study: {removed['accession']}")
            
            # Set to redo state
            self.undo_used = True
            self.undo_btn.config(text="Redo")
            
            # Update mini window button if it exists
            if self.mini_window and self.mini_window.undo_btn:
                self.mini_window.undo_btn.config(text="R")
        
        self.update_display()
    
    def delete_study_by_index(self, index: int):
        """Delete study by index from records."""
        try:
            logger.info(f"delete_study_by_index called with index: {index}")
            records = self.data_manager.data["current_shift"]["records"]
            logger.info(f"Current records count: {len(records)}")
            
            if 0 <= index < len(records):
                removed = records[index]
                accession = removed.get('accession', '')
                logger.info(f"Attempting to delete study: {accession} at index {index}")
                
                # Delete from database first
                deleted_from_db = False
                if 'id' in removed and removed['id']:
                    try:
                        self.data_manager.db.delete_record(removed['id'])
                        deleted_from_db = True
                        logger.info(f"Deleted study from database: {accession} (ID: {removed['id']})")
                    except Exception as e:
                        logger.error(f"Error deleting study from database: {e}", exc_info=True)
                else:
                    # Record doesn't have ID yet - delete by accession if we have a current shift
                    logger.info(f"Record has no ID, trying to find by accession: {accession}")
                    current_shift = self.data_manager.db.get_current_shift()
                    if current_shift:
                        try:
                            db_record = self.data_manager.db.find_record_by_accession(
                                current_shift['id'], accession
                            )
                            if db_record:
                                self.data_manager.db.delete_record(db_record['id'])
                                deleted_from_db = True
                                logger.info(f"Deleted study from database by accession: {accession} (ID: {db_record['id']})")
                            else:
                                logger.warning(f"Could not find record in database for accession: {accession}, will delete from memory only")
                        except Exception as e:
                            logger.error(f"Error deleting study from database by accession: {e}", exc_info=True)
                    else:
                        logger.warning("No current shift found in database, will delete from memory only")
                
                # Remove from memory
                records.pop(index)
                logger.info(f"Removed study from memory: {accession}, remaining records: {len(records)}")
                
                # Remove from seen_accessions to allow retracking if reopened
                if accession and accession in self.tracker.seen_accessions:
                    self.tracker.seen_accessions.remove(accession)
                    logger.info(f"Removed {accession} from seen_accessions - can be tracked again if reopened")
                
                # Remove from active_studies if currently being tracked
                if accession and accession in self.tracker.active_studies:
                    del self.tracker.active_studies[accession]
                    logger.info(f"Removed {accession} from active_studies tracking")
                
                # Remove from pending_studies cache
                with self._ps_lock:
                    if accession and accession in self._pending_studies:
                        del self._pending_studies[accession]
                        logger.info(f"Removed {accession} from pending_studies cache")
                
                # Save to sync memory changes to database
                self.data_manager.save()
                logger.info(f"Saved changes after deletion")
                
                # Reload data from database to ensure consistency between DB and memory
                if deleted_from_db:
                    try:
                        # Reload records from database
                        self.data_manager.records_data = self.data_manager._load_records_from_db()
                        # Update current_shift in main data structure
                        self.data_manager.data["current_shift"] = self.data_manager.records_data.get("current_shift", {
                            "shift_start": None,
                            "shift_end": None,
                            "records": []
                        })
                        self.data_manager.data["shifts"] = self.data_manager.records_data.get("shifts", [])
                        logger.info(f"Reloaded data from DB, current records count: {len(self.data_manager.data['current_shift']['records'])}")
                    except Exception as e:
                        logger.error(f"Error reloading data from database: {e}", exc_info=True)
                
                # Manually destroy all study widgets immediately
                for widget in list(self.study_widgets):
                    try:
                        widget.destroy()
                    except:
                        pass
                self.study_widgets.clear()
                
                # Also clear ALL children from scrollable frame directly
                for child in list(self.studies_scrollable_frame.winfo_children()):
                    try:
                        child.destroy()
                    except:
                        pass
                
                # Clear time labels too
                if hasattr(self, 'time_labels'):
                    self.time_labels.clear()
                
                logger.info("Manually destroyed all study widgets and cleared scrollable frame")
                
                # Force immediate UI refresh before rebuilding
                self.root.update_idletasks()
                self.root.update()
                
                # Force a rebuild of the recent studies list
                self.last_record_count = -1
                self.update_display()
                
                # Force multiple UI updates to ensure refresh
                self.root.update_idletasks()
                self.root.update()
                logger.info(f"UI updated after deletion, new record count: {len(self.data_manager.data['current_shift']['records'])}")
            else:
                logger.warning(f"Invalid index for deletion: {index} (records count: {len(records)})")
        except Exception as e:
            logger.error(f"Error in delete_study_by_index: {e}", exc_info=True)
    
    def _get_hour_key(self, dt: datetime) -> str:
        """Convert datetime hour to rate lookup key like '2am', '12pm'."""
        hour = dt.hour
        if hour == 0:
            return "12am"
        elif hour < 12:
            return f"{hour}am"
        elif hour == 12:
            return "12pm"
        else:
            return f"{hour - 12}pm"
    
    def _is_weekend(self, dt: datetime) -> bool:
        """Check if date is weekend (Saturday=5, Sunday=6)."""
        return dt.weekday() >= 5
    
    def _get_compensation_rate(self, dt: datetime) -> float:
        """Get compensation rate per RVU for a given datetime."""
        rates = self.data_manager.data.get("compensation_rates", {})
        if not rates:
            logger.warning("No compensation_rates found in data")
            return 0.0
        
        role = self.data_manager.data["settings"].get("role", "Partner").lower()
        # Map role to key in rates
        role_key = "partner" if role == "partner" else "assoc"
        day_type = "weekend" if self._is_weekend(dt) else "weekday"
        hour_key = self._get_hour_key(dt)
        
        try:
            rate = rates[day_type][role_key][hour_key]
            return rate
        except KeyError as e:
            logger.warning(f"KeyError getting rate: {e} - keys: {day_type}/{role_key}/{hour_key}")
            return 0.0
    
    def _calculate_study_compensation(self, record: dict) -> float:
        """Calculate compensation for a single study based on when it was finished."""
        try:
            time_finished = datetime.fromisoformat(record["time_finished"])
            rate = self._get_compensation_rate(time_finished)
            return record["rvu"] * rate
        except (KeyError, ValueError):
            return 0.0
    
    def _calculate_projected_compensation(self, start_time: datetime, end_time: datetime, rvu_rate_per_hour: float) -> float:
        """Calculate projected compensation for remaining shift hours considering hourly rate changes."""
        total_comp = 0.0
        current = start_time
        
        while current < end_time:
            # Calculate how much of this hour is within our range
            hour_start = current.replace(minute=0, second=0, microsecond=0)
            hour_end = hour_start + timedelta(hours=1)
            
            # Clip to our actual range
            effective_start = max(current, hour_start)
            effective_end = min(end_time, hour_end)
            
            # Calculate fraction of hour
            fraction_of_hour = (effective_end - effective_start).total_seconds() / 3600
            
            if fraction_of_hour > 0:
                # Get the rate for this hour
                rate = self._get_compensation_rate(hour_start)
                
                # Calculate RVU for this fraction of hour
                rvu_this_period = rvu_rate_per_hour * fraction_of_hour
                
                # Calculate compensation
                comp_this_period = rvu_this_period * rate
                total_comp += comp_this_period
            
            # Move to next hour
            current = hour_end
        
        return total_comp
    
    def _handle_inactivity_prompt(self):
        """Prompt user when no activity for 1 hour."""
        result = messagebox.askyesno(
            "Inactive Shift",
            "No new studies have been detected for over an hour.\n\n"
            "Did your shift end?\n\n"
            "• Yes - End shift at the time of the last study\n"
            "• No - Keep the shift running",
            parent=self.root
        )
        
        if result:
            logger.info("User confirmed shift ended due to inactivity")
            
            # Find the most recent study's time_finished
            records = self.data_manager.data["current_shift"].get("records", [])
            shift_end_time = datetime.now()
            
            if records:
                try:
                    last_study_times = []
                    for r in records:
                        if r.get("time_finished"):
                            last_study_times.append(datetime.fromisoformat(r["time_finished"]))
                    
                    if last_study_times:
                        shift_end_time = max(last_study_times)
                        logger.info(f"Setting inactive shift end to last study time: {shift_end_time}")
                except Exception as e:
                    logger.error(f"Error determining inactivity end time: {e}")
            
            # Stop the shift using the calculated end time
            self.is_running = False
            self.data_manager.data["current_shift"]["shift_end"] = shift_end_time.isoformat()
            self.data_manager.end_current_shift()
            
            # Cleanup state
            self.data_manager.data["current_shift"]["shift_start"] = None
            self.data_manager.data["current_shift"]["shift_end"] = None
            self.data_manager.data["current_shift"]["records"] = []
            
            self.start_btn.config(text="Start Shift")
            self.root.title("RVU Counter - Stopped")
            self.shift_start = None
            self.effective_shift_start = None
            self.projected_shift_end = None
            self.update_shift_start_label()
            self.update_recent_studies_label()
            for widget in self.study_widgets:
                widget.destroy()
            self.study_widgets.clear()
            self.data_manager.save()
            self.pace_car_frame.pack_forget()
            self._update_counters_only()
            self.update_display()
        else:
            logger.info("User chose to keep shift running after inactivity prompt")
            # Reset the timer
            self.last_activity_time = datetime.now()
            self._auto_end_prompt_shown = False

    def calculate_stats(self) -> dict:
        """Calculate statistics."""
        if not self.shift_start:
            return {
                "total": 0.0,
                "avg_per_hour": 0.0,
                "last_hour": 0.0,
                "last_full_hour": 0.0,
                "last_full_hour_range": "",
                "projected": 0.0,
                "projected_shift": 0.0,
                "comp_total": 0.0,
                "comp_avg": 0.0,
                "comp_last_hour": 0.0,
                "comp_last_full_hour": 0.0,
                "comp_projected": 0.0,
                "comp_projected_shift": 0.0,
            }
        
        records = self.data_manager.data["current_shift"]["records"]
        current_time = datetime.now()
        
        # Total RVU and compensation
        total_rvu = sum(r["rvu"] for r in records)
        total_comp = sum(self._calculate_study_compensation(r) for r in records)
        
        # Average per hour
        hours_elapsed = (current_time - self.shift_start).total_seconds() / 3600
        avg_per_hour = total_rvu / hours_elapsed if hours_elapsed > 0 else 0.0
        avg_comp_per_hour = total_comp / hours_elapsed if hours_elapsed > 0 else 0.0
        
        # Last hour - filter records and calculate both RVU and compensation
        one_hour_ago = current_time - timedelta(hours=1)
        last_hour_records = [r for r in records if datetime.fromisoformat(r["time_finished"]) >= one_hour_ago]
        last_hour_rvu = sum(r["rvu"] for r in last_hour_records)
        last_hour_comp = sum(self._calculate_study_compensation(r) for r in last_hour_records)
        
        # Last full hour (e.g., 2am to 3am)
        current_hour_start = current_time.replace(minute=0, second=0, microsecond=0)
        last_full_hour_start = current_hour_start - timedelta(hours=1)
        last_full_hour_end = current_hour_start
        
        last_full_hour_records = [r for r in records 
                                   if last_full_hour_start <= datetime.fromisoformat(r["time_finished"]) < last_full_hour_end]
        last_full_hour_rvu = sum(r["rvu"] for r in last_full_hour_records)
        last_full_hour_comp = sum(self._calculate_study_compensation(r) for r in last_full_hour_records)
        last_full_hour_range = f"{self._format_hour_label(last_full_hour_start)}-{self._format_hour_label(last_full_hour_end)}"
        
        # Projected for current hour - use current hour's rate for projection
        current_hour_records = [r for r in records if datetime.fromisoformat(r["time_finished"]) >= current_hour_start]
        current_hour_rvu = sum(r["rvu"] for r in current_hour_records)
        current_hour_comp = sum(self._calculate_study_compensation(r) for r in current_hour_records)
        
        minutes_into_hour = (current_time - current_hour_start).total_seconds() / 60
        if minutes_into_hour > 0:
            projected = (current_hour_rvu / minutes_into_hour) * 60
            projected_comp = (current_hour_comp / minutes_into_hour) * 60
        else:
            projected = 0.0
            projected_comp = 0.0
        
        # Projected shift total - extrapolate based on RVU rate and remaining time
        projected_shift_rvu = total_rvu
        projected_shift_comp = total_comp
        
        if self.effective_shift_start and self.projected_shift_end:
            # Calculate time remaining in shift
            time_remaining = (self.projected_shift_end - current_time).total_seconds()
            
            if time_remaining > 0 and hours_elapsed > 0:
                # Calculate RVU rate per hour
                rvu_rate_per_hour = avg_per_hour
                hours_remaining = time_remaining / 3600
                
                # Project additional RVU for remaining time
                projected_additional_rvu = rvu_rate_per_hour * hours_remaining
                projected_shift_rvu = total_rvu + projected_additional_rvu
                
                # Calculate projected compensation for remaining hours
                # Consider hourly rate changes throughout remaining shift
                projected_additional_comp = self._calculate_projected_compensation(
                    current_time, 
                    self.projected_shift_end, 
                    rvu_rate_per_hour
                )
                projected_shift_comp = total_comp + projected_additional_comp
        
        return {
            "total": total_rvu,
            "avg_per_hour": avg_per_hour,
            "last_hour": last_hour_rvu,
            "last_full_hour": last_full_hour_rvu,
            "last_full_hour_range": last_full_hour_range,
            "projected": projected,
            "projected_shift": projected_shift_rvu,
            "comp_total": total_comp,
            "comp_avg": avg_comp_per_hour,
            "comp_last_hour": last_hour_comp,
            "comp_last_full_hour": last_full_hour_comp,
            "comp_projected": projected_comp,
            "comp_projected_shift": projected_shift_comp,
        }
    
    def create_tooltip(self, widget, text):
        """Create a tooltip for a widget."""
        def on_enter(event):
            tooltip = tk.Toplevel()
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
            label = ttk.Label(tooltip, text=text, background="#ffffe0", relief=tk.SOLID, borderwidth=1, font=("Consolas", 8))
            label.pack()
            widget.tooltip = tooltip
        
        def on_leave(event):
            if hasattr(widget, 'tooltip'):
                widget.tooltip.destroy()
                del widget.tooltip
        
        widget.bind('<Enter>', on_enter)
        widget.bind('<Leave>', on_leave)
    
    def update_recent_studies_label(self):
        """Update the Recent Studies label based on shift status."""
        # Safety check: ensure recent_frame exists (might not be created yet during UI initialization)
        if not hasattr(self, 'recent_frame'):
            return
        
        # Check both in-memory state and data file to ensure consistency
        current_shift = self.data_manager.data.get("current_shift", {})
        shift_start_str = current_shift.get("shift_start")
        shift_end_str = current_shift.get("shift_end")
        
        # Determine if we're in an active shift:
        # 1. In-memory state says we're running AND have a shift_start, OR
        # 2. Data file has shift_start but no shift_end (active shift)
        is_active_shift = (self.is_running and self.shift_start) or (shift_start_str and not shift_end_str)
        
        # Get default foreground color from theme
        default_fg = self.theme_colors.get("fg", "black") if hasattr(self, 'theme_colors') else "black"
        
        if is_active_shift:
            # Count recent studies
            recent_count = len(current_shift.get("records", []))
            # Normal text color
            self.recent_frame.config(text=f"Recent Studies ({recent_count})", fg=default_fg)
        else:
            # Red text to indicate no active shift
            self.recent_frame.config(text="Temporary Recent - No shift started", fg="red")
    
    def _update_counters_only(self):
        """Update just the counter displays to zero (used when shift ends)."""
        self.update_recent_studies_label()
        settings = self.data_manager.data["settings"]
        
        # Set all counters to 0
        if settings.get("show_total", True):
            self.total_label.config(text="0.0")
            self.total_comp_label.config(text="")
        if settings.get("show_avg", True):
            self.avg_label.config(text="0.0")
            self.avg_comp_label.config(text="")
        if settings.get("show_last_hour", True):
            self.last_hour_label.config(text="0.0")
            self.last_hour_comp_label.config(text="")
        if settings.get("show_last_full_hour", True):
            self.last_full_hour_label.config(text="0.0")
            self.last_full_hour_range_label.config(text="")
            self.last_full_hour_label_text.config(text="hour:")
            self.last_full_hour_comp_label.config(text="")
        if settings.get("show_projected", True):
            self.projected_label.config(text="0.0")
            self.projected_comp_label.config(text="")
        if settings.get("show_projected_shift", True):
            self.projected_shift_label.config(text="0.0")
            self.projected_shift_comp_label.config(text="")
    
    def update_display(self):
        """Update the display with current statistics."""
        # Update recent studies label based on shift status
        self.update_recent_studies_label()
        
        # Only rebuild widgets if record count changed or if last_record_count is -1 (forced rebuild)
        current_count = len(self.data_manager.data["current_shift"]["records"])
        rebuild_widgets = (current_count != self.last_record_count) or (self.last_record_count == -1)
        if self.last_record_count != -1:  # Only update if not forcing rebuild
            self.last_record_count = current_count
        else:
            self.last_record_count = current_count  # Reset after forced rebuild
        
        stats = self.calculate_stats()
        settings = self.data_manager.data["settings"]
        
        # Check for inactivity if shift is running
        if self.is_running and not self._auto_end_prompt_shown:
            current_time = datetime.now()
            inactivity_minutes = (current_time - self.last_activity_time).total_seconds() / 60
            
            if inactivity_minutes >= 60:
                self._auto_end_prompt_shown = True
                self.root.after(1, self._handle_inactivity_prompt)
        
        if settings.get("show_total", True):
            self.total_label_text.grid()
            self.total_value_frame.grid()
            self.total_label.config(text=f"{stats['total']:.1f}")
            if settings.get("show_comp_total", False):
                self.total_comp_label.config(text=f"(${stats['comp_total']:,.0f})")
            else:
                self.total_comp_label.config(text="")
        else:
            self.total_label_text.grid_remove()
            self.total_value_frame.grid_remove()
        
        if settings.get("show_avg", True):
            self.avg_label_text.grid()
            self.avg_value_frame.grid()
            self.avg_label.config(text=f"{stats['avg_per_hour']:.1f}")
            if settings.get("show_comp_avg", False):
                self.avg_comp_label.config(text=f"(${stats['comp_avg']:,.0f})")
            else:
                self.avg_comp_label.config(text="")
        else:
            self.avg_label_text.grid_remove()
            self.avg_value_frame.grid_remove()
        
        if settings.get("show_last_hour", True):
            self.last_hour_label_text.grid()
            self.last_hour_value_frame.grid()
            self.last_hour_label.config(text=f"{stats['last_hour']:.1f}")
            if settings.get("show_comp_last_hour", False):
                self.last_hour_comp_label.config(text=f"(${stats['comp_last_hour']:,.0f})")
            else:
                self.last_hour_comp_label.config(text="")
        else:
            self.last_hour_label_text.grid_remove()
            self.last_hour_value_frame.grid_remove()
        
        if settings.get("show_last_full_hour", True):
            self.last_full_hour_label_frame.grid()
            self.last_full_hour_value_frame.grid()
            self.last_full_hour_label.config(text=f"{stats['last_full_hour']:.1f}")
            range_text = stats.get("last_full_hour_range", "")
            if range_text:
                self.last_full_hour_range_label.config(text=range_text)
                self.last_full_hour_label_text.config(text="hour:")
            else:
                self.last_full_hour_range_label.config(text="")
                self.last_full_hour_label_text.config(text="hour:")
            if settings.get("show_comp_last_full_hour", False):
                self.last_full_hour_comp_label.config(text=f"(${stats['comp_last_full_hour']:,.0f})")
            else:
                self.last_full_hour_comp_label.config(text="")
        else:
            self.last_full_hour_label_frame.grid_remove()
            self.last_full_hour_value_frame.grid_remove()
        
        if settings.get("show_projected", True):
            self.projected_label_text.grid()
            self.projected_value_frame.grid()
            self.projected_label.config(text=f"{stats['projected']:.1f}")
            if settings.get("show_comp_projected", False):
                self.projected_comp_label.config(text=f"(${stats['comp_projected']:,.0f})")
            else:
                self.projected_comp_label.config(text="")
        else:
            self.projected_label_text.grid_remove()
            self.projected_value_frame.grid_remove()
        
        if settings.get("show_projected_shift", True):
            self.projected_shift_label_text.grid()
            self.projected_shift_value_frame.grid()
            self.projected_shift_label.config(text=f"{stats['projected_shift']:.1f}")
            if settings.get("show_comp_projected_shift", False):
                self.projected_shift_comp_label.config(text=f"(${stats['comp_projected_shift']:,.0f})")
            else:
                self.projected_shift_comp_label.config(text="")
        else:
            self.projected_shift_label_text.grid_remove()
            self.projected_shift_value_frame.grid_remove()
        
        # Only rebuild widgets if records changed
        if rebuild_widgets:
            # Update recent studies list with X buttons
            # Clear existing widgets from list
            for widget in list(self.study_widgets):
                try:
                    widget.destroy()
                except:
                    pass
            self.study_widgets.clear()
            # Clear time labels if they exist
            if hasattr(self, 'time_labels'):
                self.time_labels.clear()
            
            # Also clear ALL children from scrollable frame directly to ensure clean slate
            for child in list(self.studies_scrollable_frame.winfo_children()):
                try:
                    child.destroy()
                except:
                    pass
            
            # Calculate how many studies can fit based on canvas height
            canvas_height = self.studies_canvas.winfo_height()
            row_height = 18  # Approximate height per study row
            max_studies = max(3, canvas_height // row_height)  # At least 3
            records = self.data_manager.data["current_shift"]["records"][-max_studies:]
            # Display in reverse order (most recent first)
            for i, record in enumerate(reversed(records)):
                # Calculate actual index in full records list
                actual_index = len(self.data_manager.data["current_shift"]["records"]) - 1 - i
                
                # Create frame for this study (vertical container)
                # Reduce vertical padding when show_time is enabled for tighter spacing
                show_time = self.data_manager.data["settings"].get("show_time", False)
                study_pady = 0 if show_time else 1
                study_frame = ttk.Frame(self.studies_scrollable_frame)
                study_frame.pack(fill=tk.X, pady=study_pady, padx=0)  # No horizontal padding
                
                # Main row frame (horizontal) - contains delete button, procedure, and RVU
                main_row_frame = ttk.Frame(study_frame)
                main_row_frame.pack(fill=tk.X, pady=0, padx=0)
                
                # X button to delete (on the left) - use Label for precise size control
                colors = self.theme_colors
                delete_btn = tk.Label(
                    main_row_frame, 
                    text="×", 
                    font=("Arial", 7),
                    bg=colors["delete_btn_bg"],
                    fg=colors["delete_btn_fg"],
                    cursor="hand2",
                    padx=0,
                    pady=0,
                    width=1,
                    anchor=tk.CENTER
                )
                # Store the actual_index in the button itself to avoid closure issues
                delete_btn.actual_index = actual_index
                # Use a closure to capture the index value
                delete_btn.bind("<Button-1>", lambda e, idx=actual_index: self.delete_study_by_index(idx))
                delete_btn.bind("<Enter>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_hover"]))
                delete_btn.bind("<Leave>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_bg"]))
                delete_btn.pack(side=tk.LEFT, padx=(1, 3), pady=0)
                
                # Study text label (show actual procedure name, or "Multiple XR" for multi-accession)
                is_multi = record.get('is_multi_accession', False)
                if is_multi:
                    # Multi-accession study - show "Multiple {modality}"
                    procedure_name = record.get('study_type', 'Multiple Studies')
                    study_type = procedure_name
                else:
                    procedure_name = record.get('procedure', record.get('study_type', 'Unknown'))
                    study_type = record.get('study_type', 'Unknown')
                
                # Check if study starts with CT, MR, US, XR, NM, or Multiple (case-insensitive)
                procedure_upper = procedure_name.upper().strip()
                study_type_upper = study_type.upper().strip()
                valid_prefixes = ['CT', 'MR', 'US', 'XR', 'NM', 'MULTIPLE']
                starts_with_valid = any(procedure_upper.startswith(prefix) or study_type_upper.startswith(prefix) for prefix in valid_prefixes)
                
                # Dynamic truncation based on window width
                frame_width = self.root.winfo_width()
                max_chars = self._calculate_max_chars(frame_width)
                display_name = self._truncate_text(procedure_name, max_chars)
                
                # Procedure label - left-aligned
                procedure_label = ttk.Label(main_row_frame, text=display_name, font=("Consolas", 8))
                if not starts_with_valid:
                    procedure_label.config(foreground="#8B0000")  # Dark red
                procedure_label.pack(side=tk.LEFT)
                
                # RVU label - stays on the far right
                rvu_text = f"{record['rvu']:.1f} RVU"
                rvu_label = ttk.Label(main_row_frame, text=rvu_text, font=("Consolas", 8))
                if not starts_with_valid:
                    rvu_label.config(foreground="#8B0000")  # Dark red
                rvu_label.pack(side=tk.RIGHT)
                
                # Time information row (if show_time is enabled) - appears BELOW the main row, tightly spaced
                show_time = self.data_manager.data["settings"].get("show_time", False)
                if show_time:
                    # Use regular tk.Frame with minimal height to reduce spacing
                    # Use canvas_bg to match the studies scrollable area background
                    bg_color = self.theme_colors.get("canvas_bg", self.theme_colors.get("bg", "#f0f0f0"))
                    time_row_frame = tk.Frame(study_frame, bg=bg_color, height=12)
                    time_row_frame.pack(fill=tk.X, pady=(0, 0), padx=0)
                    time_row_frame.pack_propagate(False)  # Prevent frame from expanding
                    
                    # Add a small spacer on the left to align with procedure text (accounting for X button width)
                    spacer_label = tk.Label(time_row_frame, text="", width=2, bg=bg_color, height=1)  # Approximate width of X button + padding
                    spacer_label.pack(side=tk.LEFT, pady=0, padx=0)
                    
                    # Time ago label - left-justified, smaller font, lighter color, no padding
                    # Use tk.Label instead of ttk.Label for less padding
                    time_ago_text = self._format_time_ago(record.get("time_finished"))
                    # Use theme color for secondary text
                    text_color = self.theme_colors.get("text_secondary", "gray")
                    time_ago_label = tk.Label(
                        time_row_frame,
                        text=time_ago_text, 
                        font=("Consolas", 7),
                        fg=text_color,
                        bg=bg_color,
                        padx=0,
                        pady=0,
                        anchor=tk.W
                    )
                    time_ago_label.pack(side=tk.LEFT, pady=0, padx=0)
                    
                    # Duration label - right-justified, same style as RVU, no padding
                    duration_seconds = record.get("duration_seconds", 0)
                    duration_text = self._format_duration(duration_seconds)
                    duration_label = tk.Label(
                        time_row_frame,
                        text=duration_text,
                        font=("Consolas", 7),
                        fg=text_color,
                        bg=bg_color,
                        padx=0,
                        pady=0,
                        anchor=tk.E
                    )
                    duration_label.pack(side=tk.RIGHT, pady=0, padx=0)
                    
                    # Store labels for updating
                    if not hasattr(self, 'time_labels'):
                        self.time_labels = []
                    self.time_labels.append({
                        'time_ago_label': time_ago_label,
                        'duration_label': duration_label,
                        'spacer_label': spacer_label,
                        'record': record,
                        'time_row_frame': time_row_frame
                    })
                
                self.study_widgets.append(study_frame)
            
            # Scroll to top to show most recent
            self.studies_canvas.update_idletasks()
            self.studies_canvas.yview_moveto(0)
            
            total_records = len(self.data_manager.data["current_shift"]["records"])
            if total_records > max_studies:
                more_count = total_records - max_studies
                more_label = ttk.Label(self.studies_scrollable_frame, text=f"... {more_count} more", font=("Consolas", 7), foreground="gray")
                more_label.pack()
                self.study_widgets.append(more_label)
    
    def update_debug_display(self):
        """Update the debug display with current PowerScribe or Mosaic data."""
        show_time = self.data_manager.data["settings"].get("show_time", False)
        
        # FIRST: Check if accession is already recorded (even if procedure is N/A)
        # This ensures "already recorded" shows for both PowerScribe and Mosaic
        # when reopening a previously-recorded study
        is_duplicate_for_display = False
        duplicate_count = 0
        total_count = 0
        all_duplicates = False
        some_duplicates = False
        
        # =======================================================================
        # UNIFIED DUPLICATE DETECTION - Same logic for PowerScribe and Mosaic
        # =======================================================================
        # 
        # Duplicate check order (same for all accessions):
        # 1. Check seen_accessions memory cache (fastest)
        # 2. Check database for current shift
        # 3. Check multi-accession history
        #
        # If any check finds the accession was recorded, it's a duplicate.
        # =======================================================================
        
        def _check_accession_duplicate(acc: str, current_shift) -> bool:
            """Check if a single accession is a duplicate. Returns True if duplicate."""
            # 1. Check memory cache first (fastest)
            if acc in self.tracker.seen_accessions:
                return True
            
            # 2. Check database
            if current_shift:
                try:
                    db_record = self.data_manager.db.find_record_by_accession(
                        current_shift['id'], acc
                    )
                    if db_record:
                        # Cache for future checks
                        self.tracker.seen_accessions.add(acc)
                        return True
                except:
                    pass
            
            # 3. Check multi-accession history
            if self.tracker._was_part_of_multi_accession(acc, self.data_manager):
                # Cache for future checks
                self.tracker.seen_accessions.add(acc)
                return True
            
            return False
        
        ignore_duplicates = self.data_manager.data["settings"].get("ignore_duplicate_accessions", True)
        current_shift = None
        if ignore_duplicates:
            try:
                current_shift = self.data_manager.db.get_current_shift()
            except:
                pass
        
        # Check for duplicates in multi-accession studies
        if self.current_multiple_accessions:
            # Extract all accession numbers for duplicate checking
            # Format: PowerScribe = ["ACC1", "ACC2"], Mosaic = ["ACC1 (PROC1)", "ACC2 (PROC2)"]
            all_accession_numbers = []
            for acc_entry in self.current_multiple_accessions:
                if '(' in acc_entry and ')' in acc_entry:
                    # Mosaic format: "ACC (PROC)" - extract just the accession
                    acc_match = re.match(r'^([^(]+)', acc_entry)
                    if acc_match:
                        all_accession_numbers.append(acc_match.group(1).strip())
                else:
                    # PowerScribe format: just accession
                    all_accession_numbers.append(acc_entry.strip())
            
            total_count = len(all_accession_numbers)
            
            if ignore_duplicates and all_accession_numbers:
                for acc in all_accession_numbers:
                    if _check_accession_duplicate(acc, current_shift):
                        duplicate_count += 1
                
                all_duplicates = duplicate_count == total_count
                some_duplicates = duplicate_count > 0 and duplicate_count < total_count
                is_duplicate_for_display = all_duplicates
        
        # Check for duplicates in single-accession studies
        elif self.current_accession and ignore_duplicates:
            is_duplicate_for_display = _check_accession_duplicate(self.current_accession, current_shift)
        
        # NOTE: We no longer return early for duplicates. Instead, we show "already recorded"
        # for the accession line but continue displaying the rest (procedure, study type, timer, etc.)
        # The duplicate status (is_duplicate_for_display, all_duplicates, some_duplicates) is used
        # below when displaying the accession line.
        
        # Check if procedure is "n/a" - if so, don't display anything
        is_na = self.current_procedure and self.current_procedure.strip().lower() in ["n/a", "na", "none", ""]
        
        if is_na or not self.current_procedure:
            self.debug_accession_label.config(text="", foreground="gray")
            self.debug_duration_label.config(text="")
            self.debug_procedure_label.config(text="", foreground="gray")
            self.debug_patient_class_label.config(text="")
            self.debug_study_type_prefix_label.config(text="")
            self.debug_study_type_label.config(text="")
            self.debug_study_rvu_label.config(text="")
        else:
            # Handle multi-accession display (2+ accessions only)
            if self.current_multiple_accessions and len(self.current_multiple_accessions) > 1:
                # Multi-accession - either active or duplicate
                # Parse accession display - handle both formats:
                # PowerScribe: ["ACC1", "ACC2"]
                # Mosaic: ["ACC1 (PROC1)", "ACC2 (PROC2)"]
                acc_display_list = []
                accession_numbers = []  # For duplicate checking
                for acc_entry in self.current_multiple_accessions[:2]:
                    if '(' in acc_entry and ')' in acc_entry:
                        # Mosaic format - extract just the accession
                        acc_match = re.match(r'^([^(]+)', acc_entry)
                        if acc_match:
                            acc_num = acc_match.group(1).strip()
                            acc_display_list.append(acc_num)
                            accession_numbers.append(acc_num)
                        else:
                            acc_display_list.append(acc_entry)
                            accession_numbers.append(acc_entry)
                    else:
                        # PowerScribe format - use as-is
                        acc_display_list.append(acc_entry)
                        accession_numbers.append(acc_entry)
                
                # Reuse duplicate checking results computed at the top of the function
                # (all_duplicates, some_duplicates, duplicate_count, total_count already computed)
                
                acc_display = ", ".join(acc_display_list)
                if len(self.current_multiple_accessions) > 2:
                    acc_display += f" (+{len(self.current_multiple_accessions) - 2})"
                
                # Calculate duration for current study (show timer even for duplicates)
                duration_text = ""
                if show_time and (self.current_accession or self.multi_accession_mode):
                    duration_text = self._get_current_study_duration()
                
                # Truncate accession if needed to make room for duration
                if show_time and duration_text:
                    # Calculate available width for accession
                    frame_width = self.root.winfo_width()
                    if frame_width > 100:
                        # Estimate space needed for duration (roughly 8-10 chars like "12m 34s")
                        duration_chars = 8
                        prefix_chars = len("Accession: ")
                        reserved = 95 + (duration_chars * 6)  # Reserve space for duration
                        usable_width = max(frame_width - reserved, 50)
                        char_width = 8 * 0.75
                        max_chars = int(usable_width / char_width)
                        max_chars = max(10, min(max_chars, 100))
                        
                        if len(acc_display) > max_chars:
                            acc_display = self._truncate_text(acc_display, max_chars)
                
                # Display based on duplicate status
                # Show "already recorded" for accession line, but still show duration and other fields
                if all_duplicates:
                    # All accessions already recorded
                    self.debug_accession_label.config(text="already recorded", foreground="#c62828")
                elif some_duplicates:
                    # Partial duplicates - show "X of Y already recorded" in red
                    self.debug_accession_label.config(text=f"{duplicate_count} of {total_count} already recorded", foreground="#c62828")
                else:
                    # No duplicates - show normal accession display
                    self.debug_accession_label.config(text=f"Accession: {acc_display}", foreground="gray")
                
                # Always show duration (even for duplicates - shows how long user has been viewing)
                self.debug_duration_label.config(text=duration_text if show_time else "")
                
                # Check if this is Mosaic multi-accession (no multi_accession_mode, but has multiple)
                data_source = self._active_source or "PowerScribe"
                is_mosaic_multi = data_source == "Mosaic" and len(self.current_multiple_accessions) > 1 and not self.multi_accession_mode
                
                if is_mosaic_multi:
                    # Mosaic multi-accession - show summary
                    if self.current_procedure and self.current_procedure != "Multiple studies":
                        # Show the first procedure
                        self.debug_procedure_label.config(text=f"Procedure: {self.current_procedure}", foreground="gray")
                    else:
                        # Show summary
                        self.debug_procedure_label.config(text=f"Procedure: {len(self.current_multiple_accessions)} studies", foreground="gray")
                elif self.multi_accession_mode:
                    # Active multi-accession tracking
                    collected_count = len(self.multi_accession_data)
                    total_count = len(self.current_multiple_accessions)
                    
                    if collected_count < total_count:
                        # Incomplete - show in red
                        self.debug_procedure_label.config(text=f"Procedure: incomplete ({collected_count}/{total_count})", foreground="red")
                    else:
                        # Complete - show "Multiple" with modality
                        modalities = set()
                        for d in self.multi_accession_data.values():
                            st = d["study_type"]
                            if st:
                                parts = st.split()
                                if parts:
                                    modalities.add(parts[0])
                        modality = list(modalities)[0] if modalities else "Studies"
                        self.debug_procedure_label.config(text=f"Procedure: Multiple {modality}", foreground="gray")
                else:
                    # Duplicate multi-accession - already completed, extract modality from study type
                    if self.current_study_type.startswith("Multiple"):
                        self.debug_procedure_label.config(text=f"Procedure: {self.current_study_type}", foreground="gray")
                    else:
                        # Get modality from current procedure
                        classification_rules = self.data_manager.data.get("classification_rules", {})
                        direct_lookups = self.data_manager.data.get("direct_lookups", {})
                        study_type, _ = match_study_type(self.current_procedure, self.data_manager.data["rvu_table"], classification_rules, direct_lookups)
                        parts = study_type.split() if study_type else []
                        modality = parts[0] if parts else "Studies"
                        self.debug_procedure_label.config(text=f"Procedure: Multiple {modality}", foreground="gray")
            else:
                # Single accession display
                accession_text = self.current_accession if self.current_accession else '-'
                
                # Use the duplicate check result from the top of the function
                # (is_duplicate_for_display was already computed using unified logic)
                is_duplicate = is_duplicate_for_display
                
                # Calculate duration for current study (show timer even for duplicates)
                duration_text = ""
                if show_time and (self.current_accession or self.multi_accession_mode):
                    duration_text = self._get_current_study_duration()
                
                # Truncate accession if needed to make room for duration
                if show_time and duration_text:
                    # Calculate available width for accession
                    frame_width = self.root.winfo_width()
                    if frame_width > 100:
                        # Estimate space needed for duration (roughly 8-10 chars like "12m 34s")
                        duration_chars = 8
                        prefix_chars = len("Accession: ")
                        reserved = 95 + (duration_chars * 6)  # Reserve space for duration
                        usable_width = max(frame_width - reserved, 50)
                        char_width = 8 * 0.75
                        max_chars = int(usable_width / char_width)
                        max_chars = max(10, min(max_chars, 100))
                        
                        if len(accession_text) > max_chars:
                            accession_text = self._truncate_text(accession_text, max_chars)
                
                # If duplicate, show "already recorded" in red instead of accession
                # But still show duration and other fields normally
                if is_duplicate:
                    self.debug_accession_label.config(text="already recorded", foreground="#c62828")
                else:
                    self.debug_accession_label.config(text=f"Accession: {accession_text}", foreground="gray")
                
                # Always show duration (even for duplicates - shows how long user has been viewing)
                self.debug_duration_label.config(text=duration_text if show_time else "")
                
                # No truncation for procedure - show full name
                procedure_display = self.current_procedure if self.current_procedure else '-'
                self.debug_procedure_label.config(text=f"Procedure: {procedure_display}", foreground="gray")
            
            self.debug_patient_class_label.config(text=f"Patient Class: {self.current_patient_class if self.current_patient_class else '-'}")
            
            # Display study type with RVU on the right (separate labels for alignment)
            if self.current_study_type:
                # Dynamic truncation based on window width to balance readability with RVU visibility
                frame_width = self.root.winfo_width()
                # Calculate available space: window width minus prefix, RVU, and margins
                # Estimate: "Study Type: " (13 chars) + RVU " 99.9 RVU" (9 chars) + margins (30px)
                available_width = max(frame_width - 180, 80)  # At least 80px
                char_width = 7.5  # Average char width in pixels for Arial 7
                max_chars = int(available_width / char_width)
                max_chars = max(15, min(max_chars, 35))  # Between 15-35 chars
                study_type_display = self._truncate_text(self.current_study_type, max_chars)
                # Show prefix
                self.debug_study_type_prefix_label.config(text="Study Type: ", foreground="gray")
                # Check if incomplete (starts with "incomplete") - show in red
                if self.current_study_type.startswith("incomplete"):
                    self.debug_study_type_label.config(text=study_type_display, foreground="red")
                else:
                    self.debug_study_type_label.config(text=study_type_display, foreground="gray")
                rvu_value = self.current_study_rvu if self.current_study_rvu is not None else 0.0
                self.debug_study_rvu_label.config(text=f"{rvu_value:.1f} RVU")
            else:
                self.debug_study_type_prefix_label.config(text="Study Type: ", foreground="gray")
                self.debug_study_type_label.config(text="-", foreground="gray")
                self.debug_study_rvu_label.config(text="")

    def _format_hour_label(self, dt: datetime) -> str:
        """Format datetime into '2am' style label."""
        if not dt:
            return ""
        label = dt.strftime("%I%p").lstrip("0").lower()
        return label or dt.strftime("%I%p").lower()
    
    def open_settings(self):
        """Open settings modal."""
        from .settings_window import SettingsWindow
        SettingsWindow(self.root, self.data_manager, self)

    def open_statistics(self):
        """Open statistics modal."""
        from .statistics_window import StatisticsWindow
        StatisticsWindow(self.root, self.data_manager, self)
    
    def open_tools(self):
        """Open tools window (Database Repair & Excel Checker)."""
        from .tools_window import ToolsWindow
        ToolsWindow(self.root, self)
    
    def open_whats_new(self):
        """Open What's New window."""
        from .whats_new_window import WhatsNewWindow
        WhatsNewWindow(self.root)
    
    def apply_theme(self):
        """Apply light or dark theme based on settings."""
        dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
        
        # Use 'clam' theme for both modes to ensure consistent layout
        # (Windows native theme ignores background colors and has different sizing)
        self.style.theme_use('clam')
        
        if dark_mode:
            # Dark mode colors
            bg_color = "#1e1e1e"  # Almost black
            fg_color = "#e0e0e0"  # Slightly off white
            entry_bg = "#2d2d2d"
            entry_fg = "#e0e0e0"
            select_bg = "#4a4a4a"
            button_bg = "#3d3d3d"
            button_fg = "#e0e0e0"
            button_active_bg = "#4a4a4a"
            treeview_bg = "#252525"
            treeview_fg = "#e0e0e0"
            canvas_bg = "#1e1e1e"
            comp_color = "#4ec94e"  # Lighter green for dark mode
            delete_btn_bg = "#3d3d3d"
            delete_btn_fg = "#aaaaaa"
            delete_btn_hover = "#ff6b6b"  # Light red for dark mode
            border_color = "#888888"  # Light grey for canvas borders (visible on dark background)
            text_secondary = "#aaaaaa"  # Gray text for secondary info
        else:
            # Light mode colors
            bg_color = "#f0f0f0"
            fg_color = "black"
            entry_bg = "white"
            entry_fg = "black"
            select_bg = "#0078d7"
            button_bg = "#e1e1e1"
            button_fg = "black"
            button_active_bg = "#d0d0d0"
            treeview_bg = "white"
            treeview_fg = "black"
            canvas_bg = "#f0f0f0"
            comp_color = "dark green"
            delete_btn_bg = "#f0f0f0"
            delete_btn_fg = "gray"
            delete_btn_hover = "#ffcccc"  # Light red for light mode
            border_color = "#cccccc"  # Light grey for canvas borders
            text_secondary = "gray"  # Gray text for secondary info
            # Pace car bar colors (light mode)
            pace_container_bg = "#e0e0e0"
            pace_current_track_bg = "#e8e8e8"
            pace_prior_track_bg = "#B8B8DC"  # Lavender
            pace_marker_bg = "#000000"  # Black marker
        
        if dark_mode:
            # Pace car bar colors (dark mode)
            pace_container_bg = "#3d3d3d"
            pace_current_track_bg = "#4a4a4a"
            pace_prior_track_bg = "#5a5a8c"  # Darker lavender
            pace_marker_bg = "#ffffff"  # White marker for visibility
        
        # Store current theme colors for new widgets
        self.theme_colors = {
            "bg": bg_color,
            "fg": fg_color,
            "button_bg": button_bg,
            "button_fg": button_fg,
            "button_active_bg": button_active_bg,
            "entry_bg": entry_bg,
            "entry_fg": entry_fg,
            "comp_color": comp_color,
            "canvas_bg": canvas_bg,
            "delete_btn_bg": delete_btn_bg,
            "delete_btn_fg": delete_btn_fg,
            "delete_btn_hover": delete_btn_hover,
            "border_color": border_color,
            "text_secondary": text_secondary,
            "dark_mode": dark_mode,
            "pace_container_bg": pace_container_bg,
            "pace_current_track_bg": pace_current_track_bg,
            "pace_prior_track_bg": pace_prior_track_bg,
            "pace_marker_bg": pace_marker_bg
        }
        
        # Configure root window
        self.root.configure(bg=bg_color)
        
        # Configure ttk styles
        self.style.configure(".", background=bg_color, foreground=fg_color)
        self.style.configure("TFrame", background=bg_color)
        self.style.configure("TLabel", background=bg_color, foreground=fg_color)
        self.style.configure("TLabelframe", background=bg_color, bordercolor=border_color)
        self.style.configure("TLabelframe.Label", background=bg_color, foreground=fg_color)
        self.style.configure("TButton", background=button_bg, foreground=button_fg, bordercolor=border_color, padding=(5, 2))
        self.style.map("TButton", 
                       background=[("active", button_active_bg), ("pressed", button_active_bg)],
                       foreground=[("active", fg_color), ("pressed", fg_color)])
        self.style.configure("TCheckbutton", background=bg_color, foreground=fg_color)
        self.style.map("TCheckbutton", background=[("active", bg_color)])
        self.style.configure("TRadiobutton", background=bg_color, foreground=fg_color)
        self.style.map("TRadiobutton", background=[("active", bg_color)])
        self.style.configure("TEntry", fieldbackground=entry_bg, foreground=entry_fg, bordercolor=border_color)
        self.style.configure("Treeview", background=treeview_bg, foreground=treeview_fg, fieldbackground=treeview_bg)
        self.style.configure("Treeview.Heading", background=button_bg, foreground=fg_color)
        self.style.map("Treeview", background=[("selected", select_bg)])
        self.style.configure("TScrollbar", background=button_bg, troughcolor=bg_color, bordercolor=border_color)
        self.style.configure("TPanedwindow", background=bg_color)
        
        # Keep red style for temporary recent studies label
        self.style.configure("Red.TLabelframe.Label", foreground="red", background=bg_color)
        
        # Update tk widgets (non-ttk) if they exist
        self._update_tk_widget_colors()
    
    def _update_tk_widget_colors(self):
        """Update colors for tk (non-ttk) widgets."""
        colors = getattr(self, 'theme_colors', None)
        if not colors:
            return
            
        bg_color = colors["bg"]
        fg_color = colors["fg"]
        comp_color = colors["comp_color"]
        canvas_bg = colors["canvas_bg"]
        text_secondary = colors.get("text_secondary", "gray")
        
        # Pace car bar colors
        pace_container_bg = colors.get("pace_container_bg", "#e0e0e0")
        pace_current_track_bg = colors.get("pace_current_track_bg", "#e8e8e8")
        pace_prior_track_bg = colors.get("pace_prior_track_bg", "#B8B8DC")
        pace_marker_bg = colors.get("pace_marker_bg", "#000000")
        
        # Update compensation labels (tk.Label)
        for label in [
            getattr(self, 'total_comp_label', None),
            getattr(self, 'avg_comp_label', None),
            getattr(self, 'last_hour_comp_label', None),
            getattr(self, 'last_full_hour_comp_label', None),
            getattr(self, 'projected_comp_label', None),
            getattr(self, 'projected_shift_comp_label', None),
        ]:
            if label:
                label.configure(bg=bg_color, fg=comp_color)
        
        # Update backup status label (tk.Label)
        backup_label = getattr(self, 'backup_status_label', None)
        if backup_label:
            backup_label.configure(bg=bg_color)
        
        # Update canvas
        canvas = getattr(self, 'studies_canvas', None)
        if canvas:
            canvas.configure(bg=canvas_bg)
        
        # Update counters frame (tk.LabelFrame)
        counters_frame = getattr(self, 'counters_frame', None)
        if counters_frame:
            counters_frame.configure(bg=bg_color, fg=fg_color)
        
        # Update recent studies frame (tk.LabelFrame)
        recent_frame = getattr(self, 'recent_frame', None)
        if recent_frame:
            recent_frame.configure(bg=bg_color, fg=fg_color)
        
        # Update debug/current study frame (tk.LabelFrame)
        debug_frame = getattr(self, 'debug_frame', None)
        if debug_frame:
            debug_frame.configure(bg=bg_color, fg=fg_color)
        
        # Update pace car bar widgets (tk.Frame)
        pace_bars_container = getattr(self, 'pace_bars_container', None)
        if pace_bars_container:
            pace_bars_container.configure(bg=pace_container_bg)
        
        pace_bar_current_track = getattr(self, 'pace_bar_current_track', None)
        if pace_bar_current_track:
            pace_bar_current_track.configure(bg=pace_current_track_bg)
        
        pace_bar_prior_track = getattr(self, 'pace_bar_prior_track', None)
        if pace_bar_prior_track:
            pace_bar_prior_track.configure(bg=pace_prior_track_bg)
        
        pace_bar_prior_marker = getattr(self, 'pace_bar_prior_marker', None)
        if pace_bar_prior_marker:
            pace_bar_prior_marker.configure(bg=pace_marker_bg)
        
        # Update pace car labels (tk.Label)
        pace_labels = [
            getattr(self, 'pace_label_now_text', None),
            getattr(self, 'pace_label_separator', None),
            getattr(self, 'pace_label_time', None),
            getattr(self, 'pace_label_right', None),
            getattr(self, 'pace_label_prior_text', None),
        ]
        for label in pace_labels:
            if label:
                label.configure(bg=bg_color, fg=text_secondary)
        
        # pace_label_now_value and pace_label_prior_value keep their dynamic colors
        if hasattr(self, 'pace_label_now_value') and self.pace_label_now_value:
            self.pace_label_now_value.configure(bg=bg_color)
        if hasattr(self, 'pace_label_prior_value') and self.pace_label_prior_value:
            self.pace_label_prior_value.configure(bg=bg_color)
        
        # Update pace label frame
        if hasattr(self, 'pace_label_frame') and self.pace_label_frame:
            self.pace_label_frame.configure(bg=bg_color)
        
        # Update studies_scrollable_frame style to use canvas_bg
        # ttk.Frame uses TFrame style, but we need a specific style for the scrollable frame
        self.style.configure("StudiesScrollable.TFrame", background=canvas_bg)
        studies_frame = getattr(self, 'studies_scrollable_frame', None)
        if studies_frame:
            studies_frame.configure(style="StudiesScrollable.TFrame")
    
    def get_theme_colors(self):
        """Get current theme colors for use by other windows."""
        return getattr(self, 'theme_colors', {
            "bg": "#f0f0f0",
            "fg": "black",
            "button_bg": "#e1e1e1",
            "button_fg": "black",
            "button_active_bg": "#d0d0d0",
            "delete_btn_bg": "#f0f0f0",
            "delete_btn_fg": "gray",
            "delete_btn_hover": "#ffcccc",
            "canvas_bg": "#f0f0f0",
            "dark_mode": False
        })
    
    def _on_window_resize(self, event):
        """Handle window resize to update truncation and study count."""
        if event.widget == self.root:
            new_width = event.width
            new_height = event.height
            # Only update if size actually changed significantly
            width_changed = abs(new_width - self._last_width) > 5
            height_changed = abs(new_height - getattr(self, '_last_height', 500)) > 15
            if width_changed or height_changed:
                self._last_width = new_width
                self._last_height = new_height
                # Force rebuild of study widgets with new truncation/count
                self.last_record_count = -1
                self.update_display()
    
    def _calculate_max_chars(self, available_width: int, font_size: int = 8) -> int:
        """Calculate max characters that fit in available width."""
        # Use default width if window not yet laid out (winfo returns 1)
        if available_width < 100:
            available_width = 240  # Default window width
        # Approximate character width for Consolas font
        # At font size 8, each character is roughly 6-7 pixels wide
        char_width = font_size * 0.75
        # Reserve space for: delete button (~20px), RVU label (~60px), padding (~15px)
        reserved = 95
        usable_width = max(available_width - reserved, 50)
        max_chars = int(usable_width / char_width)
        return max(10, min(max_chars, 100))  # Clamp between 10 and 100
    
    def _truncate_text(self, text: str, max_chars: int) -> str:
        """Truncate text with ... if needed, no trailing space."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars-3] + "..."
    
    def _format_time_ago(self, time_finished_str: str) -> str:
        """Format how long ago a study was finished.
        
        Returns format like "5 seconds ago", "2 minutes ago", "1 hour ago"
        """
        if not time_finished_str:
            return ""
        try:
            time_finished = datetime.fromisoformat(time_finished_str)
            now = datetime.now()
            delta = now - time_finished
            
            total_seconds = int(delta.total_seconds())
            
            if total_seconds < 60:
                return f"{total_seconds} second{'s' if total_seconds != 1 else ''} ago"
            elif total_seconds < 3600:
                minutes = total_seconds // 60
                return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
            else:
                hours = total_seconds // 3600
                return f"{hours} hour{'s' if hours != 1 else ''} ago"
        except (ValueError, TypeError):
            return ""
    
    def _format_duration(self, duration_seconds: float) -> str:
        """Format study duration in "xxm xxs" format.
        
        Examples: "45s", "1m 11s", "12m 30s"
        Shows minutes only if >= 1 minute.
        """
        if not duration_seconds:
            return "0s"
        
        total_seconds = int(duration_seconds)
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        
        if minutes >= 1:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    
    def _get_current_study_duration(self) -> str:
        """Get the duration of the currently active study.
        
        Returns formatted duration string (e.g., "5m 23s") or empty string if not available.
        """
        # For multi-accession mode, use multi_accession_start_time
        if self.multi_accession_mode and self.multi_accession_start_time:
            current_time = datetime.now()
            duration_seconds = (current_time - self.multi_accession_start_time).total_seconds()
            return self._format_duration(duration_seconds)
        
        # For single accession, check if this study is in active_studies
        if self.current_accession and self.current_accession in self.tracker.active_studies:
            study = self.tracker.active_studies[self.current_accession]
            start_time = study.get("start_time")
            if start_time:
                current_time = datetime.now()
                duration_seconds = (current_time - start_time).total_seconds()
                return self._format_duration(duration_seconds)
        
        return ""
    
    def start_drag(self, event):
        """Start dragging window."""
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        # Initialize last saved position if not set
        if not hasattr(self, '_last_saved_main_x'):
            self._last_saved_main_x = self.root.winfo_x()
            self._last_saved_main_y = self.root.winfo_y()
    
    def on_drag(self, event):
        """Handle window dragging."""
        x = self.root.winfo_x() + event.x - self.drag_start_x
        y = self.root.winfo_y() + event.y - self.drag_start_y
        self.root.geometry(f"+{x}+{y}")
        # Debounce position saving during drag (only if position changed)
        current_x = self.root.winfo_x()
        current_y = self.root.winfo_y()
        if current_x != self._last_saved_main_x or current_y != self._last_saved_main_y:
            if hasattr(self, '_position_save_timer'):
                self.root.after_cancel(self._position_save_timer)
            # Use shorter debounce during drag (100ms) to be more responsive
            self._position_save_timer = self.root.after(100, self.save_window_position)
    
    def _ensure_window_visible(self):
        """Post-mapping validation: ensure window is visible on a monitor.
        
        Called shortly after window is mapped to handle edge cases where:
        - Monitor configuration changed since last run
        - Window was dragged off-screen in a previous session
        - First run on a multi-monitor setup with unusual arrangement
        """
        try:
            self.root.update_idletasks()  # Ensure geometry is updated
            
            x = self.root.winfo_x()
            y = self.root.winfo_y()
            width = self.root.winfo_width()
            height = self.root.winfo_height()
            
            # Check if window top-left area is on any monitor
            # Check slightly inward to ensure the window is actually usable
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Window not visible at ({x}, {y}), repositioning...")
                
                # Find the nearest valid position
                new_x, new_y = find_nearest_monitor_for_window(x, y, width, height)
                
                if new_x != x or new_y != y:
                    self.root.geometry(f"{width}x{height}+{new_x}+{new_y}")
                    logger.info(f"Window moved from ({x}, {y}) to ({new_x}, {new_y})")
                    
                    # Save the corrected position
                    self.root.after(100, self.save_window_position)
                else:
                    # Fallback: center on primary monitor
                    primary = get_primary_monitor_bounds()
                    new_x = primary[0] + (primary[2] - primary[0] - width) // 2
                    new_y = primary[1] + (primary[3] - primary[1] - height) // 2
                    self.root.geometry(f"{width}x{height}+{new_x}+{new_y}")
                    logger.info(f"Window centered on primary monitor at ({new_x}, {new_y})")
                    self.root.after(100, self.save_window_position)
            else:
                logger.debug(f"Window visible at ({x}, {y})")
                
        except Exception as e:
            logger.error(f"Error ensuring window visibility: {e}")
    
    def on_drag_end(self, event):
        """Handle end of window dragging - save position immediately."""
        # Cancel any pending debounced save
        if hasattr(self, '_position_save_timer'):
            self.root.after_cancel(self._position_save_timer)
        # Save immediately on mouse release
        self.save_window_position()
    
    def on_double_click(self, event):
        """Handle double-click on main window - launch mini interface."""
        logger.info("Main window double-clicked, launching mini interface")
        self.launch_mini_interface()
    
    def launch_mini_interface(self):
        """Launch the mini interface and hide the main window."""
        if self.mini_window is not None:
            # Mini window already exists, just focus it
            self.mini_window.window.focus_force()
            return
        
        # Save main window position before hiding
        self.save_window_position()
        
        # Hide main window
        self.root.withdraw()
        
        # Create mini window
        from .mini_window import MiniWindow
        self.mini_window = MiniWindow(self.root, self.data_manager, self)
    
    def save_window_position(self):
        """Save the main window position and size."""
        try:
            current_x = self.root.winfo_x()
            current_y = self.root.winfo_y()
            
            # Only save if position actually changed
            if hasattr(self, '_last_saved_main_x') and hasattr(self, '_last_saved_main_y'):
                if current_x == self._last_saved_main_x and current_y == self._last_saved_main_y:
                    return  # Position hasn't changed, don't save
            
            if "window_positions" not in self.data_manager.data:
                self.data_manager.data["window_positions"] = {}
            self.data_manager.data["window_positions"]["main"] = {
                "x": current_x,
                "y": current_y,
                "width": self.root.winfo_width(),
                "height": self.root.winfo_height()
            }
            self._last_saved_main_x = current_x
            self._last_saved_main_y = current_y
            # Only save settings (window positions), not records
            self.data_manager.save(save_records=False)
            logger.debug(f"Main window position saved: ({current_x}, {current_y})")
        except Exception as e:
            logger.error(f"Error saving window position: {e}")
    
    def restore_window_position(self):
        """Restore the main window position from saved settings."""
        try:
            window_pos = self.data_manager.data.get("window_positions", {}).get("main")
            if window_pos:
                x = window_pos.get("x", 100)
                y = window_pos.get("y", 100)
                width = window_pos.get("width", 600)
                height = window_pos.get("height", 800)
                
                # Restore position and size
                self.root.geometry(f"{width}x{height}+{x}+{y}")
                logger.debug(f"Main window position restored: ({x}, {y}, {width}x{height})")
        except Exception as e:
            logger.error(f"Error restoring window position: {e}")
    
    def _update_time_display(self):
        """Update time display for recent studies and current study duration every second."""
        show_time = self.data_manager.data["settings"].get("show_time", False)
        
        # Update current study duration if show_time is enabled
        if show_time and hasattr(self, 'debug_duration_label'):
            duration_text = self._get_current_study_duration()
            if duration_text:
                # Update duration
                self.debug_duration_label.config(text=duration_text)
            else:
                self.debug_duration_label.config(text="")
        
        if hasattr(self, 'time_labels') and self.time_labels:
            if show_time:
                for label_info in self.time_labels:
                    try:
                        record = label_info['record']
                        time_ago_label = label_info['time_ago_label']
                        duration_label = label_info.get('duration_label')
                        time_row_frame = label_info.get('time_row_frame')
                        spacer_label = label_info.get('spacer_label')
                        
                        # Update time ago and ensure color matches theme
                        time_ago_text = self._format_time_ago(record.get("time_finished"))
                        text_color = self.theme_colors.get("text_secondary", "gray")
                        # Use canvas_bg to match the studies scrollable area background
                        bg_color = self.theme_colors.get("canvas_bg", self.theme_colors.get("bg", "#f0f0f0"))
                        
                        # Update all labels and frames with theme colors
                        time_ago_label.config(text=time_ago_text, fg=text_color, bg=bg_color)
                        
                        # Update duration label with theme colors
                        if duration_label:
                            duration_seconds = record.get("duration_seconds", 0)
                            duration_text = self._format_duration(duration_seconds)
                            duration_label.config(text=duration_text, fg=text_color, bg=bg_color)
                        
                        # Update spacer label background
                        if spacer_label:
                            spacer_label.config(bg=bg_color)
                        
                        # Update time row frame background
                        if time_row_frame:
                            time_row_frame.config(bg=bg_color)
                    except Exception as e:
                        logger.error(f"Error updating time display: {e}")
        
        # Schedule next update in 1 second for current study duration
        self.root.after(1000, self._update_time_display)
    
    def on_closing(self):
        """Handle window closing - properly cleanup resources."""
        logger.info("Application closing - starting cleanup...")
        
        # Stop the background thread first
        if hasattr(self, '_ps_thread_running'):
            self._ps_thread_running = False
            logger.info("Signaled background thread to stop")
        
        # Wait for thread to terminate (with timeout to prevent hanging)
        if hasattr(self, '_ps_thread') and self._ps_thread.is_alive():
            logger.info("Waiting for background thread to terminate...")
            self._ps_thread.join(timeout=2.0)
            if self._ps_thread.is_alive():
                logger.warning("Background thread did not terminate in time (daemon will be killed on exit)")
            else:
                logger.info("Background thread terminated cleanly")
        
        # Save window position and data
        self.save_window_position()
        self.data_manager.save()
        
        # Close database connection
        if hasattr(self, 'data_manager') and self.data_manager:
            try:
                self.data_manager.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database: {e}")
        
        logger.info("Application cleanup complete")
        self.root.destroy()


__all__ = ['RVUCounterApp']
