# Hypixel SkyBlock API Tracker

一个定时监控 Hypixel SkyBlock 公共 API 变更的工具，自动生成 GitHub Pages 仪表盘。

监控的 4 个端点：

- `https://api.hypixel.net/v2/resources/skyblock/collections`
- `https://api.hypixel.net/v2/resources/skyblock/skills`
- `https://api.hypixel.net/v2/resources/skyblock/items`
- `https://api.hypixel.net/v2/skyblock/bazaar`

## 工作原理

GitHub Actions 定时（每 10 分钟）运行 `scripts/build_dashboard.py`：

1. 抓取 4 个端点的 JSON 数据
2. 与上一次提交的快照（`data/snapshots/*.json`）做 diff，检测 **新增 / 删除 / 修改** 的字段
3. 将变更记录追加到 `data/changes.json`
4. 生成静态仪表盘 `site/index.html`
5. 把更新后的快照和变更日志提交回仓库
6. 部署 `site/` 到 GitHub Pages

### 变更检测策略

| 端点 | 检测内容 |
|------|----------|
| collections | 分类增删、分类内物品增删、字段修改 |
| skills | 技能增删、字段修改 |
| items | 物品增删（按 `id` 匹配）、字段修改 |
| bazaar | **仅结构性变更**（产品增删），忽略每秒波动的价格 |

## 本地运行

```bash
pip install requests
python scripts/build_dashboard.py
```

运行后会生成：

- `data/snapshots/*.json` — 各端点快照
- `data/changes.json` — 变更日志
- `site/index.html` — 仪表盘页面

## 部署到 GitHub Pages

1. 推送代码到 GitHub
2. 在仓库 **Settings → Pages** 中，将 Source 设为 **GitHub Actions**
3. （可选）在 **Settings → Secrets and variables → Actions** 添加 `HYPIXEL_API_KEY`
4. workflow 会自动定时运行并部署

也可以在 **Actions** 页面手动触发 `Hypixel API Tracker` workflow。

## 项目结构

```
.github/workflows/pages.yml   # 定时抓取 + 部署 Pages
scripts/build_dashboard.py    # 核心：抓取、diff、生成页面
data/
  snapshots/                  # 持久化快照（提交到仓库）
    collections.json
    skills.json
    items.json
    bazaar.json
  changes.json                # 变更日志
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
