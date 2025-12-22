# 🧪 RVU Counter v1.7 - Testing Guide

**Version**: 1.7  
**Date**: December 19, 2025  
**Purpose**: Comprehensive testing procedures for v1.7 auto-update system and new features

---

## 📋 Table of Contents

1. [Pre-Testing Setup](#pre-testing-setup)
2. [Test Suite 1: Fresh Installation](#test-suite-1-fresh-installation)
3. [Test Suite 2: Upgrade from v1.6](#test-suite-2-upgrade-from-v16)
4. [Test Suite 3: Auto-Update System](#test-suite-3-auto-update-system)
5. [Test Suite 4: Integrated Tools](#test-suite-4-integrated-tools)
6. [Test Suite 5: What's New Viewer](#test-suite-5-whats-new-viewer)
7. [Test Suite 6: RVU Classifications](#test-suite-6-rvu-classifications)
8. [Test Suite 7: Regression Testing](#test-suite-7-regression-testing)
9. [Bug Reporting](#bug-reporting)

---

## Pre-Testing Setup

### Environment Preparation

**Required:**
- Windows 10/11 system
- No admin privileges required
- Internet connection for update testing
- Sample payroll Excel files (for Excel checker testing)

**Backup Current Installation:**
```
1. Copy entire RVU Counter folder to backup location
2. Export database: C:\Users\<username>\AppData\Local\RVU_Counter\data\rvu_records.db
3. Note current settings and window positions
```

**Test Data:**
- Have at least 20-30 study records in database
- Have 1-2 Excel payroll files ready
- Note down any known mismatches in your data

---

## Test Suite 1: Fresh Installation

**Objective**: Verify v1.7 works correctly on a clean system

### Test 1.1: Initial Launch

**Steps:**
1. Download `RVU Counter.exe` (v1.7)
2. Place in new folder: `C:\Users\<username>\Desktop\RVU_Test\`
3. Double-click to launch

**Expected Results:**
- ✅ Application launches without errors
- ✅ Folders created automatically:
  - `data/`
  - `settings/`
  - `logs/`
  - `helpers/`
- ✅ Default settings files created:
  - `settings/user_settings.yaml`
  - `settings/rvu_rules.yaml`
- ✅ Empty database created: `data/rvu_records.db`
- ✅ Log file created: `logs/rvu_counter.log`

**Check Log File:**
```
Look for:
- "User settings file: ..." 
- "Rules file: ..."
- "Database file: ..."
- No error messages
```

### Test 1.2: Basic Functionality

**Steps:**
1. Start a shift
2. Manually add a study (if auto-capture not available)
3. End shift
4. Open Statistics
5. Change a setting
6. Close and reopen app

**Expected Results:**
- ✅ Shift starts/ends correctly
- ✅ Studies recorded to database
- ✅ Statistics display correctly
- ✅ Settings persist after restart
- ✅ Window positions saved

---

## Test Suite 2: Upgrade from v1.6

**Objective**: Verify seamless migration from v1.6 to v1.7

### Test 2.1: Pre-Upgrade Snapshot

**Steps:**
1. Start with working v1.6 installation
2. Record current state:
   - Number of records in database
   - Current shift status
   - Settings (dark mode, compensation role, etc.)
   - Window positions
3. Close v1.6

**Data to Record:**
```
Total Records: _______
Current Shift Active: Yes/No
Total Shifts: _______
Dark Mode: On/Off
Role: Assoc/Partner
Window Positions: Note where windows appear
```

### Test 2.2: Run v1.7 Upgrade

**Steps:**
1. Replace `RVU Counter.exe` with v1.7 version
2. Launch v1.7
3. Watch for migration messages in console (if visible)

**Expected Results:**
- ✅ App launches successfully
- ✅ "What's New" window appears automatically
- ✅ New folders created:
  - `data/` folder
  - `settings/` folder
  - `helpers/` folder
- ✅ Database moved to `data/rvu_records.db`
- ✅ Settings split into two files:
  - `settings/user_settings.yaml` (user prefs)
  - `settings/rvu_rules.yaml` (RVU rules)
- ✅ Legacy `rvu_settings.yaml` renamed to `.migrated`

### Test 2.3: Post-Upgrade Validation

**Steps:**
1. Check record count: Open Statistics → verify total matches pre-upgrade
2. Check current shift: Verify active shift preserved (if applicable)
3. Check settings:
   - Dark mode setting preserved
   - Compensation role preserved
   - Window positions preserved
4. Check shifts history: Statistics → Prior shift shows correctly

**Expected Results:**
- ✅ All records preserved (count matches)
- ✅ All shifts history intact
- ✅ All settings preserved
- ✅ Window positions preserved
- ✅ No data loss

**Check Migration Logs:**
```
Open logs/rvu_counter.log
Look for:
- "Migrating legacy settings from..."
- "Migration to split settings complete."
- No error messages during migration
```

---

## Test Suite 3: Auto-Update System

**Objective**: Verify the auto-update mechanism works end-to-end

### Test 3.1: Update Check on Startup

**Steps:**
1. Launch v1.7
2. Observe bottom-left corner of main window
3. Wait 5-10 seconds for background check to complete

**Expected Results:**
- ✅ No errors during startup
- ✅ If update available: "✨ Update Available" button appears
- ✅ If no update: No button appears (current behavior)

**Note**: For this test to show an update, you need a v1.8 (or higher) release published on GitHub.

### Test 3.2: Create Test Release (Developer Task)

**Steps:**
1. Go to GitHub: `https://github.com/erichter2018/RVU-Releases`
2. Click "Releases" → "Create a new release"
3. Set tag: `v1.8`
4. Set title: "RVU Counter v1.8 (Test Release)"
5. Upload a test executable (can be v1.7 renamed for testing)
6. Name it exactly: `RVU Counter.exe`
7. Publish release

**Expected Results:**
- ✅ Release created successfully
- ✅ Executable visible in assets

### Test 3.3: Update Detection

**Steps:**
1. Close and reopen v1.7
2. Wait for background check
3. Observe if "✨ Update Available" appears

**Expected Results:**
- ✅ "✨ Update Available" button appears in bottom-left
- ✅ Button is clickable
- ✅ Button shows Windows blue background

### Test 3.4: Update Download and Install

**Steps:**
1. Click "✨ Update Available" button
2. Read confirmation dialog
3. Click "Yes" to proceed with update
4. Observe progress window

**Expected Results:**
- ✅ Confirmation dialog appears with version info
- ✅ Progress window appears saying "Downloading update..."
- ✅ Progress bar shows indeterminate animation
- ✅ Download completes within reasonable time (depends on connection)
- ✅ App closes automatically
- ✅ Updater window appears briefly
- ✅ App restarts automatically with new version

**What Happens Behind the Scenes:**
1. New `RVU Counter.exe` downloaded to `helpers/RVU Counter.new.exe`
2. App launches `helpers/updater.bat`
3. App exits
4. Updater waits for app to close
5. Updater backs up current exe to `.old.exe`
6. Updater moves `.new.exe` to main `RVU Counter.exe`
7. Updater launches new exe
8. Updater cleans up

### Test 3.5: Post-Update Validation

**Steps:**
1. After app restarts, check version label (bottom-left)
2. Verify all data intact:
   - Check total records
   - Check current shift (if applicable)
   - Check settings preserved
3. Check for backup file in main folder: `RVU Counter.old.exe`

**Expected Results:**
- ✅ Version number updated (shows v1.8)
- ✅ All data preserved
- ✅ Settings preserved
- ✅ Backup exe exists
- ✅ What's New appears again (for v1.8)

---

## Test Suite 4: Integrated Tools

**Objective**: Verify Database Repair and Excel Checker work correctly

### Test 4.1: Database Repair - Access

**Steps:**
1. Click "Tools" button (main window)
2. Observe Tools window

**Expected Results:**
- ✅ Tools window opens
- ✅ Two tabs visible: "Database Repair" and "Excel Checker"
- ✅ Database Repair tab is default

### Test 4.2: Database Repair - Scan

**Steps:**
1. Click "Scan for Mismatches" button
2. Observe progress bar
3. Wait for scan to complete
4. Read results in text area

**Expected Results:**
- ✅ Progress bar shows percentage (0% → 100%)
- ✅ Progress label updates: "Scanning... (XX/TOTAL)"
- ✅ Results appear in text area after scan
- ✅ If mismatches found:
  - Shows count: "Found X mismatches"
  - Lists each mismatch with details:
    - Procedure name
    - Old type and RVU
    - New type and RVU
- ✅ If no mismatches: "✅ No mismatches found! All records match current rules."
- ✅ "Fix All Mismatches" button becomes enabled (if mismatches exist)

### Test 4.3: Database Repair - Fix

**Steps:**
1. Click "Fix All Mismatches" button
2. Read confirmation dialog
3. Click "Yes" to confirm
4. Observe progress
5. Read results

**Expected Results:**
- ✅ Confirmation dialog appears
- ✅ Progress bar shows fix progress
- ✅ Progress label updates
- ✅ Results show: "✅ Fixed X records successfully"
- ✅ "Fix All Mismatches" button becomes disabled
- ✅ Re-scan shows no mismatches

**Validation:**
- Open Statistics → verify RVU totals updated correctly
- Check specific fixed records to confirm new classifications

### Test 4.4: Excel Checker - Upload File

**Steps:**
1. Switch to "Excel Checker" tab
2. Click "Select Excel File" button
3. Choose a payroll Excel file
4. Observe processing

**Expected Results:**
- ✅ File dialog opens
- ✅ After selection, progress bar shows processing
- ✅ Progress label updates: "Processing... (XX/TOTAL)"
- ✅ Results appear in text area
- ✅ Results show:
  - File name
  - Total procedures processed
  - Number of outliers found
  - List of outliers with details:
    - Procedure name
    - Excel RVU
    - Matched type
    - Matched RVU
    - Reason for mismatch

### Test 4.5: Excel Checker - Export Report

**Steps:**
1. After processing a file, click "Export Report" button
2. Choose save location
3. Save report

**Expected Results:**
- ✅ Export button becomes enabled after processing
- ✅ Save dialog opens
- ✅ Default filename suggested: `<original>_rvu_report.txt`
- ✅ Report file created successfully
- ✅ Report contains all outlier details in readable format

**Validate Report Content:**
- Open saved .txt file
- Verify all outliers listed
- Check formatting is readable
- Confirm totals match UI display

---

## Test Suite 5: What's New Viewer

**Objective**: Verify What's New functionality

### Test 5.1: Automatic Display After Update

**Steps:**
1. Upgrade from v1.6 to v1.7 (or v1.7 to v1.8)
2. Launch new version
3. Observe if What's New appears

**Expected Results:**
- ✅ What's New window appears automatically after ~1 second
- ✅ Window shows correct version in title
- ✅ Content displays release notes
- ✅ Formatting looks good (headers, bullets, etc.)

### Test 5.2: Manual Access via Help Button

**Steps:**
1. Click "?" button (next to Settings)
2. Observe What's New window

**Expected Results:**
- ✅ What's New window opens
- ✅ Shows current version release notes
- ✅ Content is readable and formatted

### Test 5.3: Version Tracking

**Steps:**
1. Open What's New (via "?" button)
2. Close window
3. Close and reopen app
4. Check if What's New appears again

**Expected Results:**
- ✅ What's New does NOT appear on subsequent launches
- ✅ Only appears when version changes
- ✅ Can still access manually via "?" button

**Validation:**
```
Check settings/user_settings.yaml:
Look for: last_seen_version: "1.7"
Should match current version
```

---

## Test Suite 6: RVU Classifications

**Objective**: Verify all 19 RVU fixes work correctly

### Test 6.1: CT Abdomen

**Test Procedures:**
```
- "CT ABDOMEN WITHOUT IV CONTRAST" → CT Abdomen (1.0)
- "CT ABDOMEN WITH IV CONTRAST" → CT Abdomen (1.0)
- "CT ABDOMEN WITHOUT THEN WITH IV CONTRAST" → CT Abdomen (1.0)
```

**Steps:**
1. Manually add study with above procedure text
2. Check classification shown
3. Verify RVU = 1.0

**Expected**: All should match "CT Abdomen" at 1.0 RVU

### Test 6.2: CTA Chest + CT AP

**Test Procedures:**
```
- "CT CHEST ANGIOGRAPHY WITH CONTRAST AND CT ABDOMEN PELVIS WITH CONTRAST" → 3.0
```

**Steps:**
1. Add study with above text
2. Check classification
3. Verify RVU = 3.0

**Expected**: Should match "CTA Chest + CT AP" at 3.0 RVU

### Test 6.3: MRI Hip Bilateral

**Test Procedures:**
```
- "MR HIP WITHOUT IV CONTRAST BILATERAL" → MRI Hip Bilateral (3.5)
```

**Expected**: 3.5 RVU

### Test 6.4: XR Bilateral Studies

**Test Procedures:**
```
- "XR ACROMIOCLAVICULAR JOINTS BILATERAL" → XR MSK Bilateral (0.6)
- "XR CALCANEUS BILATERAL" → XR MSK Bilateral (0.6)
- "XR FOREIGN BODY BILATERAL" → XR MSK Bilateral (0.6)
- "XR TEMPOROMANDIBULAR JOINT OPEN AND CLOSED BILATERAL" → XR MSK Bilateral (0.6)
```

**Expected**: All should match "XR MSK Bilateral" at 0.6 RVU

### Test 6.5: Additional Tests

**Test each of these:**
```
- "CT HEAD FACE AND CERVICAL SPINE WITHOUT CONTRAST" → 2.9
- "CT LOWER EXTREMITY ANGIOGRAPHY WITHOUT THEN WITH IV CONTRAST" → CTA Lower Extremity (1.75)
- "CT CERVICAL THORACIC LUMBAR SPINE WITHOUT IV CONTRAST" → CT Triple Spine (5.25)
- "XR ABDOMEN DECUBITUS LATERAL" → XR Abdomen (0.3)
- "XR SCANOGRAM BONE LENGTH" → XR Scanogram (1.0)
- "CT OUTSIDE FILM READ" → CT Outside Film Read (0.9)
- "MR BRAIN ANGIOGRAPHY WITHOUT IV CONTRAST" → MRI Brain with MRA (2.3)
```

### Test 6.6: Batch Validation

**Steps:**
1. Use Excel Checker to upload old payroll files
2. Check outlier count
3. Compare with previous reports

**Expected Results:**
- ✅ Outlier count should be significantly reduced
- ✅ All 19 fixes should no longer appear as outliers
- ✅ Any remaining outliers are new/different issues

---

## Test Suite 7: Regression Testing

**Objective**: Ensure existing functionality still works

### Test 7.1: Basic Operations

**Test all core features:**
- [ ] Start/end shift
- [ ] Manual study entry
- [ ] Auto-capture from PowerScribe
- [ ] Auto-capture from Mosaic
- [ ] Undo last study
- [ ] View statistics
- [ ] Change settings
- [ ] Dark mode toggle
- [ ] Window repositioning
- [ ] Pace car display

**Expected**: All work as before, no regression

### Test 7.2: Statistics Views

**Test all statistics tabs:**
- [ ] Efficiency view
- [ ] Summary view
- [ ] Compensation view
- [ ] By Study Type view
- [ ] By Modality view
- [ ] By Body Part view
- [ ] By Hour view
- [ ] By Shift view
- [ ] Comparison view

**Expected**: All display correctly, no crashes

### Test 7.3: Settings Persistence

**Steps:**
1. Change multiple settings
2. Reposition windows
3. Close app
4. Reopen app

**Expected**: All changes persisted

---

## Bug Reporting

### If You Find a Bug

**Report Format:**
```
**Bug Title**: Brief description

**Severity**: Critical / High / Medium / Low

**Steps to Reproduce**:
1. First step
2. Second step
3. etc.

**Expected Behavior**:
What should happen

**Actual Behavior**:
What actually happened

**Environment**:
- Windows version:
- RVU Counter version:
- Upgrading from: (if applicable)

**Logs**:
Attach or paste relevant lines from logs/rvu_counter.log

**Screenshots**:
If applicable
```

### Common Issues and Solutions

**Issue**: "What's New" file not found
- **Solution**: Check `documentation/WHATS_NEW_v1.7.md` exists

**Issue**: Update check fails
- **Solution**: Check internet connection, verify GitHub repo is accessible

**Issue**: Updater doesn't run
- **Solution**: Check write permissions in app folder, run from %LOCALAPPDATA%

**Issue**: Migration doesn't happen
- **Solution**: Check logs for errors, ensure v1.6 had rvu_settings.yaml

**Issue**: Database repair finds many mismatches after update
- **This is expected** - run "Fix All Mismatches" to apply new rules

---

## Test Completion Checklist

### Before Marking v1.7 as Stable:

- [ ] All Test Suite 1 tests passed
- [ ] All Test Suite 2 tests passed
- [ ] All Test Suite 3 tests passed
- [ ] All Test Suite 4 tests passed
- [ ] All Test Suite 5 tests passed
- [ ] All Test Suite 6 tests passed
- [ ] All Test Suite 7 tests passed
- [ ] No critical bugs found
- [ ] Performance acceptable (no major slowdowns)
- [ ] Logs show no unexpected errors
- [ ] At least 2 real-world upgrade tests successful

### Sign-Off:

```
Tested by: ________________
Date: ________________
Version tested: ________________
Approved for release: Yes / No
Notes: ________________________________
```

---

**End of Testing Guide**

For technical details, see: `documentation/AUTO_UPDATE_DESIGN.md`  
For implementation status, see: `IMPLEMENTATION_COMPLETE.md`  
For user-facing notes, see: `documentation/WHATS_NEW_v1.7.md`





