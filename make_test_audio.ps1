$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
New-Item -ItemType Directory -Path (Join-Path $root 'fixtures') -Force | Out-Null
$voice = New-Object -ComObject SAPI.SpVoice
$chinese = @($voice.GetVoices() | Where-Object { $_.GetDescription() -match 'Huihui|Chinese' })
if ($chinese.Count -eq 0) { throw 'No Chinese Windows speech voice is installed.' }
$voice.Voice = $chinese[0]
$stream = New-Object -ComObject SAPI.SpFileStream
$stream.Format.Type = 18
$stream.Open((Join-Path $root 'fixtures\chinese-meeting.wav'), 3, $false)
try {
    $voice.AudioOutputStream = $stream
    $text = [System.IO.File]::ReadAllText((Join-Path $root 'fixtures\chinese-meeting.txt'), [System.Text.Encoding]::UTF8)
    [void]$voice.Speak($text)
} finally { $stream.Close() }
Write-Output 'Created local synthetic Chinese test audio; nothing was played or recorded.'
