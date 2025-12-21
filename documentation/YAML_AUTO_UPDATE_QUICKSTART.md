# YAML Auto-Update System - Quick Reference

## What Was Built

A **lightweight, silent auto-update system** specifically for `rvu_rules.yaml` that:

- ✅ Runs automatically on every app startup (background thread)
- ✅ Downloads only the yaml file (~50KB) from GitHub
- ✅ Completely silent - users never see it happen
- ✅ No restart required for the check (values load at startup)
- ✅ Falls back gracefully if GitHub is unreachable

## How to Update RVU Values (For You)

### The Simple 3-Step Process:

1. **Edit** `settings/rvu_rules.yaml`
   - Change RVU values, add CPT codes, fix classifications
   - Bump version in header: `# Version: 1.0` → `# Version: 1.1`

2. **Commit to GitHub**
   ```bash
   git add settings/rvu_rules.yaml
   git commit -m "Fix RVU values for MRI sequences"
   git push origin main
   ```

3. **Done!**
   - No packaging
   - No exe upload
   - Users automatically get it next time they launch the app

## What Users Experience

**Nothing!** It's completely silent and automatic:

1. User launches RVU Counter
2. App checks GitHub in background (~1 second)
3. If newer yaml exists, downloads it silently
4. App uses new values
5. User never knows it happened (except values are correct now!)

## When to Use This vs Full Update

### YAML Update (Fast & Silent)
Use for:
- Fixing RVU values
- Adding/correcting CPT codes
- Updating study classifications

### Full App Update (Requires Packaging)
Use for:
- Code changes
- New features
- Bug fixes
- UI changes

## Technical Implementation

**New Files Created:**
- `src/core/yaml_update_manager.py` - Handles version checking and downloading
- `documentation/YAML_UPDATE_SYSTEM.md` - Full technical documentation

**Modified Files:**
- `src/main.py` - Starts background yaml check on launch
- `settings/rvu_rules.yaml` - Added version header
- `src/core/__init__.py` - Exports YamlUpdateManager

**How It Works:**
1. Yaml file now has version in header: `# Version: 1.0`
2. On startup, background thread compares local vs GitHub version
3. If GitHub version is newer, downloads to `settings/rvu_rules.yaml`
4. App already prioritizes settings folder over bundled yaml
5. All happens in <1 second, doesn't slow startup

## GitHub URL Structure

The system pulls from:
```
https://raw.githubusercontent.com/erichter2018/e_tools/main/settings/rvu_rules.yaml
```

Make sure your GitHub repository name is `e_tools` (it is based on your workspace).

## Next Steps

Ready to package and release v1.7 with this new feature? Just say the word!

The yaml update system will work immediately once v1.7 is distributed. Then any future yaml edits you push to GitHub will automatically reach all users.

---

**Created:** December 16, 2025

