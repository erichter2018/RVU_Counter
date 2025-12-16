# RVU Counter Refactoring - Manual Validation Checklist

Use this checklist to verify the refactored application works correctly in production.

---

## Pre-Validation Setup

- [x] Backup created: `RVUCounterFull.pyw`
- [x] All automated tests passing
- [x] Code committed to git branch: `refactoring`
- [ ] Database backed up (copy `rvu_records.db` to safe location)
- [ ] Settings backed up (copy `rvu_settings.yaml` to safe location)

---

## Phase 1: Application Launch

### Test: Basic Startup
- [ ] Run `py RVUCounter.pyw` (or `python RVUCounter.pyw`)
- [ ] Application window appears
- [ ] No error messages in console
- [ ] Log file created: `rvu_counter.log`
- [ ] Window positioned correctly on screen
- [ ] All UI elements visible and properly formatted

**Expected Result:** Application launches cleanly with no errors

**If Failed:** Check console output and `rvu_counter.log` for errors

---

## Phase 2: Core Functionality

### Test: Shift Management
- [ ] Click "Start Shift" button
- [ ] Shift timer begins counting
- [ ] Current time displayed correctly
- [ ] Click "End Shift" 
- [ ] Shift ends and saves to database

**Expected Result:** Shifts start and end correctly

---

### Test: Study Detection
- [ ] Open PowerScribe 360 or Mosaic
- [ ] Open a study report
- [ ] Verify accession number appears in RVU Counter
- [ ] Verify procedure text displayed
- [ ] Close the study in PowerScribe/Mosaic
- [ ] Verify study is recorded with correct RVU
- [ ] Check counter increments appropriately

**Expected Result:** Studies detected and recorded correctly

---

### Test: Study Type Classification
- [ ] Record various study types (CT, MRI, XR, etc.)
- [ ] Verify each classified correctly
- [ ] Check RVU values match settings
- [ ] Verify special cases (CT CAP, CT Spine, etc.)

**Expected Result:** All study types classified correctly per rules

---

## Phase 3: Settings Window

### Test: Open Settings
- [ ] Click Settings button
- [ ] Settings window opens
- [ ] All current settings displayed correctly
- [ ] Window positioned properly

**Expected Result:** Settings window opens with current values

---

### Test: Modify Settings
- [ ] Change shift length
- [ ] Toggle dark mode
- [ ] Modify RVU display options
- [ ] Change compensation rates
- [ ] Click "Save Settings"
- [ ] Restart application
- [ ] Verify settings persisted

**Expected Result:** All settings save and load correctly

---

### Test: RVU Table Editing
- [ ] Open RVU table editor
- [ ] Modify an RVU value
- [ ] Save changes
- [ ] Verify new value used for classification

**Expected Result:** RVU table updates work correctly

---

## Phase 4: Statistics Window

### Test: Open Statistics
- [ ] Click Statistics button
- [ ] Statistics window opens
- [ ] All shifts displayed in list
- [ ] Current shift highlighted

**Expected Result:** Statistics window opens with shift data

---

### Test: View Shift Details
- [ ] Select a completed shift
- [ ] View shift summary (total RVU, count, duration)
- [ ] View individual study records
- [ ] Sort by different columns
- [ ] Verify calculations correct

**Expected Result:** Shift details display accurately

---

### Test: Graphing (if matplotlib installed)
- [ ] Click "Show Graphs" tab
- [ ] Hourly productivity graph displays
- [ ] Study type distribution pie chart displays
- [ ] Graphs update when selecting different shifts
- [ ] Export graph functionality works

**Expected Result:** All graphs render correctly

---

### Test: Data Export
- [ ] Select a shift
- [ ] Click "Export" button
- [ ] Choose export location
- [ ] Verify CSV/Excel file created
- [ ] Open file and verify data correct

**Expected Result:** Data exports successfully

---

### Test: Shift Management
- [ ] Delete a test shift
- [ ] Confirm deletion
- [ ] Verify shift removed from list
- [ ] Combine multiple shifts (if applicable)
- [ ] Verify combined shift has correct totals

**Expected Result:** Shift operations work correctly

---

## Phase 5: Data Persistence

### Test: Database Operations
- [ ] Restart application multiple times
- [ ] Verify all data persists across restarts
- [ ] Check database file size is reasonable
- [ ] Verify no duplicate records created
- [ ] Check indexes working (fast queries)

**Expected Result:** Data persists correctly, no corruption

---

### Test: Settings Persistence
- [ ] Modify settings
- [ ] Close application
- [ ] Reopen application
- [ ] Verify settings retained
- [ ] Check window positions saved

**Expected Result:** All settings and positions persist

---

## Phase 6: Cloud Backup (if OneDrive configured)

### Test: Automatic Backup
- [ ] Ensure OneDrive is running
- [ ] Record several studies
- [ ] Wait for auto-backup (or trigger manually)
- [ ] Check OneDrive folder for backup file
- [ ] Verify backup file is valid SQLite database

**Expected Result:** Backups created automatically

---

### Test: Backup Settings
- [ ] Open settings
- [ ] Configure backup schedule
- [ ] Enable/disable backup
- [ ] Verify backup behavior changes accordingly

**Expected Result:** Backup settings work correctly

---

## Phase 7: Error Handling

### Test: Missing Settings File
- [ ] Temporarily rename `rvu_settings.yaml`
- [ ] Start application
- [ ] Verify graceful error message
- [ ] Restore settings file
- [ ] Restart application successfully

**Expected Result:** Graceful error handling

---

### Test: Corrupted Database
- [ ] Create a copy of database for safety
- [ ] Corrupt the database file
- [ ] Start application
- [ ] Verify error handling
- [ ] Restore database
- [ ] Restart successfully

**Expected Result:** Application handles corruption gracefully

---

### Test: Off-Screen Windows
- [ ] Disconnect secondary monitor (if applicable)
- [ ] Start application
- [ ] Verify windows reposition to visible area
- [ ] Check no windows stuck off-screen

**Expected Result:** Windows always visible

---

## Phase 8: Performance

### Test: Large Dataset
- [ ] Load application with large existing database (1000+ studies)
- [ ] Verify startup time acceptable (< 5 seconds)
- [ ] Open statistics window
- [ ] Verify responsiveness
- [ ] Generate graphs
- [ ] Check memory usage reasonable

**Expected Result:** Good performance with large datasets

---

### Test: Long Running Session
- [ ] Run application for extended period (multiple hours)
- [ ] Record many studies
- [ ] Check memory doesn't grow indefinitely
- [ ] Verify no performance degradation
- [ ] Check log file size stays under 10MB

**Expected Result:** Stable performance over time

---

## Phase 9: Edge Cases

### Test: Duplicate Accessions
- [ ] Record study with accession A
- [ ] Try to record same accession A again
- [ ] Verify duplicate handling per settings
- [ ] Check ignore_duplicate_accessions setting

**Expected Result:** Duplicates handled per user preference

---

### Test: Multi-Accession Studies
- [ ] Record study with multiple accessions
- [ ] Verify each accession recorded separately
- [ ] Check RVU distributed correctly
- [ ] Verify duration split appropriately

**Expected Result:** Multi-accession studies handled correctly

---

### Test: Unknown Study Types
- [ ] Record study with unknown procedure
- [ ] Verify classified as "Unknown"
- [ ] Check RVU = 0.0
- [ ] Verify still counted in statistics

**Expected Result:** Unknown studies handled gracefully

---

## Phase 10: Packaging

### Test: Build Executable
- [ ] Navigate to `packaging` folder
- [ ] Run `package RVUCounter.bat`
- [ ] Build completes without errors
- [ ] `RVU Counter.exe` created
- [ ] Exe file size reasonable (< 100MB)

**Expected Result:** Executable builds successfully

---

### Test: Run Executable
- [ ] Copy `RVU Counter.exe` to test location
- [ ] Copy `rvu_settings.yaml` to same location
- [ ] Run `RVU Counter.exe`
- [ ] Verify application launches
- [ ] Test core functionality (start shift, record study)
- [ ] Verify settings and database created in exe directory

**Expected Result:** Executable runs correctly standalone

---

## Final Validation

### All Tests Passed?
- [ ] All Phase 1 tests passed
- [ ] All Phase 2 tests passed
- [ ] All Phase 3 tests passed
- [ ] All Phase 4 tests passed
- [ ] All Phase 5 tests passed
- [ ] All Phase 6 tests passed
- [ ] All Phase 7 tests passed
- [ ] All Phase 8 tests passed
- [ ] All Phase 9 tests passed
- [ ] All Phase 10 tests passed

---

## Sign-off

**Tested By:** _______________________

**Date:** _______________________

**Result:** 
- [ ] ✅ PASS - Ready for production
- [ ] ⚠️ PASS WITH ISSUES - Document issues below
- [ ] ❌ FAIL - Do not deploy

**Issues Found:**
```
(List any issues discovered during testing)





```

**Notes:**
```
(Additional notes or observations)





```

---

## Rollback Procedure (If Needed)

If critical issues are found:

1. Stop the refactored application
2. Restore from backup:
   ```bash
   copy RVUCounterFull.pyw RVUCounter.pyw
   ```
3. Restart with original monolithic version
4. Document issues in git
5. Fix issues in refactoring branch
6. Retest before deploying

---

## Post-Deployment Monitoring

After deploying refactored version:

- [ ] Monitor log file for errors
- [ ] Check database integrity daily (first week)
- [ ] Verify backup operations working
- [ ] Monitor performance metrics
- [ ] Collect user feedback
- [ ] Address any issues promptly

---

**Refactoring Status:** COMPLETE ✅  
**Testing Status:** PENDING (use this checklist)  
**Production Status:** READY (after checklist complete)
