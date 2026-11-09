"""Hugging Face Spaces (Gradio) entry point for the virtual try-on feature.

Zero application dependencies beyond Gradio/Pillow: the relay call itself lives
in core.py (Python stdlib only). Configure the relay via Space Secrets:
    TRYON_BASE_URL, TRYON_API_KEY, TRYON_MODEL, TRYON_MODE (optional),
    TRYON_ENDPOINT / TRYON_PROMPT / TRYON_TIMEOUT (optional)

Photos are sent ONLY to the relay you configure. Never hard-code a key here.
"""

import base64
import io
import os

import gradio as gr
from PIL import Image

from core import TransitError, TryOnConfig, generate


def _configured() -> bool:
    cfg = TryOnConfig.from_env()
    return bool(cfg.base_url and cfg.api_key and cfg.model)


def _status_md() -> str:
    cfg = TryOnConfig.from_env()
    if not (cfg.base_url and cfg.api_key and cfg.model):
        return (
            "### ⚠️ 尚未配置中转站\n"
            "在 Space 的 **Settings → Variables and secrets → Secrets** 添加 "
            "`TRYON_BASE_URL`、`TRYON_API_KEY`、`TRYON_MODEL`（可选 `TRYON_MODE`），然后重启 Space。"
        )
    path = "chat/completions" if cfg.mode == "chat" else "images/generations"
    return (
        f"### ✅ 已就绪\n模型 `{cfg.model}` · 模式 `{cfg.mode}` · 端点 `{cfg.base_url.rstrip('/')}/{path}`"
    )


def _to_b64(path: str | None) -> str:
    if not path:
        raise gr.Error("请先上传图片")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _bytes_to_pil(raw: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(raw))
    # Normalize to RGB so Gradio never chokes on RGBA/P modes.
    return im.convert("RGB") if im.mode not in ("RGB", "L") else im


def try_on(person_path, garment_path, prompt):
    if not _configured():
        raise gr.Error("尚未配置中转站：请在 Space 的 Settings → Secrets 里填好 TRYON_* 后重启。")
    if not person_path or not garment_path:
        raise gr.Error("请上传『本人照片』和『衣服照片』两张图。")

    cfg = TryOnConfig.from_env()
    if prompt and prompt.strip():
        cfg.prompt = prompt.strip()

    try:
        raw = generate(_to_b64(person_path), _to_b64(garment_path), cfg)
    except (ValueError, TransitError) as exc:
        raise gr.Error(str(exc)) from None
    except Exception as exc:  # noqa: BLE001
        raise gr.Error(f"生成失败：{exc}") from None
    return _bytes_to_pil(raw)


CSS = """
#title { text-align: center; }
footer { display: none !important; }
"""

with gr.Blocks(title="虚拟试穿 · Wardrowbe", css=CSS, theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 虚拟试穿 · AI 上身效果图", elem_id="title")
    gr.Markdown(
        "上传一张自己的正面照 + 一件衣服，生成「你穿上它」的合成图。"
        "照片只发给你配置的中转站，不会存到本 Space。"
    )
    status = gr.Markdown(_status_md())

    with gr.Row():
        with gr.Column():
            person = gr.Image(type="filepath", label="① 本人照片（正面、光线均匀）", height=320)
        with gr.Column():
            garment = gr.Image(type="filepath", label="② 衣服照片（平铺/挂拍单件）", height=320)

    prompt = gr.Textbox(
        label="附加要求（可选）",
        placeholder="留空用默认指令。例如：外套拉链拉上、背景换成街拍",
        lines=2,
    )
    with gr.Row():
        btn = gr.Button("生成上身效果图", variant="primary", scale=2)
        refresh = gr.Button("刷新配置状态", scale=1)

    out = gr.Image(label="生成结果（右键可保存）", height=480)
    gr.Markdown(
        "<small>提示：免费 Space 空闲会休眠，首次打开需等冷启动；出图约 10–60 秒，"
        "若超时请换更快的中转站/模型。效果主要看输入照片质量。</small>"
    )

    btn.click(try_on, inputs=[person, garment, prompt], outputs=out)
    refresh.click(_status_md, inputs=None, outputs=status)

if __name__ == "__main__":
    # HF Spaces expects the app on 7860; local run falls back to 8787.
    port = int(os.environ.get("PORT", os.environ.get("TRYON_PORT", "7860")))
    demo.launch(server_name="0.0.0.0", server_port=port)
