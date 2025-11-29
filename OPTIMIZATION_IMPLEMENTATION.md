# Performance Optimization Implementation Guide

This document describes three key performance optimizations implemented in the `more_optimization` branch that should be incorporated into future development.

---

## 1. Adaptive Polling in PowerScribe Worker Thread

### Problem
The PowerScribe worker thread was polling at a fixed interval (500ms), causing unnecessary CPU usage during idle periods when no studies were active.

### Solution
Implemented adaptive polling intervals that adjust based on application activity state:

- **500ms**: When a study is active AND data has recently changed
- **1000ms**: When a study is active but data hasn't changed for 1+ second  
- **2000ms**: When no study is active (idle state)

### Implementation Location
`RVUCounter.py` - `_powerscribe_worker()` method

### Key Code Pattern
```python
# Check if data changed (for adaptive polling)
current_accession_check = data.get('accession', '').strip()
data_changed = (current_accession_check != self._last_accession_seen) or \
              (not self._last_accession_seen and current_accession_check)

if data_changed:
    # Data changed - use fast polling (500ms)
    self._last_accession_seen = current_accession_check
    self._last_data_change_time = time.time()
    self._current_poll_interval = 0.5
else:
    # Check how long since last change
    time_since_change = time.time() - self._last_data_change_time
    if current_accession_check:
        # Active study but no change - moderate polling (1000ms)
        if time_since_change > 1.0:
            self._current_poll_interval = 1.0
        else:
            self._current_poll_interval = 0.5
    else:
        # No active study - slow polling (2000ms)
        self._current_poll_interval = 2.0

# Use adaptive interval
time.sleep(self._current_poll_interval)
```

### Variables Required
- `self._last_accession_seen`: Track last seen accession for change detection
- `self._last_data_change_time`: Timestamp of last data change
- `self._current_poll_interval`: Current polling interval (initialized to 1.0)

---

## 2. Increased Main Refresh Interval

### Problem
The main UI refresh loop was running every 500ms, causing frequent recalculations and UI updates even when data hadn't changed.

### Solution
Increased the main refresh interval from 500ms to 1000ms, reducing UI thread load by 50%.

### Implementation Location
`RVUCounter.py` - `RVUCounterApp.__init__()` and `setup_refresh()` method

### Key Code Pattern
```python
# In __init__
self.refresh_interval = 1000  # Changed from 500

# In setup_refresh()
self.root.after(1000, self.setup_refresh)  # Changed from self.refresh_interval
```

### Notes
- The refresh loop still responds quickly enough for user interaction
- Statistics and display updates occur once per second instead of twice
- Reduces CPU usage on main UI thread

---

## 3. Blocking Call Protection with Timeouts

### Problem
When PowerScribe transitions between studies, certain `pywinauto` operations (particularly `window_text()`, `descendants()`, `children()`) can block for extended periods (10-18 seconds), freezing the worker thread and preventing timely study detection.

### Solution
Wrapped all potentially blocking operations with timeout mechanisms using threading, allowing the worker thread to continue even if individual operations hang.

### Implementation Location
`RVUCounter.py` - Multiple locations:

1. **`_window_text_with_timeout()` function** (new helper function)
2. **`find_elements_by_automation_id()`** - Uses timeout wrapper
3. **`find_powerscribe_window()`** - Timeout on `window_text()` calls
4. **`find_mosaic_window()`** - Timeout on `window_text()` calls
5. **Worker thread** - Timeout on `descendants()` and `listbox.children()` iterations

### Key Code Pattern

#### Timeout Wrapper Function
```python
def _window_text_with_timeout(element, timeout=1.0, element_name=""):
    """Read window_text() with a timeout to prevent blocking."""
    import threading
    import time
    result = [None]
    exception = [None]
    start = time.time()
    
    def read_text():
        try:
            result[0] = element.window_text()
        except Exception as e:
            exception[0] = e
    
    thread = threading.Thread(target=read_text, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    elapsed = time.time() - start
    
    if thread.is_alive():
        # Thread is still running - window_text() is blocking
        logger.warning(f"window_text() call timed out after {timeout}s for {element_name}")
        return ""
    
    if exception[0]:
        raise exception[0]
    
    return result[0] if result[0] else ""
```

#### Usage in find_elements_by_automation_id
```python
# For cached elements
text_content = _window_text_with_timeout(cached_elem, timeout=1.0, element_name=auto_id)

# During descendants search
text_content = _window_text_with_timeout(element, timeout=1.0, element_name=elem_auto_id)
```

#### Limited Iteration for Blocking Generators
```python
# For window.descendants() - convert generator to list with limit
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
```

```python
# For listbox.children() - convert generator to list with limit
listbox_children = []
try:
    children_gen = listbox.children()
    count = 0
    for child_elem in children_gen:
        listbox_children.append(child_elem)
        count += 1
        if count >= 50:  # Limit to prevent blocking
            break
except Exception as e:
    logger.debug(f"listbox.children() iteration failed: {e}")
    listbox_children = []
```

### Timeout Values Used
- `window_text()`: 1.0 seconds (0.5-0.8 seconds in some cases)
- `find_window()` operations: 2.0 seconds
- Generator iteration limits: 1000 for `descendants()`, 50 for `listbox.children()`

### Error Handling
- All timeout wrappers return empty strings or None on timeout/failure
- Worker thread continues processing even if individual operations fail
- Logging at warning/debug levels to identify blocking issues

### Benefits
- Prevents worker thread from blocking for extended periods
- Maintains responsive study detection during PowerScribe transitions
- Graceful degradation when UI automation operations hang

---

## Implementation Checklist

When incorporating these changes into a new branch:

### Adaptive Polling
- [ ] Initialize `_last_accession_seen`, `_last_data_change_time`, `_current_poll_interval` in `__init__`
- [ ] Add change detection logic after reading PowerScribe data
- [ ] Update `_current_poll_interval` based on activity state
- [ ] Use `time.sleep(self._current_poll_interval)` instead of fixed interval

### Main Refresh Interval
- [ ] Change `self.refresh_interval = 1000` in `__init__`
- [ ] Update `setup_refresh()` to use `1000` hardcoded or `self.refresh_interval`

### Blocking Call Protection
- [ ] Add `_window_text_with_timeout()` helper function
- [ ] Replace all `element.window_text()` calls with `_window_text_with_timeout()`
- [ ] Wrap `window.descendants()` iteration with limit (1000 elements max)
- [ ] Wrap `listbox.children()` iteration with limit (50 children max)
- [ ] Add timeout protection to `find_powerscribe_window()` and `find_mosaic_window()`
- [ ] Add try/except blocks around all generator conversions

---

## Testing Recommendations

1. **Adaptive Polling**: Monitor CPU usage during idle periods - should be significantly lower
2. **Main Refresh**: Verify UI remains responsive with 1-second updates
3. **Blocking Protection**: Test during PowerScribe study transitions - should not freeze for >1 second

---

## Notes

- These optimizations are independent and can be implemented separately
- All changes maintain backward compatibility with existing functionality
- The timeout values may need adjustment based on actual PowerScribe performance characteristics


