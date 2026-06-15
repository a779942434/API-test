"""API 测试工作台 — Streamlit 主界面 (Pipeline 模式)"""

import json
import sys
import warnings
from pathlib import Path

# macOS LibreSSL 与 urllib3 v2 兼容性警告，不影响功能
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+")

import streamlit as st

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
from api_test_workbench.engine.exporter import PytestExporter, _sanitize_filename
from api_test_workbench.engine.reporter import generate_html_report, generate_json_report
from api_test_workbench.engine.environment import (
    init_default_environments, list_environments,
    save_environment, delete_environment,
    resolve_env_variables,
)

st.set_page_config(page_title="API 测试工作台 — Pipeline", layout="wide")

# ═══════════════════════════════════════════
# 自定义样式 — 暗色开发者工具风
# ═══════════════════════════════════════════
st.markdown("""
<style>
/* ===== 全局 ===== */
.stApp { background: #0F172A; }
section.main .block-container { padding-top: 1.5rem; max-width: 1400px; }

/* ===== 标题 ===== */
h1 { font-weight: 700 !important; font-size: 1.75rem !important; color: #F1F5F9 !important;
     border-left: 4px solid #3B82F6; padding-left: 16px !important; margin-bottom: 1.5rem !important; }
h2 { font-weight: 600 !important; font-size: 1.25rem !important; color: #CBD5E1 !important; margin-top: 0.5rem !important; }
h3 { font-weight: 600 !important; font-size: 1.05rem !important; color: #94A3B8 !important; }

/* ===== 卡片容器 ===== */
.stExpander {
    background: #1E293B !important;
    border: 1px solid #334155 !important;
    border-radius: 10px !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3) !important;
    margin-bottom: 12px !important;
    transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
}
.stExpander:hover {
    border-color: #3B82F6 !important;
    box-shadow: 0 4px 16px rgba(59,130,246,0.12) !important;
}
.stExpander details[open] { border-left: 3px solid #3B82F6 !important; }

/* ===== 按钮 ===== */
.stButton > button {
    border-radius: 8px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    transition: all 0.15s ease !important;
    border: none !important;
}
.stButton > button[kind="primary"] {
    background: #3B82F6 !important; color: #fff !important;
    box-shadow: 0 2px 6px rgba(59,130,246,0.3) !important;
}
.stButton > button[kind="primary"]:hover {
    background: #2563EB !important;
    box-shadow: 0 4px 12px rgba(59,130,246,0.45) !important;
    transform: translateY(-1px);
}
.stButton > button[kind="secondary"] {
    background: transparent !important; color: #93C5FD !important;
    border: 1.5px solid #3B82F6 !important;
}
.stButton > button[kind="secondary"]:hover {
    background: rgba(59,130,246,0.12) !important; border-color: #60A5FA !important; color: #BFDBFE !important;
}

/* ===== 输入框 / 文本域 ===== */
.stTextInput input, .stTextArea textarea {
    border-radius: 8px !important;
    border: 1.5px solid #334155 !important;
    background: #0F172A !important;
    color: #E2E8F0 !important;
    transition: border-color 0.15s ease, box-shadow 0.15s ease !important;
    font-size: 0.875rem !important;
}
.stTextInput input::placeholder, .stTextArea textarea::placeholder { color: #64748B !important; }
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: #3B82F6 !important;
    box-shadow: 0 0 0 3px rgba(59,130,246,0.15) !important;
}

/* ===== Selectbox ===== */
.stSelectbox [data-baseweb="select"] > div {
    background: #0F172A !important;
    border-color: #334155 !important;
    border-radius: 8px !important;
}

/* ===== Tabs ===== */
.stTabs [data-baseweb="tab-list"] {
    gap: 0px !important;
    border-bottom: 2px solid #334155 !important;
    background: transparent !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px 8px 0 0 !important;
    padding: 8px 20px !important;
    font-weight: 500 !important;
    color: #64748B !important;
    background: transparent !important;
    border: none !important;
    margin-right: 2px !important;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    color: #3B82F6 !important;
    border-bottom: 2px solid #3B82F6 !important;
    background: rgba(59,130,246,0.08) !important;
}

/* ===== Progress 进度条 ===== */
.stProgress > div > div > div {
    background: linear-gradient(90deg, #2563EB, #3B82F6) !important;
    border-radius: 4px !important;
}
.stProgress > div { background: #334155 !important; border-radius: 4px !important; }

/* ===== Alert / 消息 ===== */
.stAlert { border-radius: 8px !important; font-size: 0.875rem !important; }
div[data-testid="stNotification"] { border-radius: 8px !important; }

/* ===== Data Editor / DataFrame ===== */
.stDataFrame, .stDataEditor {
    border-radius: 8px !important;
    border: 1px solid #334155 !important;
    overflow: hidden !important;
}
.stDataFrame thead th, .stDataEditor thead th {
    background: #1E293B !important;
    font-weight: 600 !important;
    font-size: 0.8rem !important;
    color: #94A3B8 !important;
}

/* ===== 分隔线 ===== */
hr { border-color: #334155 !important; margin: 1.5rem 0 !important; }

/* ===== Caption ===== */
.stCaption { color: #64748B !important; font-size: 0.8rem !important; }

/* ===== JSON 展示 ===== */
.stJson { background: #0F172A !important; border-radius: 8px !important; padding: 8px !important; border: 1px solid #334155 !important; }

/* ===== Expander 展开/收起文字 ===== */
.stExpander details summary { color: #CBD5E1 !important; }

/* ===== 代码块 ===== */
code { background: #1E293B !important; color: #93C5FD !important; padding: 2px 6px !important; border-radius: 4px !important; }

/* ===== 数字输入 ===== */
.stNumberInput input {
    background: #0F172A !important; border-color: #334155 !important;
    color: #E2E8F0 !important; border-radius: 8px !important;
}

/* ===== Sidebar 等容器 ===== */
.st-emotion-cache-1cypcdb { background: #0F172A !important; }
</style>
""", unsafe_allow_html=True)

# ── 标题 + 存档按钮 ──
col_title, col_save, col_load = st.columns([3, 0.7, 0.7])
with col_title:
    st.title("API 测试工作台 — Pipeline")
with col_save:
    st.write("")
    if st.button("💾 保存", width='stretch', help="保存当前全部状态到本地"):
        from api_test_workbench.engine.session_store import save as session_save, SAVE_DIR
        try:
            p = st.session_state.pipeline
            # 汇总当前步骤信息供确认
            step_info = "、".join(f"Step{i+1}:{s.config.method} {s.config.url[:40] if s.config.url else '(空)'}"
                                 for i, s in enumerate(p.steps))
            path = session_save(
                p,
                st.session_state.get("field_requirements", ""),
                st.session_state.get("pipeline_test_cases_by_step", {}),
                st.session_state.get("auth_url", ""),
                st.session_state.get("auth_username", ""),
                st.session_state.get("auth_password", ""),
                st.session_state.get("auth_tenant_id", ""),
                name=p.name,
            )
            st.success(f"已保存: {len(p.steps)}步, {step_info}")
        except Exception as e:
            st.error(f"保存失败: {e}")
with col_load:
    st.write("")
    if st.button("📂 加载", width='stretch', help="从本地存档恢复"):
        st.session_state["_show_load_ui"] = True

if st.session_state.get("_show_load_ui"):
    from api_test_workbench.engine.session_store import list_saves as list_session_saves, load as session_load
    saves = list_session_saves()
    if saves:
        save_names = [f"{s['saved_at']} — {s['pipeline_name']}" for s in saves]
        col1, col2, col3 = st.columns([2, 1, 0.5])
        with col1:
            selected_idx = st.selectbox("选择存档", range(len(save_names)),
                format_func=lambda i: save_names[i], key="load_select_idx")
        with col2:
            st.write("")
            if st.button("确认加载", key="load_confirm_btn"):
                data = session_load(saves[selected_idx]["path"])
                if data:
                    p = data["pipeline"]
                    step_info = "、".join(f"Step{i+1}:{s.config.method}" for i, s in enumerate(p.steps))
                    st.session_state.pipeline = p
                    st.session_state.field_requirements = data["field_requirements"]
                    st.session_state.pipeline_test_cases_by_step = data["pipeline_test_cases_by_step"]
                    st.session_state.auth_url = data["auth_url"]
                    st.session_state.auth_username = data.get("auth_username", "")
                    st.session_state.auth_password = data.get("auth_password", "")
                    st.session_state.auth_tenant_id = data.get("auth_tenant_id", "")
                    st.session_state.pipeline_results = None
                    # 递增所有步骤 widget 版本号 → 强制用新 key 从加载数据重新初始化
                    for k in list(st.session_state.keys()):
                        if k.startswith("step_widget_ver_"):
                            st.session_state[k] += 1
                    # 同时清除旧 widget 数据键
                    for k in list(st.session_state.keys()):
                        if any(k.startswith(p) for p in (
                            "step_url_", "step_method_", "step_headers_",
                            "step_body_", "step_name_", "step_curl_", "step_ignored_",
                        )):
                            del st.session_state[k]
                    st.session_state["_show_load_ui"] = False
                    st.success(f"已恢复: {len(p.steps)}步 [{step_info}], {sum(len(v) for v in data['pipeline_test_cases_by_step'].values())} 条用例")
                    st.rerun()
                else:
                    st.error("加载失败")
        with col3:
            st.write("")
            if st.button("✕", key="load_close_btn", help="关闭加载面板"):
                st.session_state["_show_load_ui"] = False
                st.rerun()
    else:
        st.info("暂无存档")
        st.session_state["_show_load_ui"] = False

# ==================== 初始化 Session State ====================

def _create_default_step() -> ApiStep:
    return ApiStep(
        name="",
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
        "pipeline_test_cases_by_step": {},
        "pipeline_results": None,
        "auth_url": "http://bird.ob.shuyilink.com/auth/auth-login",
        "auth_username": "",
        "auth_password": "",
        "auth_tenant_id": "",
        "auth_session": None,
        "auth_ok": False,
        "field_requirements": "",
        "active_env": "",  # 当前激活的环境名，"" 表示不使用环境
        "env_variables": {},  # 当前激活环境的变量映射
        "show_env_editor": False,  # 是否打开环境编辑器
        "_default_auth_url": "http://bird.ob.shuyilink.com/auth/auth-login",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            if k == "field_requirements":
                st.session_state[k] = st.session_state.get("_field_requirements_backup", v)
            else:
                st.session_state[k] = v

    # 向后兼容：从旧版 session state 迁移（仅执行一次）
    if "api_url" in st.session_state and st.session_state.api_url and not st.session_state.get("_backward_compat_done"):
        if not st.session_state.pipeline.steps[0].config.url:
            st.session_state.pipeline.steps[0].config.url = st.session_state.api_url
            st.session_state.pipeline.steps[0].config.method = st.session_state.get("api_method", "POST")
        st.session_state["_backward_compat_done"] = True

# ==================== 辅助函数 ====================

def _render_pipeline_flow(steps: list) -> str:
    """渲染 Pipeline 可视化流程条"""
    if not steps:
        return ""
    boxes = []
    colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336", "#00BCD4", "#795548", "#607D8B"]
    for i, step in enumerate(steps):
        color = colors[i % len(colors)]
        if step.ignored:
            boxes.append(
                f'<div style="display:inline-flex;align-items:center;padding:10px 18px;margin:4px 0;'
                f'border:2px dashed #666;border-radius:10px;text-align:center;'
                f'background:#2a2a2a;min-width:100px;opacity:0.5">'
                f'<div><div style="font-size:12px;color:#888;font-weight:bold;">Step {i+1} 🚫</div>'
                f'<div style="font-size:13px;color:#888;margin-top:2px;"><s>{step.name}</s></div>'
                f'<div style="font-size:11px;color:#888;margin-top:1px;">{step.config.method}</div></div>'
                f'</div>'
            )
        else:
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


def _backup_fr():
    """备份字段定义内容，防止增删步骤等 rerun 操作丢失用户填写的数据（空值也备份）"""
    st.session_state["_field_requirements_backup"] = st.session_state.get("field_requirements", "")


def _normalize_step_urls():
    """根据登录地址强制统一所有步骤的 base URL、Headers、Body 中的 host"""
    from urllib.parse import urlparse
    auth_url = st.session_state.get("auth_url", "")
    if not auth_url:
        return
    auth_parsed = urlparse(auth_url)
    auth_host = f"{auth_parsed.scheme}://{auth_parsed.netloc}"
    for s in st.session_state.pipeline.steps:
        if not s.config.url:
            continue
        parsed = urlparse(s.config.url)
        old_host = f"{parsed.scheme}://{parsed.netloc}"
        if old_host == auth_host:
            continue
        s.config.url = f"{auth_host}{parsed.path}{'?' + parsed.query if parsed.query else ''}"
        if s.config.headers:
            for hk, hv in s.config.headers.items():
                if isinstance(hv, str) and old_host in hv:
                    s.config.headers[hk] = hv.replace(old_host, auth_host)
        body_str = json.dumps(s.config.body_template, ensure_ascii=False)
        if old_host in body_str:
            s.config.body_template = json.loads(body_str.replace(old_host, auth_host))


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


# ==================== 环境管理（侧边栏） ====================

_init_session_state()

# 首次启动时创建默认环境
init_default_environments()

with st.sidebar:
    st.markdown("### 🌍 环境管理")

    envs = list_environments()
    env_names = [e["name"] for e in envs]

    # 环境选择器
    options = ["(不使用环境)"] + env_names
    current_idx = 0
    if st.session_state.active_env and st.session_state.active_env in env_names:
        current_idx = env_names.index(st.session_state.active_env) + 1

    selected = st.selectbox(
        "选择运行环境",
        range(len(options)),
        format_func=lambda i: ("✅ " if i > 0 else "") + options[i],
        key="env_selector",
        index=current_idx,
        help="切换环境自动替换 URL 中的 {{VAR}} 占位符",
    )

    # 应用环境选择
    if selected > 0:
        env_name = env_names[selected - 1]
        if st.session_state.active_env != env_name:
            # 保存当前手动设置的 Auth 配置（用于取消环境时恢复）
            if not st.session_state.active_env:
                st.session_state._default_auth_url = st.session_state.auth_url
                st.session_state._default_auth_username = st.session_state.auth_username
                st.session_state._default_auth_password = st.session_state.auth_password
            st.session_state.active_env = env_name
            env_data = next((e for e in envs if e["name"] == env_name), None)
            if env_data:
                env_vars = dict(env_data.get("variables", {}))
                if env_data.get("base_url") and "BASE" not in env_vars:
                    env_vars["BASE"] = env_data["base_url"]
                st.session_state.env_variables = env_vars
                if env_data.get("auth_endpoint"):
                    st.session_state.auth_url = env_data["auth_endpoint"]
                if env_data.get("auth_body"):
                    try:
                        ab = json.loads(env_data["auth_body"]) if isinstance(env_data["auth_body"], str) else env_data["auth_body"]
                        st.session_state.auth_username = ab.get("username", "")
                        st.session_state.auth_password = ab.get("password", "")
                    except (json.JSONDecodeError, TypeError):
                        pass
                st.session_state.auth_ok = False
                st.session_state.auth_session = None
    else:
        if st.session_state.active_env:
            st.session_state.auth_url = st.session_state._default_auth_url
            st.session_state.auth_username = st.session_state._default_auth_username
            st.session_state.auth_password = st.session_state._default_auth_password
            st.session_state.auth_ok = False
            st.session_state.auth_session = None
        st.session_state.active_env = ""
        st.session_state.env_variables = {}

    # 当前环境状态
    if st.session_state.active_env:
        st.caption(f"当前: **{st.session_state.active_env}** · {len(st.session_state.env_variables)} 个变量")
    else:
        st.caption("未启用环境（URL 原样使用）")

    st.divider()

    # 环境编辑器
    if st.button("管理环境" if not st.session_state.show_env_editor else "收起编辑器", use_container_width=True):
        st.session_state.show_env_editor = not st.session_state.show_env_editor

    if st.session_state.show_env_editor:
        env_list = list_environments()
        env_names_for_edit = [e["name"] for e in env_list]

        # 选择要编辑的环境
        edit_target = st.selectbox(
            "编辑环境",
            ["[新建环境]"] + env_names_for_edit,
            key="env_edit_target",
        )

        if edit_target == "[新建环境]":
            env_name_input = st.text_input("环境名称", key="env_new_name", placeholder="如：dev, staging, prod")
            env_base_url = st.text_input("Base URL", key="env_new_base", placeholder="https://api.example.com")
            env_vars_raw = st.text_area(
                "变量 (每行一个: VAR_NAME=value)",
                key="env_new_vars",
                height=100,
                placeholder="BASE=https://api.example.com\nTOKEN=xxx",
            )
            env_auth_url = st.text_input("Auth URL (可选)", key="env_new_auth_url", placeholder="留空使用主登录表单")
            env_auth_body = st.text_area("Auth Body JSON (可选)", key="env_new_auth_body", height=60, placeholder='{"username":"","password":""}')

            col_save, _ = st.columns([1, 2])
            with col_save:
                if st.button("💾 保存环境", use_container_width=True, type="primary"):
                    if not env_name_input.strip():
                        st.error("请输入环境名称")
                    else:
                        # 解析变量
                        variables = {}
                        for line in env_vars_raw.strip().split("\n"):
                            line = line.strip()
                            if "=" in line:
                                k, _, v = line.partition("=")
                                variables[k.strip()] = v.strip()
                        try:
                            auth_body = json.loads(env_auth_body) if env_auth_body.strip() else {}
                        except json.JSONDecodeError:
                            auth_body = {}
                        save_environment(env_name_input.strip(), env_base_url.strip(), variables,
                                         env_auth_url.strip(), auth_body)
                        st.success(f"环境 '{env_name_input}' 已保存")
                        st.rerun()
        else:
            # 加载已有环境进行编辑
            env_data = next((e for e in env_list if e["name"] == edit_target), None)
            if env_data:
                edited_name = st.text_input("环境名称", value=env_data["name"], key="env_edit_name")
                edited_base = st.text_input("Base URL", value=env_data["base_url"], key="env_edit_base")
                vars_text = "\n".join(f"{k}={v}" for k, v in env_data.get("variables", {}).items())
                edited_vars = st.text_area("变量", value=vars_text, key="env_edit_vars", height=100)
                edited_auth_url = st.text_input("Auth URL (可选)", value=env_data.get("auth_endpoint", ""), key="env_edit_auth_url_val")
                auth_body_val = json.dumps(env_data.get("auth_body", {}), ensure_ascii=False, indent=2)
                edited_auth_body = st.text_area("Auth Body JSON (可选)", value=auth_body_val, key="env_edit_auth_body", height=60)

                col_save, col_delete = st.columns(2)
                with col_save:
                    if st.button("💾 更新环境", use_container_width=True, type="primary"):
                        variables = {}
                        for line in edited_vars.strip().split("\n"):
                            line = line.strip()
                            if "=" in line:
                                k, _, v = line.partition("=")
                                variables[k.strip()] = v.strip()
                        try:
                            auth_body = json.loads(edited_auth_body) if edited_auth_body.strip() else {}
                        except json.JSONDecodeError:
                            auth_body = {}
                        save_environment(edited_name.strip(), edited_base.strip(), variables,
                                         edited_auth_url.strip(), auth_body)
                        if st.session_state.active_env == edit_target:
                            st.session_state.active_env = edited_name.strip()
                            st.session_state.env_variables = variables
                            if edited_base.strip() and "BASE" not in variables:
                                st.session_state.env_variables["BASE"] = edited_base.strip()
                        st.success("已更新")
                        st.rerun()
                with col_delete:
                    if st.button("🗑 删除", use_container_width=True):
                        delete_environment(edit_target)
                        if st.session_state.active_env == edit_target:
                            st.session_state.active_env = ""
                            st.session_state.env_variables = {}
                        st.success(f"已删除环境 '{edit_target}'")
                        st.rerun()

    # 变量预览
    if st.session_state.active_env and st.session_state.env_variables:
        with st.expander(f"变量预览 ({len(st.session_state.env_variables)})"):
            for k, v in st.session_state.env_variables.items():
                st.caption(f"`{{{{{k}}}}}` → `{v}`")



tab1, tab2 = st.tabs(["🧪 测试流程", "🔧 造数据模式"])

with tab1:

    # ==================== ① Pipeline 配置 ====================

    st.header("🔗 ① Pipeline 配置")

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

        with st.expander(f"{'🚫 ' if step.ignored else ''}Step {i+1}：{step.name or '(未命名)'}  — {step.config.method} {step.config.url or '(未设置URL)'}", expanded=(i == 0)):
            col_name, col_method, col_failure, col_ignore = st.columns([2.5, 1, 1.2, 0.8])
            with col_name:
                step.name = st.text_input(f"步骤名称", value=step.name, key=f"step_name_{i}_v{ver}", placeholder="如：创建订单")
            with col_method:
                step.config.method = st.selectbox("Method", ["POST", "GET", "PUT", "DELETE"], key=f"step_method_{i}_v{ver}", index=["POST", "GET", "PUT", "DELETE"].index(step.config.method) if step.config.method in ["POST", "GET", "PUT", "DELETE"] else 0)
            with col_failure:
                step.on_failure = st.selectbox("失败策略", ["stop", "continue"], key=f"step_failure_{i}_v{ver}", index=0 if step.on_failure == "stop" else 1, help="stop=停止后续步骤, continue=忽略错误继续执行")
            with col_ignore:
                st.write("")
                step.ignored = st.checkbox("忽略", value=step.ignored, key=f"step_ignored_{i}_v{ver}", help="跳过此步骤，数据仍向下传递")

            step.config.url = st.text_input("接口地址", value=step.config.url, key=f"step_url_{i}_v{ver}", placeholder="http://bird.ob.shuyilink.com/linkim-pc/admin-console/tooling/sparePartDevice")
            # ── curl 命令粘贴解析 ──
            curl_col1, curl_col2 = st.columns([4, 1])
            with curl_col1:
                curl_input = st.text_area(
                    "粘贴 curl（可选，点「解析」自动填充）",
                    key=f"step_curl_{i}",
                    height=80,
                    placeholder='''curl -X POST 'http://example.com/api' -H 'Content-Type: application/json' -d '{"page":1,"size":30}' ''',
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
                            # 剔除 Cookie 头：认证由底部登录 Session 统一管理，curl 中的旧 Cookie 会覆盖登录态导致 401
                            for h in list(merged.keys()):
                                if h.lower() in ("cookie", "set-cookie"):
                                    del merged[h]
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
                body_str = st.text_area("Body 模板 (JSON)", value=json.dumps(step.config.body_template, ensure_ascii=False, indent=2), height=80, key=f"step_body_{i}_v{ver}", help="步骤间数据依赖在下方「字段定义」中描述")
                try:
                    step.config.body_template = json.loads(body_str)
                except json.JSONDecodeError:
                    st.warning("Body JSON 格式错误，修改未保存")

            # 步骤间数据依赖提示
            st.caption('步骤间数据传递：在下方「② 字段定义」中用自然语言描述，如「Step2 使用 Step1 返回的 data.id」，AI 会自动生成数据链路。')

            # 操作按钮
            btn_col1, btn_col2, btn_col3, _ = st.columns([1, 1, 1, 7])
            with btn_col1:
                if i > 0 and st.button("⬆ 上移", key=f"move_up_{i}"):
                    steps[i], steps[i-1] = steps[i-1], steps[i]
                    tcs = st.session_state.pipeline_test_cases_by_step
                    tcs[i], tcs[i-1] = tcs.get(i-1, []), tcs.get(i, [])
                    # 递增双方版本号 → Widget 全部用新 key 从 step.config 重新初始化
                    st.session_state[f"step_widget_ver_{i}"] = st.session_state.get(f"step_widget_ver_{i}", 0) + 1
                    st.session_state[f"step_widget_ver_{i-1}"] = st.session_state.get(f"step_widget_ver_{i-1}", 0) + 1
                    _backup_fr()
                    st.rerun()
            with btn_col2:
                if i < len(steps) - 1 and st.button("⬇ 下移", key=f"move_down_{i}"):
                    steps[i], steps[i+1] = steps[i+1], steps[i]
                    tcs = st.session_state.pipeline_test_cases_by_step
                    tcs[i], tcs[i+1] = tcs.get(i+1, []), tcs.get(i, [])
                    st.session_state[f"step_widget_ver_{i}"] = st.session_state.get(f"step_widget_ver_{i}", 0) + 1
                    st.session_state[f"step_widget_ver_{i+1}"] = st.session_state.get(f"step_widget_ver_{i+1}", 0) + 1
                    _backup_fr()
                    st.rerun()
            with btn_col3:
                if len(steps) > 1 and st.button("🗑 删除", key=f"delete_{i}"):
                    steps_to_delete.add(i)

    # 执行删除（保留剩余步骤的测试数据和认证状态）
    if steps_to_delete:
        old_steps = steps  # 当前步骤列表
        old_tcs = st.session_state.pipeline_test_cases_by_step
        new_steps = []
        new_tcs = {}
        new_vers = {}
        new_idx = 0
        for old_idx, s in enumerate(old_steps):
            if old_idx in steps_to_delete:
                continue
            new_steps.append(s)
            if old_idx in old_tcs:
                new_tcs[new_idx] = old_tcs[old_idx]
            new_vers[new_idx] = st.session_state.get(f"step_widget_ver_{old_idx}", 0)
            new_idx += 1
        st.session_state.pipeline.steps = new_steps
        st.session_state.pipeline_test_cases_by_step = new_tcs
        # 重排 widget 版本号，对齐新索引
        for ni in new_vers:
            st.session_state[f"step_widget_ver_{ni}"] = new_vers[ni]
        # 清理多余版本号
        for oi in range(len(old_steps)):
            if f"step_widget_ver_{oi}" in st.session_state and oi not in new_vers:
                del st.session_state[f"step_widget_ver_{oi}"]
        _backup_fr()
        st.rerun()

    # 添加步骤按钮
    if st.button("+ 添加步骤", use_container_width=True, type="secondary"):
        st.session_state.pipeline.steps.append(ApiStep(
            name="",
            config=ApiConfig(method="GET", headers={"Content-Type": "application/json"}, body_template={}),
        ))
        _backup_fr()
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
        st.info("尚未定义步骤间的数据依赖。在下方「字段定义」中用自然语言描述后点击「生成测试数据」，AI 会自动建立数据链路。")


    # ==================== ② 字段定义 & 生成 ====================

    st.header("📝 ② 字段定义 & 测试数据生成")

    st.text_area(
        "粘贴 Pipeline 字段定义（每步接口的字段约束、业务规则、数据依赖）",
        key="field_requirements",
        height=160,
        placeholder='''描述每个步骤的字段约束和步骤间的数据依赖（用自然语言即可）：

    Step 1:
    - name: string, 必填, 1-100位
    - quantity: int, 必填, 1-9999

    Step 2:
    - 用 Step1 返回的 data.records[0].id 作为查询参数

    数据依赖写法（AI 自动识别）：
    - "取 Step1 返回的 data.id"
    - "用 Step1 响应中 data.records[0].id 填入 URL"

    范围控制（在第一行声明，默认不写=完整测试覆盖）：
    - "只需正常数据，不需要边界测试" -> 只生成正向真实数据
    - "完整测试覆盖" -> 包含边界值/异常/等价类'''
    )

    # 每次渲染后备份字段定义内容（含空值），防止增删步骤时丢失
    _backup_fr()

    gen_col1, gen_col2, _ = st.columns([1.5, 1, 3])
    with gen_col1:
        test_cases_per_step = st.number_input(
            "每步用例数", min_value=1, max_value=25, value=1, step=1, key="tc_per_step",
            help="1=核心正向链路 | 3-5=含边界值 | 10+=完整覆盖（等价类/边界/异常/依赖）",
        )
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

    st.header("🔐 ③ 认证 & 执行")

    # 登录区
    with st.expander("登录认证", expanded=not st.session_state.auth_ok):
        # 环境 Auth 提示
        if st.session_state.active_env:
            env_data = next((e for e in list_environments() if e["name"] == st.session_state.active_env), None)
            if env_data and (env_data.get("auth_endpoint") or env_data.get("auth_body")):
                st.caption(f"🌍 使用环境 **{st.session_state.active_env}** 的认证配置")
        c1, c2, c3 = st.columns([3, 2, 1])
        with c1:
            st.text_input("登录接口地址", key="auth_url", placeholder="http://bird.ob.shuyilink.com/auth/auth-login")
        with c2:
            st.text_input("用户名", key="auth_username", placeholder="必填")
            st.text_input("密码", key="auth_password", type="password", placeholder="必填")
            st.text_input("tenantId（可选）", key="auth_tenant_id", placeholder="留空则不传")
        with c3:
            st.write("")
            st.write("")
            if st.button("获取 Session", use_container_width=True):
                try:
                    username = st.session_state.get("auth_username", "").strip()
                    password = st.session_state.get("auth_password", "").strip()
                    if not username or not password:
                        st.error("用户名和密码为必填项")
                    else:
                        auth_body = {"username": username, "password": password}
                        tenant_id = st.session_state.get("auth_tenant_id", "").strip()
                        session = get_auth_session(st.session_state.auth_url, auth_body, tenant_id=tenant_id)
                        st.session_state.auth_session = session
                        st.session_state.auth_ok = True
                        _normalize_step_urls()
                        st.success("登录成功（已自动统一所有步骤的 Base URL）")
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

    exec_col1, exec_col2, _ = st.columns([1, 1, 3])
    with exec_col1:
        run_clicked = st.button("执行 Pipeline", type="primary", use_container_width=True, disabled=exec_disabled)
    with exec_col2:
        # 导出 pytest 按钮
        if has_cases:
            # 使用 expander 避免每次生成 ZIP
            if st.button("📦 导出 pytest", key="export_test_tab", use_container_width=True, disabled=exec_disabled):
                try:
                    exporter = PytestExporter(
                        pipeline=st.session_state.pipeline,
                        test_cases_by_step=st.session_state.pipeline_test_cases_by_step,
                        auth_url=st.session_state.get("auth_url", ""),
                        auth_body={"username": st.session_state.get("auth_username", ""),
                                   "password": st.session_state.get("auth_password", "")},
                    )
                    zip_bytes = exporter.export_to_zip_bytes()
                    st.session_state["_export_zip"] = zip_bytes
                    st.session_state["_export_name"] = f"pytest_{_sanitize_filename(st.session_state.pipeline.name)}.zip"
                except Exception as e:
                    st.error(f"导出失败: {e}")

            # 如果已生成 ZIP，显示下载按钮
            if st.session_state.get("_export_zip"):
                st.download_button(
                    label="⬇️ 下载 ZIP",
                    data=st.session_state["_export_zip"],
                    file_name=st.session_state.get("_export_name", "pytest_export.zip"),
                    mime="application/zip",
                    use_container_width=True,
                )
        else:
            st.button("📦 导出 pytest", key="export_test_tab_noop", use_container_width=True, disabled=True, help="请先生成测试用例")

    if run_clicked:
        if not st.session_state.auth_ok:
            st.error("请先在「登录认证」区域获取 Session")
        else:
            _normalize_step_urls()
            pipeline = st.session_state.pipeline
            tcs_by_step = st.session_state.pipeline_test_cases_by_step

            progress_bar = st.progress(0, "准备执行...")
            status_text = st.empty()

            # 计算总步数（每条用例 × 每步）
            total_cases = max((len(v) for v in tcs_by_step.values()), default=0)
            total_steps = len(pipeline.steps)
            total_ops = total_cases * total_steps
            _counter = [0]  # mutable counter for closure

            def update_progress(current, total, result):
                _counter[0] += 1
                progress_bar.progress(_counter[0] / total_ops, f"链路 {_counter[0]}/{total_ops}")
                if result.test_results:
                    last = result.test_results[-1]
                    icon = "✓" if last.passed else "✗"
                    status_text.text(f"{icon} Step {current+1}: {result.step_name} — {last.case_name}")

            with st.spinner("执行 Pipeline 中..."):
                results = execute_pipeline(
                    pipeline=pipeline,
                    session=st.session_state.auth_session,
                    test_cases_by_step=tcs_by_step,
                    progress_callback=update_progress,
                    env_variables=st.session_state.get("env_variables") or None,
                )
                st.session_state.pipeline_results = results

            progress_bar.empty()
            status_text.empty()


    def _render_response_time(ms: float):
        """渲染带颜色编码的响应时间"""
        if ms <= 0:
            return
        if ms < 500:
            color = "#22C55E"   # green
        elif ms < 2000:
            color = "#F59E0B"   # amber
        else:
            color = "#EF4444"   # red
        st.markdown(
            f"**耗时:** <span style='color:{color};font-weight:600;'>{ms:.0f} ms</span>",
            unsafe_allow_html=True,
        )


    def _render_step_result(sr):
        """渲染单个步骤的执行结果"""
        if sr.skipped:
            st.info(f"此步骤被跳过：{sr.error_message}")
            return
        if sr.error_message:
            st.error(sr.error_message)
        if sr.extracted_data:
            with st.expander("提取的数据（传给下游）", expanded=False):
                st.json(sr.extracted_data)
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
                    _render_response_time(result.response_time_ms)
                    if result.error_message:
                        st.error(f"**错误:** {result.error_message}")
                    st.markdown("**响应体:**")
                    if isinstance(result.response_body, dict):
                        st.json(result.response_body)
                    else:
                        st.text(str(result.response_body)[:2000])


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

        view_mode = st.radio("查看方式", ["📋 按步骤", "🔗 按链路"], horizontal=True)

        if view_mode == "📋 按步骤":
            step_tab_labels = []
            for sr in sr_list:
                passed_count = sum(1 for r in sr.test_results if r.passed) if sr.test_results else 0
                total_count = len(sr.test_results)
                icon = "⏭" if sr.skipped else ("✓" if sr.passed else "✗")
                step_tab_labels.append(f"{icon} {sr.step_name} ({passed_count}/{total_count})")
            result_tabs = st.tabs(step_tab_labels)
            for i, (sr, tab) in enumerate(zip(sr_list, result_tabs)):
                with tab:
                    _render_step_result(sr)
        else:
            max_cases = max((len(sr.test_results) for sr in sr_list), default=0)
            chain_tabs = st.tabs([f"链路 {j+1}" for j in range(max_cases)])
            for j, tab in enumerate(chain_tabs):
                with tab:
                    chain_passed = sum(1 for sr in sr_list if j < len(sr.test_results) and sr.test_results[j].passed)
                    chain_total = sum(1 for sr in sr_list if j < len(sr.test_results))
                    chain_icon = "✓" if chain_passed == chain_total else "✗"
                    st.caption(f"{chain_icon} 链路 {j+1}: {chain_passed}/{chain_total} 通过")
                    for i, sr in enumerate(sr_list):
                        if j < len(sr.test_results):
                            r = sr.test_results[j]
                            icon = "✓" if r.passed else "✗"
                            with st.expander(f"{icon} Step{i+1} {sr.step_name} — {r.case_name}", expanded=not r.passed):
                                c1, c2 = st.columns(2)
                                with c1:
                                    st.markdown(f"**请求 URL:** `{r.request_url}`")
                                    st.markdown("**请求体:**")
                                    st.json(r.request_body)
                                with c2:
                                    st.markdown(f"**状态码:** {r.actual_status_code} (期望 {r.expected_status_code})")
                                    _render_response_time(r.response_time_ms)
                                    if r.error_message:
                                        st.error(f"**错误:** {r.error_message}")
                                    st.markdown("**响应体:**")
                                    if isinstance(r.response_body, dict):
                                        st.json(r.response_body)
                                    else:
                                        st.text(str(r.response_body)[:2000])

        # 报告导出按钮
        st.divider()
        st.subheader("📊 报告导出")
        col_html, col_json, _ = st.columns([1, 1, 3])
        with col_html:
            html_report = generate_html_report(pipeline_results, st.session_state.pipeline)
            st.download_button(
                "📥 下载 HTML 报告",
                data=html_report,
                file_name=f"api_test_report_{pipeline_results.pipeline_name}.html",
                mime="text/html",
                use_container_width=True,
                type="primary",
            )
        with col_json:
            json_report = json.dumps(
                generate_json_report(pipeline_results),
                ensure_ascii=False, indent=2,
            )
            st.download_button(
                "📥 下载 JSON 报告",
                data=json_report,
                file_name=f"api_test_report_{pipeline_results.pipeline_name}.json",
                mime="application/json",
                use_container_width=True,
            )




with tab2:
    st.header("🔧 造数据模式")
    st.caption("批量生成大量正向真实数据，自动跳过查询/搜索类步骤，仅对写操作（新增/编辑/删除）生成数据")

    from api_test_workbench.engine.utils import is_write_step

    if not st.session_state.pipeline or not st.session_state.pipeline.steps:
        st.warning("请先在「测试流程」Tab 中配置 Pipeline 步骤（① Pipeline 配置）")
    else:
        write_idx = [i for i, s in enumerate(st.session_state.pipeline.steps) if is_write_step(s)]
        query_idx = [i for i, s in enumerate(st.session_state.pipeline.steps) if not is_write_step(s)]

        with st.expander(f"📋 当前 Pipeline：{st.session_state.pipeline.name}", expanded=True):
            for i, step in enumerate(st.session_state.pipeline.steps):
                tag = "✏️ 写" if i in write_idx else "🔍 查"
                st.text(f"  {tag}  Step{i+1}: {step.config.method} {step.config.url[:70]}")
            if not write_idx:
                st.warning("当前 Pipeline 没有写操作步骤，无需造数据")
            else:
                st.info(f"将只为 {len(write_idx)} 个写操作步骤生成数据（{len(query_idx)} 个查询步骤自动跳过）")

        data_desc = st.text_area(
            "描述要生成的数据（数量、字段取值规则、随机范围）",
            key="data_gen_desc",
            height=140,
            placeholder="""生成 50 条刀具数据：
- articleName: 随机中文名称，6-12字，如"高精度铣刀"、"合金钻头"
- articleNumber: TOOL-{序号}，从001递增到050
- toolStandard: 从 [ISO标准, DIN标准, JIS标准] 随机选取
- toolType: 从 [铣刀, 钻头, 车刀, 镗刀] 随机选取
- enableInd: 固定为1
- isStandard: 固定为1""",
        )

        col1, col2, col3 = st.columns([1.2, 1, 3])
        with col1:
            data_count = st.number_input("每步生成数量", min_value=1, max_value=500, value=50, step=10, key="data_gen_count")
        with col2:
            st.write("")
            gen_data_clicked = st.button("🤖 生成数据", type="primary", use_container_width=True, disabled=not write_idx)

        if gen_data_clicked:
            if not data_desc.strip():
                st.error("请先描述数据需求")
            else:
                with st.spinner(f"AI 正在为 {len(write_idx)} 个写操作步骤各生成 {data_count} 条数据..."):
                    try:
                        from api_test_workbench.engine.generator import _get_api_key, _call_ai, _parse_json_with_retry, _make_test_case, _detect_provider
                        from api_test_workbench.config.prompts import DATA_GEN_SYSTEM_PROMPT
                        from api_test_workbench.engine.models import TestCase

                        api_key = _get_api_key()
                        provider = _detect_provider(api_key)
                        model = "claude-sonnet-4-20250514" if provider == "anthropic" else "deepseek-chat"

                        data_tcs_by_step = {}
                        for i, step in enumerate(st.session_state.pipeline.steps):
                            if i not in write_idx:
                                # 查询步骤放一条空占位，保证 Pipeline 链路不断
                                data_tcs_by_step[i] = [
                                    TestCase(case_id=f"DG_SKIP_{i}", case_name="(查询步骤-跳过造数据)",
                                             operation="list", category="positive", input_data={},
                                             expected_status_code=200, assertion_logic="str(resp_json['code']) == '0'")
                                ]
                                continue
                            bt = step.config.body_template if step.config.body_template else {}
                            step_prompt = f"""请为以下接口生成 {data_count} 条正向真实数据。

数据需求：
{data_desc}

接口：{step.config.method} {step.config.url}
Body 模板（已有默认值，只需随机化用户指定的字段）：{json.dumps(bt, ensure_ascii=False)}

要求：
- 生成恰好 {data_count} 条用例，case_id 用 DG_001 ~ DG_{data_count:03d} 格式
- 名称/编码字段使用 {{{{index}}}} 占位符（运行时替换为递增序号）
- expected_status_code 一律 200，assertion_logic: str(resp_json['code']) == '0'
- 只输出 JSON"""
                            raw = _call_ai(api_key, DATA_GEN_SYSTEM_PROMPT, step_prompt, model)
                            parsed = _parse_json_with_retry(api_key, raw, model)
                            raw_cases = [_make_test_case(tc) for tc in parsed.get("test_cases", [])]
                            # 替换 {{index}} 为实际序号（001, 002, ...）
                            for idx, tc in enumerate(raw_cases):
                                def _replace_index(obj):
                                    if isinstance(obj, dict):
                                        return {k: _replace_index(v) for k, v in obj.items()}
                                    elif isinstance(obj, list):
                                        return [_replace_index(v) for v in obj]
                                    elif isinstance(obj, str):
                                        return obj.replace("{{index}}", f"{idx + 1:03d}")
                                    return obj
                                tc.input_data = _replace_index(tc.input_data)
                            data_tcs_by_step[i] = raw_cases
                        total = sum(len(v) for v in data_tcs_by_step.values())
                        st.session_state.data_gen_cases = data_tcs_by_step
                        st.success(f"已生成 {total} 条造数据用例（{len(write_idx)} 个写步骤 × ~{data_count} 条）")
                    except Exception as e:
                        st.error(f"生成失败: {e}")

        # 执行 & 导出
        has_data = bool(st.session_state.get("data_gen_cases"))
        if has_data:
            total_cases = sum(len(v) for v in st.session_state.data_gen_cases.values())
            st.success(f"已就绪：{len(st.session_state.data_gen_cases)} 个步骤，共 {total_cases} 条造数据用例")

        exec_col1, exec_col2, _ = st.columns([1, 1, 3])
        with exec_col1:
            run_data_clicked = st.button("▶ 执行造数据", type="primary", use_container_width=True, disabled=not has_data)
        with exec_col2:
            if has_data:
                if st.button("📦 导出 pytest", key="export_data_tab", use_container_width=True):
                    try:
                        exporter = PytestExporter(
                            pipeline=st.session_state.pipeline,
                            test_cases_by_step=st.session_state.data_gen_cases,
                            auth_url=st.session_state.get("auth_url", ""),
                            auth_body={"username": st.session_state.get("auth_username", ""),
                                       "password": st.session_state.get("auth_password", "")},
                            data_only=True,
                        )
                        zip_bytes = exporter.export_to_zip_bytes()
                        st.session_state._export_data_zip = zip_bytes
                        st.session_state._export_data_name = f"data_gen_{_sanitize_filename(st.session_state.pipeline.name)}.zip"
                    except Exception as e:
                        st.error(f"导出失败: {e}")

                if st.session_state.get("_export_data_zip"):
                    st.download_button(
                        label="⬇️ 下载 ZIP",
                        data=st.session_state._export_data_zip,
                        file_name=st.session_state.get("_export_data_name", "data_gen.zip"),
                        mime="application/zip",
                        use_container_width=True,
                    )

        if run_data_clicked:
            if not st.session_state.auth_ok:
                st.error("请先在「测试流程」Tab → ③ 认证 & 执行 → 获取 Session")
            else:
                _normalize_step_urls()
                pipeline = st.session_state.pipeline
                tcs_by_step = st.session_state.data_gen_cases
                session = st.session_state.auth_session

                total_cases = max((len(v) for v in tcs_by_step.values()), default=0)
                total_steps = len(pipeline.steps)
                total_ops = total_cases * total_steps

                progress_bar = st.progress(0, "造数据中...")
                status_text = st.empty()
                counter = [0]

                from api_test_workbench.engine.runner import execute_pipeline

                def update_progress(current, total, result):
                    counter[0] += 1
                    progress_bar.progress(counter[0] / total_ops, f"造数据 {counter[0]}/{total_ops}")

                with st.spinner("批量造数据中..."):
                    pipeline_result = execute_pipeline(
                        pipeline=pipeline,
                        session=session,
                        test_cases_by_step=tcs_by_step,
                        progress_callback=update_progress,
                    )

                progress_bar.empty()
                status_text.empty()

                # 从 PipelineResult 统计成功/失败
                success = fail = 0
                for sr in pipeline_result.step_results:
                    for tr in sr.test_results:
                        if tr.passed:
                            success += 1
                        else:
                            fail += 1
                total = success + fail
                if fail == 0:
                    st.success(f"🎉 造数据完成！全部 {total} 条成功")
                else:
                    st.warning(f"造数据完成：{success} 成功 / {fail} 失败（共 {total} 条）")
