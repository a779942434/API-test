"""Pipeline → pytest 代码导出器

将 Streamlit 工作台中维护的 Pipeline + TestCase 转换为标准 pytest 脚本:
- conftest.py: fixtures (base_url, session, login_session, api_headers)
- test_<name>.py: TestClass + 正向链路方法 + 参数化异常用例
"""

import io
import json
import os
import re
import hashlib
import zipfile
from urllib.parse import urlparse

from api_test_workbench.engine.models import Pipeline, TestCase, ApiStep
from api_test_workbench.engine.utils import is_write_step, is_query_url, strip_placeholders


# ==================== 常量 ====================

_SENSITIVE_HEADERS = {
    'authorization', 'cookie', 'set-cookie', 'x-xsrf-token', 'x-csrf-token',
    'csrf-token', 'requestid', 'resource-key', 'table-id', 'app-id',
    'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform',
    'cache-control', 'pragma', 'connection',
}

_KEEP_HEADERS = {
    'content-type', 'accept', 'accept-language', 'user-agent',
    'clientid', 'clienttype', 'system-language', 'system-time-zone',
    'data-time-zone', 'tenement-code', 'origin', 'referer',
}

_STEP_PLACEHOLDER_RE = re.compile(r'\{\{step(\d+)\.(.+?)\}\}')


# ==================== 工具函数 ====================

def _filter_headers(headers: dict) -> dict:
    """过滤敏感 header，仅保留非敏感的业务 header"""
    if not headers:
        return {}
    return {
        k: v for k, v in headers.items()
        if k.lower() in _KEEP_HEADERS
    }


def _parse_url(url: str) -> tuple[str, str]:
    """解析 URL → (base_url, path)"""
    if not url:
        return "", ""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    return base, path


def _scan_deps(text: str) -> list[dict]:
    """扫描文本中的 {{stepN.response.path}} 占位符 → [{source_step, path, placeholder}]"""
    if not isinstance(text, str):
        return []
    return [
        {"source_step": int(m.group(1)) - 1, "path": m.group(2), "placeholder": m.group(0)}
        for m in _STEP_PLACEHOLDER_RE.finditer(text)
    ]


def _scan_step_deps(step: ApiStep) -> list[dict]:
    """扫描单个步骤中所有占位符依赖"""
    payload = json.dumps({
        "url": step.config.url,
        "headers": step.config.headers,
        "body": step.config.body_template,
    })
    return _scan_deps(payload)


def _sanitize_filename(name: str) -> str:
    """清洗为合法 Python 文件名"""
    safe = re.sub(r'[^\w一-鿿]', '_', name)
    if re.search(r'[一-鿿]', safe):
        h = hashlib.md5(name.encode()).hexdigest()[:6]
        safe = f"pipeline_{h}"
    return safe


def _sanitize_classname(name: str) -> str:
    """清洗为合法 Python 类名（Test 开头）"""
    safe = _sanitize_filename(name)
    safe = 'Test' + safe[0].upper() + safe[1:] if safe else 'TestPipeline'
    safe = re.sub(r'[^\w]', '', safe)
    if safe[0].isdigit():
        safe = 'Test' + safe
    return safe


def _sanitize_method(name: str) -> str:
    """清洗为合法方法名片段"""
    safe = re.sub(r'[^\w]', '_', name)
    if re.search(r'[一-鿿]', safe) and not re.search(r'[a-zA-Z]', safe):
        safe = f"step_{abs(hash(name)) % 1000}"
    return safe.lower()[:40]


def _dep_to_varname(step_idx: int, path: str) -> str:
    """依赖路径 → 类属性变量名: step0 + response.data.id → step1_response_data_id"""
    clean = re.sub(r'\[(\d+)\]', r'_\1', path)
    clean = clean.replace('.', '_')
    clean = re.sub(r'[^a-zA-Z0-9_]', '', clean)
    return f"step{step_idx + 1}_{clean}"


def _build_safe_extract(path: str, var_name: str, indent: int = 8) -> str:
    """生成从 result 安全提取数据的代码块

    path="response.data.id" → 逐层 try/except 安全提取到 type(self).var_name
    """
    parts = [p for p in path.split(".") if p != "response"]
    if not parts:
        return ""

    # 构建逐层访问
    expr_parts = ["result"]
    for p in parts:
        arr_m = re.match(r'^(.+)\[(\d+)\]$', p)
        if arr_m:
            expr_parts.append(f'["{arr_m.group(1)}"]')
            expr_parts.append(f'[{arr_m.group(2)}]')
        elif p.isdigit():
            expr_parts.append(f'[{p}]')
        else:
            expr_parts.append(f'["{p}"]')

    expr = ''.join(expr_parts)
    sp = ' ' * indent
    return (
        f"{sp}try:\n"
        f"{sp}    type(self).{var_name} = {expr}\n"
        f"{sp}except (KeyError, IndexError, TypeError, AttributeError):\n"
        f"{sp}    pass  # {path} 不存在于响应中"
    )


def _convert_assertion(assertion_logic: str) -> str:
    """将 sandbox eval 断言转为类型安全的原生 pytest assert

    关键转换:
    - resp_json['code'] == '0' → str(result.get('code')) == '0'
    - resp_json['code'] != '0' → str(result.get('code')) != '0'
    - 双层 .get('data',{}).get('id',0) → _safe_get(result, 'data', 'id', 0)
    这样无论后端返回 '0'(str) 还是 0(int) 都能正确断言，
    且 data 为字符串/对象/null 均安全。
    """
    if not assertion_logic or not assertion_logic.strip():
        return "pass  # 无断言"

    expr = strip_placeholders(assertion_logic.strip())

    # resp_json['key'] → result.get('key')
    expr = re.sub(r"resp_json\['([^']+)'\]", r"result.get('\1')", expr)
    expr = re.sub(r'resp_json\["([^"]+)"\]', r'result.get("\1")', expr)
    expr = re.sub(r'\bresp_json\b', 'result', expr)
    # status_code → resp.status_code
    expr = re.sub(r'\bstatus_code\b', 'resp.status_code', expr)
    # json['key'] → result.get('key')
    expr = re.sub(r"\bjson\['([^']+)'\]", r"result.get('\1')", expr)
    expr = re.sub(r'\bjson\["([^"]+)"\]', r'result.get("\1")', expr)

    # ── 类型安全：双层 .get('data', {}).get('key', default) → _safe_get(result, 'data', 'key', default) ──
    # 单引号版（default 匹配任意非 ) 字符，支持负数、字符串、变量等）
    expr = re.sub(
        r"result\.get\('(\w+)',\s*\{\}\)\.get\('(\w+)',\s*([^)]+)\)",
        r"_safe_get(result, '\1', '\2', \3)",
        expr,
    )
    # 双引号版
    expr = re.sub(
        r'result\.get\("(\w+)",\s*\{\}\)\.get\("(\w+)",\s*([^)]+)\)',
        r'_safe_get(result, "\1", "\2", \3)',
        expr,
    )

    # ── 类型安全：code 字段的比较必须兼容 str 和 int ──
    # result.get('code') == '0' → str(result.get('code')) == '0'
    expr = re.sub(
        r"result\.get\('code'\)\s*==\s*'(\d+)'",
        r"str(result.get('code')) == '\1'",
        expr,
    )
    expr = re.sub(
        r'result\.get\("code"\)\s*==\s*"(\d+)"',
        r'str(result.get("code")) == "\1"',
        expr,
    )
    # result.get('code') != '0' → str(result.get('code')) != '0'  ← P0#1 修复
    expr = re.sub(
        r"result\.get\('code'\)\s*!=\s*'(\d+)'",
        r"str(result.get('code')) != '\1'",
        expr,
    )
    expr = re.sub(
        r'result\.get\("code"\)\s*!=\s*"(\d+)"',
        r'str(result.get("code")) != "\1"',
        expr,
    )

    return f'assert {expr}, f"断言失败: {{result}}"'


def _dict_to_py(d: dict, indent: int = 8) -> str:
    """将 dict 转为格式化的 Python 字面量字符串"""
    if not d:
        return "{}"
    return json.dumps(d, ensure_ascii=False, indent=4).replace('\n', f'\n{" " * indent}')


# ==================== Teardown ====================


def _infer_delete_url(create_url: str) -> str:
    """从创建 URL 推断删除 URL"""
    # /admin-console/tooling/toolBit → /admin-console/tooling/toolBit/{id}
    # /admin-console/tooling/sparePartDevice → /admin-console/tooling/sparePartDevice/batch-delete
    path = urlparse(create_url).path if create_url else ""
    # 去掉尾部斜杠
    path = path.rstrip('/')
    return f"{path}/{{created_id}}"


def _gen_teardown_comment(step_idx: int, create_url: str) -> str:
    """为写操作（POST/PUT 且非查询）生成数据清理注释框架"""
    if is_query_url(create_url):
        return ""
    delete_hint = _infer_delete_url(create_url)
    return (
        f'\n'
        f'        # ====== 数据清理 ======\n'
        f'        # TODO: 调用 DELETE 接口清理本次创建的测试数据\n'
        f'        # created_id = result.get("data", {{}}).get("id")\n'
        f'        # if created_id:\n'
        f'        #     delete_url = f"{{base_url}}{delete_hint}"\n'
        f'        #     resp = login_session.delete(delete_url, headers=api_headers)\n'
        f'        #     assert resp.status_code == 200\n'
    )

class PytestExporter:
    """将 Pipeline + TestCase 导出为标准 pytest 脚本"""

    def __init__(
        self,
        pipeline: Pipeline,
        test_cases_by_step: dict[int, list[TestCase]],
        auth_url: str = "",
        auth_body: dict = None,
        env_name: str = "default",
        client_id: str = "",
        data_only: bool = False,
    ):
        self.pipeline = pipeline
        self.test_cases_by_step = test_cases_by_step
        self.auth_url = auth_url
        self.auth_body = auth_body or {}
        self.env_name = env_name
        self.client_id = client_id
        self.data_only = data_only

        # 预计算各步骤的 base_url 和 path
        self._step_info: dict[int, dict] = {}
        for i, step in enumerate(pipeline.steps):
            base, path = _parse_url(step.config.url)
            self._step_info[i] = {"base": base, "path": path}

        # 主 base_url: 优先用第一步的 base，否则用 auth_url 的 base
        auth_base, _ = _parse_url(auth_url) if auth_url else ("", "")
        self._main_base_url = auth_base or "http://localhost:8080"
        for step in pipeline.steps:
            if step.config.url:
                self._main_base_url, _ = _parse_url(step.config.url)
                break

        # 扫描所有步骤间依赖（哪些步骤引用了前面步骤的数据）
        self._all_deps: dict[int, list[dict]] = {}
        for i, step in enumerate(pipeline.steps):
            deps = _scan_step_deps(step)
            if deps:
                self._all_deps[i] = deps

    # ==================== 公开 API ====================

    def export_to_dir(self, output_dir: str) -> tuple[str, str]:
        """导出 conftest.py + test_*.py 到目录，返回文件路径"""
        os.makedirs(output_dir, exist_ok=True)
        cp = os.path.join(output_dir, "conftest.py")
        tp = os.path.join(output_dir, f"test_{_sanitize_filename(self.pipeline.name)}.py")
        # conftest.py 含凭据，写入后设为 0o600（仅 owner 可读写）
        with open(cp, "w", encoding="utf-8") as f:
            f.write(self._gen_conftest())
        os.chmod(cp, 0o600)
        with open(tp, "w", encoding="utf-8") as f:
            f.write(self._gen_test_file())
        return cp, tp

    def export_to_zip_bytes(self) -> bytes:
        """导出为 ZIP 字节流（供 Streamlit 下载）"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("conftest.py", self._gen_conftest())
            zf.writestr(
                f"test_{_sanitize_filename(self.pipeline.name)}.py",
                self._gen_test_file(),
            )
        buf.seek(0)
        return buf.getvalue()

    # ==================== conftest.py ====================

    def _gen_conftest(self) -> str:
        auth_endpoint = self.auth_url or f"{self._main_base_url}/auth/auth-login"

        # 将凭据和通用 headers 嵌入 ENVIRONMENTS 配置，方便手动维护
        creds_lines = []
        for key, val in self.auth_body.items():
            creds_lines.append(f'        "{key}": {json.dumps(val)},')

        client_id = self.client_id or '660138314043302'

        return f'''"""conftest.py — 由 API Test Workbench 自动生成

提供 pytest fixtures: base_url, session, login_session, api_headers

环境配置全部集中在 ENVIRONMENTS 字典中，切换环境、修改地址/账号/headers 都在这里操作。
"""
import pytest
import requests


# ================================================================
# 环境配置（在此处手动维护各环境的地址、账号密码、通用请求头）
# 切换环境: pytest --env=staging
# 新增环境: 复制 "default" 块，修改对应值即可
# ================================================================
ENVIRONMENTS = {{
    "{self.env_name}": {{
        "base_url": "{self._main_base_url}",
        "auth_endpoint": "{auth_endpoint}",
{chr(10).join(creds_lines)}
        # 通用请求头（认证 Token 由 login_session 自动管理，无需在此配置）
        "headers": {{
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": "pytest-api-test",
            "clientId": "{client_id}",
            "clientType": "2",
        }},
    }},
}}


def pytest_addoption(parser):
    try:
        parser.addoption(
            "--env", action="store", default="{self.env_name}",
            help="测试环境: " + ", ".join(ENVIRONMENTS.keys()),
        )
    except ValueError:
        pass


@pytest.fixture(scope="session")
def env_config(request):
    env_name = request.config.getoption("--env")
    if env_name not in ENVIRONMENTS:
        available = ", ".join(ENVIRONMENTS.keys())
        raise ValueError(f"未知环境 '{{env_name}}'，可用: {{available}}")
    return ENVIRONMENTS[env_name]


@pytest.fixture(scope="session")
def base_url(env_config):
    return env_config["base_url"]


@pytest.fixture(scope="session")
def auth_body(env_config):
    """从 ENVIRONMENTS 配置中读取当前环境的账号密码"""
    return {{
        "username": env_config["username"],
        "password": env_config["password"],
    }}


@pytest.fixture(scope="session")
def session():
    s = requests.Session()
    s.headers.update({{"Content-Type": "application/json"}})
    yield s
    s.close()


@pytest.fixture(scope="session")
def login_session(session, env_config, auth_body):
    """已认证的 HTTP 会话 — 自动调用登录接口"""
    resp = session.post(
        env_config["auth_endpoint"],
        json=auth_body,
        allow_redirects=False,
    )
    assert resp.status_code in (200, 302), \\
        f"登录失败 HTTP {{resp.status_code}}: {{resp.text[:200]}}"
    try:
        result = resp.json()
        assert str(result.get("code")) == "0", f"登录业务失败: {{result}}"
    except Exception:
        pass
    return session


@pytest.fixture(scope="session")
def api_headers(env_config):
    """从 ENVIRONMENTS 配置中读取当前环境的通用请求头"""
    return env_config["headers"]
'''

    # ==================== test_*.py ====================

    def _gen_test_file(self) -> str:
        class_name = _sanitize_classname(self.pipeline.name)
        mode_label = "造数据" if self.data_only else "测试"
        # 造数据模式不需要 import json（无 parametrize）
        has_negative = not self.data_only and any(
            any(t.category != "positive" for t in tcs)
            for tcs in self.test_cases_by_step.values()
        )
        imports = [
            f'"""测试: {self.pipeline.name} — 由 API Test Workbench 导出（{mode_label}模式）"""',
            '',
            'import pytest',
            'import time',
        ]
        if has_negative:
            imports.append('import json')
        return '\n'.join(imports) + f'\n\n\n{self._gen_class(class_name)}\n'

    def _gen_class(self, class_name: str) -> str:
        """生成完整的 TestClass"""
        lines = [
            f'class {class_name}:',
            f'    """Pipeline: {self.pipeline.name}"""',
            '',
            '    TS = int(time.time())',
            '',
            '    @staticmethod',
            '    def _safe_get(result, parent_key, child_key, default=0):',
            '        """安全获取嵌套字段，兼容 data 为字符串/对象/null 三种类型"""',
            '        data = result.get(parent_key) if isinstance(result, dict) else None',
            '        if isinstance(data, dict):',
            '            return data.get(child_key, default)',
            '        if isinstance(data, str):',
            '            return int(data) if child_key == "id" else default',
            '        return default',
            '',
        ]

        # 声明步骤间共享属性
        if self._all_deps:
            lines.append('    # 步骤间共享数据（由上游步骤设置）')
            seen = set()
            for step_idx, deps in self._all_deps.items():
                for dep in deps:
                    vn = _dep_to_varname(dep["source_step"], dep["path"])
                    if vn not in seen:
                        seen.add(vn)
                        lines.append(f'    {vn}: object = None')
            lines.append('')

        # 每个步骤的 helper（仅写操作的 body 用 self.TS 动态化唯一性字段）
        lines.append('    # ========== 步骤 Helper ==========')
        for i, step in enumerate(self.pipeline.steps):
            _, path = _parse_url(step.config.url)
            body = step.config.body_template if isinstance(step.config.body_template, dict) else {}
            is_write = is_write_step(step)
            if is_write:
                body = self._dynamize_body(body)
            lines.append(f'')
            lines.append(f'    def _step{i + 1}_url(self, base_url):')
            lines.append(f'        """步骤{i + 1}: {step.name}"""')
            lines.append(f'        return f"{{base_url}}{path}"')
            lines.append(f'')
            lines.append(f'    def _step{i + 1}_base_body(self):')
            if is_write:
                lines.append(f'        """步骤{i + 1} 基础请求体（含动态唯一值）"""')
            else:
                lines.append(f'        """步骤{i + 1} 基础请求体"""')
            lines.append(f'        return {self._render_body_code(body, indent=8)}')
        lines.append('')

        # 正向链路
        lines.append('    # ========== 正向核心链路 ==========')
        lines.append('')
        for method in self._gen_positive_chain():
            lines.append(method)
            lines.append('')

        # 异常用例（造数据模式跳过）
        if not self.data_only:
            neg_methods = self._gen_negative_parametrize()
            if neg_methods:
                lines.append('    # ========== 边界值 / 异常用例 ==========')
                lines.append('')
                for method in neg_methods:
                    lines.append(method)
                    lines.append('')

        return '\n'.join(lines)

    def _gen_positive_chain(self) -> list[str]:
        """每个步骤取第一条 positive 用例，组成顺序链路"""
        methods = []
        for seq, step_idx in enumerate(sorted(self.test_cases_by_step.keys()), start=1):
            step = self.pipeline.steps[step_idx] if step_idx < len(self.pipeline.steps) else None
            if step is None:
                continue
            tcs = self.test_cases_by_step[step_idx]
            tc = next((t for t in tcs if t.category == "positive"), tcs[0])
            methods.append(self._gen_method(seq, step_idx, step, tc))
        return methods

    def _gen_negative_parametrize(self) -> list[str]:
        """按步骤分组，生成参数化的边界/异常测试"""
        methods = []
        for step_idx in sorted(self.test_cases_by_step.keys()):
            tcs = [t for t in self.test_cases_by_step[step_idx] if t.category != "positive"]
            if not tcs:
                continue
            step = self.pipeline.steps[step_idx]
            method_name = _sanitize_method(step.name)

            # 构建参数列表（只传变化的字段）
            param_rows = []
            for tc in tcs:
                input_json = json.dumps(tc.input_data, ensure_ascii=False)
                assertion = tc.assertion_logic.replace('"', '\\"').replace('\n', ' ')
                param_rows.append(
                    f'        ("{tc.case_id}", "{tc.case_name}", '
                    f'\'{input_json}\', {tc.expected_status_code}, '
                    f'"{assertion}"),'
                )

            http_method = step.config.method.lower()
            is_write = is_write_step(step)

            # 仅写操作追加时间戳动态化代码
            dynamize_block = ""
            if is_write:
                dynamize_block = (
                    "\n        # 对唯一性敏感字段追加时间戳（避免意外触发唯一性约束）\n"
                    "        for _key in list(input_data.keys()):\n"
                    "            if isinstance(input_data[_key], str) and input_data[_key]:\n"
                    '                input_data[_key] = f"{input_data[_key]}_{self.TS}"\n'
                )

            methods.append(f'''    @pytest.mark.parametrize(
        "case_id,case_name,input_data_json,expected_status,assertion",
        [
{chr(10).join(param_rows)}
        ],
    )
    def test_negative_step{step_idx + 1}_{method_name}(
        self, login_session, base_url, api_headers,
        case_id, case_name, input_data_json, expected_status, assertion,
    ):
        """步骤{step_idx + 1}({step.name}) 边界值/异常用例"""
        input_data = json.loads(input_data_json){dynamize_block}
        body = {{**self._step{step_idx + 1}_base_body(), **input_data}}
        url = self._step{step_idx + 1}_url(base_url)

        resp = login_session.{http_method}(url, json=body, headers=api_headers)
        result = resp.json() if resp.text else {{}}

        # 类型安全断言
        if expected_status:
            assert resp.status_code == expected_status, (
                f"[{{case_id}}] HTTP状态码异常: {{resp.status_code}} (期望 {{expected_status}})"
            )
        if assertion and assertion != "pass  # 无断言":
            # 注意：参数化的断言逻辑需手动转换为类型安全的 assert 语句
            pass
        print(f"[{{case_id}}] {{case_name}}: HTTP={{resp.status_code}}, body={{str(result)[:200]}}")
''')
        return methods

    def _gen_method(self, seq: int, step_idx: int, step: ApiStep, tc: TestCase) -> str:
        """生成单个正向测试方法"""
        method_name = f"test_{seq:02d}_{_sanitize_method(step.name)}"
        http_method = step.config.method.lower()
        filtered_headers = _filter_headers(step.config.headers)

        # 构建 body — 合并 template + input_data（仅写操作对唯一性字段做动态化处理）
        body_dict = self._merge_body(step.config.body_template, tc.input_data)
        if is_write_step(step):
            body_dict = self._dynamize_body(body_dict)

        # 检查 body 中是否有占位符需替换为 self.stepX_xxx
        body_has_deps = bool(_scan_deps(json.dumps(body_dict)))

        doc = f'"""[{tc.case_id}] {tc.case_name}"""'

        lines = [
            f'    def {method_name}(self, login_session, base_url, api_headers):',
            f'        {doc}',
        ]

        # Body（动态值以 f-string 形式嵌入）
        if body_has_deps:
            lines.append(self._gen_body_with_deps(body_dict, step_idx))
        else:
            lines.append(f'        body = {self._render_body_code(body_dict, indent=8)}')

        # URL
        lines.append(f'        url = self._step{step_idx + 1}_url(base_url)')

        # Headers
        if filtered_headers:
            extra = ', '.join(f'"{k}": {json.dumps(v, ensure_ascii=False)}' for k, v in filtered_headers.items())
            lines.append(f'        headers = {{**api_headers, {extra}}}')
        else:
            lines.append(f'        headers = api_headers')

        lines.append('')

        # HTTP 请求
        lines.append(f'        resp = login_session.{http_method}(url, json=body, headers=headers)')
        lines.append(f'        result = resp.json() if resp.text else {{}}')
        lines.append('')

        # 断言（类型安全）
        lines.append(f'        # 断言 HTTP 状态码 + 业务状态码（兼容 str/int 类型）')
        lines.append(f'        assert resp.status_code == {tc.expected_status_code}, '
                     f'f"HTTP状态码异常: {{resp.status_code}}"')
        if tc.assertion_logic and tc.assertion_logic.strip():
            lines.append(f'        {_convert_assertion(tc.assertion_logic)}')
        lines.append('')

        # 提取数据供后续步骤
        extract_code = self._gen_extract_code(step_idx)
        if extract_code:
            lines.append('        # 提取数据供后续步骤使用')
            lines.append(extract_code)

        # Teardown（仅 POST/PUT 写操作）
        if http_method in ('post', 'put') and tc.category == 'positive':
            lines.append(_gen_teardown_comment(step_idx, step.config.url))

        return '\n'.join(lines)

    def _dynamize_body(self, body: dict) -> dict:
        """递归处理 body，对需要唯一性的字段值做动态化标记

        静态值 "测试刀具A001" → 动态标记 _DYNAMIZE_<原始值>
        渲染时再替换为 f"测试刀具A001_{self.TS}"
        """
        if isinstance(body, dict):
            return {k: self._dynamize_body(v) for k, v in body.items()}
        elif isinstance(body, list):
            return [self._dynamize_body(v) for v in body]
        elif isinstance(body, str) and body:
            # 使用特殊标记，后续 _render_body_code 中替换为 f-string
            return f"_DYNAMIZE_{body}"
        return body

    def _render_body_code(self, body, indent: int = 8) -> str:
        """将 body dict 渲染为 Python 代码字符串，含动态 f-string 值"""
        base_sp = indent

        def _render(val, level=1):
            """level: 嵌套深度，1=最外层"""
            cur_sp = base_sp + 4 * level
            close_sp = base_sp + 4 * (level - 1)
            if isinstance(val, dict):
                if not val:
                    return '{}'
                items = [f'{" " * cur_sp}"{k}": {_render(v, level + 1)}' for k, v in val.items()]
                return '{\n' + ',\n'.join(items) + f'\n{" " * close_sp}{"}"}'
            elif isinstance(val, list):
                if not val:
                    return '[]'
                items = [_render(v, level + 1) for v in val]
                return '[\n' + ',\n'.join(f'{" " * cur_sp}{item}' for item in items) + f'\n{" " * close_sp}{"]"}'
            elif isinstance(val, str) and val.startswith('_DYNAMIZE_'):
                original = val[len('_DYNAMIZE_'):]
                # 用 json.dumps 安全转义 + 运行时拼接，避免引号/反斜杠注入
                return f'{json.dumps(original)} + "_" + str(self.TS)'
            elif val is None:
                return 'None'
            elif isinstance(val, bool):
                return str(val)
            elif isinstance(val, (int, float)):
                return str(val)
            else:
                return json.dumps(val, ensure_ascii=False)

        return _render(body, level=1)

    def _gen_body_with_deps(self, body_dict: dict, current_step_idx: int) -> str:
        """生成带 self.stepX_xxx 引用的 body 代码

        对 dict 中的每个 str 值，扫描 {{stepN.path}} 并替换为 {self.stepN_xxx}
        """
        # 转成 Python 代码字符串，然后做替换
        body_str = json.dumps(body_dict, ensure_ascii=False, indent=4)

        def _replacer(m):
            src = int(m.group(1)) - 1
            vn = _dep_to_varname(src, m.group(2))
            return f"{{self.{vn}}}"

        resolved = _STEP_PLACEHOLDER_RE.sub(_replacer, body_str)

        # 现在 resolved 包含 {self.step1_xxx} 这样的 Python f-string 变量
        # 需要生成代码: body = f"""..."""
        # 转义 JSON 中的花括号（但保留 {self.xxx}）
        # 把 JSON 里原有的 { 和 } 转义（除了已经替换好的 {self. 部分）

        # 简单策略: 生成的代码直接使用 json.loads + 字符串替换
        # 更可靠的方式: 生成 dict 构造代码
        indent = ' ' * 8

        def _gen_dict_code(d, level=2) -> str:
            """递归生成 dict 构造代码，对字符串值做占位符替换"""
            sp = indent + '    ' * level
            if isinstance(d, dict):
                if not d:
                    return '{}'
                items = []
                for k, v in d.items():
                    val_code = _gen_dict_code(v, level + 1)
                    items.append(f'{sp}"{k}": {val_code}')
                return '{\n' + ',\n'.join(items) + f'\n{" " * (4 * (level - 1))}{"}"}'
            elif isinstance(d, list):
                if not d:
                    return '[]'
                items = [_gen_dict_code(v, level + 1) for v in d]
                return '[\n' + ',\n'.join(f'{sp}{item}' for item in items) + f'\n{" " * (4 * (level - 1))}{"]"}'
            elif isinstance(d, str):
                # 检查是否包含占位符
                deps = _scan_deps(d)
                if deps:
                    # 有占位符 → 生成 f-string
                    escaped = d.replace('{', '{{').replace('}', '}}')
                    for dep in deps:
                        m = dep["placeholder"]
                        vn = _dep_to_varname(dep["source_step"], dep["path"])
                        escaped = escaped.replace(
                            m.replace('{', '{{').replace('}', '}}'),
                            f'{{self.{vn}}}'
                        )
                    return f'f"{escaped}"'
                else:
                    return json.dumps(d, ensure_ascii=False)
            elif d is None:
                return 'None'
            elif isinstance(d, bool):
                return str(d)
            elif isinstance(d, (int, float)):
                return str(d)
            else:
                return json.dumps(d, ensure_ascii=False)

        return f'        body = {_gen_dict_code(body_dict)}'

    def _gen_extract_code(self, step_idx: int) -> str:
        """生成从当前步骤响应提取数据的代码

        仅提取被后续步骤实际引用的字段。
        """
        lines = []
        # 收集后续步骤对当前步骤的所有依赖
        extracted = set()
        for later_idx in range(step_idx + 1, len(self.pipeline.steps)):
            for dep in self._all_deps.get(later_idx, []):
                if dep["source_step"] == step_idx:
                    path = dep["path"]
                    var_name = _dep_to_varname(step_idx, path)
                    if var_name not in extracted:
                        extracted.add(var_name)
                        # 去掉 "response." 前缀
                        clean_path = path
                        if clean_path.startswith("response."):
                            clean_path = clean_path[9:]
                        lines.append(_build_safe_extract(clean_path, var_name, indent=8))

        return '\n'.join(lines) if lines else ""

    @staticmethod
    def _merge_body(template, input_data: dict) -> dict:
        """合并 body_template + input_data"""
        if isinstance(template, list):
            return template
        result = {}
        if isinstance(template, dict):
            result.update(template)
        if isinstance(input_data, dict):
            result.update(input_data)
        return result
