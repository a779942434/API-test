@echo off
chcp 65001 >nul
REM API 测试工作台 - 局域网共享启动脚本 (Windows)

cd /d "%~dp0"

REM 自动获取本机局域网 IPv4 地址
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    set LAN_IP=%%a
    goto :found_ip
)
:found_ip
set LAN_IP=%LAN_IP: =%

echo ==========================================
echo   API 测试工作台
echo   本机访问:   http://localhost:8501
echo   局域网访问: http://%LAN_IP%:8501
echo ==========================================
echo.
echo   其他人浏览器输入上方「局域网访问」地址即可
echo   (Windows 若无法访问: 控制面板 → Windows Defender 防火墙 → 允许应用通过防火墙)
echo.

.venv\Scripts\streamlit.exe run api_test_workbench\app.py
pause
