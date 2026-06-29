"""调用 AI API 生成测试用例 — 支持 DeepSeek 和 Anthropic"""

from __future__ import annotations

import json
import re
import os
import time
from typing import Optional

import requests

from api_test_workbench.config.prompts import (
    SYSTEM_PROMPT, build_user_prompt,
    PIPELINE_SYSTEM_PROMPT, build_pipeline_user_prompt,
    _classify_steps, STEP_TYPE_RULES,
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
    text = text.replace('﻿', '').replace('​', '').replace('‌', '').replace('‍', '')
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
            remaining = candidate[end_idx:].strip()
            if remaining:
                log.debug("raw_decode: JSON 后还有 %d 字符非 JSON 文本已丢弃", len(remaining))
            return candidate[:end_idx]
        except json.JSONDecodeError:
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
            if "finish_reason=length" in msg or "截断" in msg:
                raise
            if "429" in msg or "503" in msg or "502" in msg:
                last_exc = e
                if attempt < max_retries - 1:
                    wait = backoff ** (attempt + 1)
                    log.warning("API 限流/服务不可用，%ds 后重试 (%d/%d): %s", wait, attempt + 1, max_retries, msg[:100])
                    time.sleep(wait)
            else:
                raise
        except Exception as e:
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
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 8192,  # deepseek-chat 实际上限，设更高会被静默截断
        "temperature": 0.1,
    }
    # JSON Mode：约束模型输出合法 JSON（deepseek-chat 支持）
    # 注意：开启后 prompt 中必须包含 "json" 字样，否则可能报错
    if "json" in system_prompt.lower() + user_prompt.lower():
        payload["response_format"] = {"type": "json_object"}

    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    if resp.status_code != 200:
        # 如果 JSON Mode 不被支持，自动回退重试
        if "response_format" in payload and resp.status_code == 400:
            log.warning("JSON Mode 不被支持，回退到普通模式")
            del payload["response_format"]
            resp = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=180,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"DeepSeek API 返回 {resp.status_code}: {resp.text}")
        else:
            raise RuntimeError(f"DeepSeek API 返回 {resp.status_code}: {resp.text}")
    body = resp.json()
    choice = body["choices"][0]
    finish_reason = choice.get("finish_reason", "")
    usage = body.get("usage", {})
    content = choice["message"]["content"]

    # 详细记录，便于诊断截断原因
    log.info("DeepSeek finish_reason=%s completion_tokens=%s content_len=%d",
             finish_reason, usage.get("completion_tokens", "?"), len(content))

    if finish_reason in ("length", "content_filter"):
        log.warning("DeepSeek 输出异常终止 (finish_reason=%s, %d 字符)，将尝试续写", finish_reason, len(content))
        return content + "\n\n__TRUNCATED__"
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
        for block in response.content:
            if hasattr(block, 'text') and block.text:
                return block.text
        return str(response.content[0]) if response.content else ""
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
        for block in resp.json().get("content", []):
            if block.get("type") == "text" and block.get("text"):
                return block["text"]
        return resp.json()["content"][0].get("text", "")


def _looks_truncated(text: str) -> bool:
    """检查文本是否看起来像不完整的 JSON（通过括号/引号平衡判断）。"""
    stripped = text.strip()
    if not stripped:
        return False
    # 不是 JSON 开头就不检查
    if stripped[0] not in '{[':
        return False
    # 简单括号计数
    brace_depth = 0
    bracket_depth = 0
    in_string = False
    escape = False
    for ch in stripped:
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
        elif ch == '[':
            bracket_depth += 1
        elif ch == ']':
            bracket_depth -= 1
    # 括号未闭合 或 还在字符串中 → 截断
    return brace_depth != 0 or bracket_depth != 0 or in_string


def _call_ai(api_key: str, system_prompt: str, user_prompt: str, model: str) -> tuple[str, bool]:
    """统一的 AI 调用入口，自动选择 provider 并带重试。

    Returns:
        (content, was_truncated) — was_truncated 表示输出可能不完整（需续写或修复）
    """
    provider = _detect_provider(api_key)
    if provider == "deepseek":
        raw = _retry_api_call(lambda: _call_deepseek(api_key, system_prompt, user_prompt, model))
    else:
        raw = _retry_api_call(lambda: _call_anthropic(api_key, system_prompt, user_prompt, model))
    was_truncated = "__TRUNCATED__" in raw
    if was_truncated:
        raw = raw.replace("\n\n__TRUNCATED__", "").replace("__TRUNCATED__", "")

    # 增强检测：即使 finish_reason 不是 length/content_filter，
    # 如果 JSON 结构不完整（括号未闭合），也标记为截断
    if not was_truncated and _looks_truncated(raw):
        log.warning("JSON 结构不完整（括号未闭合/字符串中断），标记为截断")
        was_truncated = True

    return raw, was_truncated


def _try_recover_truncated_json(text: str) -> str | None:
    """尝试从被截断的 JSON 中恢复。

    通过逐步截断尾部 + 补齐括号来找到第一个可解析的有效 JSON。

    Returns:
        修复后的 JSON 字符串，或 None（无法修复）
    """
    if not text or len(text) < 10:
        return None

    # ── 策略 A: 对截断文本逐步回溯，找到第一个可解析的子串 ──

    # 统计括号差值
    brace_delta = 0
    bracket_delta = 0
    for ch in text:
        if ch == '{': brace_delta += 1
        elif ch == '}': brace_delta -= 1
        elif ch == '[': bracket_delta += 1
        elif ch == ']': bracket_delta -= 1

    # 如果 JSON 结构完整（括号平衡），说明问题不在截断
    if brace_delta <= 0 and bracket_delta <= 0:
        return None

    # 从尾部开始逐步截断，每次截一个字符
    max_truncate = min(len(text) // 2, 5000)  # 最多截断一半
    for cut in range(0, max_truncate):
        candidate = text[:len(text) - cut] if cut > 0 else text

        # 跳过尾部的空白和逗号
        candidate = candidate.rstrip()
        if candidate.endswith(','):
            candidate = candidate[:-1].rstrip()

        # 检测未闭合的字符串：统计引号数量的奇偶性
        # 如果是奇数个引号，尝试补一个引号来闭合
        quote_count = candidate.count('"')
        if quote_count % 2 == 1:
            candidate = candidate + '"'

        # 如果末尾是冒号，说明键值对不完整，移除到上一个逗号
        if candidate.endswith(':'):
            last_comma = candidate.rfind(',')
            if last_comma > 0:
                candidate = candidate[:last_comma]

        # 补齐括号
        cur_brace = 0
        cur_bracket = 0
        for ch in candidate:
            if ch == '{': cur_brace += 1
            elif ch == '}': cur_brace -= 1
            elif ch == '[': cur_bracket += 1
            elif ch == ']': cur_bracket -= 1

        if cur_brace > 0 or cur_bracket > 0:
            closers = ']' * max(0, cur_bracket) + '}' * max(0, cur_brace)
            candidate = candidate + closers

        # 尝试解析
        try:
            json.loads(candidate)
            log.debug("截断恢复成功: 截断 %d 字符, 补齐 %d} %d]",
                      cut, max(0, cur_brace), max(0, cur_bracket))
            return candidate
        except json.JSONDecodeError:
            continue

    return None


def _parse_json_with_retry(api_key: str, raw_text: str, model: str) -> dict:
    """解析 AI 返回的 JSON，失败时多重降级修复。"""
    cleaned = _clean_json_response(raw_text)

    # ── 策略 1: 直接解析 ──
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        first_error = str(e)
        log.debug("JSON 直接解析失败: %s", first_error[:120])

    # ── 策略 2: raw_decode 提取第一个完整 JSON 对象 ──
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(cleaned)
        if isinstance(obj, dict):
            log.info("raw_decode 成功提取第一个 JSON 对象")
            return obj
    except json.JSONDecodeError:
        pass

    # ── 策略 2.5: 截断恢复 — 找到最后一个有效的闭合位置，补全缺失的括号 ──
    if "Unterminated string" in first_error or "Expecting" in first_error or "end of file" in first_error.lower():
        recovered = _try_recover_truncated_json(cleaned)
        if recovered:
            try:
                return json.loads(recovered)
            except json.JSONDecodeError:
                try:
                    decoder = json.JSONDecoder()
                    obj, _ = decoder.raw_decode(recovered)
                    if isinstance(obj, dict):
                        log.info("截断恢复成功（raw_decode）")
                        return obj
                except json.JSONDecodeError:
                    pass
            log.warning("截断恢复失败，回退到 AI 修复")

    # ── 策略 2.6: json_repair 库确定性修复（替代 AI 修复，零 token 消耗）──
    try:
        from json_repair import repair_json
        repaired = repair_json(cleaned, return_objects=False)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass
        try:
            decoder = json.JSONDecoder()
            obj, _ = decoder.raw_decode(repaired)
            if isinstance(obj, dict):
                log.info("json_repair 修复成功")
                return obj
        except json.JSONDecodeError:
            pass
        log.warning("json_repair 修复失败，回退到 AI 修复")
    except ImportError:
        pass
    except Exception as e:
        log.debug("json_repair 异常: %s", e)

    # ── 策略 3: AI 修复 ──
    max_len = 8000
    if len(cleaned) > max_len:
        half = max_len // 2
        snippet = cleaned[:half] + "\n... (省略中间部分) ...\n" + cleaned[-half:]
    else:
        snippet = cleaned
    retry_prompt = (
        f"以下 JSON 无法解析，错误信息：{first_error}\n\n"
        f"请修复 JSON 语法错误（补全缺失的 }}、] 或逗号），只输出合法 JSON，不要任何解释：\n\n"
        f"{snippet}"
    )
    try:
        raw, _ = _call_ai(api_key, "你是一个 JSON 修复器。只输出 JSON，不要任何解释。", retry_prompt, model)
        fixed = _clean_json_response(raw)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
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
        assertions=tc_data.get("assertions", []),
        extract_fields=tc_data.get("extract_fields", []),
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
    raw, _ = _call_ai(api_key, SYSTEM_PROMPT, user_prompt, model)
    data = _parse_json_with_retry(api_key, raw, model)
    test_cases = [_make_test_case(tc) for tc in data.get("test_cases", [])]
    log.info("生成完成: %d 条用例", len(test_cases))
    return test_cases


def _calc_expected_total(step_classifications: list[str]) -> int:
    """根据步骤分类计算期望用例总数"""
    return sum(STEP_TYPE_RULES[t]["count"] for t in step_classifications)


def _build_continuation_prompt(
    original_user_prompt: str,
    current_by_step: dict[int, int],
    expected_by_step: dict[int, int],
    reason: str = "truncated",
) -> str:
    """构造续写/补充 prompt"""
    missing_steps = {
        i: expected_by_step[i] - current_by_step.get(i, 0)
        for i in expected_by_step
        if current_by_step.get(i, 0) < expected_by_step[i]
    }

    if reason == "truncated":
        return (
            f"你的上一次 JSON 输出被 token 上限截断了。\n"
            f"当前各步骤已有/需要用例数: { {i: f'{current_by_step.get(i, 0)}/{expected_by_step[i]}' for i in expected_by_step} }\n"
            f"请**只输出**剩余缺失的 test_cases（不要重复已生成的），"
            f"格式同上：{{\"steps\": [{{\"test_cases\": [...]}}]}}\n"
            f"只输出合法 JSON，不要解释。"
        )
    else:
        return (
            f"当前只生成了 {sum(current_by_step.values())} 条用例，"
            f"不足预期 {sum(expected_by_step.values())} 条。\n"
            f"原因：{reason}\n"
            f"各步骤还需补充: {missing_steps}\n"
            f"请**只输出**缺失步骤的 test_cases，"
            f"特别注意：extractor 步骤要生成恰好 {STEP_TYPE_RULES['extractor']['count']} 条、"
            f"verifier 步骤要生成恰好 {STEP_TYPE_RULES['verifier']['count']} 条。\n"
            f"格式同上：{{\"steps\": [{{\"test_cases\": [...]}}]}}\n"
            f"只输出合法 JSON，不要解释。"
        )


def _plan_batches(
    step_classifications: list[str],
    test_cases_per_step: int,
) -> list[list[tuple[int, int]]]:
    """将步骤拆分为多个批次，避免单次 AI 调用输出过大被截断。

    策略：
    - extractor + verifier（轻量，2-5条）→ 合并为一批
    - mutation（重量，10条）→ 每步单独一批，超过6条拆为子批

    Args:
        step_classifications: 每步的类型 ["extractor", "mutation", ...]
        test_cases_per_step: 用户选择的每步用例数

    Returns:
        [[(0, 2), (2, 5), (5, 5)], [(1, 5)], [(1, 5)], [(3, 5)], [(3, 5)]]
        每个子列表是一批，每个 tuple 是 (step_index, case_count)
    """
    expected_total = _calc_expected_total(step_classifications)

    # 总量小就不分批
    if expected_total <= 20:
        return [[(i, STEP_TYPE_RULES[step_classifications[i]]["count"]) for i in range(len(step_classifications))]]

    MAX_PER_CALL = 3  # 单次 API 调用最多生成的用例数（deepseek-chat 容易提前停止，降低到 3）

    batches = []
    light_steps = []  # extractor + verifier，轻量

    for i, stype in enumerate(step_classifications):
        count = STEP_TYPE_RULES[stype]["count"]

        if stype == "mutation":
            if count > MAX_PER_CALL:
                # 拆分重量步骤为多个子批
                remaining = count
                while remaining > 0:
                    chunk = min(remaining, MAX_PER_CALL)
                    batches.append([(i, chunk)])
                    remaining -= chunk
            else:
                batches.append([(i, count)])
        else:
            light_steps.append((i, count))

    if light_steps:
        batches.insert(0, light_steps)

    return batches


def _generate_single_batch(
    api_key: str,
    model: str,
    pipeline_description: str,
    pipeline_steps: list,
    step_classifications: list[str],
    batch_step_specs: list[tuple[int, int]],
    batch_label: str = "",
) -> dict[int, list]:
    """为指定步骤子集生成测试用例（支持子批拆分）。

    Args:
        batch_step_specs: [(step_index, case_count), ...]
            例: [(0, 2), (2, 5)] 表示 step0 生成 2 条, step2 生成 5 条

    Returns:
        {step_index: [TestCase, ...]}
    """
    from api_test_workbench.config.prompts import build_pipeline_user_prompt, PIPELINE_SYSTEM_PROMPT

    step_indices = [s[0] for s in batch_step_specs]
    case_counts = [s[1] for s in batch_step_specs]
    batch_expected = sum(case_counts)

    batch_steps = [pipeline_steps[i] for i in step_indices]
    # 构建临时分类（基于实际数量）
    batch_classifications = []
    for spec in batch_step_specs:
        orig_type = step_classifications[spec[0]]
        if spec[1] <= STEP_TYPE_RULES["extractor"]["count"]:
            batch_classifications.append("extractor")
        elif spec[1] <= STEP_TYPE_RULES["verifier"]["count"]:
            batch_classifications.append("verifier")
        else:
            batch_classifications.append("mutation")

    step_descriptions = [
        f"{pipeline_steps[i].name} — {pipeline_steps[i].config.method} {pipeline_steps[i].config.url}"
        for i in step_indices
    ]

    # 用实际数量覆盖 STEP_TYPE_RULES 中的默认值
    temp_rules = {
        k: {**v} for k, v in STEP_TYPE_RULES.items()
    }
    for i, count in enumerate(case_counts):
        stype = batch_classifications[i]
        temp_rules[stype] = {**temp_rules[stype], "count": count}

    # 临时替换 STEP_TYPE_RULES（在 build_pipeline_user_prompt 中会用到）
    import api_test_workbench.config.prompts as prompt_mod
    original_rules = prompt_mod.STEP_TYPE_RULES
    prompt_mod.STEP_TYPE_RULES = temp_rules

    try:
        user_prompt = build_pipeline_user_prompt(
            pipeline_description, step_descriptions, batch_steps,
            test_cases_per_step=max(case_counts),
            step_classifications=batch_classifications,
        )
    finally:
        prompt_mod.STEP_TYPE_RULES = original_rules

    user_prompt += (
        f"\n\n## 批次提示\n"
        f"本次只生成以下 {len(step_indices)} 个步骤的测试用例"
        f"（原始步骤编号: {[s+1 for s in step_indices]}），共 {batch_expected} 条。\n"
        f"各步骤期望用例数: {dict(zip([s+1 for s in step_indices], case_counts))}\n"
        f"不要生成其他步骤的用例。只输出 JSON，不要解释。"
    )

    log.info("  [%s] 生成 %d 步, 期望 %d 条", batch_label, len(step_indices), batch_expected)

    raw, was_truncated = _call_ai(api_key, PIPELINE_SYSTEM_PROMPT, user_prompt, model)
    data = _parse_json_with_retry(api_key, raw, model)

    result = _extract_cases_from_response(data, len(batch_steps))

    # 映射回原始步骤索引
    remapped = {}
    for local_idx, (orig_idx, expected_count) in enumerate(batch_step_specs):
        if local_idx in result:
            cases = result[local_idx]
            if orig_idx in remapped:
                remapped[orig_idx].extend(cases[:expected_count])
            else:
                remapped[orig_idx] = cases[:expected_count]

    actual = sum(len(v) for v in remapped.values())
    log.info("  [%s] 完成: %d 条", batch_label, actual)

    # 如果截断或数量不足，尝试续写
    if (was_truncated or actual < batch_expected * 0.8) and actual < batch_expected:
        expected_by_step = {orig_idx: expected_count for orig_idx, expected_count in batch_step_specs}
        current_by_step = {i: len(remapped.get(i, [])) for i, _ in batch_step_specs}

        # 恢复临时 rules 用于续写
        prompt_mod.STEP_TYPE_RULES = temp_rules
        try:
            continuation_prompt = _build_continuation_prompt(
                user_prompt, current_by_step, expected_by_step,
                "output_truncated" if was_truncated else f"数量不足({actual}/{batch_expected})"
            )
            cont_raw, _ = _call_ai(api_key, PIPELINE_SYSTEM_PROMPT, continuation_prompt, model)
            cont_data = _parse_json_with_retry(api_key, cont_raw, model)
            cont_result = _extract_cases_from_response(cont_data, len(batch_steps))
            for local_idx, (orig_idx, expected_count) in enumerate(batch_step_specs):
                if local_idx in cont_result and orig_idx in remapped:
                    needed = expected_count - len(remapped[orig_idx])
                    if needed > 0:
                        remapped[orig_idx].extend(cont_result[local_idx][:needed])
            log.info("  [%s] 续写完成: 总计 %d 条", batch_label, sum(len(v) for v in remapped.values()))
        except Exception as e:
            log.warning("  [%s] 续写失败: %s", batch_label, str(e))
        finally:
            prompt_mod.STEP_TYPE_RULES = original_rules

    return remapped


def generate_pipeline_test_cases(
    pipeline_description: str,
    pipeline: Pipeline,
    model: str = "",
    test_cases_per_step: int = 1,
) -> dict:
    """根据 Pipeline 描述调用 AI API 生成按步骤组织的测试用例。

    改进点：
    1. 代码预分类步骤类型，不再依赖 AI 判断
    2. 分批生成：总量 > 20 时自动拆分，避免单次输出过大被截断
    3. 双重触发续写：截断 OR 实际数 < 期望数*80%
    4. 支持新的 assertions 数组格式
    """
    api_key = _get_api_key()
    provider = _detect_provider(api_key)
    if not model:
        model = "claude-sonnet-4-20250514" if provider == "anthropic" else "deepseek-chat"

    num_steps = len(pipeline.steps)

    # 代码预分类步骤类型
    step_classifications = _classify_steps(pipeline_description, pipeline.steps)
    expected_total = _calc_expected_total(step_classifications)
    expected_by_step = {i: STEP_TYPE_RULES[step_classifications[i]]["count"] for i in range(num_steps)}

    # ── 分批策略 ──
    batches = _plan_batches(step_classifications, test_cases_per_step)

    if len(batches) == 1:
        # 单批次：走原有全量生成路径
        log.info("生成 Pipeline 测试用例: %d 步, 期望 %d 条 (分类: %s)",
                 num_steps, expected_total, ", ".join(step_classifications))

        step_descriptions = [
            f"{s.name} — {s.config.method} {s.config.url}"
            for s in pipeline.steps
        ]
        user_prompt = build_pipeline_user_prompt(
            pipeline_description, step_descriptions, pipeline.steps,
            test_cases_per_step=test_cases_per_step,
            step_classifications=step_classifications,
        )

        raw, was_truncated = _call_ai(api_key, PIPELINE_SYSTEM_PROMPT, user_prompt, model)
        data = _parse_json_with_retry(api_key, raw, model)
        test_cases_by_step = _extract_cases_from_response(data, num_steps)

        # 双重触发续写
        actual_total = sum(len(v) for v in test_cases_by_step.values())
        if (was_truncated or actual_total < expected_total * 0.8) and actual_total < expected_total:
            test_cases_by_step = _do_continuation(
                api_key, model, user_prompt, test_cases_by_step,
                expected_by_step, was_truncated, actual_total, expected_total
            )
    else:
        # 多批次：逐步生成，每批处理少量步骤
        batch_descriptions = []
        for batch_specs in batches:
            parts = []
            batch_total = 0
            for step_idx, count in batch_specs:
                stype = step_classifications[step_idx]
                parts.append(f"Step{step_idx+1}({stype})×{count}")
                batch_total += count
            batch_descriptions.append(f"{'+'.join(parts)}={batch_total}条")
        log.info("分批生成 Pipeline: %d 步 → %d 批 (期望 %d 条): %s",
                 num_steps, len(batches), expected_total, " | ".join(batch_descriptions))

        test_cases_by_step = {}
        for batch_idx, batch_specs in enumerate(batches):
            batch_label = f"Batch{batch_idx+1}/{len(batches)}"
            batch_result = _generate_single_batch(
                api_key, model,
                pipeline_description, pipeline.steps,
                step_classifications, batch_specs,
                batch_label,
            )
            # 合并结果（同名步骤追加）
            for step_idx, cases in batch_result.items():
                if step_idx in test_cases_by_step:
                    test_cases_by_step[step_idx].extend(cases)
                else:
                    test_cases_by_step[step_idx] = cases

    # ── 最终统计 ──
    actual_total = sum(len(v) for v in test_cases_by_step.values())

    if actual_total < expected_total:
        shortfall_steps = {
            i: f"{len(test_cases_by_step.get(i, []))}/{expected_by_step[i]}"
            for i in range(num_steps)
            if len(test_cases_by_step.get(i, [])) < expected_by_step[i]
        }
        log.warning("AI 未生成足够用例: 期望 %d 条, 实际 %d 条, 不足步骤: %s",
                     expected_total, actual_total, shortfall_steps)

    log.info("Pipeline 生成完成: %d 步, %d 条用例 (分成 %d 批)",
             len(test_cases_by_step), actual_total, len(batches))
    return test_cases_by_step


def _do_continuation(
    api_key: str,
    model: str,
    user_prompt: str,
    test_cases_by_step: dict[int, list],
    expected_by_step: dict[int, int],
    was_truncated: bool,
    actual_total: int,
    expected_total: int,
) -> dict[int, list]:
    """执行续写循环，最多 3 轮"""
    num_steps = len(expected_by_step)
    current_by_step = {i: len(test_cases_by_step.get(i, [])) for i in range(num_steps)}

    for round_idx in range(3):
        if sum(len(v) for v in test_cases_by_step.values()) >= expected_total:
            break

        missing = expected_total - sum(len(v) for v in test_cases_by_step.values())
        reason = "output_truncated" if was_truncated else f"数量不足（{actual_total}/{expected_total}）"

        log.warning("续写第%d轮: 缺失 %d 条, 原因: %s", round_idx + 1, missing, reason)

        continuation_prompt = _build_continuation_prompt(
            user_prompt, current_by_step, expected_by_step, reason
        )
        try:
            cont_raw, cont_was_truncated = _call_ai(
                api_key, PIPELINE_SYSTEM_PROMPT, continuation_prompt, model
            )
            cont_data = _parse_json_with_retry(api_key, cont_raw, model)
            cont_cases = _extract_cases_from_response(cont_data, num_steps)

            for step_idx, tcs in cont_cases.items():
                if step_idx in test_cases_by_step:
                    existing = test_cases_by_step[step_idx]
                    needed = expected_by_step.get(step_idx, 0) - len(existing)
                    if needed > 0:
                        existing.extend(tcs[:needed])
                else:
                    test_cases_by_step[step_idx] = tcs[:expected_by_step.get(step_idx, 0)]

            actual_total = sum(len(v) for v in test_cases_by_step.values())
            current_by_step = {i: len(test_cases_by_step.get(i, [])) for i in range(num_steps)}
            log.info("续写第%d轮完成: 总计 %d 条用例", round_idx + 1, actual_total)
            was_truncated = cont_was_truncated
        except Exception as e:
            log.warning("续写第%d轮失败: %s", round_idx + 1, str(e))
            break

    return test_cases_by_step


def _extract_cases_from_response(data: dict, num_steps: int) -> dict[int, list]:
    """从 AI 返回的 JSON 中提取用例，按步骤索引分组。

    兼容新旧两种格式：
    - 新格式：steps[i] 包含 step_type 字段
    - 旧格式：steps[i] 仅有 test_cases
    """
    test_cases_by_step = {}
    for idx, step_data in enumerate(data.get("steps", [])):
        if idx >= num_steps:
            log.warning("AI 返回了 %d 个步骤，期望 %d 个，忽略多余步骤", len(data["steps"]), num_steps)
            break
        test_cases_by_step[idx] = [
            _make_test_case(tc) for tc in step_data.get("test_cases", [])
        ]
    return test_cases_by_step
