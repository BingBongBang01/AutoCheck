@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
for /f "delims=" %%I in ('py -3.12 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" (
 echo [오류] Python 3.12를 찾지 못했습니다.
 pause
 exit /b 1
)
"%PYTHON_EXE%" "%~dp0gui.py"
if errorlevel 1 pause
