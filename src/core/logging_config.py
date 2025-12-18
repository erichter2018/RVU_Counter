"""Logging configuration with FIFO-style log file management."""

import logging
import os
from .config import LOG_FILE_NAME, LOG_MAX_BYTES, LOG_CHECK_INTERVAL, LOG_TRIM_TARGET_RATIO


class FIFOFileHandler(logging.FileHandler):
    """Custom file handler that maintains a single log file with FIFO-style trimming.
    
    When the log file exceeds max_bytes, it removes the oldest entries (from the top)
    and keeps only the most recent entries that fit within the size limit.
    """
    
    def __init__(self, filename, max_bytes=LOG_MAX_BYTES, encoding='utf-8'):
        super().__init__(filename, mode='a', encoding=encoding)
        self.max_bytes = max_bytes
        self._check_interval = LOG_CHECK_INTERVAL
        self._write_count = 0
    
    def emit(self, record):
        """Write log record and trim file if it exceeds max size."""
        super().emit(record)
        self._write_count += 1
        
        # Check file size periodically (not on every write to avoid performance impact)
        if self._write_count % self._check_interval == 0:
            self._trim_if_needed()
    
    def _trim_if_needed(self):
        """Trim log file to max_bytes by removing oldest entries."""
        try:
            if os.path.exists(self.baseFilename):
                file_size = os.path.getsize(self.baseFilename)
                if file_size > self.max_bytes:
                    # Read all lines
                    with open(self.baseFilename, 'r', encoding=self.encoding) as f:
                        lines = f.readlines()
                    
                    # Calculate target size (keep ~90% of max to avoid constant trimming)
                    target_size = int(self.max_bytes * LOG_TRIM_TARGET_RATIO)
                    
                    # Remove oldest lines from the top until we're under target size
                    trimmed_lines = lines
                    current_size = file_size
                    
                    while current_size > target_size and len(trimmed_lines) > 1:
                        # Remove oldest line (first line)
                        removed_line_size = len(trimmed_lines[0].encode(self.encoding))
                        trimmed_lines = trimmed_lines[1:]
                        current_size -= removed_line_size
                    
                    # Write back the trimmed content
                    with open(self.baseFilename, 'w', encoding=self.encoding) as f:
                        f.writelines(trimmed_lines)
        except Exception:
            # Don't log trimming errors to avoid recursion
            pass


def setup_logging(log_dir=None):
    """Configure logging with FIFO file handler and console output.
    
    Args:
        log_dir: Directory for log file. If None, uses current directory.
    
    Returns:
        Logger instance
    """
    if log_dir is None:
        log_dir = os.path.dirname(os.path.abspath(__file__))
    
    log_file = os.path.join(log_dir, LOG_FILE_NAME)
    
    # Create FIFO file handler (single file, max size)
    file_handler = FIFOFileHandler(
        log_file,
        max_bytes=LOG_MAX_BYTES,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    
    # Configure root logger
    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler]
    )
    
    return logging.getLogger(__name__)


# Create default logger instance
logger = logging.getLogger(__name__)


__all__ = ['FIFOFileHandler', 'setup_logging', 'logger']
