# RVU Counter v1.7 - Packaging Guide

## Overview

This guide explains how to package RVU Counter v1.7 into a distributable executable using PyInstaller.

---

## Quick Start

```bash
cd packaging
package RVUCounter.bat
```

This creates `RVU Counter.exe` ready for distribution.

---

## What Gets Packaged

### Core Application
- ✅ `RVUCounter.pyw` - Main entry point
- ✅ `src/` - All Python source code modules
  - `src/main.py` - Application entry
  - `src/ui/` - All UI windows (main, statistics, tools, what's new)
  - `src/data/` - Data management (data_manager, backup_manager)
  - `src/logic/` - Business logic (study_matcher, database_repair, excel_checker)
  - `src/core/` - Core utilities (config, platform_utils, update_manager)
  - `src/utils/` - Utility modules (window extraction, etc.)

### Configuration Files
- ✅ `rvu_settings.yaml` - Packaged as template (will be split on first run)
  - Becomes `settings/user_settings.yaml` + `settings/rvu_rules.yaml`

### Helper Scripts
- ✅ `helpers/updater.bat` - Auto-update sidecar script
  - Critical for update system to work

### Documentation
- ✅ `documentation/WHATS_NEW_v1.7.md` - Release notes
  - Displayed by What's New viewer
- ✅ Other documentation files (optional but recommended)

---

## System Requirements

### Development Environment
- Python 3.8+
- PyInstaller: `pip install pyinstaller`
- Required packages: `pip install -r requirements.txt`
  - tkinter (usually built-in)
  - PyYAML
  - matplotlib
  - numpy
  - pywinauto
  - openpyxl (for Excel checking)

### Build Platform
- Windows 10 or later
- Must build on Windows to create Windows executable

---

## Packaging Process

### Step 1: Prepare Source

Ensure all files are present in parent directory:
```
e_tools/
├── RVUCounter.pyw
├── rvu_settings.yaml
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── ui/ (main_window, statistics_window, tools_window, whats_new_window)
│   ├── data/ (data_manager, backup_manager)
│   ├── logic/ (study_matcher, database_repair, excel_checker)
│   └── core/ (config, platform_utils, update_manager)
├── helpers/
│   └── updater.bat
└── documentation/
    └── WHATS_NEW_v1.7.md
```

### Step 2: Run Packaging Script

```batch
cd packaging
package RVUCounter.bat
```

The script will:
1. Verify all required files exist
2. Run PyInstaller with correct parameters
3. Bundle all dependencies
4. Create single executable
5. Move exe to `packaging/` folder
6. Clean up build artifacts

### Step 3: Verify Build

After packaging, verify the executable:
```batch
RVU Counter.exe
```

**Expected behavior:**
- Application launches
- Tools and ? buttons visible
- No missing file errors
- Auto-update system functional (after GitHub release)

---

## PyInstaller Configuration

### Key Parameters

```batch
pyinstaller --onefile --windowed \
    --add-data "rvu_settings.yaml;." \
    --add-data "src;src" \
    --add-data "helpers;helpers" \
    --add-data "documentation;documentation" \
    --name "RVU Counter" \
    --hidden-import=src.ui.tools_window \
    --hidden-import=src.ui.whats_new_window \
    --hidden-import=src.core.update_manager \
    --hidden-import=src.logic.database_repair \
    --hidden-import=src.logic.excel_checker \
    --hidden-import=openpyxl \
    RVUCounter.pyw
```

### Parameter Explanation

| Parameter | Purpose |
|-----------|---------|
| `--onefile` | Creates single executable (not a folder) |
| `--windowed` | No console window (GUI app) |
| `--add-data` | Include non-Python files in build |
| `--name` | Output filename |
| `--hidden-import` | Explicitly import modules not auto-detected |
| `--clean` | Clean cache before build |

### Hidden Imports

These modules must be explicitly imported because PyInstaller's static analysis misses them:

**v1.7 New Modules:**
- `src.ui.tools_window` - Integrated tools UI
- `src.ui.whats_new_window` - What's New viewer
- `src.core.update_manager` - Auto-update logic
- `src.logic.database_repair` - Database repair backend
- `src.logic.excel_checker` - Excel checking backend
- `openpyxl` - Excel file parsing

---

## Build Artifacts

### Generated Files

During build:
```
packaging/
├── build/          (temporary, deleted after)
├── dist/           (temporary, deleted after)
└── RVU Counter.spec (temporary, deleted after)
```

After build:
```
packaging/
└── RVU Counter.exe  (final executable)
```

### File Size

Expected size: ~30-40 MB
- Includes Python runtime
- All dependencies
- All source code
- Documentation
- Helper scripts

---

## Testing the Build

### Basic Test

1. Copy `RVU Counter.exe` to a clean folder
2. Run it
3. Verify:
   - Application launches
   - UI loads correctly
   - Tools button works
   - ? button works
   - Settings can be changed

### Advanced Test

1. **Database Creation:**
   - Start shift
   - Verify `data/rvu_records.db` created

2. **Settings Migration:**
   - Verify `settings/user_settings.yaml` created
   - Verify `settings/rvu_rules.yaml` created

3. **Tools Integration:**
   - Click Tools → Database Repair
   - Click Tools → Excel Checker
   - Verify no errors

4. **What's New:**
   - Click ? button
   - Verify release notes display

5. **Logs:**
   - Check `logs/rvu_counter.log`
   - Should have no errors

---

## Troubleshooting

### "Module not found" errors

**Cause:** Missing `--hidden-import` parameter  
**Fix:** Add to packaging script:
```batch
--hidden-import=module_name
```

### "File not found" errors

**Cause:** Missing `--add-data` parameter  
**Fix:** Add to packaging script:
```batch
--add-data "source_path;destination_path"
```

### Executable won't run

**Causes:**
1. Antivirus blocking
2. Missing Visual C++ runtime
3. Corrupted build

**Fixes:**
1. Add antivirus exception
2. Install VC++ redistributable
3. Clean build: `--clean` parameter

### Executable is huge

**Cause:** Including unnecessary files  
**Fix:** Use `--exclude-module` for unused packages

---

## Distribution Checklist

Before distributing the executable:

- [ ] Built on Windows 10+
- [ ] Tested on clean machine
- [ ] All Tools features work
- [ ] What's New displays correctly
- [ ] Auto-update detects updates (after GitHub release)
- [ ] No errors in logs
- [ ] RVU classifications correct
- [ ] Database migration works
- [ ] Settings split works
- [ ] File size reasonable (~30-40MB)

---

## GitHub Release

After successful build:

1. **Create Release:**
   - Go to: `https://github.com/erichter2018/RVU-Releases`
   - Click "Create new release"
   - Tag: `v1.7`
   - Title: `RVU Counter v1.7`

2. **Upload Asset:**
   - Attach: `RVU Counter.exe`
   - **Name MUST be exactly:** `RVU Counter.exe`
   - The auto-update system depends on this exact name

3. **Add Release Notes:**
   - Copy from `documentation/WHATS_NEW_v1.7.md`

4. **Publish:**
   - Mark as "Latest release"
   - Publish

---

## Packaging vs. Distribution

**Packaging** = Creating the executable (this guide)  
**Distribution** = Sending to users (see `DISTRIBUTION_GUIDE.md`)

**For Users:**
- Don't send just the exe
- Use `Install_or_Upgrade_RVU_Counter.bat` system
- See `DISTRIBUTION_GUIDE.md` for full instructions

---

## Version History

### v1.7 Changes

**New inclusions:**
- `helpers/updater.bat` - Auto-update sidecar
- `documentation/WHATS_NEW_v1.7.md` - Release notes
- `src/ui/tools_window.py` - Integrated tools
- `src/ui/whats_new_window.py` - What's New viewer
- `src/core/update_manager.py` - Update system
- `src/logic/database_repair.py` - Database repair
- `src/logic/excel_checker.py` - Excel checking
- `openpyxl` - Excel parsing library

**No longer needed:**
- Standalone `Fix Database.exe` (now integrated)
- Standalone `RVU Excel Checker.exe` (now integrated)

---

## Legacy Packaging Scripts

### Obsolete Scripts

These are no longer needed in v1.7:

- `package fix_database.bat` - Tool now integrated in main app
- `package RVU Excel Checker.bat` - Tool now integrated in main app

**Keep for reference** but don't use for v1.7 distribution.

---

## Automation (Future)

### Continuous Integration

For future CI/CD pipeline:

```yaml
# Example GitHub Actions workflow
build:
  runs-on: windows-latest
  steps:
    - uses: actions/checkout@v2
    - uses: actions/setup-python@v2
    - run: pip install pyinstaller PyYAML matplotlib numpy openpyxl
    - run: cd packaging && package RVUCounter.bat
    - uses: actions/upload-artifact@v2
      with:
        name: RVU Counter
        path: packaging/RVU Counter.exe
```

---

## Support

**Build Issues:**
- Check `packaging/build/` logs (if build fails)
- Verify all dependencies installed: `pip list`
- Test Python imports: `python -c "import src.main"`

**Runtime Issues:**
- Check `logs/rvu_counter.log` after running exe
- Test in clean environment (no Python installed)
- Verify all files bundle correctly

**Questions:**
- See `IMPLEMENTATION_COMPLETE.md` for technical details
- See `DISTRIBUTION_GUIDE.md` for distribution help

---

## Quick Reference

**To package:**
```batch
cd packaging
package RVUCounter.bat
```

**To test:**
```batch
RVU Counter.exe
```

**To distribute:**
```batch
# Upload to GitHub releases
# Name asset: "RVU Counter.exe"
# Tag: v1.7
```

---

**Packaging Guide Version:** 1.0  
**For RVU Counter:** v1.7  
**Last Updated:** December 19, 2025





