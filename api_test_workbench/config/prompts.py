"""Claude API Prompt 模板 — 复用 CLAUDE.md 中的测试用例生成规则"""

import json

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
      "expected_status_code": 200|201|400|401|403|404|409|500,
      "expected_response_keys": ["string"],
      "assertion_logic": "string",
      "pre_condition": "string",
      "post_condition": "string"
    }
  ]
}

API 响应约定（重要）：
- 所有接口统一返回 HTTP 200
- 业务成功：resp.json()['code'] == '0'
- 业务失败：resp.json()['code'] != '0'
- 因此 expected_status_code 对所有用例都填 200
- assertion_logic 通过 code 字段判断业务结果：
  - 正向用例：resp.json()['code'] == '0'
  - 反向用例（参数校验/唯一性/不存在等）：resp.json()['code'] != '0'

生成规则：
1. 覆盖完整 CRUD + 列表查询（含分页、过滤、排序、模糊搜索）
2. 对每个字段应用：等价类（有效值、无效类型、空值/Null/undefined、特殊字符、超长/超短）、边界值（min, min-1, min+1, max, max-1, max+1）、枚举值（合法枚举、非法枚举、大小写敏感）
3. 包含业务规则校验：唯一性、必填项、格式（邮箱/手机号/日期）、跨字段逻辑
4. 测试数据必须真实可用，符合字段类型与约束，避免纯占位符
5. 每个用例的 assertion_logic 必须具体到字段级（如：resp.json()['code'] == '0'）
6. 若字段无明确约束，按行业通用规范补充（字符串默认 1-255，数字默认 0-999999）
7. 用例数量控制在 15-25 条
8. 仅输出 JSON，确保可被 json.loads() 直接解析"""


# ==================== Pipeline 模式 Prompt ====================

PIPELINE_SYSTEM_PROMPT = SYSTEM_PROMPT + """

## Pipeline 模式（多步骤 API 链路测试）

你现在为多步骤 API Pipeline 生成测试用例。每个步骤是独立的 API 接口，前一步的输出是后一步的输入。

输出结构（Pipeline 模式）：
{
  "pipeline_name": "string",
  "steps": [
    {
      "step_name": "string",
      "step_index": 0,
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
      "output_reference": "data.id"
    }
  ]
}

数据链路规则（重要 — 用户用自然语言描述，你需要翻译成占位符）：
1. **占位符格式**：{{step1.response.data.id}} 表示 Step1 响应中的 data.id 字段（步骤编号从 1 开始）
2. 占位符可用在 URL、Body JSON、Headers 的任意位置
3. 为每个步骤标注 output_reference：标识该步骤响应中哪个字段会传给下游
4. 后续步骤通过 data_dependencies 声明依赖的数据来源

**自然语言 → 占位符翻译规则**：
用户会用自然语言描述数据依赖，你需要自动识别并翻译成占位符，常见模式：
- 「取 Step1 返回的 data.id」 → {{step1.response.data.id}}
- 「用 Step1 返回数据里 data.records[0].id」 → {{step1.response.data.records[0].id}}
- 「获取 step1 返回数据里 data[0].id 或 data.records[0].id 中的随机位置id」
  → 选其中一个确定路径，如 {{step1.response.data.records[0].id}}
- 「Step1 响应中的 data.items[0].name 拼接到 URL」 → URL 中用占位符
- 「id 来自 Step1 的 data.id」 → body 中用 {{step1.response.data.id}}

关键：用户写的自然语言描述就是标准的 JSON 路径表示法（data.id / data.records[0].id），
直接加上 stepN.response. 前缀和 {{{{ }}}} 包裹即可，不需要猜测。

5. Pipeline 模式聚焦核心数据链路，每个步骤只生成 1 条正向用例（如用户指定了更
   多数量，则按指定数量生成）。不要生成多余的边界值/异常值测试用例。
6. 测试数据要真实可用，第一步的 input_data 用具体数据填充（写死），后续步骤通过
   data_dependencies 引用上游数据，input_data 留空 {}
7. 生成的每条用例的 assertion_logic 应验证 code == '0'（正向）或 code != '0'（反向）
8. 如果用户描述的链路中某个步骤只需要"查询"或"写死参数即可"，则只生成 1 条
   正向验证用例，不要画蛇添足

## input_data 与 body_template 合并规则（重要）
- 执行时采用合并策略：最终请求体 = {{**body_template, **input_data}}
- **input_data 中的字段会覆盖 body_template 中同名字段**
- 因此，如果用户说某字段「保持不变」「不变」「沿用模板」，该字段**不要**出现在 input_data 中，让 body_template 的值原样生效
- 只有需要**改变值**或**随机生成**的字段，才放到 input_data 中
- 如果用户说「其余不变」，只把需要变更的字段放入 input_data，不要画蛇添足"""


def build_pipeline_user_prompt(
    pipeline_description: str,
    step_descriptions: list[str],
    steps: list,
    test_cases_per_step: int = 1,
) -> str:
    """构造 Pipeline 模式的 User Prompt"""

    steps_block_parts = []
    for i, desc in enumerate(step_descriptions):
        parts = [f"  Step {i+1}：{desc}"]
        # 附上当前 body_template，让 AI 知道哪些字段已有值
        if i < len(steps):
            bt = steps[i].config.body_template if hasattr(steps[i], 'config') else {}
            if bt:
                parts.append(f"     Body 模板（已有值，input_data 不要重复这些不需要变的字段）：{json.dumps(bt, ensure_ascii=False)}")
        steps_block_parts.append("\n".join(parts))
    steps_block = "\n".join(steps_block_parts)

    if test_cases_per_step <= 1:
        count_hint = f"每个步骤生成恰好 {test_cases_per_step} 条正向核心用例"
        extra_rules = "6. 不要生成边界值、异常值、空值等测试用例，只聚焦核心数据链路"
    elif test_cases_per_step <= 5:
        count_hint = f"每个步骤生成恰好 {test_cases_per_step} 条测试用例，覆盖：正向、边界值、必填项为空"
        extra_rules = """6. 用例类型分配建议：
   - 1 条正向用例（正确数据）
   - 1-2 条边界值用例（min/max、min-1/max+1）
   - 剩余覆盖必填项缺失、类型错误等异常场景"""
    else:
        count_hint = f"每个步骤生成恰好 {test_cases_per_step} 条测试用例，全面覆盖：正向、等价类、边界值、异常、跨字段依赖"
        extra_rules = """6. 用例类型全面覆盖：
   - 正向：正确数据，验证正常业务
   - 等价类：不同有效值组合
   - 边界值：min-1, min, min+1, max-1, max, max+1
   - 异常：空值/null、类型错误、超长字符串、特殊字符、SQL注入测试
   - 依赖：跨字段逻辑校验（如 endTime > startTime）、唯一性校验
   每个字段至少覆盖 2-3 种场景"""

    return f"""请根据以下 API Pipeline 描述生成多步骤测试用例。

Pipeline 整体流程：
{pipeline_description}

步骤定义：
{steps_block}

生成要求：
1. {count_hint}
2. 第一步的 input_data 用具体数据填充（写死即可），后续步骤的 input_data 留空 {{}}
3. 识别步骤间的数据依赖，在 data_dependencies 中用 {{{{stepN.response.path}}}} 格式引用上游数据
4. 每个步骤标注 output_reference 字段
5. 所有 expected_status_code 填 200，正向用例 assertion_logic 填 resp_json['code'] == '0'，反向用例填 resp_json['code'] != '0'
{extra_rules}
7. input_data 只放需要覆盖/变更的字段，Body 模板中已有的值不要重复

请直接输出 JSON，不要包含任何 Markdown 标记或额外文字。"""


def build_user_prompt(field_requirements: str, api_url: str = "", method: str = "POST") -> str:
    """根据用户输入的字段定义构造 User Prompt"""
    return f"""请根据以下接口字段定义生成测试用例：

接口地址：{api_url}
请求方法：{method}

字段定义：
{field_requirements}

请直接输出 JSON，不要包含任何 Markdown 标记或额外文字。"""
