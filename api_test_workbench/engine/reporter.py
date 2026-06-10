"""测试报告导出 — HTML 和 JSON 格式"""

import json
from datetime import datetime
from typing import Any

from api_test_workbench.engine.models import Pipeline, PipelineResult, StepResult, TestResult


def _time_color(ms: float) -> str:
    if ms <= 0:
        return "#94A3B8"
    if ms < 500:
        return "#22C55E"
    if ms < 2000:
        return "#F59E0B"
    return "#EF4444"


def _status_badge(passed: bool) -> str:
    if passed:
        return '<span style="color:#22C55E;font-weight:700;">✓ PASS</span>'
    return '<span style="color:#EF4444;font-weight:700;">✗ FAIL</span>'


def _render_json_block(data: Any, max_len: int = 4000) -> str:
    """安全渲染 JSON 数据块"""
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(data)
    if len(text) > max_len:
        text = text[:max_len] + "\n... (截断)"
    return f"<pre style='background:#0F172A;color:#E2E8F0;padding:12px;border-radius:8px;overflow-x:auto;font-size:0.8rem;max-height:400px;overflow-y:auto;'>{_escape_html(text)}</pre>"


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_test_result_card(r: TestResult, step_name: str) -> str:
    """渲染单条测试结果卡片"""
    icon = "✓" if r.passed else "✗"
    border_color = "#22C55E" if r.passed else "#EF4444"
    bg = "rgba(34,197,94,0.06)" if r.passed else "rgba(239,68,68,0.06)"

    # 响应体
    resp_html = ""
    if isinstance(r.response_body, dict):
        resp_html = _render_json_block(r.response_body)
    elif r.response_body is not None:
        resp_html = f"<pre style='background:#0F172A;color:#E2E8F0;padding:12px;border-radius:8px;overflow-x:auto;font-size:0.8rem;'>{_escape_html(str(r.response_body)[:3000])}</pre>"

    # 请求体
    req_html = _render_json_block(r.request_body, 2000)

    # 错误信息
    error_html = ""
    if r.error_message:
        error_html = f"""
        <div style='background:rgba(239,68,68,0.12);border-left:3px solid #EF4444;padding:10px 14px;border-radius:4px;margin:8px 0;'>
            <strong style='color:#EF4444;'>错误:</strong>
            <pre style='color:#FCA5A5;margin:4px 0 0 0;white-space:pre-wrap;font-size:0.8rem;'>{_escape_html(r.error_message)}</pre>
        </div>"""

    time_html = ""
    if r.response_time_ms > 0:
        tc = _time_color(r.response_time_ms)
        time_html = f"<span style='color:{tc};font-weight:600;margin-left:12px;'>⏱ {r.response_time_ms:.0f} ms</span>"

    return f"""
    <div style='background:{bg};border:1px solid {border_color};border-radius:10px;padding:14px;margin:10px 0;'>
        <div style='display:flex;justify-content:space-between;align-items:center;'>
            <span>
                <strong style='font-size:1rem;'>{icon} {_escape_html(r.case_name)}</strong>
                <span style='color:#94A3B8;margin-left:8px;font-size:0.85rem;'>{_escape_html(r.case_id)}</span>
            </span>
            <span>
                <span style='color:#94A3B8;font-size:0.85rem;'>状态码: {r.actual_status_code} (期望 {r.expected_status_code})</span>
                {time_html}
            </span>
        </div>
        {error_html}
        <div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:10px;'>
            <div>
                <div style='color:#94A3B8;font-size:0.8rem;font-weight:600;margin-bottom:4px;'>📤 请求体</div>
                {req_html}
                <div style='color:#64748B;font-size:0.75rem;margin-top:2px;'>URL: {_escape_html(r.request_url[:120])}</div>
            </div>
            <div>
                <div style='color:#94A3B8;font-size:0.8rem;font-weight:600;margin-bottom:4px;'>📥 响应体</div>
                {resp_html}
            </div>
        </div>
    </div>"""


def generate_html_report(result: PipelineResult, pipeline: Pipeline) -> str:
    """生成自包含的 HTML 测试报告。

    Returns:
        完整的 HTML 字符串，包含内联 CSS，可直接保存为 .html 文件。
    """
    total_passed = sum(1 for sr in result.step_results for r in sr.test_results if r.passed)
    total_failed = sum(1 for sr in result.step_results for r in sr.test_results if not r.passed)
    total = total_passed + total_failed
    overall_icon = "✓" if result.overall_passed else "✗"
    overall_color = "#22C55E" if result.overall_passed else "#EF4444"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 计算总耗时
    total_time = sum(
        r.response_time_ms
        for sr in result.step_results
        for r in sr.test_results
        if r.response_time_ms > 0
    )

    # 步骤卡片
    step_cards = []
    for i, sr in enumerate(result.step_results):
        step_passed = sum(1 for r in sr.test_results if r.passed) if sr.test_results else 0
        step_total = len(sr.test_results)
        step_icon = "✓" if sr.passed else ("⏭" if sr.skipped else "✗")

        if sr.skipped:
            step_cards.append(f"""
            <div style='background:#1E293B;border:2px dashed #475569;border-radius:12px;padding:16px;margin:12px 0;opacity:0.6;'>
                <h3 style='margin:0;color:#94A3B8;'>⏭ Step {i+1}: {_escape_html(sr.step_name)} <span style='font-size:0.85rem;'>已跳过</span></h3>
                <p style='color:#64748B;margin:4px 0 0 0;'>{_escape_html(sr.error_message)}</p>
            </div>""")
            continue

        test_cards = "".join(_render_test_result_card(r, sr.step_name) for r in sr.test_results)

        step_cards.append(f"""
        <div style='background:#1E293B;border:1px solid #334155;border-radius:12px;padding:16px;margin:12px 0;'>
            <h3 style='margin:0 0 8px 0;color:#F1F5F9;'>
                {step_icon} Step {i+1}: {_escape_html(sr.step_name)}
                <span style='font-size:0.85rem;color:#94A3B8;font-weight:400;'> — {step_passed}/{step_total} 通过</span>
            </h3>
            {test_cards}
        </div>""")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>API 测试报告 — {_escape_html(pipeline.name)}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    background: #0F172A; color: #E2E8F0; line-height:1.6; padding:24px;
}}
.container {{ max-width:1200px; margin:0 auto; }}
.header {{
    background: linear-gradient(135deg, #1E293B 0%, #0F172A 100%);
    border:1px solid #334155; border-radius:14px; padding:24px 28px; margin-bottom:20px;
}}
.header h1 {{ font-size:1.5rem; color:#F1F5F9; margin-bottom:4px; }}
.header .meta {{ color:#64748B; font-size:0.85rem; margin-top:4px; }}
.summary {{
    display:flex; gap:20px; margin-top:16px; flex-wrap:wrap;
}}
.summary-card {{
    background:#0F172A; border:1px solid #334155; border-radius:10px;
    padding:16px 24px; text-align:center; min-width:100px;
}}
.summary-card .value {{ font-size:1.5rem; font-weight:700; }}
.summary-card .label {{ font-size:0.8rem; color:#64748B; margin-top:2px; }}
.pass {{ color:#22C55E; }} .fail {{ color:#EF4444; }} .time {{ color:#3B82F6; }}
.badge {{
    display:inline-block; padding:6px 16px; border-radius:20px;
    font-weight:700; font-size:0.9rem; margin-bottom:12px;
}}
.badge-pass {{ background:rgba(34,197,94,0.15); color:#22C55E; border:1px solid #22C55E; }}
.badge-fail {{ background:rgba(239,68,68,0.15); color:#EF4444; border:1px solid #EF4444; }}
.footer {{ text-align:center; color:#475569; font-size:0.8rem; margin-top:24px; padding:16px; }}
pre {{ white-space:pre-wrap; word-break:break-all; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>API 测试报告</h1>
        <div class="meta">{_escape_html(pipeline.name)} · {len(pipeline.steps)} 个步骤 · 生成于 {now}</div>
        <div style='margin-top:12px;'>
            <span class="badge {'badge-pass' if result.overall_passed else 'badge-fail'}">{overall_icon} {'全部通过' if result.overall_passed else '存在失败'}</span>
        </div>
        <div class="summary">
            <div class="summary-card">
                <div class="value" style="color:#E2E8F0;">{total}</div>
                <div class="label">总计</div>
            </div>
            <div class="summary-card">
                <div class="value pass">{total_passed}</div>
                <div class="label">通过</div>
            </div>
            <div class="summary-card">
                <div class="value fail">{total_failed}</div>
                <div class="label">失败</div>
            </div>
            <div class="summary-card">
                <div class="value time">{total_time:.0f} ms</div>
                <div class="label">总耗时</div>
            </div>
            <div class="summary-card">
                <div class="value" style="color:#E2E8F0;">{len(result.step_results)}</div>
                <div class="label">步骤数</div>
            </div>
        </div>
    </div>
    {"".join(step_cards)}
    <div class="footer">
        API Test Workbench · 由 Claude Code + Streamlit 驱动
    </div>
</div>
</body>
</html>"""


def generate_json_report(result: PipelineResult) -> dict:
    """生成 JSON 格式的测试报告字典。

    Returns:
        可 JSON 序列化的 dict，包含完整的执行结果数据。
    """
    step_results = []
    for sr in result.step_results:
        test_results = []
        for r in sr.test_results:
            test_results.append({
                "case_id": r.case_id,
                "case_name": r.case_name,
                "passed": r.passed,
                "actual_status_code": r.actual_status_code,
                "expected_status_code": r.expected_status_code,
                "response_time_ms": r.response_time_ms,
                "request_url": r.request_url,
                "request_body": r.request_body,
                "response_body": r.response_body if isinstance(r.response_body, (dict, list)) else str(r.response_body) if r.response_body else None,
                "error_message": r.error_message,
            })
        step_results.append({
            "step_index": sr.step_index,
            "step_name": sr.step_name,
            "passed": sr.passed,
            "skipped": sr.skipped,
            "error_message": sr.error_message,
            "extracted_data": sr.extracted_data,
            "test_results": test_results,
        })

    total_passed = sum(1 for sr in result.step_results for r in sr.test_results if r.passed)
    total_failed = sum(1 for sr in result.step_results for r in sr.test_results if not r.passed)
    total_time = sum(r.response_time_ms for sr in result.step_results for r in sr.test_results if r.response_time_ms > 0)

    return {
        "pipeline_name": result.pipeline_name,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overall_passed": result.overall_passed,
        "stopped_at_step": result.stopped_at_step,
        "summary": {
            "total": total_passed + total_failed,
            "passed": total_passed,
            "failed": total_failed,
            "total_time_ms": round(total_time, 1),
            "steps": len(result.step_results),
        },
        "step_results": step_results,
    }
