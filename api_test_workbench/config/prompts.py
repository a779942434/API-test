"""Claude API Prompt 模板 — 复用 CLAUDE.md 中的测试用例生成规则"""

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


# ==================== Pipeline 模式 Prompt ====================

PIPELINE_SYSTEM_PROMPT = SYSTEM_PROMPT + """

## Pipeline 模式（多步骤 API 链路测试）

你现在为多步骤 API Pipeline 生成测试用例。每个步骤是独立的 API 接口，前一步的输出是后一步的输入（步骤间通过 data_dependencies 传递数据）。

输出结构（Pipeline 模式）：
{
  "pipeline_name": "string",
  "steps": [
    {
      "step_name": "string",
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
          "post_condition": "string",
          "data_dependencies": {
            "url": "string (optional, with {{stepN.response.path}} placeholders)",
            "body": "string (optional, with placeholders)",
            "headers": "string (optional, with placeholders)"
          }
        }
      ],
      "output_reference": "data.records[0].id"
    }
  ]
}

核心规则：
1. **用户描述是最高优先级**：用户说不变的字段不要放入 input_data，用户说要变的才放
2. **input_data 与 body_template 合并**：最终请求体 = {**body_template, **input_data}，input_data 的同名字段覆盖 body_template
3. **步骤间数据传递**：用 data_dependencies + {{stepN.response.path}} 占位符，不要放入 input_data
4. **唯一性**：POST/PUT 的名称/编码字段追加 {timestamp}；GET/查询固定值；纯数字/布尔/空值不加
5. **响应结构适配**：API 的 data 字段可能是数组 [...] 或对象 {records:[...]}
   - data 为数组时占位符用 {{{{stepN.response.data[0].id}}}}，断言用 isinstance(data_val, list) 判断
   - data 为对象时占位符用 {{{{stepN.response.data.records[0].id}}}}，断言用 .get('records',[])
   - 运行时如果占位符路径未精确匹配，会自动尝试回退路径（移除中间包装段如 records/list/items）
   - **随机获取**：用 {{{{stepN.response.data.random.id}}}} 每次随机选取一个元素
   - **指定位置**：用 {{{{stepN.response.data[2].id}}}} 获取第 3 个元素
   - **数组长度**：用 {{{{stepN.response.data._count}}}} 获取元素个数
6. **断言**：code 必须用 str() 包裹（兼容 "0" 和 0），data 可能是字符串、列表或对象，不要直接 .id
7. **动态字段标记**：只有名称/编码等需要唯一性的字段才在 input_data 值中追加 {{{{timestamp}}}}，
   静态描述字段（如 description: "正常报修"）不需要追加时间戳，保持原值即可
8. **精简输出（关键）**：每条用例的 JSON 必须尽量精简，以节省 token 避免截断：
   - 空字符串字段（pre_condition: "", post_condition: ""）直接省略，不要输出
   - 空数组字段（expected_response_keys: []）直接省略
   - 空对象字段（data_dependencies: {}）直接省略
   - input_data 中值为空的字段也省略
   - 这样每条用例 JSON 可以从 ~1500 字缩减到 ~500 字
9. **查询/列表步骤精简**：用户说"传参不变"/"保持不变"的 GET/查询步骤，
   → input_data 严格为 {{}}，生成 1 条核心用例即可（其余 N-1 条复用同一个 input_data）
   → 这类步骤的用例不产生 boundary/equivalence/dependency 变体，全部用 positive list
   → 只有用户明确要求测查询边界时才给查询步骤生成边界用例"""


def build_pipeline_user_prompt(
    pipeline_description: str,
    step_descriptions: list[str],
    steps: list,
    test_cases_per_step: int = 1,
) -> str:
    """构造 Pipeline 模式的 User Prompt

    设计原则：用户描述是最高优先级。本提示词只定义输出格式和必要语法，
    不覆盖用户对字段变更/不变更的意图。
    """

    steps_block_parts = []
    for i, desc in enumerate(step_descriptions):
        parts = [f"  Step {i+1}：{desc}"]
        # 附上当前 body_template 的字段名（仅 key，不展示完整值以节省 token）
        if i < len(steps):
            bt = steps[i].config.body_template if hasattr(steps[i], 'config') else {}
            if bt:
                # 只显示字段名列表，减少 prompt token 消耗
                keys = _extract_keys(bt)
                parts.append(f"     Body 字段: {', '.join(keys)}")
        steps_block_parts.append("\n".join(parts))
    steps_block = "\n".join(steps_block_parts)

    # 检测用户是否只需要正常数据（非测试覆盖场景）
    desc_lower = pipeline_description.lower()
    normal_only = any(kw in desc_lower for kw in [
        "正常数据", "不需要边界", "不需要异常", "只需真实", "仅真实数据",
        "只造数据", "造数据", "只要正常", "无需边界", "无需异常",
    ])

    if normal_only:
        count_hint = f"每个步骤生成恰好 {test_cases_per_step} 条正常正向数据"
        scope_rule = "只生成正向真实数据。禁止边界值、异常值、空值、超长/超短、SQL注入等测试用例。"
        boundary_section = f"""## 正向数据多样性规则
用户只需要正常正向数据，不需要边界/异常测试。将 {test_cases_per_step} 条用例全部分配为正向数据：
- 每条用例的所有字段值都在合法范围内（不测边界、不测异常）
- 可变字段（数值/文本/枚举）每条用不同的合法值，模拟真实业务场景多样性
- 数值字段：在合理范围内取不同值（如 1、10、50、100、500）
- 文本字段：每条用不同的真实场景描述（如 "定期维护"、"零件更换"、"故障修复"）
- 禁止生成空值、超长、特殊字符、SQL注入等测试数据"""
    elif test_cases_per_step <= 1:
        count_hint = f"每个步骤生成恰好 {test_cases_per_step} 条正向核心用例"
        scope_rule = ""
        boundary_section = ""
    elif test_cases_per_step <= 5:
        count_hint = f"每个步骤生成恰好 {test_cases_per_step} 条用例，按「边界值覆盖分配规则」严格分配：1条正向 + 剩余覆盖各字段边界（必填/上限/格式）。每条的可变字段值必须不同！"
        scope_rule = ""
        boundary_section = f"""## 边界值覆盖分配规则（关键）
用户描述的每个字段约束（必填/上限/下限等）都必须有对应的边界测试用例。
将 {test_cases_per_step} 条用例按以下优先级分配，确保每个有约束的字段都被覆盖：
1. **1 条正向全字段正常值**：所有字段使用合法范围内的典型值
2. **必填字段边界**：必填字段测缺失/空值/非法类型；数值字段测 0、负数、极大值
3. **字符串上限字段边界**：每个有字符上限的字段各占 1 条 → 测等于上限（max）、超过上限（max+1）
   - 例：repairReason 上限100字 → 1条测 100 字、下一条测 101 字
   - 例：repairContent 上限200字 → 1条测 200 字
   - 字段多的，合并到同一条用例中（一条用例可同时测多个字段边界）
4. **异常/非法值**：非法枚举、特殊字符、SQL注入等
5. **剩余配额**：补充其他等价类或组合边界场景

**注意**：如果 {test_cases_per_step} 条不够覆盖所有字段边界，优先覆盖强约束字段（必填 > 上限 > 格式），并在 case_name 中注明「组合边界」。
"""
    else:
        count_hint = f"每个步骤生成恰好 {test_cases_per_step} 条用例，按「边界值覆盖分配规则」分配：正向 + 各字段边界 + 异常 + 等价类。全面覆盖！"
        scope_rule = ""
        boundary_section = f"""## 边界值覆盖分配规则（关键）
用户描述的每个字段约束（必填/上限/下限等）都必须有对应的边界测试用例。
将 {test_cases_per_step} 条用例按以下优先级分配，确保每个有约束的字段都被覆盖：
1. **正向全字段正常值**：所有字段使用合法范围内的典型值（占 1-2 条）
2. **必填字段边界**：必填字段测缺失/空值/非法类型；数值字段测 0、负数、极大值
3. **字符串上限字段边界**：每个有字符上限的字段各占至少 1 条 → 测等于上限、超过上限
4. **异常/非法值**：非法枚举、特殊字符、SQL注入等
5. **等价类组合**：剩余配额覆盖其他等价类或组合边界场景
"""

    return f"""请根据用户的 Pipeline 描述生成多步骤测试用例。

## 用户需求（最高优先级，必须严格遵循）
{pipeline_description}

## 步骤定义
{steps_block}

## 如何判断 input_data 放什么（按用户描述决定）
- 用户说「传参不变」「保持不变」「不修改」「其余传参不变」的步骤
  → input_data 必须严格为 {{}}（空对象），不要把 body_template 中的任何字段放入 input_data
  → 仅用户明确说要修改/随机化/动态生成的字段才放入 input_data
- **查询/列表步骤精简**：GET/查询步骤，用户说「传参不变」时，
  → {test_cases_per_step} 条用例全部使用相同的 input_data（{{}}），category 全部为 "positive"
  → 不需要生成 boundary/equivalence/dependency 变体，这些用例会浪费 token 导致截断
- 用户说「xxx来源于StepN的yyy」 → 不要放入 input_data，改用 data_dependencies 引用
- 用户没提到的字段 → 不要放入 input_data，保持 Body 默认值

## 步骤间数据传递
- 用 data_dependencies 字段，占位符格式：{{{{stepN.response.路径}}}}
- 自然语言 → 占位符翻译示例：
  「sparePartId来源于step1随机获取的id」
    → 若 data 为数组：[...] → data_dependencies.body 中用 {{{{step1.response.data[0].id}}}}
    → 若 data 为对象：{{records:[...]}} → data_dependencies.body 中用 {{{{step1.response.data.records[0].id}}}}
  「取 Step2 返回的 data.id」→ {{{{step2.response.data.id}}}}
- **数组索引支持**：
  * 指定位置：{{{{step1.response.data[2].id}}}} → 获取第 3 个元素的 id（索引从 0 开始）
  * 随机选取：{{{{step1.response.data.random.id}}}} → 每次随机选取一个元素的 id
  * 获取长度：{{{{step1.response.data._count}}}} → 获取数组元素个数
  * 用户说「随机获取」时请使用 random；说「第N个」时请使用 [N-1]

## 动态数据规则
- 仅 POST/PUT 步骤需要追加 {{{{timestamp}}}} 保证唯一性（如 "刀具名称_{{{{timestamp}}}}"）
- **重要**：只有名称/编码类需要唯一性的字段才追加 {{{{timestamp}}}}，静态描述字段（如 description）不要追加
- GET/查询步骤不需要时间戳
- 纯数字、布尔值、空字符串不需要追加
- **数据多样性**：仅 POST/PUT 写操作步骤需要多样性！
  * 查询步骤（GET/传参不变）：{test_cases_per_step} 条用例 input_data 全部相同（{{}} 或相同值），不产生变体
  * 写操作步骤：可变字段每条不同（数值: 1/50/100/500/999，文本: "正常磨损"/"定期维护"/"突发故障"）
- **JSON 精简**：每条用例省略空字符串("")、空数组([])、空对象({{}})，只输出有实际内容的字段

{boundary_section}
## 其他要求
- {count_hint}
- **重要：每个步骤都要生成恰好 {test_cases_per_step} 条不同的用例！后续步骤也是 {test_cases_per_step} 条！不要只给 Step2+ 生成 1 条！**
- 每个步骤在 test_cases 同级标注 "output_reference"（如 "data[0].id"）
- expected_status_code 一律 200
- 正向断言: str(resp_json['code']) == '0'（基本断言，不要强制追加 data 校验）
- 反向/异常断言: str(resp_json['code']) != '0'（**必须用 str() 包裹，兼容 code 为整数的情况**）
- 断言必须用 str() 包裹 code，兼容 code 为字符串或整数的情况
- data 字段是可选的不一定存在！只有用户明确要求校验返回值时才追加 data 断言
- 断言中可用 isinstance/list/dict 判断 data 类型，也可用 _as_dict() 防御非 dict 类型
{chr(10) + scope_rule if scope_rule else ""}
只输出 JSON，不要 Markdown 或额外文字。"""


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
