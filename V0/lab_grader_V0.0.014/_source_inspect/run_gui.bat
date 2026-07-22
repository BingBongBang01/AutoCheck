@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE="
for /f "delims=" %%I in ('py -3.12 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%I"
if not defined PYTHON_EXE set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" (
 echo [오류] 실제 Python 3.12를 찾지 못했습니다.
 pause
 exit /b 1
)
echo 사용 Python: %PYTHON_EXE%
"%PYTHON_EXE%" -c "import pymupdf" >nul 2>&1
if errorlevel 1 (
 echo PyMuPDF가 없어 requirements를 먼저 설치합니다.
 "%PYTHON_EXE%" -m pip install -r requirements.txt
 if errorlevel 1 pause & exit /b 1
)
"%PYTHON_EXE%" "%~dp0gui.py"
if errorlevel 1 pause
