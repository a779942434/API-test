"""调用 AI API 生成测试用例 — 支持 DeepSeek 和 Anthropic"""

import json
import re
import os
from typing import Optional

import requests

from api_test_workbench.config.prompts import SYSTEM_PROMPT, build_user_prompt
from api_test_workbench.engine.models import TestCase

# Anthropic SDK 可选安装，优先使用直接 HTTP 调用（兼容 DeepSeek）
_ANTHROPIC_API_KEY: Optional[str] = "sk-2b3d7a1bcbc3450585ac0ac28dd008dd"
_API_PROVIDER: Optional[str] = None  # "anthropic" | "deepseek"


def _get_api_key() -> str:
    global _ANTHROPIC_API_KEY
    if _ANTHROPIC_API_KEY:
        return _ANTHROPIC_API_KEY

    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if key:
        _ANTHROPIC_API_KEY = key
        return key

    try:
        creds_path = os.path.expanduser("~/.claude/credentials.json")
        if os.path.exists(creds_path):
            with open(creds_path) as f:
                creds = json.load(f)
            key = creds.get("apiKey") or creds.get("anthropicApiKey")
            if key:
                _ANTHROPIC_API_KEY = key
                return key
    except Exception:
        pass

    raise RuntimeError(
        "未找到 API Key。请设置环境变量：\n"
        "  DeepSeek:  export DEEPSEEK_API_KEY=sk-xxx\n"
        "  Anthropic: export ANTHROPIC_API_KEY=sk-ant-api03-xxx"
    )


def _detect_provider(api_key: str) -> str:
    """根据 key 格式自动识别 API 提供商"""
    if api_key.startswith("sk-ant"):
        return "anthropic"
    return "deepseek"


def _clean_json_response(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _call_deepseek(api_key: str, system_prompt: str, user_prompt: str, model: str) -> str:
    """调用 DeepSeek API（OpenAI 兼容格式）"""
    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 4096,
            "temperature": 0.1,
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek API 返回 {resp.status_code}: {resp.text}")
    return resp.json()["choices"][0]["message"]["content"]


def _call_anthropic(api_key: str, system_prompt: str, user_prompt: str, model: str) -> str:
    """调用 Anthropic API"""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
    except ImportError:
        # 降级为直接 HTTP 调用 Anthropic Messages API
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic API 返回 {resp.status_code}: {resp.text}")
        data = resp.json()
        return data["content"][0]["text"]


def generate_test_cases(
    field_requirements: str,
    api_url: str = "",
    method: str = "POST",
    model: str = "",
) -> list[TestCase]:
    """根据字段定义调用 AI API 生成测试用例列表"""
    api_key = _get_api_key()
    provider = _detect_provider(api_key)

    # 根据 provider 设置默认模型
    if not model:
        model = "claude-sonnet-4-20250514" if provider == "anthropic" else "deepseek-chat"

    user_prompt = build_user_prompt(field_requirements, api_url, method)

    if provider == "deepseek":
        raw = _call_deepseek(api_key, SYSTEM_PROMPT, user_prompt, model)
    else:
        raw = _call_anthropic(api_key, SYSTEM_PROMPT, user_prompt, model)

    cleaned = _clean_json_response(raw)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 重试一次修复 JSON
        retry_prompt = f"以下内容不是合法 JSON，请修复并只输出 JSON：\n\n{cleaned}"
        if provider == "deepseek":
            raw = _call_deepseek(api_key, "你是一个 JSON 修复器。只输出 JSON，不要任何解释。", retry_prompt, model)
        else:
            raw = _call_anthropic(api_key, "你是一个 JSON 修复器。只输出 JSON，不要任何解释。", retry_prompt, model)
        data = json.loads(_clean_json_response(raw))

    test_cases = []
    for tc in data.get("test_cases", []):
        test_cases.append(TestCase(
            case_id=tc.get("case_id", ""),
            case_name=tc.get("case_name", ""),
            operation=tc.get("operation", "create"),
            category=tc.get("category", "positive"),
            input_data=tc.get("input_data", {}),
            expected_status_code=tc.get("expected_status_code", 200),
            expected_response_keys=tc.get("expected_response_keys", []),
            assertion_logic=tc.get("assertion_logic", ""),
            pre_condition=tc.get("pre_condition", ""),
            post_condition=tc.get("post_condition", ""),
        ))

    return test_cases
