@echo off
echo ========================================
echo Packaging RVU Counter with PyInstaller
echo ========================================
echo.

REM Run PyInstaller
echo Building executable...
pyinstaller --onefile --windowed --add-data "rvu_settings.json;." --name "RVU Counter" RVUCounter.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: PyInstaller failed!
    pause
    exit /b 1
)

echo.
echo Build complete. Cleaning up...

REM Move the exe to the current folder
if exist "dist\RVU Counter.exe" (
    move /Y "dist\RVU Counter.exe" "RVU Counter.exe"
    echo Moved RVU Counter.exe to current folder.
)

REM Clean up build folders and files
if exist "build" (
    rmdir /S /Q "build"
    echo Removed build folder.
)

if exist "dist" (
    rmdir /S /Q "dist"
    echo Removed dist folder.
)

if exist "RVU Counter.spec" (
    del /Q "RVU Counter.spec"
    echo Removed spec file.
)

echo. 
echo ========================================
echo Packaging complete!
echo RVU Counter.exe is ready to use.
echo ========================================
echo.
pause





