"""
RVU Counter - Real-Time RVU Tracking for Radiology Practice

This is the main launcher file for the RVU Counter application.
It imports and runs the refactored modular codebase from the src/ directory.

The complete monolithic version is preserved in RVUCounterFull.pyw for reference.
"""

import sys
import os

# Add src directory to path so we can import from it
src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Import and run the main application
from src.main import main

if __name__ == "__main__":
    main()
