# Extraction Modules Migration Summary

## Overview
The missing PowerScribe, Mosaic, and Clario extraction functions have been migrated from `RVUCounterFull.pyw` into the refactored codebase under `src/utils/`.

## Problem
The refactored RVUCounter (`src/ui/main_window.py`) was calling several window detection and data extraction functions that were never migrated from the un-refactored version, causing `NameError` exceptions and preventing Mosaic/PowerScribe study detection.

## Solution
Created four new utility modules in `src/utils/`:

### 1. `window_extraction.py`
**Common window operation utilities used by all extractors**
- `_window_text_with_timeout()` - Safe text extraction with timeout to prevent blocking
- `get_cached_desktop()` - Cached Desktop object for pywinauto
- `find_elements_by_automation_id()` - Fast element lookup with caching

### 2. `powerscribe_extraction.py`
**PowerScribe 360 window detection**
- `find_powerscribe_window()` - Finds PowerScribe window by title

### 3. `mosaic_extraction.py`
**Mosaic Info Hub window detection and data extraction**
- `find_mosaic_window()` - Finds Mosaic Info Hub window
- `find_mosaic_webview_element()` - Finds WebView2 control in Mosaic window
- `get_mosaic_elements()` - Legacy recursive element extraction
- `get_mosaic_elements_via_descendants()` - Primary element extraction method (v2)
- `_is_mosaic_accession_like()` - Validates accession number format
- `extract_mosaic_data_v2()` - Primary extraction method using descendants
- `extract_mosaic_data()` - Legacy fallback extraction method

### 4. `clario_extraction.py`
**Clario worklist patient class extraction**
- `find_clario_chrome_window()` - Finds Chrome window with Clario worklist
- `find_clario_content_area()` - Finds web content area in Chrome
- `_combine_priority_and_class_clario()` - Combines priority/class into patient_class
- `extract_clario_patient_class()` - Extracts patient class from Clario

## Files Modified

### Created
- `src/utils/window_extraction.py` (163 lines)
- `src/utils/powerscribe_extraction.py` (44 lines)  
- `src/utils/mosaic_extraction.py` (751 lines)
- `src/utils/clario_extraction.py` (436 lines)

### Updated
- `src/ui/main_window.py` - Added imports for extraction functions
- `src/utils/__init__.py` - Exported extraction functions

## Functions Migrated

### From RVUCounterFull.pyw (lines ~1153-2343)
Total: **12 functions** migrated

**Window utilities:**
- `_window_text_with_timeout` (57 lines)
- `find_elements_by_automation_id` (64 lines)

**PowerScribe:**
- `find_powerscribe_window` (34 lines)

**Mosaic:**
- `find_mosaic_window` (43 lines)
- `find_mosaic_webview_element` (56 lines)
- `get_mosaic_elements` (56 lines)
- `get_mosaic_elements_via_descendants` (54 lines)
- `_is_mosaic_accession_like` (79 lines)
- `extract_mosaic_data_v2` (197 lines)
- `extract_mosaic_data` (108 lines)

**Clario:**
- `find_clario_chrome_window` (52 lines)
- `find_clario_content_area` (132 lines)
- `_combine_priority_and_class_clario` (58 lines)
- `extract_clario_patient_class` (226 lines)

## Impact
- **Before**: Mosaic and PowerScribe study detection would fail with `NameError` 
- **After**: All window detection and data extraction functions are now available in the refactored codebase
- **Compatibility**: Maintains exact same functionality as `RVUCounterFull.pyw`

## Testing Recommendations
1. Test PowerScribe window detection and study extraction
2. Test Mosaic window detection with both v2 (descendants) and legacy (WebView2) methods
3. Test Clario patient class extraction
4. Verify multi-accession handling for both PowerScribe and Mosaic
5. Monitor for any `NameError` or `AttributeError` exceptions during runtime

## Next Steps
- Run the refactored application (`python src/main.py`) 
- Monitor logs for successful Mosaic/PowerScribe detection
- Compare behavior with `RVUCounterFull.pyw` to ensure feature parity




