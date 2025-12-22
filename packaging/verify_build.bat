@echo off
REM ============================================================================
REM RVU Counter v1.7 - Build Verification Script
REM ============================================================================
REM This script verifies all necessary files are present before packaging
REM ============================================================================

echo ========================================
echo RVU Counter v1.7 Build Verification
echo ========================================
echo.

set "ERRORS=0"
set "WARNINGS=0"

REM ============================================================================
REM Check Core Files
REM ============================================================================

echo [1/6] Checking core files...
echo.

if exist "..\RVUCounter.pyw" (
    echo   ✓ RVUCounter.pyw
) else (
    echo   ✗ RVUCounter.pyw NOT FOUND
    set /a ERRORS+=1
)

if exist "..\rvu_settings.yaml" (
    echo   ✓ rvu_settings.yaml
) else (
    echo   ✗ rvu_settings.yaml NOT FOUND
    set /a ERRORS+=1
)

echo.

REM ============================================================================
REM Check Source Modules
REM ============================================================================

echo [2/6] Checking source modules...
echo.

REM Main entry
if exist "..\src\main.py" (
    echo   ✓ src\main.py
) else (
    echo   ✗ src\main.py NOT FOUND
    set /a ERRORS+=1
)

REM UI modules
if exist "..\src\ui\main_window.py" (
    echo   ✓ src\ui\main_window.py
) else (
    echo   ✗ src\ui\main_window.py NOT FOUND
    set /a ERRORS+=1
)

if exist "..\src\ui\statistics_window.py" (
    echo   ✓ src\ui\statistics_window.py
) else (
    echo   ✗ src\ui\statistics_window.py NOT FOUND
    set /a ERRORS+=1
)

if exist "..\src\ui\tools_window.py" (
    echo   ✓ src\ui\tools_window.py [v1.7]
) else (
    echo   ✗ src\ui\tools_window.py NOT FOUND [v1.7 NEW]
    set /a ERRORS+=1
)

if exist "..\src\ui\whats_new_window.py" (
    echo   ✓ src\ui\whats_new_window.py [v1.7]
) else (
    echo   ✗ src\ui\whats_new_window.py NOT FOUND [v1.7 NEW]
    set /a ERRORS+=1
)

REM Data modules
if exist "..\src\data\data_manager.py" (
    echo   ✓ src\data\data_manager.py
) else (
    echo   ✗ src\data\data_manager.py NOT FOUND
    set /a ERRORS+=1
)

if exist "..\src\data\backup_manager.py" (
    echo   ✓ src\data\backup_manager.py
) else (
    echo   ✗ src\data\backup_manager.py NOT FOUND
    set /a ERRORS+=1
)

REM Logic modules
if exist "..\src\logic\study_matcher.py" (
    echo   ✓ src\logic\study_matcher.py
) else (
    echo   ✗ src\logic\study_matcher.py NOT FOUND
    set /a ERRORS+=1
)

if exist "..\src\logic\database_repair.py" (
    echo   ✓ src\logic\database_repair.py [v1.7]
) else (
    echo   ✗ src\logic\database_repair.py NOT FOUND [v1.7 NEW]
    set /a ERRORS+=1
)

if exist "..\src\logic\excel_checker.py" (
    echo   ✓ src\logic\excel_checker.py [v1.7]
) else (
    echo   ✗ src\logic\excel_checker.py NOT FOUND [v1.7 NEW]
    set /a ERRORS+=1
)

REM Core modules
if exist "..\src\core\config.py" (
    echo   ✓ src\core\config.py
) else (
    echo   ✗ src\core\config.py NOT FOUND
    set /a ERRORS+=1
)

if exist "..\src\core\platform_utils.py" (
    echo   ✓ src\core\platform_utils.py
) else (
    echo   ✗ src\core\platform_utils.py NOT FOUND
    set /a ERRORS+=1
)

if exist "..\src\core\update_manager.py" (
    echo   ✓ src\core\update_manager.py [v1.7]
) else (
    echo   ✗ src\core\update_manager.py NOT FOUND [v1.7 NEW]
    set /a ERRORS+=1
)

echo.

REM ============================================================================
REM Check Helper Files
REM ============================================================================

echo [3/6] Checking helper files...
echo.

if exist "..\helpers\updater.bat" (
    echo   ✓ helpers\updater.bat [v1.7 CRITICAL]
) else (
    echo   ✗ helpers\updater.bat NOT FOUND [v1.7 CRITICAL]
    echo     This file is REQUIRED for auto-update to work!
    set /a ERRORS+=1
)

echo.

REM ============================================================================
REM Check Documentation
REM ============================================================================

echo [4/6] Checking documentation...
echo.

if exist "..\documentation\WHATS_NEW_v1.7.md" (
    echo   ✓ documentation\WHATS_NEW_v1.7.md [v1.7 CRITICAL]
) else (
    echo   ✗ documentation\WHATS_NEW_v1.7.md NOT FOUND [v1.7 CRITICAL]
    echo     This file is REQUIRED for What's New viewer!
    set /a ERRORS+=1
)

REM Optional but recommended
if exist "..\documentation\TESTING_GUIDE_v1.7.md" (
    echo   ✓ documentation\TESTING_GUIDE_v1.7.md [optional]
) else (
    echo   ⚠ documentation\TESTING_GUIDE_v1.7.md not found [optional]
    set /a WARNINGS+=1
)

if exist "..\documentation\AUTO_UPDATE_DESIGN.md" (
    echo   ✓ documentation\AUTO_UPDATE_DESIGN.md [optional]
) else (
    echo   ⚠ documentation\AUTO_UPDATE_DESIGN.md not found [optional]
    set /a WARNINGS+=1
)

echo.

REM ============================================================================
REM Check Dependencies
REM ============================================================================

echo [5/6] Checking Python dependencies...
echo.

REM Check if Python is available
python --version >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo   ✓ Python is available
    
    REM Check PyInstaller
    python -c "import PyInstaller" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo   ✓ PyInstaller installed
    ) else (
        echo   ✗ PyInstaller NOT installed
        echo     Install with: pip install pyinstaller
        set /a ERRORS+=1
    )
    
    REM Check PyYAML
    python -c "import yaml" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo   ✓ PyYAML installed
    ) else (
        echo   ✗ PyYAML NOT installed
        echo     Install with: pip install PyYAML
        set /a ERRORS+=1
    )
    
    REM Check matplotlib
    python -c "import matplotlib" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo   ✓ matplotlib installed
    ) else (
        echo   ✗ matplotlib NOT installed
        echo     Install with: pip install matplotlib
        set /a ERRORS+=1
    )
    
    REM Check numpy
    python -c "import numpy" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo   ✓ numpy installed
    ) else (
        echo   ✗ numpy NOT installed
        echo     Install with: pip install numpy
        set /a ERRORS+=1
    )
    
    REM Check openpyxl (v1.7 new)
    python -c "import openpyxl" >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        echo   ✓ openpyxl installed [v1.7 NEW]
    ) else (
        echo   ✗ openpyxl NOT installed [v1.7 NEW]
        echo     Install with: pip install openpyxl
        set /a ERRORS+=1
    )
    
) else (
    echo   ✗ Python NOT FOUND in PATH
    set /a ERRORS+=1
)

echo.

REM ============================================================================
REM Check RVU Classification Fixes
REM ============================================================================

echo [6/6] Checking RVU classification fixes...
echo.

REM Check if new RVU entries exist in yaml
findstr /C:"CT Abdomen: 1.0" "..\rvu_settings.yaml" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo   ✓ CT Abdomen rule found [v1.7 fix]
) else (
    echo   ⚠ CT Abdomen rule not found [v1.7 fix]
    set /a WARNINGS+=1
)

findstr /C:"MRI Hip Bilateral: 3.5" "..\rvu_settings.yaml" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo   ✓ MRI Hip Bilateral rule found [v1.7 fix]
) else (
    echo   ⚠ MRI Hip Bilateral rule not found [v1.7 fix]
    set /a WARNINGS+=1
)

findstr /C:"CT Triple Spine: 5.25" "..\rvu_settings.yaml" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo   ✓ CT Triple Spine rule found [v1.7 fix]
) else (
    echo   ⚠ CT Triple Spine rule not found [v1.7 fix]
    set /a WARNINGS+=1
)

echo.

REM ============================================================================
REM Summary
REM ============================================================================

echo ========================================
echo Verification Summary
echo ========================================
echo.

if %ERRORS% EQU 0 (
    if %WARNINGS% EQU 0 (
        echo Status: ✅ ALL CHECKS PASSED
        echo.
        echo Ready to package! Run: package RVUCounter.bat
    ) else (
        echo Status: ⚠ PASSED WITH WARNINGS
        echo.
        echo Errors: %ERRORS%
        echo Warnings: %WARNINGS%
        echo.
        echo You can proceed with packaging, but some optional files are missing.
        echo Run: package RVUCounter.bat
    )
) else (
    echo Status: ❌ FAILED
    echo.
    echo Errors: %ERRORS%
    echo Warnings: %WARNINGS%
    echo.
    echo Please fix the errors above before packaging.
    echo See PACKAGING_GUIDE.md for help.
)

echo.
echo ========================================
echo.

if %ERRORS% GTR 0 (
    pause
    exit /b 1
)

pause
exit /b 0





