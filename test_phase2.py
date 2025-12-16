"""
Test script for Phase 2 refactoring - Business Logic
"""

import sys
import os
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

print("=" * 60)
print("Phase 2 Validation: Business Logic")
print("=" * 60)

# Test imports
print("\n1. Testing imports...")
try:
    from src.logic import match_study_type, StudyTracker
    print("   [OK] match_study_type imported")
    print("   [OK] StudyTracker imported")
except Exception as e:
    print(f"   [FAIL] Import failed: {e}")
    sys.exit(1)

# Test study matcher
print("\n2. Testing study matcher...")
try:
    # Create a simple RVU table
    test_rvu_table = {
        "CT Chest": 1.5,
        "CT Head": 1.3,
        "XR Chest": 0.3,
        "MRI Brain": 2.0,
        "Unknown": 0.0
    }
    
    # Test exact match
    study_type, rvu = match_study_type("CT Chest", test_rvu_table)
    assert study_type == "CT Chest", f"Expected 'CT Chest', got '{study_type}'"
    assert rvu == 1.5, f"Expected 1.5, got {rvu}"
    print("   [OK] Exact match works")
    
    # Test prefix match (should match "XR Other" since there's no "XR Hand" in table)
    # Add "XR Other" to test table
    test_rvu_table["XR Other"] = 0.3
    study_type, rvu = match_study_type("xr hand", test_rvu_table)
    assert study_type == "XR Other", f"Expected 'XR Other', got '{study_type}'"
    print("   [OK] Prefix match works")
    
    # Test unknown
    study_type, rvu = match_study_type("", test_rvu_table)
    assert study_type == "Unknown", f"Expected 'Unknown', got '{study_type}'"
    assert rvu == 0.0, f"Expected 0.0, got {rvu}"
    print("   [OK] Unknown handling works")
    
except Exception as e:
    print(f"   [FAIL] Study matcher test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test study tracker
print("\n3. Testing study tracker...")
try:
    tracker = StudyTracker(min_seconds=5)
    
    # Add a study
    now = datetime.now()
    tracker.add_study(
        "TEST001",
        "CT Chest",
        now,
        rvu_table=test_rvu_table
    )
    assert "TEST001" in tracker.active_studies
    print("   [OK] Can add study")
    
    # Check it's not completed yet (still visible)
    completed = tracker.check_completed(now, "TEST001")
    assert len(completed) == 0
    print("   [OK] Study not completed while visible")
    
    # Mark as completed (different study visible)
    completed = tracker.check_completed(now, "TEST002")
    assert len(completed) == 0  # Too short (0 seconds)
    print("   [OK] Short studies ignored")
    
    # Add a study and wait enough time
    from datetime import timedelta
    tracker.add_study(
        "TEST003",
        "MRI Brain",
        now,
        rvu_table=test_rvu_table
    )
    later = now + timedelta(seconds=10)
    completed = tracker.check_completed(later, "")
    assert len(completed) == 1
    assert completed[0]["accession"] == "TEST003"
    print("   [OK] Study completed after min_seconds")
    
    # Test mark_seen
    tracker.mark_seen("TEST004")
    assert "TEST004" in tracker.seen_accessions
    print("   [OK] Can mark study as seen")
    
except Exception as e:
    print(f"   [FAIL] Study tracker test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("[SUCCESS] Phase 2 Validation PASSED")
print("=" * 60)
print("\nBusiness logic is working correctly.")
print("Ready to proceed to Phase 3: Data Layer extraction")
