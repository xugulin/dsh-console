@echo off
rem 启动 DSH 显示器服务（dsh-display-viewer）—— 便携包启动脚本。
rem
rem 为什么需要它：Linux 上这个服务由 systemd 单元托管（见 tools/systemd/），
rem Windows 上没有对应机制；服务不跑，插件面板就永远停在「显示器还没有打开」。
rem
rem 本文件是 GBK(cp936) 编码，与同目录其它启动脚本一致。
chcp 936 >nul 2>nul
setlocal
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"

rem HOME/USERPROFILE 指到包内 home（与其它启动脚本一致）：
rem 服务把帧缓存写在 ~/.cache/dsh-display，必须和 harness 用同一个 home，
rem 否则包内 home 与真实用户目录各留一份，排查时会对不上。
set "HOME=%HERE%\home"
set "USERPROFILE=%HERE%\home"
set "HOMEDRIVE="
set "HOMEPATH="
set "DSH_HOME=%HERE%\home\.dsh"
set "XDG_CACHE_HOME=%HERE%\home\.cache"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYW=%HERE%\runtime\win\python\pythonw.exe"
if not exist "%PYW%" set "PYW=%HERE%\runtime\win\python\python.exe"
set "VIEWER=%HERE%\app\tools\dsh-display-viewer.py"
rem 刻意不用 if (...) 括号块：路径里若出现 ) （例如重名下载生成的 "DSH-Console (1)"）
rem 会把块提前闭合，行为完全错乱 —— 同目录其它启动脚本也踩过这个坑。
if not exist "%PYW%" goto :missing
if not exist "%VIEWER%" goto :missing

rem 已经在跑就别再起一个：两个进程抢 8099，后起的那个只会报错退出。
netstat -ano | findstr /r /c:"TCP.*:8099 .*LISTENING" >nul 2>nul
if not errorlevel 1 goto :running

if not exist "%HERE%\run" mkdir "%HERE%\run" >nul 2>nul
rem 服务自己写日志（viewer 认 DSH_VIEW_LOG 这个环境变量）：
rem pythonw 没有控制台，不这样的话启动失败一点线索都没有。
set "DSH_VIEW_LOG=%HERE%\run\display-viewer.log"
set "LOG=%DSH_VIEW_LOG%"
echo 正在启动显示器服务…
rem ?? 必须是 start "" 而**不是** start "" /b：
rem /b 起的子进程挂在调用方的控制台/作业上，调用方一被结束（超时、关窗口）
rem 服务就跟着死 —— 实测踩过：脚本报"已启动"，几分钟后 8099 就空了。
rem 去掉 /b 才是真正脱离，和同目录「启动DSH网页界面.bat」一个写法。
start "" "%PYW%" "%VIEWER%"
rem 给它时间绑端口再回报结果（ping 代替 timeout：没有控制台时 timeout 会报错）
ping -n 4 127.0.0.1 >nul 2>nul
netstat -ano | findstr /r /c:"TCP.*:8099 .*LISTENING" >nul 2>nul
if errorlevel 1 goto :failed
echo 显示器服务已启动：http://127.0.0.1:8099/
echo 回到 DSH 网页界面的「显示器」标签即可看到画面。
goto :done

:running
echo 显示器服务已经在运行：http://127.0.0.1:8099/
goto :done

:failed
echo 启动似乎失败了，请看日志：
echo   %LOG%
echo.
echo 前台调试（能看到报错）：
echo   "%HERE%\runtime\win\python\python.exe" "%VIEWER%"
goto :done

:missing
echo.
echo 便携包不完整：没找到运行时或显示器服务脚本。
echo   期望 python：%PYW%
echo   期望服务：  %VIEWER%
echo.
echo 若缺 python，请先双击「Install-Windows-Runtime.bat」。
echo.

:done
if not defined DSH_NO_PAUSE pause
exit /b 0
