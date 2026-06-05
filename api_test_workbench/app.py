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
                st.session_state.get("auth_body", "{}"),
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
                    st.session_state.auth_body = data["auth_body"]
                    st.session_state.pipeline_results = None
                    # 清除所有步骤相关 widget 键，强制从加载数据重新初始化
                    for k in list(st.session_state.keys()):
                        if any(k.startswith(p) for p in (
                            "step_widget_ver_", "step_url_", "step_method_",
                            "step_headers_", "step_body_", "step_name_",
                            "step_curl_", "step_ignored_",
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
        "pipeline_test_cases_by_step": {},    # dict[int, list[TestCase]]
        "pipeline_results": None,              # Optional[PipelineResult]
        "auth_url": "http://bird.ob.shuyilink.com/auth/auth-login",
        "auth_body": '{"username": "", "password": ""}',
        "auth_session": None,
        "auth_ok": False,
        "field_requirements": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            # field_requirements 优先从备份恢复，避免增删步骤时丢失
            if k == "field_requirements":
                st.session_state[k] = st.session_state.get("_field_requirements_backup", v)
            else:
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

st.header("🔗 ① Pipeline 配置")

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

    with st.expander(f"{'🚫 ' if step.ignored else ''}Step {i+1}：{step.name or '(未命名)'}  — {step.config.method} {step.config.url or '(未设置URL)'}", expanded=(i == 0)):
        col_name, col_method, col_failure, col_ignore = st.columns([2.5, 1, 1.2, 0.8])
        with col_name:
            step.name = st.text_input(f"步骤名称", value=step.name, key=f"step_name_{i}", placeholder="如：创建订单")
        with col_method:
            step.config.method = st.selectbox("Method", ["POST", "GET", "PUT", "DELETE"], key=f"step_method_{i}_v{ver}", index=["POST", "GET", "PUT", "DELETE"].index(step.config.method) if step.config.method in ["POST", "GET", "PUT", "DELETE"] else 0)
        with col_failure:
            step.on_failure = st.selectbox("失败策略", ["stop", "continue"], key=f"step_failure_{i}", index=0 if step.on_failure == "stop" else 1, help="stop=停止后续步骤, continue=忽略错误继续执行")
        with col_ignore:
            st.write("")
            step.ignored = st.checkbox("忽略", value=step.ignored, key=f"step_ignored_{i}", help="跳过此步骤，数据仍向下传递")

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

def _init_session_state():
    defaults = {
        "pipeline": Pipeline(name="我的测试链路", steps=[_create_default_step()]),
        "pipeline_test_cases_by_step": {},
        "pipeline_results": None,
        "auth_url": "http://bird.ob.shuyilink.com/auth/auth-login",
        "auth_body": '{"username": "", "password": ""}',
        "auth_session": None,
        "auth_ok": False,
        "field_requirements": "",
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
        if step.ignored:
            boxes.append(
                f'<div style="display:inline-flex;align-items:center;padding:10px 18px;margin:4px 0;'
                f'border:2px dashed #999;border-radius:10px;text-align:center;'
                f'background:#f5f5f5;min-width:100px;opacity:0.6">'
                f'<div><div style="font-size:12px;color:#999;font-weight:bold;">Step {i+1} 🚫</div>'
                f'<div style="font-size:13px;color:#999;margin-top:2px;"><s>{step.name}</s></div>'
                f'<div style="font-size:11px;color:#999;margin-top:1px;">{step.config.method}</div></div>'
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
                if result.error_message:
                    st.error(f"**错误:** {result.error_message}")
                st.markdown("**响应体:**")
                if isinstance(result.response_body, dict):
                    st.json(result.response_body)
                else:
                    st.text(str(result.response_body)[:2000])


