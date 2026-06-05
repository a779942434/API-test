# API 测试工作台 & Python 自动化测试项目

## 项目概述

Python 接口自动化测试项目，包含两部分：
- **API 测试工作台**（Streamlit 可视化平台）：支持多步骤 Pipeline 链路测试、curl 命令解析、AI 自动生成测试数据、结果可视化
- **pytest 测试脚本**：传统的 pytest + requests 接口自动化测试

## 环境要求

- Python 3.9+
- macOS / Linux / Windows

## 快速开始

```bash
cd PythonProject_test

# 创建虚拟环境并安装依赖
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r api_test_workbench/requirements.txt

# 启动 API 测试工作台
./run_workbench.sh          # macOS/Linux
run_workbench.bat           # Windows
```

启动后访问 `http://localhost:8501`，局域网内其他人通过 `http://你的IP:8501` 访问。

## 项目结构

```
PythonProject_test/
├── api_test_workbench/          # Streamlit API 测试工作台
│   ├── app.py                   # 主界面（Pipeline 配置、字段定义、认证执行）
│   ├── requirements.txt         # 工作台依赖（streamlit、anthropic）
│   ├── config/
│   │   └── prompts.py           # AI 提示词模板（测试用例生成规则）
│   └── engine/
│       ├── models.py            # 数据模型（Pipeline/ApiStep/TestCase）
│       ├── runner.py            # 测试执行引擎（Pipeline + 断言验证）
│       ├── generator.py         # AI 测试用例生成（DeepSeek/Anthropic）
│       ├── bindings.py          # 占位符解析（{{stepN.response.path}}）
│       ├── curl_parser.py       # curl 命令解析（粘贴即用）
│       ├── session_store.py     # 状态持久化（保存/加载/自动清理）
│       └── logger.py            # 日志模块
├── tests/                       # pytest 测试用例
│   ├── test_login.py            # 登录接口测试
│   ├── test_maintenance_content_definition.py
│   ├── test_problem_definition.py
│   └── test_spare_part_device_definition.py
├── pages/                       # Playwright Page Object
│   ├── login_page.py
│   └── problem_definition_page.py
├── conftest.py                  # pytest 全局 fixture
├── pytest.ini                   # pytest 配置
├── .streamlit/config.toml       # Streamlit 主题配置
├── .gitignore
├── run_workbench.sh             # 启动脚本 (macOS/Linux)
├── run_workbench.bat            # 启动脚本 (Windows)
├── CLAUDE.md                    # Claude Code 项目指引
└── README.md                    # 本文件
```

## API 测试工作台使用说明

### ① Pipeline 配置

多步骤 API 链路测试的核心区域。每个步骤代表一个接口调用。

- **步骤名称**：自定义，便于识别
- **Method**：POST / GET / PUT / DELETE
- **失败策略**：`stop` 失败停止后续 / `continue` 忽略错误继续
- **忽略**：勾选后该步骤在链路中跳过，数据继续向下传递
- **接口地址**：直接填写 URL
- **curl 粘贴**：从浏览器 DevTools 复制 curl 命令，粘贴后点击「解析」自动填充 Method/URL/Headers/Body
- **Headers / Body 模板**：JSON 格式，支持 `{{stepN.response.path}}` 占位符引用上游数据

**操作按钮**：⬆ 上移 / ⬇ 下移 / 🗑 删除 / + 添加步骤

**数据链路概览**：自动扫描步骤间的占位符依赖，展示数据流转关系。

### ② 字段定义 & 测试数据生成

用**自然语言**描述每个步骤的字段约束和步骤间的数据依赖，AI 自动生成测试用例。

**基本写法**：
```
Step 1（创建刀具）：
- articleName: string, 必填, 唯一
- articleNumber: string, 必填, 唯一
- isStandard: 1 或 2

Step 2（查询）：
- 用 Step1 返回的 data.records[0].id 作为查询条件
```

**范围控制**（在第一行声明）：
- `只需正常数据，不需要边界测试` → 只生成正向真实数据
- 不写默认 → 完整测试覆盖（正向/边界/异常/等价类）

**每步用例数**：1=核心链路 | 3-5=含边界 | 10+=完整覆盖

点击「生成测试数据」→ AI 自动生成用例 → 可在下方表格中编辑。

### ③ 认证 & 执行

- 填写**登录接口地址**和**登录 Body**
- 点击「获取 Session」→ 登录成功后 Session 已就绪
- 点击「执行 Pipeline」→ 按链路顺序执行所有测试用例

> 注意：curl 中的 Cookie 会被自动剔除，认证统一由登录按钮管理。

### ④ 结果查看

Pipeline 执行完成后展示结果，支持两种查看方式：
- **📋 按步骤**：每个步骤一个 Tab
- **🔗 按链路**：每条完整链路一个 Tab（Step1→Step2→...→StepN）

### 💾 保存 / 📂 加载

标题栏右侧按钮：
- 保存：当前 Pipeline 配置 + 字段定义 + 测试用例 + 登录信息 → 存到 `~/.api_workbench_saves/`
- 加载：选择存档恢复全部状态
- 超过 3 天的存档自动清理

### 日志

工作台运行时自动记录日志到 `workbench.log`（项目根目录），包含每个步骤的请求/响应/错误。

## API 约定

- 所有接口统一返回 HTTP 200
- 业务成功：`resp.json()['code'] == '0'`
- 业务失败：`resp.json()['code'] != '0'`

## 常用命令

```bash
# 运行所有 pytest 测试
.venv/bin/python -m pytest

# 按关键字运行
.venv/bin/python -m pytest -k "login"

# 启动工作台
./run_workbench.sh

# 查看日志
tail -f workbench.log
grep ERROR workbench.log
```

## 维护说明

### 配置文件

| 文件 | 说明 |
|------|------|
| `.streamlit/config.toml` | Streamlit 主题（暗色开发者工具风）和服务器配置 |
| `pytest.ini` | pytest 运行配置 |
| `.gitignore` | 忽略 .env、workbench.log、.venv 等 |

### 环境变量

AI 测试用例生成功能需要 API Key，通过以下方式配置（优先级从高到低）：
1. 环境变量：`DEEPSEEK_API_KEY` 或 `ANTHROPIC_API_KEY`
2. `~/.claude/settings.json` 中的 `env.ANTHROPIC_AUTH_TOKEN`
3. `~/.claude/credentials.json` 中的 `apiKey` 或 `anthropicApiKey`

### 依赖

```
# 工作台
streamlit>=1.28
anthropic>=0.39.0
requests
pydantic

# pytest 测试
pytest>=7.0
requests>=2.28
pytest-playwright
playwright
```

### 启动脚本

- `run_workbench.sh` — macOS/Linux，会自动检测本机 IP 并显示局域网访问地址
- `run_workbench.bat` — Windows 版本

## 技术架构

```
用户操作 → app.py (Streamlit UI)
              ├── curl_parser.py   ← 粘贴 curl 命令
              ├── generator.py     ← AI 生成测试用例
              ├── runner.py        ← 执行 Pipeline + 断言
              │     ├── bindings.py   ← {{stepN.response.path}} 解析
              │     └── models.py     ← 数据模型
              ├── session_store.py ← 状态持久化
              └── logger.py        ← 日志记录
```
