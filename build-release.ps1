# build-release.ps1 - QQBot Release Builder
# Run this BEFORE creating a GitHub Release.
# It pre-installs everything so users don't need internet.
# Live2D: Cubism 4/5 SDK (pixi-live2d-display-cubism4)
param($version = "v0.10.0")

$root = Split-Path -Parent $PSCommandPath
Set-Location $root

Write-Host "=== QQBot Release Builder $version ===" -ForegroundColor Cyan

# 1. Live2D Models (Cubism 4/5, pre-installed under assets/models/)
Write-Host "[1/4] Live2D Models (Cubism 4/5) — pre-installed" -ForegroundColor Yellow
Write-Host "  7 characters: mutsumi, rana, sub, taki, tomori, umiri, yachiyo" -ForegroundColor Green
Write-Host "  v0.10.0: old Cubism 2 figures.zip extraction removed" -ForegroundColor DarkGray

# 2. Extract Node.js
Write-Host "[2/4] Setting up Node.js..." -ForegroundColor Yellow
if (Test-Path "nodejs.zip") {
    Expand-Archive -Path "nodejs.zip" -DestinationPath "node" -Force
    if (Test-Path "node\node-v22.11.0-win-x64\node.exe") {
        Copy-Item "node\node-v22.11.0-win-x64\*" "node\" -Recurse -Force
        Remove-Item "node\node-v22.11.0-win-x64" -Recurse -Force
    }
    Write-Host "  Node.js extracted" -ForegroundColor Green
}

# 3. Install Live2D dependencies (Electron)
Write-Host "[3/4] Installing Live2D (Electron ~150MB)..." -ForegroundColor Yellow
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
Set-Location modules\live2d
& ..\..\node\npm.cmd install --registry=https://registry.npmmirror.com
Set-Location $root
if (Test-Path "modules\live2d\node_modules\electron") {
    Write-Host "  Live2D installed" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] Live2D failed" -ForegroundColor Red
}

# 4. Create Python venv + install Hermes
Write-Host "[4/4] Installing Hermes..." -ForegroundColor Yellow
python -m venv .venv
.venv\Scripts\activate.bat
pip install -e hermes\ --no-deps
pip install -r hermes\requirements.txt
Write-Host "  Hermes installed" -ForegroundColor Green

# Done
Write-Host ""
Write-Host "=== Build complete ===" -ForegroundColor Cyan
Write-Host "Now create the Release zip manually or run:"
Write-Host "  Compress-Archive -Path * -DestinationPath QQBot-$version.zip"
