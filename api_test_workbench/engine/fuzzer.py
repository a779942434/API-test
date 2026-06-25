"""属性模糊测试引擎 — 对 API 字段自动生成边缘值进行模糊测试。

与 AI 生成的测试用例互补：
- AI 覆盖业务逻辑（正向流程、业务规则、步骤间依赖）
- Fuzzer 覆盖输入边界（类型变异、边界值、注入攻击、特殊字符）

输入：body_template（API 请求体模板） + 可选的 response_schema（OpenAPI Schema）
输出：list[TestCase]（category="fuzz"）

使用示例:
    from api_test_workbench.engine.fuzzer import generate_fuzz_cases

    body_template = {"name": "", "age": 0, "email": ""}
    fuzz_cases = generate_fuzz_cases(body_template, method="POST")
    # → 30-50 条模糊测试用例
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from api_test_workbench.engine.models import TestCase
from api_test_workbench.engine.logger import setup_logger

log = setup_logger("fuzzer")


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class FieldDef:
    """结构化字段定义"""
    name: str                           # 字段名
    type: str = "string"                # string|integer|number|boolean|array|object
    required: bool = False              # 是否必填
    enum: list = field(default_factory=list)        # 枚举值列表
    min_length: Optional[int] = None    # 字符串最小长度
    max_length: Optional[int] = None    # 字符串最大长度
    minimum: Optional[Union[int, float]] = None  # 数值最小值
    maximum: Optional[Union[int, float]] = None  # 数值最大值
    format: str = ""                    # email|date|url|uri|phone 等
    pattern: str = ""                   # regex 模式
    default_value: Any = None           # 默认值（从 body_template 推断）
    parent_path: str = ""               # 父路径（嵌套字段用），如 "items[0]"


@dataclass
class FuzzStrategy:
    """变异策略描述"""
    key: str                # 策略标识，如 "empty", "sql_injection"
    label: str              # 中文描述，如 "空字符串", "SQL注入"
    category: str           # positive|negative|boundary
    value: Any              # 变异后的值
    expect_success: bool = False  # 是否预期业务成功


# ═══════════════════════════════════════════════════════════
# 类型推断
# ═══════════════════════════════════════════════════════════

def _python_type_to_schema(val: Any) -> str:
    """将 Python 值映射为 JSON Schema 类型名。"""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "number"
    if isinstance(val, str):
        return "string"
    if isinstance(val, list):
        return "array"
    if isinstance(val, dict):
        return "object"
    return "string"


def infer_field_defs(body_template: dict, parent_path: str = "") -> list[FieldDef]:
    """从 body_template 递归推断字段定义。

    Args:
        body_template: API 请求体模板（dict）
        parent_path: 嵌套路径前缀

    Returns:
        FieldDef 列表，每个字段有推断的类型和默认值

    示例:
        >>> infer_field_defs({"name": "", "age": 0, "tags": []})
        [FieldDef(name="name", type="string", default_value=""),
         FieldDef(name="age", type="integer", default_value=0),
         FieldDef(name="tags", type="array", default_value=[])]
    """
    if not isinstance(body_template, dict):
        return []

    fields = []
    for key, val in body_template.items():
        field_path = f"{parent_path}.{key}" if parent_path else key
        field_type = _python_type_to_schema(val)

        fd = FieldDef(
            name=key,
            type=field_type,
            default_value=val,
            parent_path=parent_path,
        )

        # 字符串推断长度约束
        if field_type == "string" and isinstance(val, str):
            if len(val) > 0:
                fd.min_length = 1
            fd.max_length = 255  # 行业默认

        # 整数推断范围
        if field_type == "integer" and isinstance(val, int):
            if val > 0:
                fd.minimum = 1

        # 嵌套对象 → 递归推断子字段
        if field_type == "object" and isinstance(val, dict) and val:
            nested = infer_field_defs(val, parent_path=field_path)
            for nf in nested:
                nf.parent_path = field_path
            fields.extend(nested)

        # 数组 → 推断元素类型
        if field_type == "array" and isinstance(val, list) and val:
            elem_type = _python_type_to_schema(val[0])
            fd.default_value = val  # 保留原始数组

        fields.append(fd)

    return fields


def merge_schema_constraints(fields: list[FieldDef], schema: dict) -> list[FieldDef]:
    """将 OpenAPI Schema 中的精确约束合并到推断的字段定义中。

    Args:
        fields: infer_field_defs() 返回的字段列表
        schema: OpenAPI response_schema 或 requestBody schema

    Returns:
        合并约束后的字段列表
    """
    if not schema or not isinstance(schema, dict):
        return fields

    props = schema.get("properties", {})
    required_list = schema.get("required", [])

    for fd in fields:
        if fd.name not in props:
            continue

        prop = props[fd.name]
        if not isinstance(prop, dict):
            continue

        # 精确类型
        if "type" in prop:
            fd.type = prop["type"]

        # 必填
        if fd.name in required_list:
            fd.required = True

        # 字符串约束
        if "minLength" in prop:
            fd.min_length = prop["minLength"]
        if "maxLength" in prop:
            fd.max_length = prop["maxLength"]

        # 数值约束
        if "minimum" in prop:
            fd.minimum = prop["minimum"]
        if "maximum" in prop:
            fd.maximum = prop["maximum"]

        # 枚举
        if "enum" in prop:
            fd.enum = prop["enum"]

        # 格式
        if "format" in prop:
            fd.format = prop["format"]

        # 模式
        if "pattern" in prop:
            fd.pattern = prop["pattern"]

    return fields


# ═══════════════════════════════════════════════════════════
# 变异策略生成
# ═══════════════════════════════════════════════════════════

def _string_strategies(fd: FieldDef) -> list[FuzzStrategy]:
    """为 string 字段生成变异策略。"""
    strategies = [
        FuzzStrategy("empty", "空字符串", "negative", ""),
        FuzzStrategy("whitespace", "纯空格", "negative", "   "),
        FuzzStrategy("overlong_256", "超长字符串-256", "boundary", "A" * 256),
        FuzzStrategy("overlong_10000", "超长字符串-10000", "boundary", "B" * 10000),
        FuzzStrategy("sql_injection", "SQL注入-永真", "negative", "' OR '1'='1"),
        FuzzStrategy("sql_injection_drop", "SQL注入-删表", "negative", "'; DROP TABLE users; --"),
        FuzzStrategy("xss_script", "XSS注入-script", "negative", "<script>alert(1)</script>"),
        FuzzStrategy("xss_img", "XSS注入-img", "negative", "<img src=x onerror=alert(1)>"),
        FuzzStrategy("unicode", "Unicode多语言", "boundary", "测试🎉日本語한국어🏳️‍🌈"),
        FuzzStrategy("null_byte", "空字节注入", "negative", "value\x00after"),
        FuzzStrategy("special_chars", "特殊字符", "boundary", "!@#$%^&*(){}[]|\\:;\"'<>,.?/~`"),
        FuzzStrategy("newline_chars", "换行符", "negative", "line1\nline2\r\nline3\t tab"),
        FuzzStrategy("numeric_string", "纯数字字符串", "equivalence", "1234567890"),
    ]

    # 格式感知变异
    if fd.format == "email" or "email" in fd.name.lower() or "mail" in fd.name.lower():
        strategies.append(FuzzStrategy("bad_email", "非法邮箱格式", "negative", "not-an-email"))
        strategies.append(FuzzStrategy("email_no_at", "邮箱缺@", "negative", "user.example.com"))
    elif fd.format == "date" or "date" in fd.name.lower():
        strategies.append(FuzzStrategy("bad_date", "非法日期", "negative", "2025-13-99"))
        strategies.append(FuzzStrategy("date_text", "日期为文本", "negative", "not-a-date"))
    elif fd.format == "url" or fd.format == "uri" or "url" in fd.name.lower():
        strategies.append(FuzzStrategy("bad_url", "非法URL", "negative", "not-a-valid-url"))
    elif "phone" in fd.name.lower() or "mobile" in fd.name.lower() or "tel" in fd.name.lower():
        strategies.append(FuzzStrategy("bad_phone", "非法手机号", "negative", "abcdefghijk"))
        strategies.append(FuzzStrategy("phone_overflow", "超长手机号", "boundary", "1" * 50))

    # 枚举感知：非法枚举值
    if fd.enum:
        strategies.append(FuzzStrategy("invalid_enum", "非法枚举值", "negative", "__INVALID_ENUM_VALUE__"))

    # 长度边界感知
    if fd.max_length:
        boundary_val = "X" * (fd.max_length + 1)
        strategies.append(FuzzStrategy("max_length_plus_1", f"超最大长度({fd.max_length}+1)", "boundary", boundary_val))

    return strategies


def _integer_strategies(fd: FieldDef) -> list[FuzzStrategy]:
    """为 integer 字段生成变异策略。"""
    strategies = [
        FuzzStrategy("zero", "零值", "boundary", 0),
        FuzzStrategy("negative", "负数", "negative", -1),
        FuzzStrategy("max_int32", "最大int32", "boundary", 2147483647),
        FuzzStrategy("max_int64", "最大int64", "boundary", 9223372036854775807),
        FuzzStrategy("float_instead", "浮点数代替整数", "negative", 1.5),
        FuzzStrategy("string_instead", "字符串代替整数", "negative", "123"),
        FuzzStrategy("null_instead", "null代替整数", "negative", None),
        FuzzStrategy("bool_instead", "布尔代替整数", "negative", True),
        FuzzStrategy("negative_large", "大负数", "boundary", -2147483648),
    ]

    # 范围感知
    if fd.minimum is not None:
        strategies.append(FuzzStrategy("below_min", f"小于最小值({fd.minimum}-1)", "boundary", fd.minimum - 1))
    if fd.maximum is not None:
        strategies.append(FuzzStrategy("above_max", f"大于最大值({fd.maximum}+1)", "boundary", fd.maximum + 1))

    return strategies


def _number_strategies(fd: FieldDef) -> list[FuzzStrategy]:
    """为 number 字段生成变异策略。"""
    strategies = [
        FuzzStrategy("zero", "零值", "boundary", 0.0),
        FuzzStrategy("negative", "负数", "negative", -0.01),
        FuzzStrategy("huge", "极大值", "boundary", 1e308),
        FuzzStrategy("tiny", "极小正值", "boundary", 1e-308),
        FuzzStrategy("string_instead", "字符串代替数字", "negative", "abc"),
        FuzzStrategy("null_instead", "null代替数字", "negative", None),
        FuzzStrategy("bool_instead", "布尔代替数字", "negative", False),
    ]

    if fd.minimum is not None:
        strategies.append(FuzzStrategy("below_min", f"小于最小值", "boundary", fd.minimum - 0.01))
    if fd.maximum is not None:
        strategies.append(FuzzStrategy("above_max", f"大于最大值", "boundary", fd.maximum + 0.01))

    return strategies


def _boolean_strategies(fd: FieldDef) -> list[FuzzStrategy]:
    """为 boolean 字段生成变异策略。"""
    return [
        FuzzStrategy("true_val", "合法值-true", "positive", True, expect_success=True),
        FuzzStrategy("false_val", "合法值-false", "positive", False, expect_success=True),
        FuzzStrategy("string_true", "字符串true", "negative", "true"),
        FuzzStrategy("string_false", "字符串false", "negative", "false"),
        FuzzStrategy("int_one", "整数1", "negative", 1),
        FuzzStrategy("int_zero", "整数0", "negative", 0),
        FuzzStrategy("null_instead", "null代替布尔", "negative", None),
    ]


def _array_strategies(fd: FieldDef) -> list[FuzzStrategy]:
    """为 array 字段生成变异策略。"""
    original = fd.default_value if isinstance(fd.default_value, list) else []

    strategies = [
        FuzzStrategy("empty_array", "空数组", "boundary", []),
        FuzzStrategy("string_instead", "字符串代替数组", "negative", "not_array"),
        FuzzStrategy("null_instead", "null代替数组", "negative", None),
        FuzzStrategy("object_instead", "对象代替数组", "negative", {}),
    ]

    if original:
        strategies.append(FuzzStrategy("single_element", "单元素数组", "boundary", original[:1]))
        strategies.append(FuzzStrategy("large_array", "大数组-1000", "boundary", original * 1000))
        strategies.append(FuzzStrategy("nested", "嵌套数组", "boundary", [original]))

    return strategies


def _object_strategies(fd: FieldDef) -> list[FuzzStrategy]:
    """为 object 字段生成变异策略。"""
    return [
        FuzzStrategy("empty_object", "空对象", "boundary", {}),
        FuzzStrategy("null_instead", "null代替对象", "negative", None),
        FuzzStrategy("array_instead", "数组代替对象", "negative", []),
        FuzzStrategy("string_instead", "字符串代替对象", "negative", "not_an_object"),
    ]


# 策略分发
_STRATEGY_MAP = {
    "string": _string_strategies,
    "integer": _integer_strategies,
    "number": _number_strategies,
    "boolean": _boolean_strategies,
    "array": _array_strategies,
    "object": _object_strategies,
}

# 每种类型在不同强度下的最大变异策略数
_STRATEGY_LIMITS = {
    "light": {"string": 5, "integer": 4, "number": 3, "boolean": 3, "array": 3, "object": 2},
    "standard": {"string": 8, "integer": 6, "number": 5, "boolean": 4, "array": 4, "object": 3},
    "deep": {"string": 99, "integer": 99, "number": 99, "boolean": 99, "array": 99, "object": 99},
}


def _get_strategies(fd: FieldDef) -> list[FuzzStrategy]:
    """根据字段类型获取所有变异策略。"""
    handler = _STRATEGY_MAP.get(fd.type, _string_strategies)
    return handler(fd)


# ═══════════════════════════════════════════════════════════
# 核心生成函数
# ═══════════════════════════════════════════════════════════

def _build_fuzz_assertion(fd: FieldDef, strategy: FuzzStrategy) -> str:
    """为模糊测试用例构建宽松断言。

    模糊测试的目标是「不崩溃」，而非业务逻辑正确：
    - API 应该返回 HTTP 200（业务错误通过 code 字段传达，不应 500）
    - 响应应该包含 code 字段
    - 不检查 code 是否为 0（因为预期可能失败）
    """
    if strategy.expect_success:
        # 预期成功的变异（如 bool=true），用标准断言
        return "str(resp_json.get('code', '')) == '0'"

    # 宽松断言：只要有 code 字段且非 500 即通过
    return "isinstance(resp_json, dict) and 'code' in resp_json"


def _mutate_input_data(
    body_template: dict,
    fd: FieldDef,
    strategy: FuzzStrategy,
) -> dict:
    """生成变异后的 input_data。

    对于顶层字段：直接在 body 中替换该字段的值
    对于嵌套字段：通过 parent_path 定位并替换
    """
    data = copy.deepcopy(body_template)

    if fd.parent_path:
        # 嵌套字段 — 按路径定位
        parts = fd.parent_path.split(".")
        target = data
        for part in parts:
            if not part:
                continue
            # 处理数组索引如 items[0]
            if "[" in part and part.endswith("]"):
                array_name, idx_str = part.split("[", 1)
                idx = int(idx_str.rstrip("]"))
                if isinstance(target, dict) and array_name in target:
                    target = target[array_name]
                    if isinstance(target, list) and idx < len(target):
                        target = target[idx]
            else:
                if isinstance(target, dict) and part in target:
                    target = target[part]
        # target 现在指向包含目标字段的 dict
        if isinstance(target, dict) and fd.name in target:
            target[fd.name] = strategy.value
    else:
        # 顶层字段 — 直接替换
        if fd.name in data:
            data[fd.name] = strategy.value

    return data


def generate_fuzz_cases(
    body_template: dict,
    method: str = "POST",
    step_index: int = 0,
    response_schema: Optional[dict] = None,
    intensity: str = "standard",
) -> list[TestCase]:
    """为单个 API 步骤生成模糊测试用例。

    Args:
        body_template: API 请求体模板，如 {"name": "", "age": 0}
        method: HTTP 方法（用于设置 operation）
        step_index: 步骤索引（用于 case_id 前缀）
        response_schema: 可选 OpenAPI Schema，用于合并精确约束
        intensity: 变异强度
            - "light": 每字段 3-5 条（快速冒烟）
            - "standard": 每字段 6-10 条（默认，覆盖核心边界）
            - "deep": 每字段 10+ 条（全覆盖，含大数组/超长字符串）

    Returns:
        list[TestCase]: category="fuzz" 的测试用例列表

    示例:
        >>> body = {"name": "", "age": 0, "enable": False}
        >>> cases = generate_fuzz_cases(body, method="POST")
        >>> len(cases) >= 20
        True
        >>> cases[0].category
        'fuzz'
    """
    if not body_template or not isinstance(body_template, dict):
        log.warning("body_template 为空或非 dict，无法生成模糊测试用例")
        return []

    # 1. 推断字段定义
    fields = infer_field_defs(body_template)
    if not fields:
        log.warning("未能从 body_template 推断出任何字段")
        return []

    # 2. 合并 OpenAPI Schema 约束
    if response_schema:
        fields = merge_schema_constraints(fields, response_schema)

    log.info("模糊测试: %d 个字段, 强度=%s", len(fields), intensity)

    # 3. 为每个字段生成变异策略
    test_cases = []
    case_index = 0

    limits = _STRATEGY_LIMITS.get(intensity, _STRATEGY_LIMITS["standard"])

    # 将 operation 映射
    op_map = {"POST": "create", "GET": "read", "PUT": "update", "DELETE": "delete", "PATCH": "update"}
    operation = op_map.get(method.upper(), "create")

    for fd in fields:
        strategies = _get_strategies(fd)
        max_count = limits.get(fd.type, 5)

        for i, strategy in enumerate(strategies):
            if i >= max_count:
                break

            case_index += 1
            mutated_data = _mutate_input_data(body_template, fd, strategy)

            case_id = f"FZ_{step_index}__{fd.name}__{strategy.key}"
            case_name = f"模糊-{fd.name}字段-{strategy.label}"

            # 必填字段缺失策略（单独处理，不修改值而是删除字段）
            if fd.required and strategy.key == "empty":
                # 对于必填字段的 empty 策略，删除该字段而非设为空
                if fd.name in mutated_data:
                    del mutated_data[fd.name]
                case_name = f"模糊-{fd.name}字段-缺少必填字段"

            assertion = _build_fuzz_assertion(fd, strategy)

            tc = TestCase(
                case_id=case_id,
                case_name=case_name,
                operation=operation,
                category="fuzz",
                input_data=mutated_data,
                expected_status_code=200,
                expected_response_keys=[],
                assertion_logic=assertion,
                pre_condition="",
                post_condition="",
            )
            test_cases.append(tc)

    log.info("生成了 %d 条模糊测试用例", len(test_cases))
    return test_cases


def generate_fuzz_cases_for_pipeline(
    pipeline_steps: list,
    intensity: str = "standard",
) -> dict[int, list[TestCase]]:
    """为整个 Pipeline 的所有步骤生成模糊测试用例。

    Args:
        pipeline_steps: ApiStep 列表
        intensity: 变异强度

    Returns:
        {step_index: [TestCase, ...]}  — 与 pipeline_test_cases_by_step 格式兼容
    """
    result = {}
    for i, step in enumerate(pipeline_steps):
        # 跳过只读步骤（GET/HEAD/OPTIONS）
        if step.config.method.upper() in ("GET", "HEAD", "OPTIONS"):
            log.debug("跳过只读步骤 %d: %s %s", i, step.config.method, step.name)
            continue

        body_template = step.config.body_template
        if not body_template:
            log.debug("步骤 %d 无 body_template，跳过", i)
            continue

        response_schema = getattr(step.config, 'response_schema', None) or {}

        cases = generate_fuzz_cases(
            body_template=body_template,
            method=step.config.method,
            step_index=i,
            response_schema=response_schema if response_schema else None,
            intensity=intensity,
        )
        if cases:
            result[i] = cases
            log.info("步骤 %d (%s): 生成 %d 条模糊用例", i, step.name, len(cases))

    return result


# ═══════════════════════════════════════════════════════════
# 辅助工具
# ═══════════════════════════════════════════════════════════

def merge_fuzz_cases(
    existing: dict[int, list[TestCase]],
    fuzz_cases: dict[int, list[TestCase]],
) -> dict[int, list[TestCase]]:
    """将模糊测试用例合并到现有用例集合中。

    Args:
        existing: 现有的 {step_index: [TestCase, ...]}
        fuzz_cases: fuzzer 生成的 {step_index: [TestCase, ...]}

    Returns:
        合并后的字典（修改 existing 原地并返回）
    """
    for step_idx, cases in fuzz_cases.items():
        if step_idx in existing:
            existing[step_idx].extend(cases)
        else:
            existing[step_idx] = list(cases)
    return existing


def get_fuzz_stats(cases: list[TestCase]) -> dict:
    """统计模糊测试用例的分布情况。"""
    if not cases:
        return {"total": 0, "by_field": {}, "by_strategy": {}}

    fuzz_cases = [tc for tc in cases if tc.category == "fuzz"]

    by_field: dict[str, int] = {}
    by_strategy: dict[str, int] = {}

    for tc in fuzz_cases:
        # 从 case_id 解析字段名和策略: FZ_{step}__{field}__{strategy}
        # 使用 __ 分隔避免蛇形字段名（如 user_email）被误拆
        parts = tc.case_id.split("__")
        if len(parts) >= 3:
            field = parts[1]      # FZ_0 之后的字段名
            strategy = parts[2]   # 策略 key
            by_field[field] = by_field.get(field, 0) + 1
            by_strategy[strategy] = by_strategy.get(strategy, 0) + 1

    return {
        "total": len(fuzz_cases),
        "by_field": by_field,
        "by_strategy": by_strategy,
    }
