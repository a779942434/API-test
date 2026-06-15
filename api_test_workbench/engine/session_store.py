"""工作台状态持久化 — 保存/加载到本地文件，超 3 天自动清理"""

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from api_test_workbench.engine.models import (
    ApiConfig, ApiStep, Pipeline, TestCase, PipelineResult,
)

SAVE_DIR = Path.home() / ".api_workbench_saves"
MAX_AGE_DAYS = 3


def _ensure_dir():
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    SAVE_DIR.chmod(0o700)  # 仅 owner 可访问，保护凭据


def _cleanup_old():
    """删除超过 MAX_AGE_DAYS 天的存档"""
    _ensure_dir()
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    for f in SAVE_DIR.glob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def _serialize_pipeline(pipeline: Pipeline) -> dict:
    def _step_to_dict(step: ApiStep) -> dict:
        return {
            "name": step.name,
            "on_failure": step.on_failure,
            "ignored": step.ignored,
            "config": {
                "url": step.config.url,
                "method": step.config.method,
                "headers": step.config.headers,
                "body_template": step.config.body_template,
            },
        }

    return {
        "name": pipeline.name,
        "steps": [_step_to_dict(s) for s in pipeline.steps],
    }


def _deserialize_pipeline(data: dict) -> Pipeline:
    steps = []
    for s in data.get("steps", []):
        cfg = s.get("config", {})
        step = ApiStep(
            name=s.get("name", ""),
            on_failure=s.get("on_failure", "stop"),
            ignored=s.get("ignored", False),
            config=ApiConfig(
                url=cfg.get("url", ""),
                method=cfg.get("method", "POST"),
                headers=cfg.get("headers", {"Content-Type": "application/json"}),
                body_template=cfg.get("body_template", {}),
            ),
        )
        steps.append(step)
    return Pipeline(name=data.get("name", "Pipeline"), steps=steps)


def _serialize_test_cases(tcs_by_step: dict) -> dict:
    """序列化 {step_idx: [TestCase, ...]}"""
    result = {}
    for idx, tcs in tcs_by_step.items():
        result[str(idx)] = [asdict(tc) for tc in tcs]
    return result


def _deserialize_test_cases(data: dict) -> dict:
    result = {}
    for k, v in data.items():
        result[int(k)] = [TestCase(**tc) for tc in v]
    return result


def save(pipeline: Pipeline, field_requirements: str,
         test_cases_by_step: dict, auth_url: str,
         auth_username: str = "", auth_password: str = "", auth_tenant_id: str = "",
         name: str = "") -> str:
    """保存当前工作台状态，返回文件路径"""
    _ensure_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = (name or pipeline.name or "session").replace("/", "_").replace(" ", "_")
    filename = f"{ts}_{safe_name}.json"
    filepath = SAVE_DIR / filename

    data = {
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pipeline": _serialize_pipeline(pipeline),
        "field_requirements": field_requirements,
        "pipeline_test_cases_by_step": _serialize_test_cases(test_cases_by_step),
        "auth_url": auth_url,
        "auth_username": auth_username,
        "auth_password": auth_password,
        "auth_tenant_id": auth_tenant_id,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(filepath, 0o600)  # 仅 owner 可读写
    return str(filepath)


def list_saves() -> list[dict]:
    """列出所有存档，返回 [{name, path, saved_at, pipeline_name}]"""
    _ensure_dir()
    _cleanup_old()
    saves = []
    for f in sorted(SAVE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        saves.append({
            "name": f.stem,
            "path": str(f),
            "saved_at": data.get("saved_at", "未知"),
            "pipeline_name": data.get("pipeline", {}).get("name", ""),
        })
    return saves


def load(filepath: str) -> Optional[dict]:
    """加载存档，返回 {pipeline, field_requirements, test_cases_by_step, auth_url, auth_username, auth_password, auth_tenant_id}"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # 兼容旧格式：如果存的是 auth_body JSON 字符串，从中提取 username/password
    username = data.get("auth_username", "")
    password = data.get("auth_password", "")
    if not username and not password:
        old_body = data.get("auth_body", "")
        if old_body:
            try:
                body = json.loads(old_body) if isinstance(old_body, str) else old_body
                username = body.get("username", "")
                password = body.get("password", "")
            except (json.JSONDecodeError, TypeError):
                pass

    return {
        "pipeline": _deserialize_pipeline(data.get("pipeline", {})),
        "field_requirements": data.get("field_requirements", ""),
        "pipeline_test_cases_by_step": _deserialize_test_cases(data.get("pipeline_test_cases_by_step", {})),
        "auth_url": data.get("auth_url", ""),
        "auth_username": username,
        "auth_password": password,
        "auth_tenant_id": data.get("auth_tenant_id", ""),
    }
