"""OpenAPI/Swagger 导入 — 从 OpenAPI 3.x / Swagger 2.0 规范文件解析 API 端点。

支持 JSON 和 YAML 格式。
将 paths + operations 自动转换为 Pipeline ApiStep，包含 URL、方法、Headers、Body 模板、断言模板。
"""

import json
import re
from typing import Any, Optional

import yaml

from api_test_workbench.engine.models import ApiStep, ApiConfig
from api_test_workbench.engine.logger import setup_logger

log = setup_logger("openapi_parser")


# ── 公共 API ──

def parse_spec(content: str, is_yaml: bool = False) -> dict:
    """解析 OpenAPI/Swagger 规范内容，返回原始字典。

    Args:
        content: 规范文件内容字符串
        is_yaml: True 表示 YAML 格式，False 表示 JSON

    Returns:
        解析后的完整规范 dict

    Raises:
        ValueError: 解析失败或格式不支持
    """
    try:
        if is_yaml:
            spec = yaml.safe_load(content)
        else:
            spec = json.loads(content)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML 解析失败: {e}")
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败: {e}")

    if not isinstance(spec, dict):
        raise ValueError("规范文件内容不是有效的 JSON/YAML 对象")

    # 检测格式
    if "openapi" in spec:
        version = spec["openapi"]
        log.info("检测到 OpenAPI %s 规范", version)
    elif "swagger" in spec:
        version = spec["swagger"]
        log.info("检测到 Swagger %s 规范", version)
    else:
        raise ValueError("无法识别规范类型（缺少 openapi 或 swagger 字段）")

    if "paths" not in spec:
        raise ValueError("规范文件中没有 paths 定义")

    return spec


def list_endpoints(spec: dict) -> list[dict]:
    """从解析后的规范中列出所有可用端点。

    Returns:
        [
            {
                "path": "/api/users",
                "method": "GET",
                "summary": "获取用户列表",
                "operation_id": "getUsers",
                "tags": ["用户管理"],
                "has_request_body": false,
                "parameters_count": 3,
                "responses": ["200", "401", "500"]
            },
            ...
        ]
    """
    endpoints = []

    for path, methods in spec.get("paths", {}).items():
        if not isinstance(methods, dict):
            continue

        # OpenAPI 3: parameters 可在 path 级别定义
        path_params = _normalize_parameters(methods.get("parameters", []))

        for method_name, operation in methods.items():
            if method_name in ("parameters", "description", "summary", "servers"):
                continue

            # 标准 HTTP 方法
            method_upper = method_name.upper()
            if method_upper not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                continue

            if not isinstance(operation, dict):
                continue

            # 合并 path 级别 + operation 级别参数
            op_params = _normalize_parameters(operation.get("parameters", []))
            all_params = path_params + op_params

            # 请求体（OpenAPI 3 用 requestBody，Swagger 2 从 parameters 中找 body）
            has_body = False
            if "requestBody" in operation:
                has_body = True
            else:
                for p in all_params:
                    if p.get("in") == "body":
                        has_body = True
                        break

            tags = operation.get("tags", [])
            summary = operation.get("summary", "")
            op_id = operation.get("operationId", "")

            responses = list(operation.get("responses", {}).keys())

            endpoints.append({
                "path": path,
                "method": method_upper,
                "summary": summary or op_id or f"{method_upper} {path}",
                "operation_id": op_id,
                "tags": tags,
                "has_request_body": has_body,
                "parameters_count": len(all_params),
                "responses": responses,
            })

    return endpoints


def endpoint_to_step(spec: dict, endpoint: dict, base_url: str = "") -> ApiStep:
    """将单个端点转换为 ApiStep。

    Args:
        spec: 解析后的完整规范
        endpoint: list_endpoints 返回的端点 dict
        base_url: 覆盖规范中的 servers 地址

    Returns:
        ApiStep，包含预填充的 URL、Headers、Body 模板、断言逻辑
    """
    path = endpoint["path"]
    method = endpoint["method"]
    method_lower = method.lower()

    # 查找 operation 详情
    operation = spec.get("paths", {}).get(path, {}).get(method_lower, {})
    if not isinstance(operation, dict):
        operation = {}

    # 合并参数（path 级别 + operation 级别）
    path_params = _normalize_parameters(spec.get("paths", {}).get(path, {}).get("parameters", []))
    op_params = _normalize_parameters(operation.get("parameters", []))
    all_params = path_params + op_params

    # ── URL ──
    if not base_url:
        base_url = _extract_base_url(spec)
    full_url = base_url.rstrip("/") + "/" + path.lstrip("/")

    # ── Headers ──
    headers = {"Content-Type": "application/json"}
    if method in ("POST", "PUT", "PATCH") and _has_json_body(operation, all_params):
        headers["Content-Type"] = "application/json"

    # ── Body 模板 ──
    body_template = _build_body_template(spec, operation, all_params)

    # ── 断言逻辑 ──
    assertion_logic = _build_assertion(spec, operation)

    # ── 步骤名称 ──
    summary = endpoint.get("summary", "") or operation.get("summary", "")
    op_id = endpoint.get("operation_id", "") or operation.get("operationId", "")
    tags = endpoint.get("tags", [])
    if summary:
        name = summary
    elif tags:
        name = f"[{'/'.join(tags)}] {method} {path}"
    else:
        name = f"{method} {path}"

    # ── 响应 Schema ──
    response_schema = _extract_response_schema(operation, path, method)

    step = ApiStep(
        name=name,
        config=ApiConfig(
            url=full_url,
            method=method,
            headers=headers,
            body_template=body_template,
            response_schema=response_schema,
        ),
    )
    return step


def _extract_response_schema(operation: dict, path: str, method: str) -> dict:
    """从 operation 中提取成功响应 Schema。"""
    responses = operation.get("responses", {})
    # 找第一个 2xx 响应
    success_resp = None
    for code in sorted(responses.keys()):
        if code.startswith("2"):
            success_resp = responses[code]
            break
    if not success_resp or not isinstance(success_resp, dict):
        return {}

    # OpenAPI 3
    content = success_resp.get("content", {})
    json_content = content.get("application/json", content.get(list(content.keys())[0] if content else "", {}))
    schema = json_content.get("schema", {})

    # Swagger 2
    if not schema:
        schema = success_resp.get("schema", {})

    return schema if isinstance(schema, dict) else {}


# ── $ref 引用解析 ──

def _resolve_ref(spec: dict, ref: str) -> dict:
    """解析 OpenAPI 内部 $ref 引用。

    Args:
        spec: 完整的规范 dict
        ref: 引用字符串，如 #/components/schemas/Pet 或 Pet（相对引用）

    Returns:
        引用的 schema dict 或空 dict
    """
    if not ref or not isinstance(ref, str):
        return {}

    # 外部文件引用 → 跳过
    if not ref.startswith("#"):
        log.debug("不支持外部引用: %s", ref)
        return {}

    # 拆分路径: #/components/schemas/Pet → ["components", "schemas", "Pet"]
    parts = [p for p in ref.lstrip("#/").split("/") if p]
    current = spec
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            log.debug("引用路径未找到: %s (停在 %s)", ref, part)
            return {}

    return current if isinstance(current, dict) else {}


# ── 内部辅助 ──

def _normalize_parameters(params: Any) -> list[dict]:
    """将各种参数格式统一为 list[dict]"""
    if not params:
        return []
    if isinstance(params, list):
        result = []
        for p in params:
            if isinstance(p, dict):
                result.append(p)
            elif isinstance(p, str):
                # Swagger 2 中 $ref 语法
                result.append({"$ref": p})
        return result
    return []


def _extract_base_url(spec: dict) -> str:
    """从规范中提取 base URL。

    优先级: servers[0].url > host+basePath+schemes > 空字符串
    """
    # OpenAPI 3: servers
    servers = spec.get("servers", [])
    if servers and isinstance(servers, list):
        url = servers[0].get("url", "")
        # 替换变量为默认值或占位符
        url = re.sub(r'\{(\w+)\}', r'{{\1}}', url)
        return url

    # Swagger 2: host + basePath + schemes
    host = spec.get("host", "")
    base_path = spec.get("basePath", "/")
    if host:
        schemes = spec.get("schemes", ["https"])
        scheme = schemes[0] if isinstance(schemes, list) and schemes else "https"
        return f"{scheme}://{host}{base_path}"

    return ""


def _has_json_body(operation: dict, params: list[dict]) -> bool:
    """检查操作是否有 JSON 请求体。"""
    # OpenAPI 3: requestBody
    req_body = operation.get("requestBody", {})
    if req_body:
        content = req_body.get("content", {})
        if "application/json" in content:
            return True
        if content:  # 有其他 content type
            return True

    # Swagger 2: body parameter
    for p in params:
        if p.get("in") == "body":
            return True

    return False


def _build_body_template(spec: dict, operation: dict, params: list[dict]) -> dict:
    """从请求体 Schema 构建 body 模板。"""
    # OpenAPI 3: requestBody.content["application/json"].schema
    req_body = operation.get("requestBody", {})
    if req_body:
        content = req_body.get("content", {})
        json_content = content.get("application/json", content.get(list(content.keys())[0] if content else "", {}))
        schema = json_content.get("schema", {})
        if schema:
            return _schema_to_template(spec, schema)

    # Swagger 2: parameters 中 in=body 的 schema
    for p in params:
        if p.get("in") == "body":
            schema = p.get("schema", {})
            if schema:
                return _schema_to_template(spec, schema)

    # 没有请求体 Schema，用 query/path 参数构建模板
    template = {}
    for p in params:
        pin = p.get("in", "")
        if pin in ("query", "path"):
            name = p.get("name", "")
            if name:
                template[name] = _schema_default(p)
    return template


def _schema_to_template(spec: dict, schema: dict) -> dict:
    """将 JSON Schema 转换为 body 模板（填充默认值，解析 $ref）。"""
    if not schema:
        return {}

    # $ref 引用 → 递归解析
    if "$ref" in schema:
        resolved = _resolve_ref(spec, schema["$ref"])
        if resolved:
            return _schema_to_template(spec, resolved)
        return {}

    schema_type = schema.get("type", "object")

    if schema_type == "object":
        result = {}
        props = schema.get("properties", {})
        required = schema.get("required", [])
        for name, prop_schema in props.items():
            if not isinstance(prop_schema, dict):
                result[name] = ""
                continue
            # 必填字段标注提示
            default = _schema_default(prop_schema)
            if name in required:
                # 必填字段保留默认值但添加注释提示
                result[name] = default
            else:
                # 非必填字段省略（精简模板）
                pass
        # 如果所有字段都非必填，至少展示第一个字段的结构
        if not result and props:
            first = list(props.keys())[0]
            result[first] = _schema_default(props[first])
        return result

    elif schema_type == "array":
        items = schema.get("items", {})
        if items:
            return [_schema_to_template(spec, items)]
        return [{}]

    return {}


def _schema_default(schema: dict) -> Any:
    """返回 Schema 类型的默认值。

    同时考虑 example / enum / default 字段。
    """
    if not isinstance(schema, dict):
        return ""

    # 优先用 example
    if "example" in schema:
        return schema["example"]
    if "examples" in schema:
        ex = schema["examples"]
        if isinstance(ex, list) and ex:
            return ex[0]

    # 枚举值取第一个
    enum = schema.get("enum", [])
    if enum:
        return enum[0]

    # 默认值
    if "default" in schema:
        return schema["default"]

    # 按类型给默认值
    schema_type = schema.get("type", "string")
    if schema_type == "integer":
        return 0
    elif schema_type == "number":
        return 0.0
    elif schema_type == "boolean":
        return False
    elif schema_type == "array":
        return []
    elif schema_type == "object":
        return _schema_to_template({}, schema)  # _schema_default 无 spec 上下文，传空
    else:
        return ""


def _build_assertion(spec: dict, operation: dict) -> str:
    """根据响应 Schema 构建基础断言逻辑。"""
    responses = operation.get("responses", {})
    success_codes = [c for c in responses.keys() if c.startswith("2")]

    if not success_codes:
        return "str(resp_json.get('code', '')) == '0'"

    # 取第一个成功响应
    success_resp = responses.get(success_codes[0], {})
    if not isinstance(success_resp, dict):
        return "str(resp_json.get('code', '')) == '0'"

    # OpenAPI 3
    content = success_resp.get("content", {})
    json_content = content.get("application/json", content.get(list(content.keys())[0] if content else "", {}))
    schema = json_content.get("schema", {})

    # Swagger 2
    if not schema:
        schema = success_resp.get("schema", {})

    if not schema:
        return "str(resp_json.get('code', '')) == '0'"

    # 从响应 Schema 推断断言
    assertions = ["str(resp_json.get('code', '')) == '0'"]

    schema_type = schema.get("type", "object")
    if schema_type == "object":
        props = schema.get("properties", {})
        if "data" in props:
            data_schema = props["data"]
            data_type = data_schema.get("type", "")
            if data_type == "array":
                assertions.append("'data' in resp_json and isinstance(resp_json['data'], list)")
            elif data_type == "object":
                assertions.append("'data' in resp_json and isinstance(resp_json['data'], dict)")
        elif "total" in props:
            assertions.append("isinstance(resp_json.get('total'), (int, float))")
    elif schema_type == "array":
        assertions.append("isinstance(resp_json, list)")

    return " and ".join(assertions) if len(assertions) > 1 else assertions[0]


def spec_title(spec: dict) -> str:
    """获取规范标题。"""
    info = spec.get("info", {})
    return info.get("title", "Untitled API")


def spec_version(spec: dict) -> str:
    """获取规范版本。"""
    info = spec.get("info", {})
    return info.get("version", "unknown")
