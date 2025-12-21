# What's New in RVU Counter v1.7.5

## 🚀 Major Features

### Auto-Update System
- **One-Click Updates**: Check for new versions automatically on startup
- **No More Manual Downloads**: Updates download and install seamlessly
- **Update Notification**: Small "Update!" button appears when new version is ready
- **Manual Restart**: After downloading, you'll be prompted to restart the app when ready

### Silent RVU Rules Auto-Update (NEW!)
- **Automatic Rule Updates**: RVU values update automatically in the background
- **Completely Silent**: No notifications, no user action needed
- **Lightweight**: Downloads only the ~50KB yaml file (not the entire app)
- **Always Current**: Checks for rule updates every time you launch the app
- **Version Tracked**: RVU rules have their own version number separate from the app
- **Instant Corrections**: RVU value fixes reach all users within seconds of being published
- **Bandwidth Friendly**: ~50KB vs ~100MB for a full app update

### Integrated Tools
- **Database Repair Tool**: Scan and fix RVU mismatches directly from the app
- **Excel Payroll Checker**: Compare Excel files with RVU rules to find discrepancies
- **Easy Access**: Click the wrench icon (🔧) in the top right corner

### What's New Viewer
- **In-App Release Notes**: See what's new directly in the application
- **Access**: Click the "?" button in the Settings window

### Improved Data Organization
- **Separated Settings**: User preferences now separate from RVU rules
- **Cleaner Folder Structure**: 
  - `data/` - Your database and records
  - `settings/` - Your personal preferences
  - `helpers/` - Update tools
  - `logs/` - Application logs
- **Faster Rule Updates**: RVU classification rules now update automatically without app restart

## 🔧 Bug Fixes & Improvements

### RVU Classification Enhancements
- ✅ Fixed: "CT Abdomen" now correctly classified as 1.0 RVU (not CT AP)
- ✅ Fixed: "MR Brain Angiography" now correctly gets 2.3 RVU
- ✅ Fixed: Bilateral XR studies (AC joints, calcaneus, etc.) now get correct 0.6 RVU
- ✅ Fixed: "XR Abdomen Decubitus" no longer matches ultrasound
- ✅ New: "CT Outside Film Read" - 0.9 RVU
- ✅ New: "XR Scanogram" - 1.0 RVU
- ✅ New: "MRI Hip Bilateral" - 3.5 RVU
- ✅ New: "CT Triple Spine" (C+T+L) - 5.25 RVU
- ✅ New: "CT Head Face and Cervical" - 2.9 RVU
- ✅ New: "CTA Lower Extremity" - 1.75 RVU
- ✅ Updated: "CTA Chest + CT AP" - 3.0 RVU (was 2.68)
- ✅ Updated: "CT Extremity Bilateral" - 1.0 RVU (was 2.0)

### Update System Improvements
- ✅ Fixed: Removed startup delay - app launches instantly
- ✅ Fixed: Download progress dialog now fully respects dark mode
- ✅ Fixed: Update process no longer has DLL loading errors
- ✅ Improved: Manual restart after updates ensures clean process

### Performance & Stability
- Improved startup speed with new settings architecture
- Better error handling for file operations
- Enhanced logging for troubleshooting
- Background thread for yaml updates doesn't slow down startup

## 📋 Two Types of Updates

### Full Application Updates (Manual)
When code, features, or UI changes:
1. See "Update!" button in app
2. Click to download (~100MB)
3. Wait for download to complete
4. Manually restart the app when prompted
5. New version is active!

### RVU Rules Updates (Automatic & Silent)
When RVU values are corrected:
1. Launch the app normally
2. Background thread checks GitHub (~1 second)
3. If new rules exist, downloads silently (~50KB)
4. App uses new values immediately
5. You never see anything - completely transparent!

## 📋 Migration Notes

### First Launch (v1.7+)
When you first run v1.7+, the app will automatically:
1. Create the new folder structure (`data/`, `settings/`, `helpers/`, `logs/`)
2. Move your database to `data/rvu_records.db`
3. Split your settings into user preferences and RVU rules
4. Keep a backup of your old `rvu_settings.yaml` file (renamed to `.migrated`)

**This is a one-time process and happens automatically. No action needed!**

---

**Thank you for using RVU Counter!**

For questions or issues, contact the developer or check the documentation folder.
