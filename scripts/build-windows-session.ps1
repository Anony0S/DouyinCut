param([switch]$RuntimeOnly, [Parameter(Mandatory=$true)][string]$MediaSource)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $repo '.runtime/windows-packaging/logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$logPath = Join-Path $logRoot ('build-session-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE. See $logPath"
    }
}

Start-Transcript -Path $logPath | Out-Null
$previousLocation = Get-Location
$previousToken = $env:VITE_SESSION_TOKEN
$previousPath = $env:PATH
try {
    Set-Location $repo
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or [IntPtr]::Size -ne 8 -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
        throw 'Windows x64 is required for this build.'
    }
    foreach ($tool in @('uv', 'pnpm', 'node')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing build prerequisite: $tool" }
    }
    if (-not $RuntimeOnly) {
        $cargoBin = Join-Path $env:USERPROFILE '.cargo/bin'
        if (Test-Path (Join-Path $cargoBin 'cargo.exe')) { $env:PATH = "$cargoBin;$env:PATH" }
        if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Missing Rust MSVC toolchain (cargo). Install it before building Tauri.' }
        Invoke-Checked 'cargo' @('--version')
        $vswhere = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Microsoft Visual Studio/Installer/vswhere.exe'
        if (-not (Get-Command cl -ErrorAction SilentlyContinue)) {
            if (-not (Test-Path $vswhere)) { throw 'Missing Visual Studio Build Tools with the C++ workload and Windows SDK.' }
            $vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
            if (-not $vs) { throw 'Missing MSVC x64 C++ tools.' }
        }
    }
    # Vite's production config also forces the build-time token to empty.
    $env:VITE_SESSION_TOKEN = ''
    Invoke-Checked 'uv' @('python', 'install', '3.12.14')
    $buildPython = & uv python find --managed-python 3.12.14
    if ($LASTEXITCODE -ne 0) { throw 'Cannot locate the managed build interpreter.' }
    Invoke-Checked 'uv' @('sync', '--locked', '--python', $buildPython.Trim())
    Invoke-Checked 'uv' @('run', '--no-sync', 'python', 'scripts/prepare-windows-media.py', '--source', $MediaSource)
    Invoke-Checked 'pnpm' @('--dir', 'desktop', 'install', '--frozen-lockfile')
    Invoke-Checked 'pnpm' @('--dir', 'desktop', 'run', 'build')
    Invoke-Checked 'uv' @('run', '--no-sync', 'python', '-m', 'PyInstaller', '--clean', '--noconfirm',
        '--workpath', '.runtime/windows-packaging/pyinstaller',
        '--distpath', 'desktop/src-tauri/runtime', 'packaging/windows/runtime.spec')
    $runtime = Join-Path $repo 'desktop/src-tauri/runtime/DouyinCutRuntime/DouyinCutRuntime.exe'
    if (-not (Test-Path $runtime)) { throw 'Frozen runtime output is missing.' }
    Write-Output "Frozen runtime: $runtime"
    Invoke-Checked 'uv' @('run', '--no-sync', 'python', 'scripts/verify-windows-session.py', '--runtime', $runtime)
    Invoke-Checked 'uv' @('run', '--no-sync', 'python', 'scripts/verify-windows-media.py', '--runtime', $runtime)
    if (-not $RuntimeOnly) {
        Invoke-Checked 'pnpm' @('--dir', 'desktop', 'run', 'tauri', 'build', '--no-bundle', '--config', 'src-tauri/tauri.session.conf.json')
        Write-Output ("Native test program: " + (Join-Path $repo 'desktop/src-tauri/target/release/douyincut.exe'))
    }
    Write-Output 'This is a minimal session test program, not an installer or a full business acceptance build.'
    Write-Output "Build log: $logPath"
}
finally {
    $env:VITE_SESSION_TOKEN = $previousToken
    $env:PATH = $previousPath
    Set-Location $previousLocation
    Stop-Transcript | Out-Null
}
