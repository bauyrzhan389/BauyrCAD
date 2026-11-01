# Ends the SolidWorks instances BauyrCAD's own COM sessions left behind (tests, the SolidWorks
# export, format experiments): only an SLDWORKS.exe that has no window AND was started by
# Windows' COM launcher (svchost -k DcomLaunch). A SolidWorks with a window, or one started any
# other way (the user's own session), is never touched.
#
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/end_hidden_solidworks.ps1

$dcom = @(Get-CimInstance Win32_Process -Filter "Name='svchost.exe'" |
          Where-Object { $_.CommandLine -match 'DcomLaunch' } |
          ForEach-Object { $_.ProcessId })
$found = $false
foreach ($p in Get-CimInstance Win32_Process -Filter "Name='SLDWORKS.exe'") {
    $found = $true
    $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $proc) { continue }
    if ($proc.MainWindowHandle -ne 0) {
        "kept  $($p.ProcessId): has a window (the user's session)"
        continue
    }
    if ($dcom -notcontains $p.ParentProcessId) {
        "kept  $($p.ProcessId): not started by COM (parent $($p.ParentProcessId))"
        continue
    }
    Stop-Process -Id $p.ProcessId -Force
    "ended $($p.ProcessId): hidden, started by COM at $($p.CreationDate)"
}
if (-not $found) { "no SolidWorks running" }
