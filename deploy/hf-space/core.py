"""Virtual try-on core (transit-station API, OpenAI-compatible).

This module is deliberately dependency-free (stdlib only) so it can run both
inside the Wardrowbe backend container and as a standalone local tool.

It supports the two shapes most OpenAI-compatible transit stations expose for
image models:

  mode="chat"    POST {base}/chat/completions   (multimodal, images in + image out)
                 e.g. gemini-*-image / nano-banana / gpt-4o-image relays
  mode="images"  POST {base}/images/generations (image[] as reference)
                 e.g. gpt-image-* relays

The generated image is extracted from whatever the station returns
(b64_json / data URI / inline_data / markdown image / remote URL).
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

DEFAULT_PROMPT = (
    "把第 2 张图片里的衣服，穿到第 1 张图片的人身上，生成一张自然的上身效果图。"
    "保持人物的脸、发型、身材、姿势和背景不变，只替换服装；"
    "衣服的版型、颜色、图案、logo 尽量忠实还原，光影和褶皱自然。只输出图片。"
)

_MARKDOWN_DATA_URI = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)", re.I
)


@dataclass
class TryOnConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    mode: str = "chat"                 # "chat" | "images" | "edits"
    endpoint: str = ""                # optional full path override, e.g. /v1/images/edits
    proxy: str = ""                   # optional explicit proxy; env proxies are ignored by default
    timeout: int = 180
    size: str = "1024x1536"
    prompt: str = DEFAULT_PROMPT
    extra_body: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, prefix: str = "TRYON_") -> "TryOnConfig":
        return cls(
            base_url=os.environ.get(prefix + "BASE_URL", "").strip().rstrip("/"),
            api_key=os.environ.get(prefix + "API_KEY", "").strip(),
            model=os.environ.get(prefix + "MODEL", "").strip(),
            mode=os.environ.get(prefix + "MODE", "chat").strip().lower() or "chat",
            endpoint=os.environ.get(prefix + "ENDPOINT", "").strip(),
            proxy=os.environ.get(prefix + "PROXY", "").strip(),
            timeout=int(os.environ.get(prefix + "TIMEOUT", "180") or 180),
            size=os.environ.get(prefix + "SIZE", "1024x1536").strip(),
            prompt=os.environ.get(prefix + "PROMPT", DEFAULT_PROMPT).strip() or DEFAULT_PROMPT,
        )

    def validate(self) -> None:
        missing = [k for k in ("base_url", "api_key", "model") if not getattr(self, k)]
        if missing:
            raise ValueError("缺少中转站配置: " + ", ".join("TRYON_" + k.upper() for k in missing))
        if self.mode not in ("chat", "images", "edits"):
            raise ValueError("TRYON_MODE 只能是 chat / images / edits")


class TransitError(RuntimeError):
    pass


def _opener(cfg: TryOnConfig):
    # Disable ambient proxies unless explicitly configured: a stale system proxy
    # silently breaking relay calls is a common source of confusing failures.
    if cfg.proxy:
        handlers = [urllib.request.ProxyHandler({"http": cfg.proxy, "https": cfg.proxy})]
    else:
        handlers = [urllib.request.ProxyHandler({})]
    return urllib.request.build_opener(*handlers)


def _post_json(cfg: TryOnConfig, url: str, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg.api_key,
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _opener(cfg).open(req, timeout=cfg.timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        raise TransitError(f"中转站返回 HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise TransitError(f"无法连接中转站 ({url}): {e.reason}") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise TransitError("中转站返回的不是 JSON: " + raw[:400]) from None


def _build_url(cfg: TryOnConfig) -> str:
    if cfg.endpoint:
        return cfg.endpoint if cfg.endpoint.startswith("http") else cfg.base_url + "/" + cfg.endpoint.lstrip("/")
    path = {"chat": "chat/completions", "images": "images/generations", "edits": "images/edits"}[cfg.mode]
    return cfg.base_url + "/" + path


def _post_multipart(cfg: TryOnConfig, url: str, fields: dict, files: list[tuple[str, str, bytes]]) -> dict:
    """POST multipart/form-data for image edit endpoints (stdlib only)."""
    boundary = "----wardrowbe" + os.urandom(12).hex()
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    for name, filename, content in files:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: image/png\r\n\r\n"
            ).encode("utf-8")
            + content
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Authorization": "Bearer " + cfg.api_key,
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with _opener(cfg).open(req, timeout=cfg.timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        raise TransitError(f"中转站返回 HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise TransitError(f"无法连接中转站 ({url}): {e.reason}") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise TransitError("中转站返回的不是 JSON: " + raw[:400]) from None


def _data_uri(b64: str, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{b64}"


def _payload(cfg: TryOnConfig, person_b64: str, garment_b64: str) -> dict:
    person = _data_uri(person_b64)
    garment = _data_uri(garment_b64)
    if cfg.mode == "chat":
        payload = {
            "model": cfg.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": cfg.prompt},
                        {"type": "image_url", "image_url": {"url": person}},
                        {"type": "image_url", "image_url": {"url": garment}},
                    ],
                }
            ],
        }
    else:
        payload = {
            "model": cfg.model,
            "prompt": cfg.prompt,
            "n": 1,
            "size": cfg.size,
            "image": [person, garment],
            "response_format": "b64_json",
        }
    payload.update(cfg.extra_body)
    return payload


def _walk(node):
    """Yield every dict/str leaf in a nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)
    elif isinstance(node, str):
        yield node


def _extract_image(data: dict, cfg: TryOnConfig) -> bytes:
    # 1) explicit base64 fields (b64_json / inline_data.data / image.data ...)
    for node in _walk(data):
        if not isinstance(node, dict):
            continue
        for key in ("b64_json", "base64", "image_base64", "data"):
            val = node.get(key)
            if isinstance(val, str) and len(val) > 200 and not val.startswith("data:"):
                try:
                    return base64.b64decode(val)
                except Exception:
                    pass
        uri = node.get("url") or node.get("image_url") or node.get("b64")
        if isinstance(uri, dict):
            uri = uri.get("url")
        if isinstance(uri, str) and uri.startswith("data:image"):
            try:
                return base64.b64decode(uri.split(",", 1)[1])
            except Exception:
                pass

    # 2) data URI embedded in text / markdown / multimodal content parts
    for node in _walk(data):
        if isinstance(node, str):
            m = _MARKDOWN_DATA_URI.search(node)
            if m and len(m.group(1)) > 200:
                try:
                    return base64.b64decode(re.sub(r"\s", "", m.group(1)))
                except Exception:
                    pass

    # 3) plain http(s) image URL -> download
    for node in _walk(data):
        if isinstance(node, str):
            m = re.search(r"https?://\S+\.(?:png|jpe?g|webp)(?:\?\S*)?", node, re.I)
            if m:
                url = m.group(0).rstrip(")>\"']")
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with _opener(cfg).open(req, timeout=cfg.timeout) as resp:
                        return resp.read()
                except Exception:
                    pass

    raise TransitError(
        "中转站返回里没找到图片。原始响应片段: " + json.dumps(data, ensure_ascii=False)[:600]
    )


def generate(person_b64: str, garment_b64: str, cfg: TryOnConfig | None = None) -> bytes:
    """Return the generated try-on image as raw bytes (PNG/JPEG)."""
    cfg = cfg or TryOnConfig.from_env()
    cfg.validate()
    url = _build_url(cfg)
    if cfg.mode == "edits":
        # /images/edits expects multipart with repeated `image[]` parts.
        person = base64.b64decode(person_b64)
        garment = base64.b64decode(garment_b64)
        fields = {"model": cfg.model, "prompt": cfg.prompt, "n": "1", "size": cfg.size}
        fields.update({k: str(v) for k, v in cfg.extra_body.items()})
        files = [("image[]", "person.png", person), ("image[]", "garment.png", garment)]
        data = _post_multipart(cfg, url, fields, files)
    else:
        data = _post_json(cfg, url, _payload(cfg, person_b64, garment_b64))
    return _extract_image(data, cfg)
