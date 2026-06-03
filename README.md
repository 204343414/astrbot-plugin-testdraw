# astrbot_plugin_draw 🎨

极简画图插件，开小灶用。`/画图 提示词`（可附带图片做图生图），走 OpenAI 格式接口。
json 里填 url / api / 模型名就能画。

## 用法

```
/画图 一只戴墨镜的柴犬
/画图 把这张图改成水彩风   （同时发送一张图片 → 图生图）
```
别名：`/draw`、`/绘图`

## 安装

把整个 `astrbot_plugin_draw` 文件夹丢进 AstrBot 的 `data/plugins/` 目录，重启 / 重载插件即可。
首次加载会自动装 `requirements.txt` 里的 `aiohttp`。

## 配置（WebUI 插件配置页，或 data 下生成的 json）

| 字段 | 说明 |
|---|---|
| `base_url` | 接口地址，只填到 `/v1`，路径自动拼。例：`https://api.openai.com/v1` |
| `api_key` | 你的 Key |
| `model` | 模型名 |
| `mode` | `images` 或 `chat`（见下） |
| `size` | 尺寸，仅 images 模式生效，如 `1024x1024`、`auto` |
| `admin_only` | `true`=仅管理员可用；`false`=所有人可用 |
| `timeout` | 请求超时秒数，默认 180 |
| `extra_prompt` | 每次自动追加的提示词，可留空 |

## 两种模式

- **images**：标准 `/v1/images/generations`（文生图）和 `/v1/images/edits`（图生图）。
  适用 DALL·E 风格、`gpt-image-1` 等。返回 `url` 或 `b64_json` 都能处理。
- **chat**：`/v1/chat/completions` 多模态。图片+提示词丢进 chat，模型把图返回。
  适用各类把绘图能力包成 chat 的中转模型（如 `gpt-4o-image`、`gemini-2.5-flash-image` / nano-banana 等）。
  返回的图支持：`message.images` 字段、content 里的 markdown 图、data-uri、直链。

## 常见组合示例

文生图（OpenAI 官方）：
```
base_url = https://api.openai.com/v1
model    = gpt-image-1
mode     = images
```

中转 chat 画图：
```
base_url = https://你的中转/v1
model    = gpt-4o-image
mode     = chat
```
