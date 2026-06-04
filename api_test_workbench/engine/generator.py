"""调用 AI API 生成测试用例 — 支持 DeepSeek 和 Anthropic"""

import json
import re
import os
from typing import Optional

import requests

from api_test_workbench.config.prompts import (
    SYSTEM_PROMPT, build_user_prompt,
    PIPELINE_SYSTEM_PROMPT, build_pipeline_user_prompt,
)
from api_test_workbench.engine.models import TestCase, Pipeline

# API Key 通过环境变量或 ~/.claude/credentials.json / settings.json 获取
_ANTHROPIC_API_KEY: Optional[str] = None


def _get_api_key() -> str:
    global _ANTHROPIC_API_KEY
    if _ANTHROPIC_API_KEY:
        return _ANTHROPIC_API_KEY

    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if key:
        _ANTHROPIC_API_KEY = key
        return key

    for path, key_names in [
        (os.path.expanduser("~/.claude/credentials.json"), ["apiKey", "anthropicApiKey"]),
        (os.path.expanduser("~/.claude/settings.json"), ["ANTHROPIC_AUTH_TOKEN"]),
    ]:
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                if path.endswith("settings.json"):
                    data = data.get("env", data)
                for name in key_names:
                    if data.get(name):
                        _ANTHROPIC_API_KEY = data[name]
                        return data[name]
        except Exception:
            continue

    raise RuntimeError(
        "未找到 API Key。请设置环境变量：\n"
        "  DeepSeek:  export DEEPSEEK_API_KEY=sk-xxx\n"
        "  Anthropic: export ANTHROPIC_API_KEY=sk-ant-api03-xxx\n"
        "或写入 ~/.claude/settings.json 的 env.ANTHROPIC_AUTH_TOKEN 字段"
    )


def _detect_provider(api_key: str) -> str:
    return "anthropic" if api_key.startswith("sk-ant") else "deepseek"


def _clean_json_response(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _call_deepseek(api_key: str, system_prompt: str, user_prompt: str, model: str) -> str:
    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
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
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model, max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
    except ImportError:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model, "max_tokens": 4096,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic API 返回 {resp.status_code}: {resp.text}")
        return resp.json()["content"][0]["text"]


def _call_ai(api_key: str, system_prompt: str, user_prompt: str, model: str) -> str:
    """统一的 AI 调用入口，自动选择 provider"""
    provider = _detect_provider(api_key)
    if provider == "deepseek":
        return _call_deepseek(api_key, system_prompt, user_prompt, model)
    return _call_anthropic(api_key, system_prompt, user_prompt, model)


def _parse_json_with_retry(api_key: str, raw_text: str, model: str) -> dict:
    """解析 AI 返回的 JSON，失败时自动重试修复"""
    cleaned = _clean_json_response(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass  # 第一次解析失败，尝试修复

    retry_prompt = f"以下内容不是合法 JSON，请修复并只输出 JSON：\n\n{cleaned}"
    try:
        raw = _call_ai(api_key, "你是一个 JSON 修复器。只输出 JSON，不要任何解释。", retry_prompt, model)
        return json.loads(_clean_json_response(raw))
    except (json.JSONDecodeError, Exception) as e:
        raise RuntimeError(f"AI 返回了非法 JSON，且修复失败: {e}\n原始内容: {cleaned[:500]}")


def _make_test_case(tc_data: dict) -> TestCase:
    """从 AI 返回的字典构建 TestCase 对象"""
    return TestCase(
        case_id=tc_data.get("case_id", ""),
        case_name=tc_data.get("case_name", ""),
        operation=tc_data.get("operation", "create"),
        category=tc_data.get("category", "positive"),
        input_data=tc_data.get("input_data", {}),
        expected_status_code=tc_data.get("expected_status_code", 200),
        expected_response_keys=tc_data.get("expected_response_keys", []),
        assertion_logic=tc_data.get("assertion_logic", ""),
        pre_condition=tc_data.get("pre_condition", ""),
        post_condition=tc_data.get("post_condition", ""),
        data_dependencies=tc_data.get("data_dependencies", {}),
    )


def generate_test_cases(
    field_requirements: str,
    api_url: str = "",
    method: str = "POST",
    model: str = "",
) -> list[TestCase]:
    """根据字段定义调用 AI API 生成测试用例列表"""
    api_key = _get_api_key()
    provider = _detect_provider(api_key)
    if not model:
        model = "claude-sonnet-4-20250514" if provider == "anthropic" else "deepseek-chat"

    user_prompt = build_user_prompt(field_requirements, api_url, method)
    raw = _call_ai(api_key, SYSTEM_PROMPT, user_prompt, model)
    data = _parse_json_with_retry(api_key, raw, model)

    return [_make_test_case(tc) for tc in data.get("test_cases", [])]


def generate_pipeline_test_cases(
    pipeline_description: str,
    pipeline: Pipeline,
    model: str = "",
    test_cases_per_step: int = 1,
) -> dict:
    """根据 Pipeline 描述调用 AI API 生成按步骤组织的测试用例。

    data_dependencies 存入 TestCase 对象，不再修改 pipeline 原始配置，
    由 runner 在执行时动态应用。
    """
    api_key = _get_api_key()
    provider = _detect_provider(api_key)
    if not model:
        model = "claude-sonnet-4-20250514" if provider == "anthropic" else "deepseek-chat"

    step_descriptions = [
        f"{s.name} — {s.config.method} {s.config.url}"
        for s in pipeline.steps
    ]

    user_prompt = build_pipeline_user_prompt(
        pipeline_description, step_descriptions, pipeline.steps,
        test_cases_per_step=test_cases_per_step,
    )

    raw = _call_ai(api_key, PIPELINE_SYSTEM_PROMPT, user_prompt, model)
    data = _parse_json_with_retry(api_key, raw, model)

    test_cases_by_step = {}
    for step_data in data.get("steps", []):
        step_idx = step_data.get("step_index", 0)
        test_cases_by_step[step_idx] = [
            _make_test_case(tc) for tc in step_data.get("test_cases", [])
        ]

    return test_cases_by_step
