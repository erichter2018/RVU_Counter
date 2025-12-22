@echo off
REM ============================================================================
REM RVU Counter Auto-Updater
REM This script swaps the old executable with the new one after download
REM ============================================================================

setlocal enabledelayedexpansion

REM Define file names
set "APP_NAME=RVU Counter.exe"
set "NEW_APP=RVU Counter.new.exe"
set "OLD_APP=RVU Counter.old.exe"

echo ========================================
echo RVU Counter Update Process
echo ========================================
echo.

REM Navigate to parent directory (where the exe lives)
cd /d "%~dp0\.."

echo Waiting for RVU Counter to close...
timeout /t 2 /nobreak > nul

REM Wait for the main process to fully exit
:wait_for_close
tasklist /FI "IMAGENAME eq %APP_NAME%" 2>NUL | find /I /N "%APP_NAME%" >NUL
if "%ERRORLEVEL%"=="0" (
    echo Application still running, waiting...
    timeout /t 1 /nobreak > nul
    goto wait_for_close
)

echo.
echo Application closed successfully.
echo.

REM Wait for PyInstaller temp folders to be cleaned up
echo Waiting for temporary files to be cleaned up...
timeout /t 3 /nobreak > nul

:wait_for_cleanup
set "MEI_FOUND=0"
for /d %%D in ("%TEMP%\_MEI*") do set "MEI_FOUND=1"
if "%MEI_FOUND%"=="1" (
    echo PyInstaller temp folders still present, waiting...
    timeout /t 2 /nobreak > nul
    goto wait_for_cleanup
)

echo Cleanup complete.
echo.

REM Backup and swap logic
echo Performing update...
echo.

REM Remove old backup if it exists
if exist "%OLD_APP%" (
    echo Removing previous backup...
    del /f /q "%OLD_APP%" 2>nul
    if errorlevel 1 (
        echo Warning: Could not remove old backup file.
    )
)

REM Rename current exe to .old
if exist "%APP_NAME%" (
    echo Backing up current version...
    ren "%APP_NAME%" "%OLD_APP%" 2>nul
    if errorlevel 1 (
        echo ERROR: Could not rename current executable!
        echo Update failed. Please try again.
        pause
        exit /b 1
    )
)

REM Rename new exe to main name
if exist "helpers\%NEW_APP%" (
    echo Installing new version...
    move "helpers\%NEW_APP%" "%APP_NAME%" >nul 2>&1
    if errorlevel 1 (
        echo ERROR: Could not install new version!
        echo Attempting to restore backup...
        if exist "%OLD_APP%" ren "%OLD_APP%" "%APP_NAME%"
        pause
        exit /b 1
    )
) else (
    echo ERROR: New version file not found at helpers\%NEW_APP%
    echo Restoring backup...
    if exist "%OLD_APP%" ren "%OLD_APP%" "%APP_NAME%"
    pause
    exit /b 1
)

echo.
echo ========================================
echo Update complete!
echo ========================================
echo.

REM Clean up the backup file
if exist "%OLD_APP%" (
    echo Cleaning up backup file...
    del /f /q "%OLD_APP%" 2>nul
    if not errorlevel 1 (
        echo Backup removed successfully.
    )
)

echo.
echo RVU Counter has been updated successfully.
echo.
echo Please start RVU Counter when you're ready.
echo.
echo Press any key to close this window...
pause > nul
exit





