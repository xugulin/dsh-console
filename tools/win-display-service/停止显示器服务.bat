@echo off
rem 停止 DSH 显示器服务。
rem
rem 按 **8099 端口**精确找进程，不用 pkill -f 那种模糊匹配 ——
rem 模糊匹配会误杀同名的编辑器/终端（Linux 那边为此专门写过 dsh-display-reset.sh）。
chcp 936 >nul 2>nul
setlocal
set "FOUND="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:"TCP.*:8099 .*LISTENING"') do (
  taskkill /pid %%P /f >nul 2>nul
  if not errorlevel 1 (
    echo 已结束显示器服务进程 pid=%%P
    set "FOUND=1"
  )
)
if not defined FOUND echo 没有在运行的显示器服务（8099 空闲）。
if not defined DSH_NO_PAUSE pause
exit /b 0
