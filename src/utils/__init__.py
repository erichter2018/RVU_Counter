"""Utility modules for window detection and data extraction."""

from .window_extraction import (
    _window_text_with_timeout,
    find_elements_by_automation_id,
    get_cached_desktop
)
from .powerscribe_extraction import find_powerscribe_window
from .mosaic_extraction import (
    find_mosaic_window,
    find_mosaic_webview_element,
    extract_mosaic_data_v2,
    extract_mosaic_data,
    get_mosaic_elements_via_descendants,
    _is_mosaic_accession_like
)
from .clario_extraction import (
    find_clario_chrome_window,
    find_clario_content_area,
    extract_clario_patient_class
)

__all__ = [
    # Window extraction
    '_window_text_with_timeout',
    'find_elements_by_automation_id',
    'get_cached_desktop',
    # PowerScribe
    'find_powerscribe_window',
    # Mosaic
    'find_mosaic_window',
    'find_mosaic_webview_element',
    'extract_mosaic_data_v2',
    'extract_mosaic_data',
    'get_mosaic_elements_via_descendants',
    '_is_mosaic_accession_like',
    # Clario
    'find_clario_chrome_window',
    'find_clario_content_area',
    'extract_clario_patient_class',
]
