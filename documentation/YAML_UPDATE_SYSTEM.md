# RVU Rules YAML Update System

## Overview

The RVU Counter now has **two separate update mechanisms**:

1. **Full Application Updates** - For code changes, new features, bug fixes
2. **YAML-Only Updates** - For quick RVU value corrections (lightweight, silent)

## YAML Update System

### How It Works

- **Automatic & Silent** - Checks for yaml updates every time the app starts
- **Background Thread** - Doesn't slow down app startup
- **No User Action Needed** - Updates happen automatically
- **Instant Effect** - New RVU values are used immediately (no restart needed for the values themselves, but app loads yaml at startup)
- **No Full Exe Download** - Just downloads the ~50KB yaml file

### Version Tracking

The `rvu_rules.yaml` file contains version info in its header:

```yaml
# RVU Rules Configuration File
# Version: 1.0
# Last Updated: 2025-12-16
# This file is automatically updated from GitHub when newer versions are available
```

### Update Flow for Developers

#### To Update RVU Values:

1. **Edit Local YAML**
   - Open `settings/rvu_rules.yaml`
   - Make your RVU value corrections
   - Bump the version number in the header (e.g., `1.0` → `1.1`)
   - Update the "Last Updated" date

2. **Commit & Push to GitHub**
   ```bash
   git add settings/rvu_rules.yaml
   git commit -m "Update RVU values for CPT codes XYZ"
   git push origin main
   ```

3. **That's It!**
   - No packaging needed
   - No exe upload needed
   - Users get the update automatically next time they launch the app

### How Users Get Updates

**User Experience:**
1. User launches RVU Counter
2. Background thread silently checks GitHub for yaml version
3. If newer version exists, downloads it (~50KB, takes <1 second)
4. App uses the new RVU values immediately
5. User sees nothing - completely transparent!

**Logging:**
- Updates are logged to `logs/rvu_counter.log`
- Success: `"RVU rules automatically updated from GitHub"`
- No update needed: `"YAML is up to date"`

## Full Application Updates

Use this for:
- Code changes
- New features
- Bug fixes
- UI changes
- Database schema changes

**Process:**
1. Bump version in `src/__init__.py` and `src/core/config.py`
2. Run `packageRVUCounter.bat`
3. Upload exe to GitHub releases
4. Users see "Update Available!" button

## When to Use Which Update Type

### Use YAML Update (Lightweight)
- ✅ Fixing incorrect RVU values
- ✅ Adding new CPT code mappings
- ✅ Updating study classifications
- ✅ Correcting direct_lookups

### Use Full Application Update
- ✅ New features (pace car, statistics, etc.)
- ✅ Bug fixes in code
- ✅ UI improvements
- ✅ Database changes
- ✅ Performance improvements

## Technical Details

### Files Involved

- **`src/core/yaml_update_manager.py`** - Handles yaml updates
- **`src/main.py`** - Launches background yaml check on startup
- **`settings/rvu_rules.yaml`** - The rules file (version tracked)

### GitHub Integration

- **Source:** `https://raw.githubusercontent.com/erichter2018/e_tools/main/settings/rvu_rules.yaml`
- **Method:** Direct file download (no GitHub API needed)
- **Fallback:** If update fails, app uses existing/bundled yaml

### Load Priority

The app loads `rvu_rules.yaml` in this order:
1. **`settings/rvu_rules.yaml`** (local, potentially auto-updated) ← Highest priority
2. **Bundled yaml** (inside exe from PyInstaller) ← Fallback

This ensures auto-updates always take precedence!

## Testing

To test yaml updates manually:

```python
from src.core.yaml_update_manager import YamlUpdateManager

ym = YamlUpdateManager()
print(f"Local version: {ym.get_local_version()}")
print(f"Remote version: {ym.get_remote_version()}")

# Check and update
was_updated = ym.update_if_needed()
print(f"Updated: {was_updated}")
```

## Troubleshooting

**Q: What if GitHub is down?**
- App uses existing local yaml or bundled yaml
- No error shown to user
- Logged as warning

**Q: What if user's yaml is corrupted?**
- App will use bundled yaml as fallback
- Next update attempt will replace corrupted file

**Q: Can users manually edit their yaml?**
- Yes! Local yaml in settings/ folder
- But their changes will be overwritten on next auto-update
- Document this if users want custom RVU values

**Q: How do I disable auto-updates?**
- Currently no disable option (silent and harmless)
- Could add a setting if needed

## Example Workflow

**Scenario:** CPT code 70553 has wrong RVU value (2.3 should be 2.5)

**Old Way (Full Update):**
1. Edit yaml
2. Bump app version 1.7 → 1.7.1
3. Repackage entire exe (~100MB)
4. Upload to GitHub (~5 minutes)
5. Users download 100MB update
6. Users restart app

**New Way (YAML Update):**
1. Edit yaml: change `70553: 2.3` to `70553: 2.5`
2. Bump yaml version: `1.0` → `1.1`
3. `git commit && git push`
4. Done! Users get it automatically next launch
5. Takes ~1 second to download, completely silent

## Benefits

✅ **Fast corrections** - No packaging/uploading needed  
✅ **No user disruption** - Silent background updates  
✅ **Bandwidth efficient** - ~50KB vs ~100MB  
✅ **Always up-to-date** - Checked every launch  
✅ **Version controlled** - Git history tracks all changes  
✅ **Rollback friendly** - Just revert git commit  

---

**Last Updated:** December 16, 2025



