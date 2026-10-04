<#
.SYNOPSIS
    Builds Moodboard for Windows.

.DESCRIPTION
    Produces, in dist\:
      Moodboard\                       the app folder (Moodboard.exe + libraries)
      Moodboard-<version>-windows.zip  portable version: unzip and run
      Moodboard-Setup-<version>.exe    installer (only if Inno Setup 6 is installed)
    The packaged app is smoke-tested with --self-test before anything is zipped.

.EXAMPLE
    .\packaging\build.ps1                  # version taken from version.py
    .\packaging\build.ps1 -Version 1.2.0   # stamps 1.2.0 into version.py (what the release workflow does)
#>
param([string]$Version)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

if ($Version) {
    # Stamp the version into the app (shown in Help > About). UTF-8 without BOM.
    $text = "`"`"`"App version. Release builds overwrite this from the git tag (v1.2.3 -> `"1.2.3`").`"`"`"`n" +
            "__version__ = `"$Version`"`n"
    [IO.File]::WriteAllText((Join-Path $root "version.py"), $text)
} else {
    $Version = (& $python -c "import version; print(version.__version__)").Trim()
}
# Windows version resources need plain numbers: "1.2.0-beta" -> "1.2.0.0"
$parts = @(([regex]::Match($Version, '^\d+(\.\d+){0,3}')).Value.Split('.') | Where-Object { $_ })
while ($parts.Count -lt 4) { $parts += "0" }
$numeric = $parts[0..3] -join "."

Write-Host "==> Building Moodboard $Version"
& $python -m PyInstaller moodboard.spec --noconfirm --clean --log-level WARN
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$app = Join-Path $root "dist\Moodboard"
Copy-Item THIRD_PARTY_NOTICES.md $app
Copy-Item licenses $app -Recurse -Force

Write-Host "==> Self-test of the packaged app"
$result = Join-Path ([IO.Path]::GetTempPath()) "moodboard-selftest.txt"
Remove-Item $result -ErrorAction SilentlyContinue
# Start-Process -Wait: a windowed .exe would otherwise return immediately.
$proc = Start-Process -FilePath (Join-Path $app "Moodboard.exe") -ArgumentList "--self-test", "`"$result`"" -Wait -PassThru
if (Test-Path $result) { Get-Content $result | ForEach-Object { Write-Host "    $_" } }
if ($proc.ExitCode -ne 0) { throw "Self-test failed (exit code $($proc.ExitCode))" }

Write-Host "==> Portable zip"
$zip = Join-Path $root "dist\Moodboard-$Version-windows.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path $app -DestinationPath $zip  # unzips to a Moodboard\ folder

Write-Host "==> Installer"
$iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($iscc) {
    & $iscc /Qp "/DAppVersion=$Version" "/DAppVersionNumeric=$numeric" (Join-Path $root "packaging\installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
} else {
    Write-Warning "Inno Setup 6 not found, so no installer was built. Get it from https://jrsoftware.org/isdl.php"
}

Write-Host "==> Done:"
Get-ChildItem (Join-Path $root "dist") -File | ForEach-Object { Write-Host ("    {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)) }
