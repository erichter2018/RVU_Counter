"""Mini interface window for RVU Counter - minimal, distraction-free display."""

from __future__ import annotations
import tkinter as tk
from tkinter import ttk
import logging
from typing import TYPE_CHECKING, Optional
from datetime import datetime

from ..core.platform_utils import is_point_on_any_monitor, find_nearest_monitor_for_window

if TYPE_CHECKING:
    from ..data import RVUData
    from .main_window import RVUCounterApp

logger = logging.getLogger(__name__)


class MiniWindow:
    """Mini interface - compact, distraction-free RVU display."""
    
    def __init__(self, parent, data_manager: 'RVUData', app: 'RVUCounterApp'):
        self.parent = parent
        self.data_manager = data_manager
        self.app = app
        
        # Create borderless window
        self.window = tk.Toplevel(parent)
        self.window.title("RVU Counter")
        self.window.overrideredirect(True)  # Remove window decorations
        
        # Set stay on top based on main window setting
        stay_on_top = self.data_manager.data["settings"].get("stay_on_top", True)
        self.window.attributes("-topmost", stay_on_top)
        
        # Load saved position or center on main window
        window_pos = self.data_manager.data.get("window_positions", {}).get("mini", None)
        if window_pos:
            x, y = window_pos['x'], window_pos['y']
            # Validate position before applying
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Mini window position ({x}, {y}) is off-screen, using default")
                x, y = self._get_default_position()
        else:
            x, y = self._get_default_position()
        
        # Set initial position (size will be calculated after packing widgets)
        self.window.geometry(f"+{x}+{y}")
        
        # Track dragging
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.last_saved_x = None
        self.last_saved_y = None
        
        # Bind window events
        self.window.bind("<ButtonPress-1>", self.on_drag_start)
        self.window.bind("<B1-Motion>", self.on_drag_motion)
        self.window.bind("<ButtonRelease-1>", self.on_drag_end)
        self.window.bind("<Double-Button-1>", self.on_double_click)
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Create UI
        self.create_mini_ui()
        
        # Update display
        self.update_display()
        
        # Schedule periodic updates
        self.schedule_update()
    
    def _get_default_position(self) -> tuple[int, int]:
        """Get default position centered on main window."""
        try:
            main_x = self.app.root.winfo_x()
            main_y = self.app.root.winfo_y()
            main_width = self.app.root.winfo_width()
            main_height = self.app.root.winfo_height()
            # Center on main window
            x = main_x + (main_width // 2) - 60  # Approximate half-width of mini window (narrower now)
            y = main_y + (main_height // 2) - 40  # Approximate half-height of mini window (shorter now)
            return x, y
        except:
            return 100, 100  # Fallback
    
    def create_mini_ui(self):
        """Create the mini interface UI."""
        # Get theme colors
        dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
        if dark_mode:
            bg_color = "#1e1e1e"
            fg_color = "#ffffff"
            border_color = "#444444"
        else:
            bg_color = "#f0f0f0"
            fg_color = "#000000"
            border_color = "#cccccc"
        
        self.window.configure(bg=border_color)
        
        # Main container with padding for border effect
        main_frame = tk.Frame(self.window, bg=bg_color, padx=2, pady=2)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        
        # Metrics frame (will be populated dynamically)
        self.metrics_frame = tk.Frame(main_frame, bg=bg_color)
        self.metrics_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        
        # Undo button will be created with first metric
        self.undo_btn = None
        
        # Will create metric labels dynamically in update_display (but not destroy them every time)
        self.metric_widgets = {}  # Store widgets to update them without recreating
        
        # Cache pace calculation to avoid expensive recalculation every second
        self.cached_pace_value = None
        self.cached_pace_color = None
        self.last_pace_calculation_time = 0
        self.pace_calculation_interval = 5.0  # Recalculate pace every 5 seconds
        
        # Store colors for later use
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.border_color = border_color
        self.dark_mode = dark_mode
    
    def update_display(self):
        """Update the mini interface display with current metrics."""
        # Get selected metrics from settings
        settings = self.data_manager.data["settings"]
        metric1 = settings.get("mini_metric_1", "pace")
        metric2 = settings.get("mini_metric_2", "current_total")
        
        # Create labels for selected metrics
        metrics_to_show = []
        if metric1:
            metrics_to_show.append(metric1)
        if metric2 and metric2 != metric1:
            metrics_to_show.append(metric2)
        
        # Check if layout needs to change (different metrics selected)
        current_metrics_key = tuple(metrics_to_show)
        needs_rebuild = not hasattr(self, '_current_metrics') or self._current_metrics != current_metrics_key
        
        if needs_rebuild:
            # Clear all existing widgets
            for widget in self.metrics_frame.winfo_children():
                widget.destroy()
            self.metric_widgets.clear()
            self.undo_btn = None
            
            # Create new layout
            for i, metric_key in enumerate(metrics_to_show):
                # Frame for this metric (contains label and optionally undo button)
                metric_row = tk.Frame(self.metrics_frame, bg=self.bg_color)
                metric_row.pack(fill=tk.X, pady=(3 if i > 0 else 0, 0))
                
                # Metric name label (left-aligned)
                name_label = tk.Label(
                    metric_row,
                    text="",
                    font=("Arial", 6),
                    bg=self.bg_color,
                    fg=self.fg_color,
                    anchor=tk.W
                )
                name_label.pack(side=tk.LEFT)
                
                # Add undo button to first metric row (right-aligned, with 10px spacing)
                if i == 0:
                    self.undo_btn = tk.Label(
                        metric_row,
                        text="U",
                        font=("Arial", 6, "bold"),
                        bg=self.bg_color if not self.dark_mode else "#2d2d2d",
                        fg=self.fg_color,
                        cursor="hand2",
                        padx=2,
                        pady=1,
                        relief=tk.RAISED,
                        borderwidth=1
                    )
                    self.undo_btn.pack(side=tk.RIGHT, padx=(10, 0))  # 10px spacing from label
                    self.undo_btn.bind("<Button-1>", self.on_undo_click)
                    self.undo_btn.bind("<Enter>", self.on_undo_enter)
                    self.undo_btn.bind("<Leave>", self.on_undo_leave)
                
                # Metric value label (left-aligned)
                value_label = tk.Label(
                    self.metrics_frame,
                    text="",
                    font=("Arial", 9, "bold"),
                    bg=self.bg_color,
                    fg=self.fg_color,
                    anchor=tk.W
                )
                value_label.pack(fill=tk.X, pady=(0, 1))
                
                # Store widget references
                self.metric_widgets[metric_key] = {
                    'name': name_label,
                    'value': value_label
                }
            
            self._current_metrics = current_metrics_key
        
        # Update widget contents (no flickering - just update text and colors)
        for metric_key in metrics_to_show:
            if metric_key in self.metric_widgets:
                value_text, color = self._get_metric_value(metric_key)
                metric_name = self._get_metric_name(metric_key)
                
                widgets = self.metric_widgets[metric_key]
                widgets['name'].config(text=metric_name)
                widgets['value'].config(text=value_text, fg=color if color else self.fg_color)
    
    def _get_metric_value(self, metric_key: str) -> tuple[str, Optional[str]]:
        """Get the display value and color for a metric."""
        if not self.app.is_running:
            return "Not running", None
        
        settings = self.data_manager.data["settings"]
        show_pace_car = settings.get("show_pace_car", False)
        
        if metric_key == "pace":
            # Get pace vs prior shift - must have pace car enabled AND an active shift
            shift_start_str = self.data_manager.data["current_shift"].get("shift_start")
            if show_pace_car and shift_start_str:
                # Use cached pace value if available and recent (within 5 seconds)
                import time
                current_time_seconds = time.time()
                time_since_last_calc = current_time_seconds - self.last_pace_calculation_time
                
                if time_since_last_calc < self.pace_calculation_interval and self.cached_pace_value is not None:
                    # Return cached value
                    return self.cached_pace_value, self.cached_pace_color
                
                # Need to recalculate pace
                try:
                    # Calculate pace difference using the same logic as main window
                    shift_data = self.data_manager.data.get("current_shift", {})
                    records = shift_data.get("records", [])
                    current_rvu = sum(r.get('rvu', 0.0) for r in records)
                    
                    shift_start = datetime.fromisoformat(shift_start_str)
                    current_time = datetime.now()
                    
                    # Calculate elapsed time
                    use_elapsed_time = False
                    if self.app.pace_comparison_mode == 'goal':
                        use_elapsed_time = True
                    else:
                        typical_start_hour = self.app.typical_shift_start_hour
                        typical_start = shift_start.replace(hour=typical_start_hour, minute=0, second=0, microsecond=0)
                        if shift_start.hour < typical_start_hour:
                            from datetime import timedelta
                            typical_start = typical_start - timedelta(days=1)
                        minutes_diff = abs((shift_start - typical_start).total_seconds() / 60)
                        if minutes_diff > 30:
                            use_elapsed_time = True
                    
                    if use_elapsed_time:
                        elapsed_minutes = (current_time - shift_start).total_seconds() / 60
                    else:
                        reference_start = self.app._get_reference_shift_start(current_time)
                        elapsed_minutes = (current_time - reference_start).total_seconds() / 60
                    
                    if elapsed_minutes < 0:
                        elapsed_minutes = 0
                    
                    # Get prior shift data
                    prior_data = self.app._get_prior_shift_rvu_at_elapsed_time(elapsed_minutes, use_elapsed_time)
                    
                    if prior_data:
                        prior_rvu_at_elapsed, prior_total_rvu = prior_data
                        difference = current_rvu - prior_rvu_at_elapsed
                        
                        # Use same colors as main window
                        dark_mode = settings.get("dark_mode", False)
                        if difference >= 0:
                            # Ahead - blue
                            if dark_mode:
                                color = "#87CEEB"  # Bright sky blue for dark mode
                            else:
                                color = "#2874A6"  # Darker blue for light mode
                            pace_value = f"+{difference:.1f}"
                        else:
                            # Behind - red
                            if dark_mode:
                                color = "#ef5350"  # Brighter red for dark mode
                            else:
                                color = "#B71C1C"  # Darker red for light mode
                            pace_value = f"{difference:.1f}"
                        
                        # Cache the result
                        self.cached_pace_value = pace_value
                        self.cached_pace_color = color
                        self.last_pace_calculation_time = current_time_seconds
                        
                        return pace_value, color
                except Exception as e:
                    logger.error(f"Error calculating pace in mini window: {e}", exc_info=True)
            
            return "N/A", None
        
        elif metric_key == "current_total":
            # Current shift total
            shift_data = self.data_manager.data.get("current_shift", {})
            records = shift_data.get("records", [])
            total_rvu = sum(r.get('rvu', 0.0) for r in records)
            return f"{total_rvu:.1f}", None
        
        elif metric_key == "estimated_total":
            # Estimated total for shift
            if self.app.projected_shift_end:
                now = datetime.now()
                shift_data = self.data_manager.data.get("current_shift", {})
                records = shift_data.get("records", [])
                current_total = sum(r.get('rvu', 0.0) for r in records)
                
                if self.app.shift_start:
                    elapsed = (now - self.app.shift_start).total_seconds() / 3600.0
                    if elapsed > 0:
                        avg_per_hour = current_total / elapsed
                        shift_length = settings.get("shift_length_hours", 9)
                        estimated_total = avg_per_hour * shift_length
                        return f"{estimated_total:.1f}", None
            return "N/A", None
        
        elif metric_key == "average_hour":
            # Average per hour
            if self.app.shift_start:
                now = datetime.now()
                shift_data = self.data_manager.data.get("current_shift", {})
                records = shift_data.get("records", [])
                current_total = sum(r.get('rvu', 0.0) for r in records)
                
                elapsed = (now - self.app.shift_start).total_seconds() / 3600.0
                if elapsed > 0:
                    avg_per_hour = current_total / elapsed
                    return f"{avg_per_hour:.1f}/hr", None
            return "N/A", None
        
        return "N/A", None
    
    def _get_metric_name(self, metric_key: str) -> str:
        """Get the display name for a metric."""
        names = {
            "pace": "Pace",
            "current_total": "Current Total",
            "estimated_total": "Est. Total",
            "average_hour": "Avg/Hour"
        }
        return names.get(metric_key, "Unknown")
    
    def on_drag_start(self, event):
        """Handle drag start."""
        # Check if click is on the window itself, not the undo button
        if event.widget == self.undo_btn:
            return
        
        self.drag_start_x = event.x_root - self.window.winfo_x()
        self.drag_start_y = event.y_root - self.window.winfo_y()
    
    def on_drag_motion(self, event):
        """Handle drag motion."""
        # Check if we started dragging from the window (not undo button)
        if self.drag_start_x == 0 and self.drag_start_y == 0:
            return
        
        x = event.x_root - self.drag_start_x
        y = event.y_root - self.drag_start_y
        self.window.geometry(f"+{x}+{y}")
    
    def on_drag_end(self, event):
        """Handle drag end - save position."""
        self.save_position()
        # Reset drag tracking
        self.drag_start_x = 0
        self.drag_start_y = 0
    
    def on_double_click(self, event):
        """Handle double-click - return to main interface."""
        # Check if double-click is on the window itself, not the undo button
        if event.widget == self.undo_btn:
            return
        
        logger.info("Mini interface double-clicked, returning to main interface")
        self.close_and_show_main()
    
    def on_undo_click(self, event):
        """Handle undo button click."""
        logger.info("Undo button clicked in mini interface")
        # Hide tooltip if visible
        if hasattr(self, '_tooltip') and self._tooltip:
            self._tooltip.destroy()
            self._tooltip = None
        # Call the main app's undo function
        self.app.undo_last()
        # Update display
        self.update_display()
    
    def on_undo_enter(self, event):
        """Handle mouse entering undo button - show tooltip."""
        self.undo_btn.config(relief=tk.SUNKEN)
        
        # Determine if we're in undo or redo mode
        is_redo = self.app.undo_used and self.app.last_undone_study
        
        if is_redo:
            # Show the last undone study
            procedure = self.app.last_undone_study.get("procedure", "Unknown")
        else:
            # Show the last record
            records = self.data_manager.data["current_shift"].get("records", [])
            if records:
                last_record = records[-1]
                procedure = last_record.get("procedure", "Unknown")
            else:
                return  # No records, no tooltip
        
        # Create tooltip
        self._tooltip = tk.Toplevel(self.window)
        self._tooltip.wm_overrideredirect(True)
        self._tooltip.wm_attributes("-topmost", True)
        
        # Get theme colors
        if self.dark_mode:
            tooltip_bg = "#2d2d2d"
            tooltip_fg = "#ffffff"
            tooltip_border = "#555555"
        else:
            tooltip_bg = "#ffffcc"
            tooltip_fg = "#000000"
            tooltip_border = "#888888"
        
        # Create tooltip label with border
        border_frame = tk.Frame(self._tooltip, bg=tooltip_border, padx=1, pady=1)
        border_frame.pack()
        
        label = tk.Label(
            border_frame,
            text=procedure,
            font=("Arial", 7),
            bg=tooltip_bg,
            fg=tooltip_fg,
            justify=tk.LEFT,
            padx=4,
            pady=2
        )
        label.pack()
        
        # Update geometry to calculate tooltip size
        self._tooltip.update_idletasks()
        
        # Position tooltip above undo button
        x = self.undo_btn.winfo_rootx()
        y = self.undo_btn.winfo_rooty() - self._tooltip.winfo_height() - 2
        self._tooltip.wm_geometry(f"+{x}+{y}")
    
    def on_undo_leave(self, event):
        """Handle mouse leaving undo button - hide tooltip."""
        self.undo_btn.config(relief=tk.RAISED)
        
        # Destroy tooltip if it exists
        if hasattr(self, '_tooltip') and self._tooltip:
            self._tooltip.destroy()
            self._tooltip = None
    
    def save_position(self):
        """Save current window position."""
        try:
            x = self.window.winfo_x()
            y = self.window.winfo_y()
            
            # Only save if position actually changed
            if self.last_saved_x != x or self.last_saved_y != y:
                if "window_positions" not in self.data_manager.data:
                    self.data_manager.data["window_positions"] = {}
                self.data_manager.data["window_positions"]["mini"] = {
                    "x": x,
                    "y": y
                }
                self.last_saved_x = x
                self.last_saved_y = y
                self.data_manager.save()
                logger.debug(f"Mini window position saved: ({x}, {y})")
        except Exception as e:
            logger.error(f"Error saving mini window position: {e}")
    
    def close_and_show_main(self):
        """Close mini interface and show main interface."""
        # Save position before closing
        self.save_position()
        
        # Restore main window position before showing
        self.app.restore_window_position()
        
        # Show main window
        self.app.root.deiconify()
        
        # Close mini window
        self.window.destroy()
        
        # Clear reference in main app
        self.app.mini_window = None
    
    def on_closing(self):
        """Handle window closing."""
        self.close_and_show_main()
    
    def schedule_update(self):
        """Schedule the next display update."""
        try:
            self.update_display()
            # Update every second
            self.window.after(1000, self.schedule_update)
        except Exception as e:
            logger.error(f"Error in mini interface update: {e}")


__all__ = ['MiniWindow']


