"""
Main entry point for RVU Counter application.

This module initializes logging and starts the GUI application.
"""

import tkinter as tk
import sys
import os

# Setup logging first
from .core.logging_config import setup_logging

# Get the root directory for logging
if getattr(sys, 'frozen', False):
    # Running as executable
    log_dir = os.path.dirname(sys.executable)
else:
    # Running as script - go up one level from src/
    log_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Initialize logging
logger = setup_logging(log_dir)
logger.info("=" * 60)
logger.info("RVU Counter Starting")
logger.info("=" * 60)

# Import the main application
from .ui import RVUCounterApp


def main():
    """Main entry point for the application."""
    try:
        root = tk.Tk()
        app = RVUCounterApp(root)
        root.protocol("WM_DELETE_WINDOW", app.on_closing)
        logger.info("Application initialized successfully")
        root.mainloop()
    except Exception as e:
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
