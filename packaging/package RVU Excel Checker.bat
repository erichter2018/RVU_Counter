@echo off
echo ========================================
echo Packaging RVU Excel Checker with PyInstaller
echo ========================================
echo.
echo Looking for source files in parent directory...
echo.

REM Check if rvu_settings.yaml exists in parent directory
if not exist "..\rvu_settings.yaml" (
    echo ERROR: rvu_settings.yaml not found in parent directory!
    echo Please ensure rvu_settings.yaml is present in the parent folder.
    pause
    exit /b 1
)

REM Check if check_rvu_excel_files.py exists in parent directory
if not exist "..\check_rvu_excel_files.py" (
    echo ERROR: check_rvu_excel_files.py not found in parent directory!
    pause
    exit /b 1
)

REM Run PyInstaller from parent directory, outputting to packaging folder
echo Building executable...
echo Bundling rvu_settings.yaml from parent directory...
set PARENT_DIR=%~dp0..
pushd "%PARENT_DIR%"
set ABS_YAML=%CD%\rvu_settings.yaml
pyinstaller --onefile --console --add-data "%ABS_YAML%;." --name "RVU Excel Checker" --distpath "packaging\dist" --workpath "packaging\build" --specpath "packaging" --clean check_rvu_excel_files.py
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
if exist "dist\RVU Excel Checker.exe" (
    move /Y "dist\RVU Excel Checker.exe" "RVU Excel Checker.exe"
    echo Moved RVU Excel Checker.exe to packaging folder.
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

if exist "RVU Excel Checker.spec" (
    del /Q "RVU Excel Checker.spec"
    echo Removed spec file.
)

echo. 
echo ========================================
echo Packaging complete!
echo RVU Excel Checker.exe is ready in packaging folder.
echo ========================================
echo.
pause

