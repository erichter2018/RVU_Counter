"""Custom canvas-based sortable table widget for RVU Counter."""

import tkinter as tk
from tkinter import ttk

class CanvasTable:
    """Reusable Canvas-based sortable table widget."""
    
    def _get_theme_colors(self, widget):
        """Get theme colors by traversing widget hierarchy to find app instance."""
        current = widget
        for _ in range(10):  # Limit traversal depth
            if hasattr(current, 'app') and hasattr(current.app, 'theme_colors'):
                return current.app.theme_colors
            if hasattr(current, 'parent'):
                current = current.parent
            elif hasattr(current, 'master'):
                current = current.master
            else:
                break
        # Default fallback colors
        return {
            "canvas_bg": "#f0f0f0",
            "button_bg": "#e1e1e1",
            "entry_bg": "white",
            "fg": "black",
            "border_color": "#acacac"
        }
    
    def __init__(self, parent, columns, sortable_columns=None, row_height=25, header_height=30, app=None):
        """
        Create a Canvas-based sortable table.
        
        Args:
            parent: Parent widget
            columns: List of (name, width, header_text) tuples or dict with 'name', 'width', 'text', 'sortable'
            sortable_columns: Set of column names that are sortable (None = all sortable)
            row_height: Height of each data row
            header_height: Height of header row
            app: Optional app instance for theme colors (if None, will try to find it)
        """
        self.parent = parent
        self.row_height = row_height
        self.header_height = header_height
        self.app = app  # Store app reference for theme colors
        
        # Parse columns
        self.columns = []
        self.column_widths = {}
        self.column_names = []
        self.sortable = sortable_columns if sortable_columns is not None else set()
        
        for col in columns:
            if isinstance(col, dict):
                name = col['name']
                width = col['width']
                text = col.get('text', name)
                sortable = col.get('sortable', True)
            else:
                name, width, text = col
                sortable = True
            
            self.columns.append({'name': name, 'width': width, 'text': text, 'sortable': sortable})
            self.column_widths[name] = width
            self.column_names.append(name)
            if sortable:
                self.sortable.add(name)
        
        # Table dimensions
        self.table_width = sum(self.column_widths.values())
        
        # Data storage
        self.rows_data = []  # List of row dicts: {'cells': {col: value}, 'is_total': bool, 'tags': [], 'cell_text_colors': {}}
        self.sort_column = None
        self.sort_reverse = False
        
        # Get theme colors - use app if provided, otherwise try to find it
        if self.app and hasattr(self.app, 'theme_colors'):
            theme_colors = self.app.theme_colors
        else:
            theme_colors = self._get_theme_colors(parent)
        canvas_bg = theme_colors.get("canvas_bg", "#f0f0f0")
        header_bg = theme_colors.get("button_bg", "#e1e1e1")
        data_bg = theme_colors.get("entry_bg", "white")
        text_fg = theme_colors.get("fg", "black")
        border_color = theme_colors.get("border_color", "#cccccc")  # Light grey for canvas borders
        
        # Store theme colors for use in drawing
        self.theme_colors = theme_colors
        
        # Create frame with scrollbar
        self.frame = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, bg=canvas_bg, highlightthickness=1, highlightbackground=border_color)
        self.scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        
        # Inner frame for content
        self.inner_frame = ttk.Frame(self.canvas)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")
        
        # Configure scrolling
        def configure_scroll_region(event):
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        
        def configure_canvas_width(event):
            canvas_width = event.width
            self.canvas.itemconfig(self.canvas_window, width=canvas_width)
        
        self.inner_frame.bind("<Configure>", configure_scroll_region)
        self.canvas.bind("<Configure>", configure_canvas_width)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # Create header canvas
        self.header_canvas = tk.Canvas(self.inner_frame, width=self.table_width, height=header_height,
                                      bg=header_bg, highlightthickness=0)
        self.header_canvas.pack(fill=tk.X)
        
        # Create data canvas
        self.data_canvas = tk.Canvas(self.inner_frame, width=self.table_width,
                                    bg=data_bg, highlightthickness=0)
        self.data_canvas.pack(fill=tk.BOTH, expand=True)
        
        # Bind mouse wheel scrolling
        def on_mousewheel(event):
            # Windows/Linux: event.delta is in multiples of 120
            # Mac: event.delta is in pixels
            if event.delta:
                delta = -1 * (event.delta / 120) if abs(event.delta) > 1 else -1 * event.delta
            else:
                delta = -1 if event.num == 4 else 1
            self.canvas.yview_scroll(int(delta), "units")
        
        # Bind mouse wheel scrolling to the frame (not individual canvases)
        # This ensures scrolling works even when mouse is over any part of the table
        def bind_mousewheel_to_canvas(widget):
            widget.bind("<MouseWheel>", on_mousewheel)
            widget.bind("<Button-4>", on_mousewheel)  # Linux scroll up
            widget.bind("<Button-5>", on_mousewheel)  # Linux scroll down
        
        # Bind to all components for comprehensive scrolling
        bind_mousewheel_to_canvas(self.frame)
        bind_mousewheel_to_canvas(self.canvas)
        bind_mousewheel_to_canvas(self.inner_frame)
        bind_mousewheel_to_canvas(self.header_canvas)
        bind_mousewheel_to_canvas(self.data_canvas)
        
        # Draw headers
        self._draw_headers()
        
        # Pack widgets
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    def _draw_headers(self):
        """Draw header row with clickable buttons."""
        self.header_canvas.delete("all")
        x = 0
        
        for col_info in self.columns:
            col_name = col_info['name']
            width = col_info['width']
            text = col_info['text']
            sortable = col_info.get('sortable', True)
            
            # Get theme colors
            header_bg = self.theme_colors.get("button_bg", "#e1e1e1")
            header_fg = self.theme_colors.get("fg", "black")
            border_color = self.theme_colors.get("border_color", "#acacac")
            
            # Draw header rectangle
            rect_id = self.header_canvas.create_rectangle(x, 0, x + width, self.header_height,
                                                         fill=header_bg, outline=border_color, width=1,
                                                         tags=f"header_{col_name}")
            
            # Add sort indicator if sorted
            display_text = text
            if col_name == self.sort_column and col_name in self.sortable:
                indicator = " ▼" if self.sort_reverse else " ▲"
                display_text = text + indicator
            
            # Draw text - left-align text columns, center numeric columns
            if col_name == 'body_part' or col_name == 'study_type' or col_name == 'procedure' or col_name == 'metric' or col_name == 'modality' or col_name == 'patient_class' or col_name == 'category':
                text_anchor = 'w'
                text_x = x + 4  # Small left padding
            else:
                text_anchor = 'center'
                text_x = x + width//2
            self.header_canvas.create_text(text_x, self.header_height//2,
                                         text=display_text, font=('Arial', 9, 'bold'),
                                         anchor=text_anchor, fill=header_fg, tags=f"header_{col_name}")
            
            # Make clickable if sortable
            if sortable and col_name in self.sortable:
                self.header_canvas.tag_bind(f"header_{col_name}", "<Button-1>",
                                          lambda e, c=col_name: self._on_header_click(c))
                self.header_canvas.tag_bind(f"header_{col_name}", "<Enter>",
                                          lambda e: self.header_canvas.config(cursor="hand2"))
                self.header_canvas.tag_bind(f"header_{col_name}", "<Leave>",
                                          lambda e: self.header_canvas.config(cursor=""))
            
            x += width
    
    def _on_header_click(self, col_name):
        """Handle header click for sorting."""
        if col_name not in self.sortable:
            return  # Column is not sortable
        
        if self.sort_column == col_name:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = col_name
            self.sort_reverse = False
        
        # Redraw headers to show sort indicator
        self._draw_headers()
        # Redraw data with new sort order
        self._draw_data()
    
    def _draw_data(self):
        """Draw data rows."""
        self.data_canvas.delete("all")
        
        # Sort rows if needed
        rows_to_draw = list(self.rows_data)
        if self.sort_column and self.sort_column in self.sortable:
            # Separate totals from regular rows
            regular_rows = [r for r in rows_to_draw if not r.get('is_total', False)]
            total_rows = [r for r in rows_to_draw if r.get('is_total', False)]
            
            # Sort regular rows
            def get_sort_value(row):
                val = row['cells'].get(self.sort_column, "")
                # Try numeric sort first
                try:
                    if isinstance(val, str):
                        # Remove parentheses content for duration strings
                        val_clean = re.sub(r'\s*\(\d+\)$', '', val).strip()
                        if val_clean and val_clean != "-":
                            # Try parsing as duration (Xh Ym Zs)
                            total_seconds = 0
                            hours = re.search(r'(\d+)h', val_clean)
                            minutes = re.search(r'(\d+)m', val_clean)
                            seconds = re.search(r'(\d+)s', val_clean)
                            if hours:
                                total_seconds += int(hours.group(1)) * 3600
                            if minutes:
                                total_seconds += int(minutes.group(1)) * 60
                            if seconds:
                                total_seconds += int(seconds.group(1))
                            return total_seconds if total_seconds > 0 else float('inf')
                    return float(val)
                except:
                    pass
                return str(val).lower()
            
            regular_rows.sort(key=get_sort_value, reverse=self.sort_reverse)
            rows_to_draw = regular_rows + total_rows
        else:
            # Keep totals at bottom
            regular_rows = [r for r in rows_to_draw if not r.get('is_total', False)]
            total_rows = [r for r in rows_to_draw if r.get('is_total', False)]
            rows_to_draw = regular_rows + total_rows
        
        # Get theme colors once (cache for performance)
        data_bg = self.theme_colors.get("entry_bg", "white")
        data_fg = self.theme_colors.get("fg", "black")
        border_color = self.theme_colors.get("border_color", "#acacac")
        total_bg = self.theme_colors.get("button_bg", "#e1e1e1")
        
            # Draw rows - draw all rows (for now, optimization can be added later if needed)
        y = 0
        for row in rows_to_draw:
            cells = row['cells']
            is_total = row.get('is_total', False)
            cell_colors = row.get('cell_colors', {})  # Optional per-cell background colors
            cell_text_colors = row.get('cell_text_colors', {})  # Optional per-cell text colors
            
            x = 0
            for col_info in self.columns:
                col_name = col_info['name']
                width = col_info['width']
                value = cells.get(col_name, "")
                
                # Get cell color (for color coding) - use theme colors if not specified
                if col_name not in cell_colors:
                    cell_color = total_bg if is_total else data_bg
                else:
                    cell_color = cell_colors.get(col_name)
                
                # Get text color - use cell_text_colors if specified, otherwise use theme default
                text_color = cell_text_colors.get(col_name, data_fg)
                
                # Draw cell
                self.data_canvas.create_rectangle(x, y, x + width, y + self.row_height,
                                                 fill=cell_color, outline=border_color, width=1)
                
                # Draw text - support partial coloring for dollar amounts
                font = ('Arial', 9, 'bold') if is_total else ('Arial', 9)
                value_str = str(value)
                
                # Check if we need partial coloring (when text_color is specified and value contains $)
                if col_name in cell_text_colors and '$' in value_str:
                    # Parse out the dollar amount and render separately
                    import re
                    # Find dollar amount pattern ($number with optional commas)
                    dollar_match = re.search(r'(\$\d[\d,]*\.?\d*)', value_str)
                    if dollar_match:
                        dollar_amount = dollar_match.group(1)
                        dollar_start = dollar_match.start()
                        dollar_end = dollar_match.end()
                        
                        # Split text into parts
                        before_dollar = value_str[:dollar_start]
                        after_dollar = value_str[dollar_end:]
                        
                        # Get text metrics for positioning
                        test_text = self.data_canvas.create_text(0, 0, text=before_dollar, font=font, anchor='w')
                        before_bbox = self.data_canvas.bbox(test_text)
                        before_width = before_bbox[2] - before_bbox[0] if before_bbox else 0
                        self.data_canvas.delete(test_text)
                        
                        test_text = self.data_canvas.create_text(0, 0, text=dollar_amount, font=font, anchor='w')
                        dollar_bbox = self.data_canvas.bbox(test_text)
                        dollar_width = dollar_bbox[2] - dollar_bbox[0] if dollar_bbox else 0
                        self.data_canvas.delete(test_text)
                        
                        # Calculate starting x position (center alignment)
                        total_width = before_width + dollar_width
                        if after_dollar:
                            test_text = self.data_canvas.create_text(0, 0, text=after_dollar, font=font, anchor='w')
                            after_bbox = self.data_canvas.bbox(test_text)
                            after_width = after_bbox[2] - after_bbox[0] if after_bbox else 0
                            self.data_canvas.delete(test_text)
                            total_width += after_width
                        
                        start_x = x + (width - total_width) // 2
                        text_y = y + self.row_height // 2
                        
                        # Draw text parts
                        if before_dollar:
                            self.data_canvas.create_text(start_x, text_y, text=before_dollar, font=font, anchor='w', fill=data_fg)
                            start_x += before_width
                        
                        self.data_canvas.create_text(start_x, text_y, text=dollar_amount, font=font, anchor='w', fill=text_color)
                        start_x += dollar_width
                        
                        if after_dollar:
                            self.data_canvas.create_text(start_x, text_y, text=after_dollar, font=font, anchor='w', fill=data_fg)
                    else:
                        # No dollar match, render normally
                        # Left-align first column (typically names/categories), center others
                        if col_name == 'body_part' or col_name == 'study_type' or col_name == 'procedure' or col_name == 'metric' or col_name == 'modality' or col_name == 'patient_class' or col_name == 'category':
                            anchor = 'w'
                            text_x = x + 4
                        else:
                            anchor = 'center'
                            text_x = x + width//2
                        self.data_canvas.create_text(text_x, y + self.row_height//2,
                                                   text=value_str, font=font, anchor=anchor, fill=text_color)
                else:
                    # Normal rendering - entire text in one color
                    # Left-align first column (typically names/categories), center others
                    if col_name == 'body_part' or col_name == 'study_type' or col_name == 'procedure' or col_name == 'metric' or col_name == 'modality' or col_name == 'patient_class' or col_name == 'category':
                        anchor = 'w'
                        text_x = x + 4  # Small left padding
                    else:
                        anchor = 'center'
                        text_x = x + width//2
                    self.data_canvas.create_text(text_x, y + self.row_height//2,
                                               text=value_str, font=font, anchor=anchor, fill=text_color)
                x += width
            
            y += self.row_height
        
        # Set canvas height to accommodate all rows
        self.data_canvas.config(height=y)
    
    def add_row(self, cells, is_total=False, cell_colors=None, cell_text_colors=None):
        """Add a row of data (doesn't redraw - call update_data() or _draw_data() when done adding all rows)."""
        self.rows_data.append({
            'cells': cells,
            'is_total': is_total,
            'cell_colors': cell_colors or {},
            'cell_text_colors': cell_text_colors or {}
        })
    
    def update_data(self):
        """Update the display after adding rows - this triggers a single redraw."""
        self._draw_data()
    
    def clear(self):
        """Clear all rows but keep headers visible."""
        self.rows_data = []
        self.sort_column = None
        self.sort_reverse = False
        # Clear only data canvas, keep headers
        self.data_canvas.delete("all")
        # Redraw headers to ensure they're visible
        self._draw_headers()
    
    def update_theme(self):
        """Update theme colors and redraw."""
        # Get fresh theme colors
        if self.app and hasattr(self.app, 'theme_colors'):
            self.theme_colors = self.app.theme_colors
        else:
            self.theme_colors = self._get_theme_colors(self.parent)
        
        # Update canvas backgrounds
        canvas_bg = self.theme_colors.get("canvas_bg", "#f0f0f0")
        header_bg = self.theme_colors.get("button_bg", "#e1e1e1")
        data_bg = self.theme_colors.get("entry_bg", "white")
        border_color = self.theme_colors.get("border_color", "#acacac")
        
        self.canvas.config(bg=canvas_bg, highlightbackground=border_color)
        self.header_canvas.config(bg=header_bg)
        self.data_canvas.config(bg=data_bg)
        
        # Redraw with new colors
        self._draw_headers()
        self._draw_data()
    
    def update_theme(self):
        """Update theme colors and redraw."""
        # Get fresh theme colors
        if self.app and hasattr(self.app, 'theme_colors'):
            self.theme_colors = self.app.theme_colors
        else:
            self.theme_colors = self._get_theme_colors(self.parent)
        
        # Update canvas backgrounds
        canvas_bg = self.theme_colors.get("canvas_bg", "#f0f0f0")
        header_bg = self.theme_colors.get("button_bg", "#e1e1e1")
        data_bg = self.theme_colors.get("entry_bg", "white")
        border_color = self.theme_colors.get("border_color", "#acacac")
        
        self.canvas.config(bg=canvas_bg, highlightbackground=border_color)
        self.header_canvas.config(bg=header_bg)
        self.data_canvas.config(bg=data_bg)
        
        # Redraw with new colors
        self._draw_headers()
        self._draw_data()
    
    def pack(self, **kwargs):
        """Pack the table frame."""
        self.frame.pack(**kwargs)
    
    def pack_forget(self):
        """Unpack the table frame."""
        self.frame.pack_forget()




__all__ = ['CanvasTable']
