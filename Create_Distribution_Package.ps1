# ============================================================================
# RVU Counter - Distribution Package Creator
# ============================================================================
# This script creates a distribution package for sending to users
# ============================================================================

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "RVU Counter - Distribution Package Creator" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""

# Define package name with timestamp
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$packageName = "RVU_Counter_v1.7_Distribution_$timestamp"
$packagePath = ".\$packageName"

Write-Host "Creating distribution package: $packageName" -ForegroundColor Green
Write-Host ""

# ============================================================================
# Step 1: Create distribution folder
# ============================================================================

Write-Host "[1/5] Creating distribution folder..." -ForegroundColor Yellow

if (Test-Path $packagePath) {
    Remove-Item $packagePath -Recurse -Force
}
New-Item -ItemType Directory -Path $packagePath -Force | Out-Null

Write-Host "      ✓ Folder created: $packagePath" -ForegroundColor Green
Write-Host ""

# ============================================================================
# Step 2: Copy installation script
# ============================================================================

Write-Host "[2/5] Copying installation script..." -ForegroundColor Yellow

$installScript = "Install_or_Upgrade_RVU_Counter.bat"
if (Test-Path $installScript) {
    Copy-Item $installScript $packagePath\
    Write-Host "      ✓ Copied: $installScript" -ForegroundColor Green
} else {
    Write-Host "      ✗ ERROR: $installScript not found!" -ForegroundColor Red
    pause
    exit 1
}

Write-Host ""

# ============================================================================
# Step 3: Copy README
# ============================================================================

Write-Host "[3/5] Copying README..." -ForegroundColor Yellow

$readme = "DISTRIBUTION_README.txt"
if (Test-Path $readme) {
    Copy-Item $readme $packagePath\
    Write-Host "      ✓ Copied: $readme" -ForegroundColor Green
} else {
    Write-Host "      ✗ ERROR: $readme not found!" -ForegroundColor Red
    pause
    exit 1
}

Write-Host ""

# ============================================================================
# Step 4: Optional - Copy executable (for offline distribution)
# ============================================================================

Write-Host "[4/5] Copying executable (optional)..." -ForegroundColor Yellow

$exe = "RVU Counter.exe"
if (Test-Path $exe) {
    Write-Host "      Found: $exe" -ForegroundColor Cyan
    Write-Host "      Include exe for OFFLINE distribution?" -ForegroundColor Cyan
    Write-Host "      (If no, users will download from GitHub automatically)" -ForegroundColor Cyan
    $response = Read-Host "      Include exe? (Y/N)"
    
    if ($response -eq "Y" -or $response -eq "y") {
        Copy-Item $exe $packagePath\
        Write-Host "      ✓ Copied: $exe" -ForegroundColor Green
        Write-Host "      Package includes executable (OFFLINE mode)" -ForegroundColor Green
    } else {
        Write-Host "      ○ Skipped: $exe" -ForegroundColor Gray
        Write-Host "      Package is ONLINE-only (users download from GitHub)" -ForegroundColor Gray
    }
} else {
    Write-Host "      ○ Executable not found (users will download from GitHub)" -ForegroundColor Gray
}

Write-Host ""

# ============================================================================
# Step 5: Create ZIP file
# ============================================================================

Write-Host "[5/5] Creating ZIP file..." -ForegroundColor Yellow

$zipPath = ".\$packageName.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Compress-Archive -Path "$packagePath\*" -DestinationPath $zipPath -Force

Write-Host "      ✓ ZIP created: $packageName.zip" -ForegroundColor Green
Write-Host ""

# ============================================================================
# Summary
# ============================================================================

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "Package Created Successfully!" -ForegroundColor Green
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Distribution package: $packageName.zip" -ForegroundColor White
Write-Host ""

Write-Host "Package contains:" -ForegroundColor White
Get-ChildItem $packagePath | ForEach-Object {
    Write-Host "  • $($_.Name)" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "Package size: $([math]::Round((Get-Item $zipPath).Length / 1MB, 2)) MB" -ForegroundColor White
Write-Host ""

# ============================================================================
# Next steps
# ============================================================================

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "Next Steps" -ForegroundColor Yellow
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "1. Test the package:" -ForegroundColor White
Write-Host "   - Extract $packageName.zip to a test folder" -ForegroundColor Gray
Write-Host "   - Run Install_or_Upgrade_RVU_Counter.bat" -ForegroundColor Gray
Write-Host "   - Verify installation works" -ForegroundColor Gray
Write-Host ""

Write-Host "2. Distribute to users:" -ForegroundColor White
Write-Host "   - Email the ZIP file" -ForegroundColor Gray
Write-Host "   - Or upload to shared drive" -ForegroundColor Gray
Write-Host "   - Include installation instructions" -ForegroundColor Gray
Write-Host ""

Write-Host "3. Monitor deployment:" -ForegroundColor White
Write-Host "   - Track installation success rate" -ForegroundColor Gray
Write-Host "   - Gather user feedback" -ForegroundColor Gray
Write-Host "   - Address any issues" -ForegroundColor Gray
Write-Host ""

Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================================
# Open folder
# ============================================================================

$openFolder = Read-Host "Open distribution folder? (Y/N)"
if ($openFolder -eq "Y" -or $openFolder -eq "y") {
    Start-Process explorer.exe -ArgumentList $packagePath
}

Write-Host ""
Write-Host "Package ready for distribution!" -ForegroundColor Green
Write-Host ""
pause





