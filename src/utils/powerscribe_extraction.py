"""PowerScribe 360 window detection and data extraction."""

import logging
from typing import Optional, Any

from .window_extraction import get_cached_desktop, _window_text_with_timeout

logger = logging.getLogger(__name__)


def find_powerscribe_window() -> Optional[Any]:
    """Find PowerScribe 360 window by title."""
    desktop = get_cached_desktop()
    
    if desktop is None:
        return None
    
    # Try exact title first (fastest)
    try:
        windows = desktop.windows(title="PowerScribe 360 | Reporting", visible_only=True)
        if windows:
            return windows[0]
    except Exception as e:
        logger.debug(f"Error finding PowerScribe window by exact title: {e}")
    
    # Try other common titles including Nuance variations
    for title in ["PowerScribe 360", "PowerScribe 360 - Reporting", "Nuance PowerScribe 360", "Powerscribe 360"]:
        try:
            windows = desktop.windows(title=title, visible_only=True)
            for window in windows:
                try:
                    window_text = _window_text_with_timeout(window, timeout=1.0, element_name="PowerScribe window check")
                    if "RVU Counter" not in window_text:
                        return window
                except Exception as e:
                    logger.debug(f"Error checking window text for '{title}': {e}")
                    continue
        except Exception as e:
            logger.debug(f"Error finding windows with title '{title}': {e}")
            continue
    
    return None











