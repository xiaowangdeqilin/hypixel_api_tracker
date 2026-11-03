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
| packs | `/v2/resources/packs` | `deployId`、`packFormat`、最大格式的 `hash`/`url`；另见资源包文件比对 |

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
| `site/data.json` | 同源结构化数据（含 `version_info`、`data_written`） |
| `data/snapshots/*.json` | 各端点快照，供下次运行做 diff |
| `data/changes.json` | 字段变更日志（最多 500 条） |
| `data/runtime.json` | 版本一致性状态与版本切换历史 |
| `data/pack_files.json` | 资源包内文件哈希清单（基线） |
| `data/pack_texts.json` | 资源包内文本类文件内容缓存（用于展示变化片段） |
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

`VERSION` 文件（当前 `1.2.0`）是**仪表盘自身版本**，作为次要信息显示在版本条右侧。
改版本只需改这一个文件。

## 时间：统一北京时间（UTC+8）

所有写入与展示的时间都是北京时间，格式 `YYYY-MM-DD HH:MM:SS`。
`app/timeutil.py` 是唯一实现：

- `now_beijing()` 生成新数据的时间；
- `format_time()` 把历史数据里的 UTC 字符串（`...Z` 或无时区后缀）换算成北京时间；
  已经是北京时间的新格式则原样返回。

页面上的抓取时间、变更时间、生成时间、版本确认时间、资源包比对时间都经过它处理，
表头也明确标注「（北京时间）」。

## 变更展示：代码审核（diff）风格 + 只显示变化片段

「最近变更」不用普通表格，而是类似 git diff / code review 的区块：

- **新增**：只标 **绿色 `+`**，前面不会出现红色删除行
- **删除**：只标 **红色 `-`**
- **修改**：**只显示发生变化的那一段**——把原值 / 新值整理成文本后逐行比对，
  删除行标红、新增行标绿，未变化的内容完全不输出（最多 8 个片段、每片 40 行，
  前后各留 2 行上下文便于定位）

长值可点「查看完整值」展开，路径、端点、时间显示在区块头部。
本地 FastAPI 页面用同一套规则（前端用 LCS 逐行比对，见 `static/app.js`）。

## 资源包文件比对

`scripts/pack_inspector.py` 负责下载并拆解 Hypixel 资源包：

- **每次调用接口都取 `packFormat` 数值最大的版本**。该数字会随 Minecraft / Hypixel
  更新而变大（当前为 97），代码里不写死任何具体数字，页面会显示本次候选格式与选取结果
- 用官方提供的 sha1 `hash` 做缓存键：哈希未变化就直接复用上次清单，**不重复下载**
- 哈希变化时下载 zip、校验 sha1、用 `zipfile` 解压，对包内每个文件计算 sha256（前 16 位）
- 同时缓存**文本类文件**的内容（扩展名白名单、单文件 ≤128 KB、总量 ≤2 MB；
  实测 3103 个 / 约 0.42 MB），二进制文件只留哈希
- 与上次清单比对，输出新增 / 修改 / 删除：
  - 新增文件**只标绿**、删除文件**只标红**
  - 被修改的文本文件**只展示变化的那一段**（删除行标红、新增行标绿）
  - 二进制文件只说明哈希变化
- **packFormat 号变化不会被误报成全量增删**：`diff_packs()` 用固定键
  （`SkyBlock.packFormat` / `.hash` / `.url`）比对最大格式，格式号升级只体现为一条修改
- 首次运行只建立基线，不会把包内全部文件误报为「新增」

结果展示在「资源包文件变更」面板，并持久化到 `data/pack_changes.json`。

想手动排查资源包，可直接运行（不会污染线上数据之外的东西）：

```bash
python scripts/pack_inspector.py              # 检查并写入清单
python scripts/pack_inspector.py --force      # 忽略哈希缓存强制重新下载
```

## 接口异常：整次运行不做任何变化

Hypixel 接口偶尔会宕机、限流或返回不完整数据。为避免「半次更新」污染基线，
`build_dashboard.main()` 采用两阶段事务式写入：

1. **抓取 + 比对阶段完全不落盘**（资源包检查器也只返回待写数据，不自己写文件）
2. 只有满足全部条件才在最后一次性写入 `data/`：
   - 5 个接口全部成功拿到数据；
   - 资源包检查结果为 `baseline` / `updated` / `unchanged`
     （即没有 `error` / `unavailable`）
3. 任一环节失败（接口超时、非 200、下载失败、sha1 校验不符、zip 损坏、无可用下载地址），
   **本次运行不改动 `data/` 下的任何一个字节**，页面沿用上一次成功结果并在顶部标注原因

脚本通过 `$GITHUB_OUTPUT` 输出 `data_written=true|false`，工作流据此判断：
为 `false` 时跳过提交、跳过上传产物与部署，因此**连页面都不会变**。

> 取舍：宁可这次不更新，也不要把「接口没数据」误记成「内容被全部删除」。
> 定时任务每 10 分钟一次，接口恢复后下一次运行会自动补上。

## GitHub Actions 与 Pages

`.github/workflows/pages.yml` 每 10 分钟运行一次（也可手动 `workflow_dispatch`）：

1. 抓取 5 个端点、做 diff、确认版本、检查资源包
2. 仅在 `data_written=true` 时 `git add data/` 提交回仓库（`[skip ci]` 避免循环触发）
3. 仅在 `data_written=true` 时上传 `site/` 产物并部署到 GitHub Pages

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
> 只比较商品 id 集合，避免价格与版本变化刷屏。变更表格同样按「新增纯绿、
> 删除纯红、修改只显示变化片段」渲染。

## 项目结构

```
.github/workflows/pages.yml   # 定时抓取 + 部署 Pages（node24 / ubuntu-24.04）
VERSION                       # 仪表盘自身版本号
requirements.txt              # 本地 FastAPI 依赖（静态构建只需 requests）
.env.example                  # 可选的 HYPIXEL_API_KEY 示例
scripts/
  build_dashboard.py           # 核心：抓取、版本确认、diff、事务式落盘、生成页面
  pack_inspector.py            # 资源包选最大格式 / 下载 / 解压 / 片段级 diff
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
  pack_texts.json              # 资源包文本文件缓存（用于变化片段）
  pack_changes.json            # 资源包文件变更记录
site/                          # 生成的静态页面（被 .gitignore 忽略）
```

## 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| 页面显示「版本同步中」 | 正常：两个资源接口正在分批更新，等它们一致即可，当前版本不会乱跳 |
| 首次运行资源包显示「基线」 | 正常：只建立文件清单，不报新增；等 Hypixel 更新资源包后才列出文件差异 |
| 页面顶部提示「未获取到完整数据」 | 接口或资源包下载失败，**本次不改动任何数据**，沿用上次结果；下次运行会自动重试 |
| 资源包 `packFormat` 变了 | 正常：数字随 MC 更新变化，脚本每次都取最大值，只记为一条修改而非全量增删 |
| 打开线上页面仍是旧版 | CDN 缓存，等几分钟刷新；定时任务每 10 分钟也会重跑 |
| `pip install -r requirements.txt` 失败 | 静态生成不需要它，只装 `requests` 即可 |
| 抓取报 429 / 超时 | 设置 `HYPIXEL_API_KEY`，或稍后重试；脚本会整次跳过，不会写入半份数据 |
