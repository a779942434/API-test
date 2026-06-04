"""从 curl 命令字符串中提取 HTTP 请求配置。纯函数，零依赖（仅用 stdlib shlex）。"""

import re
import shlex


def parse_curl(curl_str: str) -> dict:
    """解析 curl 命令，返回 method / url / headers / body。

    Returns:
        {"method": "POST", "url": "https://...", "headers": {...}, "body": "..." | None}

    Raises:
        ValueError: 输入不是合法 curl 命令、缺少 URL、shlex 解析失败。
    """
    if not curl_str or not curl_str.strip():
        raise ValueError("命令不能为空")

    # ── 预处理：去除开头空白 ──
    s = curl_str.strip()

    # ── 去掉反斜杠续行符（\ 后跟换行），合并为一行 ──
    s = re.sub(r'\\\s*\n\s*', ' ', s)

    # ── 校验 curl 前缀 ──
    if not re.match(r'(^|.*[/\s])curl\b', s):
        raise ValueError("命令必须以 'curl' 开头，例如：curl -X POST https://...")

    # ── 去掉 'curl' 及之前的内容（如路径 /usr/bin/curl） ──
    s = re.sub(r'^.*(?<!/)\bcurl\b\s*', '', s, count=1)

    # ── 词法分析 ──
    try:
        tokens = shlex.split(s)
    except ValueError as e:
        raise ValueError(f"curl 命令引号不匹配，无法解析：{e}")

    if not tokens:
        raise ValueError("curl 命令中没有可解析的参数")

    # ── 状态机遍历 ──
    method = "GET"
    url = None
    headers = {}
    body = None

    i = 0
    while i < len(tokens):
        t = tokens[i]

        if t in ('-X', '--request'):
            i += 1
            if i >= len(tokens):
                raise ValueError(f"{t} 缺少 HTTP 方法参数")
            method = tokens[i].upper()

        elif t in ('-H', '--header'):
            i += 1
            if i >= len(tokens):
                raise ValueError(f"{t} 缺少 Header 参数")
            k, _, v = tokens[i].partition(':')
            headers[k.strip()] = v.strip()

        elif t in ('-d', '--data', '--data-raw', '--data-binary'):
            i += 1
            if i >= len(tokens):
                raise ValueError(f"{t} 缺少 Body 参数")
            body = tokens[i]

        elif t.startswith('-X') and len(t) > 2:
            method = t[2:].upper()

        elif t.startswith('--request='):
            method = t[len('--request='):].upper()

        elif t.startswith('--header='):
            rest = t[len('--header='):]
            k, _, v = rest.partition(':')
            headers[k.strip()] = v.strip()

        elif t.startswith('--data='):
            body = t[len('--data='):]

        elif t.startswith('--data-raw='):
            body = t[len('--data-raw='):]

        elif t.startswith('-'):
            # 未知 flag（-L, -k, -v, -s, --location 等），跳过不报错
            pass

        elif url is None:
            url = t

        i += 1

    # ── 方法推断：有 body 但无 -X 时默认为 POST ──
    if method == "GET" and body is not None:
        method = "POST"

    # ── 校验 ──
    if url is None:
        raise ValueError("未在 curl 命令中找到 URL")

    return {
        "method": method,
        "url": url,
        "headers": headers,
        "body": body,
    }
