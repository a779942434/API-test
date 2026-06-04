"""API 测试工作台 — Streamlit 主界面 (Pipeline 模式)"""

import json
import sys
import warnings
from pathlib import Path

# macOS LibreSSL 与 urllib3 v2 兼容性警告，不影响功能
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+")

import streamlit as st
import requests as req

# 确保项目根目录在 path 中
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from api_test_workbench.engine.models import (
    ApiConfig, TestCase, TestResult, ApiStep, Pipeline, DataBinding, PipelineResult,
)
from api_test_workbench.engine.runner import (
    run_all_tests, get_auth_session, execute_pipeline,
)
from api_test_workbench.engine.bindings import scan_placeholders
from api_test_workbench.engine.curl_parser import parse_curl

st.set_page_config(page_title="API 测试工作台 — Pipeline", layout="wide")
st.title("API 测试工作台 — Pipeline")

# ==================== 初始化 Session State ====================

def _create_default_step() -> ApiStep:
    return ApiStep(
        name="Step 1",
        config=ApiConfig(
            url="",
            method="POST",
            headers={"Content-Type": "application/json"},
            body_template={},
        ),
    )

def _init_session_state():
    defaults = {
        "pipeline": Pipeline(name="我的测试链路", steps=[_create_default_step()]),
        "pipeline_test_cases_by_step": {},    # dict[int, list[TestCase]]
        "pipeline_results": None,              # Optional[PipelineResult]
        "auth_url": "http://bird.ob.shuyilink.com/auth/auth-login",
        "auth_body": '{"username": "admin", "password": "sygl123456"}',
        "auth_session": None,
        "auth_ok": False,
        "field_requirements": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # 向后兼容：从旧版 session state 迁移
    if "api_url" in st.session_state and st.session_state.api_url:
        st.session_state.pipeline.steps[0].config.url = st.session_state.api_url
        st.session_state.pipeline.steps[0].config.method = st.session_state.get("api_method", "POST")
        try:
            st.session_state.pipeline.steps[0].config.headers = json.loads(
                st.session_state.get("api_headers", '{"Content-Type": "application/json"}')
            )
        except json.JSONDecodeError:
            pass
        try:
            st.session_state.pipeline.steps[0].config.body_template = json.loads(
                st.session_state.get("api_body_template", "{}")
            )
        except json.JSONDecodeError:
            pass

_init_session_state()


# ==================== 辅助函数 ====================

def _render_pipeline_flow(steps: list) -> str:
    """渲染 Pipeline 可视化流程条"""
    if not steps:
        return ""
    boxes = []
    colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336", "#00BCD4", "#795548", "#607D8B"]
    for i, step in enumerate(steps):
        color = colors[i % len(colors)]
        boxes.append(
            f'<div style="display:inline-flex;align-items:center;padding:10px 18px;margin:4px 0;'
            f'border:2px solid {color};border-radius:10px;text-align:center;'
            f'background:linear-gradient(135deg, #f9f9f9 0%, #fff 100%);min-width:100px;'
            f'box-shadow:0 2px 6px rgba(0,0,0,0.08)">'
            f'<div><div style="font-size:12px;color:{color};font-weight:bold;">Step {i+1}</div>'
            f'<div style="font-size:13px;color:#333;margin-top:2px;"><b>{step.name}</b></div>'
            f'<div style="font-size:11px;color:#999;margin-top:1px;">{step.config.method}</div></div>'
            f'</div>'
        )
        if i < len(steps) - 1:
            boxes.append(
                '<div style="display:inline-flex;align-items:center;font-size:20px;'
                'color:#aaa;margin:0 6px;font-weight:bold;">⟶</div>'
            )
    return (
        '<div style="display:flex;align-items:center;flex-wrap:wrap;padding:12px 0;'
        'overflow-x:auto;">' + "".join(boxes) + '</div>'
    )


def _scan_all_bindings(pipeline: Pipeline) -> list[DataBinding]:
    """扫描所有步骤配置中的占位符，返回数据依赖列表"""
    bindings = []
    for idx, step in enumerate(pipeline.steps):
        # 扫描 URL
        for b in scan_placeholders(step.config.url, idx):
            b.target_location = "url"
            bindings.append(b)
        # 扫描 Headers
        for b in scan_placeholders(step.config.headers, idx):
            b.target_location = "headers"
            bindings.append(b)
        # 扫描 Body template
        for b in scan_placeholders(step.config.body_template, idx):
            b.target_location = "body"
            bindings.append(b)
    return bindings


# ==================== ① Pipeline 配置 ====================

st.header("① Pipeline 配置")

# Pipeline 名称
pipeline_name = st.text_input("链路名称", value=st.session_state.pipeline.name, key="pipeline_name_input")
st.session_state.pipeline.name = pipeline_name

# 流程图
steps = st.session_state.pipeline.steps
if len(steps) >= 1:
    st.markdown(_render_pipeline_flow(steps), unsafe_allow_html=True)

st.markdown("---")

# 步骤编辑
st.subheader(f"步骤列表 ({len(steps)})")

# 需要删除的步骤索引（在循环中收集，循环后处理）
steps_to_delete = set()

for i, step in enumerate(steps):
    # widget 版本号：解析 curl 后递增，强制 widget 用新 value= 重新初始化
    ver_key = f"step_widget_ver_{i}"
    if ver_key not in st.session_state:
        st.session_state[ver_key] = 0
    ver = st.session_state[ver_key]

    with st.expander(f"Step {i+1}：{step.name or '(未命名)'}  — {step.config.method} {step.config.url or '(未设置URL)'}", expanded=(i == 0)):
        col_name, col_method, col_failure = st.columns([3, 1, 1.5])
        with col_name:
            step.name = st.text_input(f"步骤名称", value=step.name, key=f"step_name_{i}", placeholder="如：创建订单")
        with col_method:
            step.config.method = st.selectbox("Method", ["POST", "GET", "PUT", "DELETE"], key=f"step_method_{i}_v{ver}", index=["POST", "GET", "PUT", "DELETE"].index(step.config.method) if step.config.method in ["POST", "GET", "PUT", "DELETE"] else 0)
        with col_failure:
            step.on_failure = st.selectbox("失败策略", ["stop", "continue"], key=f"step_failure_{i}", index=0 if step.on_failure == "stop" else 1, help="stop=停止后续步骤, continue=忽略错误继续执行")

        step.config.url = st.text_input("接口地址", value=step.config.url, key=f"step_url_{i}_v{ver}", placeholder="http://bird.ob.shuyilink.com/linkim-pc/admin-console/tooling/sparePartDevice")

        # ── curl 命令粘贴解析 ──
        curl_col1, curl_col2 = st.columns([4, 1])
        with curl_col1:
            curl_input = st.text_area(
                "粘贴 curl 命令（可选，点击「解析」自动填充上方字段）",
                key=f"step_curl_{i}",
                height=80,
                placeholder="curl -X POST 'http://example.com/api' -H 'Content-Type: application/json' -d '{\"key\": \"value\"}'",
            )
        with curl_col2:
            st.write("")
            st.write("")
            parse_clicked = st.button("解析", key=f"curl_parse_btn_{i}", use_container_width=True)

        if parse_clicked:
            raw = curl_input.strip()
            if not raw:
                st.warning("请先粘贴 curl 命令")
            else:
                try:
                    parsed = parse_curl(raw)
                    step.config.method = parsed["method"]
                    if parsed["url"]:
                        step.config.url = parsed["url"]
                    if parsed["headers"]:
                        merged = dict(step.config.headers)
                        merged.update(parsed["headers"])
                        step.config.headers = merged
                    if parsed["body"] is not None:
                        try:
                            step.config.body_template = json.loads(parsed["body"])
                        except (json.JSONDecodeError, TypeError):
                            step.config.body_template = {"raw_body": parsed["body"]}
                    # 递增 widget 版本号，rerun 后所有 widget 用新 key 重新初始化
                    st.session_state[ver_key] = ver + 1
                    shortened = parsed['url'][:60] + ('...' if len(parsed['url']) > 60 else '')
                    st.success(f"已解析：{parsed['method']} {shortened}")
                    st.rerun()
                except ValueError as e:
                    st.warning(f"解析失败：{e}")
                except Exception as e:
                    st.warning(f"解析异常：{e}")

        col_h, col_b = st.columns(2)
        with col_h:
            headers_str = st.text_area("Headers (JSON)", value=json.dumps(step.config.headers, ensure_ascii=False, indent=2), height=80, key=f"step_headers_{i}_v{ver}")
            try:
                step.config.headers = json.loads(headers_str)
            except json.JSONDecodeError:
                st.warning("Headers JSON 格式错误")
        with col_b:
            body_str = st.text_area("Body 模板 (JSON)", value=json.dumps(step.config.body_template, ensure_ascii=False, indent=2), height=80, key=f"step_body_{i}_v{ver}", help="支持 {{step1.response.data.id}} 占位符引用上游数据")
            try:
                step.config.body_template = json.loads(body_str)
            except json.JSONDecodeError:
                pass  # 保留原始字符串，可能含未闭合的占位符

        # 占位符提示
        st.caption("占位符语法：`{{step1.response.data.id}}` = Step1 响应中的 `data.id` 字段。可用在 URL、Body、Headers 任意位置。")

        # 操作按钮
        btn_col1, btn_col2, btn_col3, _ = st.columns([1, 1, 1, 7])
        with btn_col1:
            if i > 0 and st.button("⬆ 上移", key=f"move_up_{i}"):
                steps[i], steps[i-1] = steps[i-1], steps[i]
                tcs = st.session_state.pipeline_test_cases_by_step
                tcs[i], tcs[i-1] = tcs.get(i-1, []), tcs.get(i, [])
                st.rerun()
        with btn_col2:
            if i < len(steps) - 1 and st.button("⬇ 下移", key=f"move_down_{i}"):
                steps[i], steps[i+1] = steps[i+1], steps[i]
                tcs = st.session_state.pipeline_test_cases_by_step
                tcs[i], tcs[i+1] = tcs.get(i+1, []), tcs.get(i, [])
                st.rerun()
        with btn_col3:
            if len(steps) > 1 and st.button("🗑 删除", key=f"delete_{i}"):
                steps_to_delete.add(i)

# 执行删除
if steps_to_delete:
    st.session_state.pipeline.steps = [s for i, s in enumerate(steps) if i not in steps_to_delete]
    st.rerun()

# 移除重复的占位符提示

# 添加步骤按钮
if st.button("+ 添加步骤", use_container_width=True, type="secondary"):
    new_idx = len(st.session_state.pipeline.steps) + 1
    st.session_state.pipeline.steps.append(ApiStep(
        name=f"Step {new_idx}",
        config=ApiConfig(method="GET", headers={"Content-Type": "application/json"}, body_template={}),
    ))
    st.rerun()

st.markdown("---")

# 数据链路概览
bindings = _scan_all_bindings(st.session_state.pipeline)
if bindings:
    st.subheader("数据链路概览")
    binding_rows = []
    for b in bindings:
        binding_rows.append({
            "来源步骤": f"Step {b.source_step_index + 1}",
            "来源字段": b.source_field,
            "注入步骤": f"Step {b.target_step_index + 1}",
            "注入位置": b.target_location,
        })
    st.dataframe(binding_rows, use_container_width=True, hide_index=True)
elif len(steps) > 1:
    st.info("尚未定义步骤间的数据依赖。在 Body/URL 中使用 `{{step1.response.data.id}}` 语法来建立数据链路。")


# ==================== ② 字段定义 & 生成 ====================

st.header("② 字段定义 & 测试数据生成")

st.text_area(
    "粘贴 Pipeline 字段定义（每步接口的字段约束、业务规则）",
    key="field_requirements",
    height=160,
    placeholder="""描述整个 Pipeline 的数据流和各步接口的字段约束：

Step 1（创建订单）：
- name: string, 必填, 1-100位
- quantity: int, 必填, 1-9999

Step 2（查询订单）：
- 用 Step1 返回的 data.id 拼接到 URL

Step 3（更新订单状态）：
- id: 来自 Step1
- status: enum[shipped, delivered, cancelled]""",
)

gen_col1, gen_col2, _ = st.columns([1.5, 1, 3])
with gen_col1:
    test_cases_per_step = st.number_input("每步用例数", min_value=1, max_value=10, value=1, step=1, key="tc_per_step",
                                          help="Pipeline 模式建议设 1，只生成核心链路")
with gen_col2:
    st.write("")
    generate_clicked = st.button("生成测试数据", type="primary", use_container_width=True)

if generate_clicked:
    if not st.session_state.field_requirements.strip():
        st.error("请先填写字段定义")
    else:
        with st.spinner("正在调用 AI API 生成 Pipeline 测试数据..."):
            try:
                from api_test_workbench.engine.generator import generate_pipeline_test_cases

                test_cases_by_step = generate_pipeline_test_cases(
                    pipeline_description=st.session_state.field_requirements,
                    pipeline=st.session_state.pipeline,
                    test_cases_per_step=test_cases_per_step,
                )
                st.session_state.pipeline_test_cases_by_step = test_cases_by_step
                total = sum(len(v) for v in test_cases_by_step.values())
                st.success(f"已为 {len(test_cases_by_step)} 个步骤生成 {total} 条测试用例")
            except Exception as e:
                st.error(f"生成失败: {e}")
                st.session_state.pipeline_test_cases_by_step = {}

# 展示 & 编辑测试用例（按步骤 Tab）
tcs_by_step = st.session_state.pipeline_test_cases_by_step
if tcs_by_step:
    st.subheader(f"测试用例（{len(tcs_by_step)} 个步骤，{sum(len(v) for v in tcs_by_step.values())} 条）")

    step_indices = sorted(tcs_by_step.keys())
    tab_labels = []
    for idx in step_indices:
        step_name = st.session_state.pipeline.steps[idx].name if idx < len(st.session_state.pipeline.steps) else f"Step {idx+1}"
        count = len(tcs_by_step[idx])
        tab_labels.append(f"Step {idx+1}: {step_name} ({count})")

    tabs = st.tabs(tab_labels)

    for tab_i, step_idx in enumerate(step_indices):
        with tabs[tab_i]:
            tc_list = tcs_by_step[step_idx]
            tc_data = []
            for tc in tc_list:
                tc_data.append({
                    "case_id": tc.case_id,
                    "case_name": tc.case_name,
                    "category": tc.category,
                    "operation": tc.operation,
                    "expected_status": tc.expected_status_code,
                    "input_data": json.dumps(tc.input_data, ensure_ascii=False),
                    "assertion_logic": tc.assertion_logic,
                })

            edited_data = st.data_editor(
                tc_data,
                column_config={
                    "case_id": st.column_config.TextColumn("ID", width="small"),
                    "case_name": st.column_config.TextColumn("用例名称", width="medium"),
                    "category": st.column_config.SelectboxColumn("类型", options=["positive", "negative", "boundary", "equivalence", "dependency"], width="small"),
                    "operation": st.column_config.SelectboxColumn("操作", options=["create", "read", "update", "delete", "list"], width="small"),
                    "expected_status": st.column_config.NumberColumn("期望状态码", width="small"),
                    "input_data": st.column_config.TextColumn("请求体 JSON", width="large"),
                    "assertion_logic": st.column_config.TextColumn("断言逻辑", width="medium"),
                },
                num_rows="dynamic",
                use_container_width=True,
                height=min(len(tc_data) * 38 + 40, 400),
                key=f"tc_editor_step_{step_idx}",
            )

            # 同步编辑结果回 TestCase 对象（处理增/删/改）
            new_tc_list = []
            for j, row in enumerate(edited_data):
                if j < len(tc_list):
                    tc = tc_list[j]
                else:
                    tc = TestCase(case_id="", case_name="", operation="create", category="positive", input_data={}, expected_status_code=200)
                tc.case_id = row["case_id"]
                tc.case_name = row["case_name"]
                tc.category = row["category"]
                tc.operation = row["operation"]
                tc.expected_status_code = row["expected_status"]
                try:
                    parsed = json.loads(row["input_data"])
                    tc.input_data = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    pass
                tc.assertion_logic = row["assertion_logic"]
                new_tc_list.append(tc)
            tcs_by_step[step_idx] = new_tc_list


# ==================== ③ 认证 & 执行 ====================

st.header("③ 认证 & 执行")

# 登录区
with st.expander("登录认证", expanded=not st.session_state.auth_ok):
    c1, c2, c3 = st.columns([3, 2, 1])
    with c1:
        st.text_input("登录接口地址", key="auth_url", placeholder="http://bird.ob.shuyilink.com/auth/auth-login")
    with c2:
        st.text_area("登录 Body (JSON)", key="auth_body", height=100)
    with c3:
        st.write("")
        st.write("")
        if st.button("获取 Session", use_container_width=True):
            try:
                auth_body = json.loads(st.session_state.auth_body)
                session = get_auth_session(st.session_state.auth_url, auth_body)
                st.session_state.auth_session = session
                st.session_state.auth_ok = True
                st.success("登录成功")
            except Exception as e:
                st.session_state.auth_ok = False
                st.error(f"登录失败: {e}")

    if st.session_state.auth_ok:
        st.success("Session 已就绪")
    else:
        st.warning("未登录，执行测试前请先获取 Session")

# 执行区
has_cases = any(len(v) > 0 for v in st.session_state.pipeline_test_cases_by_step.values())
exec_disabled = not has_cases

exec_col1, _ = st.columns([1, 4])
with exec_col1:
    run_clicked = st.button("执行 Pipeline", type="primary", use_container_width=True, disabled=exec_disabled)

if run_clicked:
    if not st.session_state.auth_ok:
        st.error("请先在「登录认证」区域获取 Session")
    else:
        pipeline = st.session_state.pipeline
        tcs_by_step = st.session_state.pipeline_test_cases_by_step

        progress_bar = st.progress(0, "准备执行...")
        status_text = st.empty()

        def update_progress(current, total, result):
            progress_bar.progress(current / total, f"执行 Step {current+1}/{total}")
            icon = "✓" if result.passed else "✗"
            if result.skipped:
                icon = "⏭"
            status_text.text(f"{icon} Step {current+1}: {result.step_name} — {'跳过' if result.skipped else f'{sum(1 for r in result.test_results if r.passed)}/{len(result.test_results)} 通过'}")

        with st.spinner("执行 Pipeline 中..."):
            results = execute_pipeline(
                pipeline=pipeline,
                session=st.session_state.auth_session,
                test_cases_by_step=tcs_by_step,
                progress_callback=update_progress,
            )
            st.session_state.pipeline_results = results

        progress_bar.empty()
        status_text.empty()

# 展示结果
pipeline_results = st.session_state.pipeline_results
if pipeline_results:
    sr_list = pipeline_results.step_results
    total_passed = sum(1 for sr in sr_list if sr.passed and not sr.skipped)
    total_failed = sum(1 for sr in sr_list if not sr.passed and not sr.skipped)
    total_skipped = sum(1 for sr in sr_list if sr.skipped)

    status_icon = "✓" if pipeline_results.overall_passed else "✗"
    st.subheader(f"Pipeline 结果：{status_icon} {total_passed} 通过 / {total_failed} 失败 / {total_skipped} 跳过 / {len(sr_list)} 总计")

    if pipeline_results.stopped_at_step >= 0:
        st.warning(f"Pipeline 在 Step {pipeline_results.stopped_at_step + 1} 处中断")

    # 按步骤 Tab 展示
    step_tab_labels = []
    for sr in sr_list:
        passed_count = sum(1 for r in sr.test_results if r.passed) if sr.test_results else 0
        total_count = len(sr.test_results)
        icon = "⏭" if sr.skipped else ("✓" if sr.passed else "✗")
        step_tab_labels.append(f"{icon} {sr.step_name} ({passed_count}/{total_count})")

    result_tabs = st.tabs(step_tab_labels)

    for i, (sr, tab) in enumerate(zip(sr_list, result_tabs)):
        with tab:
            if sr.skipped:
                st.info(f"此步骤被跳过：{sr.error_message}")
                continue

            if sr.error_message:
                st.error(sr.error_message)

            # 提取的数据摘要
            if sr.extracted_data:
                with st.expander("提取的数据（传给下游）", expanded=False):
                    st.json(sr.extracted_data)

            # 测试用例结果
            for j, result in enumerate(sr.test_results):
                icon = "✓" if result.passed else "✗"
                with st.expander(f"{icon} {result.case_name} — status={result.actual_status_code} (期望 {result.expected_status_code})", expanded=not result.passed):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**请求 URL:** `{result.request_url}`")
                        st.markdown("**请求体:**")
                        st.json(result.request_body)
                    with c2:
                        st.markdown(f"**状态码:** {result.actual_status_code} (期望 {result.expected_status_code})")
                        if result.error_message:
                            st.error(f"**错误:** {result.error_message}")
                        st.markdown("**响应体:**")
                        if isinstance(result.response_body, dict):
                            st.json(result.response_body)
                        else:
                            st.text(str(result.response_body)[:2000])
