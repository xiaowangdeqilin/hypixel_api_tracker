# Hypixel SkyBlock API Tracker v3.0.0

## 中文

**这次更新做了什么**

- **全平台发布**：Windows（x64 / arm64）、macOS（Apple Silicon / Intel）、Linux（x64 / arm64，glibc 与 musl）都有压缩包；
  另外提供 Windows 安装器、macOS `.pkg` 安装包与 `.dmg`、Linux `.deb` / `.rpm`。
- **自更新**：`tracker-server --check-update` 查新版本，`--self-update` 下载并校验 SHA256 后原地替换，
  失败会自动回滚到旧版本。设 `AUTO_UPDATE=apply` 可在启动时自动完成更新并重启。
- **崩溃报告**：任何崩溃都会在 `data/crash/` 下留一份 JSON + 人可读的 `.txt`，
  `--crash-list` 查看最近的报告；服务端单个请求崩溃只返回 500，不会拖垮整个服务。
- **FastAPI 版面板**：`pip install hypixel_tracker_api-3.0.0-py3-none-any.whl` 后运行 `hypixel-tracker-api`，
  直接读取本仓库发布的数据，提供 REST API 与轻量网页（103 种语言、时区换算、崩溃报告）。
- **多语言**：界面 103 种语言；安装器与安装说明覆盖中文、English、日本語、Deutsch、Español、Français、Русский、Português。
- **最近变更已重置**：v3.0.0 之前的快照差异不再显示，变更日志从零开始（不是数据丢失，快照仍是新的）。

**安装**

| 系统 | 推荐方式 |
|---|---|
| Windows | 下载 `HypixelTracker-Setup-*.exe` 安装，或解压 zip 直接用 |
| macOS | 下载 `.pkg` 双击安装，或挂载 `.dmg` 用 `install.sh` |
| Linux | `sudo dpkg -i hypixel-tracker_*_amd64.deb` 或 `sudo rpm -i hypixel-tracker-*.rpm` |

装好后运行 `tracker-server`，浏览器打开 <http://127.0.0.1:8000>。

## English

**Highlights**

- **Every platform**: Windows (x64/arm64), macOS (Apple Silicon/Intel), Linux (x64/arm64, glibc & musl),
  plus native installers: NSIS setup, macOS `.pkg`/`.dmg`, Linux `.deb`/`.rpm`.
- **Self-update**: `--check-update` / `--self-update`; SHA256 verified, automatic rollback on failure.
  Set `AUTO_UPDATE=apply` to update and restart on launch.
- **Crash reports**: every panic writes a JSON + human-readable `.txt` under `data/crash/`;
  `--crash-list` shows recent ones; a single failing request returns 500 instead of killing the server.
- **FastAPI edition**: `pip install hypixel_tracker_api-3.0.0-py3-none-any.whl`, then `hypixel-tracker-api`.
  It reads the data published by this repository and serves a REST API plus a lightweight dashboard.
- **Localisation**: 103 UI languages; installers ship notes in 中文 / English / 日本語 / Deutsch / Español / Français / Русский / Português.
- **Change log reset**: diffs recorded before v3.0.0 are no longer displayed; snapshots are still current.

Run `tracker-server` and open <http://127.0.0.1:8000>.

---

SHA256 checksums for every asset are in `SHA256SUMS.txt`. Verify with
`sha256sum -c SHA256SUMS.txt` (Linux) or `shasum -a 256 -c SHA256SUMS.txt` (macOS).
