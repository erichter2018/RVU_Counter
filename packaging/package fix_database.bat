@echo off
echo ========================================
echo Packaging Fix Database with PyInstaller
echo ========================================
echo.
echo Looking for source files in parent directory...
echo.

REM Check if rvu_settings.json exists in parent directory
if not exist "..\rvu_settings.json" (
    echo ERROR: rvu_settings.json not found in parent directory!
    echo Please ensure rvu_settings.json is present in the parent folder.
    pause
    exit /b 1
)

REM Check if fix_database.py exists in parent directory
if not exist "..\fix_database.py" (
    echo ERROR: fix_database.py not found in parent directory!
    pause
    exit /b 1
)

REM Run PyInstaller from parent directory
echo Building executable...
echo Bundling rvu_settings.json from parent directory...
set PARENT_DIR=%~dp0..
pushd "%PARENT_DIR%"
set ABS_JSON=%CD%\rvu_settings.json
pyinstaller --onefile --console --add-data "%ABS_JSON%;." --name "Fix Database" --distpath "packaging\dist" --workpath "packaging\build" --specpath "packaging" --clean fix_database.py
popd

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: PyInstaller failed!
    pause
    exit /b 1
)

echo.
echo Build complete. Cleaning up...

REM Move the exe to the current folder (packaging)
if exist "dist\Fix Database.exe" (
    move /Y "dist\Fix Database.exe" "Fix Database.exe"
    echo Moved Fix Database.exe to packaging folder.
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

if exist "Fix Database.spec" (
    del /Q "Fix Database.spec"
    echo Removed spec file.
)

echo. 
echo ========================================
echo Packaging complete!
echo Fix Database.exe is ready in packaging folder.
echo Note: The executable will look for rvu_records.db in the same folder.
echo ========================================
echo.
pause

