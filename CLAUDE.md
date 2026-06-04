# CLAUDE.md

## 项目概述

Python 接口自动化测试项目，包含 pytest + requests 测试脚本和 Streamlit API 测试工作台。

## 常用命令

```bash
# 运行所有测试
.venv/bin/python -m pytest

# 按关键字匹配运行
.venv/bin/python -m pytest -k "login"

# 启动 API 测试工作台（局域网可访问）
./run_workbench.sh          # macOS/Linux
run_workbench.bat           # Windows

# 安装依赖
.venv/bin/pip install -r requirements.txt
```

## 项目结构

- `conftest.py` — pytest 全局 fixture：`base_url`、`session`、`credentials`、`login_session`
- `tests/` — pytest 测试用例，文件命名 `test_*.py`
- `pages/` — Playwright Page Object 模式 UI 测试页面
- `api_test_workbench/` — Streamlit API 测试工作台
  - `app.py` — 主界面（Pipeline 配置、字段定义、认证执行）
  - `engine/` — 测试引擎（curl 解析、AI 生成、用例执行、数据绑定）
  - `config/prompts.py` — AI 提示词模板
- `pytest.ini` — pytest 配置
- `.streamlit/config.toml` — Streamlit 主题配置

## 编写测试约定

- 测试类名 `Test` 开头，方法名 `test_` 开头，中文描述写在 docstring 中
- 使用 `conftest.py` fixture 获取 `base_url`、`session`、`credentials`，不硬编码
- 断言失败时附带描述信息
- API 响应约定：HTTP 200，业务成功 `code == '0'`，失败 `code != '0'`
