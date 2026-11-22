# 抖音官网在 WebView2 中的登录验证

2026-11-22 在 Windows 上使用项目锁定的 Tauri 2.12.5 构建独立测试窗口，以 WebView2 InPrivate 会话打开 `https://www.douyin.com/`，确认抖音精选电脑版首页及内容成功加载。原型使用单独的数据目录。

直接构建的 Cargo example 没有 Windows 程序清单，系统在进入 `main` 前报找不到 `TaskDialogIndirect`（`0xC0000139`）。用 Windows SDK `mt.exe` 从项目的正式构建提取包含 `Microsoft.Windows.Common-Controls` 6.0 的清单并嵌入测试 EXE 后，窗口成功打开。正式构建已包含这份依赖清单。

用户在官方页面扫码并确认登录。第二个独立 InPrivate 测试窗口使用 Tauri 原生 Cookie 接口，读取适用于 `https://live.douyin.com/live/promotions/page/` 的 Cookie，经本机子进程的标准输入交给现有 Python 直播商品请求逻辑。验证器从已配置直播间中只读选取样本，并限制为单页查询。

2026-11-22 有 Cookie 的请求返回可解析的商品列表，首页查询 1 条，接口报告共 371 条，同时返回当前讲解商品。对同一直播间使用相同查询逻辑去掉 Cookie 后，响应无法解析为有效商品列表。本次实测覆盖原型的原生读取和现有请求链路。正式设置入口和保存规则见[设置规格](../spec/project/settings.md#云端设置)。
