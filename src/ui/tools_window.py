"""Tools window for database repair and Excel checking."""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import logging
import threading
from typing import TYPE_CHECKING

from ..core.platform_utils import (
    is_point_on_any_monitor,
    find_nearest_monitor_for_window,
    get_primary_monitor_bounds
)
from ..logic.database_repair import DatabaseRepair
from ..logic.excel_checker import ExcelChecker

if TYPE_CHECKING:
    from .main_window import RVUCounterApp

logger = logging.getLogger(__name__)


class ToolsWindow:
    """Window for integrated tools (Database Repair and Excel Checker)."""
    
    def __init__(self, parent, app: 'RVUCounterApp'):
        self.parent = parent
        self.app = app
        self.data_manager = app.data_manager
        
        # Create window
        self.window = tk.Toplevel(parent)
        self.window.title("RVU Counter Tools")
        self.window.transient(parent)
        
        # Load saved window position or center on screen
        window_pos = self.data_manager.data.get("window_positions", {}).get("tools", None)
        if window_pos:
            x, y = window_pos['x'], window_pos['y']
            # Validate position before applying
            if not is_point_on_any_monitor(x + 30, y + 30):
                logger.warning(f"Tools window position ({x}, {y}) is off-screen, finding nearest monitor")
                x, y = find_nearest_monitor_for_window(x, y, 800, 600)
            self.window.geometry(f"800x600+{x}+{y}")
        else:
            # Center on primary monitor
            try:
                primary = get_primary_monitor_bounds()
                x = primary[0] + (primary[2] - primary[0] - 800) // 2
                y = primary[1] + (primary[3] - primary[1] - 600) // 2
                self.window.geometry(f"800x600+{x}+{y}")
            except:
                self.window.geometry("800x600")
        
        # Track last saved position to avoid excessive saves
        self.last_saved_x = None
        self.last_saved_y = None
        
        # Apply theme colors
        dark_mode = self.data_manager.data["settings"].get("dark_mode", False)
        self.dark_mode = dark_mode
        self.bg_color = "#1e1e1e" if dark_mode else "#f0f0f0"
        self.fg_color = "#ffffff" if dark_mode else "#000000"
        self.text_bg = "#2d2d2d" if dark_mode else "#ffffff"
        self.text_fg = "#ffffff" if dark_mode else "#000000"
        self.window.configure(bg=self.bg_color)
        
        # Apply dark mode style to ttk widgets
        style = ttk.Style()
        if dark_mode:
            # Configure notebook for dark mode
            style.theme_use('clam')  # Use clam theme for better dark mode support
            style.configure("TNotebook", background=self.bg_color, borderwidth=0)
            style.configure("TNotebook.Tab", 
                          background="#2d2d2d", 
                          foreground="#ffffff", 
                          padding=[10, 5], 
                          borderwidth=1,
                          lightcolor="#2d2d2d",
                          darkcolor="#2d2d2d")
            style.map("TNotebook.Tab", 
                     background=[("selected", "#0078d7"), ("!selected", "#2d2d2d")],
                     foreground=[("selected", "#ffffff"), ("!selected", "#aaaaaa")],
                     expand=[("selected", [1, 1, 1, 0])])
            
            # Configure frames inside tabs
            style.configure("TFrame", background=self.bg_color)
            style.configure("TLabel", background=self.bg_color, foreground=self.fg_color)
            style.configure("TLabelframe", background=self.bg_color, foreground=self.fg_color)
            style.configure("TLabelframe.Label", background=self.bg_color, foreground=self.fg_color)
        
        # Bind window events for position saving
        self.window.bind("<Configure>", self.on_window_move)
        self.window.bind("<ButtonRelease-1>", self.on_drag_end)
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.create_ui()
        
    def create_ui(self):
        """Create the tools UI."""
        # Notebook for tabs
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Tab 1: Database Repair
        repair_frame = ttk.Frame(self.notebook)
        self.notebook.add(repair_frame, text="Database Repair")
        self.create_repair_tab(repair_frame)
        
        # Tab 2: Excel Checker
        excel_frame = ttk.Frame(self.notebook)
        self.notebook.add(excel_frame, text="Excel Checker")
        self.create_excel_tab(excel_frame)
        
    def create_repair_tab(self, parent):
        """Create the database repair tab."""
        # Header
        header = ttk.Label(parent, text="Database Repair Tool", font=("Arial", 12, "bold"))
        header.pack(pady=10)
        
        desc = ttk.Label(parent, text="Scan your database for records that don't match current RVU rules\nand optionally fix them.",
                        justify=tk.CENTER)
        desc.pack(pady=5)
        
        # Button frame
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(pady=10)
        
        self.scan_btn = ttk.Button(btn_frame, text="Scan for Mismatches", command=self.scan_database)
        self.scan_btn.pack(side=tk.LEFT, padx=5)
        
        self.fix_btn = ttk.Button(btn_frame, text="Fix All Mismatches", command=self.fix_mismatches, state=tk.DISABLED)
        self.fix_btn.pack(side=tk.LEFT, padx=5)
        
        # Progress
        self.repair_progress_var = tk.StringVar(value="")
        self.repair_progress_label = ttk.Label(parent, textvariable=self.repair_progress_var)
        self.repair_progress_label.pack(pady=5)
        
        self.repair_progress = ttk.Progressbar(parent, mode='determinate', length=600)
        self.repair_progress.pack(pady=5)
        
        # Results text area
        result_frame = ttk.LabelFrame(parent, text="Results", padding=10)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.repair_text = scrolledtext.ScrolledText(result_frame, height=15, wrap=tk.WORD,
                                                     bg=self.text_bg, fg=self.text_fg,
                                                     insertbackground=self.text_fg)
        self.repair_text.pack(fill=tk.BOTH, expand=True)
        
        # Store mismatches for fixing
        self.current_mismatches = []
        
    def create_excel_tab(self, parent):
        """Create the Excel checker tab."""
        # Header
        header = ttk.Label(parent, text="Excel Payroll Checker", font=("Arial", 12, "bold"))
        header.pack(pady=10)
        
        desc = ttk.Label(parent, text="Compare Excel payroll files with current RVU rules\nto identify discrepancies.",
                        justify=tk.CENTER)
        desc.pack(pady=5)
        
        # Button frame
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(pady=10)
        
        self.upload_btn = ttk.Button(btn_frame, text="Select Excel File", command=self.select_excel_file)
        self.upload_btn.pack(side=tk.LEFT, padx=5)
        
        self.export_btn = ttk.Button(btn_frame, text="Export Report", command=self.export_report, state=tk.DISABLED)
        self.export_btn.pack(side=tk.LEFT, padx=5)
        
        # Progress
        self.excel_progress_var = tk.StringVar(value="")
        self.excel_progress_label = ttk.Label(parent, textvariable=self.excel_progress_var)
        self.excel_progress_label.pack(pady=5)
        
        self.excel_progress = ttk.Progressbar(parent, mode='determinate', length=600)
        self.excel_progress.pack(pady=5)
        
        # Results text area
        result_frame = ttk.LabelFrame(parent, text="Results", padding=10)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.excel_text = scrolledtext.ScrolledText(result_frame, height=15, wrap=tk.WORD,
                                                    bg=self.text_bg, fg=self.text_fg,
                                                    insertbackground=self.text_fg)
        self.excel_text.pack(fill=tk.BOTH, expand=True)
        
        # Store report for export
        self.current_report = None
        
    def scan_database(self):
        """Scan the database for mismatches."""
        self.scan_btn.config(state=tk.DISABLED)
        self.fix_btn.config(state=tk.DISABLED)
        self.repair_text.delete(1.0, tk.END)
        self.repair_progress['value'] = 0
        self.repair_progress_var.set("Scanning database...")
        
        def do_scan():
            try:
                repair = DatabaseRepair(self.data_manager)
                
                def progress(current, total):
                    pct = (current / total) * 100
                    self.window.after(0, lambda: self.repair_progress.config(value=pct))
                    self.window.after(0, lambda: self.repair_progress_var.set(f"Scanning: {current}/{total} records"))
                
                mismatches = repair.find_mismatches(progress_callback=progress)
                self.current_mismatches = mismatches
                
                # Format results
                if not mismatches:
                    result = "✓ No mismatches found! All records match current RVU rules."
                else:
                    result = f"Found {len(mismatches)} mismatches:\n\n"
                    for m in mismatches[:50]:  # Show first 50
                        result += f"• {m['procedure']}\n"
                        result += f"  Old: {m['old_type']} ({m['old_rvu']} RVU)\n"
                        result += f"  New: {m['new_type']} ({m['new_rvu']} RVU)\n\n"
                    if len(mismatches) > 50:
                        result += f"\n... and {len(mismatches) - 50} more.\n"
                
                self.window.after(0, lambda: self.repair_text.insert(1.0, result))
                self.window.after(0, lambda: self.fix_btn.config(state=tk.NORMAL if mismatches else tk.DISABLED))
                self.window.after(0, lambda: self.repair_progress_var.set(f"Scan complete: {len(mismatches)} mismatches found"))
                
            except Exception as e:
                logger.error(f"Error scanning database: {e}")
                self.window.after(0, lambda: messagebox.showerror("Error", f"Scan failed: {e}"))
                
            finally:
                self.window.after(0, lambda: self.scan_btn.config(state=tk.NORMAL))
                self.window.after(0, lambda: self.repair_progress.config(value=0))
        
        threading.Thread(target=do_scan, daemon=True).start()
        
    def fix_mismatches(self):
        """Fix all found mismatches."""
        if not self.current_mismatches:
            return
            
        count = len(self.current_mismatches)
        if not messagebox.askyesno("Confirm Fix", 
                                    f"This will update {count} records in your database.\n\n"
                                    "A backup is recommended before proceeding.\n\n"
                                    "Continue?"):
            return
            
        self.fix_btn.config(state=tk.DISABLED)
        self.repair_progress['value'] = 0
        self.repair_progress_var.set("Fixing mismatches...")
        
        def do_fix():
            try:
                repair = DatabaseRepair(self.data_manager)
                
                def progress(current, total):
                    pct = (current / total) * 100
                    self.window.after(0, lambda: self.repair_progress.config(value=pct))
                    self.window.after(0, lambda: self.repair_progress_var.set(f"Fixing: {current}/{total} records"))
                
                updated = repair.fix_mismatches(self.current_mismatches, progress_callback=progress)
                
                self.window.after(0, lambda: messagebox.showinfo("Success", f"Updated {updated} records successfully!"))
                self.window.after(0, lambda: self.repair_progress_var.set(f"Fixed {updated} records"))
                self.current_mismatches = []
                
                # Refresh app data
                self.window.after(0, self.app.refresh_display)
                
            except Exception as e:
                logger.error(f"Error fixing mismatches: {e}")
                self.window.after(0, lambda: messagebox.showerror("Error", f"Fix failed: {e}"))
                
            finally:
                self.window.after(0, lambda: self.repair_progress.config(value=0))
        
        threading.Thread(target=do_fix, daemon=True).start()
        
    def select_excel_file(self):
        """Select and process an Excel file."""
        file_path = filedialog.askopenfilename(
            title="Select Excel Payroll File",
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")]
        )
        
        if not file_path:
            return
        
        # Store the selected file path for later use
        self.selected_excel_path = file_path
            
        self.upload_btn.config(state=tk.DISABLED)
        self.export_btn.config(state=tk.DISABLED)
        self.excel_text.delete(1.0, tk.END)
        self.excel_progress['value'] = 0
        self.excel_progress_var.set("Processing Excel file...")
        
        def do_check():
            try:
                checker = ExcelChecker(
                    self.data_manager.data.get("rvu_table", {}),
                    self.data_manager.data.get("classification_rules", {}),
                    self.data_manager.data.get("direct_lookups", {})
                )
                
                def progress(current, total):
                    pct = (current / total) * 100
                    self.window.after(0, lambda: self.excel_progress.config(value=pct))
                    self.window.after(0, lambda: self.excel_progress_var.set(f"Processing: {current}/{total} rows"))
                
                results = checker.check_file(file_path, progress_callback=progress)
                report_text = checker.generate_report_text(results)
                
                self.current_report = report_text
                
                self.window.after(0, lambda: self.excel_text.insert(1.0, report_text))
                self.window.after(0, lambda: self.export_btn.config(state=tk.NORMAL))
                self.window.after(0, lambda: self.excel_progress_var.set("Processing complete"))
                
            except Exception as e:
                logger.error(f"Error checking Excel file: {e}")
                self.window.after(0, lambda: messagebox.showerror("Error", f"Check failed: {e}"))
                
            finally:
                self.window.after(0, lambda: self.upload_btn.config(state=tk.NORMAL))
                self.window.after(0, lambda: self.excel_progress.config(value=0))
        
        threading.Thread(target=do_check, daemon=True).start()
        
    def export_report(self):
        """Export the current report to a text file."""
        if not self.current_report:
            return
        
        # Generate default filename from input Excel file
        default_filename = ""
        if hasattr(self, 'selected_excel_path') and self.selected_excel_path:
            # Get the base name without extension and add .txt
            base_name = os.path.splitext(os.path.basename(self.selected_excel_path))[0]
            default_filename = f"{base_name}_report.txt"
            
        file_path = filedialog.asksaveasfilename(
            title="Save Report",
            defaultextension=".txt",
            initialfile=default_filename,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.current_report)
                messagebox.showinfo("Success", f"Report saved to:\n{file_path}")
            except Exception as e:
                logger.error(f"Error saving report: {e}")
                messagebox.showerror("Error", f"Failed to save report: {e}")
    
    def on_window_move(self, event):
        """Handle window movement (debounced save)."""
        # Cancel any pending save timer
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        
        # Set a new timer (only save after user stops moving for 500ms)
        self._save_timer = self.window.after(500, self.save_position)
    
    def on_drag_end(self, event):
        """Handle drag end - save position immediately."""
        self.save_position()
    
    def save_position(self):
        """Save the current window position."""
        try:
            # Get current position
            x = self.window.winfo_x()
            y = self.window.winfo_y()
            
            # Only save if position actually changed
            if self.last_saved_x != x or self.last_saved_y != y:
                self.last_saved_x = x
                self.last_saved_y = y
                
                # Save to data manager
                if "window_positions" not in self.data_manager.data:
                    self.data_manager.data["window_positions"] = {}
                
                self.data_manager.data["window_positions"]["tools"] = {"x": x, "y": y}
                self.data_manager.save_data(save_records=False)
                
                logger.debug(f"Saved tools window position: x={x}, y={y}")
        except Exception as e:
            logger.error(f"Error saving tools window position: {e}")
    
    def on_closing(self):
        """Handle tools window closing."""
        # Cancel any pending save timer
        if hasattr(self, '_save_timer'):
            try:
                self.window.after_cancel(self._save_timer)
            except:
                pass
        self.save_position()
        self.window.destroy()


__all__ = ['ToolsWindow']





