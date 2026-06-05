"""数据绑定引擎 — 占位符解析 + 数据提取 + 依赖扫描"""

import re
from typing import Any

from api_test_workbench.engine.models import DataBinding, PipelineContext

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


def _flatten_response(data: dict, prefix: str = "response") -> dict[str, Any]:
    """将嵌套 JSON 响应扁平化为单层 dict（点号 key），并自动生成数组别名。

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
    import re as _re
    for key in list(result.keys()):
        m = _re.match(r'^(.+)\[0\](.*)$', key)
        if m:
            alias = m.group(1) + m.group(2)  # 去掉 [0]
            if alias not in result:
                aliases[alias] = result[key]
    result.update(aliases)

    return result


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
