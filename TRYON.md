# 虚拟试穿（AI 上身效果图）功能说明

本仓库基于 [Anyesh/wardrowbe](https://github.com/Anyesh/wardrowbe)（MIT 协议）fork，
在上游「衣橱管理 + AI 打标 + 每日搭配推荐」之外，新增了一个功能：

> 上传一张自己的正面照 + 一件衣服（可从衣橱里挑），由**你自己的中转站图像模型**
> 生成「你穿上这件衣服」的合成图，不限次数、按张计费、不依赖任何云端订阅。

## 为什么要单独加

- 上游开源版**不含**虚拟试穿；该功能只在官方 Cloud 版跑在 GPU 服务器上，且会员每月限量。
- 本功能把出图能力做成**可配置的中转站调用**，与衣橱原本的打标/推荐 AI 完全分开：
  两套 base_url / key / 模型互不干扰，你可以让打标继续用本地 Ollama，出图走中转站。
- 不改动任何数据库结构（不需要 alembic 迁移），不写磁盘、不依赖新服务，
  只在后端加了一个路由、一个服务层，前端加了一个页面。

## 三条使用路线

### 路线 A：只想马上看效果（零依赖，推荐先跑这个）

不需要 Docker、不需要数据库、不需要 Node，只要有 Python 3.10+：

```bash
cp tryon/.env.example tryon/.env      # 填好 4 项，见下
python tryon/serve.py                  # Windows 用 python
```

浏览器打开 http://127.0.0.1:8787 ，传两张图即可出图、下载。
`tryon/.env` 已被 .gitignore 忽略，密钥不会进仓库。

### 路线 B：完整系统里用（Docker Compose）

在部署目录的 `.env` 里加：

```ini
TRYON_ENABLED=true
TRYON_BASE_URL=https://你的中转站/v1
TRYON_API_KEY=sk-...
TRYON_MODEL=图像模型名
TRYON_MODE=chat          # 或 images
```

然后照常 `docker compose up -d --build`（或 pull 后重启 backend）。
侧边栏会出现「虚拟试穿」入口；也可直接调 API：

```bash
# 查看配置状态（不返回密钥）
curl http://localhost:8000/api/v1/tryon/status

# 生成（两张图用 base64）
curl -X POST http://localhost:8000/api/v1/tryon/generate \
  -H "Authorization: Bearer <你的JWT>" -H "Content-Type: application/json" \
  -d '{"person_image":"<base64>","garment_image":"<base64>"}'
```

`docker-compose.yml` / `docker-compose.prod.yml` 的共享环境块已加好 `TRYON_*` 透传。
交互式 API 文档在开发模式下见 `http://localhost:8000/docs`。

### 路线 C：用 API / MCP / 脚本自己驱动

后端是 FastAPI，`/openapi.json`、`/docs`、`/redoc` 全都有；
`/api/v1/tryon/generate` 就是一个普通 REST 端点，可被任何脚本、Agent 调用。
生成图以 base64 返回，调用方自行决定存哪、怎么展示。

## 提升「人脸 / 身材」还原度的用法

打开「虚拟试穿」页后：

- **多张本人照片（1–6 张）**：正面 + 侧面 + 半身，多角度、光线均匀、脸清晰。
  多张会一起送进模型互相印证，明显改善「生成的脸不像本人」。第一张是主图。
- **身材信息（可选）**：身高 / 体重 / 三围 / 体型 / 其他特征，会写进指令帮助还原体型与衣服合身度。
  注意这是文字提示，**以照片为准**，不会真的按厘米建模。
- **锁定人脸**：默认开启，向 gpt-image 传 `input_fidelity=high`。
- **身份锁定指令**：内置一段强调「不要换脸、保持五官/脸型/发型/肤色」的中文指令，并会按你上传的照片张数
  自动说明图片顺序（第 1~N 张是同一个人、最后一张是衣服）。

> 说明：当前图像生成模型对「像素级还原同一张脸」仍有固有局限，以上是能明显改善、但不能 100% 保证一致的手段。
> 若仍偏差大，优先补更清晰、多角度、同一光线下的正面照，或换 `gpt-image-2.6-sunburst`（细节一致性更好）。

## 配置项

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `TRYON_ENABLED` | 总开关 | `false` |
| `TRYON_BASE_URL` | 中转站地址（一般带 `/v1`） | 空 |
| `TRYON_API_KEY` | 中转站密钥 | 空 |
| `TRYON_MODEL` | 支持出图的模型名 | 空 |
| `TRYON_MODE` | `chat`（多模态图进图出）/ `images`（`/images/generations` JSON）/ `edits`（`/images/edits` multipart 两图，gpt-image-2.x **推荐**，2.6-sunburst 实测通过） | `edits` |
| `TRYON_ENDPOINT` | 路径不标准时填完整路径或 URL | 空 |
| `TRYON_TIMEOUT` | 单次请求超时（秒） | `180` |
| `TRYON_PROMPT` | 自定义指令，留空用内置中文默认 | 空 |
| `TRYON_ALLOW_ENV_PROMPT` | 是否允许前端覆盖指令 | `true` |

三种形态覆盖绝大多数 OpenAI 兼容中转：

- `edits`：**`POST {base}/images/edits`**，multipart 里重复 `image[]` 传两张参考图，
  是 gpt-image-2.x 官方图生图接口。已在 `gpt-image-2.6-sunburst` 上实测两图出图成功
  （1254×1254 PNG）。**虚拟试穿优先用这个**。
- `images`：`POST {base}/images/generations`，JSON 里 `image[]` 传参考图。
- `chat`：`POST {base}/chat/completions`，两张图以 `image_url` 传入，
  返回 markdown data URI 图片，适合 gemini-image / nano-banana 一类「对话式出图」模型。

> 中转站的模型白名单各不相同，换 key 后用 `GET {base}/models` 看实际开放的模型名
> （本次三个 key 分别开放 1 / 7 / 372 个模型，只有第三个含 `gpt-image-2.6-sunburst`）。

响应里的图片做了兼容解析：`b64_json` / `data:` URI / `inline_data` /
markdown 图片 / 远程图片 URL 都能取到，尽量适配不同中转的返回格式。

## 隐私与成本（务必看清）

- **唯一新增的出站流量**：把「本人照片 + 衣服照片」以 base64 POST 到
  `TRYON_BASE_URL` 指定的中转站。照片不会发给官方，也不会存到本项目磁盘。
  指向谁、留存多久，取决于你选的中转站——这一点由你在配置里完全决定。
- **费用**：按张计费，取决于中转站和模型（通常几美分到几毛人民币一张），
  没有 50 次/月这类上限。可以先只跑一张确认效果与单价。
- **默认关闭**：不配 `TRYON_*` 时功能完全不启用，其余衣橱功能不受任何影响。
- 前端页面有「未配置」提示；`/tryon/status` 也不返回密钥。

## 输入照片建议

- 本人照：1–6 张，正面 + 侧面 + 半身，光线均匀、背景干净、脸清晰、姿势简单。
- 衣服照：平铺或挂拍的单件，图案完整；别用旅游合影里抠出来的一件。
- 效果主要看输入质量，模型只是次要因素。

## 天气默认坐标（消除「Location not set」报错）

新用户没设位置时，天气接口默认返回 400，首页会提示设置位置。加了可选的服务端默认坐标
`DEFAULT_WEATHER_LAT` / `DEFAULT_WEATHER_LON`（`docker-compose.preview.yml` 默认设为西安）：
不配则保持上游行为，配了则未设位置的用户天气正常显示、不再报错。用户随时可在设置里改成自己的位置。

## 本次改动清单

新增：
- `backend/app/schemas/tryon.py`、`backend/app/services/tryon_service.py`、`backend/app/api/tryon.py`
- `frontend/app/dashboard/tryon/page.tsx`
- `tryon/core.py`（出站调用核心，纯标准库）、`tryon/serve.py`（本地预览）、`tryon/.env.example`
- 8 种语言 `frontend/messages/*/nav.json` 的 `tryOn` 文案

修改：
- `backend/app/config.py`（`TRYON_*` 设置）、`backend/app/api/router.py`（挂载路由）
- `frontend/components/sidebar.tsx` / `mobile-sidebar.tsx` / `mobile-nav.tsx`（导航入口）
- `.env.example`、`docker-compose.yml`、`docker-compose.prod.yml`（变量与透传）

## 上游同步

`upstream` 指向 https://github.com/Anyesh/wardrowbe.git ，
功能改动集中在独立文件，后续可用 `git fetch upstream && git merge upstream/main` 跟上游更新，
冲突面很小。上游若自带了虚拟试穿，可随时删掉本分支。

## 协议

沿用上游 MIT。本功能新增代码同样可自由使用/修改。
