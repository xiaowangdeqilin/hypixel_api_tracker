# Hypixel SkyBlock API Tracker

定时监控 Hypixel SkyBlock 公共 API 的变化，自动生成中文 GitHub Pages 仪表盘；
另附一个可选的本地 FastAPI 实时仪表盘。

**在线仪表盘**：https://xiaowangdeqilin.github.io/hypixel_api_tracker/

监控的 5 个端点：

| 端点 | 地址 | 追踪内容 |
|------|------|----------|
| collections | `/v2/resources/skyblock/collections` | 分类 / 物品增删、字段修改（带 `version`） |
| skills | `/v2/resources/skyblock/skills` | 技能增删、字段修改（带 `version`） |
| items | `/v2/resources/skyblock/items` | 物品增删（按 `id` 匹配）、字段修改（无 `version`） |
| bazaar | `/v2/skyblock/bazaar` | **仅商品结构性增删**，忽略每秒波动的价格 |
| packs | `/v2/resources/packs` | `deployId`、各 packFormat 的 `hash`/`url`；另见资源包文件比对 |

（域名均为 `https://api.hypixel.net`。）

## 快速开始（下载后即可运行）

只需要 **Python 3.10+** 和 `requests`，所有端点都是公开的，**不需要 API Key**。

```bash
git clone https://github.com/xiaowangdeqilin/hypixel_api_tracker.git
cd hypixel_api_tracker
pip install requests
python scripts/build_dashboard.py
```

运行完会生成 `site/index.html`，直接用浏览器打开即可（也可 `python -m http.server -d site 8000`）。
首次运行会抓取 5 个端点、建立资源包文件基线，耗时约 10–60 秒，取决于网络与资源包下载。
生成物如下：

| 文件 | 内容 |
|------|------|
| `site/index.html` | 自包含的中文仪表盘页面 |
| `site/data.json` | 同源结构化数据（含 `version_info`） |
| `data/snapshots/*.json` | 各端点快照，供下次运行做 diff |
| `data/changes.json` | 字段变更日志（最多 500 条） |
| `data/runtime.json` | 版本一致性状态与版本切换历史 |
| `data/pack_files.json` | 资源包内文件哈希清单（基线） |
| `data/pack_changes.json` | 资源包文件新增 / 修改 / 删除记录 |

> `HYPIXEL_API_KEY` 完全可选。若遇到限流，可 `export HYPIXEL_API_KEY=你的key`，
> 脚本与本地服务都会自动带上 `?key=`。仓库不依赖 `.env` 自动加载。

## 版本号：双端点一致性确认

`collections` 与 `skills` 两个资源接口的响应顶层都带 `version` 字段（`items` 没有）。
实测两者当前均为 `0.27.2.22`，`lastUpdated` 只差几十毫秒。但 Hypixel 分批更新时
**两个接口不一定同时切换版本**，如果直接取其中一个，页面版本号就会来回跳（打架）。

`resolve_version_state()`（`scripts/build_dashboard.py`）采用一致性确认：

| 情况 | 状态 | 页面当前版本 |
|------|------|--------------|
| 两接口版本相同 | `aligned` 版本一致 | 该版本（确认切换） |
| 两接口版本不同 | `syncing` 版本同步中 | **保持上一次稳定版本不变**，并分别列出两者版本 |
| 只有部分接口返回版本 | `partial` 部分接口未返回版本 | 保持上一次稳定版本 |

只有从「一致」切换到另一个「一致」时才会确认新版本，并在「版本记录」面板留一条记录
（含北京时间与上一版本）；不同步的中间态不会切换、也不会产生版本变更记录。

**版本号变化不会被当成端点内容更新**：`version` / `lastUpdated` / `success`
三个顶层元数据字段在写快照与做 diff 前统一由 `strip_meta()` 剥离，
因此版本更新不会在「最近变更」里制造一堆 `modified` 记录。
本地 FastAPI 侧的 `app/fetcher.py` 用同样的规则处理。

`VERSION` 文件（当前 `1.1.0`）是**仪表盘自身版本**，作为次要信息显示在版本条右侧。
改版本只需改这一个文件。

## 时间：统一北京时间（UTC+8）

所有写入与展示的时间都是北京时间，格式 `YYYY-MM-DD HH:MM:SS`。
`app/timeutil.py` 是唯一实现：

- `now_beijing()` 生成新数据的时间；
- `format_time()` 把历史数据里的 UTC 字符串（`...Z` 或无时区后缀）换算成北京时间；
  已经是北京时间的新格式则原样返回。

页面上的抓取时间、变更时间、生成时间、版本确认时间、资源包比对时间都经过它处理，
表头也明确标注「（北京时间）」。

## 变更展示：代码审核（diff）风格

「最近变更」不再用普通表格，而是类似 git diff / code review 的区块：

- **绿色 `+`** = 新增（值／文件）
- **红色 `-`** = 删除（值／文件）
- **修改** = 先红 `-` 显示原值、再绿 `+` 显示新值
- 长值可点「查看完整值」展开，路径、端点、时间显示在区块头部

资源包文件变更面板也沿用同一套配色。

## 资源包文件比对

`scripts/pack_inspector.py` 负责下载并拆解 Hypixel 资源包：

- 从 `packs` 响应里选出 **packFormat 最高**的 zip（实测约 18 MB / 4756 个文件）
- 用官方提供的 sha1 `hash` 做缓存键：哈希未变化就直接复用上次清单，**不重复下载**
- 哈希变化时下载 zip、校验 sha1、用 `zipfile` 解压，对包内每个文件计算 sha256（前 16 位）
- 与 `data/pack_files.json` 的上次清单比对，输出新增 / 修改 / 删除的文件路径
- 首次运行只建立基线，不会把包内全部文件误报为「新增」

结果展示在「资源包文件变更」面板，并持久化到 `data/pack_changes.json`。

## GitHub Actions 与 Pages

`.github/workflows/pages.yml` 每 10 分钟运行一次（也可手动 `workflow_dispatch`）：

1. 抓取 5 个端点、做 diff、确认版本、检查资源包
2. `git add data/` 把快照与状态提交回仓库（`[skip ci]` 避免循环触发）
3. 上传 `site/` 产物并部署到 GitHub Pages

**部署到自己的仓库**：

1. Fork / 推送代码到 GitHub
2. 仓库 **Settings → Pages** 把 Source 设为 **GitHub Actions**
3. 到 **Actions** 页手动触发 `Hypixel API Tracker` workflow 验证一次
4. 成功后打开 `https://<用户名>.github.io/<仓库名>/`

工作流已固定 `runs-on: ubuntu-24.04`（避开 `ubuntu-latest` 迁移到 Ubuntu 26 的通知），
并使用运行于 **Node.js 24** 的 action 版本，不会产生 Node.js 20 弃用警告：

| Action | 版本 | 运行时 |
|--------|------|--------|
| `actions/checkout` | `@v5` | node24 |
| `actions/setup-python` | `@v6` | node24 |
| `actions/upload-pages-artifact` | `@v5` | composite（内部 upload-artifact 为 node24） |
| `actions/deploy-pages` | `@v5` | node24 |

## 可选：本地 FastAPI 实时仪表盘

静态 Pages 之外，仓库还附带一个本地实时服务（SQLite + 后台定时抓取）：

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 http://localhost:8000/ 。服务启动时会在后台立即抓取一轮，之后按
`app/config.py` 里的间隔轮询，页面每 30 秒自动刷新。
`init_db()` 在导入 `app.main` 时自动执行，无需手动初始化；数据库文件
`data/hypixel_monitor.db` 已被 `.gitignore` 忽略。

可用接口：`/api/status`、`/api/history`、`/api/changes`、`/api/snapshot/{id}`、`/health`。

> 本地服务的抓取同样会剥离 `version` / `lastUpdated` / `success`，并对集市
> 只比较商品 id 集合，避免价格与版本变化刷屏。

## 项目结构

```
.github/workflows/pages.yml   # 定时抓取 + 部署 Pages（node24 / ubuntu-24.04）
VERSION                       # 仪表盘自身版本号
requirements.txt              # 本地 FastAPI 依赖（静态构建只需 requests）
.env.example                  # 可选的 HYPIXEL_API_KEY 示例
scripts/
  build_dashboard.py           # 核心：抓取、版本一致性确认、diff、生成页面
  pack_inspector.py            # 资源包下载 / 解压 / 文件比对
app/                          # 本地 FastAPI 服务
  timeutil.py                 # 北京时间统一处理（脚本与服务共用）
  config.py                   # 端点配置、版本状态读取
  db.py / diff.py / fetcher.py
  scheduler.py / main.py
templates/index.html          # FastAPI 页面模板
static/                        # FastAPI 页面静态资源（app.js / styles.css）
data/
  snapshots/                  # 持久化快照（提交到仓库）
  changes.json                # 字段变更日志
  runtime.json                # 版本一致性状态 + 切换历史
  pack_files.json              # 资源包文件哈希清单
  pack_changes.json            # 资源包文件变更记录
site/                          # 生成的静态页面（被 .gitignore 忽略）
```

## 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| 页面显示「版本同步中」 | 正常：两个资源接口正在分批更新，等它们一致即可，当前版本不会乱跳 |
| 首次运行资源包显示「基线」 | 正常：只建立文件清单，不报新增；等 Hypixel 更新资源包后才列出文件差异 |
| 打开线上页面仍是旧版 | CDN 缓存，等几分钟刷新；定时任务每 10 分钟也会重跑 |
| `pip install -r requirements.txt` 失败 | 静态生成不需要它，只装 `requests` 即可 |
| 抓取报 429 / 超时 | 设置 `HYPIXEL_API_KEY`，或稍后重试；脚本会跳过失败端点继续生成页面 |
