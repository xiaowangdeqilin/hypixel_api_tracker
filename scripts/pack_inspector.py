#!/usr/bin/env python3
"""
Hypixel 资源包（texture pack）的下载、解压与文件变更比对。

每次 GitHub Actions 运行时：

1. 从 ``/v2/resources/packs`` 的响应里选出 packFormat 最高的资源包；
2. 若官方 sha1 与上次记录的 ``pack_hash`` 相同，直接复用已有清单，跳过下载；
3. 否则下载 zip、校验 sha1、解压并计算每个文件的 sha256（取前 16 位）；
4. 与上次清单比对，得到新增 / 修改 / 删除的文件清单；
5. 把新清单与变更记录写回 ``data/``，供下次运行和页面展示使用。

首次运行只建立基线，不会把包内所有文件误报为「新增」。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# 下载超时（秒）；资源包约 18 MB，留足余量。
DOWNLOAD_TIMEOUT = 120
# 单文件哈希保留的字符数：16 位十六进制已足够在数千文件规模下区分内容。
HASH_LENGTH = 16
# 变更记录最多保留的批次。
MAX_PACK_CHANGE_RECORDS = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest_path(data_dir: Path) -> Path:
    return data_dir / "pack_files.json"


def pack_changes_path(data_dir: Path) -> Path:
    return data_dir / "pack_changes.json"


def load_manifest(data_dir: Path) -> Dict[str, Any]:
    path = manifest_path(data_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def load_pack_changes(data_dir: Path) -> List[Dict[str, Any]]:
    path = pack_changes_path(data_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def select_pack(packs_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """从 packs 响应里选出 packFormat 最高的一个资源包版本。"""
    best: Optional[Dict[str, Any]] = None
    for pack in packs_payload.get("packs", []) or []:
        if not isinstance(pack, dict):
            continue
        for version in pack.get("versions", []) or []:
            if not isinstance(version, dict):
                continue
            fmt = int(version.get("packFormat") or 0)
            if best is None or fmt > best["pack_format"]:
                best = {
                    "id": pack.get("id", "unknown"),
                    "deploy_id": pack.get("deployId", ""),
                    "pack_format": fmt,
                    "hash": version.get("hash", ""),
                    "url": version.get("url", ""),
                }
    if best is None or not best["url"] or not best["hash"]:
        return None
    return best


def compute_file_hashes(zip_bytes: bytes) -> Dict[str, str]:
    """解压 zip，返回 {包内文件路径: 内容 sha256 前 16 位}。"""
    result: Dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            content = archive.read(info)
            result[info.filename] = hashlib.sha256(content).hexdigest()[:HASH_LENGTH]
    return result


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def inspect_pack(
    packs_payload: Dict[str, Any],
    data_dir: Path,
    *,
    force_download: bool = False,
) -> Dict[str, Any]:
    """检查资源包，返回本次检查结果与累计变更记录。

    返回结构::

        {
          "status": "unchanged" | "updated" | "baseline" | "error" | "unavailable",
          "pack": {...} | None,
          "total_files": int,
          "changes": [...]   # 累计的文件变更批次
        }
    """
    result: Dict[str, Any] = {
        "status": "unavailable",
        "pack": None,
        "total_files": 0,
        "changes": load_pack_changes(data_dir),
        "error": None,
    }

    target = select_pack(packs_payload)
    if target is None:
        result["error"] = "packs 响应中没有可用的资源包下载地址"
        return result
    result["pack"] = target

    manifest = load_manifest(data_dir)
    previous_files: Dict[str, str] = manifest.get("files", {}) or {}
    hash_matches = manifest.get("pack_hash") == target["hash"]

    if hash_matches and not force_download:
        result["status"] = "unchanged"
        result["total_files"] = len(previous_files)
        return result

    try:
        response = requests.get(target["url"], timeout=DOWNLOAD_TIMEOUT)
        response.raise_for_status()
        zip_bytes = response.content
        actual_sha1 = hashlib.sha1(zip_bytes).hexdigest()
        if actual_sha1 != target["hash"]:
            raise ValueError(f"资源包 sha1 校验失败（期望 {target['hash']}，实际 {actual_sha1}）")
        current_files = compute_file_hashes(zip_bytes)
    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    is_baseline = not previous_files
    added = sorted(set(current_files) - set(previous_files))
    removed = sorted(set(previous_files) - set(current_files))
    modified = sorted(
        key
        for key in (set(current_files) & set(previous_files))
        if current_files[key] != previous_files[key]
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

    # 即使首次建立基线也保留一条记录，让页面能显示当前资源包状态。
    changes = [record] + result["changes"]
    changes = changes[:MAX_PACK_CHANGE_RECORDS]
    result["changes"] = changes
    result["total_files"] = len(current_files)
    result["status"] = "baseline" if is_baseline else "updated"

    _write_json(
        manifest_path(data_dir),
        {
            "pack_id": target["id"],
            "deploy_id": target["deploy_id"],
            "pack_format": target["pack_format"],
            "pack_hash": target["hash"],
            "generated_at": _now_iso(),
            "files": current_files,
        },
    )
    _write_json(pack_changes_path(data_dir), changes)
    return result
