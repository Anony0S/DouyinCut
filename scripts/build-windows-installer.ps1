#!/usr/bin/env pwsh
# Unified build entry point for DouyinCut Windows installer
# Checks dependencies, builds components in order, and generates offline installer

param(
    [string]$MediaSource = "bundled",
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $repo '.runtime/windows-packaging/logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logPath = Join-Path $logRoot "build-installer-$timestamp.log"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments, [string]$Description)
    Write-Host "Running: $Program $($Arguments -join ' ')" -ForegroundColor Gray
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE. See $logPath"
    }
}

Start-Transcript -Path $logPath | Out-Null
$previousLocation = Get-Location

try {
    Set-Location $repo

    Write-Section "Environment Check"

    # Check Windows x64
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'Windows is required for this build.'
    }
    if ([IntPtr]::Size -ne 8 -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'x64 architecture is required.'
    }
    Write-Host "✓ Windows x64" -ForegroundColor Green

    # Check Rust
    $rustVersion = & rustc --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw 'Rust is not installed. Install from https://rustup.rs/'
    }
    Write-Host "✓ Rust: $rustVersion" -ForegroundColor Green

    # Check Python via uv
    $uvVersion = & uv --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw 'uv is not installed. Install from https://docs.astral.sh/uv/'
    }
    Write-Host "✓ uv: $uvVersion" -ForegroundColor Green

    # Check Node/pnpm
    $nodeVersion = & node --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw 'Node.js is not installed.'
    }
    $pnpmVersion = & pnpm --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw 'pnpm is not installed. Run: npm install -g pnpm'
    }
    Write-Host "✓ Node: $nodeVersion, pnpm: $pnpmVersion" -ForegroundColor Green

    # Check PyInstaller
    $pyiVersion = & uv run --no-sync python -m PyInstaller --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller is not available in the uv environment.'
    }
    Write-Host "✓ PyInstaller: $pyiVersion" -ForegroundColor Green

    # Check Inno Setup
    $isccPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $isccPath)) {
        throw "Inno Setup 6 is not installed at: $isccPath`nDownload from: https://jrsoftware.org/isdl.php"
    }
    $isccVersion = & $isccPath /? 2>&1 | Select-String "Inno Setup" | Select-Object -First 1
    Write-Host "✓ Inno Setup: $isccVersion" -ForegroundColor Green

    # Read version from SPEC.md
    $specContent = Get-Content (Join-Path $repo 'SPEC.md') -Raw
    if ($specContent -match '- 版本：(\d+\.\d+\.\d+)') {
        $version = $Matches[1]
    } else {
        throw 'Could not extract version from SPEC.md'
    }
    Write-Host "✓ Version: $version" -ForegroundColor Green

    Write-Section "Building Frontend"
    Invoke-Checked 'pnpm' @('--dir', 'desktop', 'install', '--frozen-lockfile') 'Frontend dependency installation'
    Invoke-Checked 'pnpm' @('--dir', 'desktop', 'run', 'build') 'Frontend build'

    $frontendIndex = Join-Path $repo 'desktop/dist/index.html'
    if (-not (Test-Path $frontendIndex)) {
        throw 'Frontend build output is missing: desktop/dist/index.html'
    }
    Write-Host "✓ Frontend built: desktop/dist/" -ForegroundColor Green

    Write-Section "Preparing Bundled Media"
    Invoke-Checked 'uv' @('run', '--no-sync', 'python', 'scripts/prepare-windows-media.py',
        '--output-manifest', 'packaging/windows/media-manifest.json',
        '--output-sources', 'packaging/windows/media-sources.json') 'Media preparation'

    $mediaDir = Join-Path $repo '.runtime/windows-packaging/media'
    $ffmpeg = Join-Path $mediaDir 'ffmpeg.exe'
    $ffprobe = Join-Path $mediaDir 'ffprobe.exe'
    if (-not ((Test-Path $ffmpeg) -and (Test-Path $ffprobe))) {
        throw 'Bundled media tools are missing'
    }
    Write-Host "✓ Media tools prepared: $mediaDir" -ForegroundColor Green

    Write-Section "Freezing Python Runtime"
    $pyiWork = Join-Path $repo '.runtime/windows-packaging/pyinstaller'
    $pyiDist = Join-Path $repo 'desktop/src-tauri/runtime'
    Invoke-Checked 'uv' @('run', '--no-sync', 'python', '-m', 'PyInstaller',
        '--clean', '--noconfirm',
        '--workpath', $pyiWork,
        '--distpath', $pyiDist,
        'packaging/windows/runtime.spec') 'PyInstaller freeze'

    $runtime = Join-Path $pyiDist 'DouyinCutRuntime/DouyinCutRuntime.exe'
    if (-not (Test-Path $runtime)) {
        throw 'Frozen runtime output is missing'
    }
    Write-Host "✓ Frozen runtime: $runtime" -ForegroundColor Green

    # Copy media to runtime directory for installer
    $runtimeMediaDest = Join-Path $pyiDist 'media'
    if (Test-Path $runtimeMediaDest) {
        Remove-Item $runtimeMediaDest -Recurse -Force
    }
    Copy-Item $mediaDir $runtimeMediaDest -Recurse
    Write-Host "✓ Media copied to runtime directory" -ForegroundColor Green

    if (-not $SkipTests) {
        Write-Section "Running Verification Tests"
        Invoke-Checked 'uv' @('run', '--no-sync', 'python', 'scripts/verify-windows-session.py',
            '--runtime', $runtime) 'Session verification'
        Invoke-Checked 'uv' @('run', '--no-sync', 'python', 'scripts/verify-windows-media.py',
            '--runtime', $runtime) 'Media verification'
        Write-Host "✓ All verification tests passed" -ForegroundColor Green
    }

    Write-Section "Building Native Wrapper"
    $env:VITE_SESSION_TOKEN = 'build-time-placeholder'
    try {
        Invoke-Checked 'pnpm' @('--dir', 'desktop', 'run', 'tauri', 'build',
            '--no-bundle', '--config', 'src-tauri/tauri.session.conf.json') 'Tauri build'
    } finally {
        Remove-Item Env:\VITE_SESSION_TOKEN -ErrorAction SilentlyContinue
    }

    $tauriExe = Join-Path $repo 'desktop/src-tauri/target/release/douyincut.exe'
    if (-not (Test-Path $tauriExe)) {
        throw 'Tauri executable is missing'
    }
    Write-Host "✓ Native wrapper: $tauriExe" -ForegroundColor Green

    Write-Section "Generating Installer"
    $installerScript = Join-Path $repo 'packaging/windows/installer.iss'
    $installerOutput = Join-Path $repo 'desktop/src-tauri/target/release/installer'
    New-Item -ItemType Directory -Force -Path $installerOutput | Out-Null

    Invoke-Checked $isccPath @("/O$installerOutput", "/DAppVersion=$version", $installerScript) 'Inno Setup compilation'

    $installerExe = Join-Path $installerOutput "DouyinCut-$version-setup.exe"
    if (-not (Test-Path $installerExe)) {
        throw 'Installer output is missing'
    }

    $installerSize = (Get-Item $installerExe).Length
    $installerSizeMB = [math]::Round($installerSize / 1MB, 2)

    Write-Section "Build Summary"
    Write-Host "Version: $version" -ForegroundColor Green
    Write-Host "Installer: $installerExe" -ForegroundColor Green
    Write-Host "Size: $installerSizeMB MB" -ForegroundColor Green
    Write-Host "Log: $logPath" -ForegroundColor Green
    Write-Host ""
    Write-Host "✓ Build completed successfully" -ForegroundColor Green

    # Generate build manifest
    $manifest = @{
        version = $version
        timestamp = $timestamp
        installer_path = $installerExe
        installer_size_bytes = $installerSize
        components = @{
            frontend = "desktop/dist"
            runtime = $runtime
            wrapper = $tauriExe
            media = $mediaDir
        }
        verification = @{
            session_test = -not $SkipTests
            media_test = -not $SkipTests
        }
    }

    $manifestPath = Join-Path $installerOutput "build-manifest-$version.json"
    $manifest | ConvertTo-Json -Depth 10 | Out-File $manifestPath -Encoding UTF8
    Write-Host "Build manifest: $manifestPath" -ForegroundColor Cyan
}
catch {
    Write-Host ""
    Write-Host "Build failed: $_" -ForegroundColor Red
    Write-Host "See log: $logPath" -ForegroundColor Red
    exit 1
}
finally {
    Set-Location $previousLocation
    Stop-Transcript | Out-Null
}
