# Post-close daily run.
#
# Order matters: backfill first so the day's bhavcopy is stored, then settle open journal
# entries against those fresh prices, then produce the brief. Running the brief first would
# score the day against yesterday's data.
#
# NSE closes at 15:30 IST. The bhavcopy is usually published by ~18:00 IST, so schedule this
# for 18:30 IST or later — running earlier just 404s on the archive and produces a brief
# from stale data.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "data\logs"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

$Stamp = Get-Date -Format "yyyy-MM-dd"
$Log = Join-Path $LogDir "run_$Stamp.log"

function Write-Log($Message) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $Message"
    Write-Output $line
    Add-Content -Path $Log -Value $line -Encoding utf8
}

Set-Location $ProjectRoot
$env:PYTHONIOENCODING = "utf-8"

Write-Log "=== Asymmetry daily run ==="

try {
    Write-Log "1/4 backfill"
    & $Python -m asymmetry.cli backfill --days 10 2>&1 | Tee-Object -FilePath $Log -Append

    Write-Log "2/4 settle journal"
    & $Python -m asymmetry.cli journal settle 2>&1 | Tee-Object -FilePath $Log -Append

    Write-Log "3/4 brief + dashboard"
    & $Python -m asymmetry.cli brief --html 2>&1 | Tee-Object -FilePath $Log -Append

    Write-Log "4/4 done"

    $Html = Join-Path $ProjectRoot "data\briefs\$Stamp.html"
    if (Test-Path $Html) {
        Write-Log "Dashboard: $Html"
    } else {
        # Not an error: on a holiday there is no bhavcopy, so the brief carries the last
        # trading day's date instead of today's.
        Write-Log "No dashboard for $Stamp (market holiday, or bhavcopy not yet published)"
    }
}
catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    exit 1
}
