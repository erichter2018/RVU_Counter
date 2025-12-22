@echo off
REM ============================================================================
REM RVU Counter - Installation & Upgrade Script
REM ============================================================================
REM This script can be used for:
REM   1. Fresh installation (creates empty database)
REM   2. Upgrading existing installation (preserves all data)
REM
REM Usage: Place this file in your desired RVU Counter folder and run it
REM ============================================================================

setlocal enabledelayedexpansion

echo ================================================================================
echo RVU Counter - Installation and Upgrade Tool
echo ================================================================================
echo.

REM Check if running from correct location
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM ============================================================================
REM Step 1: Detect if this is an upgrade or fresh install
REM ============================================================================

set "INSTALL_TYPE=FRESH"

REM Check for existing database (in root or data/ folder)
if exist "rvu_records.db" set "INSTALL_TYPE=UPGRADE"
if exist "data\rvu_records.db" set "INSTALL_TYPE=UPGRADE"

REM Check for existing executable
if exist "RVU Counter.exe" set "INSTALL_TYPE=UPGRADE"
if exist "RVU Counter 1.6.exe" set "INSTALL_TYPE=UPGRADE"

echo.
echo Detected installation type: %INSTALL_TYPE%
echo.

if "%INSTALL_TYPE%"=="UPGRADE" (
    echo This appears to be an UPGRADE of an existing installation.
    echo Your database and settings will be preserved.
) else (
    echo This appears to be a FRESH INSTALLATION.
    echo A new empty database will be created.
)
echo.

pause
echo.

REM ============================================================================
REM Step 2: Backup existing data (if upgrade)
REM ============================================================================

if "%INSTALL_TYPE%"=="UPGRADE" (
    echo ========================================
    echo Backing up existing data...
    echo ========================================
    echo.
    
    REM Create backup folder with timestamp
    set "BACKUP_DIR=backup_%date:~-4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
    set "BACKUP_DIR=!BACKUP_DIR: =0!"
    mkdir "!BACKUP_DIR!" 2>nul
    
    REM Backup database from root or data folder
    if exist "rvu_records.db" (
        echo Backing up rvu_records.db...
        copy /Y "rvu_records.db" "!BACKUP_DIR!\rvu_records.db.backup" >nul
    )
    if exist "data\rvu_records.db" (
        echo Backing up data\rvu_records.db...
        copy /Y "data\rvu_records.db" "!BACKUP_DIR!\rvu_records.db.backup" >nul
    )
    
    REM Backup settings
    if exist "rvu_settings.yaml" (
        echo Backing up rvu_settings.yaml...
        copy /Y "rvu_settings.yaml" "!BACKUP_DIR!\rvu_settings.yaml.backup" >nul
    )
    if exist "settings\user_settings.yaml" (
        echo Backing up settings\user_settings.yaml...
        copy /Y "settings\user_settings.yaml" "!BACKUP_DIR!\user_settings.yaml.backup" >nul
    )
    if exist "settings\rvu_rules.yaml" (
        echo Backing up settings\rvu_rules.yaml...
        copy /Y "settings\rvu_rules.yaml" "!BACKUP_DIR!\rvu_rules.yaml.backup" >nul
    )
    
    echo.
    echo Backup completed: !BACKUP_DIR!
    echo.
)

REM ============================================================================
REM Step 3: Close any running instances
REM ============================================================================

echo ========================================
echo Checking for running instances...
echo ========================================
echo.

tasklist /FI "IMAGENAME eq RVU Counter.exe" 2>NUL | find /I /N "RVU Counter.exe" >NUL
if "%ERRORLEVEL%"=="0" (
    echo RVU Counter is currently running.
    echo Please close it before continuing.
    echo.
    pause
    
    REM Wait for process to close
    :wait_for_close
    tasklist /FI "IMAGENAME eq RVU Counter.exe" 2>NUL | find /I /N "RVU Counter.exe" >NUL
    if "%ERRORLEVEL%"=="0" (
        timeout /t 1 /nobreak >nul
        goto wait_for_close
    )
    echo Application closed.
)

echo No running instances detected.
echo.

REM ============================================================================
REM Step 4: Create folder structure
REM ============================================================================

echo ========================================
echo Creating folder structure...
echo ========================================
echo.

mkdir "data" 2>nul
mkdir "settings" 2>nul
mkdir "logs" 2>nul
mkdir "helpers" 2>nul
mkdir "documentation" 2>nul

echo Folders created.
echo.

REM ============================================================================
REM Step 5: Download latest version
REM ============================================================================

echo ========================================
echo Downloading latest version...
echo ========================================
echo.

REM Define GitHub release URL (GitHub converts spaces to dots in asset names)
set "GITHUB_REPO=erichter2018/RVU-Releases"
set "DOWNLOAD_URL=https://github.com/%GITHUB_REPO%/releases/latest/download/RVU.Counter.exe"

REM Check if we have PowerShell (for downloading)
where powershell >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: PowerShell not found. Cannot download automatically.
    echo.
    echo Please manually download "RVU Counter.exe" from:
    echo https://github.com/%GITHUB_REPO%/releases/latest
    echo.
    echo Place it in this folder and run this script again.
    pause
    exit /b 1
)

REM Download with PowerShell
echo Downloading from GitHub...
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%DOWNLOAD_URL%' -OutFile 'RVU Counter.exe.new' -UseBasicParsing}" 2>nul

if not exist "RVU Counter.exe.new" (
    echo ERROR: Download failed.
    echo.
    echo Please manually download "RVU Counter.exe" from:
    echo https://github.com/%GITHUB_REPO%/releases/latest
    echo.
    pause
    exit /b 1
)

echo Download completed.
echo.

REM ============================================================================
REM Step 6: Install the new version
REM ============================================================================

echo ========================================
echo Installing new version...
echo ========================================
echo.

REM Backup old executable if it exists
if exist "RVU Counter.exe" (
    echo Backing up old executable...
    move /Y "RVU Counter.exe" "RVU Counter.exe.old" >nul
)

REM Move new version into place
move /Y "RVU Counter.exe.new" "RVU Counter.exe" >nul

echo Installation complete.
echo.

REM ============================================================================
REM Step 7: Handle database based on install type
REM ============================================================================

if "%INSTALL_TYPE%"=="UPGRADE" (
    echo ========================================
    echo Preserving your database...
    echo ========================================
    echo.
    
    REM Move database to data folder if it's in root
    if exist "rvu_records.db" (
        if not exist "data\rvu_records.db" (
            echo Moving database to data folder...
            move /Y "rvu_records.db" "data\rvu_records.db" >nul
        )
    )
    
    echo Your database has been preserved.
    echo All records, shifts, and settings will be available.
    
) else (
    echo ========================================
    echo Setting up fresh installation...
    echo ========================================
    echo.
    
    echo A new empty database will be created automatically
    echo when you first launch RVU Counter.
    echo.
    echo Default settings will be used.
)

echo.

REM ============================================================================
REM Step 8: Cleanup old files (optional)
REM ============================================================================

echo ========================================
echo Cleanup
echo ========================================
echo.

REM Clean up old version executables
if exist "RVU Counter 1.6.exe" (
    echo Removing old version: RVU Counter 1.6.exe
    del /F /Q "RVU Counter 1.6.exe" >nul 2>&1
)

if exist "RVU Counter 1.51.exe" (
    echo Removing old version: RVU Counter 1.51.exe
    del /F /Q "RVU Counter 1.51.exe" >nul 2>&1
)

REM Clean up legacy settings file (v1.7+ uses split settings)
if exist "rvu_settings.yaml" (
    echo Removing legacy settings file: rvu_settings.yaml
    del /F /Q "rvu_settings.yaml" >nul 2>&1
)

REM Clean up legacy standalone tools (now integrated into main app)
if exist "fix_database.py" (
    echo Removing legacy tool: fix_database.py
    del /F /Q "fix_database.py" >nul 2>&1
)

if exist "check_rvu_excel_files.py" (
    echo Removing legacy tool: check_rvu_excel_files.py
    del /F /Q "check_rvu_excel_files.py" >nul 2>&1
)

REM Clean up very old .old executables (keep the most recent backup)
for /f "skip=1" %%f in ('dir /b /o-d "RVU Counter.exe.old*" 2^>nul') do (
    echo Removing old backup: %%f
    del /F /Q "%%f" >nul 2>&1
)

echo.

REM ============================================================================
REM Step 9: Summary and launch
REM ============================================================================

echo ================================================================================
echo Installation Complete!
echo ================================================================================
echo.

if "%INSTALL_TYPE%"=="UPGRADE" (
    echo ✓ Upgraded to latest version
    echo ✓ Database preserved in: data\rvu_records.db
    echo ✓ Backup created in: !BACKUP_DIR!
) else (
    echo ✓ Fresh installation complete
    echo ✓ Empty database will be created on first launch
)

echo ✓ Executable: RVU Counter.exe
echo ✓ Folder structure: data\, settings\, logs\, helpers\
echo.

REM Display version info if available
if exist "RVU Counter.exe" (
    echo Installed version: Latest from GitHub
)

echo.
echo ================================================================================
echo.
echo Ready to launch RVU Counter?
echo.
set /p LAUNCH="Launch now? (Y/N): "

if /I "%LAUNCH%"=="Y" (
    echo.
    echo Launching RVU Counter...
    start "" "RVU Counter.exe"
    echo.
    echo RVU Counter has been launched.
    echo You can close this window.
) else (
    echo.
    echo You can launch RVU Counter anytime by running:
    echo     RVU Counter.exe
)

echo.
echo ================================================================================
echo Thank you for using RVU Counter!
echo ================================================================================
echo.
echo This installation script will now delete itself.
echo.

REM Create a temporary script to delete this batch file
REM The timeout gives this script time to exit before deletion
echo @echo off > "%TEMP%\cleanup_installer.bat"
echo timeout /t 2 /nobreak ^>nul >> "%TEMP%\cleanup_installer.bat"
echo del /F /Q "%~f0" >> "%TEMP%\cleanup_installer.bat"
echo del /F /Q "%%~f0" >> "%TEMP%\cleanup_installer.bat"

REM Start the cleanup script in the background
start /min "" "%TEMP%\cleanup_installer.bat"

REM Exit immediately (cleanup script will delete this file)
exit /b 0





