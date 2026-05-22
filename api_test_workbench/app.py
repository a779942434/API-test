"""API 测试工作台 — Streamlit 主界面"""

import json
import sys
import os
from pathlib import Path

import streamlit as st
import requests as req

# 确保项目根目录在 path 中，以便导入 engine 模块
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from api_test_workbench.engine.models import ApiConfig, TestCase, TestResult
from api_test_workbench.engine.runner import run_all_tests, get_auth_session

st.set_page_config(page_title="API 测试工作台", layout="wide")
st.title("API 测试工作台")

# ==================== 初始化 Session State ====================

defaults = {
    "api_url": "",
    "api_method": "POST",
    "api_headers": '{"Content-Type": "application/json"}',
    "api_body_template": "{}",
    "auth_url": "http://bird.ob.shuyilink.com/auth/auth-login",
    "auth_body": '{"username": "admin", "password": "sygl123456"}',
    "auth_session": None,
    "auth_ok": False,
    "field_requirements": "",
    "test_cases": [],
    "results": [],
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ==================== ① API 配置区 ====================

st.header("① API 配置")

col1, col2 = st.columns([4, 1])
with col1:
    st.text_input("接口地址", key="api_url", placeholder="http://bird.ob.shuyilink.com/linkim-pc/admin-console/tooling/sparePartDevice")
with col2:
    st.selectbox("Method", ["POST", "GET", "PUT", "DELETE"], key="api_method")

col_h, col_b = st.columns(2)
with col_h:
    st.text_area("Headers (JSON)", key="api_headers", height=100)
with col_b:
    st.text_area("Body 模板 (JSON)", key="api_body_template", height=100,
                 help="填写默认 body 模板，生成测试数据时会根据字段定义覆盖具体值")

# 登录区
with st.expander("登录认证", expanded=False):
    c1, c2, c3 = st.columns([3, 2, 1])
    with c1:
        st.text_input("登录接口地址", key="auth_url",
                      placeholder="http://bird.ob.shuyilink.com/auth/auth-login")
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

# ==================== ② 字段定义 & 生成区 ====================

st.header("② 字段定义 & 测试数据生成")

st.text_area(
    "粘贴接口字段定义（表结构/字段约束/业务规则）",
    key="field_requirements",
    height=200,
    placeholder="""字段列表：
- username: string, 3-50位, 必填, 唯一
- email: string, 邮箱格式, 必填
- age: int, 1-150, 选填, 默认18
- status: enum[active, inactive, pending], 必填

特殊规则：
- 用户名全局唯一
- 邮箱格式必须包含@和域名""",
)

gen_col1, gen_col2 = st.columns([1, 4])
with gen_col1:
    generate_clicked = st.button("生成测试数据", type="primary", use_container_width=True)

with gen_col2:
    if generate_clicked:
        if not st.session_state.field_requirements.strip():
            st.error("请先填写字段定义")
        else:
            with st.spinner("正在调用 Claude API 生成测试数据..."):
                try:
                    from api_test_workbench.engine.generator import generate_test_cases

                    test_cases = generate_test_cases(
                        field_requirements=st.session_state.field_requirements,
                        api_url=st.session_state.api_url,
                        method=st.session_state.api_method,
                    )
                    st.session_state.test_cases = test_cases
                    st.success(f"生成 {len(test_cases)} 条测试用例")
                except Exception as e:
                    st.error(f"生成失败: {e}")
                    st.session_state.test_cases = []

# 展示 & 编辑生成的测试用例
if st.session_state.test_cases:
    st.subheader(f"测试用例列表（{len(st.session_state.test_cases)} 条）")

    # 转换为表格可编辑格式
    tc_data = []
    for tc in st.session_state.test_cases:
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
        height=min(len(tc_data) * 38 + 40, 500),
        key="tc_editor",
    )

    # 将编辑后的数据同步回 test_cases
    for i, row in enumerate(edited_data):
        if i < len(st.session_state.test_cases):
            tc = st.session_state.test_cases[i]
            tc.case_id = row["case_id"]
            tc.case_name = row["case_name"]
            tc.category = row["category"]
            tc.operation = row["operation"]
            tc.expected_status_code = row["expected_status"]
            try:
                tc.input_data = json.loads(row["input_data"])
            except json.JSONDecodeError:
                pass
            tc.assertion_logic = row["assertion_logic"]

# ==================== ③ 执行 & 结果区 ====================

st.header("③ 执行 & 结果")

exec_col1, _ = st.columns([1, 4])
with exec_col1:
    run_clicked = st.button("执行全部", type="primary", use_container_width=True, disabled=len(st.session_state.test_cases) == 0)

if run_clicked:
    if not st.session_state.auth_ok:
        st.error("请先在「登录认证」区域获取 Session")
    else:
        # 构造 ApiConfig
        try:
            headers = json.loads(st.session_state.api_headers)
        except json.JSONDecodeError:
            headers = {"Content-Type": "application/json"}
            st.warning("Headers JSON 解析失败，使用默认值")

        api_config = ApiConfig(
            url=st.session_state.api_url,
            method=st.session_state.api_method,
            headers=headers,
        )

        progress_bar = st.progress(0, "准备执行...")
        status_text = st.empty()

        def update_progress(current, total, result):
            progress_bar.progress(current / total, f"执行中 {current}/{total}")
            icon = "✓" if result.passed else "✗"
            status_text.text(f"{icon} {result.case_name} — {result.actual_status_code}")

        with st.spinner("执行测试中..."):
            results = run_all_tests(
                test_cases=st.session_state.test_cases,
                api_config=api_config,
                session=st.session_state.auth_session,
                progress_callback=update_progress,
            )
            st.session_state.results = results

        progress_bar.empty()
        status_text.empty()

# 展示结果
if st.session_state.results:
    results = st.session_state.results
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    st.subheader(f"测试结果：{passed} 通过 / {failed} 失败 / {len(results)} 总计")

    for i, result in enumerate(results):
        icon = "✓" if result.passed else "✗"
        color = "green" if result.passed else "red"

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
