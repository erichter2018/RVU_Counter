"""Settings window for RVU Counter."""

from __future__ import annotations
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import logging
from typing import TYPE_CHECKING

from ..core.platform_utils import is_point_on_any_monitor, find_nearest_monitor_for_window, get_primary_monitor_bounds
from ..logic.study_tracker import StudyTracker

if TYPE_CHECKING:
    from ..data import RVUData
    from .main_window import RVUCounterApp

logger = logging.getLogger(__name__)

class SettingsWindow:
    """Settings modal window."""
    
    def __init__(self, parent, data_manager: 'RVUData', app: 'RVUCounterApp'):
        self.parent = parent
        self.data_manager = data_manager
        self.app = app
        
        self.window = tk.Toplevel(parent)
        self.window.title("Settings")
        
        # Load saved window position or use default
        window_pos = self.data_manager.data.get("window_positions", {}).get("settings", None)
        if window_pos:
            x, y = window_pos['x'], window_pos['y']
            # Validate position before applying
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Settings window position ({x}, {y}) is off-screen, finding nearest monitor")
                x, y = find_nearest_monitor_for_window(x, y, 450, 700)
            self.window.geometry(f"450x810+{x}+{y}")
        else:
            # Center on primary monitor
            try:
                primary = get_primary_monitor_bounds()
                x = primary[0] + (primary[2] - primary[0] - 450) // 2
                y = primary[1] + (primary[3] - primary[1] - 700) // 2
                self.window.geometry(f"450x810+{x}+{y}")
            except:
                self.window.geometry("450x810")
        
        self.window.transient(parent)
        self.window.grab_set()
        
        # Track last saved position to avoid excessive saves
        self.last_saved_x = None
        self.last_saved_y = None
        
        # Bind to window movement to save position (debounced)
        self.window.bind("<Configure>", self.on_settings_window_move)
        self.window.bind("<ButtonRelease-1>", self.on_settings_drag_end)
        self.window.protocol("WM_DELETE_WINDOW", self.on_settings_closing)
        
        # Apply theme
        self.apply_theme()
        
        self.create_settings_ui()
    
    def apply_theme(self):
        """Apply theme to settings window."""
        dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
        
        if dark_mode:
            bg_color = "#1e1e1e"
            entry_bg = "#2d2d2d"
            entry_fg = "#e0e0e0"
            fg_color = "#e0e0e0"
            border_color = "#888888"
        else:
            bg_color = "SystemButtonFace"
            entry_bg = "white"
            entry_fg = "black"
            fg_color = "black"
            border_color = "#cccccc"
        
        self.window.configure(bg=bg_color)
        
        # Configure combobox style to match statistics window
        style = ttk.Style()
        if dark_mode:
            # Configure Combobox styling for dark mode visibility (matching statistics)
            style.configure("TCombobox", 
                          fieldbackground=entry_bg, 
                          foreground=entry_fg,
                          background=entry_bg,
                          selectbackground="#0078d7",
                          selectforeground="white",
                          bordercolor=border_color,
                          arrowcolor=fg_color)
            style.map("TCombobox",
                     fieldbackground=[('readonly', entry_bg), ('disabled', bg_color)],
                     foreground=[('readonly', entry_fg), ('disabled', '#888888')],
                     selectbackground=[('readonly', '#0078d7')],
                     selectforeground=[('readonly', 'white')])
    
    def create_settings_ui(self):
        """Create settings UI."""
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        settings = self.data_manager.data["settings"]
        
        # Create two-column frame for general settings
        general_settings_frame = ttk.Frame(main_frame)
        general_settings_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Column 1: Auto-resume and Dark Mode
        col1 = ttk.Frame(general_settings_frame)
        col1.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 20))
        
        # Auto-start
        self.auto_start_var = tk.BooleanVar(value=settings["auto_start"])
        ttk.Checkbutton(col1, text="Auto-resume shift on launch", variable=self.auto_start_var).pack(anchor=tk.W, pady=2)
        
        # Dark mode
        self.dark_mode_var = tk.BooleanVar(value=settings.get("dark_mode", False))
        ttk.Checkbutton(col1, text="Dark Mode", variable=self.dark_mode_var).pack(anchor=tk.W, pady=2)
        
        # Borderless mode
        self.borderless_mode_var = tk.BooleanVar(value=settings.get("borderless_mode", False))
        ttk.Checkbutton(col1, text="Remove title bar", variable=self.borderless_mode_var).pack(anchor=tk.W, pady=2)
        
        # Column 2: Show time and Stay on top
        col2 = ttk.Frame(general_settings_frame)
        col2.pack(side=tk.LEFT, anchor=tk.N)
        
        # Show time checkbox
        self.show_time_var = tk.BooleanVar(value=settings.get("show_time", False))
        ttk.Checkbutton(col2, text="Show time", variable=self.show_time_var).pack(anchor=tk.W, pady=2)
        
        # Stay on top option
        self.stay_on_top_var = tk.BooleanVar(value=settings.get("stay_on_top", True))
        ttk.Checkbutton(col2, text="Stay on top", variable=self.stay_on_top_var).pack(anchor=tk.W, pady=2)
        
        # Data source radio buttons (PowerScribe or Mosaic)
        data_source_frame = ttk.Frame(main_frame)
        data_source_frame.pack(anchor=tk.W, pady=(10, 5))
        
        ttk.Label(data_source_frame, text="Data Source:", font=("Arial", 9, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        
        # Auto-detection info (no longer manual selection)
        current_source = self.app._active_source if hasattr(self.app, '_active_source') and self.app._active_source else "auto-detecting"
        ttk.Label(data_source_frame, text=f"Auto-detect ({current_source})", 
                 font=("Arial", 9), foreground="gray").pack(side=tk.LEFT)
        
        # Keep this variable for backwards compatibility but it's not used anymore
        self.data_source_var = tk.StringVar(value="Auto")
        
        # Two-column frame for counters and compensation
        columns_frame = ttk.Frame(main_frame)
        columns_frame.pack(fill=tk.X, pady=(10, 5))
        
        # Column 1: Show Counters
        counters_col = ttk.Frame(columns_frame)
        counters_col.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 20))
        
        ttk.Label(counters_col, text="Show Counters:", font=("Arial", 9, "bold")).pack(anchor=tk.W)
        
        # Counter variables and checkbuttons
        self.show_total_var = tk.BooleanVar(value=settings["show_total"])
        self.total_cb = ttk.Checkbutton(counters_col, text="total", variable=self.show_total_var, 
                                         command=lambda: self.sync_compensation_state("total"))
        self.total_cb.pack(anchor=tk.W, pady=2)
        
        self.show_avg_var = tk.BooleanVar(value=settings["show_avg"])
        self.avg_cb = ttk.Checkbutton(counters_col, text="average per hour", variable=self.show_avg_var,
                                       command=lambda: self.sync_compensation_state("avg"))
        self.avg_cb.pack(anchor=tk.W, pady=2)
        
        self.show_last_hour_var = tk.BooleanVar(value=settings["show_last_hour"])
        self.last_hour_cb = ttk.Checkbutton(counters_col, text="last hour", variable=self.show_last_hour_var,
                                             command=lambda: self.sync_compensation_state("last_hour"))
        self.last_hour_cb.pack(anchor=tk.W, pady=2)
        
        self.show_last_full_hour_var = tk.BooleanVar(value=settings["show_last_full_hour"])
        self.last_full_hour_cb = ttk.Checkbutton(counters_col, text="last full hour", variable=self.show_last_full_hour_var,
                                                  command=lambda: self.sync_compensation_state("last_full_hour"))
        self.last_full_hour_cb.pack(anchor=tk.W, pady=2)
        
        self.show_projected_var = tk.BooleanVar(value=settings["show_projected"])
        self.projected_cb = ttk.Checkbutton(counters_col, text="est this hour", variable=self.show_projected_var,
                                             command=lambda: self.sync_compensation_state("projected"))
        self.projected_cb.pack(anchor=tk.W, pady=2)
        
        self.show_projected_shift_var = tk.BooleanVar(value=settings.get("show_projected_shift", True))
        self.projected_shift_cb = ttk.Checkbutton(counters_col, text="est shift total", variable=self.show_projected_shift_var,
                                             command=lambda: self.sync_compensation_state("projected_shift"))
        self.projected_shift_cb.pack(anchor=tk.W, pady=2)
        
        # Pace car checkbox (compare vs prior shift)
        self.show_pace_car_var = tk.BooleanVar(value=settings.get("show_pace_car", False))
        self.pace_car_cb = ttk.Checkbutton(counters_col, text="pace vs prior shift", variable=self.show_pace_car_var)
        self.pace_car_cb.pack(anchor=tk.W, pady=2)
        
        # Role radio buttons (Partner/Associate)
        role_frame = ttk.Frame(counters_col)
        role_frame.pack(anchor=tk.W, pady=(10, 2))
        
        self.role_var = tk.StringVar(value=settings.get("role", "Partner"))
        ttk.Radiobutton(role_frame, text="Partner", variable=self.role_var, value="Partner").pack(side=tk.LEFT)
        ttk.Radiobutton(role_frame, text="Associate", variable=self.role_var, value="Associate").pack(side=tk.LEFT, padx=(10, 0))
        
        # Column 2: Show Compensation
        comp_col = ttk.Frame(columns_frame)
        comp_col.pack(side=tk.LEFT, anchor=tk.N)
        
        ttk.Label(comp_col, text="Show Compensation:", font=("Arial", 9, "bold")).pack(anchor=tk.W)
        
        # Compensation variables and checkbuttons (initially set based on counter state)
        
        self.show_comp_total_var = tk.BooleanVar(value=settings.get("show_comp_total", False))
        self.comp_total_cb = ttk.Checkbutton(comp_col, text="total", variable=self.show_comp_total_var)
        self.comp_total_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_avg_var = tk.BooleanVar(value=settings.get("show_comp_avg", False))
        self.comp_avg_cb = ttk.Checkbutton(comp_col, text="average per hour", variable=self.show_comp_avg_var)
        self.comp_avg_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_last_hour_var = tk.BooleanVar(value=settings.get("show_comp_last_hour", False))
        self.comp_last_hour_cb = ttk.Checkbutton(comp_col, text="last hour", variable=self.show_comp_last_hour_var)
        self.comp_last_hour_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_last_full_hour_var = tk.BooleanVar(value=settings.get("show_comp_last_full_hour", False))
        self.comp_last_full_hour_cb = ttk.Checkbutton(comp_col, text="last full hour", variable=self.show_comp_last_full_hour_var)
        self.comp_last_full_hour_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_projected_var = tk.BooleanVar(value=settings.get("show_comp_projected", False))
        self.comp_projected_cb = ttk.Checkbutton(comp_col, text="est this hour", variable=self.show_comp_projected_var)
        self.comp_projected_cb.pack(anchor=tk.W, pady=2)
        
        self.show_comp_projected_shift_var = tk.BooleanVar(value=settings.get("show_comp_projected_shift", True))
        self.comp_projected_shift_cb = ttk.Checkbutton(comp_col, text="est shift total", variable=self.show_comp_projected_shift_var)
        self.comp_projected_shift_cb.pack(anchor=tk.W, pady=2)
        
        # Store mapping for easy sync
        self.comp_mapping = {
            "total": (self.show_total_var, self.show_comp_total_var, self.comp_total_cb),
            "avg": (self.show_avg_var, self.show_comp_avg_var, self.comp_avg_cb),
            "last_hour": (self.show_last_hour_var, self.show_comp_last_hour_var, self.comp_last_hour_cb),
            "last_full_hour": (self.show_last_full_hour_var, self.show_comp_last_full_hour_var, self.comp_last_full_hour_cb),
            "projected": (self.show_projected_var, self.show_comp_projected_var, self.comp_projected_cb),
            "projected_shift": (self.show_projected_shift_var, self.show_comp_projected_shift_var, self.comp_projected_shift_cb),
        }
        
        # Initial sync of compensation state based on counter state
        for key in self.comp_mapping:
            self.sync_compensation_state(key)
        
        # Two-column frame for Shift Length/Min Duration and Mini Interface
        shift_mini_frame = ttk.Frame(main_frame)
        shift_mini_frame.pack(fill=tk.X, pady=(10, 5))
        
        # Left column: Shift Length and Min Study Duration
        shift_col = ttk.Frame(shift_mini_frame)
        shift_col.pack(side=tk.LEFT, anchor=tk.N, padx=(0, 20))
        
        # Shift length (hours)
        ttk.Label(shift_col, text="Shift Length (hours):", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(0, 5))
        self.shift_length_var = tk.StringVar(value=str(self.data_manager.data["settings"].get("shift_length_hours", 9)))
        ttk.Entry(shift_col, textvariable=self.shift_length_var, width=10).pack(anchor=tk.W, pady=2)
        
        # Min study seconds
        ttk.Label(shift_col, text="Min Study Duration (seconds):", font=("Arial", 9, "bold")).pack(anchor=tk.W, pady=(10, 5))
        self.min_seconds_var = tk.StringVar(value=str(self.data_manager.data["settings"]["min_study_seconds"]))
        ttk.Entry(shift_col, textvariable=self.min_seconds_var, width=10).pack(anchor=tk.W, pady=2)
        
        # Right column: Mini Interface Settings
        mini_frame = ttk.LabelFrame(shift_mini_frame, text="Mini Interface", padding="5")
        mini_frame.pack(side=tk.LEFT, anchor=tk.N)
        
        ttk.Label(mini_frame, text="Double-click anywhere in main interface to\nlaunch mini mode.", 
                 font=("Arial", 8), foreground="gray", justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 5))
        
        # Metric selection dropdowns
        metrics_options = [
            ("Pace (vs prior shift)", "pace"),
            ("Current Total", "current_total"),
            ("Estimated Total (shift)", "estimated_total"),
            ("Average per Hour", "average_hour")
        ]
        
        # Row 1: First metric
        metric1_frame = ttk.Frame(mini_frame)
        metric1_frame.pack(anchor=tk.W, pady=2)
        
        ttk.Label(metric1_frame, text="Metric 1:", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 10))
        
        self.mini_metric_1_var = tk.StringVar(value=settings.get("mini_metric_1", "pace"))
        metric1_dropdown = ttk.Combobox(metric1_frame, textvariable=self.mini_metric_1_var, 
                                        values=[opt[1] for opt in metrics_options], 
                                        state="readonly", width=20)
        metric1_dropdown.pack(side=tk.LEFT)
        
        # Display readable names in dropdown
        def get_display_name(value):
            for display, val in metrics_options:
                if val == value:
                    return display
            return value
        
        # Set display name
        current_val = self.mini_metric_1_var.get()
        for i, (display, val) in enumerate(metrics_options):
            if val == current_val:
                metric1_dropdown.current(i)
                break
        
        # Row 2: Second metric
        metric2_frame = ttk.Frame(mini_frame)
        metric2_frame.pack(anchor=tk.W, pady=2)
        
        ttk.Label(metric2_frame, text="Metric 2:", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 10))
        
        self.mini_metric_2_var = tk.StringVar(value=settings.get("mini_metric_2", "current_total"))
        metric2_dropdown = ttk.Combobox(metric2_frame, textvariable=self.mini_metric_2_var, 
                                        values=[opt[1] for opt in metrics_options], 
                                        state="readonly", width=20)
        metric2_dropdown.pack(side=tk.LEFT)
        
        # Set display name
        current_val = self.mini_metric_2_var.get()
        for i, (display, val) in enumerate(metrics_options):
            if val == current_val:
                metric2_dropdown.current(i)
                break
        
        # Ignore duplicates
        self.ignore_duplicates_var = tk.BooleanVar(value=self.data_manager.data["settings"]["ignore_duplicate_accessions"])
        ttk.Checkbutton(main_frame, text="Ignore duplicate accessions", variable=self.ignore_duplicates_var).pack(anchor=tk.W, pady=2)
        
        # Cloud Backup Section
        backup_frame = ttk.LabelFrame(main_frame, text="☁️ Cloud Backup", padding="5")
        backup_frame.pack(fill=tk.X, pady=(10, 5))
        
        # Check if OneDrive is available
        backup_mgr = self.data_manager.backup_manager
        backup_settings = self.data_manager.data.get("backup", {})
        
        if backup_mgr.is_onedrive_available():
            # Enable checkbox
            self.backup_enabled_var = tk.BooleanVar(value=backup_settings.get("cloud_backup_enabled", False))
            enable_cb = ttk.Checkbutton(backup_frame, text="Enable automatic backup to OneDrive", 
                                        variable=self.backup_enabled_var,
                                        command=self._on_backup_toggle)
            enable_cb.pack(anchor=tk.W, pady=2)
            
            # Show OneDrive path
            onedrive_path = backup_mgr.get_backup_folder()
            if onedrive_path:
                path_label = ttk.Label(backup_frame, text=f"📁 {onedrive_path}", 
                                       font=("Arial", 7), foreground="gray")
                path_label.pack(anchor=tk.W, padx=(20, 0))
            
            # Backup schedule
            schedule_frame = ttk.Frame(backup_frame)
            schedule_frame.pack(anchor=tk.W, pady=(5, 2), padx=(20, 0))
            
            ttk.Label(schedule_frame, text="Backup:", font=("Arial", 8)).pack(side=tk.LEFT)
            
            self.backup_schedule_var = tk.StringVar(value=backup_settings.get("backup_schedule", "shift_end"))
            schedule_options = [
                ("After shift ends", "shift_end"),
                ("Every hour", "hourly"),
                ("Daily", "daily"),
                ("Manual only", "manual")
            ]
            
            for text, value in schedule_options:
                rb = ttk.Radiobutton(schedule_frame, text=text, variable=self.backup_schedule_var, value=value)
                rb.pack(side=tk.LEFT, padx=(10, 0))
            
            # Action buttons row
            action_frame = ttk.Frame(backup_frame)
            action_frame.pack(anchor=tk.W, pady=(5, 2))
            
            ttk.Button(action_frame, text="Backup Now", command=self._do_manual_backup).pack(side=tk.LEFT, padx=2)
            ttk.Button(action_frame, text="View Backups", command=self._show_backup_history).pack(side=tk.LEFT, padx=2)
            ttk.Button(action_frame, text="Restore", command=self._show_restore_dialog).pack(side=tk.LEFT, padx=2)
            
            # Status display
            status = backup_mgr.get_backup_status()
            self.backup_status_label = ttk.Label(backup_frame, 
                                                  text=f"{status['status_icon']} {status['status_text']}", 
                                                  font=("Arial", 8))
            self.backup_status_label.pack(anchor=tk.W, pady=(5, 0))
        else:
            # OneDrive not found
            self.backup_enabled_var = tk.BooleanVar(value=False)
            ttk.Label(backup_frame, text="⚠️ OneDrive not detected", 
                     font=("Arial", 9), foreground="orange").pack(anchor=tk.W)
            ttk.Label(backup_frame, text="Install OneDrive to enable cloud backup", 
                     font=("Arial", 8), foreground="gray").pack(anchor=tk.W)
            self.backup_schedule_var = tk.StringVar(value="shift_end")
            self.backup_status_label = None
        
        # Save/Cancel with version on bottom right
        save_cancel_frame = ttk.Frame(main_frame)
        save_cancel_frame.pack(fill=tk.X, pady=10)

        ttk.Button(save_cancel_frame, text="Save", command=self.save_settings).pack(side=tk.LEFT, padx=2)
        ttk.Button(save_cancel_frame, text="Cancel", command=self.window.destroy).pack(side=tk.LEFT, padx=2)
        
        # What's New button (?) on the right
        ttk.Button(save_cancel_frame, text="?  What's New", command=self.app.open_whats_new, width=12).pack(side=tk.RIGHT, padx=2)
    
    def sync_compensation_state(self, key):
        """Sync compensation checkbox state based on counter checkbox."""
        counter_var, comp_var, comp_cb = self.comp_mapping[key]
        if counter_var.get():
            # Counter is enabled - enable compensation checkbox
            comp_cb.config(state=tk.NORMAL)
        else:
            # Counter is disabled - disable and uncheck compensation
            comp_var.set(False)
            comp_cb.config(state=tk.DISABLED)
    
    def save_settings(self):
        """Save settings."""
        try:
            # Check if borderless mode is changing (before we save)
            old_borderless = self.data_manager.data["settings"].get("borderless_mode", False)
            new_borderless = self.borderless_mode_var.get()
            borderless_changed = old_borderless != new_borderless
            
            self.data_manager.data["settings"]["auto_start"] = self.auto_start_var.get()
            self.data_manager.data["settings"]["dark_mode"] = self.dark_mode_var.get()
            self.data_manager.data["settings"]["borderless_mode"] = self.borderless_mode_var.get()
            self.data_manager.data["settings"]["show_total"] = self.show_total_var.get()
            self.data_manager.data["settings"]["show_avg"] = self.show_avg_var.get()
            self.data_manager.data["settings"]["show_last_hour"] = self.show_last_hour_var.get()
            self.data_manager.data["settings"]["show_last_full_hour"] = self.show_last_full_hour_var.get()
            self.data_manager.data["settings"]["show_projected"] = self.show_projected_var.get()
            self.data_manager.data["settings"]["show_projected_shift"] = self.show_projected_shift_var.get()
            self.data_manager.data["settings"]["show_comp_total"] = self.show_comp_total_var.get()
            self.data_manager.data["settings"]["show_comp_avg"] = self.show_comp_avg_var.get()
            self.data_manager.data["settings"]["show_comp_last_hour"] = self.show_comp_last_hour_var.get()
            self.data_manager.data["settings"]["show_comp_last_full_hour"] = self.show_comp_last_full_hour_var.get()
            self.data_manager.data["settings"]["show_comp_projected"] = self.show_comp_projected_var.get()
            self.data_manager.data["settings"]["show_comp_projected_shift"] = self.show_comp_projected_shift_var.get()
            self.data_manager.data["settings"]["role"] = self.role_var.get()
            self.data_manager.data["settings"]["data_source"] = self.data_source_var.get()
            self.data_manager.data["settings"]["shift_length_hours"] = int(self.shift_length_var.get())
            self.data_manager.data["settings"]["min_study_seconds"] = int(self.min_seconds_var.get())
            self.data_manager.data["settings"]["ignore_duplicate_accessions"] = self.ignore_duplicates_var.get()
            self.data_manager.data["settings"]["show_time"] = self.show_time_var.get()
            self.data_manager.data["settings"]["stay_on_top"] = self.stay_on_top_var.get()
            self.data_manager.data["settings"]["show_pace_car"] = self.show_pace_car_var.get()
            self.data_manager.data["settings"]["mini_metric_1"] = self.mini_metric_1_var.get()
            self.data_manager.data["settings"]["mini_metric_2"] = self.mini_metric_2_var.get()
            
            # Save backup settings
            if "backup" not in self.data_manager.data:
                self.data_manager.data["backup"] = {}
            self.data_manager.data["backup"]["cloud_backup_enabled"] = self.backup_enabled_var.get()
            self.data_manager.data["backup"]["backup_schedule"] = self.backup_schedule_var.get()
            # If enabling backup, also set flags to prevent prompt from showing again
            if self.backup_enabled_var.get():
                self.data_manager.data["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.data["backup"]["first_backup_prompt_shown"] = True
            
            # Update tracker min_seconds
            self.app.tracker.min_seconds = self.data_manager.data["settings"]["min_study_seconds"]
            
            # Update stay on top setting
            self.app.root.attributes("-topmost", self.data_manager.data["settings"]["stay_on_top"])
            
            # Update pace car visibility (only show if enabled AND shift is active)
            has_active_shift = self.app.shift_start is not None
            if self.show_pace_car_var.get() and has_active_shift:
                self.app.pace_car_frame.pack(fill=tk.X, pady=(0, 2), after=self.app.counters_frame)
            else:
                self.app.pace_car_frame.pack_forget()
            
            self.data_manager.save()
            self.app.apply_theme()
            self.app._update_tk_widget_colors()
            # Force rebuild of widgets to show/hide time display when setting changes
            self.app.last_record_count = -1
            self.app.update_display()
            
            # Notify if borderless mode changed - requires restart
            if borderless_changed:
                messagebox.showinfo("Restart Required", 
                                   "Borderless mode changes will take effect after restarting the application.")
            
            self.window.destroy()
            logger.info("Settings saved")
        except Exception as e:
            messagebox.showerror("Error", f"Error saving settings: {e}")
            logger.error(f"Error saving settings: {e}")
    
    def on_settings_window_move(self, event):
        """Save settings window position when moved."""
        if event.widget == self.window:
            try:
                x = self.window.winfo_x()
                y = self.window.winfo_y()
                # Only save if position actually changed
                if self.last_saved_x != x or self.last_saved_y != y:
                    # Debounce: save after 100ms of no movement (shorter for responsiveness)
                    if hasattr(self, '_save_timer'):
                        self.window.after_cancel(self._save_timer)
                    self._save_timer = self.window.after(100, lambda: self.save_settings_position(x, y))
            except Exception as e:
                logger.error(f"Error saving settings window position: {e}")
    
    def on_settings_drag_end(self, event):
        """Handle end of settings window dragging - save position immediately."""
        # Cancel any pending debounced save
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        # Save immediately on mouse release
        self.save_settings_position()
    
    def save_settings_position(self, x=None, y=None):
        """Save settings window position."""
        try:
            if x is None:
                x = self.window.winfo_x()
            if y is None:
                y = self.window.winfo_y()
            
            if "window_positions" not in self.data_manager.data:
                self.data_manager.data["window_positions"] = {}
            self.data_manager.data["window_positions"]["settings"] = {
                "x": x,
                "y": y
            }
            self.last_saved_x = x
            self.last_saved_y = y
            self.data_manager.save()
        except Exception as e:
            logger.error(f"Error saving settings window position: {e}")
    
    def on_settings_closing(self):
        """Handle settings window closing."""
        # Cancel any pending save timer
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        self.save_settings_position()
        self.window.destroy()
    
    def _on_backup_toggle(self):
        """Handle backup enable/disable toggle."""
        enabled = self.backup_enabled_var.get()
        if enabled:
            # First time enabling - show confirmation
            backup_folder = self.data_manager.backup_manager.get_backup_folder()
            if backup_folder:
                logger.info(f"Cloud backup enabled. Backups will be stored in: {backup_folder}")
                # Mark that user has seen and responded to the prompt (if it was shown)
                if "backup" not in self.data_manager.data:
                    self.data_manager.data["backup"] = {}
                self.data_manager.data["backup"]["setup_prompt_dismissed"] = True
                self.data_manager.data["backup"]["first_backup_prompt_shown"] = True
                self.data_manager.save()
    
    def _do_manual_backup(self):
        """Perform a manual backup."""
        backup_mgr = self.data_manager.backup_manager
        
        # Show progress
        self.backup_status_label.config(text="⏳ Backing up...")
        self.window.update()
        
        # Perform backup
        result = backup_mgr.create_backup(force=True)
        
        if result["success"]:
            self.backup_status_label.config(text=f"☁️ Backup complete ({result.get('record_count', 0)} records)")
            messagebox.showinfo("Backup Complete", 
                               f"Backup created successfully!\n\n"
                               f"Location: {result['path']}\n"
                               f"Records: {result.get('record_count', 0)}")
        else:
            self.backup_status_label.config(text=f"⚠️ Backup failed")
            messagebox.showerror("Backup Failed", f"Backup failed: {result['error']}")
        
        # Save updated status
        self.data_manager.save()
    
    def _show_backup_history(self):
        """Show backup history dialog."""
        backup_mgr = self.data_manager.backup_manager
        backups = backup_mgr.get_backup_history()
        
        if not backups:
            messagebox.showinfo("No Backups", "No backups found yet.\n\nClick 'Backup Now' to create your first backup.")
            return
        
        # Create dialog
        dialog = tk.Toplevel(self.window)
        dialog.title("OneDrive Cloud")
        dialog.transient(self.window)
        dialog.grab_set()
        
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
        
        # Load saved window position or use default
        window_pos = self.data_manager.data.get("window_positions", {}).get("backup_history", None)
        if window_pos:
            x, y = window_pos['x'], window_pos['y']
            # Validate position before applying
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Backup history dialog position ({x}, {y}) is off-screen, finding nearest monitor")
                x, y = find_nearest_monitor_for_window(x, y, 500, 400)
            dialog.geometry(f"500x400+{x}+{y}")
        else:
            # Center on parent window
            dialog.update_idletasks()
            x = self.window.winfo_x() + (self.window.winfo_width() // 2) - 250
            y = self.window.winfo_y() + (self.window.winfo_height() // 2) - 200
            dialog.geometry(f"500x400+{x}+{y}")
        
        dialog.minsize(400, 250)
        
        # Track last saved position to avoid excessive saves
        last_saved_x = None
        last_saved_y = None
        
        def save_backup_history_position(x=None, y=None):
            """Save backup history dialog position."""
            nonlocal last_saved_x, last_saved_y
            try:
                if x is None:
                    x = dialog.winfo_x()
                if y is None:
                    y = dialog.winfo_y()
                
                if "window_positions" not in self.data_manager.data:
                    self.data_manager.data["window_positions"] = {}
                self.data_manager.data["window_positions"]["backup_history"] = {
                    "x": x,
                    "y": y
                }
                last_saved_x = x
                last_saved_y = y
                self.data_manager.save()
            except Exception as e:
                logger.error(f"Error saving backup history dialog position: {e}")
        
        def on_backup_history_move(event):
            """Save backup history dialog position when moved."""
            if event.widget == dialog:
                try:
                    x = dialog.winfo_x()
                    y = dialog.winfo_y()
                    # Only save if position actually changed
                    if last_saved_x != x or last_saved_y != y:
                        # Debounce: save after 100ms of no movement
                        if hasattr(dialog, '_save_timer'):
                            dialog.after_cancel(dialog._save_timer)
                        dialog._save_timer = dialog.after(100, lambda: save_backup_history_position(x, y))
                except Exception as e:
                    logger.error(f"Error saving backup history dialog position: {e}")
        
        def on_backup_history_drag_end(event):
            """Handle end of backup history dialog dragging - save position immediately."""
            # Cancel any pending debounced save
            if hasattr(dialog, '_save_timer'):
                try:
                    dialog.after_cancel(dialog._save_timer)
                except:
                    pass
            # Save immediately on mouse release
            save_backup_history_position()
        
        def on_backup_history_closing():
            """Handle backup history dialog closing."""
            # Cancel any pending save timer
            if hasattr(dialog, '_save_timer'):
                try:
                    dialog.after_cancel(dialog._save_timer)
                except:
                    pass
            save_backup_history_position()
            dialog.destroy()
        
        # Bind to window movement to save position (debounced)
        dialog.bind("<Configure>", on_backup_history_move)
        dialog.bind("<ButtonRelease-1>", on_backup_history_drag_end)
        dialog.protocol("WM_DELETE_WINDOW", on_backup_history_closing)
        
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
        
        # Get theme colors for canvas
        colors = self.app.get_theme_colors()
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
        
        # Store references
        dialog.backup_files = backups
        dialog.scrollable_frame = scrollable_frame
        dialog.canvas = canvas
        dialog.selected_backup = None
        
        def refresh_backup_list():
            """Refresh the backup list display."""
            # Clear existing widgets
            for widget in scrollable_frame.winfo_children():
                widget.destroy()
            
            # Re-fetch backup files
            backups = backup_mgr.get_backup_history()
            dialog.backup_files = backups
            
            # Get theme colors
            colors = self.app.get_theme_colors()
            
            # Populate scrollable frame with backup entries
            for backup in backups:
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
                delete_btn.backup_display = backup["timestamp"].strftime("%B %d, %Y at %I:%M %p")
                delete_btn.bind("<Button-1>", lambda e, btn=delete_btn: delete_backup(btn))
                delete_btn.bind("<Enter>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_hover"]))
                delete_btn.bind("<Leave>", lambda e, btn=delete_btn: btn.config(bg=colors["delete_btn_bg"]))
                delete_btn.pack(side=tk.LEFT, padx=(0, 5))
                
                # Backup label (clickable) - format same as local backups
                backup_display = backup["timestamp"].strftime("%B %d, %Y at %I:%M %p")
                # Add record count to display
                if backup.get("record_count", 0) > 0:
                    backup_display += f" ({backup['record_count']} records)"
                backup_label = ttk.Label(
                    backup_frame,
                    text=backup_display,
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
            if not backups:
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
            """Restore selected backup."""
            if not dialog.selected_backup:
                messagebox.showwarning("No Selection", "Please select a backup file.")
                return
            
            selected_backup = dialog.selected_backup
            
            # Confirm overwrite
            date_str = selected_backup["timestamp"].strftime("%B %d, %Y at %I:%M %p").lower()
            response = messagebox.askyesno(
                "Confirm Overwrite",
                f"Are you sure you want to restore this backup?\n\n"
                f"Backup: {date_str}\n\n"
                f"This will REPLACE your current study data. This action cannot be undone.\n\n"
                f"Consider creating a backup of your current data first.",
                icon="warning",
                parent=dialog
            )
            
            if response:
                dialog.destroy()
                self._confirm_restore(selected_backup)
        
        # Initial population
        refresh_backup_list()
        
        # Buttons
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(btn_frame, text="Restore", command=on_load).pack(side=tk.LEFT, padx=2)
        
        # Open folder button
        def open_folder():
            folder = backup_mgr.get_backup_folder()
            if folder:
                os.startfile(folder)
        
        ttk.Button(btn_frame, text="Open Folder", command=open_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Close", command=on_backup_history_closing).pack(side=tk.RIGHT, padx=2)
    
    def _show_restore_dialog(self):
        """Show restore from backup dialog."""
        backup_mgr = self.data_manager.backup_manager
        backups = backup_mgr.get_backup_history()
        
        if not backups:
            messagebox.showinfo("No Backups", "No backups available to restore from.")
            return
        
        # Show backup history and let user select
        self._show_backup_history()
    
    def _confirm_restore(self, backup: dict):
        """Confirm and perform restore from backup."""
        backup_mgr = self.data_manager.backup_manager
        
        # Get current database info for comparison - count from database directly
        try:
            cursor = self.data_manager.db.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM records")
            current_count = cursor.fetchone()[0]
        except:
            current_count = 0
        
        # Confirmation dialog
        date_str = backup["timestamp"].strftime("%B %d, %Y at %I:%M %p").lower()
        msg = (f"Restore from backup?\n\n"
               f"Backup: {date_str}\n"
               f"Contains: {backup['record_count']} records\n"
               f"Size: {backup['size_formatted']}\n\n"
               f"Current database: {current_count} records\n\n"
               f"⚠️ Your current data will be replaced.\n"
               f"A backup of current data will be created first.")
        
        if not messagebox.askyesno("Confirm Restore", msg, icon="warning"):
            return
        
        # Perform restore
        result = backup_mgr.restore_from_backup(backup["path"])
        
        if result["success"]:
            # Reload data from the restored database
            try:
                # Reload records from database
                self.data_manager.records_data = self.data_manager._load_records_from_db()
                # Update data structures
                self.data_manager.data["records"] = self.data_manager.records_data.get("records", [])
                self.data_manager.data["current_shift"] = self.data_manager.records_data.get("current_shift", {
                    "shift_start": None,
                    "shift_end": None,
                    "records": []
                })
                self.data_manager.data["shifts"] = self.data_manager.records_data.get("shifts", [])
                
                # Refresh the app display
                self.app.update_display()
                
                messagebox.showinfo("Restore Complete", 
                                   f"Database restored successfully!\n\n"
                                   f"Data has been reloaded from the backup.")
                logger.info(f"Database restored and reloaded: {backup['path']}")
            except Exception as e:
                logger.error(f"Error reloading data after restore: {e}", exc_info=True)
                messagebox.showwarning("Restore Complete", 
                                      f"Database restored successfully!\n\n"
                                      f"However, there was an error reloading the data.\n"
                                      f"Please restart the application to see the restored data.")
        else:
            messagebox.showerror("Restore Failed", f"Restore failed: {result['error']}")




__all__ = ['SettingsWindow']
