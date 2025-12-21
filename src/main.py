"""
Main entry point for RVU Counter application.

This module initializes logging and starts the GUI application.
"""

import tkinter as tk
import sys
import os
import threading

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
from .core.yaml_update_manager import YamlUpdateManager


def check_yaml_updates_background():
    """Check for yaml updates in background thread (silent, non-blocking)."""
    try:
        yaml_updater = YamlUpdateManager()
        was_updated = yaml_updater.update_if_needed()
        if was_updated:
            logger.info("RVU rules automatically updated from GitHub")
    except Exception as e:
        logger.error(f"Error checking yaml updates: {e}")


def main():
    """Main entry point for the application."""
    try:
        # Start background yaml update check (silent, doesn't block startup)
        threading.Thread(target=check_yaml_updates_background, daemon=True).start()
        
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
