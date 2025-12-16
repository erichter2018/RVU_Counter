"""Configuration constants and feature flags for RVU Counter."""

import os
import sys

# Version
APP_VERSION = "1.5.1"
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
LOG_FILE_NAME = "rvu_counter.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_CHECK_INTERVAL = 100  # Check log size every N writes
LOG_TRIM_TARGET_RATIO = 0.9  # Keep 90% of max size when trimming

# File names
SETTINGS_FILE_NAME = "rvu_settings.yaml"
DATABASE_FILE_NAME = "rvu_records.db"
RECORDS_JSON_FILE_NAME = "rvu_records.json"  # Legacy
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

# Backup configuration
GITHUB_BACKUP_REPO = "rvu-counter-backups"
BACKUP_BRANCH = "main"

__all__ = [
    'APP_VERSION',
    'APP_NAME',
    'HAS_TKCALENDAR',
    'HAS_MATPLOTLIB',
    'LOG_FILE_NAME',
    'LOG_MAX_BYTES',
    'LOG_CHECK_INTERVAL',
    'LOG_TRIM_TARGET_RATIO',
    'SETTINGS_FILE_NAME',
    'DATABASE_FILE_NAME',
    'RECORDS_JSON_FILE_NAME',
    'OLD_DATA_FILE_NAME',
    'DEFAULT_WINDOW_SIZES',
    'DEFAULT_SHIFT_LENGTH_HOURS',
    'DEFAULT_MIN_STUDY_SECONDS',
    'GITHUB_BACKUP_REPO',
    'BACKUP_BRANCH',
]
