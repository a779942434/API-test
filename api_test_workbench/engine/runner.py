"""执行测试用例 + 断言验证"""

import json
import traceback
from typing import Any

import requests

from api_test_workbench.engine.models import ApiConfig, TestCase, TestResult


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

    request_body = tc.input_data

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
