# Builds a standalone Windows app in dist\KingCoach\ (no Python needed to run it).
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1
# Zip the whole dist\KingCoach folder to share it. The player's own data
# (chessprep.db) is created next to KingCoach.exe on first run.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --quiet pyinstaller

$addData = @("--add-data", "fonts;fonts")
if (Test-Path stockfish) {
    # bundle the engine so Puzzle Review / Play it out work out of the box
    $addData += @("--add-data", "stockfish;stockfish")
} else {
    Write-Warning "No stockfish\ folder found - the app will ask users to add Stockfish themselves."
}

python -m PyInstaller --noconfirm --clean --windowed --name KingCoach `
    --collect-data customtkinter @addData gui.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# opening files next to the .exe, where Import PGN file... can find them
Copy-Item *.pgn dist\KingCoach\ -Force
Write-Host "Built dist\KingCoach\KingCoach.exe"
