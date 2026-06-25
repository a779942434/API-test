"""执行历史持久化 — SQLite 本地存储，支持历史查询和回归对比。

数据库位置: ~/.api_workbench_saves/workbench.db
保留策略: 90 天自动清理
"""

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse, urlunsplit

from api_test_workbench.engine.logger import setup_logger

log = setup_logger("history")

DB_PATH = Path.home() / ".api_workbench_saves" / "workbench.db"
RETENTION_DAYS = 90

# 敏感字段名（存储前自动脱敏）
_SENSITIVE_PARAMS = {'api_key', 'token', 'access_token', 'key', 'secret', 'password'}
_SENSITIVE_KEYS = {'token', 'password', 'secret', 'api_key', 'access_token', 'authorization',
                   'apikey', 'passwd', 'pwd', 'credential', 'private_key'}


def _redact_url(url: str) -> str:
    """脱敏 URL 中的敏感查询参数。"""
    if not url or '?' not in url:
        return url or ""
    try:
        u = urlparse(url)
        params = parse_qs(u.query, keep_blank_values=True)
        redacted = {}
        for k, v in params.items():
            if k.lower() in _SENSITIVE_PARAMS:
                redacted[k] = ['***REDACTED***']
            else:
                redacted[k] = v
        new_query = urlencode(redacted, doseq=True, safe='*')
        return urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
    except Exception:
        # 解析失败时直接截断 query 部分
        return url.split('?')[0] + '?...（已脱敏）'


def _redact_body(data) -> str:
    """递归脱敏请求/响应体中的敏感字段，返回安全的 JSON 字符串。"""
    if isinstance(data, dict):
        return json.dumps(
            {k: '***REDACTED***' if k.lower() in _SENSITIVE_KEYS else
             (json.loads(_redact_body(v)) if isinstance(v, (dict, list)) else v)
             for k, v in data.items()},
            ensure_ascii=False,
        )
    elif isinstance(data, list):
        return json.dumps(
            [json.loads(_redact_body(v)) if isinstance(v, (dict, list)) else v for v in data],
            ensure_ascii=False,
        )
    elif isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, (dict, list)):
                return _redact_body(parsed)
        except (json.JSONDecodeError, TypeError):
            pass
        return data
    return json.dumps(data, ensure_ascii=False, default=str)


def _ensure_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.chmod(0o700)


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接（自动创建表）。"""
    _ensure_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection):
    """创建表（幂等）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_name TEXT NOT NULL,
            env_name TEXT DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT,
            total INTEGER DEFAULT 0,
            passed INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            stopped_at_step INTEGER DEFAULT -1,
            overall_passed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS run_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            case_id TEXT NOT NULL,
            case_name TEXT DEFAULT '',
            step_idx INTEGER NOT NULL,
            step_name TEXT DEFAULT '',
            passed INTEGER NOT NULL,
            actual_status_code INTEGER DEFAULT 0,
            expected_status_code INTEGER DEFAULT 0,
            response_time_ms REAL DEFAULT 0.0,
            error_message TEXT DEFAULT '',
            request_url TEXT DEFAULT '',
            request_body TEXT DEFAULT '',
            response_body TEXT DEFAULT '',
            FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_run_results_run_id ON run_results(run_id);
        CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);
    """)


def record_run(pipeline_name: str, env_name: str, result) -> int:
    """记录一次 Pipeline 执行到历史数据库。

    Args:
        pipeline_name: 管道名称
        env_name: 环境名称
        result: PipelineResult 对象

    Returns:
        run_id: 新记录的主键 ID
    """
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = 0
    passed_count = 0
    failed_count = 0

    for sr in result.step_results:
        for tr in sr.test_results:
            total += 1
            if tr.passed:
                passed_count += 1
            else:
                failed_count += 1

    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO runs (pipeline_name, env_name, started_at, finished_at,
               total, passed, failed, stopped_at_step, overall_passed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pipeline_name,
                env_name,
                started_at,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                total,
                passed_count,
                failed_count,
                result.stopped_at_step if hasattr(result, 'stopped_at_step') else -1,
                1 if result.overall_passed else 0,
            ),
        )
        run_id = cur.lastrowid

        for sr in result.step_results:
            for tr in sr.test_results:
                # 脱敏处理
                redacted_url = _redact_url(tr.request_url or "")
                redacted_req = _redact_body(tr.request_body) if tr.request_body else ""
                redacted_resp = _redact_body(tr.response_body) if tr.response_body else ""

                conn.execute(
                    """INSERT INTO run_results
                       (run_id, case_id, case_name, step_idx, step_name, passed,
                        actual_status_code, expected_status_code, response_time_ms,
                        error_message, request_url, request_body, response_body)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        run_id,
                        tr.case_id,
                        tr.case_name,
                        sr.step_index,
                        sr.step_name,
                        1 if tr.passed else 0,
                        tr.actual_status_code,
                        tr.expected_status_code,
                        tr.response_time_ms,
                        (tr.error_message or "")[:2000],
                        redacted_url[:2000],
                        redacted_req[:4000],
                        redacted_resp[:4000],
                    ),
                )

        conn.commit()
        log.info("执行历史已记录: run_id=%d, %d 条用例, %d 通过", run_id, total, passed_count)
        return run_id
    except Exception as e:
        log.error("记录执行历史失败: %s", e)
        conn.rollback()
        raise
    finally:
        conn.close()


def list_runs(limit: int = 20) -> list[dict]:
    """列出最近的执行历史。

    Returns:
        [{id, pipeline_name, env_name, started_at, total, passed, failed, overall_passed}, ...]
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, pipeline_name, env_name, started_at, finished_at,
                      total, passed, failed, stopped_at_step, overall_passed
               FROM runs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_run_detail(run_id: int) -> Optional[dict]:
    """获取单次执行的详细信息，包含所有用例结果。

    Returns:
        {run: {...}, results: [{case_id, step_idx, step_name, passed, ...}, ...]}
    """
    conn = _get_conn()
    try:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            return None
        results = conn.execute(
            "SELECT * FROM run_results WHERE run_id = ? ORDER BY step_idx, id",
            (run_id,),
        ).fetchall()
        return {
            "run": dict(run),
            "results": [dict(r) for r in results],
        }
    finally:
        conn.close()


def get_regression_comparison(pipeline_name: str, limit: int = 5) -> list[dict]:
    """获取同一管道的最近 N 次执行摘要，用于回归对比。

    Returns:
        [{id, started_at, total, passed, failed, overall_passed}, ...]
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, started_at, finished_at, total, passed, failed, overall_passed
               FROM runs WHERE pipeline_name = ?
               ORDER BY id DESC LIMIT ?""",
            (pipeline_name, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_failed_cases(run_id: int) -> list[dict]:
    """获取指定执行中所有失败的用例。"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT case_id, case_name, step_idx, step_name,
                      expected_status_code, actual_status_code,
                      error_message, request_url
               FROM run_results WHERE run_id = ? AND passed = 0
               ORDER BY step_idx""",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def compare_with_previous(run_id: int) -> Optional[dict]:
    """与上一次同管道执行对比，找出新增失败和修复的用例。

    Returns:
        {previous_run_id, new_failures: [case_id], new_passes: [case_id]}
        无上次记录时返回 None
    """
    conn = _get_conn()
    try:
        current = conn.execute("SELECT pipeline_name FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not current:
            return None

        prev = conn.execute(
            "SELECT id FROM runs WHERE pipeline_name = ? AND id < ? ORDER BY id DESC LIMIT 1",
            (current["pipeline_name"], run_id),
        ).fetchone()
        if not prev:
            return None

        prev_id = prev["id"]

        # 当前失败的 case_id
        cur_failed = set(
            r[0] for r in conn.execute(
                "SELECT case_id FROM run_results WHERE run_id = ? AND passed = 0", (run_id,)
            ).fetchall()
        )
        # 上次失败的 case_id
        prev_failed = set(
            r[0] for r in conn.execute(
                "SELECT case_id FROM run_results WHERE run_id = ? AND passed = 0", (prev_id,)
            ).fetchall()
        )

        new_failures = sorted(cur_failed - prev_failed)
        new_passes = sorted(prev_failed - cur_failed)

        return {
            "previous_run_id": prev_id,
            "new_failures": new_failures,
            "new_passes": new_passes,
        }
    finally:
        conn.close()


def cleanup_old():
    """删除超过保留期限的历史记录。"""
    cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM runs WHERE started_at < ?", (cutoff,))
        deleted = cur.rowcount
        if deleted > 0:
            conn.commit()
            log.info("清理过期历史: 删除 %d 条记录 (早于 %s)", deleted, cutoff)
    finally:
        conn.close()


def get_summary_stats() -> dict:
    """获取汇总统计。

    Returns:
        {total_runs, total_tests, avg_pass_rate, recent_runs}
    """
    conn = _get_conn()
    try:
        total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        total_tests = conn.execute("SELECT COUNT(*) FROM run_results").fetchone()[0]
        avg_pass = conn.execute(
            "SELECT CASE WHEN COUNT(*) > 0 THEN CAST(SUM(passed) AS REAL) / COUNT(*) * 100 ELSE 0 END FROM run_results"
        ).fetchone()[0]
        recent = conn.execute(
            "SELECT pipeline_name, overall_passed, started_at FROM runs ORDER BY id DESC LIMIT 5"
        ).fetchall()

        return {
            "total_runs": total_runs,
            "total_tests": total_tests,
            "avg_pass_rate": round(avg_pass, 1),
            "recent_runs": [dict(r) for r in recent],
        }
    finally:
        conn.close()
