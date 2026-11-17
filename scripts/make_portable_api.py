#!/usr/bin/env python3
"""把 FastAPI 版面板打成一个「下载即用」的 zip。

数据每小时更新一次，而 Python 包的代码很少变动，所以不重新构建 wheel，
而是把源码 + 当前数据快照打成一个便携包，挂到滚动 release（tag: data-latest）上：

    hypixel-tracker-api-portable/
      fastapi_app/            源码（pip install -e 即可）
      data/data.json          本次更新的数据快照
      run.sh / run.ps1        一键启动（建虚拟环境 + 安装 + 启动）
      README.txt              8 种语言的启动说明
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dist-portable"
NAME = "hypixel-tracker-api-portable"

RUN_SH = """#!/usr/bin/env bash
# 首次运行会建虚拟环境并安装依赖（约 1 分钟），之后秒开
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip >/dev/null
  ./.venv/bin/pip install ./fastapi_app
fi
export HYPIXEL_DATA_FILE="$HERE/data/data.json"
exec ./.venv/bin/hypixel-tracker-api --host 127.0.0.1 --port "${PORT:-8000}"
"""

RUN_PS1 = """# 首次运行会建虚拟环境并安装依赖（约 1 分钟），之后秒开
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) {
  python -m venv .venv
  .\\.venv\\Scripts\\python.exe -m pip install --upgrade pip
  .\\.venv\\Scripts\\python.exe -m pip install .\\fastapi_app
}
$env:HYPIXEL_DATA_FILE = Join-Path $PSScriptRoot "data\\data.json"
& .\\.venv\\Scripts\\hypixel-tracker-api.exe --host 127.0.0.1 --port 8000
"""

README = """Hypixel SkyBlock API Tracker —— FastAPI 便携版 / FastAPI portable build
=====================================================================

中文：解压后运行 run.sh（Windows 双击 run.ps1），浏览器打开 http://127.0.0.1:8000
English: unzip, run ./run.sh (Windows: run.ps1), open http://127.0.0.1:8000
日本語: 解凍して run.sh を実行（Windows は run.ps1）、http://127.0.0.1:8000 を開く
Deutsch: entpacken, run.sh starten (Windows: run.ps1), http://127.0.0.1:8000 öffnen
Español: descomprime, ejecuta run.sh (Windows: run.ps1) y abre http://127.0.0.1:8000
Français: décompressez, lancez run.sh (Windows : run.ps1), ouvrez http://127.0.0.1:8000
Русский: распакуйте, запустите run.sh (Windows: run.ps1), откройте http://127.0.0.1:8000
Português: extraia, rode run.sh (Windows: run.ps1) e abra http://127.0.0.1:8000

数据是本包内置的快照（data/data.json）；想始终跟随线上最新数据，
删掉环境变量 HYPIXEL_DATA_FILE 直接运行即可，程序会从 GitHub 拉取。
The bundled snapshot is used by default; unset HYPIXEL_DATA_FILE to follow the live data on GitHub.
"""

SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist", ".mypy_cache"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def copy_tree(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if item.suffix in SKIP_SUFFIX:
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def main() -> int:
    app_dir = ROOT / "fastapi_app"
    data_file = ROOT / "site" / "data.json"
    if not app_dir.is_dir():
        print("fastapi_app/ 不存在，跳过便携包")
        return 1
    if not data_file.is_file():
        print("site/data.json 不存在，跳过便携包")
        return 1

    stage = OUT_DIR / NAME
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "data").mkdir(parents=True)

    copy_tree(app_dir, stage / "fastapi_app")
    shutil.copy2(data_file, stage / "data" / "data.json")

    scripts = {"run.sh": RUN_SH, "run.ps1": RUN_PS1, "README.txt": README}
    for name, content in scripts.items():
        path = stage / name
        path.write_text(content, encoding="utf-8", newline="\n")
    (stage / "run.sh").chmod(0o755)

    meta = {
        "built_from": os.environ.get("GITHUB_SHA", ""),
        "data_generated_at": json.loads(data_file.read_text(encoding="utf-8")).get("generated_at", ""),
    }
    (stage / "build-info.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    OUT_DIR.mkdir(exist_ok=True)
    archive = OUT_DIR / f"{NAME}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(OUT_DIR))
    size = archive.stat().st_size / 1024 / 1024
    print(f"已生成 {archive}（{size:.1f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
