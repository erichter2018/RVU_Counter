# Developer Notes - RVU Counter v1.7 Implementation

## 🏗️ Architecture Changes

### Folder Structure
```
/RVU Counter/
├── RVU Counter.exe          # Main application
├── helpers/
│   ├── updater.bat          # Update sidecar script
│   └── RVU Counter.new.exe  # Downloaded update (temporary)
├── settings/
│   ├── user_settings.yaml   # User preferences ONLY
│   └── rvu_rules.yaml       # RVU table & classification rules
├── data/
│   └── rvu_records.db       # SQLite database
└── logs/
    └── rvu_counter.log      # Application logs
```

**Migration Flow** (v1.6 → v1.7):
1. On first run, `data_manager._migrate_to_split_settings()` executes
2. Checks if `settings/rvu_settings.yaml` exists AND `settings/user_settings.yaml` doesn't
3. If true, loads legacy YAML and splits it:
   - User prefs → `user_settings.yaml`
   - RVU rules → `rvu_rules.yaml`
4. Renames original to `rvu_settings.yaml.migrated` as backup
5. Database moves handled by `migrate_data.py` (already existed)

### Settings Split Rationale
**Before**: One monolithic `rvu_settings.yaml` (user prefs + RVU rules mixed)  
**After**: Two files:
- `user_settings.yaml` - Changes frequently (theme, window positions, etc.)
- `rvu_rules.yaml` - Changes rarely (RVU values, classification rules)

**Benefits**:
- Rule hotfixes without touching user prefs
- Cleaner version control
- Easier to backup just user data
- Prevents accidental rule edits

---

## 🔄 Update System Deep Dive

### Component Interaction
```
[Main App] 
    ↓ (on startup)
[UpdateManager.check_for_updates()]
    ↓ (queries GitHub API)
[GitHub Releases API]
    ↓ (returns latest release info)
[UpdateManager._is_newer()]
    ↓ (compares versions)
[Main Window shows "✨ Update Available"]
    ↓ (user clicks)
[UpdateManager.download_update()]
    ↓ (downloads .exe to helpers/)
[UpdateManager.start_update_process()]
    ↓ (launches updater.bat, exits app)
[updater.bat]
    ↓ (waits for process exit)
    ↓ (backs up current .exe)
    ↓ (swaps in new .exe)
    ↓ (relaunches app)
[Updated App Starts]
    ↓ (detects version change)
[Shows What's New window]
```

### Version Comparison Logic
- Current version extracted from `APP_VERSION` in `config.py`
- Format: `"1.7 (12/19/2025)"` → extracts `"1.7"`
- GitHub tag format: `"v1.7"` → strips `"v"` → `"1.7"`
- Comparison: Split on `.`, convert to ints, compare as list
- Example: `[1, 7] > [1, 6]` → True

### Critical Files
1. **`src/core/update_manager.py`**
   - `check_for_updates()`: Background thread, queries GitHub
   - `download_update()`: Uses urllib, saves to helpers/
   - `start_update_process()`: Spawns detached updater.bat process
   
2. **`helpers/updater.bat`**
   - Waits for `RVU Counter.exe` process to exit
   - Uses `tasklist` polling (1 sec intervals)
   - Error handling: Restores backup on failure
   - Cleanup: Removes `.old` backup after successful relaunch

### Known Limitations
- **Windows Only**: Batch script, tasklist command
- **Single Instance**: Doesn't check for multiple instances
- **No Rollback UI**: User must manually rename files if update corrupts
- **Network Required**: No offline mode for updates

### Security Considerations
- **No Signature Verification**: .exe downloaded directly from GitHub
- **MITM Risk**: Uses HTTPS but no certificate pinning
- **Recommendation**: Use GitHub's checksum validation in future version

---

## 🛠️ Tools Integration

### Database Repair
**Location**: `src/logic/database_repair.py` → `src/ui/tools_window.py`

**How It Works**:
1. Scans all records in SQLite database
2. Re-runs `match_study_type()` on each procedure name
3. Compares stored type/RVU with current classification rules
4. Reports mismatches (different type OR RVU differs by >0.01)
5. On user confirmation, updates records in batch
6. Reloads memory cache in `data_manager`

**Threading**: Scan and fix operations run in background threads to avoid UI freeze

**Progress Callbacks**: Uses lambda closures to update progress bar from worker thread

### Excel Checker
**Location**: `src/logic/excel_checker.py` → `src/ui/tools_window.py`

**How It Works**:
1. Uses `openpyxl` to read .xlsx files
2. Looks for columns: `StandardProcedureName`, `wRVU_Matrix`
3. Iterates rows, calls `match_study_type()` on each procedure
4. Compares Excel RVU with matched RVU (epsilon: 0.01)
5. Generates text report with unique outliers

**Dependencies**: Requires `openpyxl` package (should be in requirements.txt)

**Report Format**: Plain text, same as standalone `check_rvu_excel_files.py`

---

## 📚 What's New System

### Version Tracking
- Stored in `settings/user_settings.yaml` as `last_seen_version`
- Checked on app startup in `main_window._check_version_and_show_whats_new()`
- If current version ≠ last seen, triggers What's New window
- Updates setting after display to avoid showing again

### Content Loading
- Reads from `documentation/WHATS_NEW_v1.7.md`
- Basic markdown parsing: headers, bullets, checkmarks
- Fallback message if file not found
- Could be extended to fetch from GitHub in future

### Auto-Display Logic
```python
current_version = "1.7"
last_version = settings.get("last_seen_version", "")

if last_version != current_version:
    show_whats_new()
    settings["last_seen_version"] = current_version
```

---

## 🐛 Known Issues & Workarounds

### Issue 1: Cursor Crashes During Development
**Symptom**: IDE crashes when making many file changes  
**Workaround**: Work in small chunks, document progress frequently  
**Status**: Development complete, issue no longer affects production

### Issue 2: First Update Detection
**Symptom**: Users on v1.6 won't see update until v1.8 (need v1.7 first)  
**Workaround**: Manual v1.7 deployment required  
**Status**: Expected behavior for initial rollout

### Issue 3: Updater.bat Window Flash
**Symptom**: Console window briefly visible during update  
**Workaround**: None (Windows batch limitation)  
**Future**: Could use VBScript or compiled updater.exe to hide window

### Issue 4: Database Lock During Migration
**Symptom**: SQLite "database is locked" if app still running  
**Workaround**: User instructed to close app before running migration  
**Status**: Migration happens automatically on first run, shouldn't be issue

---

## 🧪 Testing Notes

### What Was Tested
- ✅ Syntax validation (no import errors)
- ✅ File structure creation
- ✅ YAML parsing logic
- ✅ Update manager API calls (mocked)
- ✅ Tools window UI layout
- ✅ What's New content loading

### What Needs Testing
- [ ] End-to-end update flow with real GitHub release
- [ ] Database repair with actual mismatches
- [ ] Excel checker with real payroll files
- [ ] Migration from v1.6 with existing user data
- [ ] Multi-monitor window positioning
- [ ] Error handling for network failures

### Test Environment Setup
1. Create GitHub repo: `erichter2018/RVU-Releases`
2. Create a v1.8 release with dummy .exe
3. Run v1.7, verify update detection
4. Create sample database with intentional mismatches
5. Export sample Excel file with known outliers

---

## 📦 Deployment Checklist

### Pre-Release
- [ ] Bump version to 1.7 in `src/core/config.py`
- [ ] Update `src/__init__.py` version
- [ ] Verify all dependencies in `requirements.txt`
- [ ] Run PyInstaller: `packaging/package RVUCounter.bat`
- [ ] Test frozen .exe on clean system

### GitHub Release
- [ ] Create release tagged `v1.7`
- [ ] Upload `RVU Counter.exe` as asset
- [ ] Copy `documentation/WHATS_NEW_v1.7.md` to release notes
- [ ] Mark as "Latest Release"

### Post-Release
- [ ] Monitor first real update (v1.6 → v1.7)
- [ ] Check logs for migration errors
- [ ] Verify users see What's New
- [ ] Collect feedback on Tools window

---

## 🔮 Future Enhancements

### Planned for v1.8+
- [ ] Self-healing documentation (auto-download missing help files)
- [ ] Rule hotfix system (update rvu_rules.yaml without full update)
- [ ] Update rollback UI (one-click revert to previous version)
- [ ] Checksum validation for downloaded updates
- [ ] Multiple update channels (stable, beta)
- [ ] In-app changelog viewer (fetch from GitHub)
- [ ] Anonymous usage analytics (optional)
- [ ] Crash reporting system

### Technical Debt
- [ ] Replace updater.bat with Python/compiled updater
- [ ] Add unit tests for UpdateManager
- [ ] Add integration tests for migration
- [ ] Improve error messages for users
- [ ] Add logging to updater.bat
- [ ] Implement update retry logic

---

## 🎓 Lessons Learned

### What Went Well
- Small, incremental changes prevented massive breaks
- Documentation-first approach kept progress clear
- Thread-safe UI updates avoided race conditions
- Comprehensive testing guides will catch issues early

### What Could Be Better
- Version comparison could use semver library
- Updater script could use PowerShell for better error handling
- Settings split could have been more gradual
- Update UI could show download progress percentage

### Recommendations for Next Time
- Use pytest for automated testing
- Implement logging levels (DEBUG, INFO, WARN, ERROR)
- Add sentry.io or similar for crash reporting
- Create a staging environment for risky changes

---

**Last Updated**: December 19, 2025  
**Author**: AI Assistant (working overnight while user slept)  
**Status**: Ready for user testing and deployment



