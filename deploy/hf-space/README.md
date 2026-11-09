---
title: Wardrowbe Virtual Try-On
emoji: 👕
colorFrom: indigo
colorTo: purple
sdk: gradio
sdk_version: 5.44.0
app_file: app.py
pinned: false
short_description: 上传本人照 + 衣服照，用你自己的中转站图像模型生成上身效果图
---

# 虚拟试穿（Wardrowbe fork 的 AI 上身效果图功能）

把 `deploy/hf-space/` 里的 4 个文件（`app.py`、`core.py`、`requirements.txt`、本 `README.md`）
传到一个新建的 Hugging Face Space（SDK 选 **Gradio**）即可。

## 配置（必须）

在 Space 的 **Settings → Variables and secrets** 添加 Secrets（密钥务必用 Secret，不要用 Variables，否则会暴露）：

| Secret | 说明 | 示例 |
| --- | --- | --- |
| `TRYON_BASE_URL` | 中转站地址，一般带 `/v1` | `https://your-relay.example.com/v1` |
| `TRYON_API_KEY` | 中转站密钥 | `sk-...` |
| `TRYON_MODEL` | 支持出图的模型名 | `gpt-image-1` / gemini-image / nano-banana 类中转模型 |
| `TRYON_MODE` | `chat`（多模态图进图出，默认）或 `images`（`/images/generations`） | `chat` |

可选：`TRYON_ENDPOINT`（非标准路径）、`TRYON_PROMPT`（自定义指令）、`TRYON_TIMEOUT`、`TRYON_PROXY`。

> 改完 Secret 需要 **Restart** Space。首页会显示当前配置状态。

## 隐私与费用

- 唯一出站请求：把「本人照片 + 衣服照片」POST 到 `TRYON_BASE_URL`。照片不落盘、不上传 HF 数据集。
- 按张计费，取决于你的中转站/模型；没有次数上限。
- 免费 Space 空闲会休眠（约 48h 无访问），首次打开有冷启动；磁盘临时，重启不保留数据——但本应用本来就不存数据。
- 2026 年起 HF 对「运行于 compute 的 Gradio/Docker Space」有付费计划要求；免费个人账号通常仍可用 ZeroGPU 免费额度托管少量 Gradio Space。若创建时提示需要 PRO，请改用仓库里的 `.devcontainer/`（GitHub Codespaces 免费额度）或本地 `tryon/start.bat`。

## 关联

完整衣橱系统见项目根目录 `TRYON.md`；零依赖本地预览见 `tryon/`。
