# Hypixel SkyBlock API Tracker

定时监控 Hypixel SkyBlock 公共 API 的变化，自动生成中文 GitHub Pages 仪表盘；
另附一个可选的本地实时仪表盘。

**v2.0 起整个项目使用 Rust 重写**：单一静态二进制、无额外运行时依赖、
数据全部为 JSON 文件，CI 用 cargo 缓存构建。

**在线仪表盘**：https://xiaowangdeqilin.github.io/hypixel_api_tracker/

监控的 7 个端点：

| 端点 | 地址 | 追踪内容 |
|------|------|----------|
| collections | `/v2/resources/skyblock/collections` | 分类 / 物品增删、字段修改（带 `version`） |
| skills | `/v2/resources/skyblock/skills` | 技能增删、字段修改（带 `version`） |
| items | `/v2/resources/skyblock/items` | 物品增删（按 `id` 匹配）、字段修改（无 `version`） |
| bazaar | `/v2/skyblock/bazaar` | **仅商品结构性增删**，忽略每秒波动的价格 |
| packs | `/v2/resources/packs` | `deployId`、`packFormat`、最大格式的 `hash`/`url`；另见资源包文件比对 |
| bingo | `/v2/resources/skyblock/bingo` | 期次时间窗口与全部任务；任务文案按 MC 颜色码渲染（见下） |
| firesales | `/v2/skyblock/firesales` | 限时特卖价格 / 数量 / 时间窗口，多期分割（见下） |

（域名均为 `https://api.hypixel.net`。）

## 快速开始（下载后即可运行）

只需要 **Rust 工具链**（`rustup` 安装的 `cargo`，1.75+ 即可），所有端点都是公开的，
**不需要 API Key**。

```bash
git clone https://github.com/xiaowangdeqilin/hypixel_api_tracker.git
cd hypixel_api_tracker
cargo run --release --bin tracker-build
```

运行完会生成 `site/index.html`，直接用浏览器打开即可（也可用任意静态文件服务器托管
`site/` 目录）。首次运行会抓取 7 个端点、
建立资源包文件基线，编译约 2–10 分钟（之后增量编译很快），
抓取耗时约 10–60 秒。三个二进制：

| 命令 | 作用 |
|------|------|
| `cargo run --release --bin tracker-build` | 抓取 → diff → 落盘 → 生成静态站（CI 使用） |
| `cargo run --release --bin tracker-server` | 本地实时仪表盘服务（默认 `127.0.0.1:8000`，`BIND_ADDR` 可改） |
| `cargo run --release --bin locales-gen` | 从 `i18n-src/translations.json` 生成 `locales/*.json` 并校验 |

生成物如下：

| 文件 | 内容 |
|------|------|
| `site/index.html` | 自包含的中文仪表盘页面 |
| `site/data.json` | 同源结构化数据（含 `version_info`、`data_written`） |
| `data/snapshots/*.json` | 各端点快照，供下次运行做 diff |
| `data/changes.json` | 字段变更日志（只保留最近 30 天，不限条数；静态站与本地服务共用） |
| `data/version.json` | 版本一致性状态与版本切换历史（v1 的 `runtime.json`） |
| `data/pack_files.json` | 资源包内文件哈希清单（基线） |
| `data/pack_texts.json` | 资源包内文本类文件内容缓存（用于展示变化片段） |
| `data/pack_changes.json` | 资源包文件新增 / 修改 / 删除记录（只保留最近 30 天，最多 50 批） |
| `data/bingo.json` | 当前 Bingo 期次、时间窗口与全部任务（供页面展示） |
| `data/firesales.json` | 限时特卖全部期次（价格/数量/时间窗口），每次构建重新抓取覆盖 |

> `HYPIXEL_API_KEY` 完全可选。若遇到限流，可 `export HYPIXEL_API_KEY=你的key`，
> 构建与本地服务都会自动带上 `?key=`。程序以**当前工作目录**为仓库根
> （CI 直接在工作区运行；`TRACKER_HOME` 环境变量可显式指定根目录）。

## 版本号：双端点一致性确认

`collections` 与 `skills` 两个资源接口的响应顶层都带 `version` 字段（`items` 没有）。
但 Hypixel 分批更新时**两个接口不一定同时切换版本**，如果直接取其中一个，
页面版本号就会来回跳（打架）。

`resolve_version_state()`（`src/state.rs`）采用一致性确认：

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
本地服务用同样的规则处理。

`VERSION` 文件（当前 `2.0.0`）是**仪表盘自身版本**，作为次要信息显示在版本条右侧。
改版本只需改这一个文件。

## 时间：统一北京时间（UTC+8）

所有写入与展示的时间都是北京时间，格式 `YYYY-MM-DD HH:MM:SS`。
`src/timeutil.rs` 是唯一实现：

- `now_beijing()` 生成新数据的时间；
- `format_time()` 把历史数据里的 UTC 字符串（`...Z` 或无时区后缀）换算成北京时间；
  已经是北京时间的新格式则原样返回；
- `from_epoch_ms()` 把 Hypixel 常用的 epoch 毫秒（Bingo 的 `start` / `end` /
  `lastUpdated`、资源包时间戳）换算成北京时间，无效值返回 `-`；
- `parse_beijing()` 反向解析，用于计算「剩余 17 小时」这类倒计时。

页面上的抓取时间、变更时间、生成时间、版本确认时间、资源包比对时间都经过它处理，
表头也明确标注「（北京时间）」。

## 变更展示：代码审核（diff）风格 + 只显示变化片段

「最近变更」不用普通表格，而是类似 git diff / code review 的区块：

- **新增**：只标 **绿色 `+`**，前面不会出现红色删除行
- **删除**：只标 **红色 `-`**
- **修改**：**只显示发生变化的那一段**——把原值 / 新值整理成文本后逐行比对
  （`src/textdiff.rs`，LCS 实现），删除行标红、新增行标绿，未变化的内容完全不输出
  （最多 8 个片段、每片 40 行，前后各留 2 行上下文便于定位）

长值可点「查看完整值」展开，路径、端点、时间显示在区块头部。
本地页面用同一套规则（前端用 LCS 逐行比对，见 `static/app.js`）。

**增删改记录只保留最近 30 天**（`src/retention.rs` 统一口径）：

- 构建时过滤 `changes.json` 与 `pack_changes.json`，超过 30 天的记录直接从数据文件删除
- 静态页面与本地页面渲染前再过滤一次，即使数据还没重建也不会显示旧记录
- 时间字段混用过北京时间与 UTC ISO 两种写法，统一解析后比较；无法解析的时间按过期处理

## 资源包文件比对

`src/packs.rs` 负责下载并拆解 Hypixel 资源包：

- **每次调用接口都取 `packFormat` 数值最大的版本**。该数字会随 Minecraft / Hypixel
  更新而变大，代码里不写死任何具体数字，页面会显示本次候选格式与选取结果
- 用官方提供的 sha1 `hash` 做缓存键：哈希未变化就直接复用上次清单，**不重复下载**
- 哈希变化时下载 zip、校验 sha1、解压（`zip` crate），对包内每个文件计算 sha256（前 16 位）
- 同时缓存**文本类文件**的内容（扩展名白名单、单文件 ≤128 KB、总量 ≤2 MB），
  二进制文件只留哈希；zip 条目只读内存、不落盘，无路径穿越风险
- 与上次清单比对，输出新增 / 修改 / 删除：
  - 新增文件**只标绿**、删除文件**只标红**
  - 被修改的文本文件**只展示变化的那一段**（删除行标红、新增行标绿）
  - 二进制文件只说明哈希变化
- **packFormat 号变化不会被误报成全量增删**：`diff_packs()` 用固定键
  （`<pack>.packFormat` / `.hash` / `.url`）比对最大格式，格式号升级只体现为一条修改
- 首次运行只建立基线，不会把包内全部文件误报为「新增」

结果展示在「资源包文件变更」面板，并持久化到 `data/pack_changes.json`。

## 主题切换与多设备适配

页面支持**亮色 / 深色**两种主题（`static/theme.css` 定义变量、`static/theme.js` 负责切换）：

- **默认跟随设备**：读取系统的 `prefers-color-scheme`，手机系统是深色就自动用深色
- 点击顶部版本栏右侧的「🌙 深色 / ☀ 浅色」按钮手动切换，选择写入
  `localStorage`（键 `hypixel-theme`），之后不再被系统设置覆盖
- 用户没手动选过时，系统在运行中切换深浅色（如日落自动切换），页面会跟着变
- 脚本放在 `<head>` 同步执行，先写 `<html data-theme>` 再渲染，避免首屏闪色
- 本地页面与静态 Pages 页面共用同一份主题实现，两边视觉一致

多设备适配：小屏下卡片改单列、表格在容器内横向滚动、版本栏自动换行、
Bingo 5×5 网格保持 5 列阵容可横向滑动（详见 `theme.css` 中的媒体查询）。

## 限时特卖（Firesales）

`/v2/skyblock/firesales` 返回当前所有限时特卖（一次响应里可能同时包含**多期**），
面板在 Bingo 之前展示（`data/firesales.json` 持久化）。

**多期分割**：每期特卖独立成卡，按开始时间排序，以 `item_id + start` 作为唯一键；
同一物品分期上架也会各自成卡。主视图只展示**即将开始与进行中**的期次，
卡片上直接给出**价格（宝石/Gems）与数量**，以及开始 / 结束时间（北京时间）。

**显示额外数据**：面板标题右侧的按钮可展开完整数据——全部期次（含已结束）的表格、
数据抓取时间与原始 JSON；再点一次收起。

**每次都重新抓取**：特卖在开始前官方随时可能调整时间 / 数量 / 价格，
因此每次构建（Action 运行）都会重新请求并整体覆盖 `data/firesales.json`，
不存在「内容没变就跳过」的省略；官方推迟开卖会在变更日志里体现为
「旧期移除 + 新期新增」，价格 / 数量 / 结束时间变化则是逐字段修改记录。
物品名称从 items 端点解析，尚未收录的新物品回退为 id 的可读形式。

## Bingo 任务

`/v2/resources/skyblock/bingo` 返回当前这一期 Bingo 的时间窗口与全部任务，
页面**最下方**汇总展示（`data/bingo.json` 持久化）。

**任务展示为 5×5 方格**：25 个任务按接口顺序从左到右、从上到下填入格子，
每个格子显示序号、任务名、MC 配色文案与全服进度；格子空间有限，
**点击任意格子**会弹出完整文案与进度详情。窄屏下仍保持 5 列阵容，可横向滑动查看。

**三个时间**分别取不同字段，页面上分行标注：

| 展示项 | 来源 | 说明 |
|--------|------|------|
| 发布时间 | 接口 `lastUpdated` | 官方最后一次刷新这期数据的时刻 |
| 首次发现 | 本仓库记录 | 第一次抓到这一期的时刻（同一期会沿用，不会被刷新掉） |
| 开始 / 结束 | 接口 `start` / `end` | 活动起止时间 |

`start` / `end` / `lastUpdated` 都是 epoch 毫秒，统一由
`src/timeutil.rs:from_epoch_ms()` 换算成北京时间；无效值（`0`、`null`、非数字）
返回 `-`，不会出现 `1970-01-01` 这种误导性时间。页面还会根据当前时间判断
**未开始 / 进行中 / 已结束**。

**倒计时在浏览器本地计算**：构建页面时只写入 `start` / `end` 的 epoch 毫秒
（`bingo_view()` 中的 `start_ms` / `end_ms`），`static/bingo.js` 在浏览器里
按本地时间逐秒刷新「距开始 / 剩余」，到点自动从未开始切进行中、从进行中切已结束，
无需等下一次抓取；**已结束的期次不再计时**，只保留「已结束」状态徽章。
旧版 `data/bingo.json` 没有这两个字段时，读取层会从北京时间字符串自动反推。

**任务文案的颜色码**：Hypixel 的 `lore` 用 Minecraft 传统颜色码（`§7Break §e10M Ore§7 Blocks.`）。
这类码是「状态」语义而不是「包裹」——`§e` 之后的所有文字都是黄色，直到遇到下一个
颜色码或 `§r`，所以不能简单字符串替换。`src/mctext.rs` 顺序扫描并维护当前样式状态，
渲染成等价的内联样式 HTML：

| 支持 | 含义 |
|------|------|
| `§0`–`§9`、`§a`–`§f` | 16 种官方颜色（取 Minecraft 官方 RGB，`§0` 纯黑在深色背景不可见，提亮为 `#404040`） |
| `§l` `§o` `§n` `§m` | 粗体 / 斜体 / 下划线 / 删除线，可叠加 |
| `§k` | 混淆效果，页面用轻微抖动示意 |
| `§r` | 重置颜色与全部格式 |
| `§x§R§R§G§G§B§B` | 十六进制颜色（MC 1.16+） |

> 颜色码在 MC 里是大小写不敏感的（`§A` 等同 `§a`）。`&7` 写法默认**不**解析，
> 避免把 `R&D` 这类普通文本误当成颜色码。本地页面的任务文案放在深色底块里，
> 保证 `§f` 白色、`§e` 黄色这类浅色文字在浅色主题下也能看清。

**哪些变化算「任务更新」**（`normalize_bingo()` + `diff_for_endpoint()`）：

| 变化 | 是否记录 | 原因 |
|------|----------|------|
| 任务文案（`lore`）/ 目标档位 / 要求数值 | ✅ 字段级记录 | 属于任务定义 |
| 新增 / 删除任务 | ✅ 记录为「新增任务「XXX」」 | 属于任务定义 |
| 期次切换（`id` 变化） | ✅ **合并为一条**「第 59 期 → 第 60 期」 | 整期换掉时逐条比对会刷出几十条无意义增删 |
| 全服进度 `progress` | ❌ 不记录 | 每轮抓取都在增长，只用于页面进度条 |
| `lastUpdated` | ❌ 不记录 | 只是数据刷新时间，页面单独展示 |

**社区 / 个人任务**：5×5 棋盘上**只有左上→右下对角线（第 1/7/13/19/25 格）是社区任务**，
其余均为个人任务，每格左上角有徽章标注。社区任务显示全服共享进度（档位满 5 级后
封顶显示「已满级 · 5/5 档」，进度条固定 100%）；**个人任务没有全服进度**，
只显示目标数值（「要求 x」），详情弹层也不会出现「全服进度 / 完成度」两行。

## 秘密 Bingo：提示按时间依次解锁

秘密任务（Secret Bingo）的提示无法从 API 获取，由 `data/secret_bingo.json` 人工维护。
**没有秘密 Bingo 进行时不显示这一块**（连占位卡也不渲染）：`goals` 为空即整块隐藏；
若填了可选的 `start` / `end` 时间窗，则只在窗口内显示，方便提前把提示写进配置。
显示期间每个任务一张卡、固定 3 条提示槽，按 `reveal_at` 时间依次解锁——到点后浏览器
自动展开提示文本（`static/secret.js` 按本地时间逐秒倒计时），无需刷新页面，也不受
定时构建延迟影响。

格式（完整示例见 `data/secret_bingo.example.json`）：

```json
{
  "event_id": 59,
  "start": "2026-11-01 12:00:00",
  "end": "2026-11-08 12:00:00",
  "goals": [
    {
      "id": "secret-1",
      "name": "秘密任务一",
      "hints": [
        { "text": "第一条提示文本", "reveal_at": "2026-11-05 20:00:00" },
        { "text": "第二条提示文本", "reveal_at": "2026-11-06 20:00:00" },
        { "text": "第三条提示文本", "reveal_at": "2026-11-07 20:00:00" }
      ]
    }
  ]
}
```

- `reveal_at` 支持北京时间串（`YYYY-MM-DD HH:MM:SS`）或 epoch 毫秒；
  无法解析时按「已解锁」处理，避免写错日期导致提示永久隐藏；
- 提示按 `reveal_at` 升序排列后取前 3 条，配置不足的槽位显示「待公布」；
- `goals` 留空（或文件不存在）时**整块面板不渲染**；
- `start` / `end` 可选：填写后只有当前时间落在窗口内才显示，留空表示「只要有配置就显示」；
- `event_id` 仅作备注，不参与逻辑。

## 接口异常：整次运行不做任何变化

Hypixel 接口偶尔会宕机、限流或返回不完整数据。为避免「半次更新」污染基线，
`tracker-build` 采用两阶段事务式写入：

1. **抓取 + 比对阶段完全不落盘**（资源包检查器也只返回待写数据，不自己写文件）
2. 只有满足全部条件才在最后一次性写入 `data/`：
   - 7 个接口全部成功拿到数据；
   - 资源包检查结果为 `baseline` / `updated` / `unchanged`
     （即没有 `error` / `unavailable`）
3. 任一环节失败（接口超时、非 200、下载失败、sha1 校验不符、zip 损坏、无可用下载地址），
   **本次运行不改动 `data/` 下的任何一个字节**，页面沿用上一次成功结果并在顶部标注原因

程序通过 `$GITHUB_OUTPUT` 输出 `data_written=true|false`，工作流据此判断：
为 `false` 时跳过提交、跳过上传产物与部署，因此**连页面都不会变**。

> 取舍：宁可这次不更新，也不要把「接口没数据」误记成「内容被全部删除」。
> 定时任务每小时一次，接口恢复后下一次运行会自动补上。

## GitHub Actions 与 Pages

`.github/workflows/pages.yml` **每小时运行一次**（`cron: "17 * * * *"`，也可手动 `workflow_dispatch`）：

1. `dtolnay/rust-toolchain@stable` 安装工具链，`Swatinem/rust-cache@v2` 缓存依赖
   （首次约多花 2–4 分钟编译，之后增量约 30 秒）
2. `cargo run --release --locked --bin tracker-build` 抓取、diff、确认版本、检查资源包、生成页面
3. 仅在 `data_written=true` 时 `git add data/` 提交回仓库（`[skip ci]` 避免循环触发）
4. 仅在 `data_written=true` 时上传 `site/` 产物并部署到 GitHub Pages

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
| `dtolnay/rust-toolchain` | `@stable` | composite |
| `Swatinem/rust-cache` | `@v2` | node24 |
| `actions/upload-pages-artifact` | `@v5` | composite（内部 upload-artifact 为 node24） |
| `actions/deploy-pages` | `@v5` | node24 |

## 可选：本地实时仪表盘（Rust 服务）

静态 Pages 之外，仓库附带一个本地实时服务（JSON 存储 + 后台定时抓取，无数据库依赖）：

```bash
cargo run --release --bin tracker-server
# 自定义监听地址：BIND_ADDR=0.0.0.0:8000 cargo run --release --bin tracker-server
```

打开 http://localhost:8000/ 。服务启动时会在后台立即抓取一轮，之后按
`src/fetch.rs` 里登记的间隔轮询（collections/skills 2 小时、items 6 小时、
bazaar/firesales 5 分钟、packs 10 分钟、bingo 30 分钟），页面每 30 秒自动刷新。

- 快照进入 `data/server/history.json` 环形缓冲（每端点最多 30 条、保留 7 天；
  单条 payload 超过 512 KB 只存元数据，避免 items 端点 6MB+ 的响应撑爆历史文件），
  已被 `.gitignore` 忽略
- 变更写入统一的 `data/changes.json`（与 CI 构建共用一份日志）
- 可用接口：`/api/status`、`/api/history`、`/api/changes`、`/api/snapshot/{id}`、`/health`
  （响应形状与 v1 FastAPI 版保持一致；`/api/history` 不再返回完整 payload，
  需要完整内容请用 `/api/snapshot/{id}`）

> 本地服务的抓取同样会剥离 `version` / `lastUpdated` / `success`，并对集市
> 只比较商品 id 集合，避免价格与版本变化刷屏。变更表格同样按「新增纯绿、
> 删除纯红、修改只显示变化片段」渲染。

## 多语言支持（默认关闭）

本地仪表盘内置可选的界面多语言支持，**默认完全关闭**——不设置
环境变量时行为与旧版一致（纯中文、无切换器、零开销）：

```bash
# 开启多语言
ENABLE_I18N=1 cargo run --release --bin tracker-server

# 可选：调整 /api/* 限流阈值（次/分钟，默认 240）
API_RATE_LIMIT=240 cargo run --release --bin tracker-server
```

开启后页面右上角出现语言切换器，选择结果保存在 Cookie（一年有效），
也可以直接用 `?lang=` 参数覆盖（如 `/?lang=ar`）。

当前内置 **30 种语言包**（加上默认的简体中文，切换器共 31 种）：
繁體中文、English、日本語、한국어、Tiếng Việt、ไทย、Bahasa Indonesia、Bahasa Melayu、Filipino、हिन्दी、বাংলা、
اردو、العربية、עברית、فارسی、Türkçe、Français、Deutsch、Español、Italiano、
Português、Nederlands、Русский、Українська、Polski、Čeština、Magyar、
Română、Ελληνικά、Svenska。

其中**阿拉伯语、希伯来语、波斯语、乌尔都语**为从右往左（RTL）语言：
`<html dir="rtl">` 自动生效，`static/i18n.css` 负责整体镜像适配；代码、
时间戳、数字、diff 等内容保持 LTR 展示不受影响。

译文存放于 `locales/<语言代码>.json`，键为简体中文原文、值为译文；
缺失条目自动回退中文。单一事实来源是 `i18n-src/translations.json`。
**新增语言**只需：

1. 在 `i18n-src/translations.json` 的 `packs` 下追加一个语言对象；
2. 在 `src/i18n.rs` 的 `language_names()` 登记代码与母语名称；
3. RTL 语言同时加入 `RTL_LANGUAGES`；
4. 运行 `cargo run --release --bin locales-gen` 重新生成（自带键集合 /
   占位符 / HTML 标签三项一致性校验，校验失败会拒绝生成）。

> 说明：`locales/` 仅作用于本地服务页面；GitHub Pages 静态页面
> 由 Actions 构建生成，暂不参与多语言。

## 安全加固

本地服务自带一层基础防护（`src/security.rs`，无额外依赖）：

- **安全响应头**：`Content-Security-Policy`（禁内联脚本）、
  `X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、
  `Referrer-Policy` 等；前端译文通过不可执行的
  `<script type="application/json">` 注入，规避内联脚本。
- **API 限流**：`/api/*` 读接口按 IP 滑动窗口限流（默认 240 次/分钟，
  超限返回 429 与 `Retry-After`），可用 `API_RATE_LIMIT` 调整；
  阈值硬上限 10000，`/health` 不受限。
- **参数收敛**：`limit` 类查询参数强制 `1 ≤ limit ≤ 100`，
  `endpoint` 参数限长 50，`lang` 参数走白名单校验，非法取值一律忽略。
- **路径安全**：静态文件服务拒绝 `..` 路径穿越；快照 id 只接受正整数。
- **资源包安全**：zip 条目只在内存中读取（不落盘），下载上限 256 MB，
  sha1 校验不符直接判失败。
- **Cookie 安全**：语言偏好 Cookie 带 `SameSite=Lax`。
- 模板（Tera）默认自动转义，语言包来自仓库受控内容，无用户输入进入格式串的路径。
- 内存安全由 Rust 所有权模型保证；错误信息统一白化（去控制字符、截断）后才入库。

> 部署在反向代理之后时，请确保代理正确覆写（而非透传客户端可伪造的）
> `X-Forwarded-For`，限流才能按真实客户端 IP 生效。

## 项目结构

```
.github/workflows/pages.yml   # 定时抓取 + 部署 Pages（Rust 工具链 + 依赖缓存）
VERSION                       # 仪表盘自身版本号
Cargo.toml / Cargo.lock       # Rust 工程与锁定依赖（单一 crate、三个 bin）
i18n-src/translations.json    # 语言包单一事实来源（30 种语言 × 95 条）
locales/                      # 生成的语言包（<语言代码>.json，键为中文原文）
templates/
  site.html.j2                # 静态站页面骨架（tracker-build 渲染）
  site_style.css              # 静态站主样式（内联进 index.html）
  server.html.j2              # 本地服务页面模板（Tera，译文由服务端预解析注入）
static/                        # 页面静态资源（两端共用）
  app.js / styles.css          # 本地页面数据加载与主题样式
  bingo.css / bingo.js         # Bingo 面板样式与交互（本地 <link> 引用、静态页构建时内联）
  firesale.css / firesale.js   # 限时特卖面板
  theme.css / theme.js         # 亮色/深色主题变量、切换按钮与系统跟随
  i18n.css / i18n.js           # RTL 镜像适配与前端翻译函数
src/
  lib.rs                       # 模块入口 + SHA/HTML 转义工具
  timeutil.rs                  # 北京时间统一处理（构建与服务共用）
  mctext.rs                    # Minecraft 颜色码（§）渲染
  jsondiff.rs                  # 结构化 JSON diff（摘要 + 完整值）
  textdiff.rs                  # 逐行 LCS diff（只输出变化片段）
  bingo.rs                     # Bingo 期次/任务归一化、状态与进度计算
  firesale.rs                  # 限时特卖归一化、单期切分与比对
  packs.rs                     # 资源包选最大格式 / 下载 / 解压 / 片段级 diff
  retention.rs                 # 变更记录 30 天保留期
  fetch.rs                     # HTTP 抓取、校验和、端点 diff、服务端抓取循环
  state.rs                     # 数据文件原子读写、版本状态机、服务端历史
  labels.rs                    # 展示层中文文案
  render_site.rs               # 静态站 HTML 片段渲染
  i18n.rs                      # 可选多语言：语言白名单、翻译函数、RTL 判定
  security.rs                  # 安全响应头 + API 限流
  server_app.rs                # 本地服务：路由、模板、调度
  bin/build.rs                 # tracker-build（CI 数据管道）
  bin/server.rs                # tracker-server（本地服务）
  bin/locales_gen.rs           # locales-gen（语言包生成 + 三项校验）
tests/core.rs                  # 核心模块集成测试（diff/时间/i18n/保留期）
data/
  snapshots/                  # 持久化快照（提交到仓库）
  changes.json                # 字段变更日志（构建与服务共用）
  version.json                # 版本一致性状态 + 切换历史（v1 的 runtime.json）
  pack_files.json              # 资源包文件哈希清单
  pack_texts.json              # 资源包文本文件缓存（用于变化片段）
  pack_changes.json            # 资源包文件变更记录
  bingo.json                   # 当前 Bingo 期次与全部任务
  firesales.json               # 限时特卖全部期次（每次构建重新抓取覆盖）
  legacy/                      # v1 遗留数据存档（SQLite 库、旧 runtime.json）
  server/                      # 本地服务专属状态（.gitignore，不上传）
site/                          # 生成的静态页面（被 .gitignore 忽略）
```

## 常见问题

| 现象 | 原因 / 处理 |
|------|-------------|
| 页面显示「版本同步中」 | 正常：两个资源接口正在分批更新，等它们一致即可，当前版本不会乱跳 |
| 首次运行资源包显示「基线」 | 正常：只建立文件清单，不报新增；等 Hypixel 更新资源包后才列出文件差异 |
| 页面顶部提示「未获取到完整数据」 | 接口或资源包下载失败，**本次不改动任何数据**，沿用上次结果；下次运行会自动重试 |
| 资源包 `packFormat` 变了 | 正常：数字随 MC 更新变化，每次都取最大值，只记为一条修改而非全量增删 |
| 打开线上页面仍是旧版 | CDN 缓存，等几分钟刷新；定时任务每小时也会重跑 |
| `cargo build` 很慢 | 首次编译需要拉取依赖；CI 有 `rust-cache`，本地第二次起就是增量编译 |
| 抓取报 429 / 超时 | 设置 `HYPIXEL_API_KEY`，或稍后重试；程序会整次跳过，不会写入半份数据 |
| Bingo 面板显示「暂无 Bingo 数据」 | `data/bingo.json` 还不存在，跑一次 `cargo run --release --bin tracker-build` 即可 |
| Bingo 任务文案颜色不对 | 已支持 `§0`–`§f` 与 `§l/§o/§n/§m/§k/§r`；`§0` 纯黑在深色背景不可见，按可用性提亮为 `#404040` |
| Bingo 显示「未开始」/「已结束」 | 按浏览器当前时间与 `start`/`end` 自动计算并切换，属正常；已结束只保留状态徽章，不再显示倒计时 |
| Bingo 倒计时与构建时间对不上 | 正常：倒计时由浏览器按本地时间逐秒计算，比构建时刻的快照更准 |
| 变更列表越来越短 | 正常：增删改记录只保留最近 30 天，更早的会自动从页面和数据中清理 |
| 切换的主题过几天又变了 | 正常：跟随系统是默认行为；点一次切换按钮后你的选择会被记住 |
| 本地服务 `/api/history` 看不到 payload | v2 有意为之：历史列表只给元数据，完整内容用 `/api/snapshot/{id}`；超大 payload（>512KB）只存元数据 |
| 从 v1 升级后旧数据还在吗 | 在：`changes.json`、快照、资源包清单等直接沿用；`runtime.json` 改名为 `version.json`；SQLite 库存档在 `data/legacy/` 不再读取 |
