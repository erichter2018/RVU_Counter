"""Configuration constants and feature flags for RVU Counter."""

import os
import sys

# Version
APP_VERSION = "1.8.1"
APP_VERSION_DATE = "12/16/2025"  # Kept for reference, not displayed
APP_NAME = "RVU Counter"

# Optional feature availability
try:
    from tkcalendar import DateEntry, Calendar
    HAS_TKCALENDAR = True
except ImportError as e:
    HAS_TKCALENDAR = False
    print(f"Warning: tkcalendar not available: {e}")

try:
    import matplotlib
    matplotlib.use('TkAgg')  # Use TkAgg backend for Tkinter integration
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    import numpy as np
    HAS_MATPLOTLIB = True
except ImportError as e:
    HAS_MATPLOTLIB = False
    print(f"Warning: matplotlib not available: {e}")

# Logging configuration
LOG_FOLDER = "logs"
LOG_FILE_NAME = os.path.join(LOG_FOLDER, "rvu_counter.log")
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_CHECK_INTERVAL = 100  # Check log size every N writes
LOG_TRIM_TARGET_RATIO = 0.9  # Keep 90% of max size when trimming

# Folder structure
SETTINGS_FOLDER = "settings"
DATA_FOLDER = "data"
HELPERS_FOLDER = "helpers"

# File names
USER_SETTINGS_FILE_NAME = os.path.join(SETTINGS_FOLDER, "user_settings.yaml")
RULES_FILE_NAME = os.path.join(SETTINGS_FOLDER, "rvu_rules.yaml")
DATABASE_FILE_NAME = os.path.join(DATA_FOLDER, "rvu_records.db")
SETTINGS_FILE_NAME = os.path.join(SETTINGS_FOLDER, "rvu_settings.yaml")  # For legacy migration support
RECORDS_JSON_FILE_NAME = "rvu_records.json"  # Legacy (moved logic handled in migration)
OLD_DATA_FILE_NAME = "rvu_data.json"  # For migration

# Default window sizes (for validation)
DEFAULT_WINDOW_SIZES = {
    "main": {"width": 240, "height": 500},
    "settings": {"width": 450, "height": 700},
    "statistics": {"width": 1350, "height": 800}
}

# Shift configuration
DEFAULT_SHIFT_LENGTH_HOURS = 9
DEFAULT_MIN_STUDY_SECONDS = 10

# Update configuration
GITHUB_OWNER = "erichter2018"
GITHUB_REPO = "RVU-Releases"

# Backup configuration
GITHUB_BACKUP_REPO = "rvu-counter-backups"
BACKUP_BRANCH = "main"

__all__ = [
    'APP_VERSION',
    'APP_NAME',
    'HAS_TKCALENDAR',
    'HAS_MATPLOTLIB',
    'LOG_FILE_NAME',
    'LOG_FOLDER',
    'LOG_MAX_BYTES',
    'LOG_CHECK_INTERVAL',
    'LOG_TRIM_TARGET_RATIO',
    'SETTINGS_FOLDER',
    'DATA_FOLDER',
    'HELPERS_FOLDER',
    'USER_SETTINGS_FILE_NAME',
    'SETTINGS_FILE_NAME',
    'RULES_FILE_NAME',
    'DATABASE_FILE_NAME',
    'RECORDS_JSON_FILE_NAME',
    'OLD_DATA_FILE_NAME',
    'DEFAULT_WINDOW_SIZES',
    'DEFAULT_SHIFT_LENGTH_HOURS',
    'DEFAULT_MIN_STUDY_SECONDS',
    'GITHUB_OWNER',
    'GITHUB_REPO',
    'GITHUB_BACKUP_REPO',
    'BACKUP_BRANCH',
]
