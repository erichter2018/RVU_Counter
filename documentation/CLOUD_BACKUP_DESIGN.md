# Cloud Backup Design Document
## Automatic OneDrive Backup for rvu_records.db

**Version:** 1.0  
**Date:** 2025-12-05  
**Status:** Draft  

---

## Table of Contents
1. [Overview](#overview)
2. [Goals & Non-Goals](#goals--non-goals)
3. [Technical Architecture](#technical-architecture)
4. [Implementation Approaches](#implementation-approaches)
5. [User Interface Design](#user-interface-design)
6. [Error Handling & Edge Cases](#error-handling--edge-cases)
7. [Security Considerations](#security-considerations)
8. [Testing Strategy](#testing-strategy)
9. [Implementation Phases](#implementation-phases)
10. [Open Questions](#open-questions)

---

## Overview

### Problem Statement
The `rvu_records.db` SQLite database contains critical work history data that users cannot afford to lose. Currently, there is no automated backup mechanism, leaving users vulnerable to:
- Hardware failure
- Accidental deletion
- Corruption
- Ransomware/malware

### Proposed Solution
Implement automatic cloud backup to OneDrive, leveraging the fact that most enterprise Windows users already have OneDrive installed and configured through their Microsoft 365 subscription.

---

## Goals & Non-Goals

### Goals
- **Automatic backup** without user intervention after initial setup
- **Minimal resource usage** - backup should not impact app performance
- **Data integrity** - ensure backups are complete and valid
- **Easy restore** - simple process to recover from backup
- **Version history** - keep multiple backup versions
- **Cross-device awareness** - handle multiple computers gracefully

### Non-Goals
- Real-time sync (not a collaboration tool)
- Support for other cloud providers (Google Drive, Dropbox) in v1
- Backup of settings file (can be added later)
- End-to-end encryption (rely on OneDrive's encryption)

---

## Technical Architecture

### Approach Comparison

| Approach | Pros | Cons |
|----------|------|------|
| **A: OneDrive Folder Sync** | Simple, no API needed, automatic | Less control, sync conflicts possible |
| **B: OneDrive API** | Full control, explicit backup | Complex auth, API rate limits |
| **C: Hybrid** | Best of both | More code to maintain |

**Recommendation:** Approach A (OneDrive Folder Sync) for v1, with safeguards.

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      RVU Counter App                         │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐ │
│  │  Database   │───▶│   Backup    │───▶│  OneDrive Sync  │ │
│  │  Manager    │    │   Manager   │    │     Folder      │ │
│  └─────────────┘    └─────────────┘    └────────┬────────┘ │
│                            │                     │          │
│                     ┌──────▼──────┐              │          │
│                     │   Backup    │              │          │
│                     │   Settings  │              ▼          │
│                     └─────────────┘        ┌──────────┐    │
│                                            │ OneDrive │    │
│                                            │  Cloud   │    │
└─────────────────────────────────────────────┴──────────┴────┘
```

### Component Design

#### 1. Backup Manager (`backup_manager.py`)

```python
class BackupManager:
    def __init__(self, db_path: str, settings: dict):
        self.db_path = db_path
        self.settings = settings
        self.backup_folder = self._detect_onedrive_folder()
        self.last_backup_time = None
        self.backup_in_progress = False
    
    def _detect_onedrive_folder(self) -> Optional[Path]:
        """Detect OneDrive folder location."""
        pass
    
    def create_backup(self) -> BackupResult:
        """Create a backup of the database."""
        pass
    
    def restore_from_backup(self, backup_path: str) -> RestoreResult:
        """Restore database from backup."""
        pass
    
    def get_backup_history(self) -> List[BackupInfo]:
        """Get list of available backups."""
        pass
    
    def cleanup_old_backups(self, keep_count: int = 10):
        """Remove old backups beyond retention limit."""
        pass
```

#### 2. Backup Scheduler

```python
class BackupScheduler:
    INTERVALS = {
        "every_hour": 3600,
        "every_4_hours": 14400,
        "daily": 86400,
        "on_shift_end": None,  # Event-driven
        "manual_only": None
    }
    
    def schedule_next_backup(self):
        """Schedule the next backup based on settings."""
        pass
    
    def on_shift_end(self):
        """Trigger backup when shift ends (if configured)."""
        pass
```

---

## Implementation Approaches

### Approach A: OneDrive Folder Sync (Recommended for v1)

#### How It Works
1. Detect OneDrive folder location from Windows registry/environment
2. Create backup subfolder: `OneDrive/Apps/RVU Counter/Backups/`
3. Copy database to backup folder with timestamp
4. OneDrive automatically syncs to cloud

#### OneDrive Folder Detection

```python
def detect_onedrive_folder() -> Optional[Path]:
    """
    Detect OneDrive folder location.
    Priority order:
    1. Environment variable: ONEDRIVE
    2. Environment variable: OneDriveCommercial (for business)
    3. Registry: HKCU\Software\Microsoft\OneDrive\Accounts\*
    4. Default paths: ~/OneDrive, ~/OneDrive - Company Name
    """
    
    # Method 1: Environment variables
    for env_var in ['OneDriveCommercial', 'OneDriveConsumer', 'ONEDRIVE']:
        path = os.environ.get(env_var)
        if path and os.path.isdir(path):
            return Path(path)
    
    # Method 2: Registry (Windows)
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\OneDrive\Accounts\Business1"
        )
        path, _ = winreg.QueryValueEx(key, "UserFolder")
        if os.path.isdir(path):
            return Path(path)
    except:
        pass
    
    # Method 3: Common default paths
    home = Path.home()
    for subdir in home.iterdir():
        if subdir.name.startswith("OneDrive"):
            return subdir
    
    return None
```

#### Safe Database Copy

```python
def create_safe_backup(db_path: Path, backup_folder: Path) -> Path:
    """
    Create a backup with database integrity verification.
    
    Issues addressed:
    - Database locked during write
    - Partial copy corruption
    - WAL mode journal files
    """
    
    # Step 1: Create temporary copy using SQLite backup API
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"rvu_records_{timestamp}.db"
    backup_path = backup_folder / backup_name
    temp_path = backup_folder / f".{backup_name}.tmp"
    
    try:
        # Use SQLite's online backup API for consistency
        source = sqlite3.connect(db_path)
        dest = sqlite3.connect(temp_path)
        
        with dest:
            source.backup(dest)
        
        source.close()
        dest.close()
        
        # Step 2: Verify backup integrity
        verify_conn = sqlite3.connect(temp_path)
        cursor = verify_conn.execute("PRAGMA integrity_check")
        result = cursor.fetchone()[0]
        verify_conn.close()
        
        if result != "ok":
            raise BackupCorruptionError(f"Backup integrity check failed: {result}")
        
        # Step 3: Atomic rename
        temp_path.rename(backup_path)
        
        return backup_path
        
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            raise DatabaseLockedError("Database is locked, will retry later")
        raise
    finally:
        # Cleanup temp file if exists
        if temp_path.exists():
            temp_path.unlink()
```

### Approach B: OneDrive API (Future Enhancement)

#### Authentication Flow
1. Register app in Azure AD
2. Implement OAuth 2.0 with PKCE
3. Store refresh token securely in Windows Credential Manager
4. Handle token refresh automatically

#### API Endpoints
- Upload: `PUT /me/drive/root:/Apps/RVU Counter/{filename}:/content`
- List: `GET /me/drive/root:/Apps/RVU Counter:/children`
- Delete: `DELETE /me/drive/items/{item-id}`

#### Considerations
- Requires internet connectivity check
- Need to handle 429 (rate limit) responses
- Large file upload may need chunked upload API
- More complex error handling

---

## User Interface Design

### Settings Dialog Additions

```
┌─────────────────────────────────────────────────────────────┐
│  Cloud Backup Settings                                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ☑ Enable automatic cloud backup                            │
│                                                              │
│  Backup Location:                                            │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ C:\Users\erik\OneDrive\Apps\RVU Counter\Backups        │ │
│  └────────────────────────────────────────────────────────┘ │
│  [Browse...]  [Open Folder]                                  │
│                                                              │
│  Backup Schedule:                                            │
│  ○ After each shift ends                                    │
│  ○ Every 4 hours                                            │
│  ○ Daily                                                     │
│  ○ Manual only                                               │
│                                                              │
│  Keep last [10 ▼] backups                                   │
│                                                              │
│  ─────────────────────────────────────────────────────────  │
│                                                              │
│  Last Backup: Today 3:45 AM (successful)                    │
│  Next Backup: After current shift ends                      │
│                                                              │
│  [Backup Now]  [View Backup History]  [Restore from Backup] │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Main Window Indicator

Add subtle backup status indicator near version info:

```
v1.3 (2025-12-05)  ☁️ Backed up 2h ago
```

Or with issue:
```
v1.3 (2025-12-05)  ⚠️ Backup failed - click for details
```

### Backup History Dialog

```
┌─────────────────────────────────────────────────────────────┐
│  Backup History                                        [X]  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ Date/Time          Size     Status    Records  Actions │ │
│  ├────────────────────────────────────────────────────────┤ │
│  │ 12/05/25 3:45 AM   1.2 MB   ✓ Synced  1,247   [Restore]│ │
│  │ 12/04/25 8:15 AM   1.1 MB   ✓ Synced  1,198   [Restore]│ │
│  │ 12/03/25 7:30 AM   1.1 MB   ✓ Synced  1,156   [Restore]│ │
│  │ 12/02/25 8:00 AM   1.0 MB   ✓ Synced  1,102   [Restore]│ │
│  │ 12/01/25 7:45 AM   1.0 MB   ✓ Synced  1,055   [Restore]│ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  [Delete Selected]  [Refresh]                    [Close]    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Restore Confirmation Dialog

```
┌─────────────────────────────────────────────────────────────┐
│  ⚠️ Restore from Backup                                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  You are about to restore from:                             │
│  rvu_records_20251204_081500.db                             │
│                                                              │
│  This backup contains:                                       │
│  • 1,198 study records                                       │
│  • 45 shifts                                                 │
│  • Created: December 4, 2025 8:15 AM                        │
│                                                              │
│  ⚠️ WARNING: Your current database will be replaced.        │
│  A backup of your current database will be created first.   │
│                                                              │
│  Current database has:                                       │
│  • 1,247 study records (49 more than backup)                │
│  • 47 shifts (2 more than backup)                           │
│                                                              │
│                        [Cancel]  [Restore]                   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### First-Time Setup

```
┌─────────────────────────────────────────────────────────────┐
│  ☁️ Cloud Backup Setup                                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Protect your work data with automatic cloud backup!        │
│                                                              │
│  ✓ OneDrive detected at:                                    │
│    C:\Users\erik\OneDrive - Company Name                    │
│                                                              │
│  Backups will be stored in:                                 │
│    OneDrive/Apps/RVU Counter/Backups/                       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ ○ Backup after each shift (recommended)                │ │
│  │ ○ Backup daily                                          │ │
│  │ ○ Don't set up backup now                               │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│                              [Skip]  [Enable Cloud Backup]  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Error Handling & Edge Cases

### Issue Matrix

| Issue | Detection | User Notification | Automatic Recovery |
|-------|-----------|-------------------|-------------------|
| OneDrive not installed | Check on startup | Setup wizard with manual path option | N/A |
| OneDrive not signed in | Check sync status file | Warning icon + tooltip | N/A |
| OneDrive paused | Check sync status | Info icon | Backup queued |
| Database locked | SQLite exception | Silent retry | Retry 3x with backoff |
| Disk full (local) | OS exception | Error dialog | N/A |
| OneDrive full | Check after copy | Warning + email from MS | N/A |
| Network offline | Check connectivity | Silent queue | Backup when online |
| Backup corruption | Integrity check | Error + keep previous | Auto-delete corrupt |
| Sync conflict | OneDrive creates copy | List conflicts in history | Manual resolution |
| App crash mid-backup | Temp file detection | Clean up on next start | Resume/restart |

### Detailed Error Handling

#### Database Locked
```python
def backup_with_retry(self, max_retries=3, base_delay=1.0):
    """Backup with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            return self.create_backup()
        except DatabaseLockedError:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.info(f"Database locked, retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error("Database locked after all retries")
                raise
```

#### OneDrive Sync Status Check
```python
def check_onedrive_status(self, backup_path: Path) -> SyncStatus:
    """
    Check if file has been synced to cloud.
    
    OneDrive adds extended attributes to synced files.
    """
    # Method 1: Check for OneDrive overlay icons (Windows Shell)
    try:
        import pythoncom
        from win32com.shell import shell, shellcon
        # Query shell for overlay status
        pass
    except:
        pass
    
    # Method 2: Check OneDrive status file
    onedrive_root = backup_path.parent
    while onedrive_root.parent != onedrive_root:
        status_file = onedrive_root / ".odopen"
        if status_file.exists():
            # Parse status
            pass
        onedrive_root = onedrive_root.parent
    
    # Method 3: Just assume pending if file is new
    return SyncStatus.PENDING
```

#### Multi-Device Conflict Resolution
```python
def handle_sync_conflict(self, conflict_files: List[Path]):
    """
    Handle OneDrive sync conflicts.
    
    OneDrive creates files like:
    - rvu_records_20251205-DESKTOP-ABC123.db
    - rvu_records_20251205-LAPTOP-XYZ789.db
    """
    # Strategy: Keep newest, archive others
    conflicts_by_time = sorted(conflict_files, 
                               key=lambda f: f.stat().st_mtime, 
                               reverse=True)
    
    # Notify user of conflict
    self.notify_conflict(conflicts_by_time)
    
    # Move older conflicts to archive folder
    archive = self.backup_folder / "conflicts"
    archive.mkdir(exist_ok=True)
    
    for old_file in conflicts_by_time[1:]:
        old_file.rename(archive / old_file.name)
```

---

## Security Considerations

### Data Privacy
1. **PHI Concerns**: Database may contain patient identifiers (accession numbers)
   - Accession numbers alone are not typically PHI
   - No patient names/DOB/MRN stored
   - OneDrive for Business has BAA compliance option

2. **Encryption**:
   - At rest: OneDrive encrypts all files
   - In transit: TLS 1.2+
   - Optional: Add SQLCipher for local encryption (future)

### Credential Storage
```python
def store_backup_settings_securely(self):
    """
    Store sensitive settings in Windows Credential Manager.
    
    For API approach only - folder sync doesn't need credentials.
    """
    import keyring
    
    # Store OAuth refresh token
    keyring.set_password("RVUCounter", "onedrive_refresh_token", token)
```

### Access Control
- Backup folder inherits OneDrive permissions
- Files are user-private by default
- No sharing links created automatically

---

## Testing Strategy

### Unit Tests
```python
class TestBackupManager:
    def test_detect_onedrive_folder_found(self):
        """Test OneDrive folder detection when present."""
        pass
    
    def test_detect_onedrive_folder_not_found(self):
        """Test graceful handling when OneDrive not installed."""
        pass
    
    def test_backup_creates_valid_copy(self):
        """Test backup integrity."""
        pass
    
    def test_backup_during_active_write(self):
        """Test backup while database is being written to."""
        pass
    
    def test_restore_replaces_database(self):
        """Test restore overwrites current database."""
        pass
    
    def test_cleanup_keeps_correct_count(self):
        """Test old backup cleanup."""
        pass
```

### Integration Tests
1. **Full backup/restore cycle**
2. **Simulated database lock**
3. **Simulated disk full**
4. **Concurrent backup attempts**
5. **App crash recovery**

### Manual Testing Checklist
- [ ] Fresh install - OneDrive detected
- [ ] Fresh install - OneDrive not installed
- [ ] Backup during active shift
- [ ] Backup at shift end
- [ ] Restore from 1-day-old backup
- [ ] Multi-device conflict scenario
- [ ] Offline backup queue
- [ ] Very large database (100MB+)
- [ ] Rapid repeated backups

---

## Implementation Phases

### Phase 1: Core Backup (MVP)
**Timeline:** 1-2 days

- [ ] OneDrive folder detection
- [ ] Basic backup creation with SQLite backup API
- [ ] Integrity verification
- [ ] Settings UI (enable/disable, manual trigger)
- [ ] Backup on shift end
- [ ] Basic error handling

### Phase 2: Restore & History
**Timeline:** 1 day

- [ ] Backup history listing
- [ ] Restore from backup
- [ ] Pre-restore backup of current DB
- [ ] Backup count display in history

### Phase 3: Polish & Robustness
**Timeline:** 1-2 days

- [ ] Automatic cleanup of old backups
- [ ] Retry logic with backoff
- [ ] Status indicator in main window
- [ ] First-time setup wizard
- [ ] OneDrive sync status check

### Phase 4: Future Enhancements
**Timeline:** TBD

- [ ] OneDrive API integration (explicit upload)
- [ ] Backup scheduling options
- [ ] Settings backup
- [ ] Conflict resolution UI
- [ ] Incremental backup (WAL shipping)

---

## Open Questions

1. **Backup Frequency**: Is "after each shift" sufficient, or do some users want hourly?

2. **Multi-Computer**: If user works on two computers, should we:
   - Merge databases automatically?
   - Keep separate backups per device?
   - Warn and let user choose?

3. **Notification Preference**: 
   - Silent operation with status icon only?
   - Toast notifications on success/failure?
   - Only notify on failure?

4. **Storage Quota**: 
   - Should we warn if OneDrive is >90% full?
   - Compress backups to save space?

5. **PHI Compliance**:
   - Do we need to document this feature for HIPAA?
   - Should we add optional encryption?

6. **Backup Location Choice**:
   - Allow custom path outside OneDrive?
   - Support mapped network drives?

---

## Appendix

### OneDrive Registry Keys (Windows)

```
HKEY_CURRENT_USER\Software\Microsoft\OneDrive
├── Accounts
│   ├── Business1
│   │   ├── UserFolder = "C:\Users\...\OneDrive - Company"
│   │   └── UserEmail = "user@company.com"
│   └── Personal
│       └── UserFolder = "C:\Users\...\OneDrive"
└── Version = "24.xxx.xxxx.xxxx"
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `ONEDRIVE` | Personal OneDrive path |
| `OneDriveConsumer` | Personal OneDrive path (alternate) |
| `OneDriveCommercial` | Business OneDrive path |

### SQLite Backup API

```python
import sqlite3

def sqlite_backup(source_path, dest_path):
    """
    Use SQLite's online backup API.
    
    Advantages:
    - Works even if source is being written to
    - Handles WAL journal correctly
    - Atomic operation
    """
    source = sqlite3.connect(source_path)
    dest = sqlite3.connect(dest_path)
    
    with dest:
        source.backup(dest, pages=100, progress=backup_progress)
    
    source.close()
    dest.close()
```

### File Naming Convention

```
rvu_records_YYYYMMDD_HHMMSS.db
rvu_records_YYYYMMDD_HHMMSS_HOSTNAME.db  (for conflict resolution)
```

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-12-05 | AI Assistant | Initial draft |














