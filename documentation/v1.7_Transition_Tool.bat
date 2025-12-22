@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo           RVU Counter - v1.7 Transition Tool
echo ============================================================
echo.
echo This tool will:
echo 1. Organize your folders for the new v1.7 architecture.
echo 2. PRESERVE your study database (rvu_records.db).
echo 3. Reset settings to v1.7 defaults (Clean Start).
echo 4. Automatically download the latest RVU Counter.exe.
echo 5. Remove all old, messy version files.
echo.
echo Please ensure the RVU Counter is CLOSED before continuing.
echo.

:CHECK_PROCESS
tasklist /FI "IMAGENAME eq RVU Counter*" 2>NUL | find /I /N "RVU Counter" >NUL
if "%ERRORLEVEL%"=="0" (
    echo [!] WARNING: It looks like an RVU Counter process is still running.
    echo Please close all open RVU Counter windows and then press any key to try again.
    pause >nul
    goto CHECK_PROCESS
)

set /p confirm="Do you want to proceed? (Y/N): "
if /i "%confirm%" neq "Y" exit

echo.
echo [1/5] Creating new folder structure...
mkdir data settings logs helpers documentation 2>nul

echo [2/5] Preserving your Database...
if exist rvu_records.db (
    move /Y rvu_records.db data\rvu_records.db >nul
    echo   - SUCCESS: Your study records have been moved to /data/
) else (
    echo   - NOTE: No database found, starting with a fresh one.
)

echo [3/5] Cleaning old settings for v1.7 fresh start...
if exist rvu_settings.yaml (
    del /Q rvu_settings.yaml >nul
    echo   - SUCCESS: Old settings removed. v1.7 will create new ones.
)

echo [4/5] Downloading v1.7 from GitHub...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://github.com/erichter2018/RVU-Releases/releases/latest/download/RVU_Counter.exe' -OutFile 'RVU Counter.exe'"
if exist "RVU Counter.exe" (
    echo   - SUCCESS: Latest version downloaded.
) else (
    echo   - ERROR: Download failed. Please check internet or proxy.
)

echo [5/5] Tidying up old version files...
for %%f in ("RVU Counter *.exe") do (
    if "%%f" neq "RVU Counter.exe" (
        del /Q "%%f"
        echo   - Removed legacy file: %%f
    )
)

echo.
echo ============================================================
echo DONE! Your RVU Counter is now organized and upgraded.
echo ============================================================
echo.
echo You can now delete this script.
echo.
pause
start "" "RVU Counter.exe"







