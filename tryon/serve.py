"""Standalone local try-on tool — no Docker, no database, stdlib only.

Use this to preview "me wearing this garment" images immediately on any machine
with Python 3.10+, before (or instead of) deploying the full Wardrowbe stack.

Usage:
    set TRYON_BASE_URL=https://your-relay/v1        (Windows CMD)
    set TRYON_API_KEY=sk-...
    set TRYON_MODEL=your-image-model
    set TRYON_MODE=edits            # or "chat" / "images"
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
    TransitError,
    TryOnConfig,
    build_tryon_prompt,
    generate,
)

PORT = int(os.environ.get("TRYON_PORT", "8787"))
MAX_PHOTOS = 6


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
.card{background:#fff;border:1px solid #e3e5e8;border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{font-size:14px;margin:0 0 4px}
.card .hint{font-size:12px;color:#888;margin:0 0 12px}
.thumbs{display:flex;gap:10px;flex-wrap:wrap}
.thumb{position:relative;width:96px;height:128px;border-radius:8px;overflow:hidden;border:1px solid #e3e5e8}
.thumb img{width:100%;height:100%;object-fit:cover}
.thumb .x{position:absolute;right:2px;top:2px;background:#0009;color:#fff;border:0;border-radius:50%;
  width:20px;height:20px;cursor:pointer;line-height:18px;text-align:center;font-size:13px}
.thumb .tag{position:absolute;left:0;bottom:0;background:#0009;color:#fff;font-size:10px;padding:1px 5px}
.drop{border:2px dashed #cbd0d6;border-radius:10px;min-height:128px;display:flex;align-items:center;
  justify-content:center;flex-direction:column;gap:6px;cursor:pointer;color:#888;overflow:hidden;padding:10px}
.drop:hover{border-color:#4f7cff;color:#4f7cff}
.drop img{max-height:100%;max-width:100%;object-fit:contain}
.add{width:96px;height:128px;border:2px dashed #cbd0d6;border-radius:8px;display:flex;align-items:center;
  justify-content:center;flex-direction:column;cursor:pointer;color:#888;gap:4px;font-size:12px}
.add:hover{border-color:#4f7cff;color:#4f7cff}
textarea,input[type=text],input[type=number]{width:100%;border:1px solid #d5d9de;border-radius:8px;padding:9px;font:inherit}
.bodrow{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
.bodrow>div{flex:1;min-width:110px}
.bodrow label{display:block;font-size:11px;color:#888;margin-bottom:2px}
button.big{background:#2f6bff;color:#fff;border:0;border-radius:9px;padding:11px 20px;font-size:15px;
  cursor:pointer;margin:16px 8px 0 0}
button.big:disabled{opacity:.5;cursor:not-allowed}
button.ghost{background:#eef1f5;color:#333;border:0;border-radius:9px;padding:11px 18px;cursor:pointer}
.opt{display:flex;align-items:center;gap:8px;font-size:13px;margin-top:12px}
.status{font-size:13px;color:#666;margin-top:14px;white-space:pre-wrap}
.err{color:#c0392b;margin-top:12px;white-space:pre-wrap;font-size:13px}
#out{margin-top:20px;text-align:center}
#out img{max-height:560px;max-width:100%;border-radius:12px;border:1px solid #e3e5e8}
details{margin-top:10px;font-size:12px;color:#666}
</style></head><body>
<h1>虚拟试穿 · 本地预览</h1>
<p class="sub">上传 1–6 张自己的照片（正面/侧面等多角度更准）+ 一件衣服，AI 生成你穿上它的合成图。照片只发给你在 .env 里配置的中转站。</p>

<div class="card"><h2>① 本人照片 <span id="cnt" style="font-weight:normal;color:#888;font-size:12px"></span></h2>
  <p class="hint">多角度、光线均匀、脸清晰，能明显提升人脸一致性。至少 1 张。</p>
  <div class="thumbs" id="thumbs"></div>
  <input type="file" accept="image/*" multiple hidden id="fileP">
</div>

<div class="card"><h2>② 衣服照片</h2><p class="hint">平铺或挂拍的单件，图案完整</p>
  <label class="drop" id="dropG" style="height:200px"><span>点击选择 / 拖入图片</span>
    <input type="file" accept="image/*" hidden id="fileG"></label>
</div>

<div class="card">
  <h2>身材信息（可选）</h2><p class="hint">帮助还原体型与合身度，以照片为准</p>
  <div class="bodrow">
    <div><label>身高 cm</label><input type="number" id="height_cm"></div>
    <div><label>体重 kg</label><input type="number" id="weight_kg"></div>
    <div><label>胸围 cm</label><input type="number" id="bust_cm"></div>
    <div><label>腰围 cm</label><input type="number" id="waist_cm"></div>
    <div><label>臀围 cm</label><input type="number" id="hip_cm"></div>
  </div>
  <div class="bodrow">
    <div><label>体型（偏瘦/匀称/健壮…）</label><input type="text" id="build"></div>
    <div><label>其他特征（肩宽/发型/肤色…）</label><input type="text" id="notes"></div>
  </div>
</div>

<div class="card">
  <h2>附加要求（可选）</h2>
  <textarea id="prompt" rows="2" placeholder="留空用默认指令。例如：外套拉链拉上、换成街拍背景"></textarea>
  <label class="opt"><input type="checkbox" id="face" checked> 锁定人脸（input_fidelity=high，更不容易换脸）</label>
  <button class="big" id="go">生成上身效果图</button>
  <button class="ghost" id="dl" style="display:none">下载图片</button>
  <div class="status" id="status"></div>
  <div class="err" id="err"></div>
</div>
<div id="out"></div>
<script>
let P=[],G=null,LAST=null;
const b64=s=>s.split(',')[1]||s;
const cnt=()=>document.getElementById('cnt').textContent='（'+P.length+'/' + 6 + ' 张）';
function renderP(){
 const box=document.getElementById('thumbs');box.innerHTML='';
 P.forEach((v,i)=>{const d=document.createElement('div');d.className='thumb';
  d.innerHTML='<img src="'+v+'"><span class="tag">'+(i===0?'主图':'#'+(i+1))+'</span><button class="x">×</button>';
  d.querySelector('.x').onclick=()=>{P.splice(i,1);renderP();};box.appendChild(d);});
 if(P.length<6){const a=document.createElement('div');a.className='add';
  a.innerHTML='＋<span>添加照片</span>';a.onclick=()=>document.getElementById('fileP').click();box.appendChild(a);}
 cnt();}
function readFiles(files){
 const room=6-P.length;Array.from(files).slice(0,room).forEach(f=>{const r=new FileReader();
  r.onload=()=>{P.push(r.result);renderP();};r.readAsDataURL(f);});}
document.getElementById('fileP').onchange=e=>{readFiles(e.target.files);e.target.value='';};
function readG(file){const r=new FileReader();r.onload=()=>{G=r.result;const d=document.getElementById('dropG');
  d.innerHTML='';const img=document.createElement('img');img.src=G;d.appendChild(img);};r.readAsDataURL(file);}
document.getElementById('fileG').onchange=e=>{if(e.target.files[0])readG(e.target.files[0]);e.target.value='';};
const dg=document.getElementById('dropG');dg.ondragover=e=>e.preventDefault();
dg.ondrop=e=>{e.preventDefault();if(e.dataTransfer.files[0])readG(e.dataTransfer.files[0]);};
const num=id=>{const v=document.getElementById(id).value;return v===''?undefined:Number(v);};
function bodyObj(){const b={};['height_cm','weight_kg','bust_cm','waist_cm','hip_cm'].forEach(k=>{const v=num(k);if(v>0)b[k]=v;});
 ['build','notes'].forEach(k=>{const v=document.getElementById(k).value.trim();if(v)b[k]=v;});
 return Object.keys(b).length?b:undefined;}
document.getElementById('go').onclick=async()=>{
 const err=document.getElementById('err'),st=document.getElementById('status'),btn=document.getElementById('go');
 err.textContent='';if(!P.length||!G){err.textContent='请至少上传一张本人照片和一件衣服';return;}
 btn.disabled=true;st.textContent='生成中，约 10–60 秒…';document.getElementById('out').innerHTML='';
 try{const r=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({person_images:P.map(b64),garment_image:b64(G),
     prompt:document.getElementById('prompt').value||null,
     body:bodyObj(),preserve_face:document.getElementById('face').checked})});
   const j=await r.json();if(!r.ok)throw new Error(j.detail||('HTTP '+r.status));
   LAST='data:'+j.mime_type+';base64,'+j.image_base64;
   const img=document.createElement('img');img.src=LAST;
   const o=document.getElementById('out');o.innerHTML='';o.appendChild(img);
   document.getElementById('dl').style.display='inline-block';
   st.textContent='完成 · '+j.model+' · '+j.mode+' · 用了 '+j.person_photo_count+' 张人像';
 }catch(e){err.textContent=String(e.message||e);st.textContent='';}finally{btn.disabled=false;}};
document.getElementById('dl').onclick=()=>{if(!LAST)return;const a=document.createElement('a');
 a.href=LAST;a.download='tryon-'+Date.now()+'.png';a.click();};
fetch('/api/status').then(r=>r.json()).then(j=>{
 document.getElementById('status').textContent=j.configured?('已配置：'+j.model+' · '+j.mode):
   ('未配置：请在 tryon/.env 或环境变量设置 TRYON_BASE_URL / TRYON_API_KEY / TRYON_MODEL / TRYON_MODE');});
renderP();
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
            if length > 60 * 1024 * 1024:
                self._json(413, {"detail": "图片过大"})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            cfg = TryOnConfig.from_env()
            persons = payload.get("person_images") or (
                [payload["person_image"]] if payload.get("person_image") else []
            )
            persons = persons[:MAX_PHOTOS]
            image = generate(
                persons,
                payload["garment_image"],
                cfg,
                body=payload.get("body"),
                preserve_face=payload.get("preserve_face", True),
                extra_prompt=payload.get("prompt"),
            )
            self._json(200, {
                "image_base64": base64.b64encode(image).decode(),
                "mime_type": "image/png", "model": cfg.model, "mode": cfg.mode,
                "person_photo_count": len(persons),
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
    prompt = build_tryon_prompt(1)
    print("虚拟试穿本地预览 · http://127.0.0.1:%d" % PORT)
    print("  中转站: %s" % (cfg.base_url or "(未配置)"))
    print("  模型  : %s   模式: %s" % (cfg.model or "(未配置)", cfg.mode))
    print("  请求路径: %s" % url)
    print("  提示词  : %s…" % prompt[:40])
    if not (cfg.base_url and cfg.api_key and cfg.model):
        print("\n  ⚠ 还没配置中转站：把下面几项写进 tryon/.env 再运行\n"
              "    TRYON_BASE_URL / TRYON_API_KEY / TRYON_MODEL / TRYON_MODE")
    print("\n按 Ctrl+C 退出。\n")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
