# 📦 RVU Counter - Distribution Guide

**Version**: 1.7+  
**Date**: December 2025  
**Audience**: Administrators deploying RVU Counter to users

---

## 📋 Overview

This guide explains how to distribute RVU Counter to end users, whether they're installing for the first time or upgrading from a previous version.

---

## 🎯 Distribution Options

### Option 1: Automatic Installation (Recommended)

**What to send users:**
1. `Install_or_Upgrade_RVU_Counter.bat`
2. `DISTRIBUTION_README.txt`

**How it works:**
- User places batch file in desired folder (e.g., Desktop)
- User runs batch file
- Script downloads latest version from GitHub automatically
- Script handles fresh install OR upgrade intelligently
- Database and settings preserved for upgrades
- Empty database created for fresh installs

**Advantages:**
- Always gets latest version
- One script for all scenarios
- Automatic backup of user data
- No manual file copying needed
- Clear status messages
- Error handling included

**Requirements:**
- Internet connection
- PowerShell (built into Windows 10+)
- No admin rights needed

---

### Option 2: Manual Installation (Alternative)

**What to send users:**
1. `RVU Counter.exe` (the latest build)
2. Basic instructions (see below)

**Instructions for users:**
1. Create a folder for RVU Counter
2. Copy `RVU Counter.exe` to that folder
3. Run it - folders and database created automatically

**For upgrades:**
1. Replace old `RVU Counter.exe` with new version
2. Run it - migration happens automatically
3. Database and settings preserved

**Advantages:**
- Simple and direct
- No script needed
- Works offline (if you provide the exe)

**Disadvantages:**
- Manual file management
- No automatic backups
- Users must track versions themselves

---

## 📧 Sample Distribution Email

### For New Users (Fresh Install)

```
Subject: RVU Counter Installation - Radiology Productivity Tracking

Hi [Name],

Attached are the files to install RVU Counter, a tool for tracking your
radiology productivity and RVU metrics in real-time.

INSTALLATION STEPS:
1. Create a folder on your Desktop called "RVU Counter"
2. Save the attached files to that folder
3. Double-click: Install_or_Upgrade_RVU_Counter.bat
4. Follow the on-screen prompts
5. The app will launch automatically when installation completes

ATTACHED FILES:
- Install_or_Upgrade_RVU_Counter.bat (Installation script)
- DISTRIBUTION_README.txt (Detailed instructions)

The installation script will:
- Download the latest version from GitHub
- Create necessary folders and settings
- Set up an empty database for you to begin tracking

AFTER INSTALLATION:
You'll see RVU Counter.exe in your folder. You can:
- Pin it to taskbar for quick access
- Create a desktop shortcut
- Launch it whenever you start a shift

SYSTEM REQUIREMENTS:
- Windows 10 or later
- Internet connection (for initial download)
- No administrator privileges required

If you encounter any issues, refer to DISTRIBUTION_README.txt or
contact me for assistance.

Best regards,
[Your Name]
```

---

### For Existing Users (Upgrade)

```
Subject: RVU Counter v1.7 Update - New Features Available

Hi [Name],

A new version of RVU Counter (v1.7) is now available with exciting new
features including auto-updates, integrated tools, and improved accuracy.

UPGRADE STEPS:
1. Close RVU Counter if it's running
2. Go to your RVU Counter folder (where you currently have the exe)
3. Save the attached Install_or_Upgrade_RVU_Counter.bat to that folder
4. Double-click the batch file
5. Follow the on-screen prompts

ATTACHED FILES:
- Install_or_Upgrade_RVU_Counter.bat (Upgrade script)
- DISTRIBUTION_README.txt (Detailed instructions)

The upgrade script will:
- Automatically detect your existing installation
- Back up your database and settings
- Download the latest version
- Preserve ALL your data (records, shifts, settings)
- Launch the upgraded application

YOUR DATA IS SAFE:
✓ Automatic backup created before upgrade
✓ All records and shifts preserved
✓ Settings and preferences maintained
✓ Window positions remembered

WHAT'S NEW IN v1.7:
✨ Auto-Update System - One-click future updates
🛠️ Integrated Tools - Database repair and Excel checker
📄 What's New Viewer - See release notes after updates
📁 Better Organization - Clean folder structure
🔧 19 RVU Fixes - Improved classification accuracy

AFTER UPGRADE:
When you launch v1.7, a "What's New" window will appear showing all
the new features. Future updates will be even easier - just click the
"✨ Update Available" button when it appears.

Questions? Check DISTRIBUTION_README.txt or contact me.

Best regards,
[Your Name]
```

---

## 🔧 Deployment Scenarios

### Scenario 1: Individual User (Email)

**Best Method**: Automatic Installation (Option 1)

**Send:**
- `Install_or_Upgrade_RVU_Counter.bat`
- `DISTRIBUTION_README.txt`

**Email template**: Use "New Users" or "Upgrade" template above

---

### Scenario 2: Multiple Users (Network Share)

**Setup:**
1. Place these files on a network share:
   - `Install_or_Upgrade_RVU_Counter.bat`
   - `DISTRIBUTION_README.txt`

2. Send users the network path

3. Users copy files to their local machine and run

**Note**: Don't run directly from network share - copy to local first

---

### Scenario 3: IT-Managed Deployment

**For IT Departments:**

1. **Create deployment package:**
   - `RVU Counter.exe`
   - `Install_or_Upgrade_RVU_Counter.bat`
   - `DISTRIBUTION_README.txt`

2. **Deploy via:**
   - Group Policy (user-level, not machine-level)
   - SCCM/Intune as user app
   - Network share with instructions
   - Email distribution

3. **Target location:**
   - User profile: `%LOCALAPPDATA%\RVU_Counter\`
   - User Desktop: `%USERPROFILE%\Desktop\RVU_Counter\`
   - User Documents: `%USERPROFILE%\Documents\RVU_Counter\`

4. **No admin rights needed** - App is fully portable

---

### Scenario 4: Trial/Demo Installation

**For evaluation:**

1. Send just the exe: `RVU Counter.exe`
2. User runs it from any folder
3. Folders created automatically
4. Pre-populated with sample data (if desired)

**To pre-populate with sample data:**
- Include a sample `rvu_records.db` in `data/` folder
- Include sample settings in `settings/` folder

---

## 📋 Pre-Deployment Checklist

### Before Distributing

- [ ] Test the distribution script yourself
- [ ] Verify it works for both fresh install and upgrade
- [ ] Test with actual user data (not just empty database)
- [ ] Check that all folders are created correctly
- [ ] Verify GitHub download works
- [ ] Test backup and recovery
- [ ] Review logs for any errors

### GitHub Release Preparation

- [ ] Create release on GitHub: `erichter2018/RVU-Releases`
- [ ] Tag version: `v1.7`
- [ ] Upload: `RVU Counter.exe` as release asset
- [ ] Name asset exactly: `RVU Counter.exe`
- [ ] Copy release notes from `WHATS_NEW_v1.7.md`
- [ ] Mark as "Latest Release"

### Documentation

- [ ] Include DISTRIBUTION_README.txt
- [ ] Optional: Include WHATS_NEW_v1.7.md
- [ ] Optional: Include user manual (if created)

---

## 🛡️ Data Safety

### Automatic Backups

The installation script creates timestamped backups:
- Format: `backup_YYYYMMDD_HHMMSS/`
- Contains:
  - `rvu_records.db.backup`
  - `rvu_settings.yaml.backup` (if exists)
  - `user_settings.yaml.backup` (if exists)

### Manual Recovery

If something goes wrong:
```batch
1. Navigate to backup folder
2. Copy rvu_records.db.backup to data\rvu_records.db
3. Copy settings files to settings\ folder
4. Relaunch application
```

### Prevention

- Script checks for running processes before upgrade
- Creates backups before making changes
- Preserves existing files on failure
- Logs all operations

---

## 🧪 Testing Recommendations

### Before Mass Deployment

**Test with 3 scenarios:**

1. **Fresh Install**
   - New computer or new folder
   - Run script
   - Verify empty database created
   - Verify default settings applied

2. **Upgrade from v1.6**
   - Existing v1.6 installation
   - Run script
   - Verify data preserved
   - Verify settings migrated
   - Verify no errors

3. **Upgrade from v1.5 or earlier**
   - Very old installation
   - Run script
   - Verify JSON→SQLite migration works
   - Verify all data preserved

**Validation Steps:**
- Check total record count before and after
- Check shift count before and after
- Verify settings (dark mode, role, etc.)
- Check window positions
- Review logs for errors

---

## 🚨 Troubleshooting

### Common Issues

**"Download failed"**
- **Cause**: No internet or GitHub unavailable
- **Solution**: Manually download exe from GitHub, place in folder, run script again

**"PowerShell not found"**
- **Cause**: Very old Windows version
- **Solution**: Use Manual Installation (Option 2)

**"Cannot create folders"**
- **Cause**: Insufficient permissions
- **Solution**: Run from user profile location (Desktop, Documents)

**"Database not preserved"**
- **Cause**: Database was in unexpected location
- **Solution**: Check backup_YYYYMMDD_HHMMSS folder, manually restore

**"Application won't launch"**
- **Cause**: Windows Defender or antivirus blocking
- **Solution**: Add exception for RVU Counter.exe

---

## 📊 Rollout Strategy

### Phased Rollout (Recommended)

**Week 1: Pilot Group (2-3 users)**
- Select tech-savvy users
- Closely monitor their upgrade
- Gather feedback
- Fix any issues

**Week 2: Expand (10-20% of users)**
- Roll out to broader group
- Monitor for common issues
- Update documentation if needed

**Week 3+: General Availability**
- Roll out to all users
- Provide support documentation
- Monitor for issues

### Rollback Plan

If major issues discovered:
1. Users can revert using backup folder
2. Copy backup database to data/
3. Run old version exe
4. Report issues to you

---

## 📞 User Support

### Common User Questions

**Q: Will I lose my data?**
A: No. The script backs up everything before making changes. For upgrades, all records, shifts, and settings are preserved.

**Q: Do I need admin rights?**
A: No. RVU Counter runs as a normal user application.

**Q: Where should I install it?**
A: Anywhere in your user profile: Desktop, Documents, or a custom folder. Avoid Program Files.

**Q: Can I move it later?**
A: Yes. Just move the entire folder. All data stays together.

**Q: What if the download fails?**
A: Manually download "RVU Counter.exe" from the GitHub releases page and place it in your folder.

**Q: How do I update in the future?**
A: v1.7 includes auto-update! When an update is available, click the "✨ Update Available" button.

---

## 🔐 Security Considerations

### Download Safety

- Downloads from official GitHub repository only
- HTTPS encrypted download
- No third-party servers
- No telemetry or tracking

### Data Privacy

- All data stored locally (never cloud-uploaded)
- Optional OneDrive backup (user controlled)
- No analytics sent to developer
- No internet required after installation

### Antivirus

- RVU Counter.exe may trigger SmartScreen on first run
- This is normal for new executables
- Click "More info" → "Run anyway"
- Add to antivirus exceptions if needed

---

## 📈 Post-Deployment Monitoring

### What to Track

**First Week:**
- Number of successful installations
- Number of successful upgrades
- Any reported errors or issues
- User feedback on new features

**Ongoing:**
- Update adoption rate (v1.7 → v1.8, etc.)
- Tool usage (Database Repair, Excel Checker)
- Performance issues
- Feature requests

### Metrics to Collect

- Installation success rate
- Upgrade success rate
- Average installation time
- Common error patterns
- User satisfaction

---

## 🎓 Training Materials

### Quick Start Guide (For Users)

**5-Minute Setup:**
1. Run the installation script
2. Launch RVU Counter
3. Click "Settings" → configure your role (Assoc/Partner)
4. Click "Start Shift" when you begin reading
5. Studies auto-captured from PowerScribe/Mosaic

**Key Features to Highlight:**
- Real-time RVU tracking
- Pace car (goal tracking)
- Hourly breakdown
- Compensation calculator
- Statistics and analytics

### New in v1.7 (Train Users On)

**Tools Button:**
- Database Repair: Fix mismatches in old records
- Excel Checker: Verify payroll accuracy

**Help Button (?):**
- Access release notes
- See what's new

**Auto-Updates:**
- When "✨ Update Available" appears, click it
- One-click update process
- App restarts automatically

---

## 📝 Documentation to Provide

### Minimum (Required)

1. `DISTRIBUTION_README.txt` - Installation guide

### Recommended (If Available)

1. `DISTRIBUTION_README.txt` - Installation guide
2. User manual PDF (if you created one)
3. Quick reference card
4. Contact info for support

### Optional (For Power Users)

1. Full documentation/ folder
2. Technical design documents
3. Testing guides

---

## 🚀 Quick Distribution Commands

### Create Distribution Package (ZIP)

**PowerShell:**
```powershell
# Create distribution folder
New-Item -ItemType Directory -Path ".\RVU_Counter_v1.7_Distribution" -Force

# Copy distribution files
Copy-Item "Install_or_Upgrade_RVU_Counter.bat" ".\RVU_Counter_v1.7_Distribution\"
Copy-Item "DISTRIBUTION_README.txt" ".\RVU_Counter_v1.7_Distribution\"

# Optional: Include the exe directly (for offline installation)
Copy-Item "RVU Counter.exe" ".\RVU_Counter_v1.7_Distribution\" -ErrorAction SilentlyContinue

# Create ZIP file
Compress-Archive -Path ".\RVU_Counter_v1.7_Distribution\*" -DestinationPath "RVU_Counter_v1.7_Install.zip" -Force

Write-Host "✅ Distribution package created: RVU_Counter_v1.7_Install.zip"
```

### Email the Package

**Small Deployment:**
- Attach ZIP file to email
- Use email template from this guide

**Large Deployment:**
- Upload to shared network drive
- Send link in email
- Or use SharePoint/Teams file sharing

---

## 🎯 Success Criteria

### A Successful Deployment Means:

**For Fresh Installs:**
- ✅ User runs script
- ✅ Latest version downloads
- ✅ Folders created automatically
- ✅ Application launches
- ✅ User can start a shift and track studies

**For Upgrades:**
- ✅ User runs script
- ✅ Backup created automatically
- ✅ Latest version downloads
- ✅ All existing data preserved
- ✅ Settings maintained
- ✅ Window positions remembered
- ✅ Application launches with data intact
- ✅ "What's New" appears automatically

**Quality Metrics:**
- 95%+ success rate
- <5 minutes average installation time
- Zero data loss incidents
- Minimal support tickets

---

## 🆘 Support Plan

### Tier 1: Self-Service

**Provide to users:**
- DISTRIBUTION_README.txt (covers common issues)
- FAQ document (if you create one)
- Link to GitHub releases (for manual download)

### Tier 2: Email Support

**Common support requests:**
1. Download failed → Manual download link
2. Script won't run → Check PowerShell availability
3. Data not preserved → Check backup folder, restore manually
4. App won't launch → Check logs/rvu_counter.log

### Tier 3: Remote Assistance

**For complex issues:**
- TeamViewer/remote desktop
- Check logs together
- Verify database integrity
- Reinstall if needed

---

## 🔄 Update Strategy (v1.7 → v1.8+)

### For Future Updates

**Good news**: After v1.7 is installed, users don't need the installation script anymore!

**Future update process:**
1. You create GitHub release (v1.8, v1.9, etc.)
2. User launches RVU Counter
3. App detects update automatically
4. User sees "✨ Update Available" notification
5. User clicks it
6. App downloads and installs automatically
7. App restarts with new version

**Your role:**
- Create GitHub releases
- Upload `RVU Counter.exe` as asset
- Write release notes

**User role:**
- Click "Update Available" button
- Wait for download and restart
- That's it!

---

## 📦 Distribution Package Checklist

### Essential Files

- [ ] `Install_or_Upgrade_RVU_Counter.bat`
- [ ] `DISTRIBUTION_README.txt`

### Optional Files (For Offline Install)

- [ ] `RVU Counter.exe` (if distributing offline)
- [ ] User manual PDF
- [ ] Quick reference card

### Supporting Documentation

- [ ] Installation instructions
- [ ] Contact information for support
- [ ] Link to GitHub releases page

---

## 🎓 Best Practices

### Do's ✅

- ✅ Test the script yourself before distributing
- ✅ Create GitHub release first (so download works)
- ✅ Provide clear installation instructions
- ✅ Offer support during initial rollout
- ✅ Monitor for issues in first week
- ✅ Keep backups of distribution packages

### Don'ts ❌

- ❌ Don't skip testing before distribution
- ❌ Don't distribute without GitHub release ready
- ❌ Don't forget to include DISTRIBUTION_README.txt
- ❌ Don't tell users to run from network share directly
- ❌ Don't distribute without support plan

---

## 📊 Deployment Timeline

### Recommended Schedule

**Day 1: Preparation**
- Create GitHub release (v1.7)
- Test installation script
- Prepare email templates
- Identify pilot users

**Day 2-3: Pilot Deployment**
- Deploy to 2-3 test users
- Monitor closely
- Gather feedback
- Fix any issues

**Day 4-5: Broader Rollout**
- Deploy to 10-20% of users
- Monitor for patterns
- Update documentation if needed

**Week 2+: General Availability**
- Deploy to all users
- Provide support as needed
- Monitor adoption rate

---

## 🎉 Conclusion

The distribution system is designed to be:
- **Simple**: Users run one script
- **Safe**: Automatic backups before changes
- **Flexible**: Works for fresh install or upgrade
- **Automatic**: Downloads latest version
- **Reliable**: Error handling and recovery

**Key advantages:**
- One script for all scenarios
- Always gets latest version
- Data preservation guaranteed
- User-friendly with clear messages

**After initial v1.7 deployment:**
Future updates are even easier with built-in auto-update system!

---

## 📞 Contact Information

For questions about distribution or deployment:
- Review this guide
- Check DISTRIBUTION_README.txt
- Consult IMPLEMENTATION_COMPLETE.md

For technical implementation details:
- See `documentation/AUTO_UPDATE_DESIGN.md`
- See `documentation/TESTING_GUIDE_v1.7.md`

---

**Distribution guide version**: 1.0  
**Last updated**: December 19, 2025  
**For RVU Counter**: v1.7+

---

**Ready to distribute!** Use `Install_or_Upgrade_RVU_Counter.bat` + `DISTRIBUTION_README.txt`

