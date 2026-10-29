import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, Optional

from app.config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                status_code INTEGER,
                payload TEXT NOT NULL,
                checksum TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS change_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL,
                snapshot_id INTEGER NOT NULL,
                previous_snapshot_id INTEGER,
                change_type TEXT NOT NULL,
                path TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_snapshot(endpoint: str, status_code: int, payload: Dict[str, Any], checksum: str) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO snapshots (endpoint, fetched_at, status_code, payload, checksum)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                endpoint,
                datetime.utcnow().isoformat(timespec="seconds"),
                status_code,
                json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
                checksum,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_latest_snapshot(endpoint: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM snapshots
            WHERE endpoint = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (endpoint,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "endpoint": row["endpoint"],
            "fetched_at": row["fetched_at"],
            "status_code": row["status_code"],
            "payload": json.loads(row["payload"]),
            "checksum": row["checksum"],
        }
    finally:
        conn.close()


def get_snapshot_by_id(snapshot_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM snapshots
            WHERE id = ?
            LIMIT 1
            """,
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "endpoint": row["endpoint"],
            "fetched_at": row["fetched_at"],
            "status_code": row["status_code"],
            "payload": json.loads(row["payload"]),
            "checksum": row["checksum"],
        }
    finally:
        conn.close()


def insert_change_event(
    endpoint: str,
    snapshot_id: int,
    previous_snapshot_id: Optional[int],
    change_type: str,
    path: str,
    old_value: Any,
    new_value: Any,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO change_events (
                endpoint, snapshot_id, previous_snapshot_id, change_type, path, old_value, new_value, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                endpoint,
                snapshot_id,
                previous_snapshot_id,
                change_type,
                path,
                json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
                json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_recent_changes(limit: int = 20):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM change_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_recent_snapshots(endpoint: Optional[str] = None, limit: int = 10):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM snapshots
            WHERE (? IS NULL OR endpoint = ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (endpoint, endpoint, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_status_summary():
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT endpoint, MAX(id) AS latest_snapshot_id
            FROM snapshots
            GROUP BY endpoint
            """
        ).fetchall()

        summary = []
        for row in rows:
            latest = conn.execute(
                """
                SELECT endpoint, fetched_at, status_code
                FROM snapshots
                WHERE id = ?
                """,
                (row["latest_snapshot_id"],),
            ).fetchone()
            change_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM change_events
                WHERE endpoint = ?
                """,
                (latest["endpoint"],),
            ).fetchone()
            summary.append(
                {
                    "endpoint": latest["endpoint"],
                    "fetched_at": latest["fetched_at"],
                    "status_code": latest["status_code"],
                    "change_count": change_count["count"],
                }
            )
        return summary
    finally:
        conn.close()
