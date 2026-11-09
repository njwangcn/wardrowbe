"""Standalone local try-on tool — no Docker, no database, stdlib only.

Use this to preview "me wearing this garment" images immediately on any machine
with Python 3.10+, before (or instead of) deploying the full Wardrowbe stack.

Usage:
    set TRYON_BASE_URL=https://your-relay/v1        (Windows CMD)
    set TRYON_API_KEY=sk-...
    set TRYON_MODEL=your-image-model
    set TRYON_MODE=chat            # or "images"
    python tryon/serve.py
    # open http://127.0.0.1:8787

Or put those values in tryon/.env (KEY=VALUE lines) and just run the script.
The .env file is gitignored — never commit your key.

Only outbound traffic is a single POST to the relay you configure; images live
in your browser and this process, nothing is stored on disk by default.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Allow running as `python tryon/serve.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import (  # noqa: E402
    DEFAULT_PROMPT,
    TransitError,
    TryOnConfig,
    generate,
)

PORT = int(os.environ.get("TRYON_PORT", "8787"))


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:  # real env vars win
            os.environ[k] = v


HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>虚拟试穿 · 本地预览</title>
<style>
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;font:15px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  background:#f6f7f9;color:#1a1a1a;padding:24px;max-width:960px;margin:auto}
h1{font-size:22px;margin:0 0 4px}
p.sub{color:#666;margin:0 0 20px;font-size:13px}
.grid{display:flex;gap:16px;flex-wrap:wrap}
.card{flex:1;min-width:260px;background:#fff;border:1px solid #e3e5e8;border-radius:12px;padding:16px}
.card h2{font-size:14px;margin:0 0 4px}
.card .hint{font-size:12px;color:#888;margin:0 0 12px}
.drop{border:2px dashed #cbd0d6;border-radius:10px;height:220px;display:flex;align-items:center;
  justify-content:center;flex-direction:column;gap:8px;cursor:pointer;color:#888;overflow:hidden}
.drop:hover{border-color:#4f7cff;color:#4f7cff}
.drop img{max-height:100%;max-width:100%;object-fit:contain}
textarea,input[type=text]{width:100%;border:1px solid #d5d9de;border-radius:8px;padding:9px;font:inherit}
button.big{background:#2f6bff;color:#fff;border:0;border-radius:9px;padding:11px 20px;font-size:15px;
  cursor:pointer;margin:16px 8px 0 0}
button.big:disabled{opacity:.5;cursor:not-allowed}
button.ghost{background:#eef1f5;color:#333;border:0;border-radius:9px;padding:11px 18px;cursor:pointer}
.status{font-size:13px;color:#666;margin-top:14px;white-space:pre-wrap}
.err{color:#c0392b;margin-top:12px;white-space:pre-wrap;font-size:13px}
#out{margin-top:20px;text-align:center}
#out img{max-height:560px;max-width:100%;border-radius:12px;border:1px solid #e3e5e8}
</style></head><body>
<h1>虚拟试穿 · 本地预览</h1>
<p class="sub">上传一张自己的正面照 + 一件衣服，AI 生成你穿上它的合成图。照片只发给你在 .env 里配置的中转站。</p>
<div class="grid">
  <div class="card"><h2>① 本人照片</h2><p class="hint">正面、光线均匀、背景干净</p>
    <label class="drop" id="dropP"><span>点击选择 / 拖入图片</span>
      <input type="file" accept="image/*" hidden id="fileP"></label></div>
  <div class="card"><h2>② 衣服照片</h2><p class="hint">平铺或挂拍的单件，图案完整</p>
    <label class="drop" id="dropG"><span>点击选择 / 拖入图片</span>
      <input type="file" accept="image/*" hidden id="fileG"></label></div>
</div>
<div class="card" style="margin-top:16px">
  <h2>附加要求（可选）</h2>
  <textarea id="prompt" rows="2" placeholder="留空用默认指令。例如：外套拉链拉上、换成街拍背景"></textarea>
  <button class="big" id="go">生成上身效果图</button>
  <button class="ghost" id="dl" style="display:none">下载图片</button>
  <div class="status" id="status"></div>
  <div class="err" id="err"></div>
</div>
<div id="out"></div>
<script>
let P=null,G=null,LAST=null;
function bind(drop,file,set){const d=document.getElementById(drop),f=document.getElementById(file);
 const read=(file)=>{if(!file)return;const r=new FileReader();r.onload=()=>{set(r.result);
   d.innerHTML='';const img=document.createElement('img');img.src=r.result;d.appendChild(img);};r.readAsDataURL(file);};
 f.onchange=()=>read(f.files[0]);
 d.ondragover=e=>{e.preventDefault();};d.ondrop=e=>{e.preventDefault();read(e.dataTransfer.files[0]);};}
bind('dropP','fileP',v=>P=v);bind('dropG','fileG',v=>G=v);
const b64=s=>s.split(',')[1]||s;
document.getElementById('go').onclick=async()=>{
 const err=document.getElementById('err'),st=document.getElementById('status'),btn=document.getElementById('go');
 err.textContent='';if(!P||!G){err.textContent='请先上传两张图片';return;}
 btn.disabled=true;st.textContent='生成中，约 10–60 秒…';document.getElementById('out').innerHTML='';
 try{const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({person_image:b64(P),garment_image:b64(G),prompt:document.getElementById('prompt').value||null})});
   const j=await r.json();if(!r.ok)throw new Error(j.detail||('HTTP '+r.status));
   LAST='data:'+j.mime_type+';base64,'+j.image_base64;
   const img=document.createElement('img');img.src=LAST;
   const o=document.getElementById('out');o.innerHTML='';o.appendChild(img);
   document.getElementById('dl').style.display='inline-block';
   st.textContent='完成 · '+j.model+' · '+j.mode;
 }catch(e){err.textContent=String(e.message||e);st.textContent='';}finally{btn.disabled=false;}};
document.getElementById('dl').onclick=()=>{if(!LAST)return;const a=document.createElement('a');
 a.href=LAST;a.download='tryon-'+Date.now()+'.png';a.click();};
fetch('/api/status').then(r=>r.json()).then(j=>{
 document.getElementById('status').textContent=j.configured?('已配置：'+j.model+' · '+j.mode):
   ('未配置：请在 tryon/.env 或环境变量设置 TRYON_BASE_URL / TRYON_API_KEY / TRYON_MODEL / TRYON_MODE');});
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter
        print("[tryon] " + (fmt % args))

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path.startswith("/api/status"):
            cfg = TryOnConfig.from_env()
            self._json(200, {
                "configured": bool(cfg.base_url and cfg.api_key and cfg.model),
                "model": cfg.model, "mode": cfg.mode,
                "base_url_host": cfg.base_url,
            })
            return
        self._send(200, HTML.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        if not self.path.startswith("/api/generate"):
            self._json(404, {"detail": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 40 * 1024 * 1024:
                self._json(413, {"detail": "图片过大"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            cfg = TryOnConfig.from_env()
            if payload.get("prompt"):
                cfg.prompt = str(payload["prompt"])
            image = generate(payload["person_image"], payload["garment_image"], cfg)
            self._json(200, {
                "image_base64": base64.b64encode(image).decode(),
                "mime_type": "image/png", "model": cfg.model, "mode": cfg.mode,
            })
        except ValueError as exc:
            self._json(400, {"detail": str(exc)})
        except TransitError as exc:
            self._json(502, {"detail": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"detail": "本地处理失败: " + str(exc)})


def main():
    _load_env_file()
    cfg = TryOnConfig.from_env()
    path = {"chat": "chat/completions", "images": "images/generations", "edits": "images/edits"}.get(cfg.mode, "chat/completions")
    url = cfg.endpoint or ((cfg.base_url.rstrip("/") + "/" + path) if cfg.base_url else "-")
    prompt = cfg.prompt or DEFAULT_PROMPT
    print("虚拟试穿本地预览 · http://127.0.0.1:%d" % PORT)
    print("  中转站: %s" % (cfg.base_url or "(未配置)"))
    print("  模型  : %s   模式: %s" % (cfg.model or "(未配置)", cfg.mode))
    print("  请求路径: %s" % url)
    print("  提示词  : %s" % (prompt[:40] + "…" if len(prompt) > 40 else prompt))
    if not (cfg.base_url and cfg.api_key and cfg.model):
        print("\n  ⚠ 还没配置中转站：把下面几项写进 tryon/.env 再运行\n"
              "    TRYON_BASE_URL / TRYON_API_KEY / TRYON_MODEL / TRYON_MODE")
    print("\n按 Ctrl+C 退出。\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
