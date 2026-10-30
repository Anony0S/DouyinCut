#!/usr/bin/env pwsh
# Download FFmpeg essentials build

param(
    [string]$Version = "9.0.2"
)

$ErrorActionPreference = 'Stop'

$downloadUrl = "https://github.com/GyanD/codexffmpeg/releases/download/$Version/ffmpeg-$Version-essentials_build.zip"
$tempDir = Join-Path $env:TEMP "ffmpeg-essentials-download"
$zipFile = Join-Path $tempDir "ffmpeg-essentials.zip"
$extractDir = Join-Path $tempDir "extract"

Write-Host "Downloading FFmpeg essentials..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null

# Download
Write-Host "Downloading from: $downloadUrl" -ForegroundColor Gray
Invoke-WebRequest -Uri $downloadUrl -OutFile $zipFile -UseBasicParsing
Write-Host "Downloaded: $([math]::Round((Get-Item $zipFile).Length / 1MB, 1)) MB" -ForegroundColor Green

# Extract
Write-Host "Extracting..." -ForegroundColor Cyan
if (Test-Path $extractDir) {
    Remove-Item $extractDir -Recurse -Force
}
Expand-Archive -Path $zipFile -DestinationPath $extractDir -Force

# Find the extracted directory
$ffmpegDir = Get-ChildItem -Path $extractDir -Directory | Select-Object -First 1
Write-Host "Extracted to: $($ffmpegDir.FullName)" -ForegroundColor Green

# Check sizes
$ffmpegExe = Join-Path $ffmpegDir.FullName "bin\ffmpeg.exe"
$ffprobeExe = Join-Path $ffmpegDir.FullName "bin\ffprobe.exe"

if (-not (Test-Path $ffmpegExe)) {
    throw "ffmpeg.exe not found in extracted archive"
}
if (-not (Test-Path $ffprobeExe)) {
    throw "ffprobe.exe not found in extracted archive"
}

Write-Host ""
Write-Host "Component sizes:" -ForegroundColor Cyan
Write-Host "  ffmpeg.exe:  $([math]::Round((Get-Item $ffmpegExe).Length / 1MB, 1)) MB" -ForegroundColor Gray
Write-Host "  ffprobe.exe: $([math]::Round((Get-Item $ffprobeExe).Length / 1MB, 1)) MB" -ForegroundColor Gray
Write-Host ""
Write-Host "Total: $([math]::Round(((Get-Item $ffmpegExe).Length + (Get-Item $ffprobeExe).Length) / 1MB, 1)) MB" -ForegroundColor Green
Write-Host ""
Write-Host "Next step:" -ForegroundColor Yellow
Write-Host "  Run: uv run python scripts/prepare-windows-media.py --source `"$($ffmpegDir.FullName)`"" -ForegroundColor Cyan
