# Hypixel SkyBlock API Tracker

一个定时监控 Hypixel SkyBlock 公共 API 变更的工具，自动生成 GitHub Pages 仪表盘。

监控的 5 个端点：

- `https://api.hypixel.net/v2/resources/skyblock/collections`
- `https://api.hypixel.net/v2/resources/skyblock/skills`
- `https://api.hypixel.net/v2/resources/skyblock/items`
- `https://api.hypixel.net/v2/skyblock/bazaar`
- `https://api.hypixel.net/v2/resources/packs`

## 工作原理

GitHub Actions 定时（每 10 分钟）运行 `scripts/build_dashboard.py`：

1. 抓取 5 个端点的 JSON 数据（含 `packs` 资源包列表）
2. 读取资源 API（skills）响应顶层的 `version`，作为页面顶部「当前版本」；版本变化会记为一条「版本更新」变更
3. 与上一次提交的快照（`data/snapshots/*.json`）做 diff，检测 **新增 / 删除 / 修改** 的字段
4. 检查资源包官方哈希：变化时下载 zip、校验 sha1、解压并按文件内容哈希比对，得到包内文件的新增 / 修改 / 删除
5. 变更记录追加到 `data/changes.json`；资源包清单与文件变更写入 `data/pack_files.json`、`data/pack_changes.json`
6. 生成静态仪表盘 `site/index.html`，把数据提交回仓库并部署 `site/` 到 GitHub Pages

### 变更检测策略

| 端点 | 检测内容 |
|------|----------|
| collections | 分类增删、分类内物品增删、字段修改 |
| skills | 技能增删、字段修改、顶层 `version` 版本变化 |
| items | 物品增删（按 `id` 匹配）、字段修改 |
| bazaar | **仅结构性变更**（产品增删），忽略每秒波动的价格 |
| packs | 资源包 `deployId`、各 packFormat 的 `hash` / `url` 变化；另见下方文件比对 |

## 版本号

页面顶部「当前版本」显示的是 **Hypixel 资源数据版本**，取自
`https://api.hypixel.net/v2/resources/skyblock/skills` 响应顶层的 `version`
字段（items 端点目前没有该字段，仅作回退）。版本变化会被记录为一条「版本更新」
变更，并写入 `data/runtime.json`，本地 FastAPI 页面也读取它。

`VERSION` 文件（当前 `1.0.0`）是**仪表盘自身版本**，作为次要信息显示在版本条中。
两处页面都读取它，升级时只需修改这一个文件。

## 资源包文件比对

`scripts/pack_inspector.py` 负责下载并拆解 Hypixel 资源包：

- 从 packs 响应里选出 **packFormat 最高** 的 zip（实测约 18 MB / 4756 个文件）
- 用官方提供的 sha1 `hash` 做缓存键：哈希未变化就直接复用上次清单，**不重复下载**
- 哈希变化时下载 zip、校验 sha1、用 `zipfile` 解压，对包内每个文件计算 sha256（取前 16 位）
- 与 `data/pack_files.json` 里的上次清单比对，输出新增 / 修改 / 删除的文件路径
- 首次运行只建立基线，不会把包内全部文件误报为「新增」

结果展示在页面的「资源包文件变更」面板中，并持久化到 `data/pack_changes.json`。

## 本地运行

```bash
pip install requests
python scripts/build_dashboard.py
```

运行后会生成：

- `data/snapshots/*.json` — 各端点快照
- `data/changes.json` — 字段变更日志
- `data/runtime.json` — 当前 Hypixel 资源数据版本
- `data/pack_files.json` — 资源包内文件哈希清单（基线）
- `data/pack_changes.json` — 资源包文件新增 / 修改 / 删除记录
- `site/index.html` — 仪表盘页面
- `site/data.json` — 仪表盘结构化数据

## 部署到 GitHub Pages

1. 推送代码到 GitHub
2. 在仓库 **Settings → Pages** 中，将 Source 设为 **GitHub Actions**
3. （可选）在 **Settings → Secrets and variables → Actions** 添加 `HYPIXEL_API_KEY`
4. workflow 会自动定时运行并部署

也可以在 **Actions** 页面手动触发 `Hypixel API Tracker` workflow。

## 项目结构

```
.github/workflows/pages.yml   # 定时抓取 + 部署 Pages
VERSION                       # 仪表盘自身版本号
scripts/build_dashboard.py    # 核心：抓取、diff、生成页面
scripts/pack_inspector.py     # 资源包下载 / 解压 / 文件比对
data/
  snapshots/                  # 持久化快照（提交到仓库）
    collections.json
    skills.json
    items.json
    bazaar.json
    packs.json
  changes.json                # 字段变更日志
  runtime.json                # 当前资源数据版本
  pack_files.json             # 资源包文件哈希清单
  pack_changes.json           # 资源包文件变更记录
app/                          # 可选：本地 FastAPI 实时仪表盘
  config.py
  db.py
  diff.py
  fetcher.py
  scheduler.py
  main.py
templates/                    # FastAPI 模板
static/                       # FastAPI 静态资源
requirements.txt              # FastAPI 依赖（可选）
```

## 可选：本地 FastAPI 仪表盘

如果想在本地实时查看（非 Pages 方式），可以运行 FastAPI 服务：

```bash
pip install -r requirements.txt
python -c "from app.db import init_db; init_db()"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

访问 http://localhost:8000/
