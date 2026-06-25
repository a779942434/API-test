"""Schema 响应校验 — 自动校验 API 响应体是否符合 OpenAPI Schema。

无需手写字段断言，Schema 导入后自动生效。
校验包括：字段存在性、类型匹配、必填检查、枚举值范围。
"""

import re
from typing import Any, Optional

from api_test_workbench.engine.logger import setup_logger

log = setup_logger("schema_validator")


def validate_response(response_body: Any, schema: dict, path: str = "$") -> list[str]:
    """校验响应体是否符合 Schema，返回错误列表（空列表 = 通过）。

    Args:
        response_body: API 响应体（dict / list / 标量）
        schema: OpenAPI/JJSON Schema 定义
        path: 当前校验路径（用于错误定位，如 "$.data.items[0].id"）

    Returns:
        错误信息列表，每条描述一个校验失败项。空列表表示完全通过。

    示例:
        >>> schema = {"type": "object", "properties": {"code": {"type": "string"}}}
        >>> validate_response({"code": 0}, schema)
        ["$.code: 期望类型 string，实际为 int"]
    """
    if not schema or not isinstance(schema, dict):
        return []

    errors = []
    schema_type = schema.get("type", "")

    # ── 类型校验 ──
    actual_type = _get_type(response_body)

    if schema_type:
        if not _type_matches(response_body, schema_type):
            errors.append(f"{path}: 期望类型 {schema_type}，实际为 {actual_type}")
            return errors  # 类型不匹配时不再深入校验子属性

    # ── object 属性校验 ──
    if schema_type == "object" or ("properties" in schema and isinstance(response_body, dict)):
        errors.extend(_validate_object(response_body, schema, path))

    # ── array 元素校验 ──
    if schema_type == "array" or ("items" in schema and isinstance(response_body, list)):
        errors.extend(_validate_array(response_body, schema, path))

    # ── string 枚举校验 ──
    if schema_type == "string" and isinstance(response_body, str):
        enum = schema.get("enum", [])
        if enum and response_body not in enum:
            errors.append(f"{path}: 值 '{response_body}' 不在枚举 {enum} 中")

    # ── number 范围校验 ──
    if schema_type in ("integer", "number") and isinstance(response_body, (int, float)):
        if "minimum" in schema and response_body < schema["minimum"]:
            errors.append(f"{path}: 值 {response_body} 小于最小值 {schema['minimum']}")
        if "maximum" in schema and response_body > schema["maximum"]:
            errors.append(f"{path}: 值 {response_body} 大于最大值 {schema['maximum']}")

    return errors


def _validate_object(obj: dict, schema: dict, path: str) -> list[str]:
    """校验 object 的每个属性。"""
    errors = []
    props = schema.get("properties", {})
    required = schema.get("required", [])

    if not props:
        return errors

    # 必填字段检查
    for field in required:
        if field not in obj:
            errors.append(f"{path}.{field}: 缺少必填字段")

    # 每个字段的类型检查
    for field, field_schema in props.items():
        if not isinstance(field_schema, dict) or field not in obj:
            continue
        value = obj[field]
        field_path = f"{path}.{field}"
        field_errors = validate_response(value, field_schema, field_path)
        errors.extend(field_errors)

    return errors


def _validate_array(arr: list, schema: dict, path: str) -> list[str]:
    """校验 array 的每个元素。"""
    errors = []

    # minItems / maxItems
    if "minItems" in schema and len(arr) < schema["minItems"]:
        errors.append(f"{path}: 数组长度 {len(arr)} 小于最小长度 {schema['minItems']}")
    if "maxItems" in schema and len(arr) > schema["maxItems"]:
        errors.append(f"{path}: 数组长度 {len(arr)} 超过最大长度 {schema['maxItems']}")

    # 元素校验（抽样：前 10 个元素）
    items_schema = schema.get("items", {})
    if items_schema and isinstance(items_schema, dict):
        for i, item in enumerate(arr[:10]):
            item_path = f"{path}[{i}]"
            errors.extend(validate_response(item, items_schema, item_path))
        if len(arr) > 10:
            log.debug("数组 %s 长度 %d，仅校验前 10 个元素", path, len(arr))

    return errors


def _get_type(value: Any) -> str:
    """获取值的 JSON Schema 类型名。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _type_matches(value: Any, expected_type: str) -> bool:
    """检查值的类型是否匹配 Schema 声明的类型。"""
    actual = _get_type(value)

    if expected_type == actual:
        return True

    # 兼容: "number" 可接受 int 和 float
    if expected_type == "number" and actual in ("integer", "number"):
        return True

    # 兼容: "integer" 不接受 float（严格）
    if expected_type == "integer" and actual == "integer":
        return True

    # 兼容: API 返回字符串 "123" 但 Schema 声明 integer
    if expected_type in ("integer", "number") and actual == "string":
        try:
            float(value)
            log.debug("类型兼容: 值 '%s' 为 string 但可转为 %s", value, expected_type)
            return True  # 数字型字符串视为兼容
        except (ValueError, TypeError):
            pass

    return False


def extract_response_schema(spec: dict, path: str, method: str, status_code: str = "200") -> Optional[dict]:
    """从 OpenAPI 规范中提取指定操作的成功响应 Schema。

    Args:
        spec: 完整的 OpenAPI 规范 dict
        path: API 路径（如 /api/users）
        method: HTTP 方法
        status_code: 目标状态码（默认 200）

    Returns:
        响应 Schema dict，或 None
    """
    if not spec:
        return None

    method_lower = method.lower()
    operation = spec.get("paths", {}).get(path, {}).get(method_lower, {})
    if not isinstance(operation, dict):
        return None

    responses = operation.get("responses", {})
    success_resp = responses.get(status_code, {})
    if not isinstance(success_resp, dict):
        # 尝试任意 2xx
        for code in responses:
            if code.startswith("2"):
                success_resp = responses[code]
                break
        if not isinstance(success_resp, dict):
            return None

    # OpenAPI 3: content["application/json"].schema
    content = success_resp.get("content", {})
    json_content = content.get("application/json", content.get(list(content.keys())[0] if content else "", {}))
    schema = json_content.get("schema", {})

    # Swagger 2: 直接有 schema
    if not schema:
        schema = success_resp.get("schema", {})

    return schema if schema else None
