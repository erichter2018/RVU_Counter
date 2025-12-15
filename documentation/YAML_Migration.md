# YAML Migration Summary

**Date:** December 13, 2025  
**Migration:** Converted `rvu_settings.json` to `rvu_settings.yaml`

## Why YAML?

The RVU settings file had grown to over 1700 lines, making it difficult to read and maintain. YAML (YAML Ain't Markup Language) offers:

1. **Better Readability**: Cleaner syntax with less visual clutter (no quotes, fewer brackets)
2. **Smaller File Size**: 40.3% reduction (32KB → 19KB, 1706 lines → 1176 lines)
3. **Easier Editing**: More natural to read and edit manually
4. **Same Functionality**: Fully compatible, same data structure

## File Size Comparison

| Format | Size | Lines | Change |
|--------|------|-------|--------|
| JSON   | 32,103 bytes | 1,706 lines | Baseline |
| YAML   | 19,181 bytes | 1,176 lines | **-40.3%** |

## Readability Example

### JSON (Old Format):
```json
"classification_rules": {
  "CTA Brain and Neck": [
    {
      "required_keywords": [
        "CTA",
        "brain",
        "neck"
      ],
      "excluded_keywords": [
        "perfusion"
      ]
    }
  ]
}
```

### YAML (New Format):
```yaml
classification_rules:
  CTA Brain and Neck:
  - required_keywords:
    - CTA
    - brain
    - neck
    excluded_keywords:
    - perfusion
```

Much cleaner! No quotes on simple strings, less punctuation, easier to scan.

## What Changed

### Files Updated:
1. **RVUCounter.pyw** - Main application
   - Added `import yaml`
   - Changed file name from `.json` to `.yaml`
   - Updated `load_settings()` to use `yaml.safe_load()`
   - Updated `save()` to use `yaml.safe_dump()`

2. **check_rvu_excel_files.py** - Excel comparison tool
   - Added `import yaml`
   - Updated `load_rvu_settings()` function
   - Changed file references

3. **fix_database.py** - Database repair tool
   - Added `import yaml`
   - Updated `load_rvu_settings()` function
   - Changed file references

4. **Packaging Batch Files** (all 3 in `packaging/` folder)
   - `package RVUCounter.bat`
   - `package fix_database.bat`
   - `package RVU Excel Checker.bat`
   - Updated to bundle `rvu_settings.yaml` instead of `.json`

### Backup Created:
- `rvu_settings.json.pre_yaml_backup` - Original JSON file preserved

## Dependencies Added

**PyYAML** library is now required:
```bash
pip install pyyaml
```

This is automatically included when packaging executables with PyInstaller.

## Compatibility Notes

- The YAML file maintains **identical data structure** to the JSON file
- All existing functionality works the same way
- User settings, window positions, RVU tables, classification rules all preserved
- No changes to database structure or data format

## For Users

If you're running from source (not the exe):
1. Make sure PyYAML is installed: `pip install pyyaml`
2. The application will automatically use the new YAML file
3. Your old JSON file is backed up as `rvu_settings.json.pre_yaml_backup`

If you're using the packaged executable:
- Everything works the same, YAML file is bundled inside
- No action needed on your part

## For Developers

When editing `rvu_settings.yaml`:
- No quotes needed for simple strings
- Use 2-space indentation (standard YAML)
- Lists use `-` prefix
- Dictionaries use `key: value` format
- Comments start with `#`
- Be careful with indentation (YAML is whitespace-sensitive)

### Example Comment:
```yaml
rvu_table:
  # Brain imaging
  CT Brain: 0.9
  CT Brain with Contrast: 1.0
  MRI Brain: 2.3
```

## Rollback (if needed)

If you need to revert to JSON for any reason:

1. Rename backup: `rvu_settings.json.pre_yaml_backup` → `rvu_settings.json`
2. Revert code changes in Python files (use git)
3. Revert batch files
4. Uninstall PyYAML if desired: `pip uninstall pyyaml`

## Future Improvements

With YAML, we could potentially:
- Add inline comments to document specific rules
- Split into multiple files by modality (optional)
- Use YAML anchors/aliases to reduce duplication (advanced)

For now, we're keeping the same structure for maximum compatibility.




