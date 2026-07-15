#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Project,
    [ValidateSet("Start", "Status", "Rebuild", "Stop")]
    [string]$Action = "Start",
    [string]$HostName = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [switch]$Prepare,
    [switch]$Open,
    [string]$FluidSynth,
    [string]$SoundFont,
    [string]$FFmpeg
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
    throw "LedgerLine runtime is missing. Run bootstrap.ps1 -Plan and apply the approved plan first."
}
$runtimePython = (Resolve-Path -LiteralPath $runtimePython).Path
$env:LEDGERLINE_HOME = $ledgerHome

$projectRoot = (Resolve-Path -LiteralPath $Project).Path
$controlRoot = Join-Path $projectRoot ".ledgerline"
$statePath = Join-Path $controlRoot "studio-process.json"
$stdoutPath = Join-Path $controlRoot "studio.stdout.log"
$stderrPath = Join-Path $controlRoot "studio.stderr.log"
$url = "http://${HostName}:$Port/"
New-Item -ItemType Directory -Force -Path $controlRoot | Out-Null

function Read-StudioState {
    if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
        return $null
    }
    return Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
}

function Test-StudioHealth([string]$CandidateUrl = $url) {
    try {
        $healthUrl = ([string]$CandidateUrl).TrimEnd('/') + "/api/health"
        $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
        if ($health.status -ne "ok" -or -not $health.project) {
            return $false
        }
        $reportedProject = [System.IO.Path]::GetFullPath([string]$health.project)
        return $reportedProject.Equals(
            [System.IO.Path]::GetFullPath($projectRoot),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch {
        return $false
    }
}

function Get-StudioProcessIdentity($State) {
    if (-not $State -or -not $State.pid) {
        return [pscustomobject]@{
            status = "missing"
            reason = "No recorded Studio process exists."
            process = $null
            expected_start_time_utc = $null
            actual_start_time_utc = $null
            expected_executable = $null
            actual_executable = $null
        }
    }
    $candidate = Get-Process -Id ([int]$State.pid) -ErrorAction SilentlyContinue
    if (-not $candidate) {
        return [pscustomobject]@{
            status = "missing"
            reason = "The recorded PID is not running."
            process = $null
            expected_start_time_utc = [string]$State.process_start_time_utc
            actual_start_time_utc = $null
            expected_executable = [string]$State.process_executable_path
            actual_executable = $null
        }
    }

    $expectedStartValue = $State.process_start_time_utc
    $expectedStart = if ($expectedStartValue -is [DateTimeOffset]) {
        $expectedStartValue.ToUniversalTime().ToString(
            "o", [System.Globalization.CultureInfo]::InvariantCulture
        )
    } elseif ($expectedStartValue -is [DateTime]) {
        $expectedStartValue.ToUniversalTime().ToString(
            "o", [System.Globalization.CultureInfo]::InvariantCulture
        )
    } else {
        [string]$expectedStartValue
    }
    $expectedExecutable = [string]$State.process_executable_path
    $expectedRuntime = [string]$State.runtime_path
    if (-not $expectedStart -or -not $expectedExecutable -or -not $expectedRuntime) {
        return [pscustomobject]@{
            status = "stale"
            reason = "The process record predates PID identity fields and cannot be trusted."
            process = $candidate
            expected_start_time_utc = $expectedStart
            actual_start_time_utc = $null
            expected_executable = $expectedExecutable
            actual_executable = $null
        }
    }

    try {
        $actualStart = $candidate.StartTime.ToUniversalTime()
        $actualStartText = $actualStart.ToString(
            "o", [System.Globalization.CultureInfo]::InvariantCulture
        )
        $recordedStart = [DateTimeOffset]::Parse(
            $expectedStart,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::RoundtripKind
        ).UtcDateTime
        $actualExecutable = [System.IO.Path]::GetFullPath([string]$candidate.Path)
        $recordedExecutable = [System.IO.Path]::GetFullPath($expectedExecutable)
        $recordedRuntime = [System.IO.Path]::GetFullPath($expectedRuntime)
    } catch {
        return [pscustomobject]@{
            status = "stale"
            reason = "The recorded or live process identity could not be read safely."
            process = $candidate
            expected_start_time_utc = $expectedStart
            actual_start_time_utc = $null
            expected_executable = $expectedExecutable
            actual_executable = $null
        }
    }

    $startMatches = [Math]::Abs(($actualStart - $recordedStart).TotalMilliseconds) -le 1.0
    $executableMatches = $actualExecutable.Equals(
        $recordedExecutable, [System.StringComparison]::OrdinalIgnoreCase
    ) -and $actualExecutable.Equals(
        $recordedRuntime, [System.StringComparison]::OrdinalIgnoreCase
    )
    if (-not $startMatches -or -not $executableMatches) {
        $mismatch = @()
        if (-not $startMatches) { $mismatch += "process start time" }
        if (-not $executableMatches) { $mismatch += "runtime executable" }
        return [pscustomobject]@{
            status = "stale"
            reason = "PID identity mismatch: $($mismatch -join ' and '). Stop was not attempted."
            process = $candidate
            expected_start_time_utc = $expectedStart
            actual_start_time_utc = $actualStartText
            expected_executable = $expectedExecutable
            actual_executable = $actualExecutable
        }
    }
    return [pscustomobject]@{
        status = "verified"
        reason = "PID, process start time, and runtime executable match the recorded Studio process."
        process = $candidate
        expected_start_time_utc = $expectedStart
        actual_start_time_utc = $actualStartText
        expected_executable = $expectedExecutable
        actual_executable = $actualExecutable
    }
}

function Convert-ProcessIdentityReport($Identity) {
    return [ordered]@{
        status = $Identity.status
        reason = $Identity.reason
        expected_start_time_utc = $Identity.expected_start_time_utc
        actual_start_time_utc = $Identity.actual_start_time_utc
        expected_executable = $Identity.expected_executable
        actual_executable = $Identity.actual_executable
    }
}

function Get-RecordedHealthyStudio {
    $state = Read-StudioState
    if (-not $state -or -not $state.url) {
        return $null
    }
    $identity = Get-StudioProcessIdentity $state
    if ($identity.status -ne "verified") {
        return $null
    }
    if (-not (Test-StudioHealth -CandidateUrl ([string]$state.url))) {
        return $null
    }
    return $state
}

function Emit-Report([hashtable]$Report) {
    $Report | ConvertTo-Json -Depth 8
}

function Invoke-LedgerLine([string[]]$Arguments) {
    $output = & $runtimePython -m ledgerline @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($output -join [Environment]::NewLine)
    }
    return ($output -join [Environment]::NewLine)
}

function Resolve-ExplicitFile([string]$ParameterName, [string]$Value) {
    if (-not (Test-Path -LiteralPath $Value -PathType Leaf)) {
        throw "Explicit -$ParameterName path is not a file: $Value"
    }
    return (Resolve-Path -LiteralPath $Value).Path
}

function Get-LegacyRenderConfig {
    $missing = @()
    if (-not $FluidSynth) { $missing += "-FluidSynth" }
    if (-not $SoundFont) { $missing += "-SoundFont" }
    if (-not $FFmpeg) { $missing += "-FFmpeg" }
    if ($missing.Count -gt 0) {
        $missingText = $missing -join ", "
        throw (
            "Legacy audio preparation requires explicit -FluidSynth, -SoundFont, and " +
            "-FFmpeg because this project has no render.yaml. No renderer, instrument, " +
            "or media tool was inferred or substituted. Missing: $missingText. " +
            "Start without -Prepare remains available for score and MIDI inspection."
        )
    }
    return [ordered]@{
        fluidsynth = (Resolve-ExplicitFile "FluidSynth" $FluidSynth)
        soundfont = (Resolve-ExplicitFile "SoundFont" $SoundFont)
        ffmpeg = (Resolve-ExplicitFile "FFmpeg" $FFmpeg)
    }
}

function Invoke-Rebuild {
    $steps = [ordered]@{}
    $renderPath = Join-Path $projectRoot "render.yaml"
    $hasRenderGraph = Test-Path -LiteralPath $renderPath -PathType Leaf
    $legacy = if ($hasRenderGraph) { $null } else { Get-LegacyRenderConfig }
    if ($hasRenderGraph -and -not $FFmpeg) {
        throw (
            "Audio preparation for a render.yaml project requires explicit -FFmpeg. " +
            "The Studio launcher does not infer an unmanaged FFmpeg from PATH or environment. " +
            "Start without -Prepare remains available for score and MIDI inspection."
        )
    }
    $explicitFFmpeg = if ($hasRenderGraph) { Resolve-ExplicitFile "FFmpeg" $FFmpeg } else { $null }

    $steps.compile = Invoke-LedgerLine @("compile", $projectRoot, "--json")
    $renderArguments = @("render", $projectRoot)
    if ($hasRenderGraph) {
        $renderArguments += @("--ffmpeg", $explicitFFmpeg)
    } else {
        $renderArguments += @(
            "--fluidsynth", $legacy.fluidsynth,
            "--soundfont", $legacy.soundfont,
            "--ffmpeg", $legacy.ffmpeg
        )
    }
    $renderArguments += "--json"
    $steps.render = Invoke-LedgerLine $renderArguments

    if (Test-Path -LiteralPath (Join-Path $projectRoot "mix.yaml") -PathType Leaf) {
        $mixArguments = @("mix", $projectRoot)
        $mixFFmpeg = if ($hasRenderGraph) { $explicitFFmpeg } else { $legacy.ffmpeg }
        $mixArguments += @("--ffmpeg", $mixFFmpeg)
        $mixArguments += "--json"
        $steps.mix = Invoke-LedgerLine $mixArguments
    }
    return $steps
}

function Assert-NoRunningStudioReconfiguration([switch]$Always) {
    $configurationRequested = $Always -or $Prepare -or $FluidSynth -or $SoundFont -or $FFmpeg
    $recordedStudio = Get-RecordedHealthyStudio
    $healthy = (Test-StudioHealth) -or $null -ne $recordedStudio
    if ($configurationRequested -and $healthy) {
        throw (
            "A healthy Studio is already running for this project. Its approved engine paths " +
            "and production artifacts cannot be changed in place. Run -Action Stop, then Start " +
            "again with -Prepare and the explicit tool paths, or request a build through the " +
            "running Studio API. No compile, render, or mix command was started and the existing " +
            "process was not signaled."
        )
    }
}

if ($Action -eq "Status") {
    $state = Read-StudioState
    $identity = Get-StudioProcessIdentity $state
    $recordedStudio = Get-RecordedHealthyStudio
    $healthy = $null -ne $recordedStudio
    $reportedUrl = if ($healthy) { [string]$recordedStudio.url } elseif ($state.url) {
        [string]$state.url
    } else {
        $url
    }
    Emit-Report @{
        schema_version = "1"
        status = if ($healthy) {
            "running"
        } elseif ($identity.status -eq "stale") {
            "stale"
        } else {
            "stopped"
        }
        project = $projectRoot
        url = $reportedUrl
        process = $state
        process_identity = (Convert-ProcessIdentityReport $identity)
    }
    exit 0
}

if ($Action -eq "Stop") {
    $state = Read-StudioState
    $identity = Get-StudioProcessIdentity $state
    $reportedUrl = if ($state -and $state.url) { [string]$state.url } else { $url }
    if ($identity.status -eq "verified") {
        # Kill through the verified Process handle so a PID recycled after verification is not used.
        $identity.process.Kill()
        $identity.process.WaitForExit(5000)
    }
    Emit-Report @{
        schema_version = "1"
        status = if ($identity.status -eq "stale") { "stale" } else { "stopped" }
        project = $projectRoot
        url = $reportedUrl
        process_identity = (Convert-ProcessIdentityReport $identity)
    }
    exit 0
}

if ($Action -eq "Rebuild") {
    Assert-NoRunningStudioReconfiguration -Always
    $steps = Invoke-Rebuild
    Emit-Report @{
        schema_version = "1"
        status = "ready"
        project = $projectRoot
        url = $url
        steps = $steps
    }
    exit 0
}

Assert-NoRunningStudioReconfiguration
if ($Prepare) {
    $null = Invoke-Rebuild
}
Assert-NoRunningStudioReconfiguration
$recordedStudio = Get-RecordedHealthyStudio
if ($recordedStudio) {
    $recordedUrl = [string]$recordedStudio.url
    if ($Open) {
        Start-Process $recordedUrl
    }
    Emit-Report @{
        schema_version = "1"
        status = "running"
        project = $projectRoot
        url = $recordedUrl
        pid = [int]$recordedStudio.pid
        reused = $true
    }
    exit 0
}
if (Test-StudioHealth) {
    if ($Open) {
        Start-Process $url
    }
    Emit-Report @{
        schema_version = "1"
        status = "running"
        project = $projectRoot
        url = $url
        reused = $true
    }
    exit 0
}

$arguments = @(
    "-m",
    "ledgerline",
    "studio",
    ('"' + $projectRoot + '"'),
    "--host",
    $HostName,
    "--port",
    [string]$Port,
    "--no-open"
)
if ($FFmpeg) {
    $arguments += @("--ffmpeg", ('"' + (Resolve-ExplicitFile "FFmpeg" $FFmpeg) + '"'))
}
if ($FluidSynth) {
    $arguments += @(
        "--fluidsynth",
        ('"' + (Resolve-ExplicitFile "FluidSynth" $FluidSynth) + '"')
    )
}
if ($SoundFont) {
    $arguments += @("--soundfont", ('"' + (Resolve-ExplicitFile "SoundFont" $SoundFont) + '"'))
}
$process = Start-Process -FilePath $runtimePython -ArgumentList $arguments -PassThru `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
$process.Refresh()
$processStartTime = $process.StartTime.ToUniversalTime().ToString(
    "o", [System.Globalization.CultureInfo]::InvariantCulture
)
$processExecutable = [System.IO.Path]::GetFullPath([string]$process.Path)
[ordered]@{
    schema_version = "1"
    pid = $process.Id
    project = $projectRoot
    url = $url
    stdout = $stdoutPath
    stderr = $stderrPath
    started_at = $processStartTime
    process_start_time_utc = $processStartTime
    runtime_path = $runtimePython
    process_executable_path = $processExecutable
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding utf8

$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (Test-StudioHealth) {
        $ready = $true
        break
    }
    if ($process.HasExited) {
        break
    }
    Start-Sleep -Milliseconds 250
}
if (-not $ready) {
    $errorTail = if (Test-Path -LiteralPath $stderrPath) {
        (Get-Content -LiteralPath $stderrPath -Tail 30) -join [Environment]::NewLine
    } else {
        "Studio did not report a health response."
    }
    throw "LedgerLine Studio failed to start. $errorTail"
}
if ($Open) {
    Start-Process $url
}
Emit-Report @{
    schema_version = "1"
    status = "running"
    project = $projectRoot
    url = $url
    pid = $process.Id
    reused = $false
}
