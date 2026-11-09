"""Virtual try-on service.

Renders "me wearing this garment" images by calling an OpenAI-compatible
image-model endpoint (a transit station / relay). Kept independent from the
wardrobe's tagging & recommendation AI, so it can be pointed at any provider.

Two relay shapes are supported (TRYON_MODE):

  "chat"    POST {base}/chat/completions  — multimodal; two images in, one image out
            (gemini-*-image / nano-banana / gpt-4o-image style relays)
  "images"  POST {base}/images/generations — image[] as reference
            (gpt-image-* style relays)

The relay response is parsed defensively: b64_json / data URI / inline_data /
markdown image / remote URL are all handled, because relays differ widely.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

DEFAULT_PROMPT = (
    "把第 2 张图片里的衣服，穿到第 1 张图片的人身上，生成一张自然的上身效果图。"
    "保持人物的脸、发型、身材、姿势和背景不变，只替换服装；"
    "衣服的版型、颜色、图案、logo 尽量忠实还原，光影和褶皱自然。只输出图片。"
)

_MARKDOWN_DATA_URI = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)", re.I
)


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
    # 1) explicit base64 fields
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        for key in ("b64_json", "base64", "image_base64"):
            val = node.get(key)
            if isinstance(val, str) and len(val) > 200:
                raw = _decode_b64(val)
                if raw:
                    return raw, "image/png"
        # inline_data / data field carrying pure base64
        for key in ("data", "inline_data"):
            val = node.get(key)
            if isinstance(val, dict):
                val = val.get("data") or val.get("b64_json")
            if isinstance(val, str) and len(val) > 200 and not val.startswith("data:"):
                raw = _decode_b64(val)
                if raw:
                    mime = node.get("mime_type") or node.get("mimeType") or "image/png"
                    return raw, mime
        # url / image_url / b64 holding a data URI
        for key in ("url", "image_url", "b64"):
            uri = node.get(key)
            if isinstance(uri, dict):
                uri = uri.get("url")
            if isinstance(uri, str) and uri.startswith("data:image"):
                mime = uri[5:].split(";", 1)[0]
                raw = _decode_b64(uri.split(",", 1)[1])
                if raw:
                    return raw, mime

    # 2) data URI embedded in text / markdown / multimodal content parts
    for node in _walk(data):
        if isinstance(node, str):
            m = _MARKDOWN_DATA_URI.search(node)
            if m and len(m.group(1)) > 200:
                raw = _decode_b64(m.group(1))
                if raw:
                    mm = re.search(r"data:(image/[a-zA-Z0-9.+-]+)", node)
                    return raw, (mm.group(1) if mm else "image/png")

    # 3) a plain http(s) image URL — download it
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
        path = "chat/completions" if s.tryon_mode == "chat" else "images/generations"
        return s.tryon_base_url.rstrip("/") + "/" + path

    def _build_payload(self, person_b64: str, garment_b64: str, prompt: str | None, size: str | None) -> dict:
        s = self.settings
        effective_prompt = prompt or s.tryon_prompt or DEFAULT_PROMPT
        person = "data:image/jpeg;base64," + person_b64
        garment = "data:image/jpeg;base64," + garment_b64

        if s.tryon_mode == "images":
            return {
                "model": s.tryon_model,
                "prompt": effective_prompt,
                "n": 1,
                "size": size or "1024x1536",
                "image": [person, garment],
                "response_format": "b64_json",
            }
        return {
            "model": s.tryon_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": effective_prompt},
                        {"type": "image_url", "image_url": {"url": person}},
                        {"type": "image_url", "image_url": {"url": garment}},
                    ],
                }
            ],
        }

    async def generate(
        self,
        person_b64: str,
        garment_b64: str,
        prompt: str | None = None,
        size: str | None = None,
    ) -> tuple[bytes, str]:
        if not self.is_configured():
            raise TryOnNotConfiguredError(
                "虚拟试穿未配置：请在 .env 设置 TRYON_ENABLED / TRYON_BASE_URL / "
                "TRYON_API_KEY / TRYON_MODEL / TRYON_MODE"
            )
        s = self.settings
        url = self._build_url()
        payload = self._build_payload(person_b64, garment_b64, prompt, size)
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + (s.tryon_api_key or ""),
        }

        try:
            async with httpx.AsyncClient(timeout=s.tryon_timeout, follow_redirects=True) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise TryOnError(f"无法连接中转站 ({url}): {exc}") from None

        if resp.status_code != 200:
            raise TryOnError(f"中转站返回 HTTP {resp.status_code}: {resp.text[:600]}")

        try:
            data = resp.json()
        except Exception:
            raise TryOnError("中转站返回的不是 JSON: " + resp.text[:400]) from None

        return _extract_image_bytes(data)
