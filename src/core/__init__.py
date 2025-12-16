"""Core utilities and configuration for RVU Counter."""

from .config import *
from .logging_config import setup_logging, logger
from .platform_utils import get_all_monitor_bounds, get_primary_monitor_bounds, get_app_paths

__all__ = [
    'setup_logging',
    'logger',
    'get_all_monitor_bounds',
    'get_primary_monitor_bounds',
    'get_app_paths',
]
