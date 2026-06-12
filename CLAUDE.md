# CLAUDE.md

## 项目概述

Python 接口自动化测试项目，包含 Streamlit API 测试工作台 + pytest 导出引擎。

核心流程：在 Streamlit 工作台中可视化编排 API Pipeline → AI 生成测试用例 → 执行验证 → 导出为标准 pytest 脚本。

## 常用命令

```bash
# 启动 API 测试工作台
./run_workbench.sh          # macOS/Linux
run_workbench.bat           # Windows

# 运行导出的 pytest 测试
.venv/bin/python -m pytest tests/exported/<name>/ -v

# 切换环境运行
.venv/bin/python -m pytest tests/exported/<name>/ -v --env=staging

# 安装依赖
.venv/bin/pip install -r requirements.txt
```

## 项目结构

```
api_test_workbench/
  app.py                       # Streamlit UI 主程序
  config/
    prompts.py                 # AI 提示词模板（SYSTEM_PROMPT + PIPELINE_SYSTEM_PROMPT）
  engine/
    models.py                  # 数据模型 dataclasses（Pipeline, TestCase, ApiConfig...）
    runner.py                  # 测试执行引擎（单接口 + Pipeline 链路）
    generator.py               # AI 测试用例生成（DeepSeek + Anthropic）
    exporter.py                # Pipeline → pytest 代码导出器（核心模块）
    bindings.py                # {{stepN.response.path}} 占位符解析
    curl_parser.py             # curl 命令解析
    environment.py             # 多环境变量管理（dev/staging/prod）
    reporter.py                # HTML/JSON 测试报告导出
    session_store.py           # 工作台状态持久化（~/.api_workbench_saves/）
    logger.py                  # 日志模块
tests/
  exported/                    # 导出的 pytest 脚本（conftest.py + test_*.py）
pytest.ini                     # pytest 配置
requirements.txt               # requests>=2.28, pytest>=7.0
.streamlit/config.toml         # Streamlit 主题配置
```

## Exporter 设计（engine/exporter.py）

### 公开 API
```python
exporter = PytestExporter(pipeline, test_cases_by_step, auth_url, auth_body, env_name="default", client_id="")
exporter.export_to_dir(output_dir)     # → (conftest_path, test_path)
exporter.export_to_zip_bytes()         # → bytes（供 Streamlit 下载）
```

### 生成的 conftest.py 结构
- `ENVIRONMENTS` 字典：集中维护 base_url、auth_endpoint、username、password、headers（clientId/clientType 等）
- 切换环境：复制 block 修改值，`pytest --env=staging`
- `login_session` fixture：自动调用登录接口获取 Cookie
- `api_headers` fixture：从 `env_config["headers"]` 读取，无硬编码

### 生成的 test_*.py 结构
- `Test<Name>` 类，`TS = int(time.time())`
- 每步一个 helper：`_step{N}_url(base_url)` + `_step{N}_base_body()`
- 正向链路：`test_01_xxx`, `test_02_xxx` 顺序方法
- 异常用例：`@pytest.mark.parametrize` 参数化
- 步骤间数据传递：`type(self).step{N}_field = value`

### 关键设计决策
| 决策 | 实现 |
|------|------|
| 凭据维护 | `ENVIRONMENTS` 字典手动编辑，不依赖环境变量 |
| 动态数据 | 仅 POST/PUT 写操作追加 `{self.TS}`，GET/查询跳过 |
| 断言安全 | `str(result.get('code')) == '0'` 兼容 str/int |
| Token 处理 | 自动剥离 JWT，由 `login_session` 动态获取 |
| Teardown | 写操作生成 `# TODO: DELETE` 注释框架 |
| 文件权限 | conftest.py 写入后 `os.chmod(0o600)` |
| 注入防护 | `json.dumps()` 安全嵌入字符串值 |

## Prompts 规范（config/prompts.py）

### AI 生成要求
- **动态数据**：名称/编码字段追加 `{timestamp}`，纯数字/布尔/空值跳过
- **类型安全**：`str(resp_json['code']) == '0'`，`int(resp_json.get('data', {}).get('total', 0)) > 0`
- **Pipeline**：仅 POST/PUT 创建/更新类使用 timestamp，GET/查询/列表不需要
- **正常数据模式**：用户声明「只需正常数据」时禁止边界/异常/SQL注入用例

### 导出规范（生成的 pytest 代码）
1. 数据隔离：`f"value_{self.TS}"` 保证唯一性，查询类跳过
2. Headers 集中：`{**api_headers, "Custom": "val"}` 模式
3. 断言健壮：`str(result.get('code'))` + `int()` 数值转换
4. 凭据安全：ENVIRONMENTS 字典手动维护，无硬编码默认值
5. 数据清理：POST 操作生成 teardown TODO 注释
6. 代码整洁：按需导入 `json`，无未使用的 import/helper

## API 约定

- 所有接口统一返回 HTTP 200，业务结果通过 `code` 字段区分
- `str(result['code']) == '0'` 业务成功，否则失败
- `code` 可能是字符串 "0" 或整数 0，断言必须兼容

## 响应数据流

```
Step1 执行 → _flatten_response() → {"response.code":"0", "response.data.id":123}
  → PipelineContext.extracted_values[0] = {...}
    → Step2 resolve_placeholders("{{step1.response.data.id}}") → "123"
```
