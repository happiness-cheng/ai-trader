@echo off
chcp 65001 >nul
echo ========================================
echo   AI Trader - 同花顺模拟盘自动交易
echo ========================================
echo.

set PYTHON=D:\Users\陈独秀\AppData\Local\Programs\Python\Python314\python.exe
set PROJECT=%~dp0

REM 检查同花顺是否运行
tasklist /FI "IMAGENAME eq hexin.exe" 2>nul | find /I "hexin.exe" >nul
if %errorlevel% neq 0 (
    echo [提示] 同花顺未运行，正在启动...
    start "" "D:\同花顺\同花顺\hexin.exe"
    echo 等待同花顺启动（15秒）...
    timeout /t 15 /nobreak >nul
)

REM 检查交易面板
echo [提示] 请确保同花顺交易面板已打开（按F12）
echo 如果已打开，按任意键继续...
pause >nul

echo.
echo 启动 Web 仪表盘...
start "AI Trader Dashboard" %PYTHON% %PROJECT%dashboard.py

echo 等待仪表盘启动（3秒）...
timeout /t 3 /nobreak >nul

echo.
echo 仪表盘已启动: http://127.0.0.1:8501
echo.
echo 启动交易调度引擎...
echo （盘前分析 → 盘中监控 → 盘后复盘）
echo.

%PYTHON% %PROJECT%main.py

echo.
echo AI Trader 已退出
pause
