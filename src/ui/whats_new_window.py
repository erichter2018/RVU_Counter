"""What's New window - displays release notes for the current version."""

import tkinter as tk
from tkinter import ttk, scrolledtext
import os
import logging

from ..core.config import APP_VERSION
from ..core.platform_utils import get_app_root

logger = logging.getLogger(__name__)


class WhatsNewWindow:
    """Window for displaying What's New / release notes."""
    
    def __init__(self, parent):
        # Create window as independent Toplevel (not transient)
        self.window = tk.Toplevel()
        self.window.title(f"What's New - RVU Counter {APP_VERSION}")
        self.window.geometry("700x600")
        
        # Ensure window can be closed independently
        self.window.protocol("WM_DELETE_WINDOW", self.close_window)
        
        self.create_ui()
        self.load_content()
        
        # Lift to front and grab focus to override parent's grab_set()
        # This makes What's New modal over Settings (if Settings is open)
        self.window.lift()
        self.window.grab_set()  # Override parent's grab to make this window interactive
        self.window.focus_force()
        
    def close_window(self):
        """Close the window properly."""
        try:
            # Release grab before destroying to avoid leaving parent in bad state
            self.window.grab_release()
            self.window.destroy()
        except:
            pass
        
    def create_ui(self):
        """Create the UI."""
        # Header
        header_frame = ttk.Frame(self.window)
        header_frame.pack(fill=tk.X, padx=10, pady=10)
        
        title = ttk.Label(header_frame, text=f"What's New in RVU Counter {APP_VERSION}", 
                         font=("Arial", 14, "bold"))
        title.pack()
        
        # Text area
        text_frame = ttk.Frame(self.window)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        self.text = scrolledtext.ScrolledText(text_frame, wrap=tk.WORD, font=("Consolas", 10), state=tk.NORMAL)
        self.text.pack(fill=tk.BOTH, expand=True)
        
        # Close button
        btn_frame = ttk.Frame(self.window)
        btn_frame.pack(pady=(0, 10))
        
        ttk.Button(btn_frame, text="Close", command=self.close_window, width=10).pack()
        
    def load_content(self):
        """Load and display the What's New content."""
        try:
            import sys
            
            # For PyInstaller, bundled files are in sys._MEIPASS
            if getattr(sys, 'frozen', False):
                # Running as compiled executable
                bundle_dir = getattr(sys, '_MEIPASS', get_app_root())
                whats_new_path = os.path.join(bundle_dir, "documentation", "WHATS_NEW_v1.7.md")
            else:
                # Running as script
                app_root = get_app_root()
                whats_new_path = os.path.join(app_root, "documentation", "WHATS_NEW_v1.7.md")
            
            if os.path.exists(whats_new_path):
                with open(whats_new_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                # Simple markdown-to-text conversion
                content = self._format_markdown(content)
                self.text.insert(1.0, content)
            else:
                self.text.insert(1.0, "What's New content not found.\n\n"
                               "This file should be located at:\n"
                               f"{whats_new_path}\n\n"
                               "Please check the documentation folder.")
                
        except Exception as e:
            logger.error(f"Error loading What's New content: {e}")
            self.text.insert(1.0, f"Error loading content: {e}")
        
        finally:
            # Make text read-only after loading content
            self.text.config(state=tk.DISABLED)
            
    def _format_markdown(self, md_text: str) -> str:
        """Basic markdown formatting for text display."""
        lines = md_text.split('\n')
        formatted = []
        
        for line in lines:
            # Headers
            if line.startswith('# '):
                formatted.append('\n' + line[2:].upper() + '\n' + '='*50)
            elif line.startswith('## '):
                formatted.append('\n' + line[3:].upper() + '\n' + '-'*40)
            elif line.startswith('### '):
                formatted.append('\n' + line[4:])
            # Lists
            elif line.startswith('- '):
                formatted.append('  • ' + line[2:])
            elif line.startswith('* '):
                formatted.append('  • ' + line[2:])
            # Checkmarks
            elif line.strip().startswith('✅'):
                formatted.append('  ' + line.strip())
            # Regular lines
            else:
                formatted.append(line)
                
        return '\n'.join(formatted)


__all__ = ['WhatsNewWindow']





