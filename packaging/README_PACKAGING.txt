================================================================================
RVU COUNTER v1.7 - PACKAGING FOLDER
================================================================================

This folder contains scripts and tools for packaging RVU Counter into a
distributable executable.

================================================================================
QUICK START
================================================================================

To package RVU Counter v1.7:

1. Verify build environment:
   verify_build.bat

2. If verification passes, package:
   package RVUCounter.bat

3. Result: RVU Counter.exe (ready for distribution)

================================================================================
FILES IN THIS FOLDER
================================================================================

ACTIVE SCRIPTS (Use these):
---------------------------
✓ package RVUCounter.bat         - Package main application
✓ verify_build.bat               - Verify all files present before packaging
✓ PACKAGING_GUIDE.md             - Complete packaging documentation
✓ rvu_settings.yaml              - Template config (gets bundled)

LEGACY SCRIPTS (Obsolete in v1.7):
-----------------------------------
○ package fix_database.bat       - No longer needed (tool integrated)
○ package RVU Excel Checker.bat  - No longer needed (tool integrated)
○ Fix Database.exe               - Old standalone (now in Tools)
○ RVU Excel Checker.exe          - Old standalone (now in Tools)

OUTPUT:
-------
• RVU Counter.exe                - Final executable (created after packaging)
• RVU Counter 1.6.exe            - Previous version (for reference)

================================================================================
WHAT'S NEW IN v1.7 PACKAGING
================================================================================

NEW INCLUSIONS:
• helpers/updater.bat            - Auto-update sidecar script
• documentation/WHATS_NEW_v1.7.md - Release notes
• src/ui/tools_window.py         - Integrated tools UI
• src/ui/whats_new_window.py     - What's New viewer
• src/core/update_manager.py     - Update system
• src/logic/database_repair.py   - Database repair backend
• src/logic/excel_checker.py     - Excel checker backend
• openpyxl dependency            - Excel file parsing

NO LONGER NEEDED:
• Standalone Fix Database tool (now integrated)
• Standalone Excel Checker tool (now integrated)

================================================================================
PACKAGING WORKFLOW
================================================================================

Step 1: VERIFY
--------------
Run: verify_build.bat

This checks:
✓ All source files present
✓ All v1.7 new files present
✓ Python dependencies installed
✓ RVU classification fixes applied

Step 2: PACKAGE
---------------
Run: package RVUCounter.bat

This:
✓ Bundles all source code
✓ Includes helpers/updater.bat
✓ Includes documentation files
✓ Creates single executable
✓ Cleans up build artifacts

Step 3: TEST
------------
Run: RVU Counter.exe

Test:
✓ Application launches
✓ Tools button works
✓ ? button works
✓ No missing file errors

Step 4: DISTRIBUTE
------------------
Upload to GitHub releases:
• Repository: erichter2018/RVU-Releases
• Tag: v1.7
• Asset name: "RVU Counter.exe" (exact name required)

================================================================================
REQUIREMENTS
================================================================================

System:
• Windows 10 or later
• Python 3.8+

Python Packages:
• pyinstaller
• PyYAML
• matplotlib
• numpy
• pywinauto
• openpyxl (NEW in v1.7)

Install all:
pip install pyinstaller PyYAML matplotlib numpy pywinauto openpyxl

================================================================================
TROUBLESHOOTING
================================================================================

"Module not found" during packaging:
→ Check verify_build.bat output
→ Install missing dependencies

"File not found" errors:
→ Run verify_build.bat
→ Ensure all v1.7 files present

Executable won't run:
→ Test on clean machine (no Python)
→ Check antivirus exceptions
→ Review logs/rvu_counter.log

================================================================================
FILE SIZE
================================================================================

Expected: ~30-40 MB

This includes:
• Python runtime
• All dependencies (matplotlib, numpy, openpyxl, etc.)
• All source code
• Documentation
• Helper scripts

================================================================================
DISTRIBUTION
================================================================================

After packaging:

1. DON'T just send the exe to users
2. DO use the distribution system:
   - Run: Create_Distribution_Package.ps1
   - Sends: Install_or_Upgrade_RVU_Counter.bat + README
   - See: DISTRIBUTION_GUIDE.md

3. OR upload to GitHub releases:
   - Users auto-update from there
   - See: PACKAGING_GUIDE.md

================================================================================
VERSION HISTORY
================================================================================

v1.7 (Current):
• Auto-update system
• Integrated tools (DB Repair + Excel Checker)
• What's New viewer
• 19 RVU classification fixes
• New folder structure
• Split settings (user_settings + rvu_rules)

v1.6:
• Statistics improvements
• Refactored source structure
• Backup system

================================================================================
DOCUMENTATION
================================================================================

Detailed guides:
• PACKAGING_GUIDE.md          - Complete packaging documentation
• ..\DISTRIBUTION_GUIDE.md    - How to distribute to users
• ..\IMPLEMENTATION_COMPLETE.md - Technical implementation details

================================================================================
SUPPORT
================================================================================

Build issues:
• Check verify_build.bat output
• See PACKAGING_GUIDE.md
• Review PyInstaller logs in build/ folder

Runtime issues:
• Test executable on clean machine
• Check logs/rvu_counter.log
• Verify all files bundled correctly

Questions:
• See PACKAGING_GUIDE.md for comprehensive help

================================================================================

Quick commands:

Verify: verify_build.bat
Package: package RVUCounter.bat
Test: RVU Counter.exe

================================================================================



