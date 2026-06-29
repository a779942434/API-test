"""数据绑定引擎 — 占位符解析 + 数据提取 + 依赖扫描 + 语义路径注册"""

import re
from typing import Any, Optional

from api_test_workbench.engine.models import DataBinding, PipelineContext
from api_test_workbench.engine.logger import setup_logger

log = setup_logger("bindings")

# 匹配 {{stepN.response.path.to.field}} 或 {{stepN.data.id}} 或 {{stepN.extract.FIELD_NAME}}
_STEP_PLACEHOLDER_RE = re.compile(r'\{\{step(\d+)\.(.+?)\}\}')

# ── 语义路径注册表（双层路径解析的核心）──────────────────────
# key: (step_type, semantic_name)
# value: 候选物理路径列表（按优先级）
SEMANTIC_PATH_REGISTRY: dict[tuple[str, str], list[str]] = {
    # ── 列表查询接口 ──
    ("list_query", "total"): [
        "response.data.total",
        "response.data.count",
        "response.data.pageInfo.total",
        "response.result.total",
        "response.total",
    ],
    ("list_query", "first_record_id"): [
        "response.data.records[0].id",
        "response.data.list[0].id",
        "response.data[0].id",
        "response.data.items[0].id",
        "response.result.records[0].id",
    ],
    ("list_query", "record_count"): [
        "response.data._count",
        "response.data.records._count",
        "response.data.list._count",
    ],

    # ── 创建接口 ──
    ("create", "new_record_id"): [
        "response.data.id",
        "response.data.recordId",
        "response.result.id",
        "response.data",
    ],

    # ── 通用字段 ──
    ("*", "total"): [
        "response.data.total",
        "response.total",
    ],
    ("*", "first_record_id"): [
        "response.data.records[0].id",
        "response.data[0].id",
        "response.data.list[0].id",
    ],
    ("*", "new_record_id"): [
        "response.data.id",
        "response.data",
    ],
}


def extract_value(data: dict, path: str) -> Any:
    """用点号路径 + 数组索引从嵌套 dict 中取值。

    示例：
        "data.id"           → data["data"]["id"]
        "data.items[0].id"  → data["data"]["items"][0]["id"]
        "data.items[2].id"  → data["data"]["items"][2]["id"]（指定位置）
        "data.items.random.id" → 随机选取一个元素的 id
        "code"              → data["code"]
    """
    import random as _random

    if not path:
        raise KeyError("empty path")

    parts = re.findall(r'[^.\[\]]+|(?<=\[)\d+(?=\])', path)
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, (list, tuple)) and part.lstrip('-').isdigit():
            current = current[int(part)]
        elif isinstance(current, (list, tuple)) and part == 'random' and len(current) > 0:
            current = _random.choice(current)
        else:
            raise KeyError(f"Cannot resolve path '{path}': key '{part}' not found")
    return current


def _path_exists(data: dict, path: str) -> bool:
    """检查路径是否在扁平化数据中存在"""
    try:
        extract_value(data, path)
        return True
    except (KeyError, IndexError, TypeError):
        return False


def _flatten_response(data, prefix: str = "response") -> dict[str, Any]:
    """将嵌套 JSON 响应扁平化为单层 dict（点号 key），并自动生成数组别名。

    支持 dict 和 list 类型的顶层数据。

    增强：空数组优雅降级 — 返回 _count=0 和 _empty=True，不抛异常。
    """
    import random as _random

    result = {}
    _array_lengths: dict[str, int] = {}

    def _flatten(obj, current_prefix):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flatten(v, f"{current_prefix}.{k}")
        elif isinstance(obj, list):
            _array_lengths[current_prefix] = len(obj)
            if len(obj) == 0:
                # 空数组优雅降级：标记为空，设置 _count=0
                result[f"{current_prefix}._empty"] = True
                result[f"{current_prefix}._count"] = 0
                return
            for i, item in enumerate(obj):
                _flatten(item, f"{current_prefix}[{i}]")
        else:
            result[current_prefix] = obj

    _flatten(data, prefix)

    # ── 生成数组别名 ──
    aliases = {}
    for key in list(result.keys()):
        m = re.match(r'^(.+)\[0\](.*)$', key)
        if m:
            alias = m.group(1) + m.group(2)
            if alias not in result:
                aliases[alias] = result[key]
    result.update(aliases)

    # ── 生成 _count 别名 ──
    for arr_path, length in _array_lengths.items():
        count_key = f"{arr_path}._count"
        if count_key not in result:
            result[count_key] = length

    # ── 生成 random 别名 ──
    random_aliases = {}
    for key in list(result.keys()):
        m = re.match(r'^(.+)\[0\](.+)$', key)
        if m:
            arr_path = m.group(1)
            suffix = m.group(2)
            if _array_lengths.get(arr_path, 0) > 1:
                random_key = f"{arr_path}.random{suffix}"
                if random_key not in result and random_key not in random_aliases:
                    random_aliases[random_key] = {
                        "_is_random": True,
                        "array_path": arr_path,
                        "suffix": suffix,
                    }
    result.update(random_aliases)

    # ── 生成包装段别名（records/list/items） ──
    wrapper_aliases = {}
    for key in list(result.keys()):
        m = re.match(r'^(.+)\[0\](.*)$', key)
        if m:
            pfx = m.group(1)
            sfx = m.group(2)
            for wrapper in ('records', 'list', 'items'):
                alias = f"{pfx}.{wrapper}[0]{sfx}"
                if alias not in result and alias not in wrapper_aliases:
                    wrapper_aliases[alias] = result[key]
    result.update(wrapper_aliases)

    return result


def _segments_to_path(segments: list[str]) -> str:
    """将段列表还原为点号+数组索引路径。"""
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
    """当精确路径未在扁平化数据中找到时，尝试通过移除包装段来匹配。"""
    segments = re.findall(r'[^.\[\]]+|(?<=\[)\d+(?=\])', path)
    if len(segments) <= 2:
        return None

    WRAPPER_NAMES = {'records', 'list', 'items', 'data', 'result', 'content', 'body'}

    for i, seg in enumerate(segments):
        if seg.isdigit() or seg == 'response':
            continue
        if seg not in WRAPPER_NAMES and not seg.endswith('s'):
            continue

        test_segments = segments[:i] + segments[i + 1:]
        test_path = _segments_to_path(test_segments)
        if test_path in step_data:
            log.debug("路径 '%s' 未找到，回退到 '%s'（移除包装段 '%s'）", path, test_path, seg)
            return test_path

    return None


def resolve_semantic_path(
    step_type: str,
    semantic_name: str,
    flat_data: dict[str, Any],
) -> Optional[str]:
    """根据步骤类型和语义字段名，在实际扁平化数据中找到第一个存在的物理路径。

    Args:
        step_type: 步骤类型 (extractor/mutation/verifier)
        semantic_name: 语义字段名 (total/first_record_id/new_record_id)
        flat_data: _flatten_response() 输出

    Returns:
        第一个存在的物理路径，或 None
    """
    # 类型映射：步骤类型 → 注册表键
    type_key_map = {
        "extractor": "list_query",
        "verifier": "list_query",
        "mutation": "create",
    }
    registry_type = type_key_map.get(step_type, "list_query")

    # 先查精确类型
    candidates = SEMANTIC_PATH_REGISTRY.get((registry_type, semantic_name), [])

    # 再查通配类型
    if not candidates:
        candidates = SEMANTIC_PATH_REGISTRY.get(("*", semantic_name), [])

    for path in candidates:
        if path in flat_data and flat_data[path] is not None:
            log.debug("语义路径 '%s' (%s) → 物理路径 '%s'", semantic_name, step_type, path)
            return path

    return None


def extract_semantic_value(
    step_type: str,
    semantic_name: str,
    original_response: dict,
    flat_data: Optional[dict[str, Any]] = None,
) -> Any:
    """提取语义字段的实际值。

    Args:
        step_type: 步骤类型
        semantic_name: 语义字段名
        original_response: 原始 API 响应
        flat_data: 预计算的扁平化数据（可选，不传则重新计算）

    Returns:
        字段值，或 None
    """
    if flat_data is None:
        flat_data = _flatten_response(original_response)

    path = resolve_semantic_path(step_type, semantic_name, flat_data)
    if path:
        try:
            return flat_data[path]
        except KeyError:
            pass

    return None


def resolve_placeholders(template: Any, context: PipelineContext) -> Any:
    """递归扫描模板中的所有 {{stepN.path}} 占位符，用上下文中的实际值替换。

    支持三种格式：
    1. {{stepN.response.data.id}} — 物理路径（旧格式）
    2. {{stepN.extract.total}} — 语义字段名（新格式，推荐）
    3. {{stepN.data.id}} — 简写格式（兼容）
    """
    import random as _random

    if isinstance(template, str):
        def _replacer(m):
            step_1based = int(m.group(1))
            path = m.group(2)
            step_index = step_1based - 1

            if step_index not in context.extracted_values:
                raise ValueError(
                    f"Cannot resolve '{m.group(0)}': "
                    f"no data from step {step_1based} (step index {step_index})"
                )

            step_data = context.extracted_values[step_index]

            # 被忽略的步骤引用
            if step_data.get("_ignored"):
                raise ValueError(
                    f"Cannot resolve '{m.group(0)}': "
                    f"step {step_1based} 已被忽略，无可用数据"
                )

            # ── 新格式：{{stepN.extract.FIELD_NAME}} ──
            if path.startswith("extract."):
                semantic_name = path[len("extract."):]
                # 在扁平化数据中查找该语义字段
                # 先查直接路径
                if f"extract.{semantic_name}" in step_data:
                    val = step_data[f"extract.{semantic_name}"]
                    return str(val) if val is not None else ""
                # 再查语义注册表
                step_type = step_data.get("_step_type", "*")
                flat_data = {k: v for k, v in step_data.items() if not k.startswith("_")}
                phys = resolve_semantic_path(step_type, semantic_name, flat_data)
                if phys and phys in step_data:
                    val = step_data[phys]
                    if isinstance(val, dict) and val.get("_is_random"):
                        arr_path = val["array_path"]
                        suffix = val["suffix"]
                        count = step_data.get(f"{arr_path}._count", 1)
                        idx = _random.randint(0, count - 1)
                        random_key = f"{arr_path}[{idx}]{suffix}"
                        if random_key in step_data:
                            return str(step_data[random_key])
                    return str(val) if val is not None else ""
                raise KeyError(
                    f"Cannot resolve '{m.group(0)}': semantic field '{semantic_name}' "
                    f"not found in step {step_1based} data"
                )

            # ── 旧格式：response.xxx 或 data.xxx ──
            if path in step_data:
                val = step_data[path]
                if isinstance(val, dict) and val.get("_is_random"):
                    arr_path = val["array_path"]
                    suffix = val["suffix"]
                    count = step_data.get(f"{arr_path}._count", 1)
                    idx = _random.randint(0, count - 1)
                    random_key = f"{arr_path}[{idx}]{suffix}"
                    log.debug("random 路径 '%s' 解析为 '%s' (0-%d)", path, random_key, count - 1)
                    if random_key in step_data:
                        return str(step_data[random_key])
                return str(val)

            # 尝试回退路径
            fallback = _try_fallback_path(path, step_data)
            if fallback is not None:
                val = step_data[fallback]
                if isinstance(val, dict) and val.get("_is_random"):
                    arr_path = val["array_path"]
                    suffix = val["suffix"]
                    count = step_data.get(f"{arr_path}._count", 1)
                    idx = _random.randint(0, count - 1)
                    random_key = f"{arr_path}[{idx}]{suffix}"
                    log.debug("random 回退路径 '%s' → '%s' 解析为 '%s'", path, fallback, random_key)
                    if random_key in step_data:
                        return str(step_data[random_key])
                return str(val)

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
    return "value"
