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

MAX_PHOTOS = 6


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
    path = {"chat": "chat/completions", "images": "images/generations", "edits": "images/edits"}.get(cfg.mode, "chat/completions")
    return f"### ✅ 已就绪\n模型 `{cfg.model}` · 模式 `{cfg.mode}` · 端点 `{cfg.base_url.rstrip('/')}/{path}`"


def _to_b64(path: str | None) -> str:
    if not path:
        raise gr.Error("请先上传图片")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _bytes_to_pil(raw: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(raw))
    return im.convert("RGB") if im.mode not in ("RGB", "L") else im


def try_on(person_paths, garment_path, prompt, preserve_face,
           height_cm, weight_kg, bust_cm, waist_cm, hip_cm, build, notes):
    if not _configured():
        raise gr.Error("尚未配置中转站：请在 Space 的 Settings → Secrets 里填好 TRYON_* 后重启。")
    if not person_paths or not garment_path:
        raise gr.Error("请至少上传一张本人照片和一件衣服。")

    # Gradio Files (type=filepath) returns a list of paths (or tempdir objects).
    paths = person_paths if isinstance(person_paths, list) else [person_paths]
    paths = [p if isinstance(p, str) else getattr(p, "name", str(p)) for p in paths][:MAX_PHOTOS]

    body = {}
    for k, v in [("height_cm", height_cm), ("weight_kg", weight_kg),
                 ("bust_cm", bust_cm), ("waist_cm", waist_cm), ("hip_cm", hip_cm)]:
        if v and float(v) > 0:
            body[k] = float(v)
    if build and str(build).strip():
        body["build"] = str(build).strip()
    if notes and str(notes).strip():
        body["notes"] = str(notes).strip()

    cfg = TryOnConfig.from_env()
    try:
        raw = generate(
            [_to_b64(p) for p in paths],
            _to_b64(garment_path),
            cfg,
            body=body or None,
            preserve_face=bool(preserve_face),
            extra_prompt=(prompt.strip() if prompt and prompt.strip() else None),
        )
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
        "上传 1–6 张自己的照片（正面/侧面等多角度，人脸更一致）+ 一件衣服，生成合成图。"
        "照片只发给你配置的中转站，不会存到本 Space。"
    )
    status = gr.Markdown(_status_md())

    with gr.Row():
        with gr.Column():
            person = gr.Files(label="① 本人照片（可多选，最多 6 张）", file_types=["image"], type="filepath")
        with gr.Column():
            garment = gr.Image(type="filepath", label="② 衣服照片（平铺/挂拍单件）", height=320)

    with gr.Accordion("身材信息（可选，帮助还原体型与合身度）", open=False):
        with gr.Row():
            height_cm = gr.Number(label="身高 cm")
            weight_kg = gr.Number(label="体重 kg")
            bust_cm = gr.Number(label="胸围 cm")
            waist_cm = gr.Number(label="腰围 cm")
            hip_cm = gr.Number(label="臀围 cm")
        with gr.Row():
            build = gr.Textbox(label="体型（偏瘦/匀称/健壮…）")
            notes = gr.Textbox(label="其他特征（肩宽/发型/肤色…）")
        gr.Markdown("<small>以照片为准，数值只作文字提示，不会真的按厘米建模。</small>")

    prompt = gr.Textbox(
        label="附加要求（可选）",
        placeholder="留空用默认指令。例如：外套拉链拉上、背景换成街拍",
        lines=2,
    )
    preserve_face = gr.Checkbox(value=True, label="锁定人脸（input_fidelity=high，更不容易换脸）")
    with gr.Row():
        btn = gr.Button("生成上身效果图", variant="primary", scale=2)
        refresh = gr.Button("刷新配置状态", scale=1)

    out = gr.Image(label="生成结果（右键可保存）", height=480)
    gr.Markdown(
        "<small>提示：免费 Space 空闲会休眠，首次打开需等冷启动；出图约 10–60 秒。"
        "多角度、清晰的参考照能明显改善人脸一致性。</small>"
    )

    btn.click(
        try_on,
        inputs=[person, garment, prompt, preserve_face,
                height_cm, weight_kg, bust_cm, waist_cm, hip_cm, build, notes],
        outputs=out,
    )
    refresh.click(_status_md, inputs=None, outputs=status)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("TRYON_PORT", "7860")))
    demo.launch(server_name="0.0.0.0", server_port=port)
