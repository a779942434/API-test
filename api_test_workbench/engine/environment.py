"""环境/变量管理 — 支持 dev/staging/prod 多环境切换和 {{VAR}} 变量替换"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from api_test_workbench.engine.logger import setup_logger

log = setup_logger("environment")

# 环境存档目录
ENV_DIR = Path.home() / ".api_workbench_saves" / "environments"

# 匹配 {{VAR_NAME}} 环境变量（不能是 stepN.xxx 格式，那是步骤间数据绑定）
_ENV_VAR_RE = re.compile(r'\{\{(?!step\d+\.)(.+?)\}\}')


def _ensure_dir():
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    ENV_DIR.chmod(0o700)


# ── 变量解析 ──

def resolve_env_variables(template: Any, variables: dict[str, str]) -> Any:
    """递归扫描模板中的 {{VAR_NAME}} 占位符，用环境变量替换。

    规则：
    - {{stepN.response.path}} — **不处理**，留给 bindings.py 处理步骤间数据绑定
    - {{BASE_URL}}、{{TOKEN}} 等 — 替换为 variables 中对应的值
    - 未在 variables 中定义的占位符 → 保留原样（不报错，便于调试）

    Args:
        template: str / dict / list 任意嵌套结构
        variables: {"VAR_NAME": "value", ...}

    Returns:
        替换后的同类型对象
    """
    if isinstance(template, str):
        def _replacer(m):
            var_name = m.group(1).strip()
            if var_name in variables:
                return variables[var_name]
            # 未定义的变量保留原样，方便调试
            log.debug("未定义的环境变量: {{%s}}，保留原样", var_name)
            return m.group(0)
        return _ENV_VAR_RE.sub(_replacer, template)

    if isinstance(template, dict):
        return {k: resolve_env_variables(v, variables) for k, v in template.items()}

    if isinstance(template, list):
        return [resolve_env_variables(item, variables) for item in template]

    return template


# ── 环境 CRUD ──

def _env_path(name: str) -> Path:
    """环境配置文件路径"""
    safe = name.replace("/", "_").replace(" ", "_")
    return ENV_DIR / f"{safe}.json"


def load_environment(name: str) -> Optional[dict]:
    """加载指定环境配置，返回 dict 或 None"""
    _ensure_dir()
    path = _env_path(name)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("加载环境 %s 失败: %s", name, e)
        return None


def save_environment(name: str, base_url: str, variables: dict[str, str],
                     auth_endpoint: str = "", auth_body: dict = None) -> str:
    """保存环境配置，返回文件路径"""
    _ensure_dir()
    data = {
        "name": name,
        "base_url": base_url,
        "variables": variables,
        "auth_endpoint": auth_endpoint,
        "auth_body": auth_body or {},
        "updated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = _env_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)
    log.info("环境已保存: %s → %s", name, path)
    return str(path)


def delete_environment(name: str) -> bool:
    """删除环境配置"""
    path = _env_path(name)
    if path.exists():
        path.unlink()
        log.info("环境已删除: %s", name)
        return True
    return False


def list_environments() -> list[dict]:
    """列出所有已保存的环境"""
    _ensure_dir()
    envs = []
    for f in sorted(ENV_DIR.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        envs.append({
            "name": data.get("name", f.stem),
            "base_url": data.get("base_url", ""),
            "variables": data.get("variables", {}),
            "auth_endpoint": data.get("auth_endpoint", ""),
            "auth_body": data.get("auth_body", {}),
            "updated_at": data.get("updated_at", ""),
            "path": str(f),
        })
    return envs


def get_default_environments() -> list[dict]:
    """返回预置的默认环境模板（dev / staging / prod）"""
    return [
        {
            "name": "dev",
            "base_url": "http://localhost:8080",
            "variables": {"BASE": "http://localhost:8080", "HOST": "localhost"},
            "auth_endpoint": "",
            "auth_body": {},
        },
        {
            "name": "staging",
            "base_url": "https://staging.example.com",
            "variables": {"BASE": "https://staging.example.com", "HOST": "staging.example.com"},
            "auth_endpoint": "",
            "auth_body": {},
        },
        {
            "name": "prod",
            "base_url": "https://api.example.com",
            "variables": {"BASE": "https://api.example.com", "HOST": "api.example.com"},
            "auth_endpoint": "",
            "auth_body": {},
        },
    ]


def init_default_environments():
    """如果没有任何环境配置，自动创建默认的 dev/staging/prod"""
    _ensure_dir()
    existing = list_environments()
    if not existing:
        for env in get_default_environments():
            save_environment(
                name=env["name"],
                base_url=env["base_url"],
                variables=env["variables"],
                auth_endpoint=env.get("auth_endpoint", ""),
                auth_body=env.get("auth_body", {}),
            )
        log.info("已创建默认环境: dev, staging, prod")
