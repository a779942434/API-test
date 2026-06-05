"""执行测试用例 + 断言验证"""

import json
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
from api_test_workbench.engine.logger import setup_logger

log = setup_logger("runner")


def _safe_eval_assertion(assertion_logic: str, resp: requests.Response) -> tuple[bool, str]:
    """在受限上下文中执行断言逻辑字符串。

    Returns:
        (passed, error_message)
    """
    if not assertion_logic:
        return True, ""

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
        "in": lambda a, b: a in b,
        "True": True,
        "False": False,
        "None": None,
    }

    try:
        result = eval(assertion_logic, {"__builtins__": {}}, safe_context)
        if result:
            return True, ""
        return False, f"断言失败: {assertion_logic}"
    except Exception as e:
        return False, f"断言执行异常: {str(e)}"


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
) -> TestResult:
    """执行单条测试用例"""
    url = api_config.url
    method = api_config.method.upper()

    # 根据 body_template 类型构造请求体
    #   dict: 以 body_template 为基础，input_data 字段覆盖合并
    #   list: 直接使用数组作为请求体（input_data 忽略）
    #   其他: 使用 input_data（dict）或空 dict
    template = api_config.body_template
    if isinstance(template, list):
        request_body = template
    elif isinstance(template, dict):
        data = tc.input_data if isinstance(tc.input_data, dict) else {}
        request_body = {**template, **data}
    elif isinstance(tc.input_data, dict):
        request_body = tc.input_data
    else:
        request_body = {}

    try:
        log.info("%s %s", method, url[:120])
        log.debug("请求体: %s", str(request_body)[:500])
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
            )

        # 解析响应体
        try:
            response_json = resp.json() if resp.text else {}
        except (json.JSONDecodeError, ValueError):
            response_json = resp.text
        log.debug("响应 %s: %s", resp.status_code, str(response_json)[:300])

        # 验证状态码
        passed = resp.status_code == tc.expected_status_code

        # 如果状态码通过，执行附加断言
        assertion_error = ""
        if passed and tc.assertion_logic:
            assertion_passed, assertion_error = _safe_eval_assertion(tc.assertion_logic, resp)
            passed = assertion_passed

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
        )

    except requests.RequestException as e:
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


def get_auth_session(auth_endpoint: str, auth_body: dict) -> requests.Session:
    """调用登录接口，返回已认证的 Session"""
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    resp = session.post(auth_endpoint, json=auth_body)
    if resp.status_code not in (200, 302):
        raise RuntimeError(f"登录失败 status={resp.status_code}: {resp.text}")

    result = resp.json()
    if result.get("code") != '0':
        raise RuntimeError(f"登录业务失败: {result}")

    return session


# ==================== Pipeline 执行引擎 ====================


def resolve_step_config(step: ApiStep, context: PipelineContext) -> ApiConfig:
    """将步骤配置中的所有占位符替换为实际值，返回新的 ApiConfig"""
    resolved_url = resolve_placeholders(step.config.url, context)
    resolved_headers = resolve_placeholders(step.config.headers, context)
    resolved_body = resolve_placeholders(step.config.body_template, context)
    if not isinstance(resolved_body, (dict, list)):
        resolved_body = {}
    resolved_auth_body = resolve_placeholders(step.config.auth_body, context)
    resolved_auth_endpoint = resolve_placeholders(step.config.auth_endpoint, context)

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
) -> PipelineResult:
    """按用例链路执行 Pipeline：每条用例依次走完所有步骤。

    Args:
        pipeline: Pipeline 定义
        session: 已认证的 requests.Session
        test_cases_by_step: {step_index: [TestCase, ...]}
        progress_callback: callable(step_idx, total_steps, StepResult)

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
                ))
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
                resolved_config = resolve_step_config(step, context)

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
                        ApiStep(name=tc.case_name, config=tc_config), context
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

                result = run_single_test(resolved_tc, tc_config, session)
                log.info("链路 %d Step %d [%s] %s → %s",
                         case_idx + 1, step_idx + 1, tc.case_name,
                         "PASS" if result.passed else "FAIL", result.actual_status_code)
                step_results_map[step_idx].append(result)

                # 提取响应数据给下游步骤（断言失败时也提取，因为业务失败不代表没数据）
                if isinstance(result.response_body, dict):
                    context.extracted_values[step_idx] = _flatten_response(result.response_body)
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
                ))
                context.extracted_values[step_idx] = {}
                overall_passed = False
                if step.on_failure == "stop":
                    case_stopped = True
                    if stopped_at < 0:
                        stopped_at = step_idx

            if case_stopped:
                break

        if case_stopped and stopped_at >= 0:
            # 当前 case 中断，剩余 case 全部跳过
            # 但已跑完的步骤结果保留
            pass

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
            extracted_data=extracted,
        ))

    return PipelineResult(
        pipeline_name=pipeline.name,
        step_results=step_results,
        overall_passed=overall_passed,
        stopped_at_step=stopped_at,
    )
