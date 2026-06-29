"""结构化断言执行器

将 AI 生成的结构化 assertions 数组转换为可执行的断言检查。
支持跨步骤数据对比（field_diff），兼容旧格式 assertion_logic 字符串。
"""

from typing import Any, Optional
from api_test_workbench.engine.logger import setup_logger

log = setup_logger("assertion_runner")


class AssertionRunner:
    """执行结构化断言并返回结果列表。

    支持断言类型：
    - code_equals: 响应码等于期望值
    - field_exists: 字段存在
    - field_diff: 跨步骤字段对比
    - field_gte: 字段值 >= 期望值
    - custom: 旧格式 assertion_logic 字符串（fallback）
    """

    def __init__(self, step_results: dict):
        """step_results: {step_index: {"extracted": {"total": "5", ...}, "response": {...}}}"""
        self.step_results = step_results

    def run(
        self,
        assertions: list[dict],
        assertion_logic: str = "",
        current_step: int = 0,
        current_response: Optional[dict] = None,
        current_extracted: Optional[dict] = None,
        eval_fn=None,
    ) -> list[dict]:
        """执行断言列表。

        Args:
            assertions: 新格式断言列表 [{"type": "code_equals", ...}]
            assertion_logic: 旧格式断言字符串（向后兼容）
            current_step: 当前步骤索引
            current_response: 当前步骤的原始响应
            current_extracted: 当前步骤提取的字段值（语义化）
            eval_fn: 旧格式断言的 eval 函数（_safe_eval_assertion）

        Returns:
            [{"pass": bool, "type": "code_equals", "expected": ..., "actual": ...}, ...]
        """
        results = []

        # ── 新格式：结构化断言 ──
        if assertions:
            for assertion in assertions:
                result = self._evaluate(assertion, current_step, current_response, current_extracted)
                results.append(result)

        # ── 旧格式：assertion_logic 字符串（向后兼容）──
        if assertion_logic and eval_fn:
            try:
                passed = eval_fn(assertion_logic)
                results.append({
                    "pass": passed,
                    "type": "custom",
                    "expression": assertion_logic,
                    "actual": "assertion evaluated",
                })
            except Exception as e:
                results.append({
                    "pass": False,
                    "type": "custom",
                    "expression": assertion_logic,
                    "error": str(e),
                })

        return results

    def _evaluate(
        self,
        assertion: dict,
        current_step: int,
        current_response: Optional[dict],
        current_extracted: Optional[dict],
    ) -> dict:
        """评估单个结构化断言"""
        atype = assertion.get("type", "")

        if atype == "code_equals":
            return self._eval_code_equals(assertion, current_response)

        elif atype == "field_exists":
            return self._eval_field_exists(assertion, current_extracted)

        elif atype == "field_diff":
            return self._eval_field_diff(assertion, current_step, current_extracted)

        elif atype == "field_gte":
            return self._eval_field_gte(assertion, current_extracted)

        else:
            return {
                "pass": True,
                "type": atype,
                "warning": f"未知断言类型 '{atype}'，已跳过",
            }

    def _eval_code_equals(self, assertion: dict, response: Optional[dict]) -> dict:
        """响应码断言"""
        expected = str(assertion.get("expected", "0"))
        if response and isinstance(response, dict):
            actual = str(response.get("code", ""))
        else:
            actual = ""
        return {
            "pass": actual == expected,
            "type": "code_equals",
            "expected": expected,
            "actual": actual,
        }

    def _eval_field_exists(self, assertion: dict, extracted: Optional[dict]) -> dict:
        """字段存在断言"""
        path = assertion.get("path", "")
        exists = False
        if extracted and isinstance(extracted, dict):
            exists = path in extracted and extracted[path] is not None
        return {
            "pass": exists,
            "type": "field_exists",
            "path": path,
            "actual": "exists" if exists else "missing",
        }

    def _eval_field_diff(
        self,
        assertion: dict,
        current_step: int,
        current_extracted: Optional[dict],
    ) -> dict:
        """跨步骤字段对比断言"""
        field = assertion.get("field", "")
        ref_step = assertion.get("ref_step", 0)
        ref_field = assertion.get("ref_field", field)
        operator = assertion.get("operator", "equal")
        expected_diff = assertion.get("expected_diff", 0)

        # 获取当前步骤的字段值
        cur_value = None
        if current_extracted and isinstance(current_extracted, dict):
            cur_value = current_extracted.get(field)

        # 获取参考步骤的字段值
        ref_value = None
        if ref_step in self.step_results:
            ref_extracted = self.step_results[ref_step].get("extracted", {})
            if isinstance(ref_extracted, dict):
                ref_value = ref_extracted.get(ref_field)

        # 值缺失处理
        if cur_value is None or ref_value is None:
            return {
                "pass": False,
                "type": "field_diff",
                "error": "字段未提取到",
                "ref_step": ref_step,
                "ref_field": ref_field,
                "ref_value": ref_value,
                "cur_value": cur_value,
            }

        try:
            cur_int = int(cur_value)
            ref_int = int(ref_value)
            diff = cur_int - ref_int

            if operator == "equal":
                passed = diff == expected_diff
            elif operator == "gt":
                passed = diff > 0
            elif operator == "lt":
                passed = diff < 0
            elif operator == "gte":
                passed = diff >= 0
            elif operator == "lte":
                passed = diff <= 0
            else:
                return {
                    "pass": False,
                    "type": "field_diff",
                    "error": f"未知操作符 '{operator}'",
                }

            return {
                "pass": passed,
                "type": "field_diff",
                "field": field,
                "ref_step": ref_step,
                "ref_value": ref_value,
                "cur_value": cur_value,
                "diff": diff,
                "expected_diff": expected_diff,
                "operator": operator,
            }
        except (ValueError, TypeError) as e:
            return {
                "pass": False,
                "type": "field_diff",
                "error": f"数值转换失败: {e}",
                "ref_value": ref_value,
                "cur_value": cur_value,
            }

    def _eval_field_gte(self, assertion: dict, extracted: Optional[dict]) -> dict:
        """字段值 >= 期望值"""
        field = assertion.get("field", "")
        expected = assertion.get("expected", 0)

        actual = None
        if extracted and isinstance(extracted, dict):
            actual = extracted.get(field)

        try:
            actual_int = int(actual) if actual is not None else 0
            passed = actual_int >= int(expected)
            return {
                "pass": passed,
                "type": "field_gte",
                "field": field,
                "expected": expected,
                "actual": actual_int,
            }
        except (ValueError, TypeError) as e:
            return {
                "pass": False,
                "type": "field_gte",
                "error": str(e),
                "field": field,
            }
