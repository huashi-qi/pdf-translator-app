@echo off
rem PDF Translator - fallback launcher (sidebar edition)
rem The .bat always flashes a console window; prefer 启动.vbs
setlocal
set "HERE=%~dp0"
set "PYW=%HERE%runtime\pythonw.exe"
if not exist "%PYW%" goto :err
start "" /D "%HERE%" "%PYW%" "%HERE%app_sidebar.py"
exit /b 0
:err
echo [ERROR] runtime not found: %PYW%
pause
exit /b 1
