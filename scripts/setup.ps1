param([string]$Python = "")
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
if (!(Get-Command uv -ErrorAction SilentlyContinue)) {
    throw '未找到 uv。请先安装 uv 并加入 PATH：https://docs.astral.sh/uv/getting-started/installation/'
}
if (!(Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw '未找到 pnpm。请先安装 pnpm 10 并加入 PATH：https://pnpm.io/installation'
}
$taskUvArgs = @('sync', '--group', 'dev')
if ($Python) { $taskUvArgs += @('--python', $Python) }
& uv @taskUvArgs
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败。' }
Push-Location -LiteralPath desktop
try {
    & pnpm install
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败。' }
} finally { Pop-Location }
$go = Get-Command go -ErrorAction SilentlyContinue
if ($go) {
    Push-Location -LiteralPath (Join-Path $taskRoot 'tools/live_product_bridge')
    $previousGoToolchain = $env:GOTOOLCHAIN
    try {
        $env:GOTOOLCHAIN = 'auto'
        & go build -o live-product-bridge.exe .
        if ($LASTEXITCODE -ne 0) { throw '抖音商品事件桥编译失败；请确认 Go 1.26.8+ 可用并能下载 Go modules。' }
    } finally {
        if ($null -eq $previousGoToolchain) { Remove-Item Env:GOTOOLCHAIN -ErrorAction SilentlyContinue }
        else { $env:GOTOOLCHAIN = $previousGoToolchain }
        Pop-Location
    }
    Write-Host '抖音商品事件桥已编译。'
} else {
    Write-Warning '未找到 Go。视频录制仍可用；商品讲解事件采集需安装 Go 1.21+ 并联网重新运行 scripts/setup.ps1。'
}
Write-Host '依赖安装完成。运行 uv run python scripts/dev.py 启动。'
