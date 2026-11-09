"""Virtual try-on service.

Renders "me wearing this garment" images by calling an OpenAI-compatible
image-model endpoint (a transit station / relay). Kept independent from the
wardrobe's tagging & recommendation AI, so it can be pointed at any provider.

Relay shapes (TRYON_MODE):
  "chat"    POST {base}/chat/completions   — multimodal; N images in, one image out
  "images"  POST {base}/images/generations — JSON image[] reference
  "edits"   POST {base}/images/edits       — multipart, repeated image[] (gpt-image-2.x)

Identity preservation: multiple photos of the same person (front/side/…), an
optional body profile, and `input_fidelity=high` all help keep the face and build
consistent. The relay response is parsed defensively (b64_json / data URI /
inline_data / markdown / remote URL), because relays differ widely.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings
from app.schemas.tryon import BodyProfile

logger = logging.getLogger(__name__)

_MAX_PHOTOS = 6

_MARKDOWN_DATA_URI = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)", re.I
)


def build_tryon_prompt(
    n_person: int,
    body: BodyProfile | None = None,
    user_prompt: str | None = None,
    default_prompt: str | None = None,
) -> str:
    """Compose an identity-locking prompt describing the image order.

    Images are sent as: [person_1 ... person_N, garment]. The garment is always
    the LAST image, so the prompt can refer to it unambiguously regardless of N.
    """
    if n_person == 1:
        layout = "第 1 张图片是本人参考照，第 2 张图片是要试穿的衣服。"
    else:
        layout = (
            f"第 1~{n_person} 张图片是【同一个人】的不同角度参考照（正面、侧面、半身等），"
            f"第 {n_person + 1} 张图片是要试穿的衣服。"
        )

    identity = (
        "【身份一致性，最重要】生成的人必须是参考照里的同一个人："
        "严格保持其五官、脸型、眼睛、眉毛、鼻子、嘴型、肤色、发型、发色、年龄感与面部特征，"
        "不要换脸、不要美化成另一个人、不要改变五官比例。"
        + ("多张参考照请互相印证，取一致的特征；" if n_person > 1 else "")
    )

    build = (
        "严格保持参考照里的体型、身高比例、肩宽、四肢比例与姿态，"
        "只替换服装，身体轮廓不要变成另一个人。"
    )

    garment = (
        "把最后一张图片里的衣服穿到这个人身上，忠实还原衣服的版型、颜色、图案、logo、材质与细节；"
        "结合其体型自然呈现合身程度、垂坠与褶皱，光影自然。只输出最终效果图。"
    )

    parts = [layout, identity, build]

    # Body measurements are hints for the model, appended as natural language.
    if body:
        bits: list[str] = []
        if body.height_cm:
            bits.append(f"身高约 {body.height_cm:g} cm")
        if body.weight_kg:
            bits.append(f"体重约 {body.weight_kg:g} kg")
        trio = [x for x in (body.bust_cm, body.waist_cm, body.hip_cm) if x]
        if len(trio) == 3:
            bits.append(f"三围约 {body.bust_cm:g}/{body.waist_cm:g}/{body.hip_cm:g} cm")
        elif trio:
            labels = []
            if body.bust_cm:
                labels.append(f"胸围 {body.bust_cm:g}")
            if body.waist_cm:
                labels.append(f"腰围 {body.waist_cm:g}")
            if body.hip_cm:
                labels.append(f"臀围 {body.hip_cm:g}")
            bits.append("、".join(labels) + " cm")
        if body.build:
            bits.append(f"体型{body.build}")
        if bits:
            parts.append("【体型参考】此人是：" + "，".join(bits) + "。据此还原身材与穿衣合身度，但以照片为准。")
        if body.notes:
            parts.append("【其他特征】" + body.notes.strip())

    parts.append(garment)

    if user_prompt and user_prompt.strip():
        parts.append("【附加要求】" + user_prompt.strip())
    elif default_prompt and default_prompt.strip():
        parts.append("【附加要求】" + default_prompt.strip())

    return "\n".join(parts)


class TryOnError(RuntimeError):
    """Raised when the relay is unavailable, misconfigured, or returns no image."""


class TryOnNotConfiguredError(TryOnError):
    pass


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
    elif isinstance(node, str):
        yield node


def _decode_b64(text: str) -> bytes | None:
    try:
        return base64.b64decode(re.sub(r"\s", "", text), validate=False)
    except Exception:
        return None


def _extract_image_bytes(data: dict) -> tuple[bytes, str]:
    """Return (image_bytes, mime_type)."""
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        for key in ("b64_json", "base64", "image_base64"):
            val = node.get(key)
            if isinstance(val, str) and len(val) > 200:
                raw = _decode_b64(val)
                if raw:
                    return raw, "image/png"
        for key in ("data", "inline_data"):
            val = node.get(key)
            if isinstance(val, dict):
                val = val.get("data") or val.get("b64_json")
            if isinstance(val, str) and len(val) > 200 and not val.startswith("data:"):
                raw = _decode_b64(val)
                if raw:
                    mime = node.get("mime_type") or node.get("mimeType") or "image/png"
                    return raw, mime
        for key in ("url", "image_url", "b64"):
            uri = node.get(key)
            if isinstance(uri, dict):
                uri = uri.get("url")
            if isinstance(uri, str) and uri.startswith("data:image"):
                mime = uri[5:].split(";", 1)[0]
                raw = _decode_b64(uri.split(",", 1)[1])
                if raw:
                    return raw, mime

    for node in _walk(data):
        if isinstance(node, str):
            m = _MARKDOWN_DATA_URI.search(node)
            if m and len(m.group(1)) > 200:
                raw = _decode_b64(m.group(1))
                if raw:
                    mm = re.search(r"data:(image/[a-zA-Z0-9.+-]+)", node)
                    return raw, (mm.group(1) if mm else "image/png")

    for node in _walk(data):
        if isinstance(node, str):
            m = re.search(r"https?://\S+\.(?:png|jpe?g|webp)(?:\?\S*)?", node, re.I)
            if m:
                url = m.group(0).rstrip(")>\"']")
                try:
                    with httpx.Client(timeout=60, follow_redirects=True) as client:
                        resp = client.get(url)
                        resp.raise_for_status()
                        ctype = resp.headers.get("content-type", "image/png").split(";")[0]
                        return resp.content, ctype
                except Exception as exc:  # noqa: BLE001
                    logger.warning("tryon: failed to download result image %s: %s", url, exc)

    raise TryOnError(
        "中转站返回里没找到图片，请确认模型支持出图、或调整 TRYON_MODE。"
        "响应片段: " + json.dumps(data, ensure_ascii=False)[:600]
    )


class TryOnService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def is_configured(self) -> bool:
        s = self.settings
        return bool(s.tryon_enabled and s.tryon_base_url and s.tryon_api_key and s.tryon_model)

    def _mode(self) -> str:
        mode = (self.settings.tryon_mode or "chat").lower()
        return mode if mode in ("chat", "images", "edits") else "chat"

    def status_host(self) -> str | None:
        s = self.settings
        if not s.tryon_base_url:
            return None
        parsed = urlparse(s.tryon_base_url)
        return parsed.netloc or None

    def _build_url(self) -> str:
        s = self.settings
        if s.tryon_endpoint:
            if s.tryon_endpoint.startswith("http"):
                return s.tryon_endpoint
            return s.tryon_base_url.rstrip("/") + "/" + s.tryon_endpoint.lstrip("/")
        path = {"chat": "chat/completions", "images": "images/generations", "edits": "images/edits"}[self._mode()]
        return s.tryon_base_url.rstrip("/") + "/" + path

    async def generate(
        self,
        person_b64_list: list[str],
        garment_b64: str,
        prompt: str | None = None,
        size: str | None = None,
        body: BodyProfile | None = None,
        preserve_face: bool = True,
    ) -> tuple[bytes, str]:
        if not self.is_configured():
            raise TryOnNotConfiguredError(
                "虚拟试穿未配置：请在 .env 设置 TRYON_ENABLED / TRYON_BASE_URL / "
                "TRYON_API_KEY / TRYON_MODEL / TRYON_MODE"
            )
        s = self.settings
        persons = person_b64_list[:_MAX_PHOTOS]
        effective_prompt = build_tryon_prompt(
            len(persons),
            body=body,
            user_prompt=prompt,
            default_prompt=s.tryon_prompt or None,
        )
        url = self._build_url()
        headers = {"Authorization": "Bearer " + (s.tryon_api_key or "")}
        mode = self._mode()
        all_uri = ["data:image/jpeg;base64," + b for b in persons] + [
            "data:image/jpeg;base64," + garment_b64
        ]

        try:
            async with httpx.AsyncClient(timeout=s.tryon_timeout, follow_redirects=True) as client:
                if mode == "edits":
                    # /images/edits requires multipart with repeated image[] parts.
                    fields = {
                        "model": s.tryon_model,
                        "prompt": effective_prompt,
                        "n": "1",
                        "size": size or "1024x1536",
                    }
                    if preserve_face:
                        fields["input_fidelity"] = "high"
                    files = [
                        ("image[]", (f"person{i + 1}.png", base64.b64decode(b), "image/png"))
                        for i, b in enumerate(persons)
                    ] + [("image[]", ("garment.png", base64.b64decode(garment_b64), "image/png"))]
                    resp = await client.post(url, headers=headers, data=fields, files=files)
                elif mode == "images":
                    headers["Content-Type"] = "application/json"
                    payload = {
                        "model": s.tryon_model,
                        "prompt": effective_prompt,
                        "n": 1,
                        "size": size or "1024x1536",
                        "image": all_uri,
                        "response_format": "b64_json",
                    }
                    if preserve_face:
                        payload["input_fidelity"] = "high"
                    resp = await client.post(url, headers=headers, json=payload)
                else:  # chat
                    headers["Content-Type"] = "application/json"
                    content = [{"type": "text", "text": effective_prompt}] + [
                        {"type": "image_url", "image_url": {"url": u}} for u in all_uri
                    ]
                    resp = await client.post(
                        url,
                        headers=headers,
                        json={"model": s.tryon_model, "messages": [{"role": "user", "content": content}]},
                    )
        except httpx.RequestError as exc:
            raise TryOnError(f"无法连接中转站 ({url}): {exc}") from None

        if resp.status_code != 200:
            raise TryOnError(f"中转站返回 HTTP {resp.status_code}: {resp.text[:600]}")

        try:
            data = resp.json()
        except Exception:
            raise TryOnError("中转站返回的不是 JSON: " + resp.text[:400]) from None

        return _extract_image_bytes(data)
