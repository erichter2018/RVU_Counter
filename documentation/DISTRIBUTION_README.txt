================================================================================
RVU COUNTER - INSTALLATION GUIDE
================================================================================

Welcome! This guide will help you install or upgrade RVU Counter.

================================================================================
WHAT YOU RECEIVED
================================================================================

You should have received:
1. Install_or_Upgrade_RVU_Counter.bat - Installation script
2. This README file

================================================================================
QUICK START
================================================================================

OPTION 1: Fresh Installation (New User)
----------------------------------------
1. Create a new folder anywhere on your computer
   Example: C:\Users\YourName\Desktop\RVU_Counter\

2. Copy Install_or_Upgrade_RVU_Counter.bat to that folder

3. Double-click Install_or_Upgrade_RVU_Counter.bat

4. Follow the prompts - it will:
   - Download the latest version from GitHub
   - Create necessary folders
   - Set up an empty database
   - Launch the application

OPTION 2: Upgrade Existing Installation
----------------------------------------
1. Locate your existing RVU Counter folder
   (The folder that contains your current RVU Counter.exe)

2. Copy Install_or_Upgrade_RVU_Counter.bat to that folder

3. Double-click Install_or_Upgrade_RVU_Counter.bat

4. Follow the prompts - it will:
   - Detect your existing installation
   - Backup your database and settings
   - Download the latest version
   - Preserve all your data
   - Launch the upgraded application

================================================================================
WHAT THE SCRIPT DOES
================================================================================

Automatic Detection:
- Detects if this is a fresh install or upgrade
- Handles both scenarios appropriately

For Upgrades:
✓ Backs up your database (rvu_records.db)
✓ Backs up your settings (rvu_settings.yaml)
✓ Preserves all records and shifts
✓ Maintains window positions and preferences
✓ Cleans up old version executables

For Fresh Installs:
✓ Downloads latest version from GitHub
✓ Creates folder structure (data/, settings/, logs/, helpers/)
✓ Sets up empty database
✓ Uses default settings

Always:
✓ Downloads the latest version automatically
✓ Creates timestamped backups
✓ Provides clear status messages
✓ Offers to launch the application

================================================================================
SYSTEM REQUIREMENTS
================================================================================

- Windows 10 or later
- Internet connection (for downloading latest version)
- No administrator privileges required
- PowerShell (pre-installed on Windows 10+)

================================================================================
FOLDER STRUCTURE
================================================================================

After installation, your folder will contain:

RVU Counter/
├── RVU Counter.exe           (The application)
├── data/                     (Your database files)
│   └── rvu_records.db
├── settings/                 (Your preferences and RVU rules)
│   ├── user_settings.yaml
│   └── rvu_rules.yaml
├── logs/                     (Application logs)
│   └── rvu_counter.log
├── helpers/                  (Update scripts)
└── documentation/            (Guides and release notes)

================================================================================
UPGRADING FROM v1.6 OR EARLIER
================================================================================

The script automatically handles upgrades from any previous version:

From v1.6:
- Database moved from root to data/ folder
- Settings split into user_settings.yaml and rvu_rules.yaml
- All data preserved

From v1.5 or earlier:
- JSON records migrated to SQLite database
- All data preserved

No manual intervention required!

================================================================================
TROUBLESHOOTING
================================================================================

Script says "Download failed":
→ Manually download "RVU Counter.exe" from:
  https://github.com/erichter2018/RVU-Releases/releases/latest
→ Place it in your folder and run the script again

Application won't launch:
→ Check logs/rvu_counter.log for errors
→ Verify RVU Counter.exe exists in the folder
→ Try running as administrator (usually not needed)

Data not preserved after upgrade:
→ Check the backup_YYYYMMDD_HHMMSS folder
→ Your original database is there: rvu_records.db.backup
→ Copy it to data/rvu_records.db if needed

================================================================================
GETTING HELP
================================================================================

For issues or questions:
1. Check documentation/ folder for guides
2. Review logs/rvu_counter.log for error messages
3. Contact your RVU Counter administrator

================================================================================
WHAT'S NEW IN v1.7
================================================================================

✨ Auto-Update System
   - One-click updates via GitHub
   - No more manual downloads after this initial setup

🛠️ Integrated Tools
   - Database Repair tool (scan and fix mismatches)
   - Excel Checker tool (verify payroll files)
   - Access via "Tools" button

📄 What's New Viewer
   - Automatic release notes after updates
   - Access via "?" help button

📁 Clean Architecture
   - New folder structure (data/, settings/, logs/)
   - Automatic migration from older versions

🔧 19 RVU Classification Fixes
   - Improved accuracy for CT Abdomen, Bilateral studies, etc.
   - Better Excel payroll matching

================================================================================
FUTURE UPDATES
================================================================================

After this initial installation, RVU Counter includes a built-in auto-update
system. When a new version is available:

1. A notification will appear: "✨ Update Available"
2. Click it to download and install
3. The app will restart automatically
4. Your data is always preserved

You won't need to use this installation script again unless you're setting up
on a new computer or recovering from a complete reinstall.

================================================================================
DATA SAFETY
================================================================================

Your data is safe:
✓ Automatic backups before each upgrade
✓ Database never overwritten without backup
✓ Settings preserved across versions
✓ Timestamped backup folders for recovery

Backup Location:
- Created in your RVU Counter folder
- Named: backup_YYYYMMDD_HHMMSS
- Contains: database and settings files

Recommendation:
- Keep at least 2-3 backup folders
- Older backups can be deleted when confident upgrade succeeded

================================================================================
OPTIONAL: MANUAL INSTALLATION
================================================================================

If you prefer not to use the script:

1. Download "RVU Counter.exe" from GitHub releases
2. Place it in a new or existing folder
3. Run it - folders and database created automatically
4. Settings migration happens on first launch

The script just automates these steps and adds safety features.

================================================================================
NOTES FOR IT ADMINISTRATORS
================================================================================

Deployment:
- Can be deployed via network share or email
- No admin rights required for users
- Portable - no registry or system changes
- Can run from any location (Desktop, Documents, etc.)

Silent/Unattended Installation:
- Not currently supported in this version
- Script requires user confirmation at key steps
- Future versions may support silent mode

Group Policy:
- Can be deployed as a user-level application
- No machine-level policies needed
- Users can self-install in their profile

================================================================================
LICENSE & SUPPORT
================================================================================

RVU Counter is proprietary software for radiology practice management.

For support, licensing, or deployment questions, contact your
RVU Counter administrator or vendor.

================================================================================

Thank you for using RVU Counter!

Version: 1.7+
Last Updated: December 2025

================================================================================

