param(
    [Parameter(Position = 0)]
    [ValidateSet("init", "run", "web", "select", "export", "calendar", "doctor")]
    [string]$Command = "web",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Windows .venv was not found. Run Start-Quant-Web.bat first, or create .venv manually."
    $Python = "python"
}

& $Python main.py $Command @Args
