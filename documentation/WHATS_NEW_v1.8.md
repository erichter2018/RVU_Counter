# What's New in RVU Counter v1.8

**Release Date:** December 16, 2025

---

## 🎯 New Features

### Mini Interface
A brand new **distraction-free mini mode** for when you need to focus on reading while keeping track of key metrics.

**Features:**
- **Double-click anywhere** in the main interface to launch mini mode
- **Double-click mini interface** to return to full interface
- **Fully draggable** - click and hold anywhere to move
- **Configurable metrics** - Choose up to 2 metrics from:
  - Pace (vs prior shift) with +/- color indicator
  - Current Total RVU
  - Estimated Total for Shift
  - Average per Hour
- **Undo button** - Small "U" button for quick undo access
- **Position memory** - Both full and mini interfaces remember their positions separately
- **Dark mode support** - Respects your dark mode setting
- **Borderless design** - Clean, minimal appearance

**How to Use:**
1. Configure your preferred metrics in Settings → Mini Interface
2. Double-click anywhere in the main window to launch
3. Drag to reposition as needed
4. Double-click to return to full interface

---

### Improved Update System
A completely redesigned update experience that gives you more control.

**New Update Dialog:**
- **See what's new** - Full release notes displayed in scrollable window
- **Version comparison** - Clear display of current vs. new version
- **Three clear options:**
  - **Update Now** - Download and install immediately
  - **Skip This Version** - Hide this update permanently (until next version)
  - **Remind Me Later** - Close dialog, will show again next launch
- **Dark mode styled** - Fully themed dialog window
- **Cleaner button** - Simplified "Update!" button (removed warning icon)

---

### Optional Borderless Mode
For users who want an even cleaner interface.

**Feature:**
- **Remove title bar** option in Settings
- Main interface becomes borderless and draggable anywhere
- Off by default
- Requires app restart to take effect
- Use Alt+F4 to close when borderless

---

## 🔧 Improvements

### Undo/Redo Toggle
- **Smarter Undo Button** - No more permanent data loss from accidental clicks!
  - **Undo** removes the last study and button changes to "Redo"
  - **Redo** restores the undone study and button changes back to "Undo"
  - **Mini interface** - "U" button changes to "R" for redo mode
  - **Tooltip shows procedure name** - Hover over button to see which study will be affected
  - **Auto-reset** - Adding a new study clears the redo buffer and resets to undo mode
  - **Single-level toggle** - Simple and prevents confusion

### Excel Payroll Checker
- **Auto-populated filename** - Export dialog now suggests a filename based on your input file
  - Example: `Payroll_December.xlsx` → `Payroll_December_report.txt`

---

## 🐛 Fixes

### RVU Classification
- **CT Pelvis studies** are now correctly classified
  - Previously: "CT PELVIS WITHOUT IV CONTRAST" → CT Other
  - Now: "CT PELVIS WITHOUT IV CONTRAST" → CT Pelvis (1.0 RVU)
  - Properly handles studies with "pelvis" or "pel" keywords
  - Excludes studies that are actually CT AP or CT CAP

### Comparison View (Statistics)
- **Fixed "All" cumulative graph** - Previously showed incorrect data at shift start and unexplained dropoffs
  - Now correctly starts at 0 studies at shift start (23:00)
  - Properly excludes studies finished before the rounded shift start time
  - Individual modality graphs remain accurate

### Performance & Stability
- **Mini interface performance** - Eliminated freezing/unresponsiveness
  - Pace calculation now cached for 5 seconds (updates every 5s instead of every 1s)
  - Reduced excessive INFO logging to DEBUG level
  - Dramatically improved responsiveness when mini mode is active

### Position Memory
- **Window position restoration** - Main window now properly restores position when returning from mini mode
  - Both interfaces remember their positions separately
  - Positions persist across launches

---

## 💡 Settings Changes

### New Settings:
- **Mini Interface** section:
  - Metric 1 dropdown (default: Pace)
  - Metric 2 dropdown (default: Current Total)
  - Info text about double-click to launch
- **Remove title bar** checkbox (default: off)

---

## 📝 Technical Notes

- Version skipping now tracked in settings (`skipped_update_version`)
- Mini window position stored separately from main window position
- Settings automatically migrate to include new options
- All new features fully respect dark mode

---

## 🎨 User Experience

**Faster Workflows:**
- Mini mode for distraction-free work
- Quick undo access in mini interface
- Smarter filename suggestions
- Skip unwanted updates

**More Control:**
- Choose your mini metrics
- Optional borderless mode
- Skip specific versions
- Configure interface to your preference

---

## 📊 Known Issues

None at this time. All reported issues from v1.7.x have been addressed.

---

## 🔄 Upgrading from v1.7.x

1. Settings will automatically migrate
2. No data loss or conversion needed
3. Mini interface settings will use defaults until configured
4. Borderless mode is OFF by default

---

**Questions or Issues?**
Report bugs or request features through the Tools menu → What's New button.

---

*Thank you for using RVU Counter!*

