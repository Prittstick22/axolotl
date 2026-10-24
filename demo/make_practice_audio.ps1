param(
    [string]$OutputPath = (Join-Path $PSScriptRoot '..\.axolotl_workspaces\demo-preparation\pitch-reference.wav'),
    [string]$Voice = 'Microsoft Hazel Desktop',
    [ValidateRange(-10, 10)][int]$Rate = 1
)
$ErrorActionPreference = 'Stop'
# A synthetic practice reference, never a real demo recording or human rehearsal.
Add-Type -AssemblyName System.Speech
$document = Get-Content -Encoding utf8 -Raw (Join-Path $PSScriptRoot '..\docs\presentation.md')
$parts = $document -split '## Spoken track|## Delivery cues'
if ($parts.Count -ne 3) { throw 'Could not locate the spoken presentation track.' }
$spoken = $parts[1].Trim() -replace '2\.7', 'two point seven' -replace '3\.14', 'three point fourteen'
$spoken = (($spoken -split '\r?\n\r?\n' | ForEach-Object { $_ -replace '\s+', ' ' }) -join "`n`n")
$destination = [IO.Path]::GetFullPath($OutputPath)
if (Test-Path -LiteralPath $destination) { throw "Output already exists: $destination" }
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $synth.SelectVoice($Voice)
    $synth.Rate = $Rate
    $synth.SetOutputToWaveFile($destination)
    $synth.Speak($spoken)
} finally {
    $synth.Dispose()
}
Write-Output "Synthetic practice audio created: $destination"
