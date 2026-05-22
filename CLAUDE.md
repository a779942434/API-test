# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Python 自动化测试项目，使用 pytest + requests 框架编写接口自动化测试脚本。

## 常用命令

```bash
# 运行所有测试
.venv/bin/python -m pytest

# 运行单个测试文件
.venv/bin/python -m pytest tests/test_login.py

# 运行单个测试方法
.venv/bin/python -m pytest tests/test_login.py::TestLogin::test_login_success

# 按关键字匹配运行
.venv/bin/python -m pytest -k "login"

# 安装依赖
.venv/bin/pip install -r requirements.txt
```

## 项目结构

- `conftest.py` — 全局 fixture，提供 `base_url`、`session`（requests.Session）、`credentials` 等共享对象
- `tests/` — 测试用例目录，文件命名 `test_*.py`
- `pytest.ini` — pytest 配置文件

## 编写测试约定

- 测试类继承 `object`，类名以 `Test` 开头
- 测试方法名以 `test_` 开头，中文描述放在 docstring 中
- 使用 `conftest.py` 中的 fixture 获取 `base_url`、`session`、`credentials`，不硬编码 URL 和账号
- 断言失败时附带描述信息
