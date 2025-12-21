# Auto-Update & Folder Architecture Design (Non-Admin)

This document outlines a robust system for auto-updates and data separation, specifically designed for non-admin environments where portability and "Rule Hotfixes" are required.

---

## 📂 Proposed Folder Structure

To enable seamless updates and prevent data loss, we will move away from a "flat" root directory to a structured one:

```text
/RVU Counter/
├── RVU Counter.exe           # The main application (static name)
├── helpers/
│   └── updater.bat           # Sidecar script for swapping binaries
├── settings/
│   ├── user_settings.yaml    # User preferences (theme, shift length, etc.)
│   └── rvu_rules.yaml        # RVU values and classification rules (The "Manifest")
├── data/
│   └── rvu_records.db        # SQLite database
└── logs/
    └── rvu_counter.log       # Application logs
```

---

## 🔄 The Update Workflow (The "Seamless Swap")

### 1. The Legacy Cleanup (First Update Only)
Since users currently have files like `RVU Counter 1.3.exe` or `RVU Counter 1.5.exe`, the very first update to version 1.6+ will:
-   Detect its own filename.
-   Identify any other `.exe` files in the root folder containing "RVU Counter".
-   The `updater.bat` will be tasked with deleting these legacy files specifically during the transition to the standardized `RVU Counter.exe`.

### 2. Standard Update Path
1.  **Check**: App checks `version.json` on the server.
2.  **Download**: App downloads the new binary to `helpers/RVU_Counter_NEW.tmp`.
3.  **Trigger**: User clicks "Update". The app launches `helpers/updater.bat`.
4.  **Updater Logic (`helpers/updater.bat`)**:
    -   Wait for the main PID to close.
    -   `del ..\RVU Counter*.exe` (Cleans up any legacy named files and the current one).
    -   `move RVU_Counter_NEW.tmp ..\RVU Counter.exe`.
    -   `start ..\RVU Counter.exe`.

---

## ⚡ Rule Hotfixes (The "Manifest" System)

By separating `user_settings.yaml` from `rvu_rules.yaml`, we can update the **logic** of the app without updating the **code**.

-   **Independent Rule Updates**: Every hour (or on start), the app checks if the `rvu_rules.yaml` on the server has a newer timestamp or version than the local one in `/settings/`.
-   **Silent Patching**: If a new rule is found, the app downloads it and replaces the file in `/settings/`.
-   **Instant Application**: The app calls `logic.study_matcher.reload_rules()`. The user gets the new classification logic immediately without even needing to restart the `.exe`.

---

## 🔒 Non-Admin Reliability

-   **Relative Paths**: The app will use `os.path.dirname(sys.executable)` to determine its root. This ensures that whether the user runs it from their Desktop, Downloads, or a USB drive, it always finds its `/settings/` and `/data/` folders.
-   **Permission Check**: On startup, the app will verify it has write access to its own folder. If not, it will warn the user to move it to a location like `%LOCALAPPDATA%` or their `Documents` folder.

---

## 🛠️ Integrated Tooling (All-in-One Application)

To simplify the user experience and reduce the number of files to manage, the functionality of the standalone utility apps will be merged into the main `RVU Counter` UI.

-   **Excel Checker Integration**: A new "Tools" or "Verify" tab in the Statistics or Settings window will allow users to upload Excel spreadsheets for RVU verification directly within the main app.
-   **Database Repair Integration**: The `fix_database` logic (identifying duplicates and rule mismatches) will be accessible via the UI, potentially as an "Optimize Database" button. This ensures that the rules used for fixing are always in sync with the current `rvu_rules.yaml`.

---

## 🛠️ Implementation Progress

- [x] **Task 1: Basic Structure** - Created folders `data/`, `settings/`, `logs/`, `helpers/`.
- [x] **Task 2: Data Migration (Phase 1)** - Moved `rvu_records.db` to `data/`.
- [ ] **Task 2: Data Migration (Phase 2)** - Split `rvu_settings.yaml` into `user_settings.yaml` and `rvu_rules.yaml`.
- [ ] **Task 3: Logic Cleanup** - Remove legacy merging code from `data_manager.py`.
- [ ] **Task 4: Update Manager** - Implement `src/core/update_manager.py` and `helpers/updater.bat`.
- [ ] **Task 5: GitHub Service** - Version checking and Releases API integration.
- [ ] **Task 6: All-in-One Integration** - Merge Excel Checker and Database Repair into UI.
- [ ] **Task 7: Documentation Self-Healing** - Auto-download missing docs.

---

## 🛠️ Implementation Tasks (Legacy List)

1.  **Refactor Config**: Update `src/core/config.py` to point to the new subfolders (`/settings`, `/data`).
2.  **Migration Logic**: Add a one-time routine to move existing `rvu_records.db` and `rvu_settings.yaml` into their new subfolders and split the YAML.
3.  **Update Manager**: Implement the `UpdateManager` to handle the `helpers/` directory and binary swapping.
4.  **Utility Integration**: Port the logic from `check_rvu_excel_files.py` and `fix_database.py` into new modules within `src/logic/` and create corresponding UI triggers in `src/ui/`.



