# Build the Windows distribution: dist\BauyrCAD\ (app), a .zip and, if Inno Setup is
# installed, an installer.   Run from the repository root:  .\packaging\build_windows.ps1
# The runnable app is dist\BauyrCAD\BauyrCAD.exe (PyInstaller's scratch files go to %TEMP%).
# (Native tools log to stderr, so errors are detected with $LASTEXITCODE, not ErrorAction.)
$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

function Invoke-Step($name, [scriptblock]$block) {
    Write-Host "==> $name"
    & $block
    if ($LASTEXITCODE -ne 0) { Write-Error "$name failed (exit $LASTEXITCODE)"; exit 1 }
}

$version = (& $py -c "import bauyrcad; print(bauyrcad.__version__)").Trim()
Write-Host "Building BauyrCAD $version"

Invoke-Step "Icon" { & $py packaging\make_icon.py }
Invoke-Step "Examples" { & $py examples\make_examples.py | Out-Null }
Invoke-Step "PyInstaller" { & $py -m PyInstaller packaging\bauyrcad.spec --noconfirm --distpath dist --workpath "$env:TEMP\bauyrcad-pyinstaller" 2>&1 | Out-Null }
Invoke-Step "Self-test" { & dist\BauyrCAD\BauyrCAD.exe --selftest "$env:TEMP\bauyrcad-selftest.txt" | Out-Null }
Get-Content "$env:TEMP\bauyrcad-selftest.txt"

New-Item -ItemType Directory -Force dist\BauyrCAD\examples | Out-Null
Copy-Item examples\*.bcad, examples\*.step -Destination dist\BauyrCAD\examples -Force
Copy-Item LICENSE, THIRD_PARTY_NOTICES.md, README.md -Destination dist\BauyrCAD -Force

$zip = "dist\BauyrCAD-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path dist\BauyrCAD -DestinationPath $zip -CompressionLevel Optimal
Write-Host "Wrote $zip"

$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) { $iscc = Get-Command "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" -ErrorAction SilentlyContinue }
if ($iscc) {
    Invoke-Step "Installer" { & $iscc.Source "/DAppVersion=$version" packaging\installer.iss | Out-Null }
    Write-Host "Wrote dist\BauyrCAD-$version-setup.exe"
} else {
    Write-Host "Inno Setup not found - skipped installer (the zip is still a complete app)."
}
