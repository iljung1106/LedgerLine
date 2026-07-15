[CmdletBinding()]
param(
    [string]$FluidSynth,
    [string]$SoundFont,
    [string]$FFmpeg,
    [string]$Sfizz,
    [string]$Sfz,
    [ValidateRange(0, 127)][int]$BankMsb = 0,
    [ValidateRange(0, 127)][int]$BankLsb = 0,
    [ValidateRange(0, 127)][int]$Program = 0,
    [ValidateRange(8000, 384000)][int]$SampleRate = 48000,
    [ValidateRange(1, 600)][int]$TimeoutSeconds = 30,
    [switch]$RequireConfig,
    [string]$KeepOutput
)

$ErrorActionPreference = 'Stop'
$pythonPrefix = @()
$python = $env:LEDGERLINE_RUNTIME_PYTHON
if (-not $python) {
    $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $python = $pythonCommand.Source
        $pythonPrefix = @('-3.11')
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $python = $pythonCommand.Source
        }
    }
}

if (-not $python -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
    @{
        schema_version = '1'
        status = 'failed'
        reason = 'python_not_found'
        detail = 'Set LEDGERLINE_RUNTIME_PYTHON to a Python 3.11+ executable.'
    } | ConvertTo-Json -Depth 4
    exit 1
}

$scriptPath = Join-Path $PSScriptRoot 'external_engine_smoke.py'
$toolArgs = @(
    $scriptPath,
    '--bank-msb', $BankMsb,
    '--bank-lsb', $BankLsb,
    '--program', $Program,
    '--sample-rate', $SampleRate,
    '--timeout', $TimeoutSeconds
)
if ($FluidSynth) { $toolArgs += @('--fluidsynth', $FluidSynth) }
if ($SoundFont) { $toolArgs += @('--soundfont', $SoundFont) }
if ($FFmpeg) { $toolArgs += @('--ffmpeg', $FFmpeg) }
if ($Sfizz) { $toolArgs += @('--sfizz', $Sfizz) }
if ($Sfz) { $toolArgs += @('--sfz', $Sfz) }
if ($RequireConfig) { $toolArgs += '--require-config' }
if ($KeepOutput) { $toolArgs += @('--keep-output', $KeepOutput) }

& $python @pythonPrefix @toolArgs
exit $LASTEXITCODE
