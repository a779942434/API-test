"""API 测试工作台 — 数据模型"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ApiConfig:
    """单个 API 的配置"""
    name: str = ""
    url: str = ""
    method: str = "POST"
    headers: dict = field(default_factory=lambda: {"Content-Type": "application/json"})
    body_template: dict = field(default_factory=dict)
    auth_endpoint: str = ""
    auth_body: dict = field(default_factory=dict)


@dataclass
class TestCase:
    """单条测试用例"""
    case_id: str
    case_name: str
    operation: str  # create|read|update|delete|list
    category: str   # positive|negative|boundary|equivalence|dependency
    input_data: dict
    expected_status_code: int
    expected_response_keys: list = field(default_factory=list)
    assertion_logic: str = ""
    pre_condition: str = ""
    post_condition: str = ""


@dataclass
class TestResult:
    """单条测试执行结果"""
    case_id: str
    case_name: str
    passed: bool
    actual_status_code: int
    expected_status_code: int
    response_body: Any
    error_message: str = ""
    request_body: dict = field(default_factory=dict)
    request_url: str = ""
