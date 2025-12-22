"""Window extraction utilities for pywinauto operations."""

import logging
import threading
from typing import Dict, List, Optional, Any

try:
    from pywinauto import Desktop
except ImportError:
    Desktop = None

logger = logging.getLogger(__name__)

# Module-level globals
_cached_desktop: Optional[Any] = None
_timeout_thread_count = 0
_timeout_thread_lock = threading.Lock()


def _window_text_with_timeout(element, timeout=1.0, element_name=""):
    """Read window_text() with a timeout to prevent blocking.
    
    When PowerScribe transitions between studies, window_text() can block for
    extended periods (10-18 seconds). This wrapper prevents the worker thread
    from freezing by timing out after the specified duration.
    
    Note: When timeout occurs, the spawned thread becomes orphaned (blocking on 
    the UI call). These are daemon threads so they won't prevent app exit, but 
    they consume resources until the blocking call eventually returns or the 
    app exits. We track the count for monitoring purposes.
    
    Args:
        element: The UI element to read text from
        timeout: Maximum time to wait in seconds (default 1.0)
        element_name: Name/ID of element for logging (optional)
    
    Returns:
        str: The window text, or empty string if timeout/failure occurs
    """
    global _timeout_thread_count
    import time
    result = [None]
    exception = [None]
    start = time.time()
    
    def read_text():
        global _timeout_thread_count
        try:
            result[0] = element.window_text()
        except Exception as e:
            exception[0] = e
        finally:
            # If we were an orphan thread that finally completed, decrement count
            with _timeout_thread_lock:
                if _timeout_thread_count > 0:
                    _timeout_thread_count -= 1
    
    thread = threading.Thread(target=read_text, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    elapsed = time.time() - start
    
    if thread.is_alive():
        # Thread is still running - window_text() is blocking
        # Track orphan thread count for monitoring
        with _timeout_thread_lock:
            _timeout_thread_count += 1
            orphan_count = _timeout_thread_count
        logger.warning(f"window_text() call timed out after {timeout}s for {element_name} (orphan threads: {orphan_count})")
        return ""
    
    if exception[0]:
        logger.debug(f"window_text() exception for {element_name}: {exception[0]}")
        raise exception[0]
    
    return result[0] if result[0] else ""


def get_cached_desktop():
    """Get or create cached Desktop object."""
    global _cached_desktop
    
    if Desktop is None:
        return None
    
    if _cached_desktop is None:
        _cached_desktop = Desktop(backend="uia")
    
    return _cached_desktop


def find_elements_by_automation_id(window, automation_ids: List[str], cached_elements: Dict = None) -> Dict[str, Any]:
    """Find elements by Automation ID - optimized for speed.
    
    Uses cached elements when available (instant).
    Falls back to descendants search if direct lookup fails.
    Uses SHORT timeouts (0.3s) to detect study closure quickly.
    """
    found_elements = {}
    ids_needing_search = []
    
    for auto_id in automation_ids:
        # Try cache first (instant)
        if cached_elements and auto_id in cached_elements:
            try:
                cached_elem = cached_elements[auto_id]['element']
                # SHORT timeout (0.3s) - if element is stale, fail fast
                text_content = _window_text_with_timeout(cached_elem, timeout=0.3, element_name=auto_id)
                found_elements[auto_id] = {
                    'element': cached_elem,
                    'text': text_content.strip() if text_content else '',
                }
                continue  # Got it from cache, next element
            except:
                pass  # Cache invalid, need to search
        
        ids_needing_search.append(auto_id)
    
    # If we need to search for any elements, do a single descendants() call
    if ids_needing_search:
        try:
            remaining = set(ids_needing_search)
            # Limit iteration to prevent blocking
            descendants_list = []
            try:
                descendants_gen = window.descendants()
                count = 0
                for elem in descendants_gen:
                    descendants_list.append(elem)
                    count += 1
                    if count >= 1000:  # Limit to prevent excessive blocking
                        break
            except Exception as e:
                logger.debug(f"window.descendants() iteration failed: {e}")
                descendants_list = []
            
            for element in descendants_list:
                if not remaining:
                    break
                try:
                    elem_auto_id = element.element_info.automation_id
                    if elem_auto_id and elem_auto_id in remaining:
                        # SHORT timeout (0.3s) - fail fast on stale elements
                        text_content = _window_text_with_timeout(element, timeout=0.3, element_name=elem_auto_id)
                        found_elements[elem_auto_id] = {
                            'element': element,
                            'text': text_content.strip() if text_content else '',
                        }
                        remaining.remove(elem_auto_id)
                except:
                    pass
        except:
            pass
        
    return found_elements













