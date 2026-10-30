# DouyinCut Windows 安装包构建与维护指南

## 构建环境要求

### 必需工具

1. **Windows 10/11 x64**
2. **Rust** - https://rustup.rs/
3. **Python 3.11+** 通过 uv 管理 - https://docs.astral.sh/uv/
4. **Node.js 18+** 和 **pnpm** - `npm install -g pnpm`
5. **Inno Setup 6** - https://jrsoftware.org/isdl.php

### 验证环境

```powershell
# 检查所有必需工具
rustc --version
uv --version
node --version
pnpm --version
uv run python -m PyInstaller --version
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /?
```

## 构建安装包

### 完整构建（推荐）

```powershell
# 在仓库根目录执行
.\scripts\build-windows-installer.ps1
```

构建过程包括：
1. 环境检查（Rust、Python、Node、Inno Setup）
2. 构建前端（Vue + Vite）
3. 准备媒体工具（FFmpeg + ffprobe）
4. 冻结 Python 运行时（PyInstaller）
5. 构建原生包装器（Tauri）
6. 运行验证测试
7. 生成离线安装器（Inno Setup）

### 跳过测试（快速构建）

```powershell
.\scripts\build-windows-installer.ps1 -SkipTests
```

### 输出产物

```
desktop/src-tauri/target/release/installer/
├── DouyinCut-0.3.0-setup.exe    # 离线安装器
└── build-manifest-0.3.0.json     # 构建清单
```

构建日志保存在：`.runtime/windows-packaging/logs/build-installer-YYYYMMDD-HHMMSS.log`

## 安装器功能

### 特性

- **当前用户安装**：无需管理员权限
- **离线安装**：不依赖网络下载
- **静默安装**：支持 `/VERYSILENT` 参数
- **保留数据**：卸载时保留工作目录、设置和凭据
- **运行检查**：安装/更新前检查应用是否正在运行

### 用户安装

双击 `DouyinCut-x.x.x-setup.exe`，按向导操作。

默认安装位置：`C:\Users\<用户名>\AppData\Local\Programs\DouyinCut`

### 静默安装（自动化部署）

```powershell
.\DouyinCut-0.3.0-setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

### 自定义安装目录

```powershell
.\DouyinCut-0.3.0-setup.exe /DIR="D:\MyApps\DouyinCut"
```

## 更新流程

### 用户手动更新

1. **完全退出应用**（关闭窗口 + 退出托盘图标）
2. 运行新版本的安装器
3. 安装器会覆盖程序文件，保留所有数据

### 运行中更新保护

如果应用正在运行，安装器会：
1. 检测到单实例互斥锁
2. 提示用户先退出应用
3. 拒绝继续安装

这确保程序文件不会在使用中被替换。

### 数据保留策略

更新时**始终保留**：
- 工作目录（项目、录制、素材）
- 用户设置（配置文件）
- 系统凭据（API keys）

## 卸载流程

### 用户卸载

1. 通过"设置 > 应用"卸载
2. 或运行 `uninst\unins000.exe`

### 数据保留

卸载时会提示：
- **保留数据**（推荐）：仅删除程序文件
- **手动清理**：引导用户找到工作目录手动删除

工作目录位置保存在：`%LOCALAPPDATA%\DouyinCut\config\workspace.json`

### 完全清理（可选）

如需彻底删除所有数据：

```powershell
# 1. 卸载程序
# 2. 手动删除工作目录（从 workspace.json 中查找位置）
# 3. 删除配置
Remove-Item "$env:LOCALAPPDATA\DouyinCut" -Recurse -Force
# 4. 清理凭据（Windows 凭据管理器）
cmdkey /delete:douyincut-llm-api-key
cmdkey /delete:douyincut-asr-api-key
```

## 测试安装器

### 自动化测试

```powershell
.\scripts\test-windows-installer.ps1 -InstallerPath "desktop\src-tauri\target\release\installer\DouyinCut-0.3.0-setup.exe"
```

测试覆盖：
- ✅ 静默安装
- ✅ 应用启动和退出
- ✅ 运行中更新拒绝
- ✅ 退出后更新成功
- ✅ 卸载完成

测试使用隔离目录，不影响真实用户数据。

### 手动验收测试

1. **首次安装**：在干净的测试账户中安装
2. **创建数据**：导入视频、创建项目
3. **更新测试**：
   - 运行中尝试更新（应拒绝）
   - 退出后更新（应成功）
   - 验证数据和设置保留
4. **卸载测试**：
   - 卸载应用
   - 验证数据保留
   - 重装后验证数据可恢复

## 限制与已知问题

### 当前限制

- **仅支持当前用户安装**（非系统级）
- **无自动更新**：用户需手动下载新版本
- **未签名**：Windows 可能显示 SmartScreen 警告
- **仅 Windows 10/11 x64**：不支持 32 位或旧版本

### 代码签名（可选）

生产发布建议购买代码签名证书：

```powershell
# 签名命令示例（需要证书）
signtool sign /f certificate.pfx /p password /t http://timestamp.digicert.com DouyinCut-0.3.0-setup.exe
```

未签名的内部测试包不应宣传为"已签名"或"生产就绪"。

## 构建清单说明

每次构建生成 `build-manifest-VERSION.json`：

```json
{
  "version": "0.3.0",
  "timestamp": "20261030-143022",
  "installer_path": "完整路径",
  "installer_size_bytes": 文件大小,
  "components": {
    "frontend": "前端构建目录",
    "runtime": "Python 运行时路径",
    "wrapper": "原生包装器路径",
    "media": "媒体工具目录"
  },
  "verification": {
    "session_test": true,
    "media_test": true
  }
}
```

用于追溯构建产物和验证状态。

## 版本管理

版本号定义在 `SPEC.md` 的"版本"字段：

```markdown
- 版本：0.3.0
```

构建脚本自动读取此版本号并应用到安装器文件名和元数据。

## 故障排查

### 构建失败

**症状**：构建脚本报错退出

**排查**：
1. 检查日志：`.runtime/windows-packaging/logs/build-installer-*.log`
2. 验证环境：确保所有必需工具已安装
3. 清理缓存：删除 `.runtime/windows-packaging/pyinstaller`

### 安装失败

**症状**：安装器报错或静默失败

**排查**：
1. 检查安装日志：安装目录或 `%TEMP%` 中的日志
2. 确认磁盘空间充足（需要约 500MB）
3. 检查杀毒软件是否拦截

### 更新被拒绝

**症状**："应用正在运行"提示

**解决**：
1. 关闭主窗口
2. 右键托盘图标选择"退出"
3. 确认进程已退出：`tasklist | findstr douyincut`

## 参考资料

- **Inno Setup 文档**：https://jrsoftware.org/ishelp/
- **PyInstaller 文档**：https://pyinstaller.org/
- **Tauri 文档**：https://tauri.app/
- **Windows 打包规格**：`../.scratch/windows-packaging/spec.md`
