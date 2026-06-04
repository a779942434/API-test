#!/bin/bash
# API 测试工作台 - 局域网共享启动脚本 (macOS/Linux)

cd "$(dirname "$0")"

# 自动获取本机局域网 IP
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "你的IP")

echo "=========================================="
echo "  API 测试工作台"
echo "  本机访问:   http://localhost:8501"
echo "  局域网访问: http://${LAN_IP}:8501"
echo "=========================================="
echo ""
echo "  其他人浏览器输入上方「局域网访问」地址即可"
echo "  (macOS 若无法访问: 系统设置 → 网络 → 防火墙 → 关闭)"
echo ""

.venv/bin/streamlit run api_test_workbench/app.py
