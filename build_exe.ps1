<#
Build dist\EquationTool\EquationTool.exe with PyInstaller.

    .\build_exe.ps1                 # build (creates .venv on first run)
    .\build_exe.ps1 -BundlePandoc   # also copy pandoc.exe next to the exe (fully self-contained, ~180 MB more)
    .\build_exe.ps1 -Shortcut       # also create a Start Menu shortcut (-DesktopShortcut: desktop too)

The exe looks for pandoc next to itself, then on PATH, then in %LOCALAPPDATA%\Pandoc.
#>
param(
    [switch]$BundlePandoc,
    [switch]$Shortcut,
    [switch]$DesktopShortcut,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating build venv..." -ForegroundColor Cyan
    & $Python -m venv .venv
}
$py = ".venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip pyinstaller pillow
& $py tools\make_icon.py

Write-Host "Running PyInstaller..." -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean --windowed --name EquationTool `
    --icon assets\icon.ico --distpath dist --workpath build `
    --exclude-module numpy --exclude-module PIL --exclude-module pytest `
    launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

if ($BundlePandoc) {
    $pandoc = (Get-Command pandoc -ErrorAction SilentlyContinue).Source
    if (-not $pandoc) { $pandoc = Join-Path $env:LOCALAPPDATA "Pandoc\pandoc.exe" }
    if (-not (Test-Path $pandoc)) { throw "pandoc.exe not found - install it with: winget install JohnMacFarlane.Pandoc" }
    Copy-Item $pandoc "dist\EquationTool\pandoc.exe" -Force
    Write-Host "Bundled $pandoc" -ForegroundColor Cyan
}

$exe = (Resolve-Path "dist\EquationTool\EquationTool.exe").Path
Write-Host "Built $exe" -ForegroundColor Green

$dirs = @()
if ($Shortcut -or $DesktopShortcut) { $dirs += [Environment]::GetFolderPath("Programs") }
if ($DesktopShortcut) { $dirs += [Environment]::GetFolderPath("Desktop") }
if ($dirs) {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($dir in $dirs) {
        $lnk = $shell.CreateShortcut((Join-Path $dir "Equation Tool.lnk"))
        $lnk.TargetPath = $exe
        $lnk.WorkingDirectory = Split-Path $exe
        $lnk.IconLocation = $exe
        $lnk.Description = "LaTeX / MathML / OMML equation converter"
        $lnk.Save()
        Write-Host "Shortcut: $(Join-Path $dir 'Equation Tool.lnk')" -ForegroundColor Green
    }
}
