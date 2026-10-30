#!/usr/bin/env pwsh
# Test installer, update, and uninstall workflows
# Uses isolated test directories to avoid affecting real user data

param(
    [Parameter(Mandatory=$true)]
    [string]$InstallerPath,

    [switch]$SkipUpdateTest
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

function Test-ProcessRunning {
    param([string]$ProcessName)
    return (Get-Process -Name $ProcessName -ErrorAction SilentlyContinue) -ne $null
}

function Wait-ProcessExit {
    param([string]$ProcessName, [int]$TimeoutSeconds = 30)
    $elapsed = 0
    while ((Test-ProcessRunning $ProcessName) -and ($elapsed -lt $TimeoutSeconds)) {
        Start-Sleep -Milliseconds 500
        $elapsed += 0.5
    }
    if (Test-ProcessRunning $ProcessName) {
        throw "$ProcessName did not exit within $TimeoutSeconds seconds"
    }
}

try {
    Write-Section "Installer Test Setup"

    if (-not (Test-Path $InstallerPath)) {
        throw "Installer not found: $InstallerPath"
    }

    $installerInfo = Get-Item $InstallerPath
    Write-Host "Installer: $($installerInfo.FullName)" -ForegroundColor Gray
    Write-Host "Size: $([math]::Round($installerInfo.Length / 1MB, 2)) MB" -ForegroundColor Gray

    # Create isolated test environment
    $testRoot = Join-Path $repo ".runtime/windows-packaging/installer-test-$(Get-Date -Format 'yyyyMMddHHmmss')"
    $testInstallDir = Join-Path $testRoot "install"
    $testWorkDir = Join-Path $testRoot "work"

    New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $testWorkDir | Out-Null

    Write-Host "Test root: $testRoot" -ForegroundColor Gray

    Write-Section "Test 1: Silent Install"

    # Run installer in silent mode with custom install directory
    $installArgs = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/DIR=$testInstallDir",
        "/LOG=$testRoot\install.log"
    )

    Write-Host "Installing to: $testInstallDir" -ForegroundColor Gray
    $installProcess = Start-Process -FilePath $InstallerPath -ArgumentList $installArgs -Wait -PassThru

    if ($installProcess.ExitCode -ne 0) {
        throw "Installation failed with exit code $($installProcess.ExitCode)"
    }

    # Verify installation
    $appExe = Join-Path $testInstallDir "douyincut.exe"
    $runtimeExe = Join-Path $testInstallDir "runtime\DouyinCutRuntime.exe"

    if (-not (Test-Path $appExe)) {
        throw "Application executable not found: $appExe"
    }
    if (-not (Test-Path $runtimeExe)) {
        throw "Runtime executable not found: $runtimeExe"
    }

    Write-Host "✓ Installation completed" -ForegroundColor Green
    Write-Host "  App: $appExe" -ForegroundColor Gray
    Write-Host "  Runtime: $runtimeExe" -ForegroundColor Gray

    Write-Section "Test 2: Run Application"

    # Start the application
    Write-Host "Starting application..." -ForegroundColor Gray
    $appProcess = Start-Process -FilePath $appExe -PassThru

    Start-Sleep -Seconds 5

    if ($appProcess.HasExited) {
        throw "Application exited unexpectedly with code $($appProcess.ExitCode)"
    }

    Write-Host "✓ Application started (PID: $($appProcess.Id))" -ForegroundColor Green

    # Stop the application gracefully
    Write-Host "Stopping application..." -ForegroundColor Gray
    $appProcess.CloseMainWindow() | Out-Null
    Wait-ProcessExit -ProcessName "douyincut" -TimeoutSeconds 15

    Write-Host "✓ Application stopped gracefully" -ForegroundColor Green

    if (-not $SkipUpdateTest) {
        Write-Section "Test 3: Update While Running (Should Fail)"

        # Start app again
        $appProcess = Start-Process -FilePath $appExe -PassThru
        Start-Sleep -Seconds 3

        Write-Host "Attempting update while app is running..." -ForegroundColor Gray
        $updateProcess = Start-Process -FilePath $InstallerPath -ArgumentList $installArgs -Wait -PassThru

        if ($updateProcess.ExitCode -eq 0) {
            throw "Update should have failed while app is running"
        }

        Write-Host "✓ Update correctly rejected while app is running" -ForegroundColor Green

        # Stop app
        $appProcess.CloseMainWindow() | Out-Null
        Wait-ProcessExit -ProcessName "douyincut" -TimeoutSeconds 15

        Write-Section "Test 4: Update After Exit"

        Write-Host "Installing update..." -ForegroundColor Gray
        $updateProcess = Start-Process -FilePath $InstallerPath -ArgumentList $installArgs -Wait -PassThru

        if ($updateProcess.ExitCode -ne 0) {
            throw "Update failed with exit code $($updateProcess.ExitCode)"
        }

        # Verify updated installation still works
        if (-not (Test-Path $appExe)) {
            throw "Application executable missing after update"
        }

        Write-Host "✓ Update completed successfully" -ForegroundColor Green
    }

    Write-Section "Test 5: Uninstall"

    $uninstallExe = Join-Path $testInstallDir "uninst\unins000.exe"
    if (-not (Test-Path $uninstallExe)) {
        throw "Uninstaller not found: $uninstallExe"
    }

    Write-Host "Running uninstaller..." -ForegroundColor Gray
    $uninstallArgs = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/LOG=$testRoot\uninstall.log"
    )

    $uninstallProcess = Start-Process -FilePath $uninstallExe -ArgumentList $uninstallArgs -Wait -PassThru

    if ($uninstallProcess.ExitCode -ne 0) {
        throw "Uninstall failed with exit code $($uninstallProcess.ExitCode)"
    }

    # Verify program files are removed
    if (Test-Path $appExe) {
        throw "Application executable still exists after uninstall"
    }

    Write-Host "✓ Uninstall completed" -ForegroundColor Green

    Write-Section "Test Summary"

    Write-Host "✓ Silent install" -ForegroundColor Green
    Write-Host "✓ Application start and stop" -ForegroundColor Green
    if (-not $SkipUpdateTest) {
        Write-Host "✓ Update rejected while running" -ForegroundColor Green
        Write-Host "✓ Update succeeded after exit" -ForegroundColor Green
    }
    Write-Host "✓ Uninstall completed" -ForegroundColor Green
    Write-Host ""
    Write-Host "Test artifacts: $testRoot" -ForegroundColor Cyan
    Write-Host "All installer tests passed!" -ForegroundColor Green

    # Generate test report
    $report = @{
        timestamp = Get-Date -Format 'o'
        installer = $InstallerPath
        tests_passed = @(
            "silent_install",
            "application_lifecycle",
            $(if (-not $SkipUpdateTest) { "update_while_running_rejected"; "update_after_exit" }),
            "uninstall"
        ) | Where-Object { $_ }
        test_root = $testRoot
    }

    $reportPath = Join-Path $testRoot "test-report.json"
    $report | ConvertTo-Json -Depth 10 | Out-File $reportPath -Encoding UTF8
    Write-Host "Test report: $reportPath" -ForegroundColor Cyan
}
catch {
    Write-Host ""
    Write-Host "Test failed: $_" -ForegroundColor Red

    # Cleanup any running processes
    Get-Process -Name "douyincut" -ErrorAction SilentlyContinue | Stop-Process -Force

    exit 1
}
