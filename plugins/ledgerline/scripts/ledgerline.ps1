#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$LedgerLineArguments
)

$ErrorActionPreference = "Stop"
if ($env:LEDGERLINE_HOME) {
    $ledgerHome = $env:LEDGERLINE_HOME
} elseif ($env:LOCALAPPDATA) {
    $ledgerHome = Join-Path $env:LOCALAPPDATA "LedgerLine"
} else {
    $ledgerHome = Join-Path $HOME ".ledgerline"
}
$runtimePython = if ($env:LEDGERLINE_RUNTIME_PYTHON) {
    $env:LEDGERLINE_RUNTIME_PYTHON
} else {
    Join-Path $ledgerHome "runtime\venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    throw "LedgerLine runtime is missing. Run this plugin's bootstrap.ps1 -Plan, obtain user approval, then run bootstrap.ps1 -Apply."
}
$env:LEDGERLINE_HOME = $ledgerHome
& $runtimePython -m ledgerline @LedgerLineArguments
exit $LASTEXITCODE
