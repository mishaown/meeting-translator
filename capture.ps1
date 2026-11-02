param(
    [string]$OutputDirectory,
    [ValidateSet('both','mic','system')][string]$Source = 'both',
    [double]$Seconds = 0,
    [int]$ParentProcessId = 0,
    [switch]$ListDevices
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$dataRoot = if ($env:MEETING_DATA_DIR) { $env:MEETING_DATA_DIR } else { $PSScriptRoot }
$runtimeRoot = Join-Path $dataRoot 'runtime'
if (-not (Test-Path (Join-Path $runtimeRoot 'naudio.core'))) { $runtimeRoot = Join-Path $PSScriptRoot 'runtime' }
$core = Join-Path $runtimeRoot 'naudio.core\lib\netstandard2.0\NAudio.Core.dll'
$wasapi = Join-Path $runtimeRoot 'naudio.wasapi\lib\netstandard2.0\NAudio.Wasapi.dll'
Add-Type -Path $core
Add-Type -Path $wasapi
$netstandard = Get-ChildItem "$env:WINDIR\Microsoft.NET\assembly\GAC_MSIL\netstandard" -Filter netstandard.dll -Recurse | Select-Object -First 1 -ExpandProperty FullName
Add-Type -Path (Join-Path $PSScriptRoot 'Capture.cs') -ReferencedAssemblies @($core,$wasapi,$netstandard)
if ($ListDevices) { [MeetingCapture]::Devices(); exit 0 }
if (-not $OutputDirectory) { throw 'OutputDirectory is required' }
try { [MeetingCapture]::Run($OutputDirectory, $Source, $Seconds, $ParentProcessId) }
catch { $_ | Out-String | Write-Error; exit 1 }
