@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo PDF Translater Windows EXE Builder (onefile)
echo ==========================================
echo.

echo [1/7] Locate real Python 3.12
set "PYTHON_EXE="
for /f "usebackq delims=" %%I in (`py -3.12 -c "import sys; print(sys.executable)" 2^>nul`) do set "PYTHON_EXE=%%I"

if not defined PYTHON_EXE (
    echo [ERROR] py launcher could not find Python 3.12.
    echo Installed Python versions:
    py -0p
    pause
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python executable does not exist:
    echo %PYTHON_EXE%
    pause
    exit /b 1
)

echo Using Python: %PYTHON_EXE%
echo.

echo [2/7] Read app version from translate_pdf.py (single source of truth)
set "APP_VERSION="
for /f "usebackq delims=" %%V in (`"%PYTHON_EXE%" _get_version.py 2^>nul`) do set "APP_VERSION=%%V"

if not defined APP_VERSION (
    echo [ERROR] Could not read __version__ from translate_pdf.py.
    pause
    exit /b 1
)

set "APP_NAME=PDF-Translater-v%APP_VERSION%"
echo App version: %APP_VERSION%
echo Build name : %APP_NAME%
echo.

echo [3/7] Install pip packages
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto :error
"%PYTHON_EXE%" -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto :error
"%PYTHON_EXE%" -m pip install --upgrade pyinstaller
if errorlevel 1 goto :error

echo.
echo [4/7] Verify core modules in this Python environment
"%PYTHON_EXE%" -c "import sys, pymupdf, pypdf; print('Python:',sys.executable); print('PyMuPDF:',pymupdf.__file__); print('pypdf:',pypdf.__file__)"
if errorlevel 1 goto :error

echo.
echo [5/7] Clean previous build
if exist build rmdir /s /q build
if exist "dist\%APP_NAME%.exe" del /q "dist\%APP_NAME%.exe"
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

echo.
echo [6/7] PyInstaller build (--onefile: a single exe file)
REM --windowed = no console window at runtime
REM --onefile  = single exe file in dist folder
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
 --name "%APP_NAME%" ^
 --collect-all pymupdf ^
 --collect-all pypdf ^
 --collect-all anthropic ^
 --collect-all google.genai ^
 --collect-all openai ^
 --collect-all numpy ^
 --collect-all cv2 ^
 --hidden-import pymupdf ^
 --hidden-import fitz ^
 --hidden-import translate_pdf ^
 --hidden-import pdf_engine ^
 --hidden-import pdf_engine.config ^
 --hidden-import pdf_engine.segment ^
 --hidden-import pdf_engine.extraction ^
 --hidden-import pdf_engine.batching ^
 --hidden-import pdf_engine.providers_cloud ^
 --hidden-import pdf_engine.providers_local ^
 --hidden-import pdf_engine.scheduler ^
 --hidden-import pdf_engine.rendering ^
 --hidden-import pdf_engine.io_utils ^
 --hidden-import pdf_engine.filenaming ^
 --add-data "translate_pdf.py;." ^
 --add-data "pdf_engine;pdf_engine" ^
 --add-data "requirements.txt;." ^
 --add-data "prompts;prompts" ^
 gui.py
if errorlevel 1 goto :error

echo.
echo [7/7] Verify build result
if not exist "dist\%APP_NAME%.exe" (
    echo [ERROR] EXE was not created.
    goto :error
)

echo.
echo ==========================================
echo Build complete - single exe, no console window
echo dist\%APP_NAME%.exe
echo.
echo Note: this exe can be copied to any folder/PC and still run.
echo       api.txt/config.json are managed separately from the exe.
echo       API keys are stored in %%APPDATA%%\PDFTranslaterGUI\config.json
echo ==========================================
pause
exit /b 0

:error
echo.
echo [ERROR] Build failed. Please check the log above.
pause
exit /b 1
