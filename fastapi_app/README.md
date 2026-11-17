# hypixel-tracker-api

只读的 FastAPI 服务，把 [hypixel_api_tracker](https://github.com/xiaowangdeqilin/hypixel_api_tracker)
发布在 GitHub Pages 上的 `site/data.json` 转成 REST API 和一个轻量网页。不需要安装
Rust 工具链，装完 Python 包即可运行。

- 发行包：`hypixel-tracker-api`（导入名 `hypixel_tracker_api`）
- 版本：3.0.0，Python 3.9+
- 默认数据源：`https://raw.githubusercontent.com/xiaowangdeqilin/hypixel_api_tracker/main/site/data.json`
- 默认监听：`http://127.0.0.1:8000`

## 安装

### 从本仓库源码安装

```bash
cd fastapi_app
python3 -m venv .venv
.venv/bin/pip install .          # 开发时用 -e '.[test]'
```

### 从 wheel 安装

Release 页面会附带 `hypixel_tracker_api-3.0.0-py3-none-any.whl`：

```bash
python3 -m pip install --upgrade hypixel_tracker_api-3.0.0-py3-none-any.whl
```

### 各系统备注

- **Windows**：用 `py -m venv .venv`、`.venv\Scripts\pip install .`，启动命令为
  `.venv\Scripts\hypixel-tracker-api.exe`。
- **macOS / Linux**：若 `python3 -m pip install .` 报 `externally-managed-environment`，
  请先建虚拟环境，或加 `--user --break-system-packages`（不推荐）。
- **Termux / 容器**：`uvicorn[standard]` 会带上 `uvloop` 等可选依赖；若某个依赖编译失败，
  可先 `pip install . --no-deps`，再单独安装 `fastapi httpx jinja2 uvicorn`。

## 运行

```bash
hypixel-tracker-api                       # 等同 serve，127.0.0.1:8000
hypixel-tracker-api serve --host 0.0.0.0 --port 9000
hypixel-tracker-api serve --reload        # 开发模式
python3 -m hypixel_tracker_api            # 与上面等价
```

打开 <http://127.0.0.1:8000/> 即为网页；API 都在 `/api/*` 下，浏览器可直接访问。

### 命令行选项

| 命令 | 说明 |
| --- | --- |
| `hypixel-tracker-api` / `serve` | 启动服务，`--host` 默认 `127.0.0.1`，`--port` 默认 `8000`，`--reload` 开发热重载 |
| `--version` | 打印版本 |
| `--check-update` | 查询 GitHub Releases 是否有新版本，并给出 `pip` 升级命令 |
| `--print-crash ID` | 打印指定崩溃报告全文 |
| `--clear-cache` | 删除数据与语言包缓存，保留崩溃报告 |
| `-h` / `--help` | 帮助；按 `LANG` / `LC_ALL` 输出中、英、日、德、西、法、俄、葡八种语言 |

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HYPIXEL_DATA_URL` | raw.githubusercontent.com 上的 `site/data.json` | 主数据源 |
| `HYPIXEL_DATA_FILE` | 空 | 指向本地 `data.json`，存在时优先于网络（便携包模式） |
| `HYPIXEL_FALLBACK_URL` | jsDelivr 镜像 | 主源失败后的备用源 |
| `HYPIXEL_LOCALE_URL` | raw.githubusercontent.com 上的 `locales/{code}.json` | 语言包主源 |
| `HYPIXEL_LOCALE_FALLBACK_URL` | jsDelivr 镜像 | 语言包备用源（主源不可达时） |
| `HYPIXEL_CACHE_DIR` | `~/.cache/hypixel-tracker-api` | 缓存与崩溃报告根目录 |
| `HYPIXEL_DATA_TTL` | `300` | 数据缓存有效期（秒） |
| `HYPIXEL_LOCALE_TTL` | `86400` | 语言包缓存有效期（秒） |
| `HYPIXEL_HTTP_TIMEOUT` | `10` | 单次 HTTP 超时（秒），失败重试一次 |

## HTTP 接口

所有 `/api/*` 响应带 `Cache-Control: no-store`，并允许跨域（`*`）。时间字段一律是
**北京时间**字符串 `YYYY-MM-DD HH:MM:SS`，服务端不换算，网页由浏览器按所选时区展示。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 网页：版本条、7 个端点卡片、最近变更（diff 着色）、语言与时区选择 |
| GET | `/api/health` | `{status, version, data_age_seconds, stale}`；无数据时 `status=unavailable` |
| GET | `/api/version` | `{app, version, dashboard_version, build, generated_at, time_basis, repo}` |
| GET | `/api/summary` | 各端点的 `{endpoint, status, summary, fetched_at}` 与整体 `stale` |
| GET | `/api/changes?limit=&endpoint=` | 变更数组，`limit` 默认 200、夹在 1..2000 |
| GET | `/api/data/{name}` | 白名单数据；`collections/skills/items/bazaar/packs/bingo/firesales` 取自 `endpoints`，其余为顶层键 |
| GET | `/api/languages` | `[{code, name}]`，内置语言表 |
| GET | `/api/i18n/{lang}` | `{lang, dir, fallback, pack}`；`zh-CN` 返回空包表示原文 |
| GET | `/api/crash/recent?limit=` | 最近崩溃报告摘要（最多 50 条） |
| GET | `/api/update/check` | `{current, latest, available, page}`，结果缓存 10 分钟 |

`/api/data/{name}` 允许的名字：`collections` `skills` `items` `bazaar` `packs` `bingo`
`firesales` `pack` `pack_changes` `version_info` `endpoints`；其他名字返回 404。

## 数据降级与离线

1. 内存缓存未过期（`HYPIXEL_DATA_TTL`）时直接返回。
2. 过期后请求主源；失败会换 jsDelivr 镜像，每次请求超时 10 秒并重试一次。
3. 网络全挂时读取磁盘缓存，并在 `/api/health`、`/api/summary` 和网页中标
   `stale: true`。
4. 连缓存都没有时返回 HTTP 503 与 `{"error": "data unavailable", ...}`，不会把异常栈
   抛给客户端。

语言包有同样的磁盘缓存与回退链：远端 → 磁盘 → 包内 `en.json`（`zh-CN` 直接显示原文）。

## 崩溃报告

未捕获异常会写入：

```
~/.cache/hypixel-tracker-api/crash/crash-YYYYmmdd-HHMMSS.json
```

（Windows 为 `%USERPROFILE%\.cache\hypixel-tracker-api\crash\`，设置了
`HYPIXEL_CACHE_DIR` 时以该目录为准。）最多保留 20 份，自动清理最旧的。

客户端只收到：

```json
{"error": "internal server error", "crash_report": "20261117-203845", "request_id": "..."}
```

排查步骤：

1. 用响应里的 id 打印全文：`hypixel-tracker-api --print-crash 20261117-203845`。
2. 报告包含 `version / python / platform / path / method / message / traceback /
   request_id`，提 issue 时请附上（注意其中可能有本机路径）。
3. 也可以用 `GET /api/crash/recent` 看最近摘要。

## 更新

```bash
hypixel-tracker-api --check-update
python3 -m pip install --upgrade hypixel-tracker-api
```

程序不会在运行时静默替换自己的文件；升级交给 `pip`，避免包管理状态损坏。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| 页面提示「缓存数据，可能过期」 | 上游暂时不可达，服务在用磁盘缓存；稍后刷新或检查网络 |
| 所有接口 503 | 从未成功拉取过数据；确认能访问 `raw.githubusercontent.com` 或设置 `HYPIXEL_DATA_URL` |
| 端口被占用 | `--port` 换一个，或查出占用进程后释放 |
| 时区没有换算 | 时间换算依赖 JavaScript；关闭 JS 时显示北京时间原值 |
| 语言显示英文 | 该语言包远端不可达且无磁盘缓存，按设计回退英文 |

## 测试与风格

```bash
cd fastapi_app
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

测试全部使用注入的假数据源，不会访问网络。
