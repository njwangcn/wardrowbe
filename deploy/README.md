# 免费试用 / 部署指南

你的 fork 其实是**两套东西**，能免费托管的方式完全不同，先分清想试哪一个：

| 你想试的 | 是什么 | 最快免费方案 | 需要 Docker？ |
| --- | --- | --- | --- |
| **上身合成图效果**（上传两张图出图） | 一个 API 调用，零依赖 | ① 本地 `tryon/start.bat`（30 秒）② GitHub Pages / HF 静态 Space ③ HF Gradio Space | 都不需要 |
| **整套衣橱系统**（管理、打标、搭配、试穿页面） | 6 容器全栈：Postgres + Redis + 后端 + 前端 + 2 worker | GitHub Codespaces（免费额度）或本地装 Docker Desktop | 需要 |

> 结论先讲：只想看「我穿上这件衣服」的效果，**别去部署整套系统**，用静态版或本地工具，几分钟就能出第一张图。

---

## 方案 A：静态版（最省事，纯免费、零政策风险）

`deploy/static/index.html` 是一个单文件网页，浏览器直接把两张图 POST 到你自己的中转站。
没有后端、没有数据库、不需要服务器、不会休眠。

托管方式任选其一（都是静态、永久免费、无需信用卡）：

- **GitHub Pages**：把 `deploy/static/` 里的内容推到仓库的 `docs/` 或 `gh-pages` 分支 → Settings → Pages 开启 → 得到 `https://njwangcn.github.io/wardrowbe/`。
- **Hugging Face Static Space**：新建 Space 时 SDK 选 **Static**，把这个 `index.html` 传上去即可。Static Spaces 对所有人免费，且不像 Gradio/Docker Space 那样需要 PRO。
- **直接双击打开**：本地用浏览器打开 `index.html` 也能用（file:// 下部分中转站 CORS 可能更严，若失败用上面两种托管）。

配置在页面里填（Base URL / Key / 模型 / 模式），点「保存到本浏览器」后只存在 localStorage，不上传、不入库。

⚠️ 唯一门槛：中转站要允许浏览器跨域（CORS）。若报 CORS 错误，改用方案 B 或本地 Python 工具（`tryon/start.bat` 走服务端转发，无跨域问题）。

---

## 方案 B：Hugging Face Space（Gradio，公开链接、带 UI）

`deploy/hf-space/` 4 个文件 → 新建 HF Space（SDK = **Gradio**）→ 上传 → Settings → Secrets 填 `TRYON_*` → 出公开链接。

- 优点：有 Gradio 界面、密钥藏在 Space Secrets 里、由服务端转发（无 CORS 问题）。
- 缺点/政策：2026 年起 HF 对「运行于 compute 的 Gradio/Docker Space」要求付费计划（个人 = PRO）；免费个人账号一般仍可用 ZeroGPU 免费额度托管最多 2 个 Gradio Space，但**以创建页面实际提示为准**。免费层还会空闲休眠（约 48h），首次打开有冷启动。
- 若创建时提示需要升级 PRO → 不要付费，改用方案 A 或 C。

详见 `deploy/hf-space/README.md`。

---

## 方案 C：GitHub Codespaces（跑**整套**系统，免费额度内）

`docker-compose.preview.yml` + `.devcontainer/devcontainer.json` 已经配好，是为免费 2 核环境裁剪过的轻量栈（只起 4 个容器，去掉 caddy / worker / image-worker，DEBUG=true 免密登录，前端用 dev 镜像免掉完整 Next.js 生产构建）。

步骤：

1. 打开 https://github.com/njwangcn/wardrowbe → 绿色 **Code** 按钮 → **Codespaces** → **Create codespace on main**。
2. 它会自动跑 `.devcontainer/devcontainer.json` 的 `postCreateCommand`（`deploy/codespaces/up.sh`），构建并起栈，首次约 3–8 分钟。
3. Codespaces 自动把 3000 端口转发并弹出预览 → 点 **Login** 直接免密进入（DEBUG=true）。
4. 想同时试虚拟试穿：在 GitHub → Settings → **Secrets and variables → Codespaces** 添加
   `TRYON_ENABLED=true` / `TRYON_BASE_URL` / `TRYON_API_KEY` / `TRYON_MODEL` / `TRYON_MODE`，
   然后重建 Codespace（或在终端重跑 `bash deploy/codespaces/up.sh`）。

- 免费额度：个人账号约 **60 小时/月**（2 核），纯试用完全够；不绑卡也能用免费额度。
- 记得用完在 Codespaces 列表里 **Stop**，别让小时数空跑。
- 停止后数据仍在（volume 保留），下次重启继续用。

**如果 `xxx-3000.app.github.dev` 打不开**（国内网络常见，域名解析被污染或超时）：在 Codespace 终端跑

```bash
bash deploy/codespaces/tunnel.sh
```

它会起一条免费的 Cloudflare 快速隧道（免注册），打印一个 `https://xxx.trycloudflare.com` 地址，
并自动把 `NEXTAUTH_URL` 换成这个地址、重启前端，直接用新地址登录即可。
隧道进程随实例存活，Codespace 重启后重跑一次就行；隧道地址每次会变，`tunnel.sh stop` 可关闭。

其他免费 PaaS（Render / Railway / Fly / Koyeb / Zeabur）**都不推荐**：要么免费层不支持 Docker、要么 512MB 内存塞不下 Postgres+Redis+前后端、要么要绑信用卡、要么 Postgres 免费库 30 天就过期、要么 15 分钟就休眠。这套是 6 容器全栈，不是一个能塞进免费 Web Service 的单进程应用。

---

## 方案 D：本机（最稳、零成本、照片不出本机）

装好 Docker Desktop 后：

```bash
cp .env.example .env        # 填 TRYON_* 与 TRYON_ENABLED=true
docker compose -f docker-compose.preview.yml up -d --build
docker compose -f docker-compose.preview.yml exec backend alembic upgrade head
```

打开 http://localhost:3000 。或只试生图：`python tryon/serve.py`（纯标准库，连 Docker 都不用）。

---

## 隐私与费用（三种在线方案都一样）

- 唯一出站：把「本人照片 + 衣服照片」POST 到 `TRYON_BASE_URL`。密钥只在你配置的地方（本机 .env / Space Secrets / 浏览器 localStorage）。
- 按张计费，取决于你的中转站，没有次数上限。先只跑一张确认单价与效果。
- **公开链接慎用**：GitHub Pages / 公开 HF Space 任何人都能打开，别在页面上预填你的密钥、别传不想公开的照片；用完删除 Space/仓库或设私有。
