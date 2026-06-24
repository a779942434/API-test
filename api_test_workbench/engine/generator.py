"""调用 AI API 生成测试用例 — 支持 DeepSeek 和 Anthropic"""

import json
import re
import os
import time
from typing import Optional

import requests

from api_test_workbench.config.prompts import (
    SYSTEM_PROMPT, build_user_prompt,
    PIPELINE_SYSTEM_PROMPT, build_pipeline_user_prompt,
)
from api_test_workbench.engine.models import TestCase, Pipeline
from api_test_workbench.engine.logger import setup_logger

log = setup_logger("generator")

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
    """从 AI 返回文本中提取 JSON 内容。

    按优先级尝试：
    1. 清理 BOM / 零宽空格等不可见字符
    2. 提取 ```json ... ``` 或 ``` ... ``` 代码块
    3. 如果代码块没有闭合的 ```，从开头 ``` 后截取
    4. 使用 json.JSONDecoder.raw_decode() 精确定位 JSON 边界
    5. 兜底：去掉首尾非 JSON 文字
    """
    if not text or not text.strip():
        return "{}"

    # ── 步骤 0: 清理不可见字符 ──
    text = text.strip()
    # BOM (U+FEFF)、零宽空格 (U+200B)、零宽不连字符 (U+200C)、零宽连字符 (U+200D)
    text = text.replace('﻿', '').replace('​', '').replace('‌', '').replace('‍', '')
    # 其他常见不可见字符
    text = text.replace(' ', ' ')  # 非断空格 → 普通空格

    # ── 步骤 1: 完整代码块 ──
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # ── 步骤 2: 不完整代码块 ──
    if text.startswith("```"):
        inner = re.sub(r"^```(?:json)?\s*\n?", "", text)
        inner = re.sub(r"\n?```\s*$", "", inner)
        text = inner.strip()

    # ── 步骤 3: 用 raw_decode 精确定位 JSON 边界 ──
    json_start = -1
    for i, ch in enumerate(text):
        if ch in ('{', '['):
            json_start = i
            break
    if json_start >= 0:
        candidate = text[json_start:]
        try:
            decoder = json.JSONDecoder()
            obj, end_idx = decoder.raw_decode(candidate)
            # raw_decode 成功 → 精确截取到 JSON 结束位置
            remaining = candidate[end_idx:].strip()
            if remaining:
                log.debug("raw_decode: JSON 后还有 %d 字符非 JSON 文本已丢弃", len(remaining))
            return candidate[:end_idx]
        except json.JSONDecodeError:
            # raw_decode 失败 → 回退到手动截取
            pass

    # ── 步骤 4: 兜底 — 手动去掉首尾非 JSON 文字 ──
    if json_start > 0:
        text = text[json_start:]

    json_end = -1
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ('}', ']'):
            json_end = i + 1
            break
    if json_end > 0 and json_end < len(text):
        text = text[:json_end]

    return text.strip()


def _retry_api_call(fn, max_retries: int = 3, backoff: float = 1.5):
    """指数退避重试包装器。对 429/5xx/网络错误自动重试。"""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return fn()
        except requests.exceptions.Timeout as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = backoff ** attempt
                log.warning("API 超时，%ds 后重试 (%d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
        except requests.exceptions.ConnectionError as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = backoff ** attempt
                log.warning("API 连接错误，%ds 后重试 (%d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
        except RuntimeError as e:
            msg = str(e)
            if "429" in msg or "503" in msg or "502" in msg:
                last_exc = e
                if attempt < max_retries - 1:
                    wait = backoff ** (attempt + 1)
                    log.warning("API 限流/服务不可用，%ds 后重试 (%d/%d): %s", wait, attempt + 1, max_retries, msg[:100])
                    time.sleep(wait)
            else:
                raise
        except Exception as e:
            # 捕获 Anthropic SDK 异常 (anthropic.APIStatusError 等) 和其他非标准异常
            msg = str(e)
            retryable = any(code in msg for code in ("429", "503", "502", "500", "rate_limit", "RateLimitError"))
            retryable = retryable or hasattr(e, 'status_code') and getattr(e, 'status_code', 200) in (429, 500, 502, 503)
            if retryable:
                last_exc = e
                if attempt < max_retries - 1:
                    wait = backoff ** (attempt + 1)
                    log.warning("API 错误(可重试)，%ds 后重试 (%d/%d): %s", wait, attempt + 1, max_retries, msg[:120])
                    time.sleep(wait)
            else:
                raise
    raise last_exc


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
            "max_tokens": 8192,  # DeepSeek 最大输出 8K tokens
            "temperature": 0.1,
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek API 返回 {resp.status_code}: {resp.text}")
    body = resp.json()
    choice = body["choices"][0]
    finish_reason = choice.get("finish_reason", "")
    content = choice["message"]["content"]
    if finish_reason == "length":
        log.warning("DeepSeek 输出被截断 (finish_reason=length)，返回内容可能不完整")
    return content


def _call_anthropic(api_key: str, system_prompt: str, user_prompt: str, model: str) -> str:
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model, max_tokens=16384,
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
                "model": model, "max_tokens": 16384,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic API 返回 {resp.status_code}: {resp.text}")
        return resp.json()["content"][0]["text"]


def _call_ai(api_key: str, system_prompt: str, user_prompt: str, model: str) -> str:
    """统一的 AI 调用入口，自动选择 provider 并带重试"""
    provider = _detect_provider(api_key)
    if provider == "deepseek":
        return _retry_api_call(lambda: _call_deepseek(api_key, system_prompt, user_prompt, model))
    return _retry_api_call(lambda: _call_anthropic(api_key, system_prompt, user_prompt, model))


def _parse_json_with_retry(api_key: str, raw_text: str, model: str) -> dict:
    """解析 AI 返回的 JSON，失败时多重降级修复。

    策略：
    1. 清洗文本 → json.loads()
    2. 失败 → json.JSONDecoder.raw_decode() 提取第一个完整 JSON 对象
    3. 再失败 → 把截断的 JSON（最后 8000 字符）+ 错误信息发给 AI 修复
    """
    cleaned = _clean_json_response(raw_text)

    # ── 策略 1: 直接解析 ──
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        first_error = str(e)

    # ── 策略 2: raw_decode 提取第一个完整 JSON 对象 ──
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(cleaned)
        if isinstance(obj, dict):
            log.info("raw_decode 成功提取第一个 JSON 对象")
            return obj
    except json.JSONDecodeError:
        pass

    # ── 策略 3: AI 修复 ──
    # 只发送最后 8000 字符（JSON 错误通常在尾部，前面的内容没必要全发）
    snippet = cleaned[-8000:] if len(cleaned) > 8000 else cleaned
    retry_prompt = (
        f"以下 JSON 无法解析，错误信息：{first_error}\n\n"
        f"请修复 JSON 语法错误（补全缺失的 }}、] 或逗号），只输出合法 JSON，不要任何解释：\n\n"
        f"{snippet}"
    )
    try:
        raw = _call_ai(api_key, "你是一个 JSON 修复器。只输出 JSON，不要任何解释。", retry_prompt, model)
        fixed = _clean_json_response(raw)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        # raw_decode 再试一次
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(fixed)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"AI 修复后仍无法解析\n修复结果: {fixed[:500]}")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"AI 返回了非法 JSON，且修复失败: {first_error}\n原始内容前500字符: {cleaned[:500]}")


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

    log.info("生成单接口测试用例: %s %s", method, api_url)
    user_prompt = build_user_prompt(field_requirements, api_url, method)
    raw = _call_ai(api_key, SYSTEM_PROMPT, user_prompt, model)
    data = _parse_json_with_retry(api_key, raw, model)
    test_cases = [_make_test_case(tc) for tc in data.get("test_cases", [])]
    log.info("生成完成: %d 条用例", len(test_cases))
    return test_cases


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

    log.info("生成 Pipeline 测试用例: %d 步, 每步 %d 条", len(pipeline.steps), test_cases_per_step)
    raw = _call_ai(api_key, PIPELINE_SYSTEM_PROMPT, user_prompt, model)
    data = _parse_json_with_retry(api_key, raw, model)

    test_cases_by_step = {}
    for idx, step_data in enumerate(data.get("steps", [])):
        # 用 AI 返回数组的位置作为步骤索引（0,1,2...），不依赖 AI 的 step_index 字段
        test_cases_by_step[idx] = [
            _make_test_case(tc) for tc in step_data.get("test_cases", [])
        ]
    total = sum(len(v) for v in test_cases_by_step.values())
    log.info("Pipeline 生成完成: %d 步, %d 条用例", len(test_cases_by_step), total)
    return test_cases_by_step
