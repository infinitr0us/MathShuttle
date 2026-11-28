<#
Build dist\MathShuttle\MathShuttle.exe with PyInstaller.

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
& $py -m PyInstaller --noconfirm --clean --windowed --name MathShuttle `
    --icon assets\icon.ico --distpath dist --workpath build `
    --exclude-module numpy --exclude-module PIL --exclude-module pytest `
    --add-data "mathshuttle\sv_theme;mathshuttle\sv_theme" `
    launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

if ($BundlePandoc) {
    # The pandoc the app itself would use, with a Scoop shim followed to the real executable:
    # a copied launcher (Scoop / Chocolatey shim) would not run without its companion files.
    # ASCII-only JSON: the pipe uses the console code page (cp936...), which cannot hold every path
    $json = & $py -c "import json; from mathshuttle import pandoc_engine as p; print(json.dumps({'pandoc': p.pandoc_binary(), 'texmath': p.texmath_version() or ''}))"
    if ($LASTEXITCODE -ne 0) { throw "pandoc.exe not found - install it with: winget install JohnMacFarlane.Pandoc" }
    $info = $json | ConvertFrom-Json
    $pandoc, $texmath = $info.pandoc, $info.texmath
    if (-not $texmath) { throw "$pandoc looks like a launcher, not pandoc itself - set MATHSHUTTLE_PANDOC to the real pandoc.exe" }
    Copy-Item $pandoc "dist\MathShuttle\pandoc.exe" -Force
    Write-Host "Bundled $pandoc (texmath $texmath)" -ForegroundColor Cyan
}

$exe = (Resolve-Path "dist\MathShuttle\MathShuttle.exe").Path
Write-Host "Built $exe" -ForegroundColor Green

$dirs = @()
if ($Shortcut -or $DesktopShortcut) { $dirs += [Environment]::GetFolderPath("Programs") }
if ($DesktopShortcut) { $dirs += [Environment]::GetFolderPath("Desktop") }
if ($dirs) {
    $shell = New-Object -ComObject WScript.Shell
    foreach ($dir in $dirs) {
        $lnk = $shell.CreateShortcut((Join-Path $dir "MathShuttle.lnk"))
        $lnk.TargetPath = $exe
        $lnk.WorkingDirectory = Split-Path $exe
        $lnk.IconLocation = $exe
        $lnk.Description = "LaTeX / MathML / OMML equation converter"
        $lnk.Save()
        Write-Host "Shortcut: $(Join-Path $dir 'MathShuttle.lnk')" -ForegroundColor Green
    }
}
