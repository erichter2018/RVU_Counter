@echo off
echo ========================================
echo Packaging RVU Counter with PyInstaller
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

REM Check if RVUCounter.pyw exists in parent directory
if not exist "..\RVUCounter.pyw" (
    echo ERROR: RVUCounter.pyw not found in parent directory!
    pause
    exit /b 1
)

REM Run PyInstaller from parent directory, outputting to packaging folder
echo Building executable...
echo Bundling rvu_settings.json from parent directory...
set PARENT_DIR=%~dp0..
pushd "%PARENT_DIR%"
set ABS_JSON=%CD%\rvu_settings.json
pyinstaller --onefile --windowed --add-data "%ABS_JSON%;." --name "RVU Counter" --distpath "packaging\dist" --workpath "packaging\build" --specpath "packaging" --clean RVUCounter.pyw
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
echo RVU Counter.exe is ready in packaging folder.
echo ========================================
echo.
pause

