# 商品封面生成与剪映草稿封面研究

## 已确认结论

- 用户确认“封面”是剪映草稿列表封面，不是视频时间线中的图片素材。
- 商品图片接口来自导出的星斗 HAR：商品详情响应的 `data.images` 是图片列表。实现只下载 `p3-aio.ecombdimg.com` 和 `p9-aio.ecombdimg.com` 的 HTTPS 图片，保存为当前项目工作目录中的参考图。
- 生图服务按 OpenAI Images API 兼容协议接入，使用独立 Base URL、模型 ID 和 API Key，默认请求 `/images/edits`，通过 multipart 的 `image` 字段上传参考图，接受 `b64_json` 或同域 HTTPS 图片 URL。
- 封面任务绑定 `asset_id`、`plan_id` 和 `revision`。有未保存片段调整时禁止提交，生成草稿时校验封面与当前方案修订一致。
- 带封面草稿采用生成新版目录的方式，保留既有草稿。封面复制到草稿 `Resources/`，并写入 `draft_info.json.static_cover_image_path` 与 `draft_meta_info.json.draft_cover`。

## 放弃的方案及原因

- 放弃直接修改用户已有剪映草稿：需要识别并安全更新已有目录中的工程元数据，容易覆盖用户在剪映中的后续编辑，也无法稳定处理已有草稿正在被剪映读取的情况。采用生成新版草稿目录并保留旧草稿，边界更清楚且符合用户确认。
- 放弃把星斗带签名图片 URL 直接交给生图服务或写入项目数据：链接可能过期，并会把平台临时凭据带入任务 / 草稿；改为先下载到项目工作目录，再上传本地参考图。
- 暂不接入独立服务的专有请求格式：用户尚未提供真实协议样例，当前只实现 OpenAI Images API 兼容协议，真实服务字段差异留待接入时验证。

## 未验证范围

- 尚未用真实星斗账号验证商品详情 `data.images` 的所有图片类型和下载可用性。
- 尚未用真实生图服务验证模型 ID、尺寸支持、返回 `b64_json` / 同域 URL 的具体分支和实际生成效果。
- 尚未在目标剪映版本打开生成目录，确认 `static_cover_image_path` 与 `draft_cover` 均被草稿列表读取；当前证据只有本地文件写入测试。
- 2026-11-17：已确认交互采用“生成带封面剪映草稿”按钮打开弹窗；弹窗自动获取星斗商品全部图片，用户选择参考图，文本模型生成简短商品名称并附入生成要求，用户点击“生成封面”后继续使用成功封面创建新版草稿。保留旧的“生成剪映草稿”入口。
- 2026-11-17：已采用独立的 `POST /settings/cloud/short-product-name` 文本模型调用，而不是让生图模型同时负责命名；这样封面提示词中的商品名称可以在生图任务提交前确定。失败时前端回退到原商品名截短值。

## 证据

- 用户提供的导出：`C:/Users/Anony/Documents/Study/AI/REA/星斗/api.aixingdou.com_2026_11_17_15_39_32.har`。
- 实现：`backend/app/ai/image_generation.py`、`backend/app/product_platform/service.py`、`backend/app/integrations/jianying/service.py`、`backend/app/api.py`、`backend/app/worker.py`。
- 自动验证：`backend/tests/test_image_generation.py`、`backend/tests/test_jianying.py`，以及完整后端回归 367 项通过。前端构建在当前受限环境中被 esbuild 读取目录权限阻断，需在正常开发环境复核。
