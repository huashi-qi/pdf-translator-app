@echo off
rem PDF 翻译助手 —— 备用启动器（若 .vbs 被安全软件拦截时用）
rem 说明：.bat 必然先开一个 cmd 控制台，可能看到黑框一闪；
rem 想完全无闪烁请双击「启动.vbs」。
setlocal
set "HERE=%~dp0"
set "PYW=%HERE%runtime\pythonw.exe"
set "PY=%HERE%runtime\python.exe"

if not exist "%PY%" goto :nopython
if not exist "%HERE%app_sidebar.py" goto :noapp

start "" /D "%HERE%" "%PYW%" "%HERE%app_sidebar.py"
exit /b 0

:nopython
echo  [ERROR] Bundled runtime not found: %PY%
pause
exit /b 1

:noapp
echo  [ERROR] app_sidebar.py not found next to this file.
pause
exit /b 1
