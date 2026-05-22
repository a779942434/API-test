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


def _safe_eval_assertion(assertion_logic: str, resp: requests.Response) -> tuple[bool, str]:
    """在受限上下文中执行断言逻辑字符串。

    Returns:
        (passed, error_message)
    """
    if not assertion_logic:
        return True, ""

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
        if method == "GET":
            resp = session.get(url, headers=api_config.headers, params=request_body)
        elif method == "POST":
            resp = session.post(url, headers=api_config.headers, json=request_body)
        elif method == "PUT":
            resp = session.put(url, headers=api_config.headers, json=request_body)
        elif method == "DELETE":
            resp = session.delete(url, headers=api_config.headers, json=request_body)
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

    resp = session.post(auth_endpoint, json=auth_body, allow_redirects=False)
    if resp.status_code != 200:
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
    """按顺序执行 Pipeline 的所有步骤，处理步骤间的数据传递。

    Args:
        pipeline: Pipeline 定义
        session: 已认证的 requests.Session
        test_cases_by_step: {step_index: [TestCase, ...]}
        progress_callback: callable(step_idx, total_steps, StepResult)

    Returns:
        PipelineResult: 包含所有步骤结果的聚合结果
    """
    context = PipelineContext()
    step_results = []
    overall_passed = True
    stopped_at = -1
    total = len(pipeline.steps)

    for step_idx, step in enumerate(pipeline.steps):
        step_tcs = test_cases_by_step.get(step_idx, [])

        if not step_tcs:
            sr = StepResult(step_index=step_idx, step_name=step.name, passed=True)
            step_results.append(sr)
            if progress_callback:
                progress_callback(step_idx, total, sr)
            continue

        try:
            resolved_config = resolve_step_config(step, context)

            # 解析每个测试用例 input_data 中的占位符
            resolved_tcs = []
            for tc in step_tcs:
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
                resolved_tcs.append(resolved_tc)

            results = run_all_tests(resolved_tcs, resolved_config, session)
            passed = all(r.passed for r in results)

            # 从第一个通过的用例中提取响应数据
            extracted = {}
            for r in results:
                if r.passed and isinstance(r.response_body, dict):
                    extracted = _flatten_response(r.response_body)
                    break

            context.extracted_values[step_idx] = extracted
            sr = StepResult(
                step_index=step_idx,
                step_name=step.name,
                test_results=results,
                passed=passed,
                extracted_data=extracted,
            )

        except Exception as e:
            sr = StepResult(
                step_index=step_idx,
                step_name=step.name,
                passed=False,
                error_message=f"步骤执行异常: {str(e)}\n{traceback.format_exc()}",
            )
            context.extracted_values[step_idx] = {}

        step_results.append(sr)

        if not sr.passed:
            overall_passed = False
            if step.on_failure == "stop":
                stopped_at = step_idx
                # 将剩余步骤标记为 skipped
                for remaining in range(step_idx + 1, total):
                    remaining_step = pipeline.steps[remaining]
                    step_results.append(StepResult(
                        step_index=remaining,
                        step_name=remaining_step.name,
                        skipped=True,
                        error_message=f"因 Step {step_idx + 1} 失败而跳过",
                    ))
                break
            # on_failure == "continue": 继续执行后续步骤，但无提取数据

        if progress_callback:
            progress_callback(step_idx, total, sr)

    return PipelineResult(
        pipeline_name=pipeline.name,
        step_results=step_results,
        overall_passed=overall_passed,
        stopped_at_step=stopped_at,
    )
