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

DEFAULT_PROMPT = ""  # built dynamically by build_tryon_prompt (image order matters)


def build_tryon_prompt(n_person: int, body: dict | None = None, extra: str | None = None) -> str:
    """Identity-locking prompt. Images are sent as [person_1..N, garment], garment is LAST."""
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
        "不要换脸、不要美化成另一个人、不要改变五官比例；"
        "不要输出常见的通用模特脸/网红脸，不要磨皮、瘦脸、改发际线、改年龄。"
        "宁可保留参考照里的瑕疵（痘痘、细纹、脸型不对称），也不要美化。"
        + ("多张参考照请互相印证，取一致的特征；" if n_person > 1 else "")
    )
    build = "严格保持参考照里的体型、身高比例、肩宽、四肢比例与姿态，只替换服装，身体轮廓不要变成另一个人。"
    garment = (
        "把最后一张图片里的衣服穿到这个人身上，忠实还原衣服的版型、颜色、图案、logo、材质与细节；"
        "结合其体型自然呈现合身程度、垂坠与褶皱，光影自然。只输出最终效果图。"
    )
    preamble = (
        f"Reference images 1-{n_person} are photos of the SAME real person; "
        f"image {n_person + 1} is the garment to try on. "
        "CRITICAL: reproduce that exact person — same face shape, eyes, eyebrows, nose, mouth, "
        "skin tone, hairstyle, hair colour and apparent age. Never swap in a generic or idealized "
        "model face, never retouch, slim or age-change the face. "
        "Keep the person's real body proportions from the reference photos and just replace the "
        f"clothing with the garment from image {n_person + 1}, matching its cut, colour, pattern, "
        "logo, fabric and details. Photorealistic, natural light, plain background. "
        "Output only the final image."
    )
    parts = [preamble, layout, identity, build]
    if body:
        bits = []
        num = lambda k, unit: (f"{body[k]:g}{unit}" if isinstance(body.get(k), (int, float)) else None)
        for k, label, unit in [("height_cm", "身高约", " cm"), ("weight_kg", "体重约", " kg")]:
            v = num(k, unit)
            if v:
                bits.append(label + " " + v)
        trio = [body.get(k) for k in ("bust_cm", "waist_cm", "hip_cm")]
        if all(isinstance(x, (int, float)) for x in trio):
            bits.append(f"三围约 {trio[0]:g}/{trio[1]:g}/{trio[2]:g} cm")
        if body.get("build"):
            bits.append("体型" + str(body["build"]))
        if bits:
            parts.append("【体型参考】此人是：" + "，".join(bits) + "。据此还原身材与穿衣合身度，但以照片为准。")
        if body.get("notes"):
            parts.append("【其他特征】" + str(body["notes"]).strip())
    parts.append(garment)
    if extra and extra.strip():
        parts.append("【附加要求】" + extra.strip())
    return "\n".join(parts)

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


def _payload(cfg: TryOnConfig, person_b64_list: list[str], garment_b64: str, prompt: str, preserve_face: bool) -> dict:
    all_uri = [_data_uri(b) for b in person_b64_list] + [_data_uri(garment_b64)]
    if cfg.mode == "chat":
        payload = {
            "model": cfg.model,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}]
                    + [{"type": "image_url", "image_url": {"url": u}} for u in all_uri],
                }
            ],
        }
    else:
        payload = {
            "model": cfg.model,
            "prompt": prompt,
            "n": 1,
            "size": cfg.size,
            "image": all_uri,
            "response_format": "b64_json",
        }
        if preserve_face:
            payload["input_fidelity"] = "high"
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


def generate(
    person_b64: str | list[str],
    garment_b64: str,
    cfg: TryOnConfig | None = None,
    body: dict | None = None,
    preserve_face: bool = True,
    extra_prompt: str | None = None,
) -> bytes:
    """Return the generated try-on image as raw bytes (PNG/JPEG).

    `person_b64` accepts one base64 string (backward compatible) or a list of
    1..6 photos of the same person for better identity consistency.
    """
    cfg = cfg or TryOnConfig.from_env()
    cfg.validate()
    persons = [person_b64] if isinstance(person_b64, str) else list(person_b64)[:6]
    if not persons:
        raise ValueError("至少需要一张本人照片")
    prompt = build_tryon_prompt(len(persons), body, extra_prompt or None)
    url = _build_url(cfg)
    if cfg.mode == "edits":
        # /images/edits expects multipart with repeated `image[]` parts.
        fields = {"model": cfg.model, "prompt": prompt, "n": "1", "size": cfg.size}
        if preserve_face:
            fields["input_fidelity"] = "high"
        fields.update({k: str(v) for k, v in cfg.extra_body.items()})
        files = [
            ("image[]", f"person{i + 1}.png", base64.b64decode(b))
            for i, b in enumerate(persons)
        ] + [("image[]", "garment.png", base64.b64decode(garment_b64))]
        data = _post_multipart(cfg, url, fields, files)
    else:
        data = _post_json(cfg, url, _payload(cfg, persons, garment_b64, prompt, preserve_face))
    return _extract_image(data, cfg)
