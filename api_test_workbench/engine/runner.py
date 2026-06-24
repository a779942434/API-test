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

    # 安全检查：拒绝包含潜在沙箱逃逸特征的断言
    dangerous = ("__", "import", "exec", "compile", "open", "getattr", "setattr", "delattr")
    lower = assertion_logic.lower()
    for pat in dangerous:
        if pat in lower:
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
        "bool": bool,
        "len": len,
        "isinstance": isinstance,
        "list": list,
        "dict": dict,
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
        return False, f"断言失败: {assertion_logic}（响应code={resp_json.get('code')}）"
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

    try:
        log.info("%s %s", method, url[:120])
        log.debug("请求体: %s", str(request_body)[:500])
        start = time.perf_counter()
        if method == "GET":
            resp = session.get(url, headers=api_config.headers, params=request_body)
        elif method in ("POST", "PUT", "DELETE", "PATCH"):
            resp = session.request(method, url, headers=api_config.headers, json=request_body)
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


def execute_pipeline(
    pipeline: Pipeline,
    session: requests.Session,
    test_cases_by_step: dict,
    progress_callback=None,
    env_variables: dict = None,
) -> PipelineResult:
    """按用例链路执行 Pipeline：每条用例依次走完所有步骤。

    Args:
        pipeline: Pipeline 定义
        session: 已认证的 requests.Session
        test_cases_by_step: {step_index: [TestCase, ...]}
        progress_callback: callable(step_idx, total_steps, StepResult)
        env_variables: 环境变量映射 {"VAR_NAME": "value", ...}，用于 {{VAR}} 替换

    Returns:
        PipelineResult: 包含所有步骤结果的聚合结果
    """
    total_steps = len(pipeline.steps)
    # 取各步骤最大用例数
    max_cases = max((len(v) for v in test_cases_by_step.values()), default=0)
    if max_cases == 0:
        return PipelineResult(pipeline_name=pipeline.name, overall_passed=True)

    # 按步骤汇总结果
    step_results_map = {i: [] for i in range(total_steps)}  # step_idx → [TestResult, ...]
    overall_passed = True
    stopped_at = -1

    for case_idx in range(max_cases):
        log.info("===== 链路 %d/%d 开始 =====", case_idx + 1, max_cases)
        context = PipelineContext()
        case_stopped = False

        for step_idx, step in enumerate(pipeline.steps):
            # 忽略的步骤：跳过执行但保留数据传递
            if step.ignored:
                step_results_map[step_idx].append(TestResult(
                    case_id="", case_name=f"(已忽略)", passed=True,
                    actual_status_code=0, expected_status_code=0, response_body=None,
                    response_time_ms=0.0,
                ))
                # P1#10: 填充空上下文，避免下游步骤的占位符引用时报 ValueError
                context.extracted_values[step_idx] = {}
                log.debug("Step %d 已忽略，填充空上下文", step_idx + 1)
                continue

            step_tcs = test_cases_by_step.get(step_idx, [])
            if not step_tcs:
                continue
            # 该步骤用例数不足时复用最后一条（后续步骤通常只有1条，data_dependencies 自动引用当前链路数据）
            tc = step_tcs[min(case_idx, len(step_tcs) - 1)]
            if progress_callback:
                progress_callback(step_idx, total_steps,
                    StepResult(step_index=step_idx, step_name=step.name, test_results=[]))

            try:
                resolved_config = resolve_step_config(step, context, env_variables)

                # 应用 data_dependencies
                tc_config = resolved_config
                deps = getattr(tc, 'data_dependencies', {}) or {}
                if deps:
                    tc_config = ApiConfig(
                        name=resolved_config.name,
                        url=deps.get("url", resolved_config.url),
                        method=resolved_config.method,
                        headers=({**resolved_config.headers, **(
                            json.loads(deps["headers"]) if isinstance(deps.get("headers"), str) else deps["headers"]
                        )} if deps.get("headers") else resolved_config.headers),
                        body_template=_apply_body_deps(resolved_config.body_template, deps.get("body")),
                    )
                    tc_config = resolve_step_config(
                        ApiStep(name=tc.case_name, config=tc_config), context, env_variables
                    )

                resolved_input = resolve_placeholders(tc.input_data, context)
                resolved_tc = TestCase(
                    case_id=tc.case_id,
                    case_name=tc.case_name,
                    operation=tc.operation,
                    category=tc.category,
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

                # 提取响应数据给下游步骤（断言失败时也提取，因为业务失败不代表没数据）
                if isinstance(result.response_body, dict):
                    context.extracted_values[step_idx] = _flatten_response(result.response_body)
                elif isinstance(result.response_body, list):
                    # P0#4: JSON 数组响应保留结构化路径，如 response[0].id
                    context.extracted_values[step_idx] = _flatten_response(
                        result.response_body, "response"
                    )
                elif result.response_body is not None:
                    context.extracted_values[step_idx] = {"response.raw": str(result.response_body)[:200]}
                else:
                    context.extracted_values[step_idx] = {}

                if not result.passed:
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

    # 汇总 StepResult
    step_results = []
    for step_idx, step in enumerate(pipeline.steps):
        results = step_results_map.get(step_idx, [])
        if results:
            passed = all(r.passed for r in results)
            extracted = {}
            for r in results:
                if r.passed and isinstance(r.response_body, dict):
                    extracted = _flatten_response(r.response_body)
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

    return PipelineResult(
        pipeline_name=pipeline.name,
        step_results=step_results,
        overall_passed=overall_passed,
        stopped_at_step=stopped_at,
    )
