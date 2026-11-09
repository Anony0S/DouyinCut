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
Write-Host '依赖安装完成。运行 uv run python scripts/dev.py 启动。'
