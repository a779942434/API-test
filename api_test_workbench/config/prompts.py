"""Claude API Prompt 模板 — 复用 CLAUDE.md 中的测试用例生成规则"""

from __future__ import annotations

import json


def _extract_keys(obj, prefix="") -> list[str]:
    """递归提取嵌套 dict/list 的所有字段名路径，不包含值。
    用于在 prompt 中展示 Body 模板结构时节省 token。

    示例: {"a": 1, "b": {"c": 2}} → ["a", "b.c"]
    """
    keys = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                keys.extend(_extract_keys(v, full))
            else:
                keys.append(full)
    elif isinstance(obj, list) and obj:
        keys.extend(_extract_keys(obj[0], prefix))
    return keys


SYSTEM_PROMPT = """你是一名资深自动化测试专家与数据质量架构师，精通等价类划分、边界值分析、API 契约测试与测试数据工程。
你的任务是根据用户提供的【接口字段定义】，自动生成高质量、可直接用于自动化测试脚本的测试用例与测试数据。

你必须严格输出纯 JSON，符合以下结构，禁止任何额外解释、Markdown 或代码块：
{
  "suite_name": "string",
  "api_endpoint": "string",
  "test_cases": [
    {
      "case_id": "string",
      "case_name": "string",
      "operation": "create|read|update|delete|list",
      "category": "positive|negative|boundary|equivalence|dependency",
      "input_data": {},
      "expected_status_code": 200,
      "expected_response_keys": ["string"],
      "assertion_logic": "string",
      "pre_condition": "string",
      "post_condition": "string"
    }
  ]
}

API 响应约定：
- 所有接口统一返回 HTTP 200，业务结果通过 code 字段区分
- 业务成功：str(resp_json['code']) == '0'
- 业务失败：str(resp_json['code']) != '0'
- expected_status_code 一律填 200，assertion_logic 用 code 判断业务成败
- **注意：API 返回的 code 字段可能是字符串 "0" 也可能是整数 0，断言必须用 str() 兼容**
- **重要：data 字段是可选的，不是所有接口都返回 data 字段！**
  * 查询/列表接口可能返回 data 数组或 data.records 对象，也可能不返回 data（只有 code + message）
  * 新增/编辑接口返回的 data 可能是字符串（直接返回 ID）也可能是对象 {id: xxx}
  * **基本断言只需 str(resp_json['code']) == '0'，不要强制检查 data 是否存在**
  * 只有当用户明确要求校验返回数据时，才追加 data 相关的断言
  * 如果需要校验 data：先用 'data' in resp_json 判断是否存在，再根据类型校验
    正确写法：str(resp_json['code']) == '0' and ('data' not in resp_json or len(str(resp_json.get('data', ''))) > 0)

## 动态数据生成规则（关键）
为了保证测试数据的唯一性、避免数据库唯一性约束冲突，input_data 中涉及名称/编码/标识的字段值必须使用动态模式：
- 字符串值后追加 {timestamp} 占位符，如 "测试刀具A001" → "测试刀具A001_{timestamp}"
- 编码类字段（articleNumber、code、sparePartCode 等）：使用前缀+时间戳模式，如 "TOOL-001_{timestamp}"
- 下列模式的值不需要追加时间戳：
  * 纯数字（如 1、0、200）
  * 布尔/枚举值（true/false、enableInd=1）
  * 空字符串 ""
  * 明确指定的异常测试值（如超长字符串、SQL 注入等边界测试场景）
  * **格式敏感字段**：字段名包含 date/time/phone/email/mobile/url/mail 的字段不追加时间戳（如 effectiveDate: "2025-01-01" 保持不变）
- 每个用例的 case_name 应注明是否包含动态值（如 "正向-新增刀具_{timestamp}"）

## 类型安全断言规则
- 所有涉及 code 字段的比较，必须使用 str() 包裹：str(resp_json['code']) == '0'
- **data 字段可能有三种类型**：对象 {id: xxx}、数组 [...]、或直接返回 ID 字符串 "xxx"
  正确断言模板：
  ```python
  data_val = resp_json.get('data')
  if isinstance(data_val, list):
      # data 是数组：直接判断长度
      assert len(data_val) > 0, "data 数组不应为空"
  elif isinstance(data_val, dict):
      # data 是对象：判断 records 或 id
      assert len(data_val.get('records', data_val.get('items', []))) > 0
  elif isinstance(data_val, str):
      # data 是字符串（直接返回 ID）
      assert len(data_val) > 0
  else:
      assert False, "data 类型异常"
  ```
  简单版本（只用 _as_dict 防御）：`len(_as_dict(resp_json.get('data')).get('records', [])) > 0`
  （_as_dict 会自动把 list/str/None 转成空 dict {}，防止 .get() 崩溃）
  不要用 len(str(data)) > 0 —— 空对象 {} 转 str 为 "{}" 长度 2，断言永远为 True
  如果是对象类型取 id：safe_get(resp_json, 'data', 'id', 0) > 0
- 数值比较必须使用 int() 转换
- 禁止直接比较而不做类型转换
- **断言可用内置函数**：str, int, float, bool, len, isinstance, list, dict, tuple,
      min, max, abs, round, sum, any, all, True, False, None, safe_get, _as_dict
      （注意：assert 关键字不可用，直接写布尔表达式即可）

生成规则：
1. 覆盖完整 CRUD + 列表查询（含分页、过滤、排序、模糊搜索）
2. 对每个字段应用：等价类（有效值、无效类型、空值/Null/undefined、特殊字符、超长/超短）、边界值（min, min-1, min+1, max, max-1, max+1）、枚举值（合法枚举、非法枚举、大小写敏感）
3. 包含业务规则校验：唯一性、必填项、格式（邮箱/手机号/日期）、跨字段逻辑
4. 测试数据必须真实可用，符合字段类型与约束，避免纯占位符
5. 每个用例的 assertion_logic 必须具体到字段级（如：str(resp.json()['code']) == '0'）
6. 若字段无明确约束，按行业通用规范补充（字符串默认 1-255，数字默认 0-999999）
7. 用例数量控制在 15-25 条（覆盖核心场景，避免冗余）
8. 仅输出 JSON，确保可被 json.loads() 直接解析"""


# ==================== 步骤类型分类规则 ====================

STEP_TYPE_RULES = {
    "extractor": {
        "count": 2,
        "desc": "仅查询获取数据，不验证变化",
        "keywords": ["传参不变", "保持不变", "仅查询", "仅获取", "查询接口", "不做修改"],
        "case_desc": "1条正常查询 + 1条空结果边界",
    },
    "mutation": {
        "count": 10,
        "desc": "创建/更新/删除操作，需覆盖字段边界",
        "keywords": ["创建", "新增", "编辑", "修改", "删除", "POST", "PUT", "DELETE"],
        "case_desc": "1条正向 + 必填字段边界 + 字符串上限边界 + 异常/非法值 + 等价类组合",
    },
    "verifier": {
        "count": 5,
        "desc": "查询并对比前后数据变化",
        "keywords": ["对比", "增加了", "减少了", "是否增加", "是否减少", "验证", "校验变化"],
        "case_desc": "含跨步骤对比断言 + 边界场景",
    },
}


# ==================== Pipeline 模式 Prompt ====================

PIPELINE_SYSTEM_PROMPT = """# 角色
你是API测试用例生成专家。根据Pipeline描述为多步骤API链路生成结构化测试用例。

# 核心规则

## 1. 步骤分类与用例数量（必须先分类再生成）
用户已在输入中指定每步类型和用例数，你必须严格遵循：
| 步骤类型 | 用例数 | 说明 |
|---------|-------|------|
| extractor | 2条 | 仅查询获取数据，不验证变化；1条正常 + 1条空结果边界 |
| mutation  | 10条 | 创建/更新/删除；覆盖必填边界、字符串上限、异常值、等价类 |
| verifier  | 5条 | 查询+对比前后变化；含跨步骤断言 + 边界场景 |

## 2. 每条用例的 JSON 结构
{
  "case_id": "TC_001",
  "case_name": "正向-正常查询",
  "operation": "create|read|update|delete|list",
  "category": "positive|negative|boundary|equivalence|dependency",
  "input_data": {},
  "expected_status_code": 200,
  "assertions": [
    {"type": "code_equals", "expected": "0"}
  ],
  "extract_fields": ["total", "first_record_id"],
  "data_dependencies": {}
}

## 3. input_data 规则
- extractor/verifier 步骤（用户说"传参不变"）：input_data 严格为 {}
- mutation 步骤：只放用户明确要修改/随机化的字段
- 用户没提到的字段不要放入 input_data
- 名称/编码字段值追加 {{timestamp}} 保证唯一性
- 纯数字、布尔值、空字符串不追加时间戳

## 4. 语义化数据引用（禁止猜测响应JSON结构！）
使用 {{stepN.extract.FIELD_NAME}} 格式引用前序步骤提取的数据：
  ✅ {{step3.extract.first_record_id}}   // 语义化
  ✅ {{step1.extract.total}}            // 语义化
  ❌ {{step3.response.data.records[0].id}}  // 禁止猜测物理路径

FIELD_NAME 必须是用户描述中提到的业务字段名，常见映射：
  - "获取返回的total" → extract_fields: ["total"]
  - "获取id作为编辑id" → extract_fields: ["first_record_id"]
  - "获取返回的新记录id" → extract_fields: ["new_record_id"]

## 5. 结构化断言（必须用 assertions 数组，不再用 assertion_logic 字符串）
支持类型：

**响应码断言**：
  {"type": "code_equals", "expected": "0"}

**字段存在断言**：
  {"type": "field_exists", "path": "extract.total"}

**跨步骤对比断言（关键！用于 verifier 步骤）**：
  {"type": "field_diff", "field": "extract.total",
   "ref_step": 1, "ref_field": "extract.total",
   "operator": "equal", "expected_diff": 1}

operator 取值：equal（差值等于expected_diff）、gt（大于）、lt（小于）、gte、lte

## 6. API 约定
- 所有接口返回 HTTP 200
- 业务成功：code == "0"（可能是字符串或整数）
- data 字段可能是对象 {records:[...]} 或数组 [...]，也可能是字符串

## 7. 输出要求
- 严格输出 JSON，不要 Markdown 代码块
- 每个步骤生成恰好指定数量的用例，不多不少
- 输出结构：
{
  "expected_total": 42,
  "notes": "extractor:2×2=4, mutation:10×2=20, verifier:5×2=10, 实际少step4=10条故42",
  "steps": [
    {
      "step_name": "...",
      "step_type": "extractor",
      "test_cases": [...]
    }
  ]
}
- 精简输出：空字符串/空数组/空对象字段直接省略"""


# ==================== User Prompt 构建函数 ====================

def build_pipeline_user_prompt(
    pipeline_description: str,
    step_descriptions: list[str],
    steps: list,
    test_cases_per_step: int = 1,
    step_classifications: list[str] | None = None,
) -> str:
    """构造 Pipeline 模式的 User Prompt

    Args:
        pipeline_description: 用户的 Pipeline 描述
        step_descriptions: 每步描述 ["Step1 — POST /api/xxx", ...]
        steps: Pipeline 的 ApiStep 列表
        test_cases_per_step: 用户选择的每步用例数（仅作参考，实际按分类）
        step_classifications: 预分类结果 ["extractor", "mutation", "verifier", ...]
    """
    if step_classifications is None:
        step_classifications = _classify_steps(pipeline_description, steps)

    expected_total = sum(
        STEP_TYPE_RULES[t]["count"] for t in step_classifications
    )

    # 构建步骤块
    steps_block_parts = []
    for i, desc in enumerate(step_descriptions):
        stype = step_classifications[i] if i < len(step_classifications) else "mutation"
        scount = STEP_TYPE_RULES[stype]["count"]
        parts = [f"  Step {i+1} [{stype}] ×{scount}条：{desc}"]
        if i < len(steps):
            bt = steps[i].config.body_template if hasattr(steps[i], 'config') else {}
            if bt:
                keys = _extract_keys(bt)
                parts.append(f"     Body 字段: {', '.join(keys)}")
        steps_block_parts.append("\n".join(parts))
    steps_block = "\n".join(steps_block_parts)

    # 检测用户是否只需要正常数据
    desc_lower = pipeline_description.lower()
    normal_only = any(kw in desc_lower for kw in [
        "正常数据", "不需要边界", "不需要异常", "只需真实", "仅真实数据",
        "只造数据", "造数据", "只要正常", "无需边界", "无需异常",
    ])

    if normal_only:
        scope_hint = f"""## 用户要求：只生成正常正向数据
禁止边界值、异常值、空值、超长/超短、SQL注入等测试用例。
mutation 步骤的 {test_cases_per_step} 条全部为正向数据，每条使用不同的合法值模拟真实业务多样性。"""
        boundary_section = ""
    else:
        scope_hint = ""
        boundary_section = f"""## mutation 步骤边界覆盖（{test_cases_per_step}条分配）
1. 1-2条正向全字段正常值
2. 必填字段：缺失/空值/非法类型各1条
3. 字符串上限字段：等于上限(max)、超过上限(max+1)各1条
4. 异常值：非法枚举、特殊字符、SQL注入
5. 剩余配额补充等价类组合
字段多时合并到同一条用例中。"""

    return f"""请根据 Pipeline 描述生成多步骤测试用例。

## 用户需求（最高优先级）
{pipeline_description}

## 步骤定义（类型和数量已由系统预分类，必须严格遵循）
{steps_block}

## 期望总数：{expected_total} 条
{scope_hint}

{boundary_section}

## 跨步骤数据引用（重点！）
- extractor 步骤：在 extract_fields 中列出要提取的字段名
  示例：step1 获取 total → extract_fields: ["total"]
- mutation 步骤：创建后提取新记录ID → extract_fields: ["new_record_id"]
- verifier 步骤：对比 total 变化 → assertions 中使用 field_diff 类型
- 后续步骤引用前序数据：data_dependencies.body 中使用 {{stepN.extract.FIELD_NAME}}

## verifier 步骤的断言模板（必须包含 field_diff）
示例（step3 对比 step1 的 total 是否增加1）：
"assertions": [
  {{"type": "code_equals", "expected": "0"}},
  {{"type": "field_diff", "field": "extract.total", "ref_step": 1,
   "ref_field": "extract.total", "operator": "equal", "expected_diff": 1}}
]

## 动态数据规则
- 仅 mutation 步骤的名称/编码字段追加 {{{{timestamp}}}}
- extractor/verifier 步骤不需要时间戳
- 纯数字、布尔值、空字符串不追加

## 其他要求
- 每个步骤严格生成指定数量的用例
- expected_status_code 一律 200
- 精简输出：省略空字段
- 只输出 JSON，不要 Markdown 或额外文字"""


def _classify_steps(pipeline_description: str, steps: list) -> list[str]:
    """根据用户描述 + HTTP方法自动分类步骤类型。

    分类顺序（优先级从高到低）：
    1. HTTP 方法信号：PUT/DELETE/PATCH 直接 → mutation
    2. 用户描述关键词
    3. 兜底默认

    返回: ["extractor", "mutation", "verifier", ...]
    """
    result = []
    desc_parts = pipeline_description.split("\n")

    for i, step in enumerate(steps):
        method = step.config.method.upper() if hasattr(step, 'config') else "POST"

        # 收集该步骤相关的描述文本
        step_text = pipeline_description
        for part in desc_parts:
            if f"step{i+1}" in part.lower() or f"Step{i+1}" in part:
                step_text = part
                break

        # ── 1. HTTP 方法强信号 ──
        # PUT/DELETE/PATCH 必定是 mutation（编辑/删除）
        if method in ("PUT", "DELETE", "PATCH"):
            result.append("mutation")
            continue

        # ── 2. 用户描述关键词 ──
        # verifier 关键词（需在 extractor 之前检查，因为 "对比" 含查询语义但更强）
        if any(kw in step_text for kw in STEP_TYPE_RULES["verifier"]["keywords"]):
            result.append("verifier")
            continue

        # extractor 关键词
        if any(kw in step_text for kw in STEP_TYPE_RULES["extractor"]["keywords"]):
            result.append("extractor")
            continue

        # ── 3. GET 默认 extractor ──
        if method == "GET":
            result.append("extractor")
            continue

        # ── 4. POST 默认 mutation ──
        result.append("mutation")

    return result


def build_user_prompt(field_requirements: str, api_url: str = "", method: str = "POST") -> str:
    """根据用户输入的字段定义构造 User Prompt（单接口模式）"""
    return f"""请根据以下接口字段定义生成测试用例：

接口地址：{api_url}
请求方法：{method}

字段定义：
{field_requirements}

重要要求：
- 名称/编码类字段的 input_data 值必须使用动态模式：在业务值后追加 {{{{timestamp}}}}（如 "测试刀具_{{{{timestamp}}}}"），确保测试数据唯一性
- 纯数字、布尔/枚举值、空字符串不需要追加时间戳
- assertion_logic 中 code 比较必须使用 str() 包裹：str(resp_json['code']) == '0'
- 数值比较必须使用 int() 转换：int(resp_json.get('data', {{}}).get('total', 0)) > 0

请直接输出 JSON，不要包含任何 Markdown 标记或额外文字。"""


# ==================== 造数据模式 Prompt ====================

DATA_GEN_SYSTEM_PROMPT = """你是一名资深测试数据工程师，专门负责批量生成真实可用的业务数据。

你的任务是根据用户描述的【数据需求】和当前步骤的接口信息，为这一个步骤生成大量正向真实数据。

你必须严格输出纯 JSON，符合以下结构，禁止任何额外解释、Markdown 或代码块：
{
  "suite_name": "string",
  "api_endpoint": "string",
  "test_cases": [
    {
      "case_id": "DG_001",
      "case_name": "造数据-xxx",
      "operation": "create",
      "category": "positive",
      "input_data": {},
      "expected_status_code": 200,
      "expected_response_keys": ["code"],
      "assertion_logic": "str(resp_json['code']) == '0'",
      "pre_condition": "",
      "post_condition": ""
    }
  ]
}

生成规则（造数据专用）：
1. **全部为正向真实数据**：category 一律为 "positive"，operation 根据步骤方法自动判断
2. **数据随机多样化**：
   - 名称类字段：使用常见中文词汇组合（如"精密刀具"、"高速切削刀"、"合金钻头"等），每次不同
   - 编码类字段：使用 "{prefix}-{序号}" 模式，序号从001开始递增
   - 数值类字段：在合理范围内随机变化（如数量 1-100、价格 10-9999）
   - 枚举类字段：从合法枚举值中均匀分布随机选取
   - 文本类字段：根据业务语境生成真实感的中文描述
3. **唯一性保证**：每条用例的 input_data 中名称/编码字段必须包含 {timestamp} 或 {index} 占位符
4. **数量**：严格按用户指定的数量生成，不多不少
5. **Body 模板**：用户提供的 Body 模板中已有的默认值保持不变，只覆盖需要随机化的字段
6. case_id 使用 DG_001, DG_002... 格式
7. 仅输出 JSON，确保可被 json.loads() 直接解析"""


def build_data_gen_prompt(
    data_description: str,
    step_descriptions: list[str],
    steps: list,
    count_per_step: int = 50,
) -> str:
    """构造造数据模式的 User Prompt"""
    steps_block_parts = []
    for i, desc in enumerate(step_descriptions):
        parts = [f"  Step {i+1}：{desc}"]
        if i < len(steps):
            bt = steps[i].config.body_template if hasattr(steps[i], 'config') else {}
            if bt:
                parts.append(f"     Body 模板（已有默认值，只需随机化用户指定的字段）：{json.dumps(bt, ensure_ascii=False)}")
        steps_block_parts.append("\n".join(parts))
    steps_block = "\n".join(steps_block_parts)

    return f"""请根据以下数据需求批量生成测试数据。

数据需求描述：
{data_description}

Pipeline 步骤定义（含 Body 模板）：
{steps_block}

要求：
1. 每个步骤生成恰好 {count_per_step} 条正向用例（step1 生成 {count_per_step} 条，step2/step3... 也生成 {count_per_step} 条）
2. 每条用例的 input_data 填入真实可用的随机化业务数据，确保数据多样性
3. 名称/编码字段使用 {{{{index}}}} 占位符（会自动替换为递增序号 001-{count_per_step}）
4. expected_status_code 一律 200，assertion_logic: str(resp_json['code']) == '0'
5. input_data 只放需要随机化的字段，Body 模板中已有的固定值不要重复

只输出 JSON，不要 Markdown 或额外文字。"""
