# RVU Counter - Refactored Source Code

This directory contains the refactored, modular codebase for RVU Counter.

---

## Directory Structure

```
src/
├── __init__.py                 # Package initialization
├── main.py                     # Application entry point
│
├── core/                       # Core utilities (425 lines)
│   ├── config.py               # Constants and feature flags
│   ├── logging_config.py       # Logging configuration
│   └── platform_utils.py       # Platform-specific utilities
│
├── logic/                      # Business logic (450 lines)
│   ├── study_matcher.py        # Study type classification
│   └── study_tracker.py        # Study tracking
│
├── data/                       # Data access layer (2,130 lines)
│   ├── database.py             # SQLite database operations
│   ├── data_manager.py         # Settings and data persistence
│   └── backup_manager.py       # Cloud backup management
│
└── ui/                         # User interface (12,982 lines)
    ├── main_window.py          # Main application window
    ├── settings_window.py      # Settings dialog
    ├── statistics_window.py    # Statistics window
    └── widgets/
        └── canvas_table.py     # Custom table widget
```

---

## Module Dependencies

```
┌─────────────────────────────────────────┐
│              main.py                    │
│         (Entry Point)                   │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│           ui/main_window.py             │
│         (RVUCounterApp)                 │
└─┬───────────┬───────────┬──────────────┘
  │           │           │
  ▼           ▼           ▼
┌────┐   ┌─────────┐   ┌──────┐
│ui/ │   │ data/   │   │logic/│
│    │   │         │   │      │
│set-│   │database │   │study │
│tings   │data_mgr │   │match │
│stat│   │backup   │   │track │
└─┬──┘   └────┬────┘   └───┬──┘
  │           │            │
  │           ▼            │
  │      ┌────────┐        │
  │      │ core/  │◄───────┘
  │      │        │
  │      │config  │
  │      │logging │
  │      │platform│
  └─────►└────────┘
```

**Key Principle:** Dependencies flow downward
- UI depends on data and logic
- Data depends on core
- Logic depends on core
- Core has no internal dependencies

---

## Module Descriptions

### `core/` - Foundation Layer

**Purpose:** Provides foundational utilities used by all other modules

**Files:**
- `config.py` - Application constants, feature detection
- `logging_config.py` - Custom FIFO log handler, logging setup
- `platform_utils.py` - Windows API wrappers (multi-monitor, app paths)

**Dependencies:** None (stdlib only)

**Usage:**
```python
from src.core import config, setup_logging, get_app_paths

logger = setup_logging()
settings_dir, data_dir = get_app_paths()
print(f"Version: {config.APP_VERSION}")
```

---

### `logic/` - Business Logic Layer

**Purpose:** Pure business logic with no side effects

**Files:**
- `study_matcher.py` - Classifies procedure text to study types
- `study_tracker.py` - Tracks active studies, detects completion

**Dependencies:** `core/` only

**Usage:**
```python
from src.logic import match_study_type, StudyTracker

# Classify a study
study_type, rvu = match_study_type("CT Chest", rvu_table)

# Track studies
tracker = StudyTracker(min_seconds=10)
tracker.add_study("ACC123", "CT Head", datetime.now())
completed = tracker.check_completed(datetime.now())
```

---

### `data/` - Data Access Layer

**Purpose:** All data persistence, database operations, backups

**Files:**
- `database.py` - SQLite database wrapper (RecordsDatabase)
- `data_manager.py` - Settings and data management (RVUData)
- `backup_manager.py` - OneDrive backup automation

**Dependencies:** `core/`, `logic/`

**Usage:**
```python
from src.data import RecordsDatabase, RVUData, BackupManager

# Database operations
db = RecordsDatabase("rvu_records.db")
shift_id = db.start_shift("2025-12-16T10:00:00")
db.add_record(shift_id, record_dict)

# Data management
data_mgr = RVUData()
data_mgr.save()

# Backups
backup = BackupManager(db_file, data_mgr.data, data_mgr)
```

---

### `ui/` - User Interface Layer

**Purpose:** All GUI components and user interaction

**Files:**
- `main_window.py` - Main application window (RVUCounterApp)
- `settings_window.py` - Settings dialog
- `statistics_window.py` - Statistics and graphing
- `widgets/canvas_table.py` - Custom table widget

**Dependencies:** `core/`, `logic/`, `data/`

**Usage:**
```python
from src.ui import RVUCounterApp
import tkinter as tk

root = tk.Tk()
app = RVUCounterApp(root)
root.mainloop()
```

---

## Development Guidelines

### Adding New Features

1. **Determine the right module:**
   - Business logic? → `logic/`
   - Data storage? → `data/`
   - UI component? → `ui/`
   - Utility function? → `core/` or `utils/`

2. **Follow dependency rules:**
   - Core modules: No dependencies on other modules
   - Logic modules: Can use core, not data or UI
   - Data modules: Can use core and logic, not UI
   - UI modules: Can use everything

3. **Add tests:**
   - Create test file in root: `test_<feature>.py`
   - Test module in isolation
   - Test integration with other modules

### Code Style

- Use type hints for function parameters and returns
- Add docstrings to classes and complex functions
- Keep functions focused and single-purpose
- Avoid circular imports (use TYPE_CHECKING if needed)
- Log important events and errors

### Testing Strategy

```python
# Unit test (isolated module)
from src.logic import match_study_type

def test_study_matcher():
    result = match_study_type("CT Chest", rvu_table)
    assert result == ("CT Chest", 1.5)

# Integration test (multiple modules)
from src.data import RecordsDatabase
from src.logic import StudyTracker

def test_database_integration():
    db = RecordsDatabase("test.db")
    tracker = StudyTracker()
    # ... test interaction
```

---

## Common Tasks

### Running the Application
```bash
# From root directory
python RVUCounter.pyw
```

### Building Executable
```bash
cd packaging
"package RVUCounter.bat"
```

### Running Tests
```bash
# Individual phases
py test_phase1.py
py test_phase2.py
py test_phase3.py
py test_phase4.py

# Final integration
py test_final.py
py test_app_launch.py
```

### Adding a New Study Type Rule
1. Open `rvu_settings.yaml`
2. Add entry to `rvu_table`
3. Add classification rule to `classification_rules` (if needed)
4. Test with `fix_database.py` to update existing records

### Debugging
```python
# Logging is configured in core/logging_config.py
# Check rvu_counter.log for detailed logs

# Increase log verbosity
logging.getLogger().setLevel(logging.DEBUG)
```

---

## Migration from Monolith

Version 1.7 introduces a standardized folder structure for better organization and portability:
- `/data/rvu_records.db`: The SQLite database
- `/settings/rvu_settings.yaml`: User preferences
- `/settings/rvu_rules.yaml`: RVU values and classification rules (Split in v1.7)
- `/logs/rvu_counter.log`: Application logs
- `/helpers/`: Update scripts and temporary files

The application automatically handles the migration from the old "flat" structure to this new format on first launch.

---

## Troubleshooting

### Import Errors
```
ModuleNotFoundError: No module named 'src'
```
**Solution:** Ensure you're running from root directory, not from `src/`

### Circular Import
```
ImportError: cannot import name 'X' from partially initialized module
```
**Solution:** Use `TYPE_CHECKING` and string annotations:
```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .other_module import SomeClass

def func(param: 'SomeClass'):  # String annotation
    ...
```

### Missing Dependencies
```
ImportError: No module named 'yaml'
```
**Solution:** Install requirements:
```bash
pip install -r requirements.txt
```

---

## Performance Notes

The refactored architecture has **no performance impact**:
- Module imports happen once at startup
- Function calls have identical overhead
- Memory usage unchanged
- Actual runtime behavior identical

---

## Contributing

When adding code to the refactored structure:

1. **Choose the right location** - Follow the architecture
2. **Maintain separation** - Don't mix concerns
3. **Add tests** - Test your changes
4. **Document** - Add docstrings for public APIs
5. **Commit often** - Small, focused commits

---

## Questions?

See also:
- `REFACTORING_SUMMARY.md` - Complete refactoring details
- `VALIDATION_CHECKLIST.md` - Manual testing checklist
- `RVUCounterFull.pyw` - Original monolithic code (for reference)

---

*Refactored: December 2025*  
*Version: 1.6 (12/18/2025)*  
*Architecture: Modular MVC*
