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
    data_dependencies: dict = field(default_factory=dict)  # {url, body, headers} 运行时注入，不修改用户配置


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


# ==================== Pipeline 相关模型 ====================


@dataclass
class DataBinding:
    """步骤间数据依赖关系（从模板自动扫描）"""
    source_step_index: int      # 数据来源步骤索引 (0-based)
    source_field: str           # 提取路径，如 "response.data.id"
    target_step_index: int      # 注入目标步骤索引 (0-based)
    target_location: str        # "url" | "body.<path>" | "headers.<name>"
    placeholder: str = ""       # 原始占位符文本，如 "{{step1.response.data.id}}"


@dataclass
class ApiStep:
    """Pipeline 中单个步骤"""
    name: str = ""                              # 步骤名，如 "创建订单"
    config: ApiConfig = field(default_factory=ApiConfig)
    on_failure: str = "stop"                    # "stop" | "continue"


@dataclass
class Pipeline:
    """完整 Pipeline 定义"""
    name: str = "Pipeline"
    steps: list = field(default_factory=list)   # list[ApiStep]


@dataclass
class StepResult:
    """单个步骤的执行结果"""
    step_index: int
    step_name: str
    test_results: list = field(default_factory=list)   # list[TestResult]
    passed: bool = False
    extracted_data: dict = field(default_factory=dict)  # 提取出来供后续步骤使用的数据
    skipped: bool = False
    error_message: str = ""


@dataclass
class PipelineResult:
    """Pipeline 整体执行结果"""
    pipeline_name: str
    step_results: list = field(default_factory=list)   # list[StepResult]
    overall_passed: bool = False
    stopped_at_step: int = -1


@dataclass
class PipelineContext:
    """运行时共享上下文（内部用，不持久化）"""
    extracted_values: dict = field(default_factory=dict)
    # extracted_values[step_index] = {"response.data.id": 123, ...}
