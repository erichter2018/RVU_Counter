"""Statistics window for RVU Counter - detailed analytics and graphing."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Dict, List, Optional

from ..core.config import HAS_MATPLOTLIB, HAS_TKCALENDAR
from ..core.platform_utils import is_point_on_any_monitor, find_nearest_monitor_for_window
from .widgets import CanvasTable

if HAS_MATPLOTLIB:
    import matplotlib
    matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import numpy as np

if HAS_TKCALENDAR:
    from tkcalendar import DateEntry, Calendar

if TYPE_CHECKING:
    from ..data import RVUData
    from .main_window import RVUCounterApp

logger = logging.getLogger(__name__)

class StatisticsWindow:
    """Statistics modal window for detailed stats."""
    
    def __init__(self, parent, data_manager: 'RVUData', app: 'RVUCounterApp'):
        self.parent = parent
        self.data_manager = data_manager
        self.app = app
        
        # Create modal window
        self.window = tk.Toplevel(parent)
        self.window.title("Statistics")
        self.window.transient(parent)
        self.window.grab_set()
        
        # Make window larger for detailed stats
        self.window.geometry("1350x800")
        self.window.minsize(800, 500)
        
        # Restore saved position or center on screen
        positions = self.data_manager.data.get("window_positions", {})
        if "statistics" in positions:
            pos = positions["statistics"]
            x, y = pos['x'], pos['y']
            # Validate position before applying
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Statistics window position ({x}, {y}) is off-screen, finding nearest monitor")
                x, y = find_nearest_monitor_for_window(x, y, 1350, 800)
            self.window.geometry(f"1350x800+{x}+{y}")
        else:
            # Center on primary monitor using Windows API
            try:
                primary = get_primary_monitor_bounds()
                x = primary[0] + (primary[2] - primary[0] - 1350) // 2
                y = primary[1] + (primary[3] - primary[1] - 800) // 2
                self.window.geometry(f"1350x800+{x}+{y}")
            except:
                # Fallback: use parent's screen (old behavior)
                parent.update_idletasks()
                screen_width = parent.winfo_screenwidth()
                screen_height = parent.winfo_screenheight()
                x = (screen_width - 1350) // 2
                y = (screen_height - 800) // 2
                self.window.geometry(f"1350x800+{x}+{y}")
        
        # Track position for saving
        self.last_saved_x = self.window.winfo_x()
        self.last_saved_y = self.window.winfo_y()
        
        # Bind window events
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.window.bind("<Configure>", self.on_configure)
        self.window.bind("<ButtonRelease-1>", self.on_statistics_drag_end)
        
        # Apply theme
        self.apply_theme()
        
        # Create UI
        self.create_ui()
    
    def create_ui(self):
        """Create the statistics UI."""
        # State variables
        self.selected_period = tk.StringVar(value="current_shift")
        self.view_mode = tk.StringVar(value="efficiency")
        self.selected_shift_index = None  # For shift list selection
        
        # Projection variables
        self.projection_days = tk.IntVar(value=14)
        self.projection_extra_days = tk.IntVar(value=0)
        self.projection_extra_hours = tk.IntVar(value=0)
        
        # Comparison mode variables
        self.comparison_shift1_index = None  # Index in shifts list for first shift (current/newer)
        self.comparison_shift2_index = None  # Index in shifts list for second shift (prior/older)
        self.comparison_graph_mode = tk.StringVar(value="accumulation")  # accumulation or average
        self.comparison_delta_mode = tk.StringVar(value="rvu")  # rvu or percent
        
        # Track previous period to show/hide custom date frame
        self.previous_period = "current_shift"
        
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create horizontal paned window (left panel + main content)
        paned = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)
        
        # === LEFT PANEL (Selection) ===
        left_panel = ttk.Frame(paned, padding="5")
        paned.add(left_panel, weight=0)
        
        # Shift Analysis Section
        shift_frame = ttk.LabelFrame(left_panel, text="Shift Analysis", padding="8")
        shift_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Current shift radio - disable if no shift is running
        self.current_shift_radio = ttk.Radiobutton(shift_frame, text="Current Shift", variable=self.selected_period, 
                       value="current_shift", command=self.refresh_data)
        self.current_shift_radio.pack(anchor=tk.W, pady=2)
        
        # Disable current shift option if no shift is running
        if not self.app.is_running:
            self.current_shift_radio.config(state=tk.DISABLED)
            self.selected_period.set("prior_shift")  # Default to prior shift instead
        
        ttk.Radiobutton(shift_frame, text="Prior Shift", variable=self.selected_period,
                       value="prior_shift", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        
        # Projection Section (only visible in compensation view)
        projection_frame = ttk.LabelFrame(left_panel, text="Projection", padding="8")
        projection_frame.pack(fill=tk.X, pady=(0, 10))
        self.projection_frame = projection_frame  # Store reference to show/hide
        
        self.projection_radio = ttk.Radiobutton(projection_frame, text="Monthly Projection", variable=self.selected_period,
                       value="projection", command=self.refresh_data)
        self.projection_radio.pack(anchor=tk.W, pady=2)
        # Initially hide projection section (only show when compensation view is selected)
        projection_frame.pack_forget()
        
        # Historical Section
        history_frame = ttk.LabelFrame(left_panel, text="Historical", padding="8")
        history_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Radiobutton(history_frame, text="This Work Week", variable=self.selected_period,
                       value="this_work_week", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last Work Week", variable=self.selected_period,
                       value="last_work_week", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="This Month", variable=self.selected_period,
                       value="this_month", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last Month", variable=self.selected_period,
                       value="last_month", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last 3 Months", variable=self.selected_period,
                       value="last_3_months", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="Last Year", variable=self.selected_period,
                       value="last_year", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(history_frame, text="All Time", variable=self.selected_period,
                       value="all_time", command=self.refresh_data).pack(anchor=tk.W, pady=2)
        
        # Custom date range option
        ttk.Radiobutton(history_frame, text="Custom Date Range", variable=self.selected_period,
                       value="custom_date_range", command=self.on_custom_date_selected).pack(anchor=tk.W, pady=2)
        
        # Custom date range input frame (hidden by default)
        self.custom_date_frame = ttk.Frame(history_frame)
        
        # Start date with calendar button
        ttk.Label(self.custom_date_frame, text="From:").grid(row=0, column=0, padx=(20, 5), pady=2, sticky=tk.W)
        start_date_frame = ttk.Frame(self.custom_date_frame)
        start_date_frame.grid(row=0, column=1, padx=5, pady=2, sticky=tk.W)
        
        self.custom_start_date = tk.StringVar(value=datetime.now().strftime("%m/%d/%Y"))
        self.custom_start_entry = ttk.Entry(start_date_frame, textvariable=self.custom_start_date, width=12)
        self.custom_start_entry.pack(side=tk.LEFT)
        
        if HAS_TKCALENDAR:
            def open_start_calendar():
                cal_dialog = tk.Toplevel(self.window)
                cal_dialog.title("Select Start Date")
                cal_dialog.transient(self.window)
                cal_dialog.grab_set()
                
                try:
                    # Parse current date or use today
                    current_val = self.custom_start_date.get()
                    try:
                        current_dt = datetime.strptime(current_val, "%m/%d/%Y")
                    except:
                        current_dt = datetime.now()
                    
                    # Apply theme colors to calendar
                    colors = self.app.get_theme_colors()
                    is_dark = colors['bg'] == '#2b2b2b'
                    
                    if is_dark:
                        # Dark mode calendar styling
                        cal = Calendar(cal_dialog, selectmode='day', 
                                     year=current_dt.year, month=current_dt.month, day=current_dt.day,
                                     background='#2b2b2b',
                                     foreground='white',
                                     headersbackground='#1e1e1e',
                                     headersforeground='white',
                                     selectbackground='#0078d7',
                                     selectforeground='white',
                                     normalbackground='#2b2b2b',
                                     normalforeground='white',
                                     weekendbackground='#353535',
                                     weekendforeground='white',
                                     othermonthforeground='#666666',
                                     othermonthbackground='#2b2b2b',
                                     othermonthweforeground='#666666',
                                     othermonthwebackground='#2b2b2b')
                    else:
                        # Light mode calendar (default)
                        cal = Calendar(cal_dialog, selectmode='day', 
                                     year=current_dt.year, month=current_dt.month, day=current_dt.day)
                    cal.pack(padx=10, pady=10)
                    
                    def set_start_date():
                        selected_date = cal.selection_get()
                        self.custom_start_date.set(selected_date.strftime("%m/%d/%Y"))
                        cal_dialog.destroy()
                        self.on_date_change()
                    
                    btn_frame = ttk.Frame(cal_dialog)
                    btn_frame.pack(pady=5)
                    ttk.Button(btn_frame, text="OK", command=set_start_date).pack(side=tk.LEFT, padx=5)
                    ttk.Button(btn_frame, text="Cancel", command=cal_dialog.destroy).pack(side=tk.LEFT, padx=5)
                    
                    # Position dialog next to the button
                    cal_dialog.update_idletasks()
                    button_x = start_date_frame.winfo_rootx() + start_cal_btn.winfo_x() + start_cal_btn.winfo_width()
                    button_y = start_date_frame.winfo_rooty() + start_cal_btn.winfo_y()
                    dialog_width = cal_dialog.winfo_width()
                    dialog_height = cal_dialog.winfo_height()
                    
                    # Position to the right of button, or above if not enough space
                    screen_width = self.window.winfo_screenwidth()
                    if button_x + dialog_width + 10 > screen_width:
                        # Position above button
                        cal_dialog.geometry(f"+{button_x - dialog_width // 2}+{button_y - dialog_height - 5}")
                    else:
                        # Position to the right
                        cal_dialog.geometry(f"+{button_x + 5}+{button_y}")
                    
                except Exception as e:
                    logger.error(f"Error opening calendar: {e}")
                    cal_dialog.destroy()
            
            start_cal_btn = ttk.Button(start_date_frame, text="📅", width=3, command=open_start_calendar)
            start_cal_btn.pack(side=tk.LEFT, padx=(2, 0))
        
        self.custom_start_entry.bind("<FocusOut>", lambda e: self.on_date_change())
        
        # End date with calendar button
        ttk.Label(self.custom_date_frame, text="To:").grid(row=1, column=0, padx=(20, 5), pady=2, sticky=tk.W)
        end_date_frame = ttk.Frame(self.custom_date_frame)
        end_date_frame.grid(row=1, column=1, padx=5, pady=2, sticky=tk.W)
        
        self.custom_end_date = tk.StringVar(value=datetime.now().strftime("%m/%d/%Y"))
        self.custom_end_entry = ttk.Entry(end_date_frame, textvariable=self.custom_end_date, width=12)
        self.custom_end_entry.pack(side=tk.LEFT)
        
        if HAS_TKCALENDAR:
            def open_end_calendar():
                cal_dialog = tk.Toplevel(self.window)
                cal_dialog.title("Select End Date")
                cal_dialog.transient(self.window)
                cal_dialog.grab_set()
                
                try:
                    # Parse current date or use today
                    current_val = self.custom_end_date.get()
                    try:
                        current_dt = datetime.strptime(current_val, "%m/%d/%Y")
                    except:
                        current_dt = datetime.now()
                    
                    # Apply theme colors to calendar
                    colors = self.app.get_theme_colors()
                    is_dark = colors['bg'] == '#2b2b2b'
                    
                    if is_dark:
                        # Dark mode calendar styling
                        cal = Calendar(cal_dialog, selectmode='day',
                                     year=current_dt.year, month=current_dt.month, day=current_dt.day,
                                     background='#2b2b2b',
                                     foreground='white',
                                     headersbackground='#1e1e1e',
                                     headersforeground='white',
                                     selectbackground='#0078d7',
                                     selectforeground='white',
                                     normalbackground='#2b2b2b',
                                     normalforeground='white',
                                     weekendbackground='#353535',
                                     weekendforeground='white',
                                     othermonthforeground='#666666',
                                     othermonthbackground='#2b2b2b',
                                     othermonthweforeground='#666666',
                                     othermonthwebackground='#2b2b2b')
                    else:
                        # Light mode calendar (default)
                        cal = Calendar(cal_dialog, selectmode='day',
                                     year=current_dt.year, month=current_dt.month, day=current_dt.day)
                    cal.pack(padx=10, pady=10)
                    
                    def set_end_date():
                        selected_date = cal.selection_get()
                        self.custom_end_date.set(selected_date.strftime("%m/%d/%Y"))
                        cal_dialog.destroy()
                        self.on_date_change()
                    
                    btn_frame = ttk.Frame(cal_dialog)
                    btn_frame.pack(pady=5)
                    ttk.Button(btn_frame, text="OK", command=set_end_date).pack(side=tk.LEFT, padx=5)
                    ttk.Button(btn_frame, text="Cancel", command=cal_dialog.destroy).pack(side=tk.LEFT, padx=5)
                    
                    # Position dialog next to the button
                    cal_dialog.update_idletasks()
                    button_x = end_date_frame.winfo_rootx() + end_cal_btn.winfo_x() + end_cal_btn.winfo_width()
                    button_y = end_date_frame.winfo_rooty() + end_cal_btn.winfo_y()
                    dialog_width = cal_dialog.winfo_width()
                    dialog_height = cal_dialog.winfo_height()
                    
                    # Position to the right of button, or above if not enough space
                    screen_width = self.window.winfo_screenwidth()
                    if button_x + dialog_width + 10 > screen_width:
                        # Position above button
                        cal_dialog.geometry(f"+{button_x - dialog_width // 2}+{button_y - dialog_height - 5}")
                    else:
                        # Position to the right
                        cal_dialog.geometry(f"+{button_x + 5}+{button_y}")
                    
                except Exception as e:
                    logger.error(f"Error opening calendar: {e}")
                    cal_dialog.destroy()
            
            end_cal_btn = ttk.Button(end_date_frame, text="📅", width=3, command=open_end_calendar)
            end_cal_btn.pack(side=tk.LEFT, padx=(2, 0))
        
        self.custom_end_entry.bind("<FocusOut>", lambda e: self.on_date_change())
        
        # Initially hide the custom date frame (don't pack it yet)
        # It will be shown when custom_date_range is selected
        
        # Comparison Section (only visible in comparison view)
        comparison_frame = ttk.LabelFrame(left_panel, text="Shift Comparison", padding="8")
        self.comparison_frame = comparison_frame  # Store reference to show/hide
        
        ttk.Label(comparison_frame, text="Shift 1:", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(0, 2))
        self.comparison_shift1_var = tk.StringVar()
        self.comparison_shift1_combo = ttk.Combobox(comparison_frame, state="readonly", width=25)
        self.comparison_shift1_combo.pack(fill=tk.X, pady=(0, 10))
        self.comparison_shift1_combo.bind("<<ComboboxSelected>>", lambda e: self.on_comparison_shift_selected(e))
        
        ttk.Label(comparison_frame, text="Shift 2:", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(0, 2))
        self.comparison_shift2_var = tk.StringVar()
        self.comparison_shift2_combo = ttk.Combobox(comparison_frame, state="readonly", width=25)
        self.comparison_shift2_combo.pack(fill=tk.X, pady=(0, 10))
        self.comparison_shift2_combo.bind("<<ComboboxSelected>>", lambda e: self.on_comparison_shift_selected(e))
        
        # Initially hide comparison section (only show when comparison view is selected)
        comparison_frame.pack_forget()
        
        # Shifts List Section (with delete capability)
        shifts_frame = ttk.LabelFrame(left_panel, text="All Shifts", padding="8")
        shifts_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # Scrollable list of shifts
        canvas_bg = getattr(self, 'theme_canvas_bg', 'SystemButtonFace')
        self.shifts_canvas = tk.Canvas(shifts_frame, width=210, highlightthickness=0, bg=canvas_bg)
        shifts_scrollbar = ttk.Scrollbar(shifts_frame, orient="vertical", command=self.shifts_canvas.yview)
        self.shifts_list_frame = ttk.Frame(self.shifts_canvas)
        
        self.shifts_list_frame.bind("<Configure>", 
            lambda e: self.shifts_canvas.configure(scrollregion=self.shifts_canvas.bbox("all")))
        self.shifts_canvas.create_window((0, 0), window=self.shifts_list_frame, anchor="nw")
        self.shifts_canvas.configure(yscrollcommand=shifts_scrollbar.set)
        
        self.shifts_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        shifts_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # === RIGHT PANEL (Main Content) ===
        right_panel = ttk.Frame(paned, padding="5")
        paned.add(right_panel, weight=1)
        self.right_panel = right_panel  # Store reference for projection settings
        
        # View mode toggle
        view_frame = ttk.Frame(right_panel)
        view_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(view_frame, text="View:", font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Radiobutton(view_frame, text="Efficiency", variable=self.view_mode,
                       value="efficiency", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="Compensation", variable=self.view_mode,
                       value="compensation", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Hour", variable=self.view_mode,
                       value="by_hour", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Modality", variable=self.view_mode,
                       value="by_modality", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Patient Class", variable=self.view_mode,
                       value="by_patient_class", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Study Type", variable=self.view_mode,
                       value="by_study_type", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="By Body Part", variable=self.view_mode,
                       value="by_body_part", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="All Studies", variable=self.view_mode,
                       value="all_studies", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="Comparison", variable=self.view_mode,
                       value="comparison", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(view_frame, text="Summary", variable=self.view_mode,
                       value="summary", command=self.refresh_data).pack(side=tk.LEFT, padx=5)
        
        # Period label with checkboxes for efficiency view
        period_frame = ttk.Frame(right_panel)
        period_frame.pack(fill=tk.X, pady=(0, 10))
        self.period_label = ttk.Label(period_frame, text="", font=("Arial", 12, "bold"))
        self.period_label.pack(side=tk.LEFT, anchor=tk.W)
        
        # Frame for efficiency view controls (study count mode and color coding)
        efficiency_controls_frame = ttk.Frame(period_frame)
        efficiency_controls_frame.pack(side=tk.RIGHT, anchor=tk.E)
        
        # Study count display mode (average vs total) - to the left of color coding
        self.study_count_mode_frame = ttk.Frame(efficiency_controls_frame)
        self.study_count_mode_frame.pack(side=tk.LEFT, padx=(0, 15))
        
        # Load saved value or default to "average"
        saved_study_count_mode = self.data_manager.data.get("settings", {}).get("efficiency_study_count_mode", "average")
        self.study_count_mode = tk.StringVar(value=saved_study_count_mode)  # Options: "average", "total"
        self.study_count_radio_buttons = []  # Store references to radio buttons
        
        # Checkboxes for efficiency color coding (will be shown/hidden based on view mode)
        self.efficiency_checkboxes_frame = ttk.Frame(efficiency_controls_frame)
        self.efficiency_checkboxes_frame.pack(side=tk.LEFT, anchor=tk.E)
        
        # Efficiency color coding options (created when efficiency view is shown)
        # Load saved value or default to "duration"
        saved_heatmap_mode = self.data_manager.data.get("settings", {}).get("efficiency_heatmap_mode", "duration")
        self.heatmap_mode = tk.StringVar(value=saved_heatmap_mode)  # Options: "none", "duration", "count"
        self.heatmap_radio_buttons = []  # Store references to radio buttons
        
        # Data table frame
        self.table_frame = ttk.Frame(right_panel)
        self.table_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create Treeview for data display (for all views except efficiency)
        self.tree = ttk.Treeview(self.table_frame, show="headings")
        self.tree_scrollbar_y = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
        self.tree_scrollbar_x = ttk.Scrollbar(self.table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=self.tree_scrollbar_y.set, xscrollcommand=self.tree_scrollbar_x.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree_scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        
        # Efficiency trees will be created dynamically when needed
        self.efficiency_night_tree = None
        self.efficiency_day_tree = None
        self.efficiency_frame = None
        
        # Summary frame at bottom
        summary_frame = ttk.LabelFrame(right_panel, text="Summary", padding="10")
        summary_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.summary_label = ttk.Label(summary_frame, text="", font=("Arial", 10))
        self.summary_label.pack(anchor=tk.W)
        
        # Bottom button row
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(button_frame, text="Refresh", command=self.refresh_data, width=12).pack(side=tk.LEFT, padx=2)
        
        # Partial shifts detected button (hidden by default, shown when partial shifts detected)
        self.partial_shifts_btn = ttk.Button(button_frame, text="⚠ Partial Shifts", 
                                             command=self.show_partial_shifts_dialog, width=14)
        # Don't pack yet - will be shown if partial shifts are detected
        
        # Combine shifts button (always visible)
        ttk.Button(button_frame, text="Combine Shifts", 
                  command=self.show_combine_shifts_dialog, width=14).pack(side=tk.LEFT, padx=2)
        
        ttk.Button(button_frame, text="Close", command=self.on_closing, width=12).pack(side=tk.RIGHT, padx=2)
        
        # Center frame for backup buttons
        center_frame = ttk.Frame(button_frame)
        center_frame.pack(expand=True)
        ttk.Button(center_frame, text="Backup Data", command=self.backup_study_data, width=16).pack(side=tk.LEFT, padx=2)
        ttk.Button(center_frame, text="Load Backup Data", command=self.load_backup_data, width=16).pack(side=tk.LEFT, padx=2)
        
        # Initial data load
        self.populate_shifts_list()
        self.refresh_data()
        
        # Check for partial shifts and show button if detected
        self.update_partial_shifts_button()
    
    def get_all_shifts(self) -> List[dict]:
        """Get all shifts from records, sorted by date (newest first)."""
        shifts = []
        
        # Get historical shifts from the "shifts" array
        historical_shifts = self.data_manager.data.get("shifts", [])
        for shift in historical_shifts:
            shift_copy = shift.copy()
            # Extract date from shift_start for display
            try:
                start = datetime.fromisoformat(shift.get("shift_start", ""))
                shift_copy["date"] = start.strftime("%Y-%m-%d")
            except:
                shift_copy["date"] = "Unknown"
            shifts.append(shift_copy)
        
        # Add current shift only if it's actually running (has shift_start but NO shift_end)
        current_shift = self.data_manager.data.get("current_shift", {})
        shift_is_active = current_shift.get("shift_start") and not current_shift.get("shift_end")
        if current_shift.get("records") and shift_is_active:
            shifts.append({
                "date": "current",
                "shift_start": current_shift.get("shift_start", ""),
                "shift_end": current_shift.get("shift_end", ""),
                "records": current_shift.get("records", []),
                "is_current": True
            })
        
        # Sort by shift_start (newest first)
        def sort_key(s):
            if s.get("is_current"):
                return datetime.max
            try:
                return datetime.fromisoformat(s.get("shift_start", ""))
            except:
                return datetime.min
        
        shifts.sort(key=sort_key, reverse=True)
        return shifts
    
    def populate_shifts_list(self):
        """Populate the shifts list in left panel."""
        # Clear existing
        for widget in self.shifts_list_frame.winfo_children():
            widget.destroy()
        
        shifts = self.get_all_shifts()
        now = datetime.now()
        
        for i, shift in enumerate(shifts):
            shift_frame = ttk.Frame(self.shifts_list_frame)
            shift_frame.pack(fill=tk.X, pady=1)
            
            # Format shift label
            if shift.get("is_current"):
                label_text = "Current Shift"
            else:
                try:
                    start = datetime.fromisoformat(shift.get("shift_start", ""))
                    
                    # Round down time to nearest hour (e.g., 10:23pm -> 10pm)
                    start_rounded = start.replace(minute=0, second=0, microsecond=0)
                    
                    # Calculate days ago
                    days_diff = (now.date() - start_rounded.date()).days
                    
                    # Format label based on how recent it is
                    if days_diff == 0:
                        label_text = "Today"
                    elif days_diff == 1:
                        label_text = "Yesterday"
                    elif days_diff <= 7:
                        # Show day name + xd ago (e.g., "Friday 2d ago")
                        day_name = start_rounded.strftime("%A")
                        label_text = f"{day_name} {days_diff}d ago"
                    else:
                        # Older shifts: show date with rounded time
                        label_text = start_rounded.strftime("%m/%d %I%p").lower().replace(":00", "")
                except:
                    label_text = shift.get("date", "Unknown")
            
            # Study count and RVU
            records = shift.get("records", [])
            count = len(records)
            total_rvu = sum(r.get("rvu", 0) for r in records)
            
            # Shift button (clickable frame with left-justified name and right-justified count/RVU)
            btn_frame = ttk.Frame(shift_frame)
            btn_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
            
            # Left-justified shift name
            name_label = ttk.Label(btn_frame, text=label_text, anchor=tk.W, cursor="hand2")
            name_label.pack(side=tk.LEFT)
            
            # Spacer to push count to the right
            spacer = ttk.Frame(btn_frame)
            spacer.pack(side=tk.LEFT, fill=tk.X, expand=True)
            
            # Right-justified count and RVU
            count_label = ttk.Label(btn_frame, text=f"({count}, {total_rvu:.1f} RVU)", anchor=tk.E, cursor="hand2")
            count_label.pack(side=tk.RIGHT)
            
            # Make the entire frame clickable
            def select_shift_cmd(event, idx=i):
                self.select_shift(idx)
            btn_frame.bind("<Button-1>", select_shift_cmd)
            name_label.bind("<Button-1>", select_shift_cmd)
            count_label.bind("<Button-1>", select_shift_cmd)
            spacer.bind("<Button-1>", select_shift_cmd)
            
            # Delete button (subtle, small)
            if not shift.get("is_current"):
                colors = self.app.get_theme_colors()
                del_btn = tk.Label(shift_frame, text="×", font=("Arial", 8), 
                                   fg=colors["delete_btn_fg"], bg=colors["delete_btn_bg"],
                                   cursor="hand2", width=2)
                del_btn.shift_idx = i
                del_btn.bind("<Button-1>", lambda e, btn=del_btn: self.confirm_delete_shift(btn.shift_idx))
                del_btn.bind("<Enter>", lambda e, btn=del_btn: btn.config(bg=colors["delete_btn_hover"]))
                del_btn.bind("<Leave>", lambda e, btn=del_btn: btn.config(bg=colors["delete_btn_bg"]))
                del_btn.pack(side=tk.LEFT)
    
    def select_shift(self, shift_index: int):
        """Select a specific shift from the list."""
        self.selected_shift_index = shift_index
        self.selected_period.set("specific_shift")
        self.refresh_data()
    
    def confirm_delete_shift(self, shift_index: int):
        """Confirm and delete a shift."""
        shifts = self.get_all_shifts()
        if shift_index >= len(shifts):
            return
        
        shift = shifts[shift_index]
        if shift.get("is_current"):
            return  # Can't delete current shift from here
        
        # Format confirmation message
        try:
            start = datetime.fromisoformat(shift.get("shift_start", ""))
            date_str = start.strftime("%B %d, %Y at %I:%M %p")
        except:
            date_str = shift.get("date", "Unknown date")
        
        records = shift.get("records", [])
        rvu = sum(r.get("rvu", 0) for r in records)
        
        result = messagebox.askyesno(
            "Delete Shift?",
            f"Delete shift from {date_str}?\n\n"
            f"This will remove {len(records)} studies ({rvu:.1f} RVU).\n\n"
            "This action cannot be undone.",
            parent=self.window
        )
        
        if result:
            self.delete_shift(shift_index)
    
    def delete_shift(self, shift_index: int):
        """Delete a shift from records."""
        shifts = self.get_all_shifts()
        if shift_index >= len(shifts):
            return
        
        shift = shifts[shift_index]
        if shift.get("is_current"):
            return
        
        shift_start = shift.get("shift_start")
        
        # Delete from database first (find by shift_start)
        try:
            cursor = self.data_manager.db.conn.cursor()
            cursor.execute('SELECT id FROM shifts WHERE shift_start = ? AND is_current = 0', (shift_start,))
            row = cursor.fetchone()
            if row:
                self.data_manager.db.delete_shift(row[0])
        except Exception as e:
            logger.error(f"Error deleting shift from database: {e}")
        
        # Find and remove from the in-memory shifts array
        historical_shifts = self.data_manager.data.get("shifts", [])
        for i, s in enumerate(historical_shifts):
            if s.get("shift_start") == shift_start:
                historical_shifts.pop(i)
                # Also update records_data
                if "shifts" in self.data_manager.records_data:
                    for j, rs in enumerate(self.data_manager.records_data["shifts"]):
                        if rs.get("shift_start") == shift_start:
                            self.data_manager.records_data["shifts"].pop(j)
                            break
                logger.info(f"Deleted shift starting {shift_start}")
                break
        
        # Refresh UI
        self.populate_shifts_list()
        self.refresh_data()
    
    def _format_date_range(self, start: datetime, end: datetime) -> str:
        """Format a date range as MM/DD/YYYY - MM/DD/YYYY."""
        start_str = start.strftime("%m/%d/%Y")
        end_str = end.strftime("%m/%d/%Y")
        return f"{start_str} - {end_str}"
    
    def get_records_for_period(self) -> Tuple[List[dict], str]:
        """Get records for the selected period. Returns (records, period_description)."""
        period = self.selected_period.get()
        now = datetime.now()
        
        if period == "current_shift":
            records = self.data_manager.data.get("current_shift", {}).get("records", [])
            # Get shift start time if available
            shift_start_str = self.data_manager.data.get("current_shift", {}).get("shift_start")
            if shift_start_str:
                try:
                    start = datetime.fromisoformat(shift_start_str)
                    date_range = self._format_date_range(start, now)
                    return records, f"Current Shift - {date_range}"
                except:
                    pass
            return records, "Current Shift"
        
        elif period == "prior_shift":
            shifts = self.get_all_shifts()
            # Find the first non-current shift
            for shift in shifts:
                if not shift.get("is_current"):
                    records = shift.get("records", [])
                    try:
                        start = datetime.fromisoformat(shift.get("shift_start", ""))
                        end_str = shift.get("shift_end", "")
                        if end_str:
                            end = datetime.fromisoformat(end_str)
                        else:
                            end = start + timedelta(hours=12)  # Default end if not available
                        date_range = self._format_date_range(start, end)
                        return records, f"Prior Shift - {date_range}"
                    except:
                        pass
                    return records, f"Prior Shift ({shift.get('date', '')})"
            return [], "Prior Shift (none found)"
        
        elif period == "specific_shift":
            shifts = self.get_all_shifts()
            if self.selected_shift_index is not None and self.selected_shift_index < len(shifts):
                shift = shifts[self.selected_shift_index]
                if shift.get("is_current"):
                    # Same as current shift logic
                    shift_start_str = self.data_manager.data.get("current_shift", {}).get("shift_start")
                    if shift_start_str:
                        try:
                            start = datetime.fromisoformat(shift_start_str)
                            date_range = self._format_date_range(start, now)
                            return shift.get("records", []), f"Current Shift - {date_range}"
                        except:
                            pass
                    return shift.get("records", []), "Current Shift"
                try:
                    start = datetime.fromisoformat(shift.get("shift_start", ""))
                    end_str = shift.get("shift_end", "")
                    if end_str:
                        end = datetime.fromisoformat(end_str)
                    else:
                        end = start + timedelta(hours=12)
                    date_range = self._format_date_range(start, end)
                    desc = start.strftime("%B %d, %Y %I:%M %p")
                    return shift.get("records", []), f"Shift: {desc} - {date_range}"
                except:
                    desc = shift.get("date", "")
                    return shift.get("records", []), f"Shift: {desc}"
            return [], "No shift selected"
        
        elif period == "this_work_week":
            # Current work week: Monday at typical shift start to next Monday at shift end
            start, end = self._get_work_week_range(now, "this")
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"This Work Week - {date_range}"
        
        elif period == "last_work_week":
            # Previous work week: Monday at typical shift start to next Monday at shift end
            start, end = self._get_work_week_range(now, "last")
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"Last Work Week - {date_range}"
        
        elif period == "all_time":
            # All records from all time
            start = datetime.min.replace(year=2000)
            records = self._get_records_in_range(start, now)
            date_range = self._format_date_range(start, now)
            return records, f"All Time - {date_range}"
        
        elif period == "projection":
            # Projection - return empty records for now, projection will use historical data
            # This is handled separately in _display_projection
            return [], "Monthly Projection"
        elif period == "custom_date_range":
            # Custom date range - get dates from entry fields
            try:
                start_str = self.custom_start_date.get().strip()
                end_str = self.custom_end_date.get().strip()
                # Parse dates (MM/DD/YYYY format)
                start = datetime.strptime(start_str, "%m/%d/%Y")
                end = datetime.strptime(end_str, "%m/%d/%Y")
                # Set time to start/end of day
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
                
                # Validate: start should be before end
                if start > end:
                    return [], f"Custom Date Range - Invalid (start date must be before end date)"
                
                records = self._get_records_in_range(start, end)
                date_range = self._format_date_range(start, end)
                return records, f"Custom Date Range - {date_range}"
            except ValueError as e:
                return [], f"Custom Date Range - Invalid date format (use MM/DD/YYYY)"
            except Exception as e:
                return [], f"Custom Date Range - Error: {str(e)}"
        
        elif period == "this_month":
            # This month: 1st of current month to end of current month
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            # End of current month: first day of next month minus 1 day, at 23:59:59
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)
            end = (next_month - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"This Month - {date_range}"
        
        elif period == "last_month":
            # Last month: 1st of last month to last day of last month (end of last month)
            if now.month == 1:
                # Last month was December of previous year
                start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
                # End of December: first day of current month (Jan 1) minus 1 day
                end = now.replace(month=1, day=1) - timedelta(days=1)
            else:
                # Last month is previous month of current year
                start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
                # End of last month: first day of current month minus 1 day
                end = now.replace(day=1) - timedelta(days=1)
            # Set end to last moment of the last day
            end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"Last Month - {date_range}"
        
        elif period == "last_3_months":
            # Last 3 months: 1st of the month 3 months ago to end of current month
            current_month = now.month
            current_year = now.year
            
            # Calculate month 3 months ago
            months_back = 3
            target_month = current_month - months_back
            target_year = current_year
            
            while target_month <= 0:
                target_month += 12
                target_year -= 1
            
            start = datetime(target_year, target_month, 1, 0, 0, 0, 0)
            
            # End of current month
            if now.month == 12:
                next_month = now.replace(year=now.year + 1, month=1, day=1)
            else:
                next_month = now.replace(month=now.month + 1, day=1)
            end = (next_month - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
            
            records = self._get_records_in_range(start, end)
            date_range = self._format_date_range(start, end)
            return records, f"Last 3 Months - {date_range}"
        
        elif period == "last_year":
            start = now - timedelta(days=365)
            records = self._get_records_in_range(start, now)
            date_range = self._format_date_range(start, now)
            return records, f"Last Year - {date_range}"
        
        return [], "Unknown period"
    
    def _get_work_week_range(self, target_date: datetime, which_week: str = "last") -> Tuple[datetime, datetime]:
        """Calculate work week range based on typical shift times.
        
        Uses dynamically calculated typical_shift_start_hour and typical_shift_end_hour.
        E.g., Monday at shift start to next Monday at shift end.
        
        Args:
            target_date: Reference date
            which_week: "this" for current work week, "last" for previous work week
            
        Returns:
            Tuple of (start_datetime, end_datetime) for the work week
        """
        # Get typical shift times from the app object
        start_hour = self.app.typical_shift_start_hour if hasattr(self.app, 'typical_shift_start_hour') else 23
        end_hour = self.app.typical_shift_end_hour if hasattr(self.app, 'typical_shift_end_hour') else 8
        
        # Find the Monday that started the current work week
        days_since_monday = target_date.weekday()  # Monday = 0
        
        # Determine which work week we're in
        if days_since_monday == 0 and target_date.hour < end_hour:
            # It's Monday before shift end - we're still in the previous work week
            work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=8)
        else:
            # After shift end Monday or later in week - find the Monday that started this week
            if days_since_monday == 0:
                # It's Monday after shift end - current work week started last Monday
                work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
            else:
                # It's Tuesday-Sunday - find the most recent Monday
                work_week_start_monday = target_date.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
        
        # Work week starts Monday at typical shift start hour
        work_week_start = work_week_start_monday.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        # Work week ends next Monday at typical shift end hour
        work_week_end = (work_week_start_monday + timedelta(days=7)).replace(hour=end_hour, minute=0, second=0, microsecond=0)
        
        if which_week == "last":
            # Go back one week (7 days)
            work_week_start = work_week_start - timedelta(days=7)
            work_week_end = work_week_end - timedelta(days=7)
        
        return work_week_start, work_week_end
    
    def _get_records_in_range(self, start: datetime, end: datetime) -> List[dict]:
        """Get all records within a date range from the database."""
        # Use database query for accurate results
        start_str = start.isoformat()
        end_str = end.isoformat()
        records = self.data_manager.db.get_records_in_date_range(start_str, end_str)
        return records
    
    def _expand_multi_accession_records(self, records: List[dict]) -> List[dict]:
        """Expand multi-accession records into individual modality records.
        
        For records with study_type like "Multiple XR", expand them into
        multiple XR records based on accession_count.
        """
        expanded_records = []
        for record in records:
            study_type = record.get("study_type", "Unknown")
            
            # Check if this is a multi-accession record
            if study_type.startswith("Multiple ") and record.get("is_multi_accession", False):
                # Extract the actual modality (e.g., "XR" from "Multiple XR")
                modality = study_type.replace("Multiple ", "").strip()
                accession_count = record.get("accession_count", 1)
                
                # Expand into multiple records with the actual modality
                # Split RVU and duration across the accessions
                total_rvu = record.get("rvu", 0)
                total_duration = record.get("duration_seconds", 0)
                rvu_per_study = total_rvu / accession_count if accession_count > 0 else 0
                duration_per_study = total_duration / accession_count if accession_count > 0 else 0
                
                # Get individual data if available (for newer records)
                individual_procedures = record.get("individual_procedures", [])
                individual_study_types = record.get("individual_study_types", [])
                individual_rvus = record.get("individual_rvus", [])
                individual_accessions = record.get("individual_accessions", [])
                
                # Check if we have individual data stored
                has_individual_data = (individual_study_types and individual_rvus and 
                                     len(individual_study_types) == accession_count and 
                                     len(individual_rvus) == accession_count)
                
                for i in range(accession_count):
                    expanded_record = record.copy()
                    
                    if has_individual_data:
                        # Use stored individual data
                        expanded_record["study_type"] = individual_study_types[i]
                        expanded_record["rvu"] = individual_rvus[i]
                        if individual_procedures and i < len(individual_procedures):
                            expanded_record["procedure"] = individual_procedures[i]
                        if individual_accessions and i < len(individual_accessions):
                            expanded_record["accession"] = individual_accessions[i]
                    else:
                        # Fallback: try to classify individual procedures to get study types and RVUs
                        if individual_procedures and i < len(individual_procedures):
                            # Classify the individual procedure to get its study type and RVU
                            # match_study_type is defined at module level in this same file
                            rvu_table = self.data_manager.data.get("rvu_table", {})
                            classification_rules = self.data_manager.data.get("classification_rules", {})
                            direct_lookups = self.data_manager.data.get("direct_lookups", {})
                            
                            if not rvu_table:
                                logger.warning(f"Cannot classify procedure '{individual_procedures[i]}' - no RVU table loaded")
                            
                            procedure = individual_procedures[i]
                            # Call match_study_type which is defined at module level
                            study_type, rvu = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
                            
                            expanded_record["study_type"] = study_type
                            expanded_record["rvu"] = rvu
                            expanded_record["procedure"] = procedure
                        else:
                            # Fallback to generic modality and split RVU
                            expanded_record["study_type"] = modality
                            expanded_record["rvu"] = rvu_per_study
                            # Fall back to showing "1/3", "2/3", etc.
                            original_procedure = record.get("procedure", f"Multiple {modality}")
                            base_procedure = original_procedure.split(" (")[0] if " (" in original_procedure else original_procedure
                            expanded_record["procedure"] = f"{base_procedure} ({i+1}/{accession_count})"
                    
                    expanded_record["duration_seconds"] = duration_per_study
                    expanded_record["is_multi_accession"] = False  # Mark as individual now
                    
                    expanded_records.append(expanded_record)
            else:
                # Regular record, keep as-is
                expanded_records.append(record)
        
        return expanded_records
    
    def on_custom_date_selected(self):
        """Handle when custom date range radio is selected."""
        # Show the custom date frame
        self.custom_date_frame.pack(fill=tk.X, pady=(5, 0))
        # Update the window to ensure DateEntry widgets render properly
        self.window.update_idletasks()
        self.refresh_data()
    
    def on_date_change(self):
        """Handle when custom date entry fields are changed."""
        # Only refresh if custom date range is selected
        if self.selected_period.get() == "custom_date_range":
            self.refresh_data()
    
    def _count_shifts_in_period(self) -> int:
        """Count the number of unique shifts in the selected period."""
        period = self.selected_period.get()
        now = datetime.now()
        
        if period == "current_shift":
            # Current shift counts as 1
            if self.data_manager.data.get("current_shift", {}).get("shift_start"):
                return 1
            return 0
        
        elif period == "prior_shift":
            # Prior shift counts as 1
            shifts = self.get_all_shifts()
            for shift in shifts:
                if not shift.get("is_current"):
                    return 1
            return 0
        
        elif period == "specific_shift":
            # Specific shift counts as 1
            return 1
        
        elif period in ["this_work_week", "last_work_week"]:
            # Count shifts in the work week range
            which_week = "this" if period == "this_work_week" else "last"
            start, end = self._get_work_week_range(now, which_week)
            return self._count_shifts_in_range(start, end)
        
        elif period == "all_time":
            # Count all shifts
            start = datetime.min.replace(year=2000)
            return self._count_shifts_in_range(start, now)
        
        elif period == "custom_date_range":
            # Count shifts in custom date range
            try:
                start_str = self.custom_start_date.get().strip()
                end_str = self.custom_end_date.get().strip()
                start = datetime.strptime(start_str, "%m/%d/%Y")
                end = datetime.strptime(end_str, "%m/%d/%Y")
                start = start.replace(hour=0, minute=0, second=0, microsecond=0)
                end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
                if start > end:
                    return 0
                return self._count_shifts_in_range(start, end)
            except:
                return 0
        
        elif period in ["this_month", "last_month"]:
            # Count shifts in month range
            if period == "this_month":
                start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                # End of current month
                if now.month == 12:
                    end = now.replace(year=now.year + 1, month=1, day=1, hour=23, minute=59, second=59, microsecond=999999) - timedelta(days=1)
                else:
                    end = now.replace(month=now.month + 1, day=1, hour=23, minute=59, second=59, microsecond=999999) - timedelta(days=1)
            else:  # last_month
                if now.month == 1:
                    start = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
                    end = now.replace(day=1, hour=23, minute=59, second=59, microsecond=999999) - timedelta(days=1)
                else:
                    start = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
                    end = now.replace(month=now.month, day=1, hour=23, minute=59, second=59, microsecond=999999) - timedelta(days=1)
            return self._count_shifts_in_range(start, end)
        
        elif period == "last_3_months":
            # Count shifts in last 3 months
            start = now - timedelta(days=90)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            return self._count_shifts_in_range(start, now)
        
        elif period == "last_year":
            # Count shifts in last year
            start = now.replace(year=now.year - 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return self._count_shifts_in_range(start, now)
        
        return 0
    
    def _count_shifts_in_range(self, start: datetime, end: datetime) -> int:
        """Count unique shifts that have records within the date range."""
        shift_ids = set()
        
        # Check current shift
        current_shift = self.data_manager.data.get("current_shift", {})
        if current_shift.get("shift_start"):
            try:
                shift_start = datetime.fromisoformat(current_shift.get("shift_start", ""))
                # Check if shift has any records in the range
                for record in current_shift.get("records", []):
                    try:
                        rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                        if start <= rec_time <= end:
                            shift_ids.add("current")
                            break
                    except:
                        pass
            except:
                pass
        
        # Check historical shifts
        for shift in self.data_manager.data.get("shifts", []):
            try:
                shift_start_str = shift.get("shift_start", "")
                if not shift_start_str:
                    continue
                shift_start = datetime.fromisoformat(shift_start_str)
                # Check if shift has any records in the range
                for record in shift.get("records", []):
                    try:
                        rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                        if start <= rec_time <= end:
                            # Use shift_start as unique identifier
                            shift_ids.add(shift_start_str)
                            break
                    except:
                        pass
            except:
                pass
        
        return len(shift_ids)
    
    def refresh_data(self):
        """Refresh the data display based on current selections."""
        current_period = self.selected_period.get()
        
        # Show/hide custom date frame based on selection
        if current_period == "custom_date_range":
            self.custom_date_frame.pack(fill=tk.X, pady=(5, 0))
            # Update the window to ensure widgets render properly
            self.window.update_idletasks()
        else:
            self.custom_date_frame.pack_forget()
        
        records, period_desc = self.get_records_for_period()
        
        # For efficiency view, add shift count in parentheses after date range
        if self.view_mode.get() == "efficiency":
            # Count unique shifts in the date range
            shift_count = self._count_shifts_in_period()
            if shift_count > 0:
                # Find where the date range ends (after the last dash)
                if " - " in period_desc:
                    # Add shift count after the date range
                    period_desc = f"{period_desc} ({shift_count} shift{'s' if shift_count != 1 else ''})"
                else:
                    # No date range, just add shift count
                    period_desc = f"{period_desc} ({shift_count} shift{'s' if shift_count != 1 else ''})"
        
        # Set period label (override for comparison mode later if needed)
        self.period_label.config(text=period_desc)
        
        # Expand multi-accession records into individual modality records for statistics
        records = self._expand_multi_accession_records(records)
        
        view_mode = self.view_mode.get()
        
        # Override period label for comparison mode
        if view_mode == "comparison":
            self.period_label.config(text="Shift Comparison")
        
        # Hide tree for all views (all use Canvas now)
        self.tree.pack_forget()
        self.tree_scrollbar_y.pack_forget()
        self.tree_scrollbar_x.pack_forget()
        
        # Hide all Canvas tables and frames (they will be recreated/shown by each view)
        canvas_tables = ['_summary_table', '_all_studies_table', '_by_modality_table', 
                        '_by_patient_class_table', '_by_study_type_table', '_by_body_part_table', 
                        '_by_hour_table', '_compensation_table', '_projection_table']
        for table_attr in canvas_tables:
            if hasattr(self, table_attr):
                try:
                    table = getattr(self, table_attr)
                    if hasattr(table, 'frame'):
                        table.frame.pack_forget()
                except:
                    pass
        
        # Hide _all_studies_frame (separate frame for all studies view)
        if hasattr(self, '_all_studies_frame'):
            try:
                self._all_studies_frame.pack_forget()
            except:
                pass
        
        # Hide efficiency frame
        if self.efficiency_frame:
            try:
                self.efficiency_frame.pack_forget()
            except:
                pass
        
        # Hide compensation frame
        if hasattr(self, 'compensation_frame') and self.compensation_frame:
            try:
                self.compensation_frame.pack_forget()
            except:
                pass
        
        # Show/hide projection section in left panel based on view mode
        if hasattr(self, 'projection_frame'):
            if view_mode == "compensation":
                # Show projection section when in compensation view
                try:
                    # Find historical frame to pack before it
                    left_panel = self.projection_frame.master
                    for widget in left_panel.winfo_children():
                        if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "Historical":
                            self.projection_frame.pack(fill=tk.X, pady=(0, 10), before=widget)
                            break
                except:
                    pass
            else:
                # Hide projection section in other views (efficiency, etc.)
                try:
                    self.projection_frame.pack_forget()
                    # If projection was selected, switch to a default period
                    if current_period == "projection":
                        self.selected_period.set("current_shift")
                        records, period_desc = self.get_records_for_period()
                except:
                    pass
        
        # Show/hide projection settings frame in right panel based on mode
        if hasattr(self, 'projection_settings_frame'):
            try:
                if view_mode == "compensation" and current_period == "projection":
                    # Settings will be shown in _display_projection
                    pass
                else:
                    self.projection_settings_frame.pack_forget()
            except:
                pass
        
        # Show/hide comparison section in left panel based on view mode
        if hasattr(self, 'comparison_frame'):
            if view_mode == "comparison":
                # Show comparison section when in comparison view
                try:
                    # Find shifts list frame to pack before it
                    left_panel = self.comparison_frame.master
                    for widget in left_panel.winfo_children():
                        if isinstance(widget, ttk.LabelFrame) and widget.cget("text") == "All Shifts":
                            self.comparison_frame.pack(fill=tk.X, pady=(0, 10), before=widget)
                            break
                    # Populate comparison comboboxes only if not already populated
                    if not hasattr(self, '_comparison_shifts_populated') or not self._comparison_shifts_populated:
                        self._populate_comparison_shifts(preserve_selection=False)
                        self._comparison_shifts_populated = True
                except Exception as e:
                    logger.error(f"Error showing comparison frame: {e}")
            else:
                # Hide comparison section in other views
                try:
                    self.comparison_frame.pack_forget()
                except:
                    pass
                
                # Clean up ALL comparison-related stored data
                cleanup_attrs = [
                    '_comparison_canvas_widgets',
                    '_comparison_data1',
                    '_comparison_data2', 
                    '_comparison_scroll_canvas',
                    '_comparison_scrollable_frame',
                    '_comparison_mousewheel_canvas',
                    '_comparison_mousewheel_frame',
                    '_comparison_mousewheel_callback',
                    '_comparison_controls_frame',
                    '_comparison_content_frame',
                    '_comparison_modality_frame'
                ]
                
                for attr in cleanup_attrs:
                    if hasattr(self, attr):
                        try:
                            # Set to None so it gets recreated when switching back
                            setattr(self, attr, None)
                        except:
                            pass
        
        # Show/hide efficiency checkboxes based on view mode
        if view_mode == "efficiency":
            # Show study count mode frame
            self.study_count_mode_frame.pack(side=tk.LEFT, padx=(0, 15))
            
            # Make sure study count mode frame is visible and create radio buttons if needed
            if not self.study_count_radio_buttons:
                # Helper function to save study count mode and refresh
                def save_study_count_mode():
                    self.data_manager.data.setdefault("settings", {})["efficiency_study_count_mode"] = self.study_count_mode.get()
                    self.data_manager.save(save_records=False)
                    # Force immediate redraw of efficiency view if it's currently displayed
                    if hasattr(self, '_efficiency_redraw_functions') and self._efficiency_redraw_functions:
                        for redraw_func in self._efficiency_redraw_functions:
                            try:
                                redraw_func()  # This will read the updated radio button value
                            except Exception as e:
                                logger.debug(f"Error calling redraw function: {e}")
                                pass
                    # Always do a full refresh to ensure everything is updated
                    self.refresh_data()
                
                # Create radio buttons for study count display mode
                ttk.Label(self.study_count_mode_frame, text="Study Count:", font=('Arial', 9)).pack(side=tk.LEFT, padx=(0, 5))
                
                self.study_count_radio_buttons.append(ttk.Radiobutton(
                    self.study_count_mode_frame,
                    text="Average",
                    variable=self.study_count_mode,
                    value="average",
                    command=save_study_count_mode
                ))
                self.study_count_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
                
                self.study_count_radio_buttons.append(ttk.Radiobutton(
                    self.study_count_mode_frame,
                    text="Total",
                    variable=self.study_count_mode,
                    value="total",
                    command=save_study_count_mode
                ))
                self.study_count_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
            
            # Make sure radio buttons frame is visible and create radio buttons if needed
            if not self.heatmap_radio_buttons:
                # Helper function to save heatmap mode and refresh
                def save_heatmap_mode():
                    self.data_manager.data.setdefault("settings", {})["efficiency_heatmap_mode"] = self.heatmap_mode.get()
                    self.data_manager.save(save_records=False)
                    # Redraw efficiency view if it's currently displayed
                    if hasattr(self, '_efficiency_redraw_functions') and self._efficiency_redraw_functions:
                        for redraw_func in self._efficiency_redraw_functions:
                            try:
                                redraw_func()
                            except:
                                pass
                    else:
                        # Fallback to full refresh if redraw functions not available
                        self.refresh_data()
                
                # Create radio buttons for heat map mode
                # Pack label on LEFT first
                ttk.Label(self.efficiency_checkboxes_frame, text="Color Coding:", font=('Arial', 9)).pack(side=tk.LEFT, padx=(0, 5))
                
                # Pack buttons on LEFT in order: None, Duration, Study Count
                self.heatmap_radio_buttons.append(ttk.Radiobutton(
                    self.efficiency_checkboxes_frame,
                    text="None",
                    variable=self.heatmap_mode,
                    value="none",
                    command=save_heatmap_mode
                ))
                self.heatmap_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
                
                self.heatmap_radio_buttons.append(ttk.Radiobutton(
                    self.efficiency_checkboxes_frame,
                    text="Duration",
                    variable=self.heatmap_mode,
                    value="duration",
                    command=save_heatmap_mode
                ))
                self.heatmap_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
                
                self.heatmap_radio_buttons.append(ttk.Radiobutton(
                    self.efficiency_checkboxes_frame,
                    text="Study Count",
                    variable=self.heatmap_mode,
                    value="count",
                    command=save_heatmap_mode
                ))
                self.heatmap_radio_buttons[-1].pack(side=tk.LEFT, padx=2)
            self.efficiency_checkboxes_frame.pack(side=tk.LEFT, anchor=tk.E)
        else:
            if self.efficiency_checkboxes_frame:
                self.efficiency_checkboxes_frame.pack_forget()
            if hasattr(self, 'study_count_mode_frame'):
                self.study_count_mode_frame.pack_forget()
        
        if view_mode == "by_hour":
            self._display_by_hour(records)
        elif view_mode == "by_modality":
            self._display_by_modality(records)
        elif view_mode == "by_patient_class":
            self._display_by_patient_class(records)
        elif view_mode == "by_study_type":
            self._display_by_study_type(records)
        elif view_mode == "by_body_part":
            self._display_by_body_part(records)
        elif view_mode == "all_studies":
            self._display_all_studies(records)
        elif view_mode == "efficiency":
            self._display_efficiency(records)
        elif view_mode == "compensation":
            if self.selected_period.get() == "projection":
                self._display_projection(records)
            else:
                self._display_compensation(records)
        elif view_mode == "summary":
            self._display_summary(records)
        elif view_mode == "comparison":
            self._display_comparison()
            # Summary is handled within _display_comparison, skip default summary update
            return
        
        # Update summary
        total_studies = len(records)
        total_rvu = sum(r.get("rvu", 0) for r in records)
        avg_rvu = total_rvu / total_studies if total_studies > 0 else 0
        
        self.summary_label.config(
            text=f"Total: {total_studies} studies  |  {total_rvu:.1f} RVU  |  Avg: {avg_rvu:.2f} RVU/study"
        )
    
    def _display_by_hour(self, records: List[dict]):
        """Display data broken down by hour using Canvas table."""
        # Group by hour and collect all modalities first
        hour_data = {}
        all_modalities = {}
        for record in records:
            try:
                rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                hour = rec_time.hour
            except:
                continue
                
            if hour not in hour_data:
                hour_data[hour] = {"studies": 0, "rvu": 0, "modalities": {}}
            
            hour_data[hour]["studies"] += 1
            hour_data[hour]["rvu"] += record.get("rvu", 0)
            
            # Track modality
            study_type = record.get("study_type", "Unknown")
            modality = study_type.split()[0] if study_type else "Unknown"
            hour_data[hour]["modalities"][modality] = hour_data[hour]["modalities"].get(modality, 0) + 1
            all_modalities[modality] = all_modalities.get(modality, 0) + 1
        
        # Sort modalities by name for consistent column order
        sorted_modalities = sorted(all_modalities.keys())
        
        # Build dynamic columns: Hour, Studies, RVU, Avg/Study, then one column per modality
        columns = [
            {'name': 'hour', 'width': 120, 'text': 'Hour', 'sortable': True},
            {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': True},
            {'name': 'rvu', 'width': 80, 'text': 'RVU', 'sortable': True},
            {'name': 'avg_rvu', 'width': 80, 'text': 'Avg/Study', 'sortable': True}
        ]
        for modality in sorted_modalities:
            columns.append({'name': modality, 'width': 70, 'text': modality, 'sortable': True})
        
        # Clear/create Canvas table
        if hasattr(self, '_by_hour_table'):
            try:
                self._by_hour_table.frame.pack_forget()
                self._by_hour_table.frame.destroy()
            except:
                pass
            delattr(self, '_by_hour_table')
        
        self._by_hour_table = CanvasTable(self.table_frame, columns, app=self.app)
        # Ensure table is visible
        self._by_hour_table.frame.pack_forget()  # Remove any existing packing
        self._by_hour_table.pack(fill=tk.BOTH, expand=True)
        
        # Calculate totals
        total_studies = sum(d["studies"] for d in hour_data.values())
        total_rvu = sum(d["rvu"] for d in hour_data.values())
        
        # Find the earliest time_performed to determine shift start hour
        start_hour = None
        if records:
            earliest_time = None
            for record in records:
                try:
                    rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                    if earliest_time is None or rec_time < earliest_time:
                        earliest_time = rec_time
                except:
                    continue
            if earliest_time:
                start_hour = earliest_time.hour
        
        # Sort hours starting from shift start hour, wrapping around at 24
        if start_hour is not None and hour_data:
            # Create a sorted list starting from start_hour
            sorted_hours = []
            for offset in range(24):
                hour = (start_hour + offset) % 24
                if hour in hour_data:
                    sorted_hours.append(hour)
        else:
            # Fallback to regular chronological sort if no start hour found
            sorted_hours = sorted(hour_data.keys())
        
        # Display hours in order
        for hour in sorted_hours:
            data = hour_data[hour]
            # Format hour
            hour_12 = hour % 12 or 12
            am_pm = "AM" if hour < 12 else "PM"
            next_hour = (hour + 1) % 24
            next_12 = next_hour % 12 or 12
            next_am_pm = "AM" if next_hour < 12 else "PM"
            hour_str = f"{hour_12}{am_pm}-{next_12}{next_am_pm}"
            
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            
            # Build row cells
            row_cells = {
                'hour': hour_str,
                'studies': str(data["studies"]),
                'rvu': f"{data['rvu']:.1f}",
                'avg_rvu': f"{avg_rvu:.2f}"
            }
            
            # Add count for each modality
            for modality in sorted_modalities:
                count = data["modalities"].get(modality, 0)
                row_cells[modality] = str(count) if count > 0 else ""
            
            self._by_hour_table.add_row(row_cells)
        
        # Add totals row
        if hour_data:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            total_row = {
                'hour': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}"
            }
            # Add total counts for each modality
            for modality in sorted_modalities:
                total_count = all_modalities[modality]
                total_row[modality] = str(total_count) if total_count > 0 else ""
            self._by_hour_table.add_row(total_row, is_total=True)
        
        # Update display once after all rows are added
        self._by_hour_table.update_data()
    
    def _display_by_modality(self, records: List[dict]):
        """Display data broken down by modality using Canvas table."""
        # Clear/create Canvas table
        if hasattr(self, '_by_modality_table'):
            try:
                self._by_modality_table.clear()
            except:
                if hasattr(self, '_by_modality_table'):
                    self._by_modality_table.frame.pack_forget()
                    self._by_modality_table.frame.destroy()
                    delattr(self, '_by_modality_table')
        
        if not hasattr(self, '_by_modality_table'):
            columns = [
                {'name': 'modality', 'width': 100, 'text': 'Modality', 'sortable': True},
                {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': True},
                {'name': 'rvu', 'width': 80, 'text': 'RVU', 'sortable': True},
                {'name': 'avg_rvu', 'width': 80, 'text': 'Avg/Study', 'sortable': True},
                {'name': 'pct_studies', 'width': 80, 'text': '% Studies', 'sortable': True},
                {'name': 'pct_rvu', 'width': 80, 'text': '% RVU', 'sortable': True}
            ]
            self._by_modality_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._by_modality_table.frame.pack_forget()  # Remove any existing packing
        self._by_modality_table.pack(fill=tk.BOTH, expand=True)
        self._by_modality_table.clear()
        
        # Group by modality
        modality_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            study_type = record.get("study_type", "Unknown")
            modality = study_type.split()[0] if study_type else "Unknown"
            rvu = record.get("rvu", 0)
            
            # Handle any remaining "Multiple" modality from old records
            # Extract the actual modality (e.g., "XR" from "Multiple XR")
            if modality == "Multiple" and len(study_type.split()) > 1:
                modality = study_type.split()[1]
            
            if modality not in modality_data:
                modality_data[modality] = {"studies": 0, "rvu": 0}
            
            modality_data[modality]["studies"] += 1
            modality_data[modality]["rvu"] += rvu
            total_studies += 1
            total_rvu += rvu
        
        # Sort by RVU (highest first) and display
        for modality in sorted(modality_data.keys(), key=lambda k: modality_data[k]["rvu"], reverse=True):
            data = modality_data[modality]
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            pct_studies = (data["studies"] / total_studies * 100) if total_studies > 0 else 0
            pct_rvu = (data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
            
            self._by_modality_table.add_row({
                'modality': modality,
                'studies': str(data["studies"]),
                'rvu': f"{data['rvu']:.1f}",
                'avg_rvu': f"{avg_rvu:.2f}",
                'pct_studies': f"{pct_studies:.1f}%",
                'pct_rvu': f"{pct_rvu:.1f}%"
            })
        
        # Add totals row
        if modality_data:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self._by_modality_table.add_row({
                'modality': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}",
                'pct_studies': '100%',
                'pct_rvu': '100%'
            }, is_total=True)
        
        # Update display once after all rows are added
        self._by_modality_table.update_data()
    
    def _display_by_patient_class(self, records: List[dict]):
        """Display data broken down by patient class using Canvas table."""
        # Clear/create Canvas table
        if hasattr(self, '_by_patient_class_table'):
            try:
                self._by_patient_class_table.clear()
            except:
                if hasattr(self, '_by_patient_class_table'):
                    self._by_patient_class_table.frame.pack_forget()
                    self._by_patient_class_table.frame.destroy()
                    delattr(self, '_by_patient_class_table')
        
        if not hasattr(self, '_by_patient_class_table'):
            columns = [
                {'name': 'patient_class', 'width': 120, 'text': 'Patient Class', 'sortable': True},
                {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': True},
                {'name': 'rvu', 'width': 80, 'text': 'RVU', 'sortable': True},
                {'name': 'avg_rvu', 'width': 80, 'text': 'Avg/Study', 'sortable': True},
                {'name': 'pct_studies', 'width': 80, 'text': '% Studies', 'sortable': True},
                {'name': 'pct_rvu', 'width': 80, 'text': '% RVU', 'sortable': True}
            ]
            self._by_patient_class_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._by_patient_class_table.frame.pack_forget()  # Remove any existing packing
        self._by_patient_class_table.pack(fill=tk.BOTH, expand=True)
        self._by_patient_class_table.clear()
        
        # Group by patient class
        class_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            # Handle missing patient_class (historical data may not have it)
            patient_class = record.get("patient_class", "").strip()
            if not patient_class:
                patient_class = "(Unknown)"
            rvu = record.get("rvu", 0)
            
            if patient_class not in class_data:
                class_data[patient_class] = {"studies": 0, "rvu": 0}
            
            class_data[patient_class]["studies"] += 1
            class_data[patient_class]["rvu"] += rvu
            total_studies += 1
            total_rvu += rvu
        
        # Sort by RVU (highest first) and display
        for patient_class in sorted(class_data.keys(), key=lambda k: class_data[k]["rvu"], reverse=True):
            data = class_data[patient_class]
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            pct_studies = (data["studies"] / total_studies * 100) if total_studies > 0 else 0
            pct_rvu = (data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
            
            self._by_patient_class_table.add_row({
                'patient_class': patient_class,
                'studies': str(data["studies"]),
                'rvu': f"{data['rvu']:.1f}",
                'avg_rvu': f"{avg_rvu:.2f}",
                'pct_studies': f"{pct_studies:.1f}%",
                'pct_rvu': f"{pct_rvu:.1f}%"
            })
        
        # Add totals row
        if class_data:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self._by_patient_class_table.add_row({
                'patient_class': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}",
                'pct_studies': '100%',
                'pct_rvu': '100%'
            }, is_total=True)
        
        # Update display once after all rows are added
        self._by_patient_class_table.update_data()
    
    def _display_by_study_type(self, records: List[dict]):
        """Display data broken down by study type using Canvas table."""
        # Clear/create Canvas table
        if hasattr(self, '_by_study_type_table'):
            try:
                self._by_study_type_table.clear()
            except:
                if hasattr(self, '_by_study_type_table'):
                    self._by_study_type_table.frame.pack_forget()
                    self._by_study_type_table.frame.destroy()
                    delattr(self, '_by_study_type_table')
        
        if not hasattr(self, '_by_study_type_table'):
            columns = [
                {'name': 'study_type', 'width': 225, 'text': 'Study Type', 'sortable': True},  # Increased by 50% (150 -> 225)
                {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': True},
                {'name': 'rvu', 'width': 80, 'text': 'RVU', 'sortable': True},
                {'name': 'avg_rvu', 'width': 80, 'text': 'Avg/Study', 'sortable': True},
                {'name': 'pct_studies', 'width': 80, 'text': '% Studies', 'sortable': True},
                {'name': 'pct_rvu', 'width': 80, 'text': '% RVU', 'sortable': True}
            ]
            self._by_study_type_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._by_study_type_table.frame.pack_forget()  # Remove any existing packing
        self._by_study_type_table.pack(fill=tk.BOTH, expand=True)
        self._by_study_type_table.clear()
        
        # Group by study type
        type_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            # Handle missing study_type (historical data may not have it)
            study_type = record.get("study_type", "").strip()
            if not study_type:
                study_type = "(Unknown)"
            
            # Handle any remaining "Multiple ..." study types from old records
            # Convert "Multiple XR" -> "XR Other", etc.
            if study_type.startswith("Multiple "):
                modality = study_type.replace("Multiple ", "").strip()
                study_type = f"{modality} Other" if modality else "(Unknown)"
            
            # Group "CT Spine Lumbar" and "CT Spine Lumbar Recon" with "CT Spine" for display purposes
            # Group "CT CAP Angio", "CT CAP Trauma", and "CT CA" with "CT CAP" for display purposes
            # Keep the original RVU value, but group them together
            grouping_key = study_type
            if study_type == "CT Spine Lumbar" or study_type == "CT Spine Lumbar Recon":
                grouping_key = "CT Spine"
            elif study_type == "CT CAP Angio" or study_type == "CT CAP Angio Combined" or study_type == "CT CAP Trauma" or study_type == "CT CA":
                grouping_key = "CT CAP"
            
            rvu = record.get("rvu", 0)
            
            if grouping_key not in type_data:
                type_data[grouping_key] = {"studies": 0, "rvu": 0}
            
            type_data[grouping_key]["studies"] += 1
            type_data[grouping_key]["rvu"] += rvu
            total_studies += 1
            total_rvu += rvu
        
        # Sort by RVU (highest first) and display
        for study_type in sorted(type_data.keys(), key=lambda k: type_data[k]["rvu"], reverse=True):
            data = type_data[study_type]
            avg_rvu = data["rvu"] / data["studies"] if data["studies"] > 0 else 0
            pct_studies = (data["studies"] / total_studies * 100) if total_studies > 0 else 0
            pct_rvu = (data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
            
            self._by_study_type_table.add_row({
                'study_type': study_type,
                'studies': str(data["studies"]),
                'rvu': f"{data['rvu']:.1f}",
                'avg_rvu': f"{avg_rvu:.2f}",
                'pct_studies': f"{pct_studies:.1f}%",
                'pct_rvu': f"{pct_rvu:.1f}%"
            })
        
        # Add totals row
        if type_data:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self._by_study_type_table.add_row({
                'study_type': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}",
                'pct_studies': '100%',
                'pct_rvu': '100%'
            }, is_total=True)
        
        # Update display once after all rows are added
        self._by_study_type_table.update_data()
    
    def _get_body_part_group(self, study_type: str) -> str:
        """Map study types to modality-specific anatomical groups for hierarchical display."""
        study_lower = study_type.lower()
        
        # Determine modality first
        if study_type.startswith('CT ') or study_type.startswith('CTA '):
            # === CT STUDIES ===
            is_cta = study_type.startswith('CTA')
            
            # Check for body region combinations first
            has_chest = ('chest' in study_lower or ' cap' in study_lower or ' ca ' in study_lower or study_lower.endswith(' ca'))
            has_abdomen = ('abdomen' in study_lower or ' ap' in study_lower or ' cap' in study_lower or ' ca ' in study_lower or study_lower.endswith(' ca'))
            has_pelvis = ('pelvis' in study_lower or ' ap' in study_lower or ' cap' in study_lower)
            
            # CTA Runoff with Abdo/Pelvis - special case
            if 'runoff' in study_lower and ('abdomen' in study_lower or 'pelvis' in study_lower or 'abdo' in study_lower):
                return "CTA: Abdomen/Pelvis"
            
            # CT/CTA Body (Chest+Abdomen combinations ± Pelvis)
            elif has_chest and has_abdomen:
                return "CTA: Body" if is_cta else "CT: Body"
            
            # CT/CTA Abdomen/Pelvis (no chest)
            elif has_abdomen and not has_chest:
                return "CTA: Abdomen/Pelvis" if is_cta else "CT: Abdomen/Pelvis"
            
            # CT/CTA Chest alone
            elif has_chest and not has_abdomen:
                return "CTA: Chest" if is_cta else "CT: Chest"
            
            # CT/CTA Brain
            elif any(kw in study_lower for kw in ['brain', 'head', 'face', 'sinus', 'orbit', 'temporal', 'maxillofacial']):
                return "CTA: Brain" if is_cta else "CT: Brain"
            
            # CT/CTA Neck (without brain/head)
            elif 'neck' in study_lower:
                return "CTA: Neck" if is_cta else "CT: Neck"
            
            # CT/CTA Spine
            elif any(kw in study_lower for kw in ['spine', 'cervical', 'thoracic', 'lumbar', 'sacrum', 'coccyx']):
                return "CTA: Spine" if is_cta else "CT: Spine"
            
            # CT/CTA MSK
            elif any(kw in study_lower for kw in ['shoulder', 'arm', 'elbow', 'wrist', 'hand', 'hip', 'femur', 'knee', 'leg', 'ankle', 'foot', 'joint', 'bone']):
                return "CTA: MSK" if is_cta else "CT: MSK"
            
            # CT/CTA Pelvis alone (no abdomen) - MSK
            elif has_pelvis and not has_abdomen:
                return "CTA: MSK" if is_cta else "CT: MSK"
            
            else:
                return "CTA: Other" if is_cta else "CT: Other"
        
        elif study_type.startswith('MR') or study_type.startswith('MRI'):
            # === MRI STUDIES ===
            # MRI Brain
            if any(kw in study_lower for kw in ['brain', 'head', 'face', 'orbit', 'pituitary', 'iap', 'temporal']):
                return "MRI: Brain"
            
            # MRI Spine
            elif any(kw in study_lower for kw in ['spine', 'cervical', 'thoracic', 'lumbar', 'sacrum', 'coccyx']):
                return "MRI: Spine"
            
            # MRI Abdomen/Pelvis
            elif any(kw in study_lower for kw in ['abdomen', 'pelvis', 'liver', 'kidney', 'pancreas', 'mrcp', 'enterography']):
                return "MRI: Abdomen/Pelvis"
            
            # MRI MSK
            elif any(kw in study_lower for kw in ['shoulder', 'arm', 'elbow', 'wrist', 'hand', 'hip', 'femur', 'knee', 'leg', 'ankle', 'foot', 'joint', 'extremity']):
                return "MRI: MSK"
            
            # MRI Neck
            elif 'neck' in study_lower:
                return "MRI: Neck"
            
            # MRI Chest (rare but exists)
            elif 'chest' in study_lower or 'thorax' in study_lower:
                return "MRI: Chest"
            
            else:
                return "MRI: Other"
        
        elif study_type.startswith('XR'):
            # === X-RAY STUDIES ===
            # XR Chest
            if 'chest' in study_lower:
                return "XR: Chest"
            
            # XR Abdomen
            elif 'abdomen' in study_lower:
                return "XR: Abdomen"
            
            # XR MSK
            elif any(kw in study_lower for kw in ['msk', 'bone', 'shoulder', 'arm', 'elbow', 'wrist', 'hand', 'finger', 
                                                    'hip', 'pelvis', 'femur', 'knee', 'leg', 'ankle', 'foot', 'toe',
                                                    'spine', 'cervical', 'thoracic', 'lumbar', 'sacrum', 'coccyx',
                                                    'rib', 'clavicle', 'scapula', 'joint', 'extremity']):
                return "XR: MSK"
            
            else:
                return "XR: Other"
        
        elif study_type.startswith('US'):
            # === ULTRASOUND STUDIES ===
            # US Abdomen/Pelvis
            if any(kw in study_lower for kw in ['abdomen', 'pelvis', 'liver', 'kidney', 'gallbladder', 'spleen', 'pancreas', 'bladder', 'ovary', 'uterus', 'prostate']):
                return "US: Abdomen/Pelvis"
            
            # US Vascular
            elif any(kw in study_lower for kw in ['vascular', 'doppler', 'artery', 'vein', 'vessel', 'dvt', 'carotid']):
                return "US: Vascular"
            
            # US MSK
            elif any(kw in study_lower for kw in ['shoulder', 'elbow', 'wrist', 'hand', 'hip', 'knee', 'ankle', 'foot', 'tendon', 'joint']):
                return "US: MSK"
            
            # US Breast
            elif 'breast' in study_lower:
                return "US: Breast"
            
            # US Thyroid/Neck
            elif 'thyroid' in study_lower or 'neck' in study_lower:
                return "US: Neck"
            
            else:
                return "US: Other"
        
        elif study_type.startswith('NM'):
            # === NUCLEAR MEDICINE STUDIES ===
            # NM Cardiac
            if any(kw in study_lower for kw in ['cardiac', 'heart', 'myocard', 'stress', 'viability']):
                return "NM: Cardiac"
            
            # NM Bone
            elif 'bone' in study_lower:
                return "NM: Bone"
            
            # NM Other organs
            else:
                return "NM: Other"
        
        else:
            # === OTHER MODALITIES ===
            return "Other"
    
    def _display_by_body_part(self, records: List[dict]):
        """Display data grouped by anatomical body part with hierarchical organization."""
        logger.debug(f"_display_by_body_part called with {len(records)} records")
        
        # Clear/create Canvas table (force recreation to pick up any column changes)
        if hasattr(self, '_by_body_part_table'):
            try:
                self._by_body_part_table.clear()
            except:
                if hasattr(self, '_by_body_part_table'):
                    self._by_body_part_table.frame.pack_forget()
                    self._by_body_part_table.frame.destroy()
                    delattr(self, '_by_body_part_table')
        
        # Create table if it doesn't exist
        if not hasattr(self, '_by_body_part_table'):
            columns = [
                {'name': 'body_part', 'width': 250, 'text': 'Body Part / Study Type', 'sortable': False},  # Narrower first column
                {'name': 'studies', 'width': 80, 'text': 'Studies', 'sortable': False},
                {'name': 'rvu', 'width': 100, 'text': 'Total RVU', 'sortable': False},
                {'name': 'avg_rvu', 'width': 80, 'text': 'Avg RVU', 'sortable': False},
                {'name': 'pct_studies', 'width': 90, 'text': '% Studies', 'sortable': False},
                {'name': 'pct_rvu', 'width': 90, 'text': '% RVU', 'sortable': False}
            ]
            self._by_body_part_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._by_body_part_table.frame.pack_forget()
        self._by_body_part_table.pack(fill=tk.BOTH, expand=True)
        self._by_body_part_table.clear()
        
        # Group by study type first, then by body part
        type_data = {}
        total_studies = 0
        total_rvu = 0
        
        for record in records:
            study_type = record.get("study_type", "").strip()
            if not study_type:
                study_type = "(Unknown)"
            
            # Handle any remaining "Multiple ..." study types
            if study_type.startswith("Multiple "):
                modality = study_type.replace("Multiple ", "").strip()
                study_type = f"{modality} Other" if modality else "(Unknown)"
            
            rvu = record.get("rvu", 0)
            
            if study_type not in type_data:
                type_data[study_type] = {"studies": 0, "rvu": 0}
            
            type_data[study_type]["studies"] += 1
            type_data[study_type]["rvu"] += rvu
            total_studies += 1
            total_rvu += rvu
        
        # Group study types by body part
        body_part_groups = {}
        for study_type, data in type_data.items():
            body_part = self._get_body_part_group(study_type)
            if body_part not in body_part_groups:
                body_part_groups[body_part] = {"studies": 0, "rvu": 0, "types": {}}
            
            body_part_groups[body_part]["studies"] += data["studies"]
            body_part_groups[body_part]["rvu"] += data["rvu"]
            body_part_groups[body_part]["types"][study_type] = data
        
        logger.debug(f"Created {len(body_part_groups)} body part groups: {list(body_part_groups.keys())}")
        
        # Sort body parts by modality priority, then by specific order
        def body_part_sort_key(body_part):
            """Sort by modality (CT, CTA, XR, US, MRI, NM, other), then by specific body part order."""
            # Determine modality priority based on prefix
            if body_part.startswith('CT:') or body_part.startswith('CTA:'):
                # Treat CTA as immediately after CT (same priority level)
                is_cta = body_part.startswith('CTA:')
                modality_priority = 0
                
                # Special ordering: CT Body, CT Abdomen/Pelvis, CT Chest, then CTA equivalents, then others
                if body_part == 'CT: Body':
                    sub_priority = 0
                elif body_part == 'CT: Abdomen/Pelvis':
                    sub_priority = 1
                elif body_part == 'CT: Chest':
                    sub_priority = 2
                elif body_part == 'CTA: Body':
                    sub_priority = 3
                elif body_part == 'CTA: Abdomen/Pelvis':
                    sub_priority = 4
                elif body_part == 'CTA: Chest':
                    sub_priority = 5
                else:
                    # For other CT/CTA categories, CTA comes after CT
                    base_name = body_part.replace('CTA:', '').replace('CT:', '')
                    if is_cta:
                        sub_priority = 100 + ord(base_name[0]) if base_name else 100  # CTA after CT
                    else:
                        sub_priority = 50 + ord(base_name[0]) if base_name else 50  # CT before CTA
            elif body_part.startswith('XR:'):
                modality_priority = 1
                sub_priority = 0
            elif body_part.startswith('US:'):
                modality_priority = 2
                sub_priority = 0
            elif body_part.startswith('MRI:'):
                modality_priority = 3
                sub_priority = 0
            elif body_part.startswith('NM:'):
                modality_priority = 4
                sub_priority = 0
            else:
                modality_priority = 99  # Everything else
                sub_priority = 0
            
            # Return: (modality, sub_priority, alphabetical name)
            return (modality_priority, sub_priority, body_part)
        
        sorted_body_parts = sorted(body_part_groups.keys(), key=body_part_sort_key)
        
        # Display hierarchically
        for body_part in sorted_body_parts:
            bp_data = body_part_groups[body_part]
            num_children = len(bp_data["types"])
            
            # Sort study types within this body part by RVU
            sorted_types = sorted(bp_data["types"].keys(), 
                                 key=lambda k: bp_data["types"][k]["rvu"], 
                                 reverse=True)
            
            # If only one child study type, skip parent header and just show the study type
            if num_children == 1:
                study_type = sorted_types[0]
                st_data = bp_data["types"][study_type]
                st_avg_rvu = st_data["rvu"] / st_data["studies"] if st_data["studies"] > 0 else 0
                st_pct_studies = (st_data["studies"] / total_studies * 100) if total_studies > 0 else 0
                st_pct_rvu = (st_data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
                
                # Show study type with body part prefix for context
                self._by_body_part_table.add_row({
                    'body_part': f"{body_part} - {study_type}",  # Combined display
                    'studies': str(st_data["studies"]),
                    'rvu': f"{st_data['rvu']:.1f}",
                    'avg_rvu': f"{st_avg_rvu:.2f}",
                    'pct_studies': f"{st_pct_studies:.1f}%",
                    'pct_rvu': f"{st_pct_rvu:.1f}%"
                })
            else:
                # Multiple children - show parent header and children
                avg_rvu = bp_data["rvu"] / bp_data["studies"] if bp_data["studies"] > 0 else 0
                pct_studies = (bp_data["studies"] / total_studies * 100) if total_studies > 0 else 0
                pct_rvu = (bp_data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
                
                # Add body part header row with custom background color (don't use is_total)
                # Use button background color for headers (works in both themes)
                theme_colors = self._by_body_part_table.theme_colors
                header_bg = theme_colors.get("button_bg", "#e1e1e1")
                
                self._by_body_part_table.add_row({
                    'body_part': f"▼ {body_part}",  # Parent category with arrow
                    'studies': str(bp_data["studies"]),
                    'rvu': f"{bp_data['rvu']:.1f}",
                    'avg_rvu': f"{avg_rvu:.2f}",
                    'pct_studies': f"{pct_studies:.1f}%",
                    'pct_rvu': f"{pct_rvu:.1f}%"
                }, cell_colors={col: header_bg for col in ['body_part', 'studies', 'rvu', 'avg_rvu', 'pct_studies', 'pct_rvu']})
                
                # Add individual study types (indented with 5 spaces)
                for study_type in sorted_types:
                    st_data = bp_data["types"][study_type]
                    st_avg_rvu = st_data["rvu"] / st_data["studies"] if st_data["studies"] > 0 else 0
                    st_pct_studies = (st_data["studies"] / total_studies * 100) if total_studies > 0 else 0
                    st_pct_rvu = (st_data["rvu"] / total_rvu * 100) if total_rvu > 0 else 0
                    
                    self._by_body_part_table.add_row({
                        'body_part': f"     {study_type}",  # 5 spaces for visual separation
                        'studies': str(st_data["studies"]),
                        'rvu': f"{st_data['rvu']:.1f}",
                        'avg_rvu': f"{st_avg_rvu:.2f}",
                        'pct_studies': f"{st_pct_studies:.1f}%",
                        'pct_rvu': f"{st_pct_rvu:.1f}%"
                    })
        
        # Add totals row
        if body_part_groups:
            total_avg = total_rvu / total_studies if total_studies > 0 else 0
            self._by_body_part_table.add_row({
                'body_part': 'TOTAL',
                'studies': str(total_studies),
                'rvu': f"{total_rvu:.1f}",
                'avg_rvu': f"{total_avg:.2f}",
                'pct_studies': '100%',
                'pct_rvu': '100%'
            }, is_total=True)
        
        # Update display once after all rows are added
        logger.debug(f"Calling update_data() on body part table with {len(self._by_body_part_table.rows_data)} rows")
        self._by_body_part_table.update_data()
        logger.debug("Body part table update_data() completed")
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to a human-readable string (e.g., '5m 30s', '1h 23m')."""
        if seconds is None or seconds == 0:
            return "N/A"
        
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        
        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if secs > 0 and hours == 0:  # Only show seconds if less than an hour
            parts.append(f"{secs}s")
        
        return " ".join(parts) if parts else "0s"
    
    def _display_all_studies(self, records: List[dict]):
        """Display all individual studies with virtual scrolling for performance."""
        # Clear/create frame for virtual table
        if hasattr(self, '_all_studies_frame'):
            try:
                self._all_studies_frame.destroy()
            except:
                pass
        
        if hasattr(self, '_all_studies_table'):
            try:
                self._all_studies_table.frame.pack_forget()
                self._all_studies_table.frame.destroy()
            except:
                pass
            if hasattr(self, '_all_studies_table'):
                delattr(self, '_all_studies_table')
        
        # Filter out multi-accession records (they should have been split into individual records)
        # Exclude records with study_type starting with "Multiple" or with individual_accessions populated
        filtered_records = []
        for record in records:
            study_type = record.get("study_type", "")
            # Skip multi-accession records
            if study_type.startswith("Multiple "):
                continue
            # Skip records with individual_accessions (old format that should have been migrated)
            if record.get("individual_accessions"):
                individual_accessions = record.get("individual_accessions", [])
                if individual_accessions and len(individual_accessions) > 0:
                    continue
            filtered_records.append(record)
        
        # Store filtered records for virtual rendering
        self._all_studies_records = filtered_records
        self._all_studies_row_height = 22
        
        # Clear render state to force fresh render
        if hasattr(self, '_last_render_range'):
            delattr(self, '_last_render_range')
        if hasattr(self, '_rendered_rows'):
            delattr(self, '_rendered_rows')
        
        # Column definitions with widths
        self._all_studies_columns = [
            ('num', 35, '#'),
            ('date', 80, 'Date'),
            ('time', 70, 'Time'),
            ('procedure', 260, 'Procedure'),
            ('study_type', 90, 'Study Type'),  # Reduced from 110 to 90 to ensure RVU stays visible
            ('rvu', 45, 'RVU'),
            ('duration', 70, 'Duration'),
            ('delete', 25, '×')
        ]
        
        # Create frame
        self._all_studies_frame = ttk.Frame(self.table_frame)
        self._all_studies_frame.pack(fill=tk.BOTH, expand=True)
        
        colors = self.app.get_theme_colors()
        canvas_bg = colors.get("entry_bg", "white")
        header_bg = colors.get("button_bg", "#e1e1e1")
        border_color = colors.get("border_color", "#cccccc")  # Light grey for canvas borders
        text_fg = colors.get("fg", "black")
        
        # Calculate total width
        total_width = sum(col[1] for col in self._all_studies_columns)
        
        # Header canvas (fixed)
        header_canvas = tk.Canvas(self._all_studies_frame, height=25, bg=header_bg, 
                                  highlightthickness=1, highlightbackground=border_color)
        header_canvas.pack(fill=tk.X)
        
        # Draw headers with sorting
        x = 0
        # Preserve existing sort state if it exists, otherwise reset
        if not hasattr(self, '_all_studies_sort_column'):
            self._all_studies_sort_column = None
            self._all_studies_sort_reverse = False
        
        for col_name, width, header_text in self._all_studies_columns:
            # Skip delete column for sorting
            if col_name != 'delete':
                # Create clickable header
                rect_id = header_canvas.create_rectangle(x, 0, x + width, 25, fill=header_bg, outline=border_color, tags=f"header_{col_name}")
                text_id = header_canvas.create_text(x + width//2, 12, text=header_text, font=('Arial', 9, 'bold'), fill=text_fg, tags=f"header_{col_name}")
                
                # Bind click event
                header_canvas.tag_bind(f"header_{col_name}", "<Button-1>", 
                                      lambda e, col=col_name: self._sort_all_studies(col))
                header_canvas.tag_bind(f"header_{col_name}", "<Enter>", 
                                      lambda e: header_canvas.config(cursor="hand2"))
                header_canvas.tag_bind(f"header_{col_name}", "<Leave>", 
                                      lambda e: header_canvas.config(cursor=""))
            else:
                # Non-clickable delete header
                header_canvas.create_rectangle(x, 0, x + width, 25, fill=header_bg, outline=border_color)
                header_canvas.create_text(x + width//2, 12, text=header_text, font=('Arial', 9, 'bold'), fill=text_fg)
            x += width
        
        # Store header canvas reference for updating sort indicators
        self._all_studies_header_canvas = header_canvas
        
        # Data canvas with scrollbar
        data_frame = ttk.Frame(self._all_studies_frame)
        data_frame.pack(fill=tk.BOTH, expand=True)
        
        self._all_studies_canvas = tk.Canvas(data_frame, bg=canvas_bg, highlightthickness=0)
        
        # Custom scroll command that also triggers re-render
        def on_scroll(*args):
            self._all_studies_canvas.yview(*args)
            self._render_visible_rows()
        
        scrollbar = ttk.Scrollbar(data_frame, orient="vertical", command=on_scroll)
        
        # Custom yscrollcommand that triggers re-render
        def on_scroll_set(first, last):
            scrollbar.set(first, last)
            self._render_visible_rows()
        
        self._all_studies_canvas.configure(yscrollcommand=on_scroll_set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._all_studies_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Set scroll region based on total rows
        total_height = len(records) * self._all_studies_row_height
        self._all_studies_canvas.configure(scrollregion=(0, 0, total_width, total_height))
        
        # Mouse wheel scrolling
        def on_mousewheel(event):
            self._all_studies_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        self._all_studies_canvas.bind("<MouseWheel>", on_mousewheel)
        self._all_studies_canvas.bind("<Configure>", lambda e: self._render_visible_rows())
        self._all_studies_canvas.bind("<Map>", lambda e: self._render_visible_rows())  # Trigger when widget becomes visible
        
        # Ensure layout is complete before initial render
        self._all_studies_frame.update_idletasks()
        self.window.update_idletasks()  # Also update parent window
        
        # Initial render - use multiple triggers for reliability
        def initial_render():
            try:
                self._all_studies_canvas.update_idletasks()
                # Force render by clearing last range check
                if hasattr(self, '_last_render_range'):
                    delattr(self, '_last_render_range')
                self._render_visible_rows()
            except:
                pass
        
        # Try immediate render first
        try:
            initial_render()
        except:
            pass
        
        # Then schedule delayed renders as backup
        self._all_studies_canvas.after(10, initial_render)
        self._all_studies_canvas.after(50, initial_render)  # Backup trigger in case first one fails
        self._all_studies_canvas.after(100, initial_render)  # Another backup
        
        # Set up delete handler
        self._setup_all_studies_delete_handler()
        
        # If there's a saved sort state, apply it after display
        if hasattr(self, '_all_studies_sort_column') and self._all_studies_sort_column:
            # Use after_idle to ensure display is complete before sorting
            saved_reverse = getattr(self, '_all_studies_sort_reverse', False)
            self._all_studies_frame.after_idle(
                lambda: self._sort_all_studies(self._all_studies_sort_column, force_reverse=saved_reverse)
            )
    
    def _sort_all_studies(self, col_name: str, force_reverse: bool = None):
        """Sort all studies by column.
        
        Args:
            col_name: Column name to sort by
            force_reverse: If provided, use this reverse value instead of toggling
        """
        if not hasattr(self, '_all_studies_records'):
            return
        
        # Toggle sort direction if clicking same column (unless force_reverse is provided)
        if force_reverse is not None:
            self._all_studies_sort_column = col_name
            self._all_studies_sort_reverse = force_reverse
        elif hasattr(self, '_all_studies_sort_column') and self._all_studies_sort_column == col_name:
            self._all_studies_sort_reverse = not self._all_studies_sort_reverse
        else:
            self._all_studies_sort_column = col_name
            self._all_studies_sort_reverse = False
        
        # Sort records based on column
        reverse = self._all_studies_sort_reverse
        
        if col_name == 'num':
            # Sort by original index (no-op, just reverse original order)
            pass  # Don't sort, just reverse if needed
        elif col_name == 'date' or col_name == 'time':
            # Sort by time_performed
            self._all_studies_records.sort(
                key=lambda r: r.get('time_performed', ''),
                reverse=reverse
            )
        elif col_name == 'procedure':
            self._all_studies_records.sort(
                key=lambda r: r.get('procedure', '').lower(),
                reverse=reverse
            )
        elif col_name == 'study_type':
            self._all_studies_records.sort(
                key=lambda r: r.get('study_type', '').lower(),
                reverse=reverse
            )
        elif col_name == 'rvu':
            self._all_studies_records.sort(
                key=lambda r: r.get('rvu', 0),
                reverse=reverse
            )
        elif col_name == 'duration':
            self._all_studies_records.sort(
                key=lambda r: r.get('duration_seconds', 0),
                reverse=reverse
            )
        
        # Update header to show sort indicator
        if hasattr(self, '_all_studies_header_canvas'):
            canvas = self._all_studies_header_canvas
            colors = self.app.get_theme_colors()
            header_bg = colors.get("button_bg", "#e1e1e1")
            border_color = colors.get("border_color", "#cccccc")
            text_fg = colors.get("fg", "black")
            
            # Redraw headers with sort indicators
            canvas.delete("all")
            x = 0
            for col, width, header_text in self._all_studies_columns:
                # Add sort indicator if this is the sorted column
                display_text = header_text
                if col == col_name and col != 'delete':
                    indicator = " ▼" if reverse else " ▲"
                    display_text = header_text + indicator
                
                # Skip delete column for sorting
                if col != 'delete':
                    rect_id = canvas.create_rectangle(x, 0, x + width, 25, fill=header_bg, outline=border_color, tags=f"header_{col}")
                    text_id = canvas.create_text(x + width//2, 12, text=display_text, font=('Arial', 9, 'bold'), fill=text_fg, tags=f"header_{col}")
                    
                    # Bind click event
                    canvas.tag_bind(f"header_{col}", "<Button-1>", 
                                   lambda e, c=col: self._sort_all_studies(c))
                    canvas.tag_bind(f"header_{col}", "<Enter>", 
                                   lambda e: canvas.config(cursor="hand2"))
                    canvas.tag_bind(f"header_{col}", "<Leave>", 
                                   lambda e: canvas.config(cursor=""))
                else:
                    canvas.create_rectangle(x, 0, x + width, 25, fill=header_bg, outline=border_color)
                    canvas.create_text(x + width//2, 12, text=display_text, font=('Arial', 9, 'bold'), fill=text_fg)
                x += width
        
        # Force immediate re-render by clearing cache and rendering
        if hasattr(self, '_last_render_range'):
            delattr(self, '_last_render_range')
        if hasattr(self, '_rendered_rows'):
            self._rendered_rows.clear()
        # Re-render the visible rows immediately
        self._render_visible_rows()
        # Force canvas update to ensure sort is visible
        self._all_studies_canvas.update_idletasks()
        # Also trigger a configure event to force refresh
        self._all_studies_canvas.event_generate("<Configure>")
    
    def _truncate_text(self, text: str, max_chars: int) -> str:
        """Truncate text to fit within max characters, adding ... if needed."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 3] + "..."
    
    def _render_visible_rows(self):
        """Render only the visible rows for virtual scrolling performance."""
        if not hasattr(self, '_all_studies_canvas') or not hasattr(self, '_all_studies_records'):
            return
        
        canvas = self._all_studies_canvas
        records = self._all_studies_records
        row_height = self._all_studies_row_height
        columns = self._all_studies_columns
        
        colors = self.app.get_theme_colors()
        data_bg = colors.get("entry_bg", "white")
        text_fg = colors.get("fg", "black")
        border_color = colors.get("border_color", "#cccccc")  # Light grey for canvas borders
        
        # Get visible range
        canvas.update_idletasks()
        try:
            canvas_height = canvas.winfo_height()
            # If canvas hasn't been laid out yet (height is 0 or very small), use a default height
            if canvas_height < 50:
                # Try to get parent frame height as fallback
                try:
                    parent_height = canvas.master.winfo_height()
                    if parent_height > 50:
                        canvas_height = parent_height
                    else:
                        canvas_height = 400  # Default visible height for initial render
                except:
                    canvas_height = 400  # Default visible height for initial render
            
            y_top = canvas.canvasy(0)
            y_bottom = canvas.canvasy(canvas_height)
            
            # Calculate visible range
            first_visible = max(0, int(y_top // row_height) - 2)
            last_visible = min(len(records), int(y_bottom // row_height) + 3)
        except:
            # If we can't get dimensions, render first 20 rows as fallback
            if len(records) > 0:
                first_visible = 0
                last_visible = min(len(records), 20)
            else:
                return
        
        # Ensure we render at least the first row if we have records
        if len(records) > 0 and last_visible <= first_visible:
            last_visible = min(len(records), first_visible + 20)  # Render at least first 20 rows
        
        # Track what we've rendered to avoid re-rendering
        if not hasattr(self, '_rendered_rows'):
            self._rendered_rows = set()
        
        # Check if we need to re-render (scroll position changed significantly)
        current_range = (first_visible, last_visible)
        
        # Force render if:
        # 1. We have records but range is invalid (last_visible is 0 or <= first_visible when we should have rows)
        # 2. Canvas might not be visible yet (check if it's actually mapped)
        force_render = False
        if len(records) > 0:
            try:
                # Check if canvas is actually visible
                canvas_mapped = canvas.winfo_viewable()
                if not canvas_mapped:
                    force_render = True
                # Also force if range seems invalid
                if last_visible == 0 or (last_visible <= first_visible and len(records) > 0):
                    force_render = True
            except:
                force_render = True
        
        if not force_render and hasattr(self, '_last_render_range') and self._last_render_range == current_range:
            return  # No change, skip
        self._last_render_range = current_range
        
        # Clear canvas and render visible rows
        canvas.delete("all")
        
        for idx in range(first_visible, last_visible):
            if idx >= len(records):
                break
            
            record = records[idx]
            y = idx * row_height
            
            # Parse record data
            procedure = record.get("procedure", "Unknown")
            study_type = record.get("study_type", "Unknown")
            rvu = record.get("rvu", 0.0)
            duration = record.get("duration_seconds", 0)
            duration_str = self._format_duration(duration)
            
            time_performed = record.get("time_performed", "")
            date_str = ""
            time_str = ""
            if time_performed:
                try:
                    dt = datetime.fromisoformat(time_performed)
                    date_str = dt.strftime("%m/%d/%y")
                    time_str = dt.strftime("%I:%M%p").lstrip("0").lower()
                except:
                    pass
            
            # Truncate procedure and study_type to fit column widths - be very aggressive with study_type
            # Use fixed limits to ensure RVU column always stays visible on screen
            procedure_col_width = next((w for name, w, _ in columns if name == 'procedure'), 260)
            study_type_col_width = next((w for name, w, _ in columns if name == 'study_type'), 90)
            
            # Calculate procedure max chars (can be more generous)
            procedure_max_chars = max(15, int((procedure_col_width - 20) / 6))
            
            # For study_type, use a fixed aggressive limit to ensure RVU stays visible
            # 90px column with ~6px per char = ~15 chars, but limit to 8 to be very safe
            study_type_max_chars = 8  # Fixed aggressive limit to ensure RVU column visible
            
            procedure_truncated = self._truncate_text(procedure, procedure_max_chars)
            study_type_truncated = self._truncate_text(study_type, study_type_max_chars)
            
            row_data = [
                str(idx + 1),
                date_str,
                time_str,
                procedure_truncated,
                study_type_truncated,
                f"{rvu:.1f}",
                duration_str,
                "×"
            ]
            
            # Draw row
            x = 0
            for i, (col_name, width, _) in enumerate(columns):
                # Draw cell background
                canvas.create_rectangle(x, y, x + width, y + row_height, 
                                        fill=data_bg, outline=border_color, width=1)
                # Draw text
                cell_text = row_data[i] if i < len(row_data) else ""
                anchor = 'w' if col_name == 'procedure' else 'center'
                text_x = x + 4 if anchor == 'w' else x + width // 2
                canvas.create_text(text_x, y + row_height // 2, text=cell_text, 
                                   font=('Arial', 8), fill=text_fg, anchor=anchor)
                x += width
    
    def _setup_all_studies_delete_handler(self):
        """Set up click handling for the delete column in all studies view."""
        if not hasattr(self, '_all_studies_canvas'):
            return
        
        canvas = self._all_studies_canvas
        row_height = self._all_studies_row_height
        columns = self._all_studies_columns
        
        # Calculate x position of delete column
        delete_col_x = sum(col[1] for col in columns[:-1])  # All columns except last
        delete_col_width = columns[-1][1]  # Last column width
        
        colors = self.app.get_theme_colors()
        hover_color = colors.get("delete_btn_hover", "#ffcccc")
        
        # Track currently hovered row
        self._hover_row_idx = None
        
        def on_motion(event):
            canvas_y = canvas.canvasy(event.y)
            canvas_x = event.x
            
            # Check if in delete column
            if delete_col_x <= canvas_x <= delete_col_x + delete_col_width:
                row_idx = int(canvas_y // row_height)
                if 0 <= row_idx < len(self._all_studies_records):
                    if self._hover_row_idx != row_idx:
                        # Clear previous hover first
                        canvas.delete("hover")
                        self._hover_row_idx = row_idx
                        # Draw new hover overlay
                        y1 = row_idx * row_height
                        canvas.create_rectangle(
                            delete_col_x, y1, delete_col_x + delete_col_width, y1 + row_height,
                            fill=hover_color, outline="", tags="hover"
                        )
                        canvas.create_text(
                            delete_col_x + delete_col_width // 2, y1 + row_height // 2,
                            text="×", font=('Arial', 8), fill=colors.get("fg", "black"), tags="hover"
                        )
                        canvas.config(cursor="hand2")
                    return
            
            # Not hovering over delete column
            if self._hover_row_idx is not None:
                canvas.delete("hover")
                self._hover_row_idx = None
                canvas.config(cursor="")
        
        def on_leave(event):
            if self._hover_row_idx is not None:
                canvas.delete("hover")
                self._hover_row_idx = None
                canvas.config(cursor="")
        
        def on_click(event):
            canvas_y = canvas.canvasy(event.y)
            canvas_x = event.x
            
            # Check if in delete column
            if delete_col_x <= canvas_x <= delete_col_x + delete_col_width:
                row_idx = int(canvas_y // row_height)
                if 0 <= row_idx < len(self._all_studies_records):
                    self._delete_all_studies_record(row_idx)
        
        canvas.bind("<Motion>", on_motion)
        canvas.bind("<Leave>", on_leave)
        canvas.bind("<Button-1>", on_click)
    
    def _delete_all_studies_record(self, row_idx: int):
        """Delete a record from the all studies view."""
        if not hasattr(self, '_all_studies_records') or row_idx >= len(self._all_studies_records):
            return
        
        record = self._all_studies_records[row_idx]
        accession = record.get("accession", "")
        
        # Save current sort state and scroll position before deletion
        saved_sort_column = getattr(self, '_all_studies_sort_column', None)
        saved_sort_reverse = getattr(self, '_all_studies_sort_reverse', False)
        
        # Save scroll position (as fraction of total content)
        saved_scroll_position = None
        if hasattr(self, '_all_studies_canvas'):
            try:
                saved_scroll_position = self._all_studies_canvas.yview()[0]  # Get top position (0.0 to 1.0)
            except:
                pass
        
        # Confirm deletion
        result = messagebox.askyesno(
            "Delete Study?",
            f"Delete this study?\n\n"
            f"Accession: {accession}\n"
            f"Procedure: {record.get('procedure', 'Unknown')}\n"
            f"RVU: {record.get('rvu', 0):.1f}",
            parent=self.window
        )
        
        if result:
            time_performed = record.get("time_performed", "")
            record_id = record.get("id")  # Database ID if available
            
            # Delete from database first
            deleted_from_db = False
            if record_id:
                try:
                    self.data_manager.db.delete_record(record_id)
                    deleted_from_db = True
                    logger.info(f"Deleted study from database: {accession} (ID: {record_id})")
                except Exception as e:
                    logger.error(f"Error deleting study from database: {e}", exc_info=True)
            else:
                # Record doesn't have ID - try to find it in database by accession
                # Check current shift first
                try:
                    current_shift = self.data_manager.db.get_current_shift()
                    if current_shift:
                        db_record = self.data_manager.db.find_record_by_accession(
                            current_shift['id'], accession
                        )
                        if db_record:
                            self.data_manager.db.delete_record(db_record['id'])
                            deleted_from_db = True
                            logger.info(f"Deleted study from database by accession: {accession} (ID: {db_record['id']})")
                except Exception as e:
                    logger.error(f"Error finding/deleting study in database (current shift): {e}", exc_info=True)
                
                # If not found in current shift, check historical shifts
                if not deleted_from_db:
                    try:
                        historical_shifts = self.data_manager.db.get_all_shifts()
                        for shift in historical_shifts:
                            if shift.get('is_current'):
                                continue
                            db_record = self.data_manager.db.find_record_by_accession(
                                shift['id'], accession
                            )
                            if db_record:
                                self.data_manager.db.delete_record(db_record['id'])
                                deleted_from_db = True
                                logger.info(f"Deleted study from database by accession (historical shift): {accession} (ID: {db_record['id']})")
                                break
                    except Exception as e:
                        logger.error(f"Error finding/deleting study in database (historical shifts): {e}", exc_info=True)
            
            # Delete from memory (check current shift first, then historical)
            found_in_memory = False
            current_records = self.data_manager.data.get("current_shift", {}).get("records", [])
            for i, r in enumerate(current_records):
                if r.get("accession") == accession and r.get("time_performed") == time_performed:
                    current_records.pop(i)
                    self.data_manager.save()
                    logger.info(f"Deleted study from current shift memory: {accession}")
                    found_in_memory = True
                    break
            
            # If not found in current shift, check historical shifts
            if not found_in_memory:
                for shift in self.data_manager.data.get("shifts", []):
                    shift_records = shift.get("records", [])
                    for i, r in enumerate(shift_records):
                        if r.get("accession") == accession and r.get("time_performed") == time_performed:
                            shift_records.pop(i)
                            self.data_manager.save()
                            logger.info(f"Deleted study from historical shift memory: {accession}")
                            found_in_memory = True
                            break
                    if found_in_memory:
                        break
            
            if not found_in_memory and not deleted_from_db:
                logger.warning(f"Could not find record to delete in memory or database: {accession}")
            
            # Refresh data and restore state
            self.refresh_data()
            
            # Restore sort state and scroll position after refresh completes
            def restore_state():
                if saved_sort_column:
                    self._sort_all_studies(saved_sort_column, force_reverse=saved_sort_reverse)
                
                # Restore scroll position
                if saved_scroll_position is not None and hasattr(self, '_all_studies_canvas'):
                    try:
                        # Wait a moment for canvas to update, then restore scroll
                        self._all_studies_canvas.update_idletasks()
                        self._all_studies_canvas.yview_moveto(saved_scroll_position)
                    except:
                        pass
            
            self.window.after_idle(restore_state)
    
    def _sort_column(self, col: str, reverse: bool = None):
        """Sort treeview by column. Toggles direction on each click."""
        # Track current sort state
        if not hasattr(self, '_current_sort_col'):
            self._current_sort_col = None
            self._current_sort_reverse = False
        
        # If clicking same column, toggle direction; otherwise sort ascending
        if self._current_sort_col == col:
            reverse = not self._current_sort_reverse
        else:
            reverse = False
        
        self._current_sort_col = col
        self._current_sort_reverse = reverse
        
        # Get all items with their values before clearing
        all_items_data = []
        for item in self.tree.get_children(""):
            values = []
            for c in self.tree["columns"]:
                values.append(self.tree.set(item, c))
            sort_val = self.tree.set(item, col)
            
            # Check if it's a totals/separator row by checking all values
            is_total = False
            for val in values:
                if isinstance(val, str):
                    val_str = val.strip()
                    # Check for separator patterns: "─", dashes, "TOTAL", or all dashes
                    if ("─" in val_str or val_str.startswith("TOTAL") or 
                        (len(val_str) > 0 and all(c in "─-" for c in val_str)) or
                        val_str == "─" * len(val_str)):
                        is_total = True
                        break
            
            all_items_data.append((sort_val, values, is_total))
        
        # Separate regular items and totals/separators
        regular_items = [(val, values) for val, values, is_total in all_items_data if not is_total]
        totals_data = [(val, values) for val, values, is_total in all_items_data if is_total]
        
        # Sort regular items
        try:
            # Check if column contains numeric data
            numeric_cols = ["rvu", "studies", "avg_rvu", "pct_studies", "pct_rvu"]
            if col in numeric_cols or (regular_items and regular_items[0][0] and 
                                       str(regular_items[0][0]).replace(".", "").replace("-", "").replace("%", "").strip().isdigit()):
                # Numeric sort
                regular_items.sort(key=lambda t: float(str(t[0]).replace("%", "").replace(",", "")) if t[0] and str(t[0]).replace(".", "").replace("-", "").replace("%", "").replace(",", "").strip().isdigit() else float('-inf'), reverse=reverse)
            elif col == "time_to_read":
                # Sort by duration - parse time format (e.g., "5m 30s" -> seconds for sorting)
                def parse_duration(val):
                    if not val or val == "N/A":
                        return 0
                    total_seconds = 0
                    val_str = str(val).strip()
                    # Parse format like "1h 23m", "5m 30s", "30s"
                    hours = re.search(r'(\d+)h', val_str)
                    minutes = re.search(r'(\d+)m', val_str)
                    seconds = re.search(r'(\d+)s', val_str)
                    if hours:
                        total_seconds += int(hours.group(1)) * 3600
                    if minutes:
                        total_seconds += int(minutes.group(1)) * 60
                    if seconds:
                        total_seconds += int(seconds.group(1))
                    return total_seconds
                regular_items.sort(key=lambda t: parse_duration(t[0]), reverse=reverse)
            else:
                # String sort
                regular_items.sort(key=lambda t: str(t[0]).lower() if t[0] else "", reverse=reverse)
        except (ValueError, TypeError):
            # Fallback to string sort
            regular_items.sort(key=lambda t: str(t[0]).lower() if t[0] else "", reverse=reverse)
        
        # Clear tree
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        
        # Insert sorted regular items
        for val, values in regular_items:
            self.tree.insert("", tk.END, values=values)
        
        # Insert totals at end
        for val, values in totals_data:
            self.tree.insert("", tk.END, values=values)
        
        # Update column headings to show sort direction (subtle arrows: ▲ ▼)
        for column in self.tree["columns"]:
            heading_text = self.tree.heading(column)["text"]
            # Remove existing sort indicators
            heading_text = heading_text.replace("▲ ", "").replace("▼ ", "").strip()
            
            # Add indicator and command for clicked column
            if column == col:
                indicator = "▼ " if reverse else "▲ "
                self.tree.heading(column, text=indicator + heading_text, 
                                 command=lambda c=column: self._sort_column(c))
            else:
                self.tree.heading(column, text=heading_text,
                                 command=lambda c=column: self._sort_column(c))
    
    def _display_efficiency(self, records: List[dict]):
        """Display efficiency view with Canvas-based spreadsheet showing per-cell color coding.
        Two sections: 11pm-10am (night) and 11am-10pm (day), each with Modality + 12 hour columns.
        """
        # Checkboxes are now shown/hidden in refresh_data() method
        # No need to manage them here
        
        # Ensure efficiency frame exists
        if self.efficiency_frame is None:
            self.efficiency_frame = ttk.Frame(self.table_frame)
        
        # Clear existing widgets and redraw functions
        for widget in list(self.efficiency_frame.winfo_children()):
            try:
                widget.destroy()
            except:
                pass
        # Clear redraw function references when rebuilding
        if hasattr(self, '_efficiency_redraw_functions'):
            self._efficiency_redraw_functions.clear()
        
        # Make sure efficiency frame is packed and visible
        try:
            self.efficiency_frame.pack_forget()
        except:
            pass
        self.efficiency_frame.pack(fill=tk.BOTH, expand=True)
        
        # Define hour ranges
        night_hours = list(range(23, 24)) + list(range(0, 11))  # 11pm-10am (12 hours)
        day_hours = list(range(11, 23))  # 11am-10pm (12 hours)
        
        # Build data structure: modality -> hour -> list of durations and counts
        efficiency_data = {}
        study_count_data = {}  # modality -> hour -> count
        shifts_per_hour = {}  # modality -> hour -> set of shift identifiers (for average calculation)
        
        # Build a mapping of time_performed to shift_id by checking all shifts
        # This helps us calculate averages (studies per hour / number of shifts with data in that hour)
        shift_time_map = {}  # time_performed -> shift_id
        all_shifts = []
        current_shift = self.data_manager.data.get("current_shift", {})
        if current_shift.get("shift_start"):
            all_shifts.append(("current", current_shift))
        for shift in self.data_manager.data.get("shifts", []):
            if shift.get("shift_start"):
                all_shifts.append((shift.get("shift_start"), shift))
        
        # Helper function to track which shift a record belongs to
        def track_shift_for_record(modality, hour, record_time_str):
            """Track which shift this record belongs to for average calculation."""
            if not record_time_str:
                return
            try:
                record_time = datetime.fromisoformat(record_time_str)
                # Find which shift this record belongs to
                for shift_id, shift_data in all_shifts:
                    shift_start_str = shift_data.get("shift_start") if isinstance(shift_data, dict) else None
                    if shift_start_str:
                        try:
                            shift_start = datetime.fromisoformat(shift_start_str)
                            shift_end_str = shift_data.get("shift_end") if isinstance(shift_data, dict) else None
                            if shift_end_str:
                                shift_end = datetime.fromisoformat(shift_end_str)
                            else:
                                # No end time, assume 9 hour shift
                                shift_end = shift_start + timedelta(hours=9)
                            
                            if shift_start <= record_time <= shift_end:
                                # This record belongs to this shift
                                if modality not in shifts_per_hour:
                                    shifts_per_hour[modality] = {}
                                if hour not in shifts_per_hour[modality]:
                                    shifts_per_hour[modality][hour] = set()
                                shifts_per_hour[modality][hour].add(shift_id)
                                break
                        except:
                            pass
            except:
                pass
        
        for record in records:
            study_type = record.get("study_type", "Unknown")
            modality = study_type.split()[0] if study_type else "Unknown"
            
            try:
                rec_time = datetime.fromisoformat(record.get("time_performed", ""))
                hour = rec_time.hour
                record_time_str = record.get("time_performed", "")
            except:
                continue
            
            # Check if this is a "Multiple" modality record that should be expanded
            if modality == "Multiple" or study_type.startswith("Multiple "):
                # Expand into individual studies
                individual_study_types = record.get("individual_study_types", [])
                accession_count = record.get("accession_count", 1)
                duration = record.get("duration_seconds", 0)
                duration_per_study = duration / accession_count if accession_count > 0 else 0
                
                # Check if we have individual data stored
                has_individual_data = individual_study_types and len(individual_study_types) == accession_count
                
                if has_individual_data:
                    # We have individual study types - process each one
                    for i, individual_st in enumerate(individual_study_types):
                        expanded_mod = individual_st.split()[0] if individual_st else "Unknown"
                        
                        # Track duration data (divide total duration equally)
                        if duration_per_study and duration_per_study > 0:
                            if expanded_mod not in efficiency_data:
                                efficiency_data[expanded_mod] = {}
                            if hour not in efficiency_data[expanded_mod]:
                                efficiency_data[expanded_mod][hour] = []
                            efficiency_data[expanded_mod][hour].append(duration_per_study)
                        
                        # Track study count data
                        if expanded_mod not in study_count_data:
                            study_count_data[expanded_mod] = {}
                        if hour not in study_count_data[expanded_mod]:
                            study_count_data[expanded_mod][hour] = 0
                        study_count_data[expanded_mod][hour] += 1
                        track_shift_for_record(expanded_mod, hour, record_time_str)
                else:
                    # No individual data - try to parse from study_type (e.g., "Multiple CT, XR")
                    # Extract modalities after "Multiple "
                    if study_type.startswith("Multiple "):
                        modality_str = study_type.replace("Multiple ", "").strip()
                        modalities_list = [m.strip() for m in modality_str.split(",")]
                        count = len(modalities_list)
                        
                        for mod in modalities_list:
                            # Track duration data (divide total duration equally)
                            if duration and duration > 0:
                                per_study_duration = duration / count
                                if mod not in efficiency_data:
                                    efficiency_data[mod] = {}
                                if hour not in efficiency_data[mod]:
                                    efficiency_data[mod][hour] = []
                                efficiency_data[mod][hour].append(per_study_duration)
                            
                            # Track study count data
                            if mod not in study_count_data:
                                study_count_data[mod] = {}
                            if hour not in study_count_data[mod]:
                                study_count_data[mod][hour] = 0
                            study_count_data[mod][hour] += 1
                            track_shift_for_record(mod, hour, record_time_str)
                    else:
                        # Can't expand - treat as single "Multiple" entry
                        # Track duration data
                        if duration and duration > 0:
                            if modality not in efficiency_data:
                                efficiency_data[modality] = {}
                            if hour not in efficiency_data[modality]:
                                efficiency_data[modality][hour] = []
                            efficiency_data[modality][hour].append(duration)
                        
                        # Track study count data
                        if modality not in study_count_data:
                            study_count_data[modality] = {}
                        if hour not in study_count_data[modality]:
                            study_count_data[modality][hour] = 0
                        study_count_data[modality][hour] += 1
                        track_shift_for_record(modality, hour, record_time_str)
            else:
                # Regular single study - process normally
                # Track duration data
                duration = record.get("duration_seconds", 0)
                if duration and duration > 0:
                    if modality not in efficiency_data:
                        efficiency_data[modality] = {}
                    if hour not in efficiency_data[modality]:
                        efficiency_data[modality][hour] = []
                    efficiency_data[modality][hour].append(duration)
                
                # Track study count data (all studies, not just those with duration)
                if modality not in study_count_data:
                    study_count_data[modality] = {}
                if hour not in study_count_data[modality]:
                    study_count_data[modality][hour] = 0
                study_count_data[modality][hour] += 1
                track_shift_for_record(modality, hour, record_time_str)
        
        # Combine modalities from both data sources
        all_modalities = sorted(set(list(efficiency_data.keys()) + list(study_count_data.keys())))
        
        # Helper function to get color coding (blue=low, red=high by default)
        # Get theme colors for efficiency view
        theme_colors = self.app.theme_colors if hasattr(self, 'app') and hasattr(self.app, 'theme_colors') else {}
        data_bg = theme_colors.get("entry_bg", "white")
        text_fg = theme_colors.get("fg", "black")
        border_color = theme_colors.get("border_color", "#cccccc")  # Light grey for canvas borders
        total_bg = theme_colors.get("button_bg", "#e1e1e1")
        
        def get_heatmap_color(value, min_val, max_val, range_val, reverse=False):
            """Return hex color: light blue (low) to light red (high) by default.
            Set reverse=True to invert (blue=high, red=low).
            Works for both duration and count values.
            """
            if value is None or range_val == 0:
                return data_bg  # Use theme background for empty
            
            normalized = (value - min_val) / range_val
            if reverse:
                normalized = 1.0 - normalized  # Reverse the color mapping
            
            # Light blue: RGB(227, 242, 253) = #E3F2FD
            # Light red: RGB(255, 235, 238) = #FFEBEE
            r = int(227 + (255 - 227) * normalized)
            g = int(242 + (235 - 242) * normalized)
            b = int(253 + (238 - 253) * normalized)
            return f"#{r:02x}{g:02x}{b:02x}"
        
        # Helper to create Canvas-based spreadsheet table
        def create_spreadsheet_table(parent_frame, hours_list, section_title):
            """Create a Canvas-based spreadsheet table with per-cell color coding.
            Supports both duration and study count colors based on checkbox states.
            """
            # Frame with scrollbar
            table_frame = ttk.Frame(parent_frame)
            table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
            
            # Get theme colors from app
            theme_colors = self.app.theme_colors if hasattr(self, 'app') and hasattr(self.app, 'theme_colors') else {}
            canvas_bg = theme_colors.get("canvas_bg", "#f0f0f0")
            border_color = theme_colors.get("border_color", "#cccccc")  # Light grey for canvas borders
            canvas = tk.Canvas(table_frame, bg=canvas_bg, highlightthickness=1, highlightbackground=border_color)
            scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=canvas.yview)
            
            # Inner frame on canvas for content
            inner_frame = ttk.Frame(canvas)
            canvas_window = canvas.create_window((0, 0), window=inner_frame, anchor="nw")
            
            # Configure scrolling
            def configure_scroll_region(event):
                canvas.configure(scrollregion=canvas.bbox("all"))
            
            def configure_canvas_width(event):
                canvas_width = event.width
                canvas.itemconfig(canvas_window, width=canvas_width)
            
            inner_frame.bind("<Configure>", configure_scroll_region)
            canvas.bind("<Configure>", configure_canvas_width)
            canvas.configure(yscrollcommand=scrollbar.set)
            
            # Table dimensions
            modality_col_width = 100
            hour_col_width = 75
            row_height = 25
            header_height = 30
            
            # Calculate table width
            table_width = modality_col_width + (12 * hour_col_width)
            
            # Get theme colors for efficiency view
            header_bg = theme_colors.get("button_bg", "#e1e1e1")
            
            # Create header row with button-style appearance
            header_canvas = tk.Canvas(inner_frame, width=table_width, height=header_height, 
                                     bg=header_bg, highlightthickness=0)
            header_canvas.pack(fill=tk.X)
            
            # Store row data for sorting
            row_data_list = []
            total_row_data = None
            
            # Sort state
            sort_column = None
            sort_reverse = False
            
            def draw_headers():
                """Draw headers with sort indicators."""
                header_canvas.delete("all")
                x = 0
                # Modality header (sortable)
                header_text = "Modality"
                if sort_column == "modality":
                    header_text += " ▼" if sort_reverse else " ▲"
                header_fg = theme_colors.get("fg", "black")
                header_border = theme_colors.get("border_color", "#cccccc")  # Light grey for canvas borders
                rect_id = header_canvas.create_rectangle(x, 0, x + modality_col_width, header_height, 
                                                         fill=header_bg, outline=header_border, width=1,
                                                         tags="header_modality")
                
                header_canvas.create_text(x + modality_col_width//2, header_height//2, 
                                         text=header_text, font=('Arial', 9, 'bold'), anchor='center',
                                         fill=header_fg, tags="header_modality")
                header_canvas.tag_bind("header_modality", "<Button-1>", lambda e: on_modality_click())
                header_canvas.tag_bind("header_modality", "<Enter>", lambda e: header_canvas.config(cursor="hand2"))
                header_canvas.tag_bind("header_modality", "<Leave>", lambda e: header_canvas.config(cursor=""))
                x += modality_col_width
                
                # Hour headers (not sortable)
                for hour in hours_list:
                    hour_12 = hour % 12 or 12
                    am_pm = "AM" if hour < 12 else "PM"
                    hour_label = f"{hour_12}{am_pm}"
                    header_canvas.create_rectangle(x, 0, x + hour_col_width, header_height,
                                                  fill=header_bg, outline=header_border, width=1)
                    header_canvas.create_text(x + hour_col_width//2, header_height//2,
                                             text=hour_label, font=('Arial', 9, 'bold'), anchor='center',
                                             fill=header_fg)
                    x += hour_col_width
            
            def on_modality_click():
                """Handle modality header click for sorting."""
                nonlocal sort_column, sort_reverse
                if sort_column == "modality":
                    sort_reverse = not sort_reverse
                else:
                    sort_column = "modality"
                    sort_reverse = False
                draw_headers()
                draw_rows()
            
            def draw_rows():
                """Draw all rows, sorted if needed."""
                rows_canvas.delete("all")
                
                # Get radio button state - force update to ensure we get current values
                try:
                    heatmap_mode = self.heatmap_mode.get()
                except:
                    heatmap_mode = "duration"
                show_duration = (heatmap_mode == "duration")
                show_count = (heatmap_mode == "count")
                
                # Sort row data if needed
                rows_to_draw = list(row_data_list)
                if sort_column == "modality":
                    rows_to_draw.sort(key=lambda r: r['modality'].lower(), reverse=sort_reverse)
                
                y = 0
                for row_data in rows_to_draw:
                    modality = row_data['modality']
                    row_cell_data = row_data['cell_data']
                    row_count_data = row_data.get('count_data', [])
                    min_duration = row_data['min_duration']
                    max_duration = row_data['max_duration']
                    duration_range = row_data['duration_range']
                    min_count = row_data.get('min_count', 0)
                    max_count = row_data.get('max_count', 0)
                    count_range = row_data.get('count_range', 1)
                    
                    # Draw row
                    x = 0
                    # Modality cell
                    rows_canvas.create_rectangle(x, y, x + modality_col_width, y + row_height,
                                               fill=data_bg, outline=border_color, width=1)
                    rows_canvas.create_text(x + modality_col_width//2, y + row_height//2,
                                           text=modality, font=('Arial', 9), anchor='center',
                                           fill=text_fg)
                    x += modality_col_width
                    
                    # Hour cells with color coding
                    # Get avg_count_data which stores pre-calculated averages (total/shifts)
                    row_avg_count_data = row_data.get('avg_count_data', row_count_data)
                    
                    for idx, (avg_duration, _) in enumerate(row_cell_data):
                        # Rebuild cell text based on current study_count_mode (not the stored one)
                        current_study_count_mode = self.study_count_mode.get() if hasattr(self, 'study_count_mode') else "average"
                        total_count = row_count_data[idx] if idx < len(row_count_data) else 0
                        avg_count = row_avg_count_data[idx] if idx < len(row_avg_count_data) else 0
                        
                        # Build cell text dynamically based on current mode - ALWAYS rebuild, don't use stored text
                        if avg_duration is not None:
                            duration_str = self._format_duration(avg_duration)
                            if current_study_count_mode == "average":
                                # Show average: pre-calculated (total / num_shifts)
                                cell_text = f"{duration_str} ({avg_count})"
                            else:
                                # Show total: use the total study count
                                cell_text = f"{duration_str} ({total_count})"
                        elif total_count > 0:
                            if current_study_count_mode == "average":
                                # Show average
                                cell_text = f"({avg_count})"
                            else:
                                cell_text = f"({total_count})"
                        else:
                            cell_text = "-"
                        
                        # Determine cell color based on active heatmaps
                        cell_color = data_bg  # Default to background
                        
                        # Apply duration colors if enabled (blue=fast, red=slow)
                        if show_duration and avg_duration is not None:
                            cell_color = get_heatmap_color(avg_duration, min_duration, max_duration, duration_range, reverse=False)
                        
                        # Apply study count colors if enabled (blue=high count, red=low count - reversed from duration)
                        if show_count and total_count is not None and total_count > 0:
                            # Only count colors enabled (reversed: blue=high, red=low)
                            cell_color = get_heatmap_color(total_count, min_count, max_count, count_range, reverse=True)
                        
                        rows_canvas.create_rectangle(x, y, x + hour_col_width, y + row_height,
                                                   fill=cell_color, outline=border_color, width=1)
                        
                        # Use dark text for shaded cells (light colored), theme text color for unshaded
                        # Shaded cells are light (blue to red), so use dark text
                        if cell_color != data_bg:
                            # Cell is shaded - use dark text for readability
                            cell_text_color = "#000000"  # Black text for light colored cells
                        else:
                            # Cell is not shaded - use theme text color
                            cell_text_color = text_fg
                        
                        rows_canvas.create_text(x + hour_col_width//2, y + row_height//2,
                                               text=cell_text, font=('Arial', 8), anchor='center',
                                               fill=cell_text_color)
                        x += hour_col_width
                    y += row_height
                
                # Draw TOTAL row with color coding
                if total_row_data:
                    y += 5
                    x = 0
                    rows_canvas.create_rectangle(x, y, x + modality_col_width, y + row_height,
                                               fill=total_bg, outline=border_color, width=1)
                    # Always show "Total" as the label (not based on study count mode)
                    rows_canvas.create_text(x + modality_col_width//2, y + row_height//2,
                                           text="Total", font=('Arial', 9, 'bold'), anchor='center',
                                           fill=text_fg)
                    x += modality_col_width
                    
                    total_hour_cells = total_row_data['hour_cells']
                    total_hour_durations = total_row_data.get('hour_durations', [None] * len(total_hour_cells))
                    total_hour_counts = total_row_data.get('hour_counts', [None] * len(total_hour_cells))
                    total_min_duration = total_row_data.get('min_duration', 0)
                    total_max_duration = total_row_data.get('max_duration', 0)
                    total_duration_range = total_row_data.get('duration_range', 1)
                    total_min_count = total_row_data.get('min_count', 0)
                    total_max_count = total_row_data.get('max_count', 0)
                    total_count_range = total_row_data.get('count_range', 1)
                    
                    # Rebuild cell text dynamically based on current study_count_mode
                    current_study_count_mode = self.study_count_mode.get() if hasattr(self, 'study_count_mode') else "average"
                    
                    # Get average counts for total row
                    total_hour_avg_counts = total_row_data.get('hour_avg_counts', [])
                    
                    for idx in range(len(total_hour_cells)):
                        # Rebuild cell text based on current mode
                        avg_duration = total_hour_durations[idx] if idx < len(total_hour_durations) else None
                        total_count = total_hour_counts[idx] if idx < len(total_hour_counts) else None
                        hour_count = total_count if total_count is not None else 0
                        avg_count = total_hour_avg_counts[idx] if idx < len(total_hour_avg_counts) else 0
                        
                        if avg_duration is not None:
                            duration_str = self._format_duration(avg_duration)
                            if current_study_count_mode == "average":
                                # Show average: pre-calculated (total / num_shifts)
                                cell_text = f"{duration_str} ({avg_count})"
                            else:
                                # Show total study count
                                cell_text = f"{duration_str} ({hour_count})"
                        elif hour_count > 0:
                            if current_study_count_mode == "average":
                                # Show average
                                cell_text = f"({avg_count})"
                            else:
                                cell_text = f"({hour_count})"
                        else:
                            cell_text = "-"
                        
                        # Determine cell color based on active heatmaps
                        cell_color = total_bg  # Default to background
                        
                        # Apply duration colors if enabled (blue=fast, red=slow)
                        if show_duration and avg_duration is not None:
                            cell_color = get_heatmap_color(avg_duration, total_min_duration, total_max_duration, total_duration_range, reverse=False)
                        
                        # Apply study count colors if enabled (blue=high count, red=low count - reversed from duration)
                        if show_count and hour_count > 0:
                            cell_color = get_heatmap_color(hour_count, total_min_count, total_max_count, total_count_range, reverse=True)
                        
                        rows_canvas.create_rectangle(x, y, x + hour_col_width, y + row_height,
                                                   fill=cell_color, outline=border_color, width=1)
                        
                        # Use dark text for shaded cells, theme text color for unshaded
                        if cell_color != total_bg:
                            cell_text_color = "#000000"  # Black text for light colored cells
                        else:
                            cell_text_color = text_fg
                        
                        rows_canvas.create_text(x + hour_col_width//2, y + row_height//2,
                                               text=cell_text, font=('Arial', 8, 'bold'), anchor='center',
                                               fill=cell_text_color)
                        x += hour_col_width
                    y += row_height
                
                rows_canvas.config(height=y + 5)
            
            # Get data canvas background from theme
            data_bg = theme_colors.get("entry_bg", "white")
            
            # Create data rows canvas (must be created before draw_rows is called)
            rows_canvas = tk.Canvas(inner_frame, width=table_width, 
                                   bg=data_bg, highlightthickness=0)
            rows_canvas.pack(fill=tk.BOTH, expand=True)
            
            # Draw initial headers
            draw_headers()
            
            # Get study count mode (average vs total)
            study_count_mode = self.study_count_mode.get() if hasattr(self, 'study_count_mode') else "average"
            
            # Build row data for all modalities
            for modality in all_modalities:
                modality_durations = []
                modality_counts_row = []  # Total counts
                modality_avg_counts_row = []  # Average counts (total / num_shifts)
                row_cell_data = []
                
                for hour in hours_list:
                    # Get duration data
                    avg_duration = None
                    duration_count = 0
                    if modality in efficiency_data and hour in efficiency_data[modality]:
                        durations = efficiency_data[modality][hour]
                        avg_duration = sum(durations) / len(durations)
                        duration_count = len(durations)
                    
                    # Get study count data
                    study_count = study_count_data.get(modality, {}).get(hour, 0) if modality in study_count_data else 0
                    modality_counts_row.append(study_count)
                    
                    # Calculate average: total studies / number of shifts with data in this hour
                    num_shifts_with_data = len(shifts_per_hour.get(modality, {}).get(hour, set())) if modality in shifts_per_hour else 0
                    if num_shifts_with_data == 0:
                        num_shifts_with_data = 1  # Avoid division by zero, assume at least 1 shift
                    avg_studies = round(study_count / num_shifts_with_data) if study_count > 0 else 0
                    modality_avg_counts_row.append(avg_studies)
                    
                    # Build cell text based on study count mode
                    if avg_duration is not None:
                        duration_str = self._format_duration(avg_duration)
                        if study_count_mode == "average":
                            # Show average: studies per hour averaged across shifts
                            cell_text = f"{duration_str} ({avg_studies})"
                        else:
                            # Show total: use the total study count
                            cell_text = f"{duration_str} ({study_count})"
                    elif study_count > 0:
                        if study_count_mode == "average":
                            # Average: studies per hour averaged across shifts
                            cell_text = f"({avg_studies})"
                        else:
                            cell_text = f"({study_count})"
                    else:
                        cell_text = "-"
                    
                    modality_durations.append(avg_duration)
                    row_cell_data.append((avg_duration, cell_text))
                
                # Calculate min/max for duration colors
                valid_durations = [d for d in modality_durations if d is not None]
                if valid_durations:
                    min_duration = min(valid_durations)
                    max_duration = max(valid_durations)
                    duration_range = max_duration - min_duration if max_duration > min_duration else 1
                else:
                    min_duration = max_duration = 0
                    duration_range = 1
                
                # Calculate min/max for count colors for this row (per-row calculation)
                valid_counts = [c for c in modality_counts_row if c > 0]
                if valid_counts:
                    min_count = min(valid_counts)
                    max_count = max(valid_counts)
                    count_range = max_count - min_count if max_count > min_count else 1
                else:
                    min_count = max_count = 0
                    count_range = 1
                
                row_data_list.append({
                    'modality': modality,
                    'cell_data': row_cell_data,
                    'count_data': modality_counts_row,  # Total counts
                    'avg_count_data': modality_avg_counts_row,  # Average counts (total / num_shifts)
                    'min_duration': min_duration,
                    'max_duration': max_duration,
                    'duration_range': duration_range,
                    'min_count': min_count,
                    'max_count': max_count,
                    'count_range': count_range
                })
            
            # Build TOTAL row data with color coding support
            if efficiency_data:
                total_hour_cells = []
                total_hour_durations = []
                total_hour_counts = []  # Total counts
                total_hour_avg_counts = []  # Average counts (total / num_shifts)
                total_shifts_per_hour = []  # Number of shifts with data in each hour
                
                for hour in hours_list:
                    hour_durations = []
                    hour_count = 0
                    hour_duration_count = 0
                    # Track unique shifts for this hour across all modalities
                    hour_shift_ids = set()
                    
                    for mod in efficiency_data.keys():
                        if hour in efficiency_data[mod]:
                            hour_durations.extend(efficiency_data[mod][hour])
                            hour_duration_count += len(efficiency_data[mod][hour])
                        # Count all studies for this hour across all modalities
                        if mod in study_count_data and hour in study_count_data[mod]:
                            hour_count += study_count_data[mod][hour]
                        # Collect shift IDs for this hour
                        if mod in shifts_per_hour and hour in shifts_per_hour[mod]:
                            hour_shift_ids.update(shifts_per_hour[mod][hour])
                    
                    num_shifts = len(hour_shift_ids) if hour_shift_ids else 0
                    if num_shifts == 0:
                        num_shifts = 1  # Avoid division by zero
                    avg_count = round(hour_count / num_shifts) if hour_count > 0 else 0
                    
                    total_hour_counts.append(hour_count if hour_count > 0 else None)
                    total_hour_avg_counts.append(avg_count)
                    total_shifts_per_hour.append(num_shifts)
                    
                    # Build cell text based on study count mode (will be rebuilt in draw_rows)
                    if hour_durations:
                        avg_duration = sum(hour_durations) / len(hour_durations)
                        duration_str = self._format_duration(avg_duration)
                        if study_count_mode == "average":
                            cell_text = f"{duration_str} ({avg_count})"
                        else:
                            cell_text = f"{duration_str} ({hour_count})"
                        total_hour_durations.append(avg_duration)
                    else:
                        if study_count_mode == "average":
                            cell_text = f"({avg_count})" if avg_count > 0 else "-"
                        elif study_count_mode == "total" and hour_count > 0:
                            cell_text = f"({hour_count})"
                        else:
                            cell_text = "-"
                        total_hour_durations.append(None)
                    
                    total_hour_cells.append(cell_text)
                
                # Calculate min/max for total row duration colors
                valid_total_durations = [d for d in total_hour_durations if d is not None]
                if valid_total_durations:
                    total_min_duration = min(valid_total_durations)
                    total_max_duration = max(valid_total_durations)
                    total_duration_range = total_max_duration - total_min_duration if total_max_duration > total_min_duration else 1
                else:
                    total_min_duration = total_max_duration = 0
                    total_duration_range = 1
                
                # Calculate min/max for total row count colors
                valid_total_counts = [c for c in total_hour_counts if c is not None and c > 0]
                if valid_total_counts:
                    total_min_count = min(valid_total_counts)
                    total_max_count = max(valid_total_counts)
                    total_count_range = total_max_count - total_min_count if total_max_count > total_min_count else 1
                else:
                    total_min_count = total_max_count = 0
                    total_count_range = 1
                
                total_row_data = {
                    'hour_cells': total_hour_cells,
                    'hour_durations': total_hour_durations,
                    'hour_counts': total_hour_counts,  # Total counts
                    'hour_avg_counts': total_hour_avg_counts,  # Average counts (total / num_shifts)
                    'min_duration': total_min_duration,
                    'max_duration': total_max_duration,
                    'duration_range': total_duration_range,
                    'min_count': total_min_count,
                    'max_count': total_max_count,
                    'count_range': total_count_range
                }
            
            # Initial draw
            draw_rows()
            
            # Store reference to draw_rows so it can be called when radio buttons change
            # This allows redrawing without full refresh_data() call
            if not hasattr(self, '_efficiency_redraw_functions'):
                self._efficiency_redraw_functions = []
            self._efficiency_redraw_functions.append(draw_rows)
            
            # Pack canvas and scrollbar
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            return canvas
        
        # Create two spreadsheet tables
        create_spreadsheet_table(self.efficiency_frame, night_hours, "Night Shift")
        create_spreadsheet_table(self.efficiency_frame, day_hours, "Day Shift")
    
    def _display_summary(self, records: List[dict]):
        """Display summary statistics using Canvas table."""
        # Clear any existing canvas table
        if hasattr(self, '_summary_table'):
            try:
                self._summary_table.clear()
            except:
                if hasattr(self, '_summary_table'):
                    self._summary_table.frame.pack_forget()
                    self._summary_table.frame.destroy()
                    delattr(self, '_summary_table')
        
        # Create Canvas table if it doesn't exist
        if not hasattr(self, '_summary_table'):
            columns = [
                {'name': 'metric', 'width': 300, 'text': 'Metric', 'sortable': True},
                {'name': 'value', 'width': 300, 'text': 'Value', 'sortable': True}  # Increased by 50% (200 -> 300)
            ]
            self._summary_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._summary_table.frame.pack_forget()  # Remove any existing packing
        self._summary_table.pack(fill=tk.BOTH, expand=True)
        self._summary_table.clear()
        
        total_studies = len(records)
        total_rvu = sum(r.get("rvu", 0) for r in records)
        avg_rvu = total_rvu / total_studies if total_studies > 0 else 0
        
        # Calculate time span - sum of actual shift durations, not time from first to last record
        hours = 0.0
        shifts_with_records = {}  # Initialize outside conditional
        if records:
            # Get all shifts (current and historical)
            all_shifts = []
            current_shift = self.data_manager.data.get("current_shift", {})
            if current_shift.get("shift_start"):
                all_shifts.append(current_shift)
            all_shifts.extend(self.data_manager.data.get("shifts", []))
            
            # Find which shifts contain these records and sum their durations
            record_times = []
            for r in records:
                try:
                    record_times.append(datetime.fromisoformat(r.get("time_performed", "")))
                except:
                    pass
            
            if record_times:
                # Find unique shifts that contain any of these records
                # Use shift_start as unique identifier since each shift has a unique start time
                for record_time in record_times:
                    for shift in all_shifts:
                        try:
                            shift_start_str = shift.get("shift_start")
                            if not shift_start_str:
                                continue
                            
                            shift_start = datetime.fromisoformat(shift_start_str)
                            shift_end_str = shift.get("shift_end")
                            
                            # Check if record falls within this shift
                            if shift_end_str:
                                shift_end = datetime.fromisoformat(shift_end_str)
                                if shift_start <= record_time <= shift_end:
                                    shifts_with_records[shift_start_str] = shift
                            else:
                                # Current shift without end - check if record is after shift start
                                if record_time >= shift_start:
                                    shifts_with_records[shift_start_str] = shift
                        except:
                            continue
                
                # Also check if the selected period spans multiple shifts by checking shift time ranges
                # This ensures we include all shifts in the period, even if they don't have records
                period = self.selected_period.get()
                if period in ["this_work_week", "last_work_week", "last_7_days", "last_30_days", "last_90_days", "custom_date_range", "all_time"]:
                    # For date range periods, also include shifts that fall within the period
                    period_start = None
                    period_end = None
                    now = datetime.now()
                    
                    if period == "this_work_week":
                        period_start, period_end = self._get_work_week_range(now, "this")
                    elif period == "last_work_week":
                        period_start, period_end = self._get_work_week_range(now, "last")
                    elif period == "last_7_days":
                        period_start = now - timedelta(days=7)
                        period_end = now
                    elif period == "last_30_days":
                        period_start = now - timedelta(days=30)
                        period_end = now
                    elif period == "last_90_days":
                        period_start = now - timedelta(days=90)
                        period_end = now
                    elif period == "custom_date_range":
                        try:
                            start_str = self.custom_start_date.get().strip()
                            end_str = self.custom_end_date.get().strip()
                            period_start = datetime.strptime(start_str, "%m/%d/%Y")
                            period_end = datetime.strptime(end_str, "%m/%d/%Y") + timedelta(days=1) - timedelta(seconds=1)
                        except:
                            period_start = None
                            period_end = None
                    elif period == "all_time":
                        period_start = datetime.min.replace(year=2000)
                        period_end = now
                    
                    # Include shifts that overlap with the period
                    if period_start and period_end:
                        for shift in all_shifts:
                            try:
                                shift_start_str = shift.get("shift_start")
                                if not shift_start_str:
                                    continue
                                
                                shift_start = datetime.fromisoformat(shift_start_str)
                                shift_end_str = shift.get("shift_end")
                                
                                # Check if shift overlaps with period
                                if shift_end_str:
                                    shift_end = datetime.fromisoformat(shift_end_str)
                                    # Shift overlaps if it starts before period ends and ends after period starts
                                    if shift_start <= period_end and shift_end >= period_start:
                                        shifts_with_records[shift_start_str] = shift
                                else:
                                    # Current shift - include if it starts before period ends
                                    if shift_start <= period_end:
                                        shifts_with_records[shift_start_str] = shift
                            except:
                                continue
                
                # Sum durations of unique shifts
                for shift_start_str, shift in shifts_with_records.items():
                    try:
                        shift_start = datetime.fromisoformat(shift_start_str)
                        shift_end_str = shift.get("shift_end")
                        
                        if shift_end_str:
                            shift_end = datetime.fromisoformat(shift_end_str)
                            shift_duration = (shift_end - shift_start).total_seconds() / 3600
                            hours += shift_duration
                        else:
                            # Current shift - use latest record time as end
                            if record_times:
                                latest_record_time = max(record_times)
                                if latest_record_time > shift_start:
                                    shift_duration = (latest_record_time - shift_start).total_seconds() / 3600
                                    hours += shift_duration
                    except:
                        continue
                
                rvu_per_hour = total_rvu / hours if hours > 0 else 0
                studies_per_hour = total_studies / hours if hours > 0 else 0
            else:
                rvu_per_hour = 0
                studies_per_hour = 0
        else:
            rvu_per_hour = 0
            studies_per_hour = 0
        
        # Modality breakdown with duration tracking - expand "Multiple" records
        modalities = {}
        modality_durations = {}  # Track durations for each modality
        for r in records:
            try:
                st = r.get("study_type", "Unknown")
                mod = st.split()[0] if st else "Unknown"
                
                # Check if this is a "Multiple" modality record that should be expanded
                if mod == "Multiple" or st.startswith("Multiple "):
                    # Expand into individual studies
                    individual_study_types = r.get("individual_study_types", [])
                    individual_procedures = r.get("individual_procedures", [])
                    accession_count = r.get("accession_count", 1)
                    duration = r.get("duration_seconds", 0)
                    duration_per_study = duration / accession_count if accession_count > 0 else 0
                    
                    # Check if we have individual data stored
                    has_individual_data = individual_study_types and len(individual_study_types) == accession_count
                    
                    if has_individual_data:
                        # Use stored individual data
                        for i in range(accession_count):
                            individual_st = individual_study_types[i] if i < len(individual_study_types) else "Unknown"
                            expanded_mod = individual_st.split()[0] if individual_st else "Unknown"
                            
                            modalities[expanded_mod] = modalities.get(expanded_mod, 0) + 1
                            
                            # Track duration for average calculation
                            if duration > 0:
                                if expanded_mod not in modality_durations:
                                    modality_durations[expanded_mod] = []
                                modality_durations[expanded_mod].append(duration_per_study)
                    elif individual_procedures and len(individual_procedures) == accession_count:
                        # Try to classify individual procedures if we don't have stored study types
                        rvu_table = self.data_manager.data.get("rvu_table", {})
                        classification_rules = self.data_manager.data.get("classification_rules", {})
                        direct_lookups = self.data_manager.data.get("direct_lookups", {})
                        
                        # match_study_type is defined at module level in this file
                        for i in range(accession_count):
                            procedure = individual_procedures[i] if i < len(individual_procedures) else ""
                            study_type, _ = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
                            
                            expanded_mod = study_type.split()[0] if study_type else "Unknown"
                            
                            modalities[expanded_mod] = modalities.get(expanded_mod, 0) + 1
                            
                            # Track duration for average calculation
                            if duration > 0:
                                if expanded_mod not in modality_durations:
                                    modality_durations[expanded_mod] = []
                                modality_durations[expanded_mod].append(duration_per_study)
                    else:
                        # Fallback: if we can't expand, extract modality from "Multiple XR" format
                        # Extract actual modality from "Multiple XR" -> "XR"
                        if st.startswith("Multiple "):
                            actual_modality = st.replace("Multiple ", "").strip()
                            if actual_modality:
                                expanded_mod = actual_modality.split()[0]
                            else:
                                expanded_mod = "Unknown"
                        else:
                            expanded_mod = "Unknown"
                        
                        modalities[expanded_mod] = modalities.get(expanded_mod, 0) + accession_count if accession_count > 0 else 1
                        
                        # Track duration for average calculation (split across accessions)
                        if duration > 0:
                            if expanded_mod not in modality_durations:
                                modality_durations[expanded_mod] = []
                            for _ in range(accession_count if accession_count > 0 else 1):
                                modality_durations[expanded_mod].append(duration_per_study)
                else:
                    # Regular record - not "Multiple"
                    modalities[mod] = modalities.get(mod, 0) + 1
                    
                    # Track duration for average calculation
                    duration = r.get("duration_seconds", 0)
                    if duration and duration > 0:
                        if mod not in modality_durations:
                            modality_durations[mod] = []
                        modality_durations[mod].append(duration)
            except Exception as e:
                # Log error but continue processing other records
                logger.error(f"Error processing record in summary modality breakdown: {e}")
                # Fallback: add as regular record
                try:
                    st = r.get("study_type", "Unknown")
                    mod = st.split()[0] if st else "Unknown"
                    modalities[mod] = modalities.get(mod, 0) + 1
                    duration = r.get("duration_seconds", 0)
                    if duration and duration > 0:
                        if mod not in modality_durations:
                            modality_durations[mod] = []
                        modality_durations[mod].append(duration)
                except:
                    pass
        
        top_modality = max(modalities.keys(), key=lambda k: modalities[k]) if modalities else "N/A"
        
        # Calculate shift-level metrics (1, 2, 6)
        # Use the records parameter and filter by shift, rather than shift.get("records")
        shift_stats = []
        if records and shifts_with_records:
            for shift_start_str, shift in shifts_with_records.items():
                # Filter records that belong to this shift
                shift_records = []
                try:
                    shift_start = datetime.fromisoformat(shift_start_str)
                    shift_end_str = shift.get("shift_end")
                    
                    for r in records:
                        try:
                            record_time = datetime.fromisoformat(r.get("time_performed", ""))
                            if shift_end_str:
                                shift_end = datetime.fromisoformat(shift_end_str)
                                if shift_start <= record_time <= shift_end:
                                    shift_records.append(r)
                            else:
                                # Current shift
                                if record_time >= shift_start:
                                    shift_records.append(r)
                        except:
                            continue
                except Exception as e:
                    logger.error(f"Error filtering records for shift {shift_start_str}: {e}")
                    continue
                
                # Include shift even if no records (for completeness), but skip if we can't calculate stats
                if not shift_records:
                    logger.debug(f"Shift {shift_start_str} has no records after filtering, skipping")
                    continue
                
                shift_rvu = sum(r.get("rvu", 0) for r in shift_records)
                shift_studies = len(shift_records)
                
                # Calculate shift duration
                try:
                    shift_start = datetime.fromisoformat(shift_start_str)
                    shift_end_str = shift.get("shift_end")
                    if shift_end_str:
                        shift_end = datetime.fromisoformat(shift_end_str)
                        shift_duration = (shift_end - shift_start).total_seconds() / 3600
                    else:
                        # Current shift - estimate from records
                        shift_record_times = []
                        for r in shift_records:
                            try:
                                shift_record_times.append(datetime.fromisoformat(r.get("time_performed", "")))
                            except:
                                pass
                        if shift_record_times:
                            latest_time = max(shift_record_times)
                            shift_duration = (latest_time - shift_start).total_seconds() / 3600
                            # Ensure minimum duration of 0.1 hours (6 minutes) for very short shifts
                            if shift_duration < 0.1 and shift_studies > 0:
                                shift_duration = 0.1
                        else:
                            # If no record times but we have studies, use a minimum duration
                            if shift_studies > 0:
                                shift_duration = 0.1  # Minimum 6 minutes
                            else:
                                shift_duration = 0
                    
                    shift_rvu_per_hour = shift_rvu / shift_duration if shift_duration > 0 else 0
                    
                    # Format shift date
                    shift_date = shift_start.strftime("%m/%d/%Y")
                    
                    # Only add shift if it has valid duration (duration > 0 means we can calculate rvu_per_hour)
                    # But we'll still include shifts with 0 duration if they have studies (for tracking)
                    shift_stats.append({
                        'date': shift_date,
                        'rvu': shift_rvu,
                        'rvu_per_hour': shift_rvu_per_hour,
                        'duration': shift_duration,
                        'studies': shift_studies
                    })
                except Exception as e:
                    logger.error(f"Error calculating stats for shift {shift_start_str}: {e}")
                    continue
        
        # Find highest RVU shift (1)
        highest_rvu_shift = None
        if shift_stats:
            highest_rvu_shift = max(shift_stats, key=lambda s: s['rvu'])
        
        # Find most efficient shift (2)
        most_efficient_shift = None
        if shift_stats:
            most_efficient_shift = max(shift_stats, key=lambda s: s['rvu_per_hour'])
        
        # Total shifts completed (6)
        total_shifts_completed = len(shift_stats)
        
        # Average time to read overall (10)
        all_durations = [r.get("duration_seconds", 0) for r in records if r.get("duration_seconds", 0) > 0]
        avg_time_to_read = sum(all_durations) / len(all_durations) if all_durations else 0
        
        # Calculate hourly metrics (11, 12, 13, 14) - averaged across shifts (typically best)
        # First, group records by shift
        records_by_shift = {}
        for r in records:
            # Find which shift this record belongs to
            record_time = None
            try:
                record_time = datetime.fromisoformat(r.get("time_performed", ""))
            except:
                continue
            
            # Find the shift this record belongs to
            record_shift = None
            for shift_start_str, shift in shifts_with_records.items():
                try:
                    shift_start = datetime.fromisoformat(shift_start_str)
                    shift_end_str = shift.get("shift_end")
                    if shift_end_str:
                        shift_end = datetime.fromisoformat(shift_end_str)
                        if shift_start <= record_time <= shift_end:
                            record_shift = shift_start_str
                            break
                    else:
                        # Current shift
                        if record_time >= shift_start:
                            record_shift = shift_start_str
                            break
                except:
                    continue
            
            if record_shift:
                if record_shift not in records_by_shift:
                    records_by_shift[record_shift] = []
                records_by_shift[record_shift].append(r)
        
        # Calculate hourly stats per shift, then average across shifts
        hourly_stats_per_shift = {}  # shift -> hour -> stats
        for shift_start_str, shift_records in records_by_shift.items():
            hourly_stats_per_shift[shift_start_str] = {}
            for r in shift_records:
                try:
                    time_performed = datetime.fromisoformat(r.get("time_performed", ""))
                    hour = time_performed.hour
                    
                    if hour not in hourly_stats_per_shift[shift_start_str]:
                        hourly_stats_per_shift[shift_start_str][hour] = {
                            'studies': 0,
                            'rvu': 0,
                            'durations': []
                        }
                    
                    hourly_stats_per_shift[shift_start_str][hour]['studies'] += 1
                    hourly_stats_per_shift[shift_start_str][hour]['rvu'] += r.get("rvu", 0)
                    duration = r.get("duration_seconds", 0)
                    if duration > 0:
                        hourly_stats_per_shift[shift_start_str][hour]['durations'].append(duration)
                except:
                    continue
        
        # Average hourly stats across all shifts
        hourly_stats = {}  # hour -> averaged stats
        all_hours = set()
        for shift_stats in hourly_stats_per_shift.values():
            all_hours.update(shift_stats.keys())
        
        for hour in all_hours:
            studies_list = []
            rvu_list = []
            durations_list = []
            
            for shift_stats in hourly_stats_per_shift.values():
                if hour in shift_stats:
                    studies_list.append(shift_stats[hour]['studies'])
                    rvu_list.append(shift_stats[hour]['rvu'])
                    durations_list.extend(shift_stats[hour]['durations'])
            
            if studies_list:  # Only include hours that appear in at least one shift
                hourly_stats[hour] = {
                    'studies': sum(studies_list) / len(studies_list) if studies_list else 0,  # Average studies per shift
                    'rvu': sum(rvu_list) / len(rvu_list) if rvu_list else 0,  # Average RVU per shift
                    'durations': durations_list,  # All durations for averaging
                    'total_studies': sum(studies_list),  # Keep total for display
                    'shift_count': len(studies_list)  # How many shifts had this hour
                }
        
        # Find busiest hour (11) - highest average studies per shift
        busiest_hour = None
        if hourly_stats:
            busiest_hour = max(hourly_stats.keys(), key=lambda h: hourly_stats[h]['studies'])
        
        # Find most productive hour (12) - highest average RVU per shift
        most_productive_hour = None
        if hourly_stats:
            most_productive_hour = max(hourly_stats.keys(), key=lambda h: hourly_stats[h]['rvu'])
        
        # Find fastest hour (14) - shortest average time to read (averaged across all studies in that hour)
        fastest_hour = None
        fastest_avg_duration = float('inf')
        if hourly_stats:
            for hour, stats in hourly_stats.items():
                if stats['durations']:
                    avg_duration = sum(stats['durations']) / len(stats['durations'])
                    if avg_duration < fastest_avg_duration:
                        fastest_avg_duration = avg_duration
                        fastest_hour = hour
        
        # Calculate consistency score (20) - Coefficient of Variation
        consistency_score = None
        # Check if we have enough shifts with valid data (need at least 2 shifts with RVU per hour > 0)
        # Filter to only shifts that have duration > 0 and rvu_per_hour > 0
        valid_shift_stats = []
        for s in shift_stats:
            if isinstance(s, dict):
                duration = s.get('duration', 0)
                rvu_ph = s.get('rvu_per_hour', 0)
                studies = s.get('studies', 0)
                # Include shift if it has valid duration and positive RVU per hour
                if duration > 0 and rvu_ph > 0:
                    valid_shift_stats.append(s)
        
        logger.debug(f"Shift stats calculation: total shifts={len(shift_stats)}, valid shifts={len(valid_shift_stats)}, shifts_with_records={len(shifts_with_records)}")
        if len(valid_shift_stats) > 1:
            rvu_per_hour_values = [s['rvu_per_hour'] for s in valid_shift_stats]
            if rvu_per_hour_values and len(rvu_per_hour_values) > 1:
                mean_rvu_per_hour = sum(rvu_per_hour_values) / len(rvu_per_hour_values)
                if mean_rvu_per_hour > 0:
                    variance = sum((x - mean_rvu_per_hour) ** 2 for x in rvu_per_hour_values) / len(rvu_per_hour_values)
                    std_dev = variance ** 0.5
                    coefficient_of_variation = (std_dev / mean_rvu_per_hour) * 100
                    consistency_score = coefficient_of_variation
                else:
                    logger.debug(f"Mean RVU per hour is 0, cannot calculate variability")
            else:
                logger.debug(f"Not enough rvu_per_hour_values: {len(rvu_per_hour_values) if rvu_per_hour_values else 0}")
        else:
            logger.debug(f"Not enough valid shifts: {len(valid_shift_stats)} (need 2+)")
        
        # Helper function to format hour
        def format_hour(h):
            if h is None:
                return "N/A"
            hour_12 = h % 12 or 12
            am_pm = "am" if h < 12 else "pm"
            return f"{hour_12}{am_pm}"
        
        # Add summary rows to Canvas table
        self._summary_table.add_row({'metric': 'Total Studies', 'value': str(total_studies)})
        self._summary_table.add_row({'metric': 'Total RVU', 'value': f"{total_rvu:.1f}"})
        self._summary_table.add_row({'metric': 'Average RVU per Study', 'value': f"{avg_rvu:.2f}"})
        
        # Calculate compensation above average RVU per study (in dollars per hour)
        above_avg_records = [r for r in records if r.get("rvu", 0) > avg_rvu]
        if above_avg_records and hours > 0:
            # Calculate total compensation for above-average studies
            above_avg_compensation = sum(self._calculate_study_compensation(r) for r in above_avg_records)
            # Calculate compensation per hour
            above_avg_comp_per_hour = above_avg_compensation / hours
            self._summary_table.add_row({
                'metric': 'Hourly compensation rate',
                'value': f"${above_avg_comp_per_hour:,.2f}/hr"
            })
        else:
            self._summary_table.add_row({
                'metric': 'Hourly compensation rate',
                'value': 'N/A'
            })
        
        # Calculate XR vs CT efficiency metrics
        xr_records = []
        ct_records = []
        
        for r in records:
            study_type = r.get("study_type", "").upper()
            # Check if it's XR (including CR, X-ray, Radiograph)
            if study_type.startswith("XR") or study_type.startswith("CR") or "X-RAY" in study_type or "RADIOGRAPH" in study_type:
                xr_records.append(r)
            # Check if it's CT (including CTA)
            elif study_type.startswith("CT"):
                ct_records.append(r)
        
        # Get average compensation rate from compensation_rates structure
        # Use a representative rate (e.g., weekday partner 11pm = 40, or average)
        compensation_rates = self.data_manager.data.get("compensation_rates", {})
        compensation_rate = 0
        if compensation_rates:
            # Try to get a representative rate (weekday partner 11pm as default, or average)
            try:
                role = self.data_manager.data["settings"].get("role", "Partner").lower()
                role_key = "partner" if role == "partner" else "assoc"
                # Use 11pm weekday rate as representative, or calculate average
                if "weekday" in compensation_rates and role_key in compensation_rates["weekday"]:
                    rates_dict = compensation_rates["weekday"][role_key]
                    # Use 11pm rate, or calculate average of all rates
                    compensation_rate = rates_dict.get("11pm", 0)
                    if compensation_rate == 0:
                        # Calculate average if 11pm not found
                        all_rates = [v for v in rates_dict.values() if isinstance(v, (int, float)) and v > 0]
                        compensation_rate = sum(all_rates) / len(all_rates) if all_rates else 0
            except Exception as e:
                logger.debug(f"Error getting compensation rate: {e}")
                compensation_rate = 0
        
        # Calculate XR efficiency
        if xr_records:
            xr_total_rvu = sum(r.get("rvu", 0) for r in xr_records)
            xr_total_minutes = sum(r.get("duration_seconds", 0) for r in xr_records) / 60.0
            xr_rvu_per_minute = xr_total_rvu / xr_total_minutes if xr_total_minutes > 0 else 0
            
            # Calculate studies and time to reach $100 compensation
            # Always calculate time, even if compensation rate is 0 (will show N/A for studies)
            if compensation_rate > 0 and xr_rvu_per_minute > 0:
                target_rvu = 100.0 / compensation_rate  # RVU needed for $100
                xr_avg_rvu_per_study = xr_total_rvu / len(xr_records) if xr_records else 0
                xr_studies_for_100 = target_rvu / xr_avg_rvu_per_study if xr_avg_rvu_per_study > 0 else 0
                # Calculate time directly from RVU per minute rate (more accurate)
                xr_time_for_100_minutes = target_rvu / xr_rvu_per_minute if xr_rvu_per_minute > 0 else 0
                xr_time_for_100_formatted = self._format_duration(xr_time_for_100_minutes * 60) if xr_time_for_100_minutes > 0 else "N/A"
            elif xr_rvu_per_minute > 0:
                # Calculate time to reach 100 RVU if compensation rate not set
                target_rvu = 100.0
                xr_time_for_100_minutes = target_rvu / xr_rvu_per_minute if xr_rvu_per_minute > 0 else 0
                xr_time_for_100_formatted = self._format_duration(xr_time_for_100_minutes * 60) if xr_time_for_100_minutes > 0 else "N/A"
                xr_studies_for_100 = 0  # Can't calculate without rate
            else:
                xr_studies_for_100 = 0
                xr_time_for_100_formatted = "N/A"
        else:
            xr_rvu_per_minute = 0
            xr_studies_for_100 = 0
            xr_time_for_100_formatted = "N/A"
        
        # Calculate CT efficiency
        if ct_records:
            ct_total_rvu = sum(r.get("rvu", 0) for r in ct_records)
            ct_total_minutes = sum(r.get("duration_seconds", 0) for r in ct_records) / 60.0
            ct_rvu_per_minute = ct_total_rvu / ct_total_minutes if ct_total_minutes > 0 else 0
            
            # Calculate studies and time to reach $100 compensation
            # Always calculate time, even if compensation rate is 0 (will show N/A for studies)
            if compensation_rate > 0 and ct_rvu_per_minute > 0:
                target_rvu = 100.0 / compensation_rate  # RVU needed for $100
                ct_avg_rvu_per_study = ct_total_rvu / len(ct_records) if ct_records else 0
                ct_studies_for_100 = target_rvu / ct_avg_rvu_per_study if ct_avg_rvu_per_study > 0 else 0
                # Calculate time directly from RVU per minute rate (more accurate)
                ct_time_for_100_minutes = target_rvu / ct_rvu_per_minute if ct_rvu_per_minute > 0 else 0
                ct_time_for_100_formatted = self._format_duration(ct_time_for_100_minutes * 60) if ct_time_for_100_minutes > 0 else "N/A"
            elif ct_rvu_per_minute > 0:
                # Calculate time to reach 100 RVU if compensation rate not set
                target_rvu = 100.0
                ct_time_for_100_minutes = target_rvu / ct_rvu_per_minute if ct_rvu_per_minute > 0 else 0
                ct_time_for_100_formatted = self._format_duration(ct_time_for_100_minutes * 60) if ct_time_for_100_minutes > 0 else "N/A"
                ct_studies_for_100 = 0  # Can't calculate without rate
            else:
                ct_studies_for_100 = 0
                ct_time_for_100_formatted = "N/A"
        else:
            ct_rvu_per_minute = 0
            ct_studies_for_100 = 0
            ct_time_for_100_formatted = "N/A"
        
        # Add XR vs CT efficiency metrics (grouped: RVU/min together, then to $100 together)
        self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
        self._summary_table.add_row({'metric': 'XR vs CT Efficiency:', 'value': ''})
        
        # Group RVU per minute together
        if xr_records:
            self._summary_table.add_row({
                'metric': '  XR RVU per Minute',
                'value': f"{xr_rvu_per_minute:.3f}"
            })
        else:
            self._summary_table.add_row({'metric': '  XR RVU per Minute', 'value': 'N/A (no XR studies)'})
        
        if ct_records:
            self._summary_table.add_row({
                'metric': '  CT RVU per Minute',
                'value': f"{ct_rvu_per_minute:.3f}"
            })
        else:
            self._summary_table.add_row({'metric': '  CT RVU per Minute', 'value': 'N/A (no CT studies)'})
        
        # Group "to $100" together
        if xr_records:
            if compensation_rate > 0 and xr_studies_for_100 > 0 and xr_time_for_100_formatted != "N/A":
                self._summary_table.add_row({
                    'metric': '  XR to $100',
                    'value': f"{xr_studies_for_100:.1f} studies, {xr_time_for_100_formatted}"
                })
            elif xr_time_for_100_formatted != "N/A":
                # Show time even if compensation rate not set
                self._summary_table.add_row({
                    'metric': '  XR to $100',
                    'value': f"{xr_time_for_100_formatted} (rate not set)"
                })
            else:
                self._summary_table.add_row({
                    'metric': '  XR to $100',
                    'value': 'N/A'
                })
        
        if ct_records:
            if compensation_rate > 0 and ct_studies_for_100 > 0 and ct_time_for_100_formatted != "N/A":
                self._summary_table.add_row({
                    'metric': '  CT to $100',
                    'value': f"{ct_studies_for_100:.1f} studies, {ct_time_for_100_formatted}"
                })
            elif ct_time_for_100_formatted != "N/A":
                # Show time even if compensation rate not set
                self._summary_table.add_row({
                    'metric': '  CT to $100',
                    'value': f"{ct_time_for_100_formatted} (rate not set)"
                })
            else:
                self._summary_table.add_row({
                    'metric': '  CT to $100',
                    'value': 'N/A'
                })
        
        self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
        
        # Shift-level metrics section
        self._summary_table.add_row({'metric': 'Time Span', 'value': f"{hours:.1f} hours"})
        self._summary_table.add_row({'metric': 'Studies per Hour', 'value': f"{studies_per_hour:.1f}"})
        self._summary_table.add_row({'metric': 'RVU per Hour', 'value': f"{rvu_per_hour:.1f}"})
        self._summary_table.add_row({'metric': 'Total Shifts Completed', 'value': str(total_shifts_completed)})
        
        # Highest RVU shift (1)
        if highest_rvu_shift:
            self._summary_table.add_row({'metric': 'Highest RVU Shift', 'value': f"{highest_rvu_shift['date']}: {highest_rvu_shift['rvu']:.1f} RVU"})
        else:
            self._summary_table.add_row({'metric': 'Highest RVU Shift', 'value': 'N/A'})
        
        # Most efficient shift (2)
        if most_efficient_shift:
            self._summary_table.add_row({'metric': 'Most Efficient Shift', 'value': f"{most_efficient_shift['date']}: {most_efficient_shift['rvu_per_hour']:.1f} RVU/hr"})
        else:
            self._summary_table.add_row({'metric': 'Most Efficient Shift', 'value': 'N/A'})
        
        self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
        
        # Hourly metrics section
        # Display hourly metrics (averaged across shifts)
        if busiest_hour is not None:
            busiest_stats = hourly_stats[busiest_hour]
            avg_studies = busiest_stats['studies']
            total_studies = busiest_stats.get('total_studies', 0)
            shift_count = busiest_stats.get('shift_count', 0)
            self._summary_table.add_row({'metric': 'Busiest Hour', 'value': f"{format_hour(busiest_hour)} ({avg_studies:.1f} avg studies/shift, {total_studies} total)" if shift_count > 1 else f"{format_hour(busiest_hour)} ({total_studies} studies)"})
        else:
            self._summary_table.add_row({'metric': 'Busiest Hour', 'value': 'N/A'})
        
        if most_productive_hour is not None:
            productive_stats = hourly_stats[most_productive_hour]
            avg_rvu = productive_stats['rvu']
            total_rvu = sum(hourly_stats_per_shift[s].get(most_productive_hour, {}).get('rvu', 0) for s in records_by_shift.keys() if most_productive_hour in hourly_stats_per_shift.get(s, {}))
            shift_count = productive_stats.get('shift_count', 0)
            self._summary_table.add_row({'metric': 'Most Productive Hour', 'value': f"{format_hour(most_productive_hour)} ({avg_rvu:.1f} avg RVU/shift, {total_rvu:.1f} total)" if shift_count > 1 else f"{format_hour(most_productive_hour)} ({total_rvu:.1f} RVU)"})
        else:
            self._summary_table.add_row({'metric': 'Most Productive Hour', 'value': 'N/A'})
        
        # Fastest hour (14)
        if fastest_hour is not None:
            fastest_formatted = self._format_duration(fastest_avg_duration)
            fastest_studies = len(hourly_stats[fastest_hour]['durations'])
            self._summary_table.add_row({'metric': 'Fastest Hour', 'value': f"{format_hour(fastest_hour)} ({fastest_formatted} avg, {fastest_studies} studies)"})
        else:
            self._summary_table.add_row({'metric': 'Fastest Hour', 'value': 'N/A'})
        
        self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
        self._summary_table.add_row({'metric': 'Top Modality', 'value': f"{top_modality} ({modalities.get(top_modality, 0)} studies)"})
        
        # Recalculate total_studies after expanding "Multiple" records
        expanded_total_studies = sum(modalities.values()) if modalities else total_studies
        
        # Modality Breakdown - show each modality with percent volume and study count
        if modalities and expanded_total_studies > 0:
            self._summary_table.add_row({'metric': 'Modality Breakdown', 'value': ''})
            # Sort modalities alphabetically
            sorted_modalities = sorted(modalities.items(), key=lambda x: x[0].lower())
            for mod, count in sorted_modalities:
                percent = (count / expanded_total_studies) * 100
                self._summary_table.add_row({'metric': f"  {mod}", 'value': f"{percent:.1f}% ({count} studies)"})
        else:
            self._summary_table.add_row({'metric': 'Modality Breakdown', 'value': 'N/A'})
        
        # Add average time to read by modality
        if modality_durations:
            self._summary_table.add_row({'metric': '', 'value': ''})  # Spacer
            
            # Average time to read (10) - moved to just above "by Modality"
            avg_time_formatted = self._format_duration(avg_time_to_read) if avg_time_to_read > 0 else "N/A"
            self._summary_table.add_row({'metric': 'Average Time to Read', 'value': avg_time_formatted})
            
            self._summary_table.add_row({'metric': 'Average Time to Read by Modality', 'value': ''})
            # Sort modalities alphabetically
            modality_avgs = []
            for mod, durations in modality_durations.items():
                if durations:
                    avg_duration = sum(durations) / len(durations)
                    modality_avgs.append((mod, avg_duration, len(durations)))
            
            modality_avgs.sort(key=lambda x: x[0].lower())
            for mod, avg_duration, count in modality_avgs:
                avg_formatted = self._format_duration(avg_duration)
                self._summary_table.add_row({'metric': f"  {mod}", 'value': f"{avg_formatted} ({count} studies)"})
        
        # Update display once after all rows are added
        self._summary_table.update_data()
    
    def _calculate_study_compensation(self, record: dict) -> float:
        """Calculate compensation for a single study based on when it was finished."""
        try:
            time_finished = datetime.fromisoformat(record.get("time_finished", record.get("time_performed", "")))
            rate = self.app._get_compensation_rate(time_finished)
            return record.get("rvu", 0) * rate
        except (KeyError, ValueError, AttributeError):
            return 0.0
    
    def _display_compensation(self, records: List[dict]):
        """Display compensation view with study count, modality breakdown, and total compensation."""
        # Clear/create Canvas table
        if hasattr(self, '_compensation_table'):
            try:
                self._compensation_table.clear()
            except:
                if hasattr(self, '_compensation_table'):
                    self._compensation_table.frame.pack_forget()
                    self._compensation_table.frame.destroy()
                    delattr(self, '_compensation_table')
        
        if not hasattr(self, '_compensation_table'):
            columns = [
                {'name': 'category', 'width': 300, 'text': 'Category', 'sortable': False},
                {'name': 'value', 'width': 250, 'text': 'Value', 'sortable': False}
            ]
            self._compensation_table = CanvasTable(self.table_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._compensation_table.frame.pack_forget()  # Remove any existing packing
        self._compensation_table.pack(fill=tk.BOTH, expand=True)
        self._compensation_table.clear()
        
        # Calculate total compensation
        total_compensation = sum(self._calculate_study_compensation(r) for r in records)
        total_studies = len(records)
        total_rvu = sum(r.get("rvu", 0) for r in records)
        
        # Calculate hours elapsed - sum of actual shift durations, not time from first to last record
        hours_elapsed = 0.0
        if self.selected_period.get() == "current_shift" and self.app and self.app.shift_start:
            # For current shift, use actual elapsed time
            hours_elapsed = (datetime.now() - self.app.shift_start).total_seconds() / 3600
        elif records:
            # For historical periods, sum actual shift durations
            try:
                # Get all shifts (current and historical)
                all_shifts = []
                current_shift = self.data_manager.data.get("current_shift", {})
                if current_shift.get("shift_start"):
                    all_shifts.append(current_shift)
                all_shifts.extend(self.data_manager.data.get("shifts", []))
                
                # Find which shifts contain these records and sum their durations
                record_times = []
                for r in records:
                    try:
                        time_str = r.get("time_finished") or r.get("time_performed", "")
                        if time_str:
                            record_times.append(datetime.fromisoformat(time_str))
                    except:
                        pass
                
                shifts_with_records = {}
                if record_times:
                    # Find unique shifts that contain any of these records
                    for record_time in record_times:
                        for shift in all_shifts:
                            try:
                                shift_start_str = shift.get("shift_start")
                                if not shift_start_str:
                                    continue
                                
                                shift_start = datetime.fromisoformat(shift_start_str)
                                shift_end_str = shift.get("shift_end")
                                
                                # Check if record falls within this shift
                                if shift_end_str:
                                    shift_end = datetime.fromisoformat(shift_end_str)
                                    if shift_start <= record_time <= shift_end:
                                        shifts_with_records[shift_start_str] = shift
                                else:
                                    # Current shift without end - check if record is after shift start
                                    if record_time >= shift_start:
                                        shifts_with_records[shift_start_str] = shift
                            except:
                                continue
                
                # Also check if the selected period spans multiple shifts by checking shift time ranges
                # This ensures we include all shifts in the period, even if they don't have records
                period = self.selected_period.get()
                if period in ["this_work_week", "last_work_week", "last_7_days", "last_30_days", "last_90_days", "custom_date_range", "all_time"]:
                    # For date range periods, also include shifts that fall within the period
                    period_start = None
                    period_end = None
                    now = datetime.now()
                    
                    if period == "this_work_week":
                        period_start, period_end = self._get_work_week_range(now, "this")
                    elif period == "last_work_week":
                        period_start, period_end = self._get_work_week_range(now, "last")
                    elif period == "last_7_days":
                        period_start = now - timedelta(days=7)
                        period_end = now
                    elif period == "last_30_days":
                        period_start = now - timedelta(days=30)
                        period_end = now
                    elif period == "last_90_days":
                        period_start = now - timedelta(days=90)
                        period_end = now
                    elif period == "custom_date_range":
                        try:
                            start_str = self.custom_start_date.get().strip()
                            end_str = self.custom_end_date.get().strip()
                            period_start = datetime.strptime(start_str, "%m/%d/%Y")
                            period_end = datetime.strptime(end_str, "%m/%d/%Y") + timedelta(days=1) - timedelta(seconds=1)
                        except:
                            period_start = None
                            period_end = None
                    elif period == "all_time":
                        period_start = datetime.min.replace(year=2000)
                        period_end = now
                    
                    # Include shifts that overlap with the period
                    if period_start and period_end:
                        for shift in all_shifts:
                            try:
                                shift_start_str = shift.get("shift_start")
                                if not shift_start_str:
                                    continue
                                
                                shift_start = datetime.fromisoformat(shift_start_str)
                                shift_end_str = shift.get("shift_end")
                                
                                # Check if shift overlaps with period
                                if shift_end_str:
                                    shift_end = datetime.fromisoformat(shift_end_str)
                                    # Shift overlaps if it starts before period ends and ends after period starts
                                    if shift_start <= period_end and shift_end >= period_start:
                                        shifts_with_records[shift_start_str] = shift
                                else:
                                    # Current shift - include if it starts before period ends
                                    if shift_start <= period_end:
                                        shifts_with_records[shift_start_str] = shift
                            except:
                                continue
                
                # Sum durations of unique shifts
                for shift_start_str, shift in shifts_with_records.items():
                    try:
                        shift_start = datetime.fromisoformat(shift_start_str)
                        shift_end_str = shift.get("shift_end")
                        
                        if shift_end_str:
                            shift_end = datetime.fromisoformat(shift_end_str)
                            shift_duration = (shift_end - shift_start).total_seconds() / 3600
                            hours_elapsed += shift_duration
                        else:
                            # Current shift - use latest record time as end
                            if record_times:
                                latest_record_time = max(record_times)
                                if latest_record_time > shift_start:
                                    shift_duration = (latest_record_time - shift_start).total_seconds() / 3600
                                    hours_elapsed += shift_duration
                    except:
                        continue
                
                # Fallback if no shifts found - use shift length
                if hours_elapsed == 0.0:
                    hours_elapsed = self.app.data_manager.data["settings"].get("shift_length_hours", 9.0) if self.app else 9.0
            except (ValueError, AttributeError):
                # Fallback to shift length if time parsing fails
                hours_elapsed = self.app.data_manager.data["settings"].get("shift_length_hours", 9.0) if self.app else 9.0
        else:
            # No records - use shift length as fallback
            hours_elapsed = self.app.data_manager.data["settings"].get("shift_length_hours", 9.0) if self.app else 9.0
        
        # Calculate compensation per hour
        comp_per_hour = total_compensation / hours_elapsed if hours_elapsed > 0 else 0.0
        
        # Calculate compensation per RVU
        comp_per_rvu = total_compensation / total_rvu if total_rvu > 0 else 0.0
        
        # Get compensation color from theme (dark green for light mode, lighter green for dark mode)
        comp_color = "dark green"
        if self.app and hasattr(self.app, 'theme_colors'):
            comp_color = self.app.theme_colors.get("comp_color", "dark green")
        
        # Add summary rows
        self._compensation_table.add_row({'category': 'Total Studies', 'value': str(total_studies)})
        self._compensation_table.add_row({'category': 'Total RVU', 'value': f"{total_rvu:.2f}"})
        self._compensation_table.add_row(
            {'category': 'Total Compensation', 'value': f"${total_compensation:,.2f}"},
            cell_text_colors={'value': comp_color}
        )
        self._compensation_table.add_row(
            {'category': 'Compensation per Hour', 'value': f"${comp_per_hour:,.2f}/hr"},
            cell_text_colors={'value': comp_color}
        )
        self._compensation_table.add_row(
            {'category': 'Compensation per RVU', 'value': f"${comp_per_rvu:,.2f}/RVU"},
            cell_text_colors={'value': comp_color}
        )
        self._compensation_table.add_row({'category': '', 'value': ''})  # Spacer
        
        # Modality breakdown - expand "Multiple" records into individual studies
        modality_stats = {}
        for r in records:
            st = r.get("study_type", "Unknown")
            mod = st.split()[0] if st else "Unknown"
            
            # Check if this is a "Multiple" modality record that should be expanded
            if mod == "Multiple" or st.startswith("Multiple "):
                # Expand into individual studies
                individual_study_types = r.get("individual_study_types", [])
                individual_rvus = r.get("individual_rvus", [])
                individual_procedures = r.get("individual_procedures", [])
                accession_count = r.get("accession_count", 1)
                total_rvu = r.get("rvu", 0)
                original_comp = self._calculate_study_compensation(r)
                
                # Check if we have individual data stored
                has_individual_data = (individual_study_types and individual_rvus and 
                                     len(individual_study_types) == accession_count and 
                                     len(individual_rvus) == accession_count)
                
                if has_individual_data:
                    # Use stored individual data
                    for i in range(accession_count):
                        individual_st = individual_study_types[i] if i < len(individual_study_types) else "Unknown"
                        individual_rvu = individual_rvus[i] if i < len(individual_rvus) else 0
                        
                        # Extract modality from individual study type
                        expanded_mod = individual_st.split()[0] if individual_st else "Unknown"
                        
                        # Initialize modality if needed
                        if expanded_mod not in modality_stats:
                            modality_stats[expanded_mod] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
                        
                        # Split compensation proportionally based on RVU
                        if total_rvu > 0:
                            comp_per_study = original_comp * (individual_rvu / total_rvu)
                        else:
                            comp_per_study = original_comp / accession_count if accession_count > 0 else 0
                        
                        modality_stats[expanded_mod]['count'] += 1
                        modality_stats[expanded_mod]['rvu'] += individual_rvu
                        modality_stats[expanded_mod]['compensation'] += comp_per_study
                elif individual_procedures and len(individual_procedures) == accession_count:
                    # Try to classify individual procedures if we don't have stored study types
                    rvu_table = self.data_manager.data.get("rvu_table", {})
                    classification_rules = self.data_manager.data.get("classification_rules", {})
                    direct_lookups = self.data_manager.data.get("direct_lookups", {})
                    
                    # match_study_type is defined at module level in this file
                    
                    rvu_per_study = total_rvu / accession_count if accession_count > 0 else 0
                    comp_per_study = original_comp / accession_count if accession_count > 0 else 0
                    
                    for i in range(accession_count):
                        procedure = individual_procedures[i] if i < len(individual_procedures) else ""
                        study_type, rvu = match_study_type(procedure, rvu_table, classification_rules, direct_lookups)
                        
                        expanded_mod = study_type.split()[0] if study_type else "Unknown"
                        
                        if expanded_mod not in modality_stats:
                            modality_stats[expanded_mod] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
                        
                        # Use calculated RVU if available, otherwise split evenly
                        actual_rvu = rvu if rvu > 0 else rvu_per_study
                        # Adjust compensation based on actual RVU if we calculated it
                        if rvu > 0 and total_rvu > 0:
                            actual_comp = original_comp * (actual_rvu / total_rvu)
                        else:
                            actual_comp = comp_per_study
                        
                        modality_stats[expanded_mod]['count'] += 1
                        modality_stats[expanded_mod]['rvu'] += actual_rvu
                        modality_stats[expanded_mod]['compensation'] += actual_comp
                else:
                    # Fallback: if we can't expand, extract modality from "Multiple XR" format
                    # Extract actual modality from "Multiple XR" -> "XR"
                    if st.startswith("Multiple "):
                        actual_modality = st.replace("Multiple ", "").strip()
                        if actual_modality:
                            expanded_mod = actual_modality.split()[0]
                        else:
                            expanded_mod = "Unknown"
                    else:
                        expanded_mod = "Unknown"
                    
                    if expanded_mod not in modality_stats:
                        modality_stats[expanded_mod] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
                    
                    # Split evenly across accession count
                    modality_stats[expanded_mod]['count'] += accession_count if accession_count > 0 else 1
                    modality_stats[expanded_mod]['rvu'] += total_rvu
                    modality_stats[expanded_mod]['compensation'] += original_comp
            else:
                # Regular record - not "Multiple"
                if mod not in modality_stats:
                    modality_stats[mod] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
                modality_stats[mod]['count'] += 1
                modality_stats[mod]['rvu'] += r.get("rvu", 0)
                modality_stats[mod]['compensation'] += self._calculate_study_compensation(r)
        
        # Sort modalities by compensation (highest first)
        sorted_modalities = sorted(modality_stats.items(), key=lambda x: x[1]['compensation'], reverse=True)
        
        self._compensation_table.add_row({'category': 'Modality Breakdown', 'value': ''})
        for mod, stats in sorted_modalities:
            # Format value with dollar amount at the end (will be colored green)
            comp_value = f"${stats['compensation']:,.2f}"
            value_text = f"{stats['count']} studies, {stats['rvu']:.2f} RVU, {comp_value}"
            # cell_text_colors will only color the dollar amount part
            self._compensation_table.add_row({
                'category': f"  {mod}",
                'value': value_text
            }, cell_text_colors={'value': comp_color})
        
        self._compensation_table.update_data()
    
    def _display_projection(self, records: List[dict]):
        """Display projection view with configurable days/hours and projected compensation."""
        # Projection settings frame - place in right panel (period_frame area or above table)
        # First, ensure we have a settings frame in the right panel
        if not hasattr(self, 'projection_settings_frame'):
            # Create settings frame in the right panel, below period_frame
            # We'll need to pack it above the table_frame
            self.projection_settings_frame = ttk.LabelFrame(self.right_panel, text="Projection Settings", padding="10")
        
        # Clear existing widgets in settings frame
        for widget in self.projection_settings_frame.winfo_children():
            widget.destroy()
        
        # Pack settings frame above table (before table_frame)
        self.projection_settings_frame.pack_forget()  # Remove from any previous location
        self.projection_settings_frame.pack(fill=tk.X, pady=(0, 10), before=self.table_frame)
        
        settings_frame = self.projection_settings_frame
        
        # Create or reuse compensation frame for results
        if not hasattr(self, 'compensation_frame') or self.compensation_frame is None:
            self.compensation_frame = ttk.Frame(self.table_frame)
        else:
            # Clear existing widgets
            for widget in self.compensation_frame.winfo_children():
                widget.destroy()
        
        self.compensation_frame.pack(fill=tk.BOTH, expand=True)
        
        # Ensure projection variables are initialized (should already be done in create_ui)
        if not hasattr(self, 'projection_days'):
            self.projection_days = tk.IntVar(value=14)
        if not hasattr(self, 'projection_extra_days'):
            self.projection_extra_days = tk.IntVar(value=0)
        if not hasattr(self, 'projection_extra_hours'):
            self.projection_extra_hours = tk.IntVar(value=0)
        
        ttk.Label(settings_frame, text="Base Days:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        days_spinbox = ttk.Spinbox(settings_frame, from_=1, to=31, width=10, 
                                   textvariable=self.projection_days, command=self.refresh_data)
        days_spinbox.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="Extra Days:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=5)
        extra_days_spinbox = ttk.Spinbox(settings_frame, from_=0, to=31, width=10,
                                         textvariable=self.projection_extra_days, command=self.refresh_data)
        extra_days_spinbox.grid(row=0, column=3, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="Extra Hours:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        extra_hours_spinbox = ttk.Spinbox(settings_frame, from_=0, to=100, width=10,
                                          textvariable=self.projection_extra_hours, command=self.refresh_data)
        extra_hours_spinbox.grid(row=1, column=1, padx=5, pady=5)
        
        ttk.Label(settings_frame, text="Hours per Day: 9 (11pm-8am)", font=("Arial", 9)).grid(
            row=1, column=2, columnspan=2, sticky=tk.W, padx=5, pady=5)
        
        # Calculate projection based on historical data
        total_days = self.projection_days.get() + self.projection_extra_days.get()
        base_hours = total_days * 9  # 9 hours per day (11pm-8am)
        total_hours = base_hours + self.projection_extra_hours.get()
        
        # Use historical data to project
        # Get recent historical data (last 3 months or available)
        now = datetime.now()
        start_date = now - timedelta(days=90)  # Last 3 months
        historical_records = self._get_records_in_range(start_date, now)
        
        if not historical_records:
            # No historical data
            results_frame = ttk.LabelFrame(self.compensation_frame, text="Projected Results", padding="10")
            results_frame.pack(fill=tk.BOTH, expand=True)
            ttk.Label(results_frame, text="No historical data available for projection.", 
                     font=("Arial", 10)).pack(pady=20)
            return
        
        # Calculate averages from historical data
        historical_studies = len(historical_records)
        historical_rvu = sum(r.get("rvu", 0) for r in historical_records)
        historical_compensation = sum(self._calculate_study_compensation(r) for r in historical_records)
        
        # Calculate historical hours worked (clipped to the date range)
        historical_hours = self._calculate_historical_hours(historical_records, start_date, now)
        
        if historical_hours > 0:
            rvu_per_hour = historical_rvu / historical_hours
            studies_per_hour = historical_studies / historical_hours
            compensation_per_hour = historical_compensation / historical_hours
        else:
            rvu_per_hour = 0
            studies_per_hour = 0
            compensation_per_hour = 0
        
        # Project for total_hours
        projected_rvu = rvu_per_hour * total_hours
        projected_studies = studies_per_hour * total_hours
        projected_compensation = compensation_per_hour * total_hours
        
        # Project by study type based on historical distribution
        study_type_distribution = {}
        for r in historical_records:
            st = r.get("study_type", "Unknown")
            if st not in study_type_distribution:
                study_type_distribution[st] = {'count': 0, 'rvu': 0.0, 'compensation': 0.0}
            study_type_distribution[st]['count'] += 1
            study_type_distribution[st]['rvu'] += r.get("rvu", 0)
            study_type_distribution[st]['compensation'] += self._calculate_study_compensation(r)
        
        # Normalize distribution
        if historical_studies > 0:
            for st in study_type_distribution:
                study_type_distribution[st]['percentage'] = study_type_distribution[st]['count'] / historical_studies
        
        # Results frame
        results_frame = ttk.LabelFrame(self.compensation_frame, text="Projected Results", padding="10")
        results_frame.pack(fill=tk.BOTH, expand=True)
        
        # Create Canvas table for projection results
        if hasattr(self, '_projection_table'):
            try:
                self._projection_table.clear()
            except:
                if hasattr(self, '_projection_table'):
                    self._projection_table.frame.pack_forget()
                    self._projection_table.frame.destroy()
                    delattr(self, '_projection_table')
        
        if not hasattr(self, '_projection_table'):
            columns = [
                {'name': 'metric', 'width': 300, 'text': 'Metric', 'sortable': True},
                {'name': 'value', 'width': 250, 'text': 'Projected Value', 'sortable': True}
            ]
            self._projection_table = CanvasTable(results_frame, columns, app=self.app)
        
        # Always pack the table to ensure it's visible
        self._projection_table.frame.pack_forget()  # Remove any existing packing
        self._projection_table.pack(fill=tk.BOTH, expand=True)
        self._projection_table.clear()
        
        # Get compensation color from theme (dark green for light mode, lighter green for dark mode)
        comp_color = "dark green"
        if self.app and hasattr(self.app, 'theme_colors'):
            comp_color = self.app.theme_colors.get("comp_color", "dark green")
        
        # Add projection summary
        self._projection_table.add_row({'metric': 'Projected Hours', 'value': f"{total_hours:.1f} hours ({total_days} days)"})
        self._projection_table.add_row({'metric': 'Projected Studies', 'value': f"{projected_studies:.1f}"})
        self._projection_table.add_row({'metric': 'Projected RVU', 'value': f"{projected_rvu:.2f}"})
        self._projection_table.add_row(
            {'metric': 'Projected Compensation', 'value': f"${projected_compensation:,.2f}"},
            cell_text_colors={'value': comp_color}
        )
        self._projection_table.add_row({'metric': '', 'value': ''})  # Spacer
        
        # Add historical averages used for projection
        self._projection_table.add_row({'metric': 'Based on Historical Data:', 'value': ''})
        self._projection_table.add_row({'metric': '', 'value': f"{historical_studies} studies over {historical_hours:.1f} hours"})
        self._projection_table.add_row({'metric': '', 'value': f"Average: {studies_per_hour:.2f} studies/hour"})
        self._projection_table.add_row({'metric': '', 'value': f"Average: {rvu_per_hour:.2f} RVU/hour"})
        self._projection_table.add_row(
            {'metric': '', 'value': f"Average: ${compensation_per_hour:.2f}/hour"},
            cell_text_colors={'value': comp_color}
        )
        self._projection_table.add_row({'metric': '', 'value': ''})  # Spacer
        
        # Projected study type breakdown
        self._projection_table.add_row({'metric': 'Projected Study Type Breakdown:', 'value': ''})
        sorted_study_types = sorted(study_type_distribution.items(), 
                                   key=lambda x: x[0])  # Sort by study type name
        
        # Show ALL study types, not just top 10
        for st, stats in sorted_study_types:
            projected_count = stats['percentage'] * projected_studies if historical_studies > 0 else 0
            projected_rvu_type = stats['percentage'] * projected_rvu if historical_studies > 0 else 0
            projected_comp_type = stats['percentage'] * projected_compensation if historical_studies > 0 else 0
            # Format with dollar amount - only the dollar amount will be colored green
            self._projection_table.add_row({
                'metric': f"  {st}",
                'value': f"{projected_count:.1f} studies, {projected_rvu_type:.2f} RVU, ${projected_comp_type:,.2f}"
            }, cell_text_colors={'value': comp_color})
        
        self._projection_table.update_data()
    
    def _calculate_historical_hours(self, records: List[dict], date_range_start: datetime = None, date_range_end: datetime = None) -> float:
        """
        Calculate total hours worked from historical records.
        
        Args:
            records: List of records in the date range
            date_range_start: Start of the date range being analyzed (to clip shifts)
            date_range_end: End of the date range being analyzed (to clip shifts)
        """
        # Get all shifts that contain these records
        all_shifts = []
        current_shift = self.data_manager.data.get("current_shift", {})
        if current_shift.get("shift_start"):
            all_shifts.append(current_shift)
        all_shifts.extend(self.data_manager.data.get("shifts", []))
        
        # Find unique shifts
        shifts_with_records = {}
        for r in records:
            try:
                record_time = datetime.fromisoformat(r.get("time_performed", ""))
                for shift in all_shifts:
                    shift_start_str = shift.get("shift_start")
                    if not shift_start_str:
                        continue
                    shift_start = datetime.fromisoformat(shift_start_str)
                    shift_end_str = shift.get("shift_end")
                    if shift_end_str:
                        shift_end = datetime.fromisoformat(shift_end_str)
                        if shift_start <= record_time <= shift_end:
                            shifts_with_records[shift_start_str] = shift
                    else:
                        if record_time >= shift_start:
                            shifts_with_records[shift_start_str] = shift
            except:
                continue
        
        # Sum durations, clipping to date range to avoid counting overlapping/shared time
        total_hours = 0.0
        # Track time periods to merge overlaps
        time_periods = []
        
        for shift_start_str, shift in shifts_with_records.items():
            try:
                shift_start = datetime.fromisoformat(shift_start_str)
                shift_end_str = shift.get("shift_end")
                if shift_end_str:
                    shift_end = datetime.fromisoformat(shift_end_str)
                else:
                    # Current shift - use CURRENT TIME, not last record time
                    # This ensures accurate hours worked for incomplete shifts
                    # (Using last record time would understate hours and inflate RVU/hour rate)
                    shift_end = datetime.now()
                    logger.debug(f"Using current time for incomplete shift duration calculation")
                
                # Clip shift to date range if provided
                if date_range_start is not None:
                    shift_start = max(shift_start, date_range_start)
                if date_range_end is not None:
                    shift_end = min(shift_end, date_range_end)
                
                # Only count if shift still has valid duration after clipping
                if shift_start < shift_end:
                    time_periods.append((shift_start, shift_end))
            except:
                continue
        
        # Merge overlapping time periods to avoid double-counting
        if time_periods:
            # Sort by start time
            time_periods.sort(key=lambda x: x[0])
            
            # Merge overlaps
            merged_periods = []
            current_start, current_end = time_periods[0]
            
            for start, end in time_periods[1:]:
                if start <= current_end:
                    # Overlaps or adjacent - merge
                    current_end = max(current_end, end)
                else:
                    # No overlap - save current and start new
                    merged_periods.append((current_start, current_end))
                    current_start, current_end = start, end
            
            # Don't forget the last period
            merged_periods.append((current_start, current_end))
            
            # Sum the merged periods
            for start, end in merged_periods:
                total_hours += (end - start).total_seconds() / 3600
        
        return total_hours
    
    def _populate_comparison_shifts(self, preserve_selection=True):
        """Populate the comparison shift comboboxes with available shifts.
        
        Args:
            preserve_selection: If True, keeps current selections if they're still valid
        """
        try:
            # Save current selections if preserving
            current_idx1 = self.comparison_shift1_index if preserve_selection else None
            current_idx2 = self.comparison_shift2_index if preserve_selection else None
            
            # Get all shifts from database (including current if it exists)
            all_shifts = []
            
            # Get current shift if it exists
            current_shift = self.data_manager.db.get_current_shift()
            if current_shift:
                all_shifts.append(current_shift)
            
            # Get historical shifts
            historical_shifts = self.data_manager.db.get_all_shifts()
            all_shifts.extend(historical_shifts)
            
            if not all_shifts:
                return
            
            # Format shift options for display
            shift_options = []
            for i, shift in enumerate(all_shifts):
                start = datetime.fromisoformat(shift['shift_start'])
                
                # Label with "Current" or date/time
                if shift.get('is_current'):
                    label = f"Current - {start.strftime('%a %m/%d %I:%M%p')}"
                else:
                    label = start.strftime("%a %m/%d %I:%M%p")
                
                # Get records for this shift and calculate stats
                records = self.data_manager.db.get_records_for_shift(shift['id'])
                # Expand multi-accession records
                records = self._expand_multi_accession_records(records)
                
                study_count = len(records)
                total_rvu = sum(r.get('rvu', 0) for r in records)
                
                display_text = f"{label} - {total_rvu:.1f} RVU ({study_count} studies)"
                shift_options.append(display_text)
            
            # Store the shifts list for later reference
            self.comparison_shifts_list = all_shifts
            
            # Update comboboxes
            self.comparison_shift1_combo['values'] = shift_options
            self.comparison_shift2_combo['values'] = shift_options
            
            # Set selections: use preserved selections if valid, otherwise defaults
            if preserve_selection and current_idx1 is not None and current_idx1 < len(all_shifts):
                self.comparison_shift1_index = current_idx1
            elif len(all_shifts) >= 1:
                self.comparison_shift1_index = 0
            
            if preserve_selection and current_idx2 is not None and current_idx2 < len(all_shifts):
                self.comparison_shift2_index = current_idx2
            elif len(all_shifts) >= 2:
                self.comparison_shift2_index = 1
            
            # Set default selections if not already set
            if self.comparison_shift1_index is None and len(all_shifts) >= 1:
                self.comparison_shift1_index = 0
            if self.comparison_shift2_index is None and len(all_shifts) >= 2:
                self.comparison_shift2_index = 1
            
            # Apply selections to comboboxes
            if self.comparison_shift1_index is not None and self.comparison_shift1_index < len(shift_options):
                self.comparison_shift1_combo.current(self.comparison_shift1_index)
            
            if self.comparison_shift2_index is not None and self.comparison_shift2_index < len(shift_options):
                self.comparison_shift2_combo.current(self.comparison_shift2_index)
            
        except Exception as e:
            logger.error(f"Error populating comparison shifts: {e}")
    
    def on_comparison_shift_selected(self, event=None):
        """Handle shift selection change in comparison mode."""
        try:
            # Get current selections
            idx1 = self.comparison_shift1_combo.current()
            idx2 = self.comparison_shift2_combo.current()
            
            # Only update if valid indices
            if idx1 >= 0:
                self.comparison_shift1_index = idx1
            if idx2 >= 0:
                self.comparison_shift2_index = idx2
            
            # Refresh the comparison view
            if self.view_mode.get() == "comparison":
                self._display_comparison()
                        
        except Exception as e:
            logger.error(f"Error handling comparison shift selection: {e}")
    
    def _update_comparison_graphs(self, changed_element: str):
        """Update comparison graphs WITHOUT full UI redraw.
        
        Only redraws the matplotlib figure contents, preserving scroll position and all UI state.
        This should NEVER call _display_comparison() - if widgets don't exist, do nothing.
        """
        # Verify we have valid widgets - if not, do nothing (don't trigger redraw)
        if not hasattr(self, '_comparison_canvas_widgets') or not self._comparison_canvas_widgets:
            logger.debug("_update_comparison_graphs: No canvas widgets, skipping")
            return
        
        if not hasattr(self, '_comparison_data1') or not hasattr(self, '_comparison_data2'):
            logger.debug("_update_comparison_graphs: No cached data, skipping")
            return
        
        # Verify widgets are still valid (not destroyed)
        try:
            # Quick validity check - try to access the widget
            test_widget = self._comparison_canvas_widgets[0].get_tk_widget()
            test_widget.winfo_exists()
        except Exception:
            logger.debug("_update_comparison_graphs: Canvas widgets destroyed, skipping")
            return
        
        try:
            # Get current scroll position BEFORE any updates
            canvas = getattr(self, '_comparison_scroll_canvas', None)
            scroll_pos = 0
            if canvas:
                try:
                    scroll_pos = canvas.yview()[0]
                except:
                    pass
            
            # Get theme colors
            theme_colors = self.app.get_theme_colors()
            is_dark = theme_colors['bg'] == '#2b2b2b'
            
            # Get the cached data
            data1 = self._comparison_data1
            data2 = self._comparison_data2
            
            # Get shift start times
            shift1_start_rounded = data1['shift_start_rounded']
            shift2_start_rounded = data2['shift_start_rounded']
            use_actual_time = shift1_start_rounded.hour == shift2_start_rounded.hour
            
            # Update figures in-place based on what changed
            if changed_element in ['mode', 'delta', 'all']:
                # Update RVU graphs (first figure - has 2 subplots)
                # Mode changes Graph 1, delta changes Graph 2
                if len(self._comparison_canvas_widgets) >= 1 and changed_element in ['mode', 'all']:
                    canvas_widget = self._comparison_canvas_widgets[0]
                    fig1 = canvas_widget.figure
                    
                    if len(fig1.axes) >= 2:
                        ax1, ax2 = fig1.axes[0], fig1.axes[1]
                        
                        # Clear and redraw axes
                        ax1.clear()
                        ax2.clear()
                        
                        # Re-apply dark mode colors
                        for ax in [ax1, ax2]:
                            ax.set_facecolor(theme_colors['bg'])
                            ax.tick_params(colors=theme_colors['fg'])
                            ax.xaxis.label.set_color(theme_colors['fg'])
                            ax.yaxis.label.set_color(theme_colors['fg'])
                            ax.title.set_color(theme_colors['fg'])
                            for spine in ax.spines.values():
                                spine.set_edgecolor(theme_colors['fg'] if is_dark else '#cccccc')
                        
                        # Redraw plots
                        self._plot_rvu_progression(ax1, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
                        self._plot_rvu_delta(ax2, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
                        
                        fig1.tight_layout(pad=2.5)
                        canvas_widget.draw_idle()  # Use draw_idle for better performance
                
                # Handle delta mode changes (only Graph 2 / ax2)
                if len(self._comparison_canvas_widgets) >= 1 and changed_element == 'delta':
                    canvas_widget = self._comparison_canvas_widgets[0]
                    fig1 = canvas_widget.figure
                    
                    if len(fig1.axes) >= 2:
                        ax2 = fig1.axes[1]
                        
                        # Clear and redraw only ax2
                        ax2.clear()
                        
                        # Re-apply dark mode colors
                        ax2.set_facecolor(theme_colors['bg'])
                        ax2.tick_params(colors=theme_colors['fg'])
                        ax2.xaxis.label.set_color(theme_colors['fg'])
                        ax2.yaxis.label.set_color(theme_colors['fg'])
                        ax2.title.set_color(theme_colors['fg'])
                        for spine in ax2.spines.values():
                            spine.set_edgecolor(theme_colors['fg'] if is_dark else '#cccccc')
                        
                        # Redraw only delta plot
                        self._plot_rvu_delta(ax2, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
                        
                        fig1.tight_layout(pad=2.5)
                        canvas_widget.draw_idle()
            
            if changed_element in ['modality', 'all']:
                # Update study count graph (second figure)
                if len(self._comparison_canvas_widgets) >= 2:
                    canvas_widget = self._comparison_canvas_widgets[1]
                    fig2 = canvas_widget.figure
                    
                    if len(fig2.axes) >= 1:
                        ax3 = fig2.axes[0]
                        
                        # Clear and redraw
                        ax3.clear()
                        
                        # Re-apply dark mode colors
                        ax3.set_facecolor(theme_colors['bg'])
                        ax3.tick_params(colors=theme_colors['fg'])
                        ax3.xaxis.label.set_color(theme_colors['fg'])
                        ax3.yaxis.label.set_color(theme_colors['fg'])
                        ax3.title.set_color(theme_colors['fg'])
                        for spine in ax3.spines.values():
                            spine.set_edgecolor(theme_colors['fg'] if is_dark else '#cccccc')
                        
                        # Get selected modality
                        selected_modality = self.comparison_modality_filter.get()
                        
                        if selected_modality == "all":
                            self._plot_total_studies(ax3, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
                        else:
                            self._plot_modality_progression(ax3, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, selected_modality, theme_colors)
                        
                        fig2.tight_layout(pad=2.5)
                        canvas_widget.draw_idle()  # Use draw_idle for better performance
            
            # Restore scroll position
            if canvas and scroll_pos > 0:
                # Use after_idle to ensure scroll happens after drawing
                self.window.after_idle(lambda: canvas.yview_moveto(scroll_pos))
                
        except Exception as e:
            logger.error(f"Error updating comparison graphs: {e}", exc_info=True)
            # Do NOT call _display_comparison here - just log the error
    
    def _restore_scroll_position(self, position):
        """Restore scroll position after redraw."""
        try:
            canvas = getattr(self, '_comparison_scroll_canvas', None)
            if canvas:
                canvas.yview_moveto(position)
        except:
            pass
    
    def _display_comparison(self):
        """Display shift comparison view with graphs and numerical comparisons."""
        if not HAS_MATPLOTLIB:
            # Show message if matplotlib is not available
            error_label = ttk.Label(self.table_frame, 
                                   text="Matplotlib is required for comparison view.\nPlease install: pip install matplotlib",
                                   font=("Arial", 12), foreground="red")
            error_label.pack(pady=50)
            self.summary_label.config(text="Comparison view unavailable")
            return
        
        # Get the two shifts to compare from the stored list
        shifts = getattr(self, 'comparison_shifts_list', [])
        if not shifts:
            error_label = ttk.Label(self.table_frame, 
                                   text="No shifts available for comparison",
                                   font=("Arial", 12), foreground="red")
            error_label.pack(pady=50)
            self.summary_label.config(text="No shifts available")
            return
        
        if len(shifts) < 2:
            error_label = ttk.Label(self.table_frame, 
                                   text="At least two shifts are required for comparison.\nComplete more shifts to use this feature.",
                                   font=("Arial", 12), foreground="orange")
            error_label.pack(pady=50)
            self.summary_label.config(text="Need at least 2 shifts to compare")
            return
        
        if self.comparison_shift1_index is None or self.comparison_shift2_index is None:
            self.summary_label.config(text="Please select two shifts to compare")
            return
        
        if self.comparison_shift1_index >= len(shifts) or self.comparison_shift2_index >= len(shifts):
            self.summary_label.config(text="Invalid shift selection")
            return
        
        shift1 = shifts[self.comparison_shift1_index]
        shift2 = shifts[self.comparison_shift2_index]
        
        # Get records for each shift
        records1 = self.data_manager.db.get_records_for_shift(shift1['id'])
        records2 = self.data_manager.db.get_records_for_shift(shift2['id'])
        
        # Expand multi-accession records
        records1 = self._expand_multi_accession_records(records1)
        records2 = self._expand_multi_accession_records(records2)
        
        # Initialize modality selection if not exists
        if not hasattr(self, 'comparison_modality_filter'):
            self.comparison_modality_filter = tk.StringVar(value="all")
        
        # Create persistent control frame OUTSIDE scrollable area (only once)
        if not hasattr(self, '_comparison_controls_frame') or self._comparison_controls_frame is None:
            # Clear existing content first
            for widget in self.table_frame.winfo_children():
                widget.destroy()
            
            # Create control frame at top (fixed, not scrollable)
            controls_frame = ttk.Frame(self.table_frame)
            controls_frame.pack(fill=tk.X, padx=10, pady=(5, 5))
            self._comparison_controls_frame = controls_frame
            
            # All graph controls on one line
            all_controls_frame = ttk.Frame(controls_frame)
            all_controls_frame.pack(fill=tk.X)
            
            # Graph 1 controls
            ttk.Label(all_controls_frame, text="Graph 1:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
            ttk.Radiobutton(all_controls_frame, text="Accumulation", variable=self.comparison_graph_mode,
                           value="accumulation", command=lambda: self._update_comparison_graphs('mode')).pack(side=tk.LEFT, padx=2)
            ttk.Radiobutton(all_controls_frame, text="Average", variable=self.comparison_graph_mode,
                           value="average", command=lambda: self._update_comparison_graphs('mode')).pack(side=tk.LEFT, padx=(2, 15))
            
            # Graph 2 controls
            ttk.Label(all_controls_frame, text="Graph 2:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
            ttk.Radiobutton(all_controls_frame, text="RVU Delta", variable=self.comparison_delta_mode,
                           value="rvu", command=lambda: self._update_comparison_graphs('delta')).pack(side=tk.LEFT, padx=2)
            ttk.Radiobutton(all_controls_frame, text="Percent Delta", variable=self.comparison_delta_mode,
                           value="percent", command=lambda: self._update_comparison_graphs('delta')).pack(side=tk.LEFT, padx=(2, 15))
            
            # Graph 3 controls (modality filter - will be populated dynamically below)
            ttk.Label(all_controls_frame, text="Graph 3:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
            self._comparison_modality_frame = all_controls_frame  # Store reference for dynamic population
            
            # Create scrollable content area below controls
            content_frame = ttk.Frame(self.table_frame)
            content_frame.pack(fill=tk.BOTH, expand=True)
            self._comparison_content_frame = content_frame
        else:
            # Controls exist, just clear the content area
            if hasattr(self, '_comparison_content_frame') and self._comparison_content_frame:
                for widget in self._comparison_content_frame.winfo_children():
                    widget.destroy()
        
        # Get the content frame for scrollable area
        content_frame = self._comparison_content_frame
        
        # Create scrollable frame for graphs
        canvas = tk.Canvas(content_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(content_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        # Store references for incremental updates
        self._comparison_scroll_canvas = canvas
        self._comparison_scrollable_frame = scrollable_frame
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Enable mouse wheel scrolling when mouse is over the canvas or its children
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        # Helper to bind mousewheel to a widget and all its children recursively
        def bind_mousewheel_recursive(widget):
            widget.bind("<MouseWheel>", on_mousewheel)
            for child in widget.winfo_children():
                bind_mousewheel_recursive(child)
        
        # Bind to the canvas and all widgets in scrollable_frame
        canvas.bind("<MouseWheel>", on_mousewheel)
        bind_mousewheel_recursive(scrollable_frame)
        
        # Store reference to unbind later if needed
        self._comparison_mousewheel_canvas = canvas
        self._comparison_mousewheel_frame = scrollable_frame
        self._comparison_mousewheel_callback = on_mousewheel
        
        # Get theme colors for dark mode support
        theme_colors = self.app.get_theme_colors()
        is_dark = theme_colors['bg'] == '#2b2b2b'
        
        # Calculate figure width based on available space (no controls panel anymore)
        window_width = self.window.winfo_width() if self.window.winfo_width() > 1 else 1350
        available_width = (window_width - 320) / 100  # Just left panel + padding
        fig_width = max(8, min(available_width, 12))  # Better width range
        
        selected_modality = self.comparison_modality_filter.get()
        
        # Process data for graphs and store for incremental updates
        data1 = self._process_shift_data_for_comparison(shift1, records1)
        data2 = self._process_shift_data_for_comparison(shift2, records2)
        
        # Store for incremental graph updates
        self._comparison_data1 = data1
        self._comparison_data2 = data2
        # Store records for minute-by-minute accumulation graph
        self._comparison_records1 = records1
        self._comparison_records2 = records2
        
        # Get rounded shift start times for x-axis display
        shift1_start_rounded = data1['shift_start_rounded']
        shift2_start_rounded = data2['shift_start_rounded']
        
        # Determine if shifts have matching times (compare rounded hours)
        use_actual_time = shift1_start_rounded.hour == shift2_start_rounded.hour
        
        # Align time ranges: extend shorter shift to match longer shift
        # This shows the full comparison without cutting off the longer shift
        max_hour1 = data1['max_hour']
        max_hour2 = data2['max_hour']
        
        # Use the maximum of both shifts (extend shorter one to match longer one)
        common_max_hour = max(max_hour1, max_hour2)
        
        # Pad shorter shift to match longer shift length
        self._align_shift_data(data1, common_max_hour)
        self._align_shift_data(data2, common_max_hour)
        
        # Store canvas widgets for cleanup
        canvas_widgets = []
        
        # Update modality filter radiobuttons in the persistent frame
        # Only destroy modality radiobuttons (after "Graph 3:" label)
        if hasattr(self, '_comparison_modality_frame'):
            modality_frame = self._comparison_modality_frame
            
            # Clear old modality radiobuttons only (identify by checking their text values)
            # We need to find widgets after the "Graph 3:" label and destroy them
            found_graph3_label = False
            widgets_to_destroy = []
            for widget in modality_frame.winfo_children():
                if isinstance(widget, ttk.Label) and "Graph 3:" in str(widget.cget("text")):
                    found_graph3_label = True
                elif found_graph3_label and isinstance(widget, ttk.Radiobutton):
                    widgets_to_destroy.append(widget)
            
            for widget in widgets_to_destroy:
                widget.destroy()
            
            # Get all unique modalities from both shifts
            all_modalities_set = set()
            for mod_dict in [data1['modality_cumulative'], data2['modality_cumulative']]:
                all_modalities_set.update(mod_dict.keys())
            all_modalities = sorted(list(all_modalities_set))
            
            # Add "All" option
            ttk.Radiobutton(modality_frame, text="All", variable=self.comparison_modality_filter,
                           value="all", command=lambda: self._update_comparison_graphs('modality')).pack(side=tk.LEFT, padx=2)
            
            # Add individual modality options (limit to 6 for space)
            for modality in all_modalities[:6]:
                ttk.Radiobutton(modality_frame, text=modality, variable=self.comparison_modality_filter,
                               value=modality, command=lambda m=modality: self._update_comparison_graphs('modality')).pack(side=tk.LEFT, padx=2)
        
        # === FIRST FIGURE: RVU Graphs (Graph 1 & 2) ===
        # Create first figure with 2 subplots - consistent spacing
        fig1 = Figure(figsize=(fig_width, 8), dpi=100)
        fig1.patch.set_facecolor(theme_colors['bg'])
        fig1.subplots_adjust(hspace=0.35)  # Consistent vertical spacing between subplots
        
        ax1 = fig1.add_subplot(2, 1, 1)  # RVU accumulation
        ax2 = fig1.add_subplot(2, 1, 2)  # Delta from average RVU
        
        # Apply dark mode colors
        for ax in [ax1, ax2]:
            ax.set_facecolor(theme_colors['bg'])
            ax.tick_params(colors=theme_colors['fg'])
            ax.xaxis.label.set_color(theme_colors['fg'])
            ax.yaxis.label.set_color(theme_colors['fg'])
            ax.title.set_color(theme_colors['fg'])
            for spine in ax.spines.values():
                spine.set_edgecolor(theme_colors['fg'] if is_dark else '#cccccc')
        
        # Plot 1: RVU Accumulation/Average
        self._plot_rvu_progression(ax1, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
        
        # Plot 2: Delta from Average RVU
        self._plot_rvu_delta(ax2, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
        
        fig1.tight_layout(pad=2.5)
        
        # Embed first figure
        canvas_widget1 = FigureCanvasTkAgg(fig1, master=scrollable_frame)
        canvas_widget1.draw()
        canvas_widget1.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))
        canvas_widgets.append(canvas_widget1)
        
        # === SECOND FIGURE: Study Count Graph (Graph 3) ===
        # Create second figure with 1 subplot
        fig2 = Figure(figsize=(fig_width, 4), dpi=100)
        fig2.patch.set_facecolor(theme_colors['bg'])
        
        ax3 = fig2.add_subplot(1, 1, 1)  # Study count
        
        # Apply dark mode colors
        ax3.set_facecolor(theme_colors['bg'])
        ax3.tick_params(colors=theme_colors['fg'])
        ax3.xaxis.label.set_color(theme_colors['fg'])
        ax3.yaxis.label.set_color(theme_colors['fg'])
        ax3.title.set_color(theme_colors['fg'])
        for spine in ax3.spines.values():
            spine.set_edgecolor(theme_colors['fg'] if is_dark else '#cccccc')
        
        # Plot 3: Study count (summed if "all", by modality if specific)
        if selected_modality == "all":
            # Sum all modalities = total studies
            self._plot_total_studies(ax3, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, theme_colors)
        else:
            self._plot_modality_progression(ax3, data1, data2, shift1_start_rounded, shift2_start_rounded, use_actual_time, selected_modality, theme_colors)
        
        fig2.tight_layout(pad=2.5)
        
        # Embed second figure
        canvas_widget2 = FigureCanvasTkAgg(fig2, master=scrollable_frame)
        canvas_widget2.draw()
        canvas_widget2.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))
        canvas_widgets.append(canvas_widget2)
        
        # Store references for cleanup
        self._comparison_canvas_widgets = canvas_widgets
        
        # Re-bind mousewheel to newly created widgets (matplotlib canvases)
        if hasattr(self, '_comparison_mousewheel_callback'):
            def bind_mousewheel_recursive(widget):
                widget.bind("<MouseWheel>", self._comparison_mousewheel_callback)
                for child in widget.winfo_children():
                    bind_mousewheel_recursive(child)
            
            bind_mousewheel_recursive(scrollable_frame)
        
        # Numerical comparison below graphs
        comparison_frame = ttk.LabelFrame(scrollable_frame, text="Numerical Comparison", padding="10")
        comparison_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
        
        # Create comparison table (use original shift start times for display)
        shift1_start = datetime.fromisoformat(shift1['shift_start'])
        shift2_start = datetime.fromisoformat(shift2['shift_start'])
        self._create_comparison_table(comparison_frame, shift1, shift2, records1, records2, 
                                     shift1_start, shift2_start)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Update summary
        total1 = len(records1)
        total2 = len(records2)
        rvu1 = sum(r.get("rvu", 0) for r in records1)
        rvu2 = sum(r.get("rvu", 0) for r in records2)
        
        self.summary_label.config(
            text=f"Current: {total1} studies, {rvu1:.1f} RVU  |  Prior: {total2} studies, {rvu2:.1f} RVU"
        )
    
    def _align_shift_data(self, data: dict, target_max_hour: int):
        """Align shift data to a target maximum hour by padding or trimming."""
        current_max = data['max_hour']
        
        if current_max < target_max_hour:
            # Pad with zeros/last values
            padding_needed = target_max_hour - current_max
            last_rvu = data['cumulative_rvu'][-1] if data['cumulative_rvu'] else 0
            last_studies = data['cumulative_studies'][-1] if data['cumulative_studies'] else 0
            
            for _ in range(padding_needed):
                data['cumulative_rvu'].append(last_rvu)
                data['cumulative_studies'].append(last_studies)
                # Pad averages (recalculate)
                hour = len(data['avg_rvu'])
                data['avg_rvu'].append(last_rvu / (hour + 1) if hour >= 0 else 0)
                data['avg_studies'].append(last_studies / (hour + 1) if hour >= 0 else 0)
                
                # Pad modality data
                for modality in data['modality_cumulative']:
                    last_val = data['modality_cumulative'][modality][-1] if data['modality_cumulative'][modality] else 0
                    data['modality_cumulative'][modality].append(last_val)
        
        elif current_max > target_max_hour:
            # Trim excess data
            data['cumulative_rvu'] = data['cumulative_rvu'][:target_max_hour + 1]
            data['cumulative_studies'] = data['cumulative_studies'][:target_max_hour + 1]
            data['avg_rvu'] = data['avg_rvu'][:target_max_hour + 1]
            data['avg_studies'] = data['avg_studies'][:target_max_hour + 1]
            
            for modality in data['modality_cumulative']:
                data['modality_cumulative'][modality] = data['modality_cumulative'][modality][:target_max_hour + 1]
        
        # Update max_hour
        data['max_hour'] = target_max_hour
    
    def _process_shift_data_for_comparison(self, shift: dict, records: List[dict]) -> dict:
        """Process shift data into hourly buckets for comparison graphs."""
        shift_start = datetime.fromisoformat(shift['shift_start'])
        
        # Normalize shift to standard 11pm start
        # If shift starts between 9pm-1am, round to 11pm
        # If shift starts outside this range, just round to nearest hour
        hour = shift_start.hour
        if 21 <= hour <= 23 or 0 <= hour <= 1:
            # Round to 11pm of the appropriate day
            if hour <= 1:  # After midnight, use previous day's 11pm
                shift_start_rounded = shift_start.replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=1)
            else:
                shift_start_rounded = shift_start.replace(hour=23, minute=0, second=0, microsecond=0)
        else:
            # For other start times, just round down to nearest hour
            shift_start_rounded = shift_start.replace(minute=0, second=0, microsecond=0)
        
        # Initialize hourly data structures
        hourly_data = {}
        
        for record in records:
            time_finished = datetime.fromisoformat(record['time_finished'])
            elapsed_hours = (time_finished - shift_start_rounded).total_seconds() / 3600
            hour_bucket = int(elapsed_hours)  # 0, 1, 2, etc.
            
            if hour_bucket not in hourly_data:
                hourly_data[hour_bucket] = {
                    'rvu': 0,
                    'study_count': 0,
                    'modalities': {}
                }
            
            hourly_data[hour_bucket]['rvu'] += record.get('rvu', 0)
            hourly_data[hour_bucket]['study_count'] += 1
            
            # Track by modality - extract from study_type
            study_type = record.get('study_type', 'Unknown')
            modality = study_type.split()[0] if study_type else "Unknown"
            # Handle "Multiple XR" -> extract "XR"
            if modality == "Multiple" and len(study_type.split()) > 1:
                modality = study_type.split()[1]
            
            if modality not in hourly_data[hour_bucket]['modalities']:
                hourly_data[hour_bucket]['modalities'][modality] = 0
            hourly_data[hour_bucket]['modalities'][modality] += 1
        
        # Calculate cumulative and average data
        max_hour = max(hourly_data.keys()) if hourly_data else 0
        cumulative_rvu = []
        cumulative_studies = []
        avg_rvu = []
        avg_studies = []
        modality_cumulative = {}
        
        for hour in range(max_hour + 1):
            if hour in hourly_data:
                # Accumulation
                prev_rvu = cumulative_rvu[-1] if cumulative_rvu else 0
                prev_studies = cumulative_studies[-1] if cumulative_studies else 0
                cumulative_rvu.append(prev_rvu + hourly_data[hour]['rvu'])
                cumulative_studies.append(prev_studies + hourly_data[hour]['study_count'])
                
                # Average (per hour up to this point)
                avg_rvu.append(cumulative_rvu[-1] / (hour + 1))
                avg_studies.append(cumulative_studies[-1] / (hour + 1))
                
                # Modality cumulative
                for modality, count in hourly_data[hour]['modalities'].items():
                    if modality not in modality_cumulative:
                        modality_cumulative[modality] = []
                    prev_count = modality_cumulative[modality][-1] if modality_cumulative[modality] else 0
                    modality_cumulative[modality].append(prev_count + count)
            else:
                # No studies in this hour, carry forward previous values
                cumulative_rvu.append(cumulative_rvu[-1] if cumulative_rvu else 0)
                cumulative_studies.append(cumulative_studies[-1] if cumulative_studies else 0)
                avg_rvu.append(cumulative_rvu[-1] / (hour + 1) if cumulative_rvu else 0)
                avg_studies.append(cumulative_studies[-1] / (hour + 1) if cumulative_studies else 0)
                
                for modality in modality_cumulative:
                    modality_cumulative[modality].append(modality_cumulative[modality][-1] if modality_cumulative[modality] else 0)
        
        # Calculate average RVU per study for the shift
        total_rvu = sum(r.get('rvu', 0) for r in records)
        total_studies = len(records)
        avg_rvu_per_study = total_rvu / total_studies if total_studies > 0 else 0
        
        return {
            'hourly_data': hourly_data,
            'cumulative_rvu': cumulative_rvu,
            'cumulative_studies': cumulative_studies,
            'avg_rvu': avg_rvu,
            'avg_studies': avg_studies,
            'modality_cumulative': modality_cumulative,
            'avg_rvu_per_study': avg_rvu_per_study,
            'max_hour': max_hour,
            'shift_start_rounded': shift_start_rounded
        }
    
    def _smooth_hourly_data(self, y_values: List[float], num_points: int = None) -> tuple:
        """Interpolate hourly data to create smoother curves.
        
        Args:
            y_values: List of hourly values
            num_points: Number of interpolated points per hour (default: 10 for smooth curve)
        
        Returns:
            Tuple of (x_indices, smoothed_y_values)
        """
        if not y_values or len(y_values) < 2:
            return list(range(len(y_values))), y_values
        
        if num_points is None:
            # Create 10 points per hour for smooth interpolation
            num_points = 10
        
        # Original x indices (hourly)
        x_original = np.array(range(len(y_values)))
        y_original = np.array(y_values)
        
        # Create finer x grid (more points between hours)
        x_smooth = np.linspace(0, len(y_values) - 1, len(y_values) * num_points)
        
        # Use cubic interpolation for smooth curves
        # For cubic, we need at least 4 points, so use linear if we have fewer
        if len(y_values) >= 4:
            # Use numpy's interpolation with cubic spline approximation
            # Create a simple cubic interpolation using numpy
            y_smooth = np.interp(x_smooth, x_original, y_original)
            
            # Apply a simple smoothing filter (moving average) for extra smoothness
            window_size = min(5, len(y_smooth) // 10)
            if window_size > 1:
                # Simple moving average
                kernel = np.ones(window_size) / window_size
                y_smooth = np.convolve(y_smooth, kernel, mode='same')
        else:
            # For fewer points, just use linear interpolation
            y_smooth = np.interp(x_smooth, x_original, y_original)
        
        return x_smooth.tolist(), y_smooth.tolist()
    
    def _plot_rvu_progression(self, ax, data1: dict, data2: dict, shift1_start: datetime, 
                             shift2_start: datetime, use_actual_time: bool, theme_colors: dict = None):
        """Plot RVU accumulation or average progression."""
        mode = self.comparison_graph_mode.get()
        
        if mode == "accumulation":
            # For accumulation mode, use minute-by-minute granularity
            # Get records for minute-by-minute calculation
            records1 = getattr(self, '_comparison_records1', [])
            records2 = getattr(self, '_comparison_records2', [])
            
            # Calculate minute-by-minute cumulative RVU
            def calculate_minute_by_minute(records, shift_start_rounded):
                """Calculate cumulative RVU at each minute."""
                if not records:
                    return [], []
                
                # Sort records by time_finished
                sorted_records = sorted(records, key=lambda r: datetime.fromisoformat(r.get('time_finished', '')))
                
                # Find the time range
                first_time = datetime.fromisoformat(sorted_records[0]['time_finished'])
                last_time = datetime.fromisoformat(sorted_records[-1]['time_finished'])
                
                # Calculate total minutes in shift (ensure at least 1 minute)
                total_minutes = max(1, int((last_time - shift_start_rounded).total_seconds() / 60) + 1)
                
                # Initialize minute buckets with cumulative RVU
                minute_rvu = {}  # minute_index -> RVU added at that minute
                
                for record in sorted_records:
                    try:
                        time_finished = datetime.fromisoformat(record['time_finished'])
                        elapsed_minutes = int((time_finished - shift_start_rounded).total_seconds() / 60)
                        # Ensure non-negative minute index
                        if elapsed_minutes < 0:
                            elapsed_minutes = 0
                        rvu = record.get('rvu', 0)
                        
                        if elapsed_minutes not in minute_rvu:
                            minute_rvu[elapsed_minutes] = 0
                        minute_rvu[elapsed_minutes] += rvu
                    except (ValueError, KeyError):
                        continue  # Skip invalid records
                
                # Build cumulative array - one entry per minute
                cumulative_data = []
                time_labels = []
                current_rvu = 0
                
                for minute in range(total_minutes):
                    if minute in minute_rvu:
                        current_rvu += minute_rvu[minute]
                    cumulative_data.append(current_rvu)
                    
                    # Create time label
                    time_at_minute = shift_start_rounded + timedelta(minutes=minute)
                    time_labels.append(time_at_minute.strftime("%H:%M"))
                
                return cumulative_data, time_labels
            
            y1, x1_labels = calculate_minute_by_minute(records1, shift1_start)
            y2, x2_labels = calculate_minute_by_minute(records2, shift2_start)
            
            # Handle empty data case
            if not y1 and not y2:
                # Fallback to hourly data if no records
                y1 = data1['cumulative_rvu']
                y2 = data2['cumulative_rvu']
                hours1 = list(range(len(y1)))
                hours2 = list(range(len(y2)))
                x1 = [(shift1_start + timedelta(hours=h)).strftime("%H:%M") for h in hours1]
                x2 = [(shift2_start + timedelta(hours=h)).strftime("%H:%M") for h in hours2]
            else:
                # Use the longer of the two for x-axis
                max_len = max(len(y1), len(y2)) if (y1 and y2) else (len(y1) if y1 else len(y2))
                
                # Extend shorter series to match longer one (carry forward last value)
                if len(y1) < max_len:
                    last_val = y1[-1] if y1 else 0
                    y1.extend([last_val] * (max_len - len(y1)))
                    last_label = x1_labels[-1] if x1_labels else shift1_start.strftime("%H:%M")
                    # Generate time labels for extended portion
                    for i in range(len(x1_labels), max_len):
                        time_at_minute = shift1_start + timedelta(minutes=i)
                        x1_labels.append(time_at_minute.strftime("%H:%M"))
                
                if len(y2) < max_len:
                    last_val = y2[-1] if y2 else 0
                    y2.extend([last_val] * (max_len - len(y2)))
                    last_label = x2_labels[-1] if x2_labels else shift2_start.strftime("%H:%M")
                    # Generate time labels for extended portion
                    for i in range(len(x2_labels), max_len):
                        time_at_minute = shift2_start + timedelta(minutes=i)
                        x2_labels.append(time_at_minute.strftime("%H:%M"))
                
                x1 = x1_labels
                x2 = x2_labels
            
            ylabel = "Cumulative RVU"
            title = "RVU Accumulation"
        else:
            # For average mode, use hourly data with smoothing
            y1 = data1['avg_rvu']
            y2 = data2['avg_rvu']
            ylabel = "Average RVU per Hour"
            title = "RVU Average per Hour"
            
            # Smooth the hourly data
            x1_smooth, y1_smooth = self._smooth_hourly_data(y1)
            x2_smooth, y2_smooth = self._smooth_hourly_data(y2)
            
            hours1 = list(range(len(y1)))
            hours2 = list(range(len(y2)))
            
            if use_actual_time:
                x1 = [(shift1_start + timedelta(hours=h)).strftime("%H:%M") for h in hours1]
                x2 = [(shift2_start + timedelta(hours=h)).strftime("%H:%M") for h in hours2]
            else:
                x1 = [f"Hour {h}" for h in hours1]
                x2 = [f"Hour {h}" for h in hours2]
        
        # Plot with appropriate style based on mode
        if mode == "accumulation":
            # Minute-by-minute: use smooth line without markers (too many points)
            ax.plot(range(len(y1)), y1, color='#4472C4', linewidth=2, label='Shift 1')
            ax.plot(range(len(y2)), y2, color='#9966CC', linewidth=2, label='Shift 2')
        else:
            # Hourly: use smoothed line without markers for smooth appearance
            ax.plot(x1_smooth, y1_smooth, color='#4472C4', linewidth=2, label='Shift 1')
            ax.plot(x2_smooth, y2_smooth, color='#9966CC', linewidth=2, label='Shift 2')
        
        if mode == "accumulation":
            ax.set_xlabel("Time", fontsize=10)
        else:
            ax.set_xlabel("Time" if use_actual_time else "Hours from Start", fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Set x-axis to start at zero with no left padding
        # For smoothed data, use original hourly length for x-axis limits
        if mode == "accumulation":
            max_len = max(len(x1), len(x2))
        else:
            # For smoothed hourly data, use original hourly count
            max_len = max(len(y1), len(y2)) if mode == "average" else max(len(x1), len(x2))
        
        if max_len > 0:
            ax.set_xlim(0, max_len - 1)
            ax.margins(x=0.01)
        
        # Set x-axis labels - for minute-by-minute, show every 30 minutes or every hour
        if max_len > 0:
            if mode == "accumulation":
                # For minute-by-minute: show labels every 30-60 minutes depending on shift length
                # Aim for ~10-15 labels total for readability
                if max_len <= 120:  # 2 hours or less: show every 15 minutes
                    step = 15
                elif max_len <= 480:  # 8 hours or less: show every 30 minutes
                    step = 30
                elif max_len <= 720:  # 12 hours or less: show every 60 minutes
                    step = 60
                else:  # Very long shift: show every 2 hours
                    step = 120
            else:
                # For hourly: show every other hour (use original hourly indices)
                step = max(1, max_len // 8)
            
            # Calculate tick positions
            if mode == "accumulation":
                tick_positions = list(range(0, max_len, step))
            else:
                # For hourly: ensure we don't go beyond max_len - 1 (the last hour bucket start)
                tick_positions = list(range(0, max_len, step))
                tick_positions = [p for p in tick_positions if p < max_len]
            
            ax.set_xticks(tick_positions)
            ax.set_xticklabels([x1[i] if i < len(x1) else x2[i] if i < len(x2) else "" 
                               for i in tick_positions], rotation=45, ha='right', fontsize=8)
    
    def _plot_rvu_delta(self, ax, data1: dict, data2: dict, shift1_start: datetime,
                       shift2_start: datetime, use_actual_time: bool, theme_colors: dict = None):
        """Plot hourly RVU delta from average (absolute or percent)."""
        # Get delta mode
        delta_mode = self.comparison_delta_mode.get() if hasattr(self, 'comparison_delta_mode') else 'rvu'
        
        # Calculate average RVU per hour for each shift
        total_hours1 = data1['max_hour'] + 1 if data1['max_hour'] >= 0 else 1
        total_hours2 = data2['max_hour'] + 1 if data2['max_hour'] >= 0 else 1
        total_rvu1 = data1['cumulative_rvu'][-1] if data1['cumulative_rvu'] else 0
        total_rvu2 = data2['cumulative_rvu'][-1] if data2['cumulative_rvu'] else 0
        avg_rvu_per_hour1 = total_rvu1 / total_hours1 if total_hours1 > 0 else 0
        avg_rvu_per_hour2 = total_rvu2 / total_hours2 if total_hours2 > 0 else 0

        # Calculate hourly RVU (RVU earned in each specific hour)
        delta1 = []
        delta2 = []

        for hour in range(len(data1['cumulative_rvu'])):
            if hour == 0:
                hourly_rvu = data1['cumulative_rvu'][0]
            else:
                hourly_rvu = data1['cumulative_rvu'][hour] - data1['cumulative_rvu'][hour-1]
            
            if delta_mode == 'percent':
                # Convert to percentage of average
                delta_val = ((hourly_rvu - avg_rvu_per_hour1) / avg_rvu_per_hour1 * 100) if avg_rvu_per_hour1 > 0 else 0
            else:
                delta_val = hourly_rvu - avg_rvu_per_hour1
            delta1.append(delta_val)

        for hour in range(len(data2['cumulative_rvu'])):
            if hour == 0:
                hourly_rvu = data2['cumulative_rvu'][0]
            else:
                hourly_rvu = data2['cumulative_rvu'][hour] - data2['cumulative_rvu'][hour-1]
            
            if delta_mode == 'percent':
                # Convert to percentage of average
                delta_val = ((hourly_rvu - avg_rvu_per_hour2) / avg_rvu_per_hour2 * 100) if avg_rvu_per_hour2 > 0 else 0
            else:
                delta_val = hourly_rvu - avg_rvu_per_hour2
            delta2.append(delta_val)

        hours1 = list(range(len(delta1)))
        hours2 = list(range(len(delta2)))

        if use_actual_time:
            # Time labels represent the START of each hour bucket (e.g., hour 7 = 7am for 7am-8am bucket)
            x1 = [(shift1_start + timedelta(hours=h)).strftime("%H:%M") for h in hours1]
            x2 = [(shift2_start + timedelta(hours=h)).strftime("%H:%M") for h in hours2]
        else:
            x1 = [f"Hour {h}" for h in hours1]
            x2 = [f"Hour {h}" for h in hours2]

        # Smooth the hourly delta data
        x1_smooth, delta1_smooth = self._smooth_hourly_data(delta1)
        x2_smooth, delta2_smooth = self._smooth_hourly_data(delta2)

        ax.plot(x1_smooth, delta1_smooth, color='#4472C4', linewidth=2, label='Shift 1')
        ax.plot(x2_smooth, delta2_smooth, color='#9966CC', linewidth=2, label='Shift 2')
        ax.axhline(y=0, color='gray', linestyle='--', alpha=0.5)

        ax.set_xlabel("Time" if use_actual_time else "Hours from Start", fontsize=10)
        
        if delta_mode == 'percent':
            ax.set_ylabel("Percent Delta from Average (%)", fontsize=10)
            ax.set_title("Hourly RVU Percent Delta", fontsize=12, fontweight='bold')
        else:
            ax.set_ylabel("Hourly RVU Delta from Average", fontsize=10)
            ax.set_title("Hourly RVU Delta", fontsize=12, fontweight='bold')
        
        ax.legend()
        ax.grid(True, alpha=0.3)

        # Set x-axis to end at the START of the last hour bucket
        # If we have 8 hours (0-7), the last bucket is 7am-8am, so end at position 7 (7am)
        max_len = max(len(delta1), len(delta2))
        if max_len > 0:
            # End at max_len - 1 (the start of the last hour bucket)
            ax.set_xlim(0, max_len - 1)
            ax.margins(x=0.01)

        # Set x-axis labels (use original hourly indices)
        if max_len > 0:
            step = max(1, max_len // 8)
            # Only show labels up to max_len - 1 (the last hour bucket start)
            tick_positions = list(range(0, max_len, step))
            # Ensure we don't go beyond max_len - 1
            tick_positions = [p for p in tick_positions if p < max_len]
            ax.set_xticks(tick_positions)
            ax.set_xticklabels([x1[i] if i < len(x1) else x2[i] if i < len(x2) else ""
                               for i in tick_positions], rotation=45, ha='right', fontsize=8)
    
    def _plot_modality_progression(self, ax, data1: dict, data2: dict, shift1_start: datetime, 
                                   shift2_start: datetime, use_actual_time: bool, modality_filter: str = "all", theme_colors: dict = None):
        """Plot study accumulation by modality - minute-by-minute granularity."""
        mode = self.comparison_graph_mode.get()
        
        # Get records for minute-by-minute calculation
        records1 = getattr(self, '_comparison_records1', [])
        records2 = getattr(self, '_comparison_records2', [])
        
        # Determine which modalities to plot based on filter
        if modality_filter == "all":
            # Get all modalities from both shifts, sorted by total count
            all_modalities = {}
            for mod_dict in [data1['modality_cumulative'], data2['modality_cumulative']]:
                for modality, counts in mod_dict.items():
                    if modality not in all_modalities:
                        all_modalities[modality] = 0
                    all_modalities[modality] += max(counts) if counts else 0
            
            # Sort by count and plot all modalities (limit to 8 for readability)
            sorted_modalities = sorted(all_modalities.items(), key=lambda x: x[1], reverse=True)
            modalities_to_plot = [m[0] for m in sorted_modalities[:8]]
            title_suffix = " (All)" if len(sorted_modalities) <= 8 else " (Top 8)"
        else:
            # Plot only the selected modality
            modalities_to_plot = [modality_filter]
            title_suffix = f" ({modality_filter})"
        
        if not modalities_to_plot:
            ax.text(0.5, 0.5, 'No modality data available', ha='center', va='center', transform=ax.transAxes)
            return
        
        # Calculate minute-by-minute cumulative studies by modality
        def calculate_modality_minute_by_minute(records, shift_start_rounded, modalities_to_plot):
            """Calculate cumulative studies by modality at each minute."""
            if not records:
                return {}, []
            
            # Sort records by time_finished
            sorted_records = sorted(records, key=lambda r: datetime.fromisoformat(r.get('time_finished', '')))
            
            # Find the time range
            first_time = datetime.fromisoformat(sorted_records[0]['time_finished'])
            last_time = datetime.fromisoformat(sorted_records[-1]['time_finished'])
            
            # Calculate total minutes in shift
            total_minutes = max(1, int((last_time - shift_start_rounded).total_seconds() / 60) + 1)
            
            # Initialize minute buckets by modality
            minute_modality_counts = {}  # minute_index -> {modality: count}
            
            for record in sorted_records:
                try:
                    time_finished = datetime.fromisoformat(record['time_finished'])
                    elapsed_minutes = int((time_finished - shift_start_rounded).total_seconds() / 60)
                    if elapsed_minutes < 0:
                        elapsed_minutes = 0
                    
                    # Extract modality from study_type
                    study_type = record.get('study_type', 'Unknown')
                    modality = study_type.split()[0] if study_type else "Unknown"
                    if modality == "Multiple" and len(study_type.split()) > 1:
                        modality = study_type.split()[1]
                    
                    if modality not in modalities_to_plot:
                        continue
                    
                    if elapsed_minutes not in minute_modality_counts:
                        minute_modality_counts[elapsed_minutes] = {}
                    if modality not in minute_modality_counts[elapsed_minutes]:
                        minute_modality_counts[elapsed_minutes][modality] = 0
                    minute_modality_counts[elapsed_minutes][modality] += 1
                except (ValueError, KeyError):
                    continue
            
            # Build cumulative arrays - one entry per minute for each modality
            modality_data = {}  # modality -> (x_indices, y_values)
            time_labels = []
            
            for modality in modalities_to_plot:
                cumulative_data = []
                current_count = 0
                
                for minute in range(total_minutes):
                    if minute in minute_modality_counts and modality in minute_modality_counts[minute]:
                        current_count += minute_modality_counts[minute][modality]
                    cumulative_data.append(current_count)
                    
                    # Create time label (only once)
                    if not time_labels:
                        time_at_minute = shift_start_rounded + timedelta(minutes=minute)
                        time_labels.append(time_at_minute.strftime("%H:%M"))
                
                if mode == "average":
                    # Convert to average per hour (studies per hour up to this point)
                    cumulative_data = [count / ((minute + 1) / 60) if minute >= 0 else 0 
                                     for minute, count in enumerate(cumulative_data)]
                
                modality_data[modality] = (list(range(total_minutes)), cumulative_data)
            
            return modality_data, time_labels
        
        modality_data1, x1_labels = calculate_modality_minute_by_minute(records1, shift1_start, modalities_to_plot)
        modality_data2, x2_labels = calculate_modality_minute_by_minute(records2, shift2_start, modalities_to_plot)
        
        # Find max length for alignment
        max_len = 0
        for mod_data in [modality_data1, modality_data2]:
            for modality, (x_vals, y_vals) in mod_data.items():
                max_len = max(max_len, len(x_vals))
        
        # Extend shorter series to match longer one
        for modality in modalities_to_plot:
            if modality in modality_data1:
                x1, y1 = modality_data1[modality]
                if len(x1) < max_len:
                    last_val = y1[-1] if y1 else 0
                    y1.extend([last_val] * (max_len - len(x1)))
                    x1.extend(range(len(x1), max_len))
                    for i in range(len(x1_labels), max_len):
                        time_at_minute = shift1_start + timedelta(minutes=i)
                        x1_labels.append(time_at_minute.strftime("%H:%M"))
            
            if modality in modality_data2:
                x2, y2 = modality_data2[modality]
                if len(x2) < max_len:
                    last_val = y2[-1] if y2 else 0
                    y2.extend([last_val] * (max_len - len(x2)))
                    x2.extend(range(len(x2), max_len))
                    for i in range(len(x2_labels), max_len):
                        time_at_minute = shift2_start + timedelta(minutes=i)
                        x2_labels.append(time_at_minute.strftime("%H:%M"))
        
        # Plot each modality
        colors_current = ['#4472C4', '#70AD47', '#FFC000', '#E74C3C', '#9B59B6']
        colors_prior = ['#9966CC', '#C06090', '#FF9966', '#F39C12', '#3498DB']
        
        for i, modality in enumerate(modalities_to_plot):
            color_idx = i % len(colors_current)
            
            if modality in modality_data1:
                x1, y1 = modality_data1[modality]
                ax.plot(x1, y1, color=colors_current[color_idx], linewidth=1.5, 
                       label=f'{modality} (Shift 1)', alpha=0.8)
            
            if modality in modality_data2:
                x2, y2 = modality_data2[modality]
                ax.plot(x2, y2, color=colors_prior[color_idx], linewidth=1.5, 
                       label=f'{modality} (Shift 2)', alpha=0.8, linestyle='--')
        
        ylabel = "Average Studies per Hour" if mode == "average" else "Cumulative Studies"
        title = f"Study Count by Modality{title_suffix}"
        
        ax.set_xlabel("Time", fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend(fontsize=8, loc='best')
        grid_color = theme_colors['fg'] if theme_colors else 'gray'
        ax.grid(True, alpha=0.2, color=grid_color)
        
        # Set x-axis to start at zero with no left padding
        if max_len > 0:
            ax.set_xlim(0, max_len - 1)
            ax.margins(x=0.01)
        
        # Set x-axis labels - show every 30-60 minutes depending on shift length
        if max_len > 0:
            if max_len <= 120:  # 2 hours or less: show every 15 minutes
                step = 15
            elif max_len <= 480:  # 8 hours or less: show every 30 minutes
                step = 30
            elif max_len <= 720:  # 12 hours or less: show every 60 minutes
                step = 60
            else:  # Very long shift: show every 2 hours
                step = 120
            
            ax.set_xticks(range(0, max_len, step))
            ax.set_xticklabels([x1_labels[i] if i < len(x1_labels) else x2_labels[i] if i < len(x2_labels) else ""
                               for i in range(0, max_len, step)], rotation=45, ha='right', fontsize=8)
    
    def _plot_total_studies(self, ax, data1: dict, data2: dict, shift1_start: datetime, 
                           shift2_start: datetime, use_actual_time: bool, theme_colors: dict = None):
        """Plot total study accumulation or average."""
        mode = self.comparison_graph_mode.get()
        
        if mode == "accumulation":
            y1 = data1['cumulative_studies']
            y2 = data2['cumulative_studies']
            ylabel = "Cumulative Studies"
            title = "Total Study Accumulation"
        else:
            y1 = data1['avg_studies']
            y2 = data2['avg_studies']
            ylabel = "Average Studies per Hour"
            title = "Average Study Rate"
        
        hours1 = list(range(len(y1)))
        hours2 = list(range(len(y2)))
        
        if use_actual_time:
            x1 = [(shift1_start + timedelta(hours=h)).strftime("%H:%M") for h in hours1]
            x2 = [(shift2_start + timedelta(hours=h)).strftime("%H:%M") for h in hours2]
        else:
            x1 = [f"Hour {h}" for h in hours1]
            x2 = [f"Hour {h}" for h in hours2]
        
        # Smooth the hourly data (both accumulation and average modes use hourly data)
        x1_smooth, y1_smooth = self._smooth_hourly_data(y1)
        x2_smooth, y2_smooth = self._smooth_hourly_data(y2)
        
        ax.plot(x1_smooth, y1_smooth, color='#4472C4', linewidth=2, label='Shift 1')
        ax.plot(x2_smooth, y2_smooth, color='#9966CC', linewidth=2, label='Shift 2')
        
        ax.set_xlabel("Time" if use_actual_time else "Hours from Start", fontsize=10)
        ax.set_ylabel(ylabel, fontsize=10)
        ax.set_title(title, fontsize=12, fontweight='bold')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Set x-axis to start at zero with tight margins
        # Use original hourly length for x-axis limits (not smoothed length)
        max_len = max(len(y1), len(y2)) if mode == "average" else max(len(x1), len(x2))
        ax.set_xlim(0, max_len - 1)
        ax.margins(x=0.01)
        
        # Set x-axis labels (use original hourly indices)
        if max_len > 0:
            step = max(1, max_len // 8)
            ax.set_xticks(range(0, max_len, step))
            ax.set_xticklabels([x1[i] if i < len(x1) else x2[i] if i < len(x2) else "" 
                               for i in range(0, max_len, step)], rotation=45, ha='right', fontsize=8)
    
    def _create_comparison_table(self, parent: ttk.Frame, shift1: dict, shift2: dict, 
                                records1: List[dict], records2: List[dict],
                                shift1_start: datetime, shift2_start: datetime):
        """Create numerical comparison table below graphs."""
        # Calculate statistics
        total_rvu1 = sum(r.get('rvu', 0) for r in records1)
        total_rvu2 = sum(r.get('rvu', 0) for r in records2)
        total_studies1 = len(records1)
        total_studies2 = len(records2)
        
        # Calculate compensation (reuse the app's calculation if available)
        total_comp1 = sum(self._calculate_study_compensation(r) for r in records1)
        total_comp2 = sum(self._calculate_study_compensation(r) for r in records2)
        
        # Count by modality - extract from study_type
        modality_counts1 = {}
        modality_counts2 = {}
        for r in records1:
            study_type = r.get('study_type', 'Unknown')
            mod = study_type.split()[0] if study_type else "Unknown"
            if mod == "Multiple" and len(study_type.split()) > 1:
                mod = study_type.split()[1]
            modality_counts1[mod] = modality_counts1.get(mod, 0) + 1
        for r in records2:
            study_type = r.get('study_type', 'Unknown')
            mod = study_type.split()[0] if study_type else "Unknown"
            if mod == "Multiple" and len(study_type.split()) > 1:
                mod = study_type.split()[1]
            modality_counts2[mod] = modality_counts2.get(mod, 0) + 1
        
        # Create grid layout
        headers = ["Metric", "Current Shift", "Prior Shift", "Difference"]
        for col, header in enumerate(headers):
            label = ttk.Label(parent, text=header, font=("Arial", 10, "bold"))
            label.grid(row=0, column=col, padx=10, pady=5, sticky=tk.W)
        
        row = 1
        
        # Shift dates
        ttk.Label(parent, text="Date/Time:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=shift1_start.strftime("%a %m/%d %I:%M%p")).grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=shift2_start.strftime("%a %m/%d %I:%M%p")).grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text="-").grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
        row += 1
        
        # Total RVU
        ttk.Label(parent, text="Total RVU:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"{total_rvu1:.2f}").grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"{total_rvu2:.2f}").grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
        diff_rvu = total_rvu1 - total_rvu2
        diff_color = "green" if diff_rvu > 0 else "red" if diff_rvu < 0 else "black"
        ttk.Label(parent, text=f"{diff_rvu:+.2f}", foreground=diff_color).grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
        row += 1
        
        # Total Compensation
        ttk.Label(parent, text="Total Compensation:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"${total_comp1:,.2f}").grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"${total_comp2:,.2f}").grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
        diff_comp = total_comp1 - total_comp2
        diff_color = "green" if diff_comp > 0 else "red" if diff_comp < 0 else "black"
        ttk.Label(parent, text=f"${diff_comp:+,.2f}", foreground=diff_color).grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
        row += 1
        
        # Total Studies
        ttk.Label(parent, text="Total Studies:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"{total_studies1}").grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
        ttk.Label(parent, text=f"{total_studies2}").grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
        diff_studies = total_studies1 - total_studies2
        diff_color = "green" if diff_studies > 0 else "red" if diff_studies < 0 else "black"
        ttk.Label(parent, text=f"{diff_studies:+d}", foreground=diff_color).grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
        row += 1
        
        # Separator
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(row=row, column=0, columnspan=4, sticky=tk.EW, pady=10)
        row += 1
        
        # Studies by Modality header
        ttk.Label(parent, text="Studies by Modality:", font=("Arial", 10, "bold")).grid(row=row, column=0, padx=10, pady=5, sticky=tk.W, columnspan=4)
        row += 1
        
        # Get all unique modalities
        all_modalities = sorted(set(list(modality_counts1.keys()) + list(modality_counts2.keys())))
        
        for modality in all_modalities:
            count1 = modality_counts1.get(modality, 0)
            count2 = modality_counts2.get(modality, 0)
            diff = count1 - count2
            
            ttk.Label(parent, text=f"  {modality}:").grid(row=row, column=0, padx=10, pady=2, sticky=tk.W)
            ttk.Label(parent, text=f"{count1}").grid(row=row, column=1, padx=10, pady=2, sticky=tk.W)
            ttk.Label(parent, text=f"{count2}").grid(row=row, column=2, padx=10, pady=2, sticky=tk.W)
            diff_color = "green" if diff > 0 else "red" if diff < 0 else "black"
            ttk.Label(parent, text=f"{diff:+d}", foreground=diff_color).grid(row=row, column=3, padx=10, pady=2, sticky=tk.W)
            row += 1
    
    def backup_study_data(self):
        """Create a backup JSON export of the SQLite database with timestamp."""
        try:
            # Use the SQLite export method to create a JSON backup
            backup_path = self.data_manager.export_records_to_json()
            backup_filename = os.path.basename(backup_path)
            
            messagebox.showinfo("Backup Created", f"Study data backed up successfully!\n\nBackup file: {backup_filename}")
            logger.info(f"Backup created: {backup_path}")
        except Exception as e:
            error_msg = f"Error creating backup: {str(e)}"
            messagebox.showerror("Backup Failed", error_msg)
            logger.error(error_msg)
    
    def load_backup_data(self):
        """Show dialog to select and load a backup file."""
        try:
            records_file = self.data_manager.records_file
            backup_dir = os.path.dirname(records_file)
            
            # Find all backup files
            backup_files = []
            if os.path.exists(backup_dir):
                for filename in os.listdir(backup_dir):
                    if filename.startswith("rvu_records_backup_") and filename.endswith(".json"):
                        backup_path = os.path.join(backup_dir, filename)
                        try:
                            # Try to extract timestamp from filename
                            # Format: rvu_records_backup_YYYY-MM-DD_HH-MM-SS.json
                            timestamp_str = filename.replace("rvu_records_backup_", "").replace(".json", "")
                            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                            # Get file modification time for sorting
                            mtime = os.path.getmtime(backup_path)
                            backup_files.append({
                                "filename": filename,
                                "path": backup_path,
                                "timestamp": timestamp,
                                "mtime": mtime,
                                "display": timestamp.strftime("%B %d, %Y at %I:%M %p")
                            })
                        except:
                            # If we can't parse timestamp, use file mtime
                            mtime = os.path.getmtime(backup_path)
                            backup_files.append({
                                "filename": filename,
                                "path": backup_path,
                                "timestamp": datetime.fromtimestamp(mtime),
                                "mtime": mtime,
                                "display": datetime.fromtimestamp(mtime).strftime("%B %d, %Y at %I:%M %p")
                            })
            
            if not backup_files:
                messagebox.showinfo("No Backups", "No backup files found.")
                return
            
            # Sort by modification time (newest first)
            backup_files.sort(key=lambda x: x["mtime"], reverse=True)
            
            # Create selection dialog
            self._show_backup_selection_dialog(backup_files)
            
        except Exception as e:
            error_msg = f"Error loading backup list: {str(e)}"
            messagebox.showerror("Error", error_msg)
            logger.error(error_msg)
    
    def _show_backup_selection_dialog(self, backup_files: List[dict]):
        """Show a dialog with list of backups to select from."""
        # Create dialog window
        dialog = tk.Toplevel(self.window)
        dialog.title("Local")
        dialog.transient(self.window)
        dialog.grab_set()
        dialog.geometry("500x400")
        
        # Apply theme to dialog
        colors = self.app.get_theme_colors()
        dialog.configure(bg=colors["bg"])
        
        # Configure ttk styles for this dialog
        try:
            style = self.app.style
        except:
            style = ttk.Style()
        
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabel", background=colors["bg"], foreground=colors["fg"])
        style.configure("TButton", background=colors["button_bg"], foreground=colors["button_fg"], 
                       bordercolor=colors.get("border_color", "#cccccc"))
        style.map("TButton", 
                 background=[("active", colors["button_active_bg"]), ("pressed", colors["button_active_bg"])],
                 foreground=[("active", colors["fg"]), ("pressed", colors["fg"])])
        style.configure("TScrollbar", background=colors["button_bg"], troughcolor=colors["bg"], 
                       bordercolor=colors.get("border_color", "#cccccc"))
        
        # Center dialog on parent window
        dialog.update_idletasks()
        x = self.window.winfo_x() + (self.window.winfo_width() // 2) - (dialog.winfo_width() // 2)
        y = self.window.winfo_y() + (self.window.winfo_height() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")
        
        # Main container frame
        main_frame = ttk.Frame(dialog, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Label
        label = ttk.Label(main_frame, text="Select a backup to restore:", font=("Arial", 10))
        label.pack(anchor=tk.W, pady=(0, 5))
        
        # Frame for scrollable backup list
        list_container = ttk.Frame(main_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        # Create canvas with scrollbar for scrollable list
        canvas_frame = ttk.Frame(list_container)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        
        # Apply theme colors to canvas (use colors already fetched above)
        canvas = tk.Canvas(canvas_frame, highlightthickness=0, bg=colors["bg"])
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Update canvas window width when canvas is resized
        def update_canvas_window_width(event):
            canvas.itemconfig(canvas_window, width=event.width)
        
        canvas.bind('<Configure>', update_canvas_window_width)
        
        # Update scroll region when scrollable frame changes
        def update_scroll_region(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        
        scrollable_frame.bind('<Configure>', update_scroll_region)
        
        # Mouse wheel scrolling (bind to canvas and scrollable_frame)
        def on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        canvas.bind("<MouseWheel>", on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", on_mousewheel)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Store references for refresh
        dialog.backup_files = backup_files
        dialog.scrollable_frame = scrollable_frame
        dialog.canvas = canvas
        dialog.selected_backup = None
        
        def refresh_backup_list():
            """Refresh the backup list display."""
            # Clear existing widgets
            for widget in scrollable_frame.winfo_children():
                widget.destroy()
            
            # Re-fetch backup files
            records_file = self.data_manager.records_file
            backup_dir = os.path.dirname(records_file)
            backup_files = []
            if os.path.exists(backup_dir):
                for filename in os.listdir(backup_dir):
                    if filename.startswith("rvu_records_backup_") and filename.endswith(".json"):
                        backup_path = os.path.join(backup_dir, filename)
                        try:
                            timestamp_str = filename.replace("rvu_records_backup_", "").replace(".json", "")
                            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d_%H-%M-%S")
                            mtime = os.path.getmtime(backup_path)
                            backup_files.append({
                                "filename": filename,
                                "path": backup_path,
                                "timestamp": timestamp,
                                "mtime": mtime,
                                "display": timestamp.strftime("%B %d, %Y at %I:%M %p")
                            })
                        except:
                            mtime = os.path.getmtime(backup_path)
                            backup_files.append({
                                "filename": filename,
                                "path": backup_path,
                                "timestamp": datetime.fromtimestamp(mtime),
                                "mtime": mtime,
                                "display": datetime.fromtimestamp(mtime).strftime("%B %d, %Y at %I:%M %p")
                            })
            
            # Sort by timestamp (newest first)
            backup_files.sort(key=lambda x: x["mtime"], reverse=True)
            dialog.backup_files = backup_files
            
            # Get theme colors
            colors = self.app.get_theme_colors()
            
            # Populate scrollable frame with backup entries
            for i, backup in enumerate(backup_files):
                backup_frame = ttk.Frame(scrollable_frame)
                backup_frame.pack(fill=tk.X, pady=1, padx=2)
                
                # X button to delete
                delete_btn = tk.Label(
                    backup_frame,
                    text="×",
                    font=("Arial", 8),
                    bg=colors["delete_btn_bg"],
                    fg=colors["delete_btn_fg"],
                    cursor="hand2",
                    padx=2,
                    pady=2,
                    width=2,
                    anchor=tk.CENTER
                )
                delete_btn.backup_path = backup["path"]
                delete_btn.backup_display = backup["display"]
                delete_btn.bind("<Button-1>", lambda e, btn=delete_btn: delete_backup(btn))
                delete_btn.bind("<Enter>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_hover"]))
                delete_btn.bind("<Leave>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_bg"]))
                delete_btn.pack(side=tk.LEFT, padx=(0, 5))
                
                # Backup label (clickable)
                backup_label = ttk.Label(
                    backup_frame,
                    text=backup["display"],
                    font=("Consolas", 9),
                    cursor="hand2"
                )
                backup_label.backup = backup
                backup_label.bind("<Button-1>", lambda e, lbl=backup_label: select_backup(lbl))
                backup_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
                
                # Highlight selected backup
                if dialog.selected_backup and dialog.selected_backup["path"] == backup["path"]:
                    backup_label.config(background=colors.get("button_bg", "#e1e1e1"))
            
            # Update canvas scroll region
            canvas.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
            
            # If no backups left, close dialog
            if not backup_files:
                messagebox.showinfo("No Backups", "No backup files found.")
                dialog.destroy()
        
        def select_backup(label):
            """Select a backup file."""
            # Clear previous selection
            for widget in scrollable_frame.winfo_children():
                if isinstance(widget, ttk.Frame):
                    for child in widget.winfo_children():
                        if isinstance(child, ttk.Label) and hasattr(child, 'backup'):
                            child.config(background="")
            
            # Highlight selected
            label.config(background=self.app.get_theme_colors().get("button_bg", "#e1e1e1"))
            dialog.selected_backup = label.backup
        
        def delete_backup(btn):
            """Delete a backup file."""
            backup_path = btn.backup_path
            backup_display = btn.backup_display
            
            # Confirm deletion
            response = messagebox.askyesno(
                "Delete Backup?",
                f"Are you sure you want to delete this backup?\n\n"
                f"Backup: {backup_display}\n\n"
                f"This action cannot be undone.",
                parent=dialog
            )
            
            if response:
                try:
                    if os.path.exists(backup_path):
                        os.remove(backup_path)
                        logger.info(f"Backup deleted: {backup_path}")
                        
                        # Clear selection if deleted backup was selected
                        if dialog.selected_backup and dialog.selected_backup["path"] == backup_path:
                            dialog.selected_backup = None
                        
                        # Refresh the list
                        refresh_backup_list()
                    else:
                        messagebox.showwarning("File Not Found", f"Backup file not found:\n{backup_path}")
                        refresh_backup_list()
                except Exception as e:
                    error_msg = f"Error deleting backup: {str(e)}"
                    messagebox.showerror("Delete Failed", error_msg)
                    logger.error(error_msg)
        
        def on_load():
            if not dialog.selected_backup:
                messagebox.showwarning("No Selection", "Please select a backup file.")
                return
            
            selected_backup = dialog.selected_backup
            
            # Confirm overwrite
            response = messagebox.askyesno(
                "Confirm Overwrite",
                f"Are you sure you want to restore this backup?\n\n"
                f"Backup: {selected_backup['display']}\n\n"
                f"This will REPLACE your current study data. This action cannot be undone.\n\n"
                f"Consider creating a backup of your current data first.",
                icon="warning",
                parent=dialog
            )
            
            if response:
                try:
                    # Use the SQLite import method which properly syncs data
                    success = self.data_manager.import_records_from_json(selected_backup["path"])
                    
                    if success:
                        # Refresh the app
                        self.app.update_display()
                        
                        # Refresh statistics window
                        self.populate_shifts_list()
                        self.refresh_data()
                        
                        messagebox.showinfo("Backup Restored", f"Backup restored successfully!\n\nRestored from: {selected_backup['display']}")
                        logger.info(f"Backup restored: {selected_backup['path']}")
                        
                        dialog.destroy()
                    else:
                        messagebox.showerror("Restore Failed", "Failed to import backup data")
                except Exception as e:
                    error_msg = f"Error restoring backup: {str(e)}"
                    messagebox.showerror("Restore Failed", error_msg)
                    logger.error(error_msg)
        
        def on_cancel():
            dialog.destroy()
        
        # Initial population
        refresh_backup_list()
        
        # Buttons frame
        buttons_frame = ttk.Frame(dialog, padding="10")
        buttons_frame.pack(fill=tk.X)
        
        ttk.Button(buttons_frame, text="Restore", command=on_load, width=20).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons_frame, text="Cancel", command=on_cancel, width=12).pack(side=tk.RIGHT, padx=5)
    
    def on_configure(self, event):
        """Handle window configuration changes (move/resize)."""
        if event.widget == self.window:
            x = self.window.winfo_x()
            y = self.window.winfo_y()
            
            # Only save if position actually changed
            if x != self.last_saved_x or y != self.last_saved_y:
                # Debounce position saving (shorter for responsiveness)
                if hasattr(self, '_save_timer'):
                    try:
                        self.window.after_cancel(self._save_timer)
                    except:
                        pass
                self._save_timer = self.window.after(100, lambda: self.save_position(x, y))
    
    def on_statistics_drag_end(self, event):
        """Handle end of statistics window dragging - save position immediately."""
        # Cancel any pending debounced save
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        # Save immediately on mouse release
        self.save_position()
    
    def save_position(self, x=None, y=None):
        """Save statistics window position."""
        try:
            if x is None:
                x = self.window.winfo_x()
            if y is None:
                y = self.window.winfo_y()
            
            if "window_positions" not in self.data_manager.data:
                self.data_manager.data["window_positions"] = {}
            self.data_manager.data["window_positions"]["statistics"] = {
                "x": x,
                "y": y
            }
            self.last_saved_x = x
            self.last_saved_y = y
            # Only save settings (window positions), not records
            self.data_manager.save(save_records=False)
        except Exception as e:
            logger.error(f"Error saving statistics window position: {e}")
    
    def apply_theme(self):
        """Apply theme to statistics window."""
        dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
        
        # Get or create style (use app's style if available, otherwise create new one)
        try:
            style = self.app.style
        except:
            style = ttk.Style()
        
        # Use 'clam' theme for consistent styling
        style.theme_use('clam')
        
        if dark_mode:
            bg_color = "#1e1e1e"
            canvas_bg = "#252525"
            fg_color = "#e0e0e0"
            entry_bg = "#2d2d2d"
            entry_fg = "#e0e0e0"
            border_color = "#888888"
        else:
            bg_color = "SystemButtonFace"
            canvas_bg = "SystemButtonFace"
            fg_color = "black"
            entry_bg = "white"
            entry_fg = "black"
            border_color = "#cccccc"
        
        self.window.configure(bg=bg_color)
        self.theme_bg = bg_color
        self.theme_canvas_bg = canvas_bg
        
        # Configure ttk styles for Entry and Spinbox widgets
        style.configure("TEntry", fieldbackground=entry_bg, foreground=entry_fg, bordercolor=border_color)
        style.configure("TSpinbox", fieldbackground=entry_bg, foreground=entry_fg, bordercolor=border_color,
                       background=entry_bg, arrowcolor=fg_color)
        style.configure("TLabel", background=bg_color, foreground=fg_color)
        style.configure("TFrame", background=bg_color)
        style.configure("TLabelframe", background=bg_color, bordercolor=border_color)
        style.configure("TLabelframe.Label", background=bg_color, foreground=fg_color)
        
        # Configure Combobox styling for dark mode visibility
        style.configure("TCombobox", 
                       fieldbackground=entry_bg, 
                       foreground=entry_fg,
                       background=entry_bg,
                       selectbackground="#0078d7" if dark_mode else "SystemHighlight",
                       selectforeground="white" if dark_mode else "SystemHighlightText",
                       bordercolor=border_color,
                       arrowcolor=fg_color)
        style.map("TCombobox",
                 fieldbackground=[('readonly', entry_bg), ('disabled', bg_color)],
                 foreground=[('readonly', entry_fg), ('disabled', '#888888')],
                 selectbackground=[('readonly', '#0078d7' if dark_mode else 'SystemHighlight')],
                 selectforeground=[('readonly', 'white' if dark_mode else 'SystemHighlightText')])
    
    def detect_partial_shifts(self, typical_start_hour: int = None) -> List[List[dict]]:
        """
        Detect 'interrupted' shifts - shifts that started around typical start time, lasted <9 hours,
        and have consecutive shorter shifts that make up the remaining time.
        Returns a list of shift groups that could be combined.
        """
        shifts = self.get_all_shifts()
        # Filter out current shift and sort by start time (oldest first)
        historical = [s for s in shifts if not s.get("is_current") and s.get("shift_start")]
        
        # Use provided typical start hour or default to 23 (11pm)
        if typical_start_hour is None:
            typical_start_hour = 23
        
        def parse_shift(s):
            try:
                start = datetime.fromisoformat(s.get("shift_start", ""))
                end = datetime.fromisoformat(s.get("shift_end", "")) if s.get("shift_end") else start
                return start, end
            except:
                return None, None
        
        # Sort by start time
        historical.sort(key=lambda s: s.get("shift_start", ""))
        
        partial_groups = []
        used_indices = set()
        
        for i, shift in enumerate(historical):
            if i in used_indices:
                continue
                
            start, end = parse_shift(shift)
            if not start:
                continue
            
            # Check if shift started around typical start time (±0.5 hour)
            start_hour = start.hour + start.minute / 60
            typical_low = typical_start_hour - 0.5
            typical_high = typical_start_hour + 0.5
            # Handle wraparound at midnight
            if typical_start_hour >= 23:
                is_typical_start = start_hour >= typical_low or start_hour <= (typical_high % 24)
            else:
                is_typical_start = typical_low <= start_hour <= typical_high
            
            # Calculate duration
            duration_hours = (end - start).total_seconds() / 3600
            
            # If started around typical time and lasted <9 hours, look for continuation shifts
            if is_typical_start and duration_hours < 9:
                group = [shift]
                used_indices.add(i)
                total_duration = duration_hours
                last_end = end
                
                # Look for consecutive shifts within 4 hours of previous ending
                for j in range(i + 1, len(historical)):
                    if j in used_indices:
                        continue
                    
                    next_start, next_end = parse_shift(historical[j])
                    if not next_start:
                        continue
                    
                    # Check if this shift starts within 4 hours of the last one ending
                    gap_hours = (next_start - last_end).total_seconds() / 3600
                    if 0 <= gap_hours <= 4:
                        next_duration = (next_end - next_start).total_seconds() / 3600
                        group.append(historical[j])
                        used_indices.add(j)
                        total_duration += next_duration
                        last_end = next_end
                        
                        # Stop if we've accumulated enough for a full shift
                        if total_duration >= 9:
                            break
                    elif gap_hours > 4:
                        break  # Too big a gap
                
                # If we found multiple shifts that together form a reasonable duration
                if len(group) > 1 and total_duration >= 4:  # At least 4 hours combined
                    partial_groups.append(group)
        
        return partial_groups
    
    def show_partial_shifts_dialog(self):
        """Show dialog to combine detected partial shifts."""
        partial_groups = self.detect_partial_shifts()
        
        if not partial_groups:
            messagebox.showinfo("No Partial Shifts", 
                              "No interrupted/partial shift patterns detected.",
                              parent=self.window)
            return
        
        dialog = tk.Toplevel(self.window)
        dialog.title("Combine Partial Shifts")
        dialog.transient(self.window)
        dialog.grab_set()
        
        # Position near button
        dialog.geometry(f"+{self.window.winfo_x() + 50}+{self.window.winfo_y() + 100}")
        
        ttk.Label(dialog, text="Detected shift groups that may have been interrupted:",
                 font=("Arial", 10, "bold")).pack(padx=15, pady=(15, 10))
        
        # Scrollable frame for groups
        canvas = tk.Canvas(dialog, height=300, width=450)
        scrollbar = ttk.Scrollbar(dialog, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(15, 0), pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 15), pady=5)
        
        selected_groups = []
        
        for group_idx, group in enumerate(partial_groups):
            group_frame = ttk.LabelFrame(scroll_frame, text=f"Group {group_idx + 1}", padding=5)
            group_frame.pack(fill=tk.X, pady=5, padx=5)
            
            # Calculate combined stats
            total_records = sum(len(s.get("records", [])) for s in group)
            total_rvu = sum(sum(r.get("rvu", 0) for r in s.get("records", [])) for s in group)
            
            first_start = datetime.fromisoformat(group[0].get("shift_start", ""))
            last_end = datetime.fromisoformat(group[-1].get("shift_end", ""))
            total_hours = (last_end - first_start).total_seconds() / 3600
            
            info_text = f"{len(group)} shifts • {total_records} studies • {total_rvu:.1f} RVU • {total_hours:.1f}h total span"
            ttk.Label(group_frame, text=info_text).pack(anchor=tk.W)
            
            # List each shift in the group
            for shift in group:
                try:
                    start = datetime.fromisoformat(shift.get("shift_start", ""))
                    end = datetime.fromisoformat(shift.get("shift_end", ""))
                    dur = (end - start).total_seconds() / 3600
                    shift_info = f"  • {start.strftime('%m/%d %I:%M%p')} - {end.strftime('%I:%M%p')} ({dur:.1f}h, {len(shift.get('records', []))} studies)"
                except:
                    shift_info = "  • Unknown"
                ttk.Label(group_frame, text=shift_info, font=("Arial", 9)).pack(anchor=tk.W)
            
            # Checkbox to select this group
            var = tk.BooleanVar(value=True)
            selected_groups.append((group, var))
            ttk.Checkbutton(group_frame, text="Combine this group", variable=var).pack(anchor=tk.W, pady=(5, 0))
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=15, padx=15)
        
        def do_combine():
            groups_to_combine = [g for g, v in selected_groups if v.get()]
            if groups_to_combine:
                for group in groups_to_combine:
                    self._combine_shift_group(group)
                dialog.destroy()
                self.populate_shifts_list()
                self.refresh_data()
                self.update_partial_shifts_button()
                messagebox.showinfo("Shifts Combined", 
                                  f"Successfully combined {len(groups_to_combine)} shift group(s).",
                                  parent=self.window)
        
        ttk.Button(btn_frame, text="Combine Selected", command=do_combine).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
    
    def show_combine_shifts_dialog(self):
        """Show dialog to manually combine shifts."""
        shifts = self.get_all_shifts()
        historical = [s for s in shifts if not s.get("is_current") and s.get("shift_start")]
        
        if len(historical) < 2:
            messagebox.showinfo("Not Enough Shifts",
                              "You need at least 2 shifts to combine.",
                              parent=self.window)
            return
        
        dialog = tk.Toplevel(self.window)
        dialog.title("Combine Shifts")
        dialog.transient(self.window)
        dialog.grab_set()
        
        # Position near button
        dialog.geometry(f"+{self.window.winfo_x() + 50}+{self.window.winfo_y() + 100}")
        
        ttk.Label(dialog, text="Select shifts to combine (select 2 or more):",
                 font=("Arial", 10, "bold")).pack(padx=15, pady=(15, 10))
        
        # Scrollable frame
        canvas_frame = ttk.Frame(dialog)
        canvas_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
        
        canvas = tk.Canvas(canvas_frame, height=350, width=400)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Track how many shifts loaded and selection vars
        shifts_shown = [0]
        max_initial = 20
        selection_vars = []
        
        def add_shift_row(shift, idx):
            frame = ttk.Frame(scroll_frame)
            frame.pack(fill=tk.X, pady=2)
            
            var = tk.BooleanVar()
            selection_vars.append((shift, var))
            
            try:
                start = datetime.fromisoformat(shift.get("shift_start", ""))
                end = datetime.fromisoformat(shift.get("shift_end", ""))
                dur = (end - start).total_seconds() / 3600
                records = shift.get("records", [])
                rvu = sum(r.get("rvu", 0) for r in records)
                text = f"{start.strftime('%m/%d/%Y %I:%M%p')} ({dur:.1f}h, {len(records)} studies, {rvu:.1f} RVU)"
            except:
                text = f"Shift {idx + 1}"
            
            ttk.Checkbutton(frame, text=text, variable=var).pack(anchor=tk.W)
        
        def load_shifts(count):
            current = shifts_shown[0]
            for i in range(current, min(current + count, len(historical))):
                add_shift_row(historical[i], i)
            shifts_shown[0] = min(current + count, len(historical))
            
            # Update load more button visibility
            if shifts_shown[0] < len(historical):
                load_more_btn.pack(pady=5)
            else:
                load_more_btn.pack_forget()
        
        # Load more button
        load_more_btn = ttk.Button(scroll_frame, text="Load More...", 
                                   command=lambda: load_shifts(20))
        
        # Initial load
        load_shifts(max_initial)
        
        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill=tk.X, pady=15, padx=15)
        
        def do_combine():
            selected = [s for s, v in selection_vars if v.get()]
            if len(selected) < 2:
                messagebox.showwarning("Selection Required",
                                      "Please select at least 2 shifts to combine.",
                                      parent=dialog)
                return
            
            # Sort by start time
            selected.sort(key=lambda s: s.get("shift_start", ""))
            
            # Confirm
            first_start = datetime.fromisoformat(selected[0].get("shift_start", ""))
            last_end = datetime.fromisoformat(selected[-1].get("shift_end", ""))
            total_records = sum(len(s.get("records", [])) for s in selected)
            total_rvu = sum(sum(r.get("rvu", 0) for r in s.get("records", [])) for s in selected)
            
            result = messagebox.askyesno(
                "Confirm Combine",
                f"Combine {len(selected)} shifts?\n\n"
                f"Start: {first_start.strftime('%m/%d/%Y %I:%M %p')}\n"
                f"End: {last_end.strftime('%m/%d/%Y %I:%M %p')}\n"
                f"Total: {total_records} studies, {total_rvu:.1f} RVU\n\n"
                "This will merge all studies into a single shift.",
                parent=dialog
            )
            
            if result:
                self._combine_shift_group(selected)
                dialog.destroy()
                self.populate_shifts_list()
                self.refresh_data()
                self.update_partial_shifts_button()
                messagebox.showinfo("Shifts Combined",
                                  f"Successfully combined {len(selected)} shifts.",
                                  parent=self.window)
        
        ttk.Button(btn_frame, text="Combine Selected", command=do_combine).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=5)
    
    def _combine_shift_group(self, shifts: List[dict]):
        """Combine multiple shifts into one. Takes earliest start, latest end, merges all records."""
        if len(shifts) < 2:
            return
        
        # Sort by start time
        shifts.sort(key=lambda s: s.get("shift_start", ""))
        
        # Combine data
        combined_start = shifts[0].get("shift_start")
        combined_end = shifts[-1].get("shift_end")
        combined_records = []
        
        for shift in shifts:
            combined_records.extend(shift.get("records", []))
        
        # Sort records by time_performed
        combined_records.sort(key=lambda r: r.get("time_performed", ""))
        
        # Delete old shifts from database
        for shift in shifts:
            shift_start = shift.get("shift_start")
            try:
                cursor = self.data_manager.db.conn.cursor()
                cursor.execute('SELECT id FROM shifts WHERE shift_start = ? AND is_current = 0', (shift_start,))
                row = cursor.fetchone()
                if row:
                    self.data_manager.db.delete_shift(row[0])
            except Exception as e:
                logger.error(f"Error deleting shift from database during combine: {e}")
            
            # Remove from in-memory data
            historical_shifts = self.data_manager.data.get("shifts", [])
            for i, s in enumerate(historical_shifts):
                if s.get("shift_start") == shift_start:
                    historical_shifts.pop(i)
                    break
            
            if "shifts" in self.data_manager.records_data:
                for i, s in enumerate(self.data_manager.records_data["shifts"]):
                    if s.get("shift_start") == shift_start:
                        self.data_manager.records_data["shifts"].pop(i)
                        break
        
        # Create the combined shift in database
        try:
            cursor = self.data_manager.db.conn.cursor()
            cursor.execute('''
                INSERT INTO shifts (shift_start, shift_end, is_current)
                VALUES (?, ?, 0)
            ''', (combined_start, combined_end))
            self.data_manager.db.conn.commit()
            combined_shift_id = cursor.lastrowid
            
            # Add all records to the combined shift
            for record in combined_records:
                self.data_manager.db.add_record(combined_shift_id, record)
            
            logger.info(f"Created combined shift in database: ID={combined_shift_id}")
        except Exception as e:
            logger.error(f"Error saving combined shift to database: {e}")
            return  # Don't add to memory if database save failed
        
        # Reload data from database to ensure in-memory data matches database
        # This prevents duplicates that could occur if we manually add to in-memory data
        # and then reload from database later
        try:
            self.data_manager.records_data = self.data_manager._load_records_from_db()
            # Update the main data structure as well
            self.data_manager.data["shifts"] = self.data_manager.records_data.get("shifts", [])
            logger.info(f"Reloaded data from database after combining shifts")
        except Exception as e:
            logger.error(f"Error reloading data from database: {e}")
        
        logger.info(f"Combined {len(shifts)} shifts into one ({len(combined_records)} records)")
    
    def update_partial_shifts_button(self):
        """Update visibility of the partial shifts button based on detection."""
        partial_groups = self.detect_partial_shifts()
        if partial_groups:
            self.partial_shifts_btn.pack(side=tk.LEFT, padx=2)
        else:
            self.partial_shifts_btn.pack_forget()
    
    def on_closing(self):
        """Handle window closing."""
        # Cancel any pending save timer
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        self.save_position()
        self.window.destroy()




__all__ = ['StatisticsWindow']
