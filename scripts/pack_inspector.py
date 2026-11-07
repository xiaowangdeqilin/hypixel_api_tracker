#!/usr/bin/env python3
"""Hypixel 资源包（texture pack）的下载、解压与文件变更比对。

每次 GitHub Actions 运行时：

1. 从 ``/v2/resources/packs`` 的响应里**动态选出 packFormat 数值最大**的资源包
   （该数字随 Minecraft / Hypixel 更新而变化，代码中不写死任何具体数字）；
2. 若官方 sha1 与上次记录的 ``pack_hash`` 相同，直接复用已有清单，跳过下载；
3. 否则下载 zip、校验 sha1、解压，计算每个文件的 sha256（前 16 位），
   并为文本类文件缓存内容（用于下次比对时做逐行 diff）；
4. 与上次清单比对，得到新增 / 修改 / 删除的文件清单，修改项只记录变化片段；
5. 把结果交给调用方统一提交（``commit_pack_result``）。

**失败安全**：接口无数据、下载失败、sha1 校验失败或解压失败时，本模块
**不会写入任何文件**，也不产生任何变更记录，磁盘上的基线保持不变，
由 ``build_dashboard.main()`` 决定整次运行是否落盘。

首次运行只建立基线，不会把包内所有文件误报为「新增」。
"""

from __future__ import annotations

import difflib
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.retention import filter_recent  # noqa: E402
from app.timeutil import now_beijing  # noqa: E402

# 下载超时（秒）；资源包约 18 MB，留足余量。
DOWNLOAD_TIMEOUT = 120
# 单文件哈希保留的字符数：16 位十六进制已足够在数千文件规模下区分内容。
HASH_LENGTH = 16
# 变更记录最多保留的批次。
MAX_PACK_CHANGE_RECORDS = 50

# --------------------------------------------------------------------------- #
# 文本缓存：只有文本类文件才缓存内容，用于下次比对时展示「哪一段变了」
# --------------------------------------------------------------------------- #

TEXT_EXTENSIONS = (
    ".json", ".mcmeta", ".txt", ".lang", ".fsh", ".vsh", ".glsl",
    ".properties", ".yml", ".yaml", ".cfg", ".md", ".toml", ".csv",
)
# 单个文本文件最大缓存体积（字节），超过则只记录哈希。
MAX_TEXT_FILE_BYTES = 128 * 1024
# 文本缓存总量上限（字节），实测当前资源包约 0.42 MB，留足余量。
MAX_TOTAL_TEXT_BYTES = 2 * 1024 * 1024

# 逐行 diff 的展示上限，避免单个文件刷屏。
DIFF_CONTEXT_LINES = 2
DIFF_MAX_HUNKS = 8
DIFF_MAX_LINES_PER_HUNK = 40


def _now_iso() -> str:
    """兼容旧调用名：返回当前北京时间字符串。"""
    return now_beijing()


def manifest_path(data_dir: Path) -> Path:
    return data_dir / "pack_files.json"


def texts_path(data_dir: Path) -> Path:
    return data_dir / "pack_texts.json"


def pack_changes_path(data_dir: Path) -> Path:
    return data_dir / "pack_changes.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def load_manifest(data_dir: Path) -> Dict[str, Any]:
    data = _load_json(manifest_path(data_dir), {})
    return data if isinstance(data, dict) else {}


def load_texts(data_dir: Path) -> Dict[str, Any]:
    data = _load_json(texts_path(data_dir), {})
    return data if isinstance(data, dict) else {}


def load_pack_changes(data_dir: Path) -> List[Dict[str, Any]]:
    data = _load_json(pack_changes_path(data_dir), [])
    return data if isinstance(data, list) else []


def save_pack_changes(data_dir: Path, changes: List[Dict[str, Any]]) -> None:
    """写回资源包变更记录（用于清理超过保留期的记录）。"""
    _write_json(pack_changes_path(data_dir), changes)


# --------------------------------------------------------------------------- #
# 选取最大 packFormat
# --------------------------------------------------------------------------- #


def parse_pack_format(value: Any) -> Optional[int]:
    """把 packFormat 解析成整数；无法解析时返回 None。

    该字段理论上会随 Minecraft 版本升级而变大，因此任何地方都不写死具体数值，
    每次调用接口时重新取最大值。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def collect_pack_versions(packs_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """摊平 packs 响应中所有可用的资源包版本（含所属 pack 信息）。"""
    versions: List[Dict[str, Any]] = []
    packs = packs_payload.get("packs") if isinstance(packs_payload, dict) else None
    for pack in packs or []:
        if not isinstance(pack, dict):
            continue
        for version in pack.get("versions") or []:
            if not isinstance(version, dict):
                continue
            fmt = parse_pack_format(version.get("packFormat"))
            if fmt is None:
                continue
            versions.append(
                {
                    "id": pack.get("id", "unknown"),
                    "deploy_id": pack.get("deployId", ""),
                    "pack_format": fmt,
                    "hash": version.get("hash", ""),
                    "url": version.get("url", ""),
                }
            )
    return versions


def select_pack(packs_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """选出 packFormat 数值最大的资源包版本。

    数字随 MC / Hypixel 更新而变化，所以每次调用接口都重新取最大，不做任何
    本地缓存或硬编码。返回结果里附带 ``candidates``，供页面展示选取依据。
    """
    versions = collect_pack_versions(packs_payload)
    usable = [v for v in versions if v["url"] and v["hash"]]
    if not usable:
        return None
    best = max(usable, key=lambda item: item["pack_format"])
    best["candidates"] = sorted({v["pack_format"] for v in versions}, reverse=True)
    best["pack_count"] = len(
        packs_payload.get("packs") or []
    ) if isinstance(packs_payload, dict) else 0
    return best


# --------------------------------------------------------------------------- #
# zip 解析：哈希清单 + 文本缓存
# --------------------------------------------------------------------------- #


def is_text_file(filename: str) -> bool:
    return filename.lower().endswith(TEXT_EXTENSIONS)


def scan_zip(zip_bytes: bytes) -> Tuple[Dict[str, str], Dict[str, str]]:
    """解压 zip，返回 ``(文件哈希清单, 文本文件内容缓存)``。"""
    files: Dict[str, str] = {}
    texts: Dict[str, str] = {}
    total_text_bytes = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            content = archive.read(info)
            files[info.filename] = hashlib.sha256(content).hexdigest()[:HASH_LENGTH]
            if not is_text_file(info.filename) or len(content) > MAX_TEXT_FILE_BYTES:
                continue
            if total_text_bytes + len(content) > MAX_TOTAL_TEXT_BYTES:
                continue
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            texts[info.filename] = text
            total_text_bytes += len(text)
    return files, texts


def diff_text_lines(
    old_text: str, new_text: str
) -> List[Dict[str, Any]]:
    """对两个文本做逐行比对，**只返回发生变化的片段**（带少量上下文）。

    返回结构::

        [{"old_start": int, "new_start": int,
          "lines": [{"kind": "ctx" | "del" | "add", "text": str}]}]

    未变化的部分完全不输出，页面因此只显示「哪一段发生了修改」。
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    if old_text == new_text:
        return []

    hunks: List[Dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    pending: List[Dict[str, str]] = []
    old_start, new_start = 0, 0
    i2 = len(old_lines)  # 循环未产生 opcode 时的尾上下文兜底

    def flush() -> None:
        if not pending:
            return
        hunks.append(
            {
                "old_start": old_start + 1,
                "new_start": new_start + 1,
                "lines": pending[:DIFF_MAX_LINES_PER_HUNK],
            }
        )
        if len(pending) > DIFF_MAX_LINES_PER_HUNK:
            hunks[-1]["truncated"] = len(pending) - DIFF_MAX_LINES_PER_HUNK
        pending.clear()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            if i2 - i1 <= 2 * DIFF_CONTEXT_LINES and pending:
                # 变化段之间的少量上下文保留，让人看得懂位置
                pending.extend({"kind": "ctx", "text": ln} for ln in old_lines[i1:i2])
            else:
                if pending:
                    head = old_lines[i1 : i1 + DIFF_CONTEXT_LINES]
                    pending.extend({"kind": "ctx", "text": ln} for ln in head)
                    flush()
            continue
        if not pending:
            ctx_old = old_lines[max(0, i1 - DIFF_CONTEXT_LINES) : i1]
            ctx_new = new_lines[max(0, j1 - DIFF_CONTEXT_LINES) : j1]
            old_start = max(0, i1 - DIFF_CONTEXT_LINES)
            new_start = max(0, j1 - DIFF_CONTEXT_LINES)
            pending.extend({"kind": "ctx", "text": ln} for ln in ctx_new or ctx_old)
        if tag in ("replace", "delete"):
            pending.extend({"kind": "del", "text": ln} for ln in old_lines[i1:i2])
        if tag in ("replace", "insert"):
            pending.extend({"kind": "add", "text": ln} for ln in new_lines[j1:j2])
        if len(pending) > DIFF_MAX_LINES_PER_HUNK and tag != "equal":
            flush()

    if pending:
        tail_old = old_lines[i2 : i2 + DIFF_CONTEXT_LINES]
        pending.extend({"kind": "ctx", "text": ln} for ln in tail_old)
        flush()

    return hunks[:DIFF_MAX_HUNKS]


def build_modified_entries(
    paths: List[str],
    previous_files: Dict[str, str],
    current_files: Dict[str, str],
    previous_texts: Dict[str, str],
    current_texts: Dict[str, str],
) -> List[Dict[str, Any]]:
    """为被修改的文件生成展示条目。

    * 文本文件：只携带变化片段（``hunks``），页面据此标红标绿；
    * 二进制文件：只说明哈希变化，不展示无意义的内容。
    """
    entries: List[Dict[str, Any]] = []
    for path in paths:
        old_hash = previous_files.get(path, "")
        new_hash = current_files.get(path, "")
        old_text = previous_texts.get(path)
        new_text = current_texts.get(path)
        if isinstance(old_text, str) and isinstance(new_text, str):
            hunks = diff_text_lines(old_text, new_text)
            if hunks:
                entries.append(
                    {
                        "path": path,
                        "kind": "text",
                        "old_hash": old_hash,
                        "new_hash": new_hash,
                        "hunks": hunks,
                    }
                )
                continue
        entries.append(
            {
                "path": path,
                "kind": "binary" if not is_text_file(path) else "text",
                "old_hash": old_hash,
                "new_hash": new_hash,
            }
        )
    return entries


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def inspect_pack(
    packs_payload: Dict[str, Any],
    data_dir: Path,
    *,
    force_download: bool = False,
) -> Dict[str, Any]:
    """检查资源包，返回本次检查结果，**但不写入任何文件**。

    调用方需要用 :func:`commit_pack_result` 落盘，从而在接口异常时可以让
    整次运行一个字节都不改。

    返回结构::

        {
          "status": "baseline" | "updated" | "unchanged" | "error" | "unavailable",
          "pack": {...} | None,
          "total_files": int,
          "changes": [...],      # 累计变更记录（未落盘）
          "error": str | None,
          "pending": {...} | None  # 需要写入的数据，None 表示无需写入
        }
    """
    result: Dict[str, Any] = {
        "status": "unavailable",
        "pack": None,
        "total_files": 0,
        "changes": load_pack_changes(data_dir),
        "error": None,
        "pending": None,
    }

    target = select_pack(packs_payload)
    if target is None:
        result["error"] = "packs 响应中没有可用的资源包下载地址"
        return result
    result["pack"] = target
    result["candidates"] = target.get("candidates", [])

    manifest = load_manifest(data_dir)
    previous_files: Dict[str, str] = manifest.get("files", {}) or {}
    texts_cache = load_texts(data_dir)
    previous_texts: Dict[str, str] = texts_cache.get("texts") or {}
    result["total_files"] = len(previous_files)

    hash_matches = manifest.get("pack_hash") == target["hash"]
    texts_missing = (
        not previous_texts or texts_cache.get("pack_hash") != target["hash"]
    )
    content_only_refresh = hash_matches and previous_files and not force_download

    if content_only_refresh and not texts_missing:
        result["status"] = "unchanged"
        return result

    try:
        response = requests.get(target["url"], timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        zip_bytes = response.content
        actual_sha1 = hashlib.sha1(zip_bytes).hexdigest()
        if actual_sha1 != target["hash"]:
            raise ValueError(
                f"资源包 sha1 校验失败（官方 {target['hash']}，实际 {actual_sha1}）"
            )
        current_files, current_texts = scan_zip(zip_bytes)
        if not current_files:
            raise ValueError("资源包解压后没有可比对的文件")
    except Exception as exc:  # noqa: BLE001
        # 任何一步失败都保持基线不变，交由上层决定是否整次跳过。
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    if content_only_refresh:
        # 旧基线缺少文本缓存（升级到含片段 diff 的版本）：只补齐缓存，
        # 不改动文件哈希清单、不产生任何文件变更记录。
        result["status"] = "texts_backfilled"
        result["total_files"] = len(current_files)
        result["pending"] = {
            "texts": {
                "pack_id": target["id"],
                "pack_hash": target["hash"],
                "generated_at": _now_iso(),
                "texts": current_texts,
            }
        }
        return result

    is_baseline = not previous_files
    added = sorted(set(current_files) - set(previous_files))
    removed = sorted(set(previous_files) - set(current_files))
    modified_paths = sorted(
        key
        for key in (set(current_files) & set(previous_files))
        if current_files[key] != previous_files[key]
    )
    modified = build_modified_entries(
        modified_paths, previous_files, current_files, previous_texts, current_texts
    )

    record: Dict[str, Any] = {
        "time": _now_iso(),
        "pack_id": target["id"],
        "deploy_id": target["deploy_id"],
        "pack_format": target["pack_format"],
        "pack_hash": target["hash"],
        "total_files": len(current_files),
        "added": [] if is_baseline else added,
        "modified": [] if is_baseline else modified,
        "removed": [] if is_baseline else removed,
        "note": "首次建立基线" if is_baseline else None,
    }

    changes = [record] + result["changes"]
    # 超过保留期（30 天）的批次不再展示，也不再进入数据文件。
    changes = filter_recent(changes, key="time")
    changes = changes[:MAX_PACK_CHANGE_RECORDS]
    result["changes"] = changes
    result["total_files"] = len(current_files)
    result["status"] = "baseline" if is_baseline else "updated"
    result["pending"] = {
        "manifest": {
            "pack_id": target["id"],
            "deploy_id": target["deploy_id"],
            "pack_format": target["pack_format"],
            "pack_hash": target["hash"],
            "generated_at": _now_iso(),
            "files": current_files,
        },
        "texts": {
            "pack_id": target["id"],
            "pack_hash": target["hash"],
            "generated_at": _now_iso(),
            "texts": current_texts,
        },
        "changes": changes,
    }
    return result


def commit_pack_result(result: Dict[str, Any], data_dir: Path) -> bool:
    """把 :func:`inspect_pack` 的结果落盘（按需写，补缓存时只写 texts）。"""
    pending = result.get("pending")
    if not isinstance(pending, dict):
        return False
    written = False
    if "manifest" in pending:
        _write_json(manifest_path(data_dir), pending["manifest"])
        written = True
    if "texts" in pending:
        _write_json(texts_path(data_dir), pending["texts"])
        written = True
    if "changes" in pending:
        _write_json(pack_changes_path(data_dir), pending["changes"])
        written = True
    return written


if __name__ == "__main__":  # pragma: no cover - 手动排查用
    import argparse

    parser = argparse.ArgumentParser(description="手动检查 Hypixel 资源包文件变化")
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument(
        "--force", action="store_true", help="忽略官方哈希缓存，强制重新下载"
    )
    args = parser.parse_args()

    payload = requests.get(
        "https://api.hypixel.net/v2/resources/packs", timeout=30
    ).json()
    outcome = inspect_pack(payload, Path(args.data_dir), force_download=args.force)
    print("状态:", outcome["status"])
    print("选用 packFormat:", (outcome.get("pack") or {}).get("pack_format"))
    print("候选格式:", outcome.get("candidates"))
    print("文件数:", outcome["total_files"])
    if outcome.get("error"):
        print("错误:", outcome["error"])
    if commit_pack_result(outcome, Path(args.data_dir)):
        print("已写入清单与变更记录")
    else:
        print("未写入任何文件（沿用上次基线）")
