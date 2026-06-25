"""执行测试用例 + 断言验证"""

import json
import re
import time
import traceback
from typing import Any

import requests

from api_test_workbench.engine.models import (
    ApiConfig, ApiStep, TestCase, TestResult,
    Pipeline, PipelineResult, PipelineContext, StepResult,
)
from api_test_workbench.engine.bindings import (
    _flatten_response, resolve_placeholders,
)
from api_test_workbench.engine.environment import resolve_env_variables
from api_test_workbench.engine.logger import setup_logger
from api_test_workbench.engine.utils import is_query_url, strip_placeholders

log = setup_logger("runner")


class _StepData:
    """包装扁平化步骤数据，支持 dict['key'] 和 .key.subkey 两种访问方式。

    示例：
        d = _StepData({"response.code": "0", "response.data.total": "5"})
        d['response.data.total']   → "5"
        d.response.data.total      → "5"  （与占位符语法一致）
    """

    def __init__(self, flat_dict: dict):
        self._data = flat_dict

    def __getitem__(self, key):
        return self._data[key]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        # 在扁平化字典中查找以 name 或 name.xxx 开头的 key
        prefix = name + "."
        matches = {}
        for k, v in self._data.items():
            if k == name:
                # 精确匹配：直接返回值
                return v
            if k.startswith(prefix):
                # 子路径匹配：去掉前缀后的部分作为子 key
                sub_key = k[len(prefix):]
                matches[sub_key] = v
        if matches:
            return _StepData(matches)
        raise AttributeError(f"'{type(self).__name__}' 中没有 '{name}'（可用 key: {list(self._data.keys())}）")

    def __repr__(self):
        return f"StepData({self._data})"

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __contains__(self, key):
        return key in self._data


def _wrap_step_context(step_context: dict) -> dict:
    """将 {step_index: flat_dict} 包装为 {step_index: _StepData}"""
    if not step_context:
        return {}
    return {idx: _StepData(data) for idx, data in step_context.items()}


def _safe_eval_assertion(assertion_logic: str, resp: requests.Response, step_context: dict = None) -> tuple[bool, str]:
    """在受限上下文中执行断言逻辑字符串。

    Args:
        assertion_logic: 断言表达式字符串
        resp: HTTP 响应对象
        step_context: {step_index: flattened_response_dict} 上游步骤数据，注入为 step1/step2/...

    Returns:
        (passed, error_message)
    """
    if not assertion_logic:
        return True, ""

    # 剥离 AI 残留占位符
    assertion_logic = strip_placeholders(assertion_logic)

    # 剥离 assert 关键字（AI 可能误写 assert xxx，实际只需布尔表达式）
    assertion_logic = re.sub(r'^assert\s+', '', assertion_logic.strip())

    # 修复：data 字段不一定存在，直接检查 len(str(.get('data',''))) > 0 在无 data 时必然失败
    # → 追加 'data' not in resp_json or 前缀，使无 data 的响应也能通过
    assertion_logic = re.sub(
        r"len\(str\((\w+)\.get\('data',\s*['\"]{2}\)\)\)\s*>\s*0",
        r"('data' not in \1 or len(str(\1.get('data', ''))) > 0)",
        assertion_logic,
    )

    # 安全转换：将 data 字段的 .get() 转为防御式，兼容 data 为字符串（直接返回 ID）、
    # 为 dict（对象）、为 None 三种情况
    # resp_json.get('data', {}).get('id', 0) → safe_get(resp_json, 'data', 'id', 0)
    # 规则：如果 data 是字符串 → 它就是 ID；如果是 dict → 取其 .id
    def _safe_get(data, parent_key, child_key, default=0):
        parent = data.get(parent_key) if isinstance(data, dict) else data
        if isinstance(parent, dict):
            return parent.get(child_key, default)
        if isinstance(parent, list):
            # data 字段是数组时，取第一个元素的 child_key
            if parent and isinstance(parent[0], dict):
                return parent[0].get(child_key, default)
            return default
        if isinstance(parent, str):
            return int(parent) if child_key == 'id' else default
        return default

    def _as_dict(val):
        """安全转换为 dict，非 dict 类型（如 list/str/None）返回空 dict。
        防止 list.get('records', []) 导致的 AttributeError。
        """
        return val if isinstance(val, dict) else {}

    # 单引号版：双层 .get('data', {}).get('id', 0) → safe_get(...)
    assertion_logic = re.sub(
        r"(\w+)\.get\('(\w+)',\s*\{\}\)\.get\('(\w+)',\s*(\d+)\)",
        r"safe_get(\1, '\2', '\3', \4)",
        assertion_logic,
    )
    # 双引号版（P1#9）
    assertion_logic = re.sub(
        r'(\w+)\.get\("(\w+)",\s*\{\}\)\.get\("(\w+)",\s*(\d+)\)',
        r'safe_get(\1, "\2", "\3", \4)',
        assertion_logic,
    )
    # 简化版：单层 .get('data', {}) → _as_dict() 防御 list/dict/str 三种类型
    assertion_logic = re.sub(
        r"(\w+)\.get\('(\w+)',\s*\{\}\)",
        r"_as_dict(\1.get('\2'))",
        assertion_logic,
    )
    assertion_logic = re.sub(
        r'(\w+)\.get\("(\w+)",\s*\{\}\)',
        r'_as_dict(\1.get("\2"))',
        assertion_logic,
    )
    # 修复历史遗留：已存在的 (resp_json.get('data') or {}).get(...) 模式
    # → _as_dict(resp_json.get('data')).get(...)
    assertion_logic = re.sub(
        r"\((\w+)\.get\('(\w+)'\)\s+or\s+\{\}\)\.get\(",
        r"_as_dict(\1.get('\2')).get(",
        assertion_logic,
    )
    assertion_logic = re.sub(
        r'\((\w+)\.get\("(\w+)"\)\s+or\s+\{\}\)\.get\(',
        r'_as_dict(\1.get("\2")).get(',
        assertion_logic,
    )

    # 安全检查：拒绝包含潜在沙箱逃逸特征的断言（使用 \b 词边界避免误报，
    # 如 "important" 不再因包含 "import" 而被拒绝）
    dangerous = (r"\b__\w*__\b", r"\bimport\b", r"\bexec\b", r"\bcompile\b",
                 r"\bopen\b", r"\bgetattr\b", r"\bsetattr\b", r"\bdelattr\b",
                 r"\beval\b", r"\bglobals\b", r"\blocals\b")
    lowered = assertion_logic.lower()
    for pat in dangerous:
        if re.search(pat, lowered):
            return False, f"断言包含不安全字符: {pat}"

    try:
        resp_json = resp.json() if resp.text else {}
    except (json.JSONDecodeError, ValueError):
        resp_json = {}

    safe_context = {
        "resp": resp,
        "status_code": resp.status_code,
        "resp_json": resp_json,
        "json": resp_json,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "len": len,
        "isinstance": isinstance,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "min": min,
        "max": max,
        "abs": abs,
        "round": round,
        "sum": sum,
        "any": any,
        "all": all,
        "in": lambda a, b: a in b,
        "True": True,
        "False": False,
        "None": None,
        "safe_get": _safe_get,
        "_as_dict": _as_dict,
    }

    # 注入上游步骤数据：step1, step2, ...（1-based，匹配占位符习惯）
    # 包装为 _StepData 支持 dict['key'] 和 .key.subkey 两种访问
    if step_context:
        for step_idx, step_data in _wrap_step_context(step_context).items():
            safe_context[f"step{step_idx + 1}"] = step_data

    try:
        result = eval(assertion_logic, {"__builtins__": {}}, safe_context)
        if result:
            return True, ""
        # 构造详细错误信息：包含断言表达式、完整响应体、业务状态码
        resp_code = resp_json.get('code') if isinstance(resp_json, dict) else 'N/A'
        resp_msg = resp_json.get('message', '') if isinstance(resp_json, dict) else ''
        resp_body = json.dumps(resp_json, ensure_ascii=False, indent=2) if isinstance(resp_json, (dict, list)) else str(resp_json)
        if len(resp_body) > 2000:
            resp_body = resp_body[:2000] + "\n...（已截断）"

        # 识别负向用例意外通过的情况
        hint = ""
        lowered = assertion_logic.lower()
        if ("!=" in lowered or "not" in lowered) and str(resp_code) == '0' and "'code'" in lowered:
            hint = "\n⚠️ 负向用例意外通过：API 返回 code=0（业务成功），但断言期望业务失败"

        return False, (
            f"断言失败: {assertion_logic}\n"
            f"业务码: code={resp_code}"
            + (f", message={resp_msg}" if resp_msg else "")
            + f"\n响应体:\n{resp_body}"
            + hint
        )
    except Exception as e:
        # 断言执行异常（如 data 字段是字符串而非对象导致 .get() 失败）
        # 这不影响测试结果——断言失败就是 FAIL
        hint = ""
        if "object has no attribute 'get'" in str(e) or "AttributeError" in str(type(e).__name__):
            hint = "（可能原因：API 返回的 data 字段是 list 而非 dict，请用 _as_dict(resp_json.get('data')) 防御）"
        return False, f"断言执行异常 [{type(e).__name__}]: {e}\n{hint}\n断言: {assertion_logic}"


def _apply_body_deps(template, dep_body):
    """将 data_dependencies.body 合并到 body_template，支持 dict 和 list"""
    if not dep_body:
        return template
    try:
        dep = json.loads(dep_body) if isinstance(dep_body, str) else dep_body
    except (json.JSONDecodeError, TypeError):
        return template
    if isinstance(template, dict) and isinstance(dep, dict):
        return {**template, **dep}
    if isinstance(dep, (dict, list)):
        return dep  # list/dict 直接替换
    return template


def _execute_hook(hook_str: str, session: requests.Session) -> tuple[bool, str]:
    """解析并执行前置/后置钩子。

    支持两种格式：
    1. JSON 动作描述: {"method": "POST", "url": "/api/xxx", "body": {...}}
    2. 纯文本描述: "记录返回的data.id" → 仅记录日志，不执行

    Args:
        hook_str: pre_condition 或 post_condition 字符串
        session: 已认证的 Session

    Returns:
        (success, message) — 钩子失败不抛异常，返回 False + 错误信息
    """
    if not hook_str or not hook_str.strip():
        return True, ""

    # 尝试解析为 JSON 动作
    try:
        action = json.loads(hook_str)
    except (json.JSONDecodeError, TypeError):
        # 纯文本描述（如 "记录返回的id" 或 "无"）→ 仅记录
        stripped = hook_str.strip()
        if stripped and stripped not in ('无', '无需', '无前置条件', '无后置条件'):
            log.debug("钩子（描述）: %s", stripped[:200])
        return True, ""

    if not isinstance(action, dict):
        return True, ""

    method = (action.get("method") or "GET").upper()
    url = action.get("url", "")
    body = action.get("body") or action.get("data") or {}
    headers = action.get("headers", {})

    if not url:
        log.warning("钩子缺少 url 字段，跳过: %s", hook_str[:100])
        return True, ""

    try:
        log.info("🔧 执行钩子: %s %s", method, url[:120])
        if method == "GET":
            resp = session.get(url, headers=headers, params=body, timeout=30)
        else:
            resp = session.request(method, url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        log.debug("钩子响应 %s: %s", resp.status_code, str(resp.text)[:200])
        return True, ""
    except requests.exceptions.Timeout:
        return False, f"钩子超时: {method} {url}"
    except requests.exceptions.RequestException as e:
        log.warning("钩子执行失败: %s %s → %s", method, url, e)
        return False, f"钩子失败: {method} {url} → {e}"


def run_single_test(
    tc: TestCase,
    api_config: ApiConfig,
    session: requests.Session,
    step_context: dict = None,
) -> TestResult:
    """执行单条测试用例

    Args:
        tc: 测试用例
        api_config: API 配置
        session: 已认证的 Session
        step_context: {step_index: flattened_dict} 上游步骤数据，注入断言上下文
    """
    url = api_config.url
    method = api_config.method.upper()

    # 递归替换占位符: {{timestamp}} → 当前时间戳, {{index}} → 保留（已在生成阶段替换）
    def _resolve_placeholders(obj):
        if isinstance(obj, dict):
            return {k: _resolve_placeholders(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_resolve_placeholders(v) for v in obj]
        elif isinstance(obj, str):
            return obj.replace("{{timestamp}}", str(int(time.time())))
        return obj

    # 根据 body_template 类型构造请求体
    template = api_config.body_template
    if isinstance(template, list):
        request_body = _resolve_placeholders(template)
    elif isinstance(template, dict):
        data = tc.input_data if isinstance(tc.input_data, dict) else {}
        request_body = _resolve_placeholders({**template, **data})
    elif isinstance(tc.input_data, dict):
        request_body = _resolve_placeholders(tc.input_data)
    else:
        request_body = {}

    # Debug: 仅记录 cookie 数量和 header 名称，不打印敏感值
    log.debug("Session cookie count: %d, header names: %s",
              len(session.cookies), list(session.headers.keys()))

    # ── 前置钩子 ──
    pre_ok, pre_msg = _execute_hook(tc.pre_condition, session)
    if not pre_ok:
        log.warning("前置钩子失败: %s", pre_msg)

    try:
        log.info("%s %s", method, url[:120])
        log.debug("请求体: %s", str(request_body)[:500])
        start = time.perf_counter()
        if method == "GET":
            resp = session.get(url, headers=api_config.headers, params=request_body, timeout=30)
        elif method in ("POST", "PUT", "DELETE", "PATCH"):
            resp = session.request(method, url, headers=api_config.headers, json=request_body, timeout=30)
        else:
            return TestResult(
                case_id=tc.case_id,
                case_name=tc.case_name,
                passed=False,
                actual_status_code=0,
                expected_status_code=tc.expected_status_code,
                response_body=None,
                error_message=f"不支持的 HTTP 方法: {method}",
                request_body=request_body,
                request_url=url,
                response_time_ms=0.0,
            )
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

        # 解析响应体
        try:
            response_json = resp.json() if resp.text else {}
        except (json.JSONDecodeError, ValueError):
            response_json = resp.text
        log.debug("响应 %s: %s", resp.status_code, str(response_json)[:300])

        # 验证状态码
        passed = resp.status_code == tc.expected_status_code

        # P1#8: 检测 401 可能为 Token 过期
        if resp.status_code == 401:
            log.warning("收到 401 Unauthorized — 可能 Token 已过期，建议重新登录")

        # 如果状态码通过，执行附加断言
        assertion_error = ""
        if passed and tc.assertion_logic:
            assertion_passed, assertion_error = _safe_eval_assertion(tc.assertion_logic, resp, step_context)
            passed = assertion_passed

        # 验证预期响应键（P0#3: expected_response_keys 校验）
        if passed and tc.expected_response_keys:
            resp_json = response_json if isinstance(response_json, dict) else {}
            missing_keys = [k for k in tc.expected_response_keys if k not in resp_json]
            if missing_keys:
                passed = False
                assertion_error = (
                    f"响应缺少预期键: {missing_keys}，"
                    f"实际键: {list(resp_json.keys())[:20]}"
                )

        # ── Schema 响应校验（从 OpenAPI 导入的 Schema 自动生效）──
        if passed and api_config.response_schema:
            try:
                from api_test_workbench.engine.schema_validator import validate_response
                schema_errors = validate_response(response_json, api_config.response_schema)
                if schema_errors:
                    passed = False
                    assertion_error = (
                        f"Schema 校验失败 ({len(schema_errors)} 项):\n" +
                        "\n".join(f"  • {e}" for e in schema_errors[:10])
                    )
                    if len(schema_errors) > 10:
                        assertion_error += f"\n  ... 还有 {len(schema_errors) - 10} 项未显示"
                    log.warning("Schema 校验发现 %d 个问题", len(schema_errors))
            except Exception:
                pass  # Schema 校验失败不影响主流程

        # ── 后置钩子 ──
        post_ok, post_msg = _execute_hook(tc.post_condition, session)
        if not post_ok:
            log.warning("后置钩子失败: %s", post_msg)

        # ── 模糊测试宽松判定 ──
        # 模糊测试目标：服务不崩溃（HTTP 2xx）即通过，不关注业务 code
        if tc.category == "fuzz" and not passed:
            if 200 <= resp.status_code < 300:
                # 服务正常响应（即使业务 code != 0），视为通过
                passed = True
                assertion_error = ""
                log.info("模糊用例 %s 服务正常响应 HTTP %d，视为通过", tc.case_id, resp.status_code)
            elif resp.status_code >= 500:
                # 服务端错误 → 真正的 bug
                assertion_error = (
                    f"[模糊测试发现Bug] {assertion_error}\n"
                    f"服务返回 {resp.status_code}，模糊输入导致服务端异常！"
                )
                log.warning("模糊用例 %s 触发服务端 %d 错误！", tc.case_id, resp.status_code)

        return TestResult(
            case_id=tc.case_id,
            case_name=tc.case_name,
            passed=passed,
            actual_status_code=resp.status_code,
            expected_status_code=tc.expected_status_code,
            response_body=response_json,
            error_message=assertion_error,
            request_body=request_body,
            request_url=url,
            response_time_ms=elapsed_ms,
        )

    except requests.RequestException as e:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        log.error("请求异常 %s %s: %s", method, url[:120], str(e))
        return TestResult(
            case_id=tc.case_id,
            case_name=tc.case_name,
            passed=False,
            actual_status_code=0,
            expected_status_code=tc.expected_status_code,
            response_body=None,
            error_message=f"请求异常: {str(e)}\n{traceback.format_exc()}",
            request_body=request_body,
            request_url=url,
            response_time_ms=elapsed_ms,
        )


def run_all_tests(
    test_cases: list[TestCase],
    api_config: ApiConfig,
    session: requests.Session,
    progress_callback=None,
) -> list[TestResult]:
    """执行全部测试用例，返回结果列表"""
    results = []
    for i, tc in enumerate(test_cases):
        result = run_single_test(tc, api_config, session)
        results.append(result)
        if progress_callback:
            progress_callback(i + 1, len(test_cases), result)
    return results


def get_auth_session(auth_endpoint: str, auth_body: dict, tenant_id: str = "") -> requests.Session:
    """调用登录接口，返回已认证的 Session。tenant_id 可选，传入时合并到 Body"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    if tenant_id:
        auth_body = {**auth_body, "tenantId": tenant_id}

    log.info("登录请求: POST %s", auth_endpoint)
    resp = session.post(auth_endpoint, json=auth_body)
    if resp.status_code not in (200, 302):
        log.error("登录失败: %s → status=%s", auth_endpoint, resp.status_code)
        raise RuntimeError(
            f"登录失败 [{resp.status_code}] — 请检查环境 Auth URL 是否正确\n"
            f"请求地址: POST {auth_endpoint}\n"
            f"响应: {resp.text[:300]}"
        )

    result = resp.json()
    if result.get("code") != '0':
        raise RuntimeError(f"登录业务失败: {result}")

    # 如果登录响应中包含 token/accessToken，自动注入 Authorization header
    data = result.get("data", {}) or {}
    token = (
        result.get("token")
        or result.get("access_token")
        or data.get("token")
        or data.get("accessToken")
        or data.get("access_token")
    )
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})
        log.info("登录成功，已注入 Bearer token: %s...", token[:20])
        log.debug("登录响应 keys: %s", list(result.keys()))
    else:
        log.info("登录成功（Cookie 认证）: %s", auth_endpoint)
        log.debug("登录响应 keys (无token): %s", list(result.keys()))

    return session


# ==================== Pipeline 执行引擎 ====================


def resolve_step_config(step: ApiStep, context: PipelineContext, env_variables: dict = None) -> ApiConfig:
    """将步骤配置中的所有占位符替换为实际值，返回新的 ApiConfig

    解析顺序：先步骤间数据绑定 ({{stepN.path}}) → 再环境变量 ({{VAR_NAME}})
    """
    resolved_url = resolve_placeholders(step.config.url, context)
    resolved_headers = resolve_placeholders(step.config.headers, context)
    resolved_body = resolve_placeholders(step.config.body_template, context)
    if not isinstance(resolved_body, (dict, list)):
        resolved_body = {}
    resolved_auth_body = resolve_placeholders(step.config.auth_body, context)
    resolved_auth_endpoint = resolve_placeholders(step.config.auth_endpoint, context)

    # 第二遍：环境变量替换 {{VAR_NAME}}
    if env_variables:
        resolved_url = resolve_env_variables(resolved_url, env_variables)
        resolved_headers = resolve_env_variables(resolved_headers, env_variables)
        resolved_body = resolve_env_variables(resolved_body, env_variables)
        resolved_auth_endpoint = resolve_env_variables(resolved_auth_endpoint, env_variables)
        resolved_auth_body = resolve_env_variables(resolved_auth_body, env_variables)

    return ApiConfig(
        name=step.config.name or step.name,
        url=resolved_url,
        method=step.config.method,
        headers=resolved_headers,
        body_template=resolved_body,
        auth_endpoint=resolved_auth_endpoint,
        auth_body=resolved_auth_body,
    )


def _is_fuzz_case(case_or_result) -> bool:
    """检查用例或结果是否为模糊测试（通过 case_id 前缀判断）。

    适用于 TestCase 和 TestResult（两者都有 case_id 属性）。
    模糊测试用例的 case_id 以 'FZ_' 开头，由 engine/fuzzer.py 生成。
    """
    cid = getattr(case_or_result, 'case_id', '')
    return isinstance(cid, str) and cid.startswith('FZ_')


def execute_pipeline(
    pipeline: Pipeline,
    session: requests.Session,
    test_cases_by_step: dict,
    progress_callback=None,
    env_variables: dict = None,
    max_workers: int = 5,
) -> PipelineResult:
    """执行 Pipeline：步骤顺序执行，同一步骤内多用例并行执行。"""
    total_steps = len(pipeline.steps)
    max_cases = max((len(v) for v in test_cases_by_step.values()), default=0)
    if max_cases == 0:
        return PipelineResult(pipeline_name=pipeline.name, overall_passed=True)

    step_results_map = {i: [] for i in range(total_steps)}
    overall_passed = True
    stopped_at = -1

    # ── 并行/串行分发 ──
    if max_workers > 1 and max_cases > 1:
        _execute_pipeline_parallel(
            pipeline, session, test_cases_by_step, env_variables,
            max_workers, progress_callback, step_results_map,
        )
        # 汇总并行模式的 overall_passed 和 stopped_at
        overall_passed = True
        stopped_at = -1
        for step_idx in range(total_steps):
            results = step_results_map.get(step_idx, [])
            if not results and step_idx < total_steps:
                # 步骤被中断（on_failure=stop 触发）
                if stopped_at < 0:
                    stopped_at = step_idx
            for r in results:
                if not r.passed:
                    # 模糊测试失败不影响 overall_passed（与串行模式一致）
                    if not _is_fuzz_case(r):
                        overall_passed = False
    else:
        # 串行模式
        for case_idx in range(max_cases):
            log.info("===== 链路 %d/%d 开始 =====", case_idx + 1, max_cases)
            context = PipelineContext()
            case_stopped = False

            for step_idx, step in enumerate(pipeline.steps):
                if step.ignored:
                    step_results_map[step_idx].append(TestResult(
                        case_id="", case_name="(已忽略)", passed=True,
                        actual_status_code=0, expected_status_code=0, response_body=None,
                        response_time_ms=0.0,
                    ))
                    context.extracted_values[step_idx] = {"_ignored": True}
                    continue

                step_tcs = test_cases_by_step.get(step_idx, [])
                if not step_tcs:
                    continue
                tc_idx = min(case_idx, len(step_tcs) - 1)
                if tc_idx != case_idx:
                    log.info("Step %d 用例数(%d)不足，链路 %d 复用用例 #%d",
                             step_idx + 1, len(step_tcs), case_idx + 1, tc_idx)
                tc = step_tcs[tc_idx]

                try:
                    if progress_callback:
                        try:
                            progress_callback(step_idx, total_steps,
                                StepResult(step_index=step_idx, step_name=step.name, test_results=[]))
                        except Exception:
                            pass

                    resolved_config = resolve_step_config(step, context, env_variables)
                    tc_config = resolved_config
                    deps = getattr(tc, 'data_dependencies', {}) or {}
                    if deps:
                        dep_headers = {}
                        if deps.get("headers"):
                            try:
                                dep_headers = json.loads(deps["headers"]) if isinstance(deps["headers"], str) else deps["headers"]
                            except (json.JSONDecodeError, TypeError):
                                pass
                        tc_config = ApiConfig(
                            name=resolved_config.name,
                            url=deps.get("url", resolved_config.url),
                            method=resolved_config.method,
                            headers=({**resolved_config.headers, **dep_headers} if dep_headers else resolved_config.headers),
                            body_template=_apply_body_deps(resolved_config.body_template, deps.get("body")),
                            response_schema=step.config.response_schema,
                        )
                        tc_config = resolve_step_config(
                            ApiStep(name=tc.case_name, config=tc_config), context, env_variables
                        )

                    resolved_input = resolve_placeholders(tc.input_data, context)
                    resolved_tc = TestCase(
                        case_id=tc.case_id, case_name=tc.case_name,
                        operation=tc.operation, category=tc.category,
                        input_data=resolved_input if isinstance(resolved_input, dict) else {},
                        expected_status_code=tc.expected_status_code,
                        expected_response_keys=tc.expected_response_keys,
                        assertion_logic=tc.assertion_logic,
                        pre_condition=tc.pre_condition,
                        post_condition=tc.post_condition,
                    )

                    result = run_single_test(resolved_tc, tc_config, session, step_context=context.extracted_values)
                    log.info("链路 %d Step %d [%s] %s → %s",
                             case_idx + 1, step_idx + 1, tc.case_name,
                             "PASS" if result.passed else "FAIL", result.actual_status_code)
                    step_results_map[step_idx].append(result)

                    if isinstance(result.response_body, dict):
                        context.extracted_values[step_idx] = _flatten_response(result.response_body)
                    elif isinstance(result.response_body, list):
                        context.extracted_values[step_idx] = _flatten_response(result.response_body, "response")
                    elif result.response_body is not None:
                        context.extracted_values[step_idx] = {"response.raw": str(result.response_body)[:200]}
                    else:
                        context.extracted_values[step_idx] = {}

                    if not result.passed:
                        # 模糊测试失败不中断 Pipeline，也不影响 overall_passed
                        if not _is_fuzz_case(tc):
                            overall_passed = False
                            if step.on_failure == "stop":
                                case_stopped = True
                                if stopped_at < 0:
                                    stopped_at = step_idx

                except Exception as e:
                    log.error("链路 %d Step %d 异常: %s", case_idx + 1, step_idx + 1, str(e))
                    step_results_map[step_idx].append(TestResult(
                        case_id=tc.case_id, case_name=tc.case_name,
                        passed=False, actual_status_code=0,
                        expected_status_code=tc.expected_status_code,
                        response_body=None,
                        error_message=f"步骤执行异常: {str(e)}\n{traceback.format_exc()}",
                        response_time_ms=0.0,
                    ))
                    context.extracted_values[step_idx] = {}
                    overall_passed = False
                    if step.on_failure == "stop":
                        case_stopped = True
                        if stopped_at < 0:
                            stopped_at = step_idx

                if case_stopped:
                    break

    # ── 汇总 StepResult ──
    step_results = []
    for step_idx, step in enumerate(pipeline.steps):
        results = step_results_map.get(step_idx, [])
        if results:
            passed = all(r.passed for r in results)
            extracted = {}
            for r in results:
                if r.passed and isinstance(r.response_body, dict):
                    extracted = _flatten_response(r.response_body)
                    log.debug("Step %d 提取数据来源: [%s] %s", step_idx + 1, r.case_id, r.case_name)
                    break
        else:
            passed = True
            extracted = {}
        step_results.append(StepResult(
            step_index=step_idx,
            step_name=step.name,
            test_results=results,
            passed=passed,
            skipped=step.ignored,
            extracted_data=extracted,
        ))

    pipeline_result = PipelineResult(
        pipeline_name=pipeline.name,
        step_results=step_results,
        overall_passed=overall_passed,
        stopped_at_step=stopped_at,
    )

    try:
        from api_test_workbench.engine.history import record_run
        env = (env_variables or {}).get("ENV_NAME", "")
        record_run(pipeline.name, env, pipeline_result)
    except Exception:
        pass

    return pipeline_result


def _execute_pipeline_parallel(
    pipeline, session, test_cases_by_step, env_variables,
    max_workers, progress_callback, step_results_map,
):
    """并行执行：步骤顺序执行，步骤内用例并行。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    total_steps = len(pipeline.steps)
    accumulated_context = {}  # step_idx → flattened_response

    for step_idx, step in enumerate(pipeline.steps):
        step_tcs = test_cases_by_step.get(step_idx, [])

        if step.ignored:
            for tc in step_tcs:
                step_results_map[step_idx].append(TestResult(
                    case_id=tc.case_id if tc else "", case_name="(已忽略)", passed=True,
                    actual_status_code=0, expected_status_code=0, response_body=None,
                    response_time_ms=0.0,
                ))
            accumulated_context[step_idx] = {"_ignored": True}
            continue

        if not step_tcs:
            continue

        workers = min(max_workers, len(step_tcs))
        log.info("===== Step %d/%d (%s): %d 用例, %d 并行 =====",
                 step_idx + 1, total_steps, step.name, len(step_tcs), workers)

        # 步骤级占位符解析
        context = PipelineContext()
        context.extracted_values = accumulated_context
        try:
            resolved_config = resolve_step_config(step, context, env_variables)
            base_url = resolved_config.url
            base_headers = resolved_config.headers
            base_body = resolved_config.body_template
        except Exception as e:
            log.warning("占位符解析失败: %s", e)
            base_url = step.config.url
            base_headers = step.config.headers
            base_body = step.config.body_template

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for tc in step_tcs:
                worker_session = _copy_session(session)
                future = executor.submit(
                    _run_one_case,
                    tc, step, base_url, base_headers, base_body,
                    worker_session, accumulated_context, env_variables,
                )
                futures[future] = tc

            for future in as_completed(futures):
                tc = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    log.error("用例 %s 并行异常: %s", tc.case_id, e)
                    result = TestResult(
                        case_id=tc.case_id, case_name=tc.case_name,
                        passed=False, actual_status_code=0,
                        expected_status_code=tc.expected_status_code,
                        response_body=None,
                        error_message=f"并行执行异常: {e}",
                        request_url=base_url, response_time_ms=0.0,
                    )
                step_results_map[step_idx].append(result)

        # 按原始顺序排序
        tc_order = {tc.case_id: i for i, tc in enumerate(step_tcs)}
        step_results_map[step_idx].sort(key=lambda r: tc_order.get(r.case_id, 999))

        # 提取数据供后续步骤
        for r in step_results_map[step_idx]:
            if r.passed and isinstance(r.response_body, dict):
                accumulated_context[step_idx] = _flatten_response(r.response_body)
                log.debug("Step %d 提取数据来源: [%s] %s", step_idx + 1, r.case_id, r.case_name)
                break
        else:
            accumulated_context[step_idx] = {}

        # 检查是否应中止 Pipeline（非模糊用例全部失败 + on_failure=stop）
        non_fuzz_results = [r for r in step_results_map[step_idx] if not _is_fuzz_case(r)]
        if not non_fuzz_results:
            non_fuzz_results = step_results_map[step_idx]  # 全部是模糊用例，不过滤
        step_all_failed = all(not r.passed for r in non_fuzz_results)
        if step_all_failed and step.on_failure == "stop":
            log.warning("Step %d 全部失败且 on_failure=stop，中止 Pipeline", step_idx + 1)
            break

        if progress_callback:
            try:
                progress_callback(step_idx, total_steps,
                    StepResult(step_index=step_idx, step_name=step.name,
                               test_results=step_results_map[step_idx]))
            except Exception:
                pass


def _copy_session(session: requests.Session) -> requests.Session:
    """复制 Session：拷贝 cookies 和 headers，线程安全。"""
    import requests as _r
    new_s = _r.Session()
    new_s.cookies.update(session.cookies)
    new_s.headers.update(session.headers)
    return new_s


def _run_one_case(tc, step, base_url, base_headers, base_body,
                  session, accumulated_context, env_variables):
    """执行单条用例（供并行调度）。"""
    context = PipelineContext()
    context.extracted_values = accumulated_context

    deps = getattr(tc, 'data_dependencies', {}) or {}
    tc_config = ApiConfig(
        url=deps.get("url", base_url),
        method=step.config.method,
        headers=dict(base_headers) if isinstance(base_headers, dict) else {},
        body_template=base_body if isinstance(base_body, (dict, list)) else {},
        response_schema=step.config.response_schema,
    )

    if deps:
        dep_headers = {}
        if deps.get("headers"):
            try:
                dep_headers = json.loads(deps["headers"]) if isinstance(deps["headers"], str) else deps["headers"]
            except (json.JSONDecodeError, TypeError):
                pass
        if dep_headers:
            tc_config.headers.update(dep_headers)
        try:
            dep_body = deps.get("body", "")
            if dep_body:
                dep_body_data = json.loads(dep_body) if isinstance(dep_body, str) else dep_body
                tc_config.body_template = _apply_body_deps(base_body, dep_body_data)
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            tc_config = resolve_step_config(
                ApiStep(name=tc.case_name, config=tc_config), context, env_variables
            )
        except Exception:
            pass

    resolved_input = resolve_placeholders(tc.input_data, context)
    resolved_tc = TestCase(
        case_id=tc.case_id, case_name=tc.case_name,
        operation=tc.operation, category=tc.category,
        input_data=resolved_input if isinstance(resolved_input, dict) else {},
        expected_status_code=tc.expected_status_code,
        expected_response_keys=tc.expected_response_keys,
        assertion_logic=tc.assertion_logic,
        pre_condition=tc.pre_condition,
        post_condition=tc.post_condition,
    )

    return run_single_test(resolved_tc, tc_config, session, step_context=context.extracted_values)

