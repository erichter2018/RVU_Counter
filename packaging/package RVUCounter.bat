@echo off
echo ========================================
echo Packaging RVU Counter v1.7 with PyInstaller
echo ========================================
echo.
echo Looking for source files in parent directory...
echo.

REM Check if rvu_settings.yaml exists in parent directory (for packaging)
if not exist "..\rvu_settings.yaml" (
    echo ERROR: rvu_settings.yaml not found in parent directory!
    echo Please ensure rvu_settings.yaml is present in the parent folder.
    pause
    exit /b 1
)

REM Check if RVUCounter.pyw exists in parent directory
if not exist "..\RVUCounter.pyw" (
    echo ERROR: RVUCounter.pyw not found in parent directory!
    pause
    exit /b 1
)

REM Check if src folder exists in parent directory
if not exist "..\src" (
    echo ERROR: src folder not found in parent directory!
    echo Please ensure the refactored src/ directory is present.
    pause
    exit /b 1
)

REM Check for new v1.7 files
if not exist "..\helpers\updater.bat" (
    echo WARNING: helpers\updater.bat not found!
    echo This file is required for auto-update functionality.
    pause
)

if not exist "..\documentation\WHATS_NEW_v1.7.md" (
    echo WARNING: documentation\WHATS_NEW_v1.7.md not found!
    echo This file is required for What's New feature.
    pause
)

REM Run PyInstaller from parent directory, outputting to packaging folder
echo Building executable...
echo Bundling all necessary files from parent directory...
echo  - settings/ folder (user_settings.yaml template + rvu_rules.yaml)
echo  - src/ folder (all source code)
echo  - helpers/ folder (updater.bat)
echo  - documentation/ folder (WHATS_NEW and other docs)
echo.
set PARENT_DIR=%~dp0..
pushd "%PARENT_DIR%"
set ABS_SETTINGS=%CD%\settings
set ABS_SRC=%CD%\src
set ABS_HELPERS=%CD%\helpers
set ABS_DOCS=%CD%\documentation
pyinstaller --onefile --windowed ^
    --add-data "%ABS_SETTINGS%;settings" ^
    --add-data "%ABS_SRC%;src" ^
    --add-data "%ABS_HELPERS%;helpers" ^
    --add-data "%ABS_DOCS%;documentation" ^
    --name "RVU Counter" ^
    --distpath "packaging\dist" ^
    --workpath "packaging\build" ^
    --specpath "packaging" ^
    --clean ^
    --hidden-import=src ^
    --hidden-import=src.main ^
    --hidden-import=src.ui ^
    --hidden-import=src.ui.main_window ^
    --hidden-import=src.ui.statistics_window ^
    --hidden-import=src.ui.tools_window ^
    --hidden-import=src.ui.whats_new_window ^
    --hidden-import=src.data ^
    --hidden-import=src.data.data_manager ^
    --hidden-import=src.data.backup_manager ^
    --hidden-import=src.logic ^
    --hidden-import=src.logic.study_matcher ^
    --hidden-import=src.logic.database_repair ^
    --hidden-import=src.logic.excel_checker ^
    --hidden-import=src.core ^
    --hidden-import=src.core.config ^
    --hidden-import=src.core.platform_utils ^
    --hidden-import=src.core.update_manager ^
    --hidden-import=openpyxl ^
    --hidden-import=openpyxl.cell ^
    --hidden-import=openpyxl.cell.cell ^
    RVUCounter.pyw
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
if exist "dist\RVU Counter.exe" (
    move /Y "dist\RVU Counter.exe" "RVU Counter.exe"
    echo Moved RVU Counter.exe to packaging folder.
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
echo ========================================
echo.
echo RVU Counter.exe (v1.7) is ready in packaging folder.
echo.
echo This build includes:
echo  ✓ Auto-update system (UpdateManager)
echo  ✓ Integrated Tools (Database Repair + Excel Checker)
echo  ✓ What's New viewer
echo  ✓ helpers/updater.bat (for auto-updates)
echo  ✓ documentation/WHATS_NEW_v1.7.md
echo  ✓ All 19 RVU classification fixes
echo.
echo The executable is self-contained and ready for:
echo  1. GitHub release upload
echo  2. Direct distribution
echo  3. Installation via Install_or_Upgrade_RVU_Counter.bat
echo.
echo Next steps:
echo  1. Test the executable
echo  2. Upload to GitHub releases as "RVU Counter.exe"
echo  3. Distribute to users
echo.
echo ========================================
echo.
pause

