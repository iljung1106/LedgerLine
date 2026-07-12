#Requires -Version 7.0
[CmdletBinding(DefaultParameterSetName = "Plan")]
param(
    [Parameter(ParameterSetName = "Plan")]
    [switch]$Plan,
    [Parameter(Mandatory = $true, ParameterSetName = "Apply")]
    [switch]$Apply,
    [string]$LedgerLineHome
)

$ErrorActionPreference = "Stop"
$pluginRoot = Split-Path -Parent $PSScriptRoot
$wheel = Get-ChildItem -LiteralPath (Join-Path $pluginRoot "assets") -Filter "ledgerline-*.whl" |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (-not $wheel) {
    throw "The plugin does not contain a LedgerLine wheel."
}

if (-not $LedgerLineHome) {
    if ($env:LEDGERLINE_HOME) {
        $LedgerLineHome = $env:LEDGERLINE_HOME
    } elseif ($env:LOCALAPPDATA) {
        $LedgerLineHome = Join-Path $env:LOCALAPPDATA "LedgerLine"
    } else {
        $LedgerLineHome = Join-Path $HOME ".ledgerline"
    }
}
$runtimeRoot = Join-Path $LedgerLineHome "runtime"
$venv = Join-Path $runtimeRoot "venv"
$runtimePython = Join-Path $venv "Scripts\python.exe"

function Resolve-BasePython {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $version = & $py.Source -3.11 -c "import sys; print('.'.join(map(str, sys.version_info[:2])))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq "3.11") {
            return @{ Exe = $py.Source; Prefix = @("-3.11") }
        }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $version = & $python.Source -c "import sys; print('.'.join(map(str, sys.version_info[:2])))" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version -eq "3.11") {
            return @{ Exe = $python.Source; Prefix = @() }
        }
    }
    throw "Python 3.11 was not found. Install it explicitly before applying this plan."
}

$basePython = Resolve-BasePython
$runtimeVersion = $null
if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
    $runtimeVersion = & $runtimePython -c "import ledgerline; print(ledgerline.__version__)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $runtimeVersion = $null
    }
}
$planStatus = if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    "ready"
} elseif ($runtimeVersion -eq "0.4.0") {
    "already_installed"
} else {
    "upgrade_required"
}
$report = [ordered]@{
    schema_version = "1"
    status = $planStatus
    action = "create_managed_python_venv"
    base_python = $basePython.Exe
    destination = $venv
    bundled_wheel = $wheel.FullName
    installed_version = $runtimeVersion
    network_sources = @("https://pypi.org/simple")
    dependencies = @("cryptography>=45,<47", "mido>=1.3.3,<2", "numpy>=2,<3", "PyYAML>=6.0.2,<7")
    system_changes = @()
    modifies_path = $false
    modifies_registry = $false
}
if (-not $Apply) {
    $report | ConvertTo-Json -Depth 5
    exit 0
}

New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
if (-not (Test-Path -LiteralPath $runtimePython)) {
    & $basePython.Exe @($basePython.Prefix) -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw "Python failed to create the managed LedgerLine virtual environment."
    }
}
& $runtimePython -m pip install --disable-pip-version-check --no-input $wheel.FullName
if ($LASTEXITCODE -ne 0) {
    throw "pip failed to install the bundled LedgerLine wheel."
}
& $runtimePython -m pip install --disable-pip-version-check --no-input --force-reinstall --no-deps $wheel.FullName
if ($LASTEXITCODE -ne 0) {
    throw "pip failed to refresh the bundled LedgerLine wheel."
}
& $runtimePython -m pip check
if ($LASTEXITCODE -ne 0) {
    throw "The managed LedgerLine runtime has unsatisfied dependencies."
}
$installed = & $runtimePython -c "import ledgerline; print(ledgerline.__version__)"
if ($LASTEXITCODE -ne 0 -or $installed -ne "0.4.0") {
    throw "LedgerLine runtime verification failed."
}
$env:LEDGERLINE_HOME = $LedgerLineHome
& $runtimePython -m ledgerline doctor --json
exit $LASTEXITCODE
