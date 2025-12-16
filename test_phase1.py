"""
Test script for Phase 1 refactoring - Core utilities
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 60)
print("Phase 1 Validation: Core Utilities")
print("=" * 60)

# Test imports
print("\n1. Testing imports...")
try:
    from src.core import config
    print("   [OK] config imported")
    from src.core import logging_config
    print("   [OK] logging_config imported")
    from src.core import platform_utils
    print("   [OK] platform_utils imported")
except Exception as e:
    print(f"   [FAIL] Import failed: {e}")
    sys.exit(1)

# Test config constants
print("\n2. Testing config constants...")
try:
    assert hasattr(config, 'APP_VERSION')
    assert hasattr(config, 'HAS_MATPLOTLIB')
    assert hasattr(config, 'LOG_MAX_BYTES')
    print(f"   [OK] APP_VERSION = {config.APP_VERSION}")
    print(f"   [OK] HAS_MATPLOTLIB = {config.HAS_MATPLOTLIB}")
    print(f"   [OK] HAS_TKCALENDAR = {config.HAS_TKCALENDAR}")
except Exception as e:
    print(f"   [FAIL] Config test failed: {e}")
    sys.exit(1)

# Test logging setup
print("\n3. Testing logging configuration...")
try:
    logger = logging_config.setup_logging(os.path.dirname(__file__))
    logger.info("Test log message from validation script")
    print("   [OK] Logging configured successfully")
    print(f"   [OK] Log file: {os.path.join(os.path.dirname(__file__), config.LOG_FILE_NAME)}")
except Exception as e:
    print(f"   [FAIL] Logging test failed: {e}")
    sys.exit(1)

# Test platform utilities
print("\n4. Testing platform utilities...")
try:
    monitors = platform_utils.get_all_monitor_bounds()
    primary = platform_utils.get_primary_monitor_bounds()
    print(f"   [OK] Detected {len(monitors[4])} monitor(s)")
    print(f"   [OK] Virtual bounds: {monitors[0]}, {monitors[1]} to {monitors[2]}, {monitors[3]}")
    print(f"   [OK] Primary monitor: {primary}")
except Exception as e:
    print(f"   [FAIL] Platform utilities test failed: {e}")
    sys.exit(1)

# Test get_app_paths
print("\n5. Testing app paths...")
try:
    settings_dir, data_dir = platform_utils.get_app_paths()
    print(f"   [OK] Settings dir: {settings_dir}")
    print(f"   [OK] Data dir: {data_dir}")
except Exception as e:
    print(f"   [FAIL] App paths test failed: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("[SUCCESS] Phase 1 Validation PASSED")
print("=" * 60)
print("\nCore utilities are working correctly.")
print("Ready to proceed to Phase 2: Models & Logic extraction")
