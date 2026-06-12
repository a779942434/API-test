"""API 测试工作台 — 共享工具函数"""

import re

# 查询类 URL 关键词（这些接口不需要造数据）
_QUERY_KEYWORDS = ['search', 'page', 'list', 'query', 'find', 'get', 'option', 'select']

# {{xxx}} 占位符正则（用于剥离 AI 残留的占位符语法）
_PLACEHOLDER_RE = re.compile(r'\{\{(.+?)\}\}')


def is_write_step(step) -> bool:
    """判断步骤是否为写操作（POST/PUT/PATCH，且非查询类 URL）"""
    method = (step.config.method if hasattr(step, 'config') else getattr(step, 'method', 'GET')).upper()
    if method not in ('POST', 'PUT', 'PATCH'):
        return False
    url = step.config.url if hasattr(step, 'config') else getattr(step, 'url', '')
    return not any(kw in url.lower() for kw in _QUERY_KEYWORDS)


def is_query_url(url: str) -> bool:
    """判断 URL 是否为查询/搜索类操作"""
    return any(kw in url.lower() for kw in _QUERY_KEYWORDS)


def strip_placeholders(text: str) -> str:
    """剥离 AI 残留的 {{xxx}} 占位符语法"""
    return _PLACEHOLDER_RE.sub(r'\1', text)
