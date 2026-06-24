"""数据绑定引擎 — 占位符解析 + 数据提取 + 依赖扫描"""

import re
from typing import Any, Optional

from api_test_workbench.engine.models import DataBinding, PipelineContext
from api_test_workbench.engine.logger import setup_logger

log = setup_logger("bindings")

# 匹配 {{stepN.response.path.to.field}} 或 {{stepN.data.id}}
_STEP_PLACEHOLDER_RE = re.compile(r'\{\{step(\d+)\.(.+?)\}\}')


def extract_value(data: dict, path: str) -> Any:
    """用点号路径 + 数组索引从嵌套 dict 中取值。

    示例：
        "data.id"           → data["data"]["id"]
        "data.items[0].id"  → data["data"]["items"][0]["id"]
        "code"              → data["code"]
    """
    if not path:
        raise KeyError("empty path")

    # 按 . 或 [N] 分割路径
    parts = re.findall(r'[^.\[\]]+|(?<=\[)\d+(?=\])', path)
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip('-').isdigit():
            current = current[int(part)]
        else:
            raise KeyError(f"Cannot resolve path '{path}': key '{part}' not found")
    return current


def _flatten_response(data, prefix: str = "response") -> dict[str, Any]:
    """将嵌套 JSON 响应扁平化为单层 dict（点号 key），并自动生成数组别名。

    支持 dict 和 list 类型的顶层数据。

    返回示例：
        {"response.code": "0", "response.data[0].id": 123, "response.data.id": 123}
        （data 是数组时，data.id 自动别名到 data[0].id）
    """
    result = {}

    def _flatten(obj, current_prefix):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flatten(v, f"{current_prefix}.{k}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _flatten(item, f"{current_prefix}[{i}]")
        else:
            result[current_prefix] = obj

    _flatten(data, prefix)

    # 生成数组别名：response.data[0].id → response.data.id
    aliases = {}
    for key in list(result.keys()):
        m = re.match(r'^(.+)\[0\](.*)$', key)
        if m:
            alias = m.group(1) + m.group(2)  # 去掉 [0]
            if alias not in result:
                aliases[alias] = result[key]
    result.update(aliases)

    # 生成常见包装名别名：response.data[0].id → response.data.records[0].id
    # 当 API 返回 data 为数组但 AI 误加 records/list/items 包装段时，仍能精确匹配
    wrapper_aliases = {}
    for key in list(result.keys()):
        m = re.match(r'^(.+)\[0\](.*)$', key)
        if m:
            prefix = m.group(1)
            suffix = m.group(2)
            for wrapper in ('records', 'list', 'items'):
                alias = f"{prefix}.{wrapper}[0]{suffix}"
                if alias not in result and alias not in wrapper_aliases:
                    wrapper_aliases[alias] = result[key]
    result.update(wrapper_aliases)

    return result


def _segments_to_path(segments: list[str]) -> str:
    """将段列表还原为点号+数组索引路径。

    例如 ['response', 'data', '0', 'id'] → 'response.data[0].id'
    """
    if not segments:
        return ""
    result = segments[0]
    for seg in segments[1:]:
        if seg.isdigit() or (seg.startswith('-') and seg[1:].isdigit()):
            result += f"[{seg}]"
        else:
            result += f".{seg}"
    return result


def _try_fallback_path(path: str, step_data: dict[str, Any]) -> Optional[str]:
    """当精确路径未在扁平化数据中找到时，尝试通过移除包装段来匹配。

    例如 API 返回 data 为数组时，_flatten_response 生成 response.data[0].id，
    但 AI 可能生成 response.data.records[0].id（假设 {records:[...]} 包装）。
    此函数尝试移除 records/list/items 等中间段来找到匹配。

    Args:
        path: 原始路径，如 'response.data.records[0].id'
        step_data: 扁平化后的步骤数据 dict

    Returns:
        匹配的回退路径，或 None
    """
    segments = re.findall(r'[^.\[\]]+|(?<=\[)\d+(?=\])', path)
    if len(segments) <= 2:
        return None

    # 常见包装段名称（AI 可能误加的中间段）
    WRAPPER_NAMES = {'records', 'list', 'items', 'data', 'result', 'content', 'body'}

    for i, seg in enumerate(segments):
        # 跳过数字索引、response 前缀、以及不在包装名集合中的段
        if seg.isdigit() or seg == 'response':
            continue
        if seg not in WRAPPER_NAMES and not seg.endswith('s'):
            continue

        # 尝试移除这个段
        test_segments = segments[:i] + segments[i + 1:]
        test_path = _segments_to_path(test_segments)
        if test_path in step_data:
            log.debug("路径 '%s' 未找到，回退到 '%s'（移除包装段 '%s'）", path, test_path, seg)
            return test_path

    return None


def resolve_placeholders(template: Any, context: PipelineContext) -> Any:
    """递归扫描模板中的所有 {{stepN.path}} 占位符，用上下文中的实际值替换。

    template 可以是 str / dict / list — 会递归处理。
    context.extracted_values[step_index] = {"response.data.id": 123, ...}
    占位符格式：{{step1.response.data.id}}（1-based 用户索引）
    """
    if isinstance(template, str):
        def _replacer(m):
            step_1based = int(m.group(1))      # 用户输入的是 1-based
            path = m.group(2)
            step_index = step_1based - 1        # 内部存储为 0-based

            if step_index not in context.extracted_values:
                raise ValueError(
                    f"Cannot resolve '{m.group(0)}': "
                    f"no data from step {step_1based} (step index {step_index})"
                )

            step_data = context.extracted_values[step_index]

            if path in step_data:
                return str(step_data[path])

            # 尝试回退路径（处理 AI 生成路径与实际响应结构不匹配的情况）
            fallback = _try_fallback_path(path, step_data)
            if fallback is not None:
                return str(step_data[fallback])

            raise KeyError(
                f"Cannot resolve '{m.group(0)}': path '{path}' "
                f"not found in step {step_1based} data"
            )

        return _STEP_PLACEHOLDER_RE.sub(_replacer, template)

    if isinstance(template, dict):
        return {k: resolve_placeholders(v, context) for k, v in template.items()}

    if isinstance(template, list):
        return [resolve_placeholders(item, context) for item in template]

    return template


def scan_placeholders(template: Any, target_step_index: int) -> list[DataBinding]:
    """扫描模板中所有 {{stepN.path}} 占位符，返回 DataBinding 列表。

    仅用于 UI 展示"数据流概览"表格，不影响运行时执行。
    """
    bindings = []

    if isinstance(template, str):
        for m in _STEP_PLACEHOLDER_RE.finditer(template):
            step_1based = int(m.group(1))
            path = m.group(2)
            source_step = step_1based - 1

            # 推断注入位置类型
            location = _infer_location(template, m.group(0))

            bindings.append(DataBinding(
                source_step_index=source_step,
                source_field=path,
                target_step_index=target_step_index,
                target_location=location,
                placeholder=m.group(0),
            ))

    elif isinstance(template, dict):
        for key, value in template.items():
            bindings.extend(scan_placeholders(value, target_step_index))

    elif isinstance(template, list):
        for item in template:
            bindings.extend(scan_placeholders(item, target_step_index))

    return bindings


def _infer_location(template_str: str, placeholder: str) -> str:
    """推断占位符在模板字符串中的位置类型（启发式）"""
    # 简单判断：如果占位符在形如 "url": "{{...}}" 的上下文中
    # 实际使用时在 scan_placeholders 调用方通过上下文判断更准确
    return "value"
