param(
    [int]$Port = 5080,
    [string]$HostAddress = "127.0.0.1",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Find-Python {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    $localPythonRoots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe")
    )
    foreach ($candidate in $localPythonRoots) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    foreach ($command in @("py", "python", "python3")) {
        $resolved = Get-Command $command -ErrorAction SilentlyContinue
        if ($null -ne $resolved) {
            $versionOutput = & $command --version 2>&1
            if ($LASTEXITCODE -eq 0 -and "$versionOutput" -match "Python 3") {
                return $command
            }
        }
    }

    throw "Python 3 was not found. Install Python 3.10+ and enable Add python.exe to PATH."
}

$Python = Find-Python
Write-Host "Using Python: $Python"

if ((Test-Path ".venv") -and -not (Test-Path ".venv\Scripts\python.exe")) {
    $backupName = ".venv_mac_backup_{0}" -f (Get-Date -Format "yyyyMMdd_HHmmss")
    Write-Host "Found a non-Windows .venv. Moving it to $backupName ..."
    Move-Item -LiteralPath ".venv" -Destination $backupName
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Windows virtual environment..."
    & $Python -m venv .venv
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}

if (-not $SkipInstall) {
    Write-Host "Installing/updating dependencies..."
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r requirements.txt
}

$url = "http://${HostAddress}:${Port}"
Write-Host "Starting A-share quant web UI: $url"
Start-Process $url
& $Python main.py web --host $HostAddress --port $Port
