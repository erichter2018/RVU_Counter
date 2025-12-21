"""Platform-specific utilities for Windows (monitor detection, app paths)."""

import os
import sys
import logging
from typing import Tuple, List

logger = logging.getLogger(__name__)


# =============================================================================
# Multi-Monitor Support (Windows Native)
# =============================================================================

def get_all_monitor_bounds() -> Tuple[int, int, int, int, List[Tuple[int, int, int, int]]]:
    """Get virtual screen bounds encompassing all monitors using Windows API.
    
    Returns:
        (virtual_left, virtual_top, virtual_right, virtual_bottom, list_of_monitor_rects)
        
    Uses ctypes to call Windows EnumDisplayMonitors for accurate multi-monitor detection.
    This handles:
    - Monitors with negative coordinates (left/above primary)
    - Different resolutions per monitor
    - Non-standard monitor arrangements (vertical stacking, etc.)
    - DPI scaling
    """
    try:
        import ctypes
        from ctypes import wintypes
        
        # Windows API constants
        user32 = ctypes.windll.user32
        
        monitors = []
        
        # Callback function for EnumDisplayMonitors
        MONITORENUMPROC = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,  # hMonitor
            ctypes.c_void_p,  # hdcMonitor
            ctypes.POINTER(wintypes.RECT),  # lprcMonitor
            ctypes.c_void_p   # dwData
        )
        
        def monitor_enum_callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            rect = lprcMonitor.contents
            monitors.append((rect.left, rect.top, rect.right, rect.bottom))
            return True
        
        # Enumerate all monitors
        callback = MONITORENUMPROC(monitor_enum_callback)
        user32.EnumDisplayMonitors(None, None, callback, 0)
        
        if not monitors:
            # Fallback if enumeration fails
            logger.warning("EnumDisplayMonitors returned no monitors, using fallback")
            return (0, 0, 1920, 1080, [(0, 0, 1920, 1080)])
        
        # Calculate virtual screen bounds (bounding box of all monitors)
        virtual_left = min(m[0] for m in monitors)
        virtual_top = min(m[1] for m in monitors)
        virtual_right = max(m[2] for m in monitors)
        virtual_bottom = max(m[3] for m in monitors)
        
        logger.debug(f"Detected {len(monitors)} monitors: {monitors}")
        logger.debug(f"Virtual screen bounds: ({virtual_left}, {virtual_top}) to ({virtual_right}, {virtual_bottom})")
        
        return (virtual_left, virtual_top, virtual_right, virtual_bottom, monitors)
        
    except Exception as e:
        logger.error(f"Error enumerating monitors: {e}")
        # Fallback to reasonable defaults
        return (0, 0, 1920, 1080, [(0, 0, 1920, 1080)])


def get_primary_monitor_bounds() -> Tuple[int, int, int, int]:
    """Get the bounds of the primary monitor using Windows API.
    
    Returns: (left, top, right, bottom) of the primary monitor.
    The primary monitor always contains the origin point (0, 0).
    """
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        
        # MONITOR_DEFAULTTOPRIMARY = 1
        # Get the monitor that contains point (0, 0) which is always on primary
        hMonitor = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
        
        if hMonitor:
            # MONITORINFO structure
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)
                ]
            
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            
            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                rect = mi.rcMonitor
                return (rect.left, rect.top, rect.right, rect.bottom)
        
        # Fallback
        return (0, 0, 1920, 1080)
        
    except Exception as e:
        logger.error(f"Error getting primary monitor: {e}")
        return (0, 0, 1920, 1080)


def is_point_on_any_monitor(x: int, y: int) -> bool:
    """Check if a point is visible on any monitor.
    
    This is more accurate than just checking virtual screen bounds because
    monitors may not form a contiguous rectangle.
    """
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        
        # MONITOR_DEFAULTTONULL = 0 - returns NULL if point not on any monitor
        point = wintypes.POINT(int(x), int(y))
        hMonitor = user32.MonitorFromPoint(point, 0)
        
        return hMonitor is not None and hMonitor != 0
        
    except Exception as e:
        logger.debug(f"Error checking point on monitor: {e}")
        # Fallback: assume point is visible if within virtual bounds
        vl, vt, vr, vb, _ = get_all_monitor_bounds()
        return vl <= x < vr and vt <= y < vb


def find_nearest_monitor_for_window(x: int, y: int, width: int, height: int) -> Tuple[int, int]:
    """Find the best position for a window that may be off-screen.
    
    Returns adjusted (x, y) coordinates that ensure the window is visible.
    Prefers keeping the window on its current monitor if partially visible,
    otherwise moves to the nearest monitor.
    """
    try:
        import ctypes
        from ctypes import wintypes
        
        user32 = ctypes.windll.user32
        
        # Check if the window center is on any monitor
        center_x = x + width // 2
        center_y = y + height // 2
        
        # MONITOR_DEFAULTTONEAREST = 2 - returns nearest monitor if not on any
        point = wintypes.POINT(int(center_x), int(center_y))
        hMonitor = user32.MonitorFromPoint(point, 2)
        
        if hMonitor:
            # Get monitor info
            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),  # Work area (excludes taskbar)
                    ("dwFlags", wintypes.DWORD)
                ]
            
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            
            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
                work = mi.rcWork  # Use work area to avoid taskbar
                mon_left, mon_top = work.left, work.top
                mon_right, mon_bottom = work.right, work.bottom
                mon_width = mon_right - mon_left
                mon_height = mon_bottom - mon_top
                
                # Clamp window to this monitor's work area
                new_x = max(mon_left, min(x, mon_right - width))
                new_y = max(mon_top, min(y, mon_bottom - height))
                
                # If window is larger than monitor, at least show top-left
                if width > mon_width:
                    new_x = mon_left
                if height > mon_height:
                    new_y = mon_top
                
                return (int(new_x), int(new_y))
        
        # Fallback: use primary monitor
        pm = get_primary_monitor_bounds()
        new_x = max(pm[0], min(x, pm[2] - width))
        new_y = max(pm[1], min(y, pm[3] - height))
        return (int(new_x), int(new_y))
        
    except Exception as e:
        logger.error(f"Error finding nearest monitor: {e}")
        return (50, 50)  # Safe fallback


# =============================================================================
# App Paths
# =============================================================================

def get_app_root():
    """Get the root directory of the application, handling portable executable cases."""
    if getattr(sys, 'frozen', False):
        # Running as a compiled .exe
        return os.path.dirname(sys.executable)
    else:
        # Running as a script - assume root is parent of 'src'
        # script_dir is src/core/
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.abspath(os.path.join(script_dir, "..", ".."))

def ensure_directories():
    """Create the required directory structure if it doesn't exist."""
    # Import inside to avoid circular dependency
    from .config import SETTINGS_FOLDER, DATA_FOLDER, HELPERS_FOLDER, LOG_FOLDER
    
    root = get_app_root()
    for folder in [SETTINGS_FOLDER, DATA_FOLDER, HELPERS_FOLDER, LOG_FOLDER]:
        path = os.path.join(root, folder)
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)

def get_app_paths():
    """Get the correct paths for bundled app vs running as script.

    Returns:
        tuple: (settings_dir, data_dir)
        - settings_dir: Where bundled settings file is (read-only in bundle)
        - data_dir: The root directory for data (next to exe or project root)
    """
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        settings_dir = sys._MEIPASS
        data_dir = os.path.dirname(sys.executable)
    else:
        # Running as script
        data_dir = get_app_root()
        settings_dir = data_dir
        
    logger.info(f"App paths: settings={settings_dir}, data={data_dir}")
    return settings_dir, data_dir


__all__ = [
    'get_all_monitor_bounds',
    'get_primary_monitor_bounds',
    'is_point_on_any_monitor',
    'find_nearest_monitor_for_window',
    'get_app_paths',
    'get_app_root',
    'ensure_directories',
]
