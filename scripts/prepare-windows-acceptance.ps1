#!/usr/bin/env pwsh
# Prepare acceptance test environment for Windows release validation
# Generates test fixtures, sample data, and verification tools

param(
    [Parameter(Mandatory=$true)]
    [string]$InstallerPath,

    [string]$OutputDir = ".runtime/windows-packaging/acceptance"
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

try {
    Set-Location $repo

    Write-Section "Acceptance Test Preparation"

    if (-not (Test-Path $InstallerPath)) {
        throw "Installer not found: $InstallerPath"
    }

    $installerInfo = Get-Item $InstallerPath
    $version = $installerInfo.BaseName -replace 'DouyinCut-', '' -replace '-setup', ''
    Write-Host "Installer: $($installerInfo.FullName)" -ForegroundColor Gray
    Write-Host "Version: $version" -ForegroundColor Gray

    $outputRoot = Join-Path $repo $OutputDir
    New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null

    Write-Section "Generating Test Video"

    # Generate a short test video with speech
    $testVideo = Join-Path $outputRoot "test-video.mp4"

    if (-not (Test-Path $testVideo)) {
        Write-Host "Creating test video (this may take a moment)..." -ForegroundColor Gray

        # Use FFmpeg to generate a simple test video
        $ffmpeg = ".runtime/windows-packaging/media/ffmpeg.exe"
        if (-not (Test-Path $ffmpeg)) {
            throw "FFmpeg not found. Run build script first."
        }

        # Generate 30 second color test pattern with beep
        & $ffmpeg -f lavfi -i "color=c=blue:s=1280x720:d=30" `
                  -f lavfi -i "sine=frequency=440:duration=30" `
                  -c:v libx264 -preset fast -crf 23 `
                  -c:a aac -b:a 128k `
                  -y $testVideo 2>&1 | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to generate test video"
        }

        Write-Host "✓ Test video created: $testVideo" -ForegroundColor Green
    } else {
        Write-Host "✓ Test video exists: $testVideo" -ForegroundColor Green
    }

    Write-Section "Copying Acceptance Checklist"

    $checklist = Join-Path $repo ".scratch/windows-packaging/acceptance-checklist.md"
    $checklistCopy = Join-Path $outputRoot "acceptance-checklist.md"
    Copy-Item $checklist $checklistCopy -Force
    Write-Host "✓ Checklist: $checklistCopy" -ForegroundColor Green

    Write-Section "Creating Test Instructions"

    $instructions = @"
# DouyinCut Windows Release Acceptance Test

**Version**: $version
**Date**: $(Get-Date -Format 'yyyy-MM-dd')

## Quick Start

1. **Install on clean Windows 10/11**:
   - Copy installer to test machine: ``$($installerInfo.Name)``
   - Double-click installer, follow wizard
   - Default location: ``C:\Users\<user>\AppData\Local\Programs\DouyinCut``

2. **First launch**:
   - Select work directory (try C:/, D:/, Chinese path, space path)
   - Cancel to test cancellation behavior

3. **Import test video**:
   - Use provided ``test-video.mp4``
   - Or use your own video file

4. **Configure cloud settings**:
   - Enter API keys (text model + ASR)
   - Test immediate model response
   - Try exiting during test (should interrupt immediately)

5. **Run video analysis**:
   - Submit analysis job
   - Watch ASR progress
   - Try exiting during ASR (should exit immediately)
   - Restart and verify ASR resumes or replays
   - After ASR, try exiting during planner (should exit immediately)
   - Restart and verify planner resumes with ASR cache

6. **Export outputs**:
   - Preview: verify playback
   - MP4 export: verify file plays
   - SRT export: verify subtitle format
   - JianYing draft: verify directory structure
   - If JianYing installed: open draft in JianYing

7. **Live monitoring** (if authorized):
   - Add monitor with room URL
   - Verify status updates
   - Test recording start/stop
   - Test product listening

8. **Lifecycle tests**:
   - Second instance: should show "already running"
   - Close window: minimize to tray
   - Tray menu: verify options
   - Exit: verify clean shutdown
   - Task manager: no residual processes

9. **Update test**:
   - While app running: installer should refuse
   - After exit: installer should succeed
   - Restart: verify data preserved

10. **Uninstall test**:
    - Uninstall via Settings > Apps
    - Choose "keep data"
    - Verify program files removed
    - Verify work directory preserved
    - Reinstall and verify data recovery

## Detailed Checklist

See ``acceptance-checklist.md`` for complete validation steps.

## Evidence Collection

Record:
- System version (Win 10/11, build number)
- Screenshots of key screens
- Installer log: ``%LOCALAPPDATA%\Temp\Setup Log YYYY-MM-DD #NNN.txt``
- Application log: work directory ``/logs/``
- Test video used
- API provider and response times
- Any errors or warnings

## Known Limitations

- **No code signing**: SmartScreen may warn
- **Current user only**: not system-wide install
- **No auto-update**: manual download required
- **Windows 10/11 x64 only**

## Support

If issues occur:
- Check installer log
- Check application log in work directory
- Verify system requirements
- See ``docs/windows-installer.md`` for troubleshooting
"@

    $instructionsPath = Join-Path $outputRoot "TEST-INSTRUCTIONS.md"
    $instructions | Out-File $instructionsPath -Encoding UTF8
    Write-Host "✓ Instructions: $instructionsPath" -ForegroundColor Green

    Write-Section "Creating Verification Script"

    $verifyScript = @'
#!/usr/bin/env pwsh
# Quick verification script for acceptance test environment

Write-Host "DouyinCut Installation Verification" -ForegroundColor Cyan
Write-Host ""

$exePath = "$env:LOCALAPPDATA\Programs\DouyinCut\douyincut.exe"

if (Test-Path $exePath) {
    Write-Host "✓ Application installed: $exePath" -ForegroundColor Green
    $version = (Get-Item $exePath).VersionInfo.FileVersion
    Write-Host "  Version: $version" -ForegroundColor Gray
} else {
    Write-Host "✗ Application not found" -ForegroundColor Red
}

$configPath = "$env:LOCALAPPDATA\DouyinCut\config\workspace.json"
if (Test-Path $configPath) {
    Write-Host "✓ Configuration exists: $configPath" -ForegroundColor Green
    $config = Get-Content $configPath -Raw | ConvertFrom-Json
    Write-Host "  Work directory: $($config.path)" -ForegroundColor Gray
} else {
    Write-Host "⚠ Configuration not found (first run not completed)" -ForegroundColor Yellow
}

$processes = Get-Process -Name "douyincut", "DouyinCutRuntime" -ErrorAction SilentlyContinue
if ($processes) {
    Write-Host "✓ Application is running:" -ForegroundColor Green
    $processes | ForEach-Object { Write-Host "  $($_.Name) (PID: $($_.Id))" -ForegroundColor Gray }
} else {
    Write-Host "○ Application not running" -ForegroundColor Gray
}

Write-Host ""
Write-Host "Credential check:" -ForegroundColor Cyan
@('douyincut-llm-api-key', 'douyincut-asr-api-key') | ForEach-Object {
    $cred = cmdkey /list:$_ 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  ✓ $_" -ForegroundColor Green
    } else {
        Write-Host "  ○ $_ (not set)" -ForegroundColor Gray
    }
}
'@

    $verifyScriptPath = Join-Path $outputRoot "verify-installation.ps1"
    $verifyScript | Out-File $verifyScriptPath -Encoding UTF8
    Write-Host "✓ Verification script: $verifyScriptPath" -ForegroundColor Green

    Write-Section "Copying Installer"

    $installerCopy = Join-Path $outputRoot $installerInfo.Name
    Copy-Item $InstallerPath $installerCopy -Force
    Write-Host "✓ Installer copy: $installerCopy" -ForegroundColor Green

    Write-Section "Generating Package Manifest"

    $manifest = @{
        version = $version
        preparation_date = Get-Date -Format 'o'
        installer = @{
            filename = $installerInfo.Name
            size_bytes = $installerInfo.Length
            path = $installerCopy
        }
        test_data = @{
            video = $testVideo
            checklist = $checklistCopy
            instructions = $instructionsPath
            verify_script = $verifyScriptPath
        }
        requirements = @{
            os = "Windows 10 or 11 (x64)"
            user = "Standard user (non-admin)"
            disk_space = "2 GB minimum"
            optional = @{
                cloud_api = "Text model + ASR API keys"
                douyin_account = "For live monitoring test"
                jianying = "For draft import test"
            }
        }
        test_scenarios = @(
            "Fresh install on clean system",
            "Work directory selection (C:/, D:/, Chinese, space)",
            "Cloud settings and immediate test",
            "Video analysis with exit interruption",
            "Export (MP4, SRT, JianYing)",
            "Update while running (reject) and after exit (succeed)",
            "Uninstall with data preservation"
        )
    }

    $manifestPath = Join-Path $outputRoot "acceptance-manifest.json"
    $manifest | ConvertTo-Json -Depth 10 | Out-File $manifestPath -Encoding UTF8
    Write-Host "✓ Manifest: $manifestPath" -ForegroundColor Green

    Write-Section "Preparation Complete"

    Write-Host "Acceptance test package ready:" -ForegroundColor Green
    Write-Host "  Location: $outputRoot" -ForegroundColor Cyan
    Write-Host "  Installer: $($installerInfo.Name)" -ForegroundColor Cyan
    Write-Host "  Test video: test-video.mp4" -ForegroundColor Cyan
    Write-Host "  Instructions: TEST-INSTRUCTIONS.md" -ForegroundColor Cyan
    Write-Host "  Checklist: acceptance-checklist.md" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Copy contents of $outputRoot to test machine" -ForegroundColor Gray
    Write-Host "  2. Follow TEST-INSTRUCTIONS.md" -ForegroundColor Gray
    Write-Host "  3. Fill out acceptance-checklist.md" -ForegroundColor Gray
    Write-Host "  4. Run verify-installation.ps1 for quick checks" -ForegroundColor Gray
}
catch {
    Write-Host ""
    Write-Host "Preparation failed: $_" -ForegroundColor Red
    exit 1
}
