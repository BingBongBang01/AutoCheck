# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Hidden imports for Netmiko, PyWebview, Pandas, Openpyxl, ReportLab and app modules
# 'pywebview'는 PyPI 배포명일 뿐 importable 모듈명이 아니다(실제 모듈은 'webview') — 이 이름
# 그대로 hiddenimports에 넣으면 PyInstaller가 "Hidden import 'pywebview' not found"를 낸다.
# main.py가 `import webview`를 직접 하므로 정적 분석이 어차피 잡아 주지만(빌드 자체는 성공),
# 에러 로그가 실제 문제와 헷갈리므로 올바른 모듈명으로 고쳐 둔다.
hidden_imports = [
    'webview',
    'clr',
    'pythonnet',
    'netmiko',
    'netmiko.cisco',
    'netmiko.arista',
    'netmiko.ssh_autodetect',
    'netmiko.base_connection',
    'pandas',
    'openpyxl',
    'reportlab',
    'yaml',
]

# Include submodules for key packages
for pkg in ['api', 'engine', 'parsers', 'core', 'report', 'ai_analysis']:
    try:
        hidden_imports += collect_submodules(pkg)
    except Exception:
        pass

# Data files to bundle into EXE
# 번들한 설정 파일은 첫 실행 때 사용자 폴더로 복사된다(core/paths.py _init_user_data_templates).
# 그래서 개발 PC 의 비밀이 섞일 수 있는 것은 담지 않는다:
#   - ai_config.yaml: 클라우드 AI API 키가 들어 있다. 없으면 앱은 빈 설정으로 시작하고 설정 화면이 만든다.
#   - labs/: 예전 레포의 프로젝트 폴더(장비 계정 포함). 앱은 번들의 labs 를 읽지 않는다
#     (프로젝트는 사용자 문서 폴더에 런타임에 생긴다).
#   - requirements.txt: 소스 실행용이다. EXE 사용자 안내(실행방법.txt)는 build_exe.py 가 번들 안이 아니라
#     dist/AutoCheck/ 의 AutoCheck.exe 옆에 복사한다.
datas = [
    ('web_ui', 'web_ui'),
    ('config', 'config'),
    ('VERSION', '.'),
]
for optional_file in ['connection.yaml', 'ai_settings.yaml']:
    if os.path.exists(optional_file):
        datas.append((optional_file, '.'))

# 담기 전에 비밀(API 키 모양, 값이 든 api_key/password 필드, ai_config.yaml 같은 이름)을 검사하고
# 하나라도 있으면 빌드를 멈춘다 — tools/bundle_secret_check.py.
sys.path.insert(0, SPECPATH)
from tools.bundle_secret_check import find_bundle_secrets
_bundle_problems = find_bundle_secrets([src for src, _dest in datas])
if _bundle_problems:
    raise SystemExit("번들에 비밀로 보이는 내용이 있어 빌드를 멈춥니다:\n  " + "\n  ".join(_bundle_problems))

# Exclude unnecessary heavy packages to minimize build size
excludes = [
    'tkinter',
    'matplotlib',
    'IPython',
    'pytest',
    'unittest',
    'notebook',
    'scipy',
]

# AutoCheck.exe 속성(자세히)에 보이는 제품 정보 — 버전은 VERSION 파일에서 읽는다(올릴 때 한 곳만 고친다).
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)
with open(os.path.join(SPECPATH, 'VERSION'), encoding='utf-8') as _version_file:
    _version = _version_file.read().strip()
_version_numbers = tuple((list(map(int, _version.split('.'))) + [0, 0, 0, 0])[:4])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_version_numbers, prodvers=_version_numbers),
    kids=[
        StringFileInfo([StringTable('041204B0', [          # 한국어(0x0412) · 유니코드(1200)
            StringStruct('CompanyName', 'TaeHyeong.Kim'),
            StringStruct('FileDescription', 'AutoCheck - 네트워크 장비 정기점검 시스템'),
            StringStruct('FileVersion', _version),
            StringStruct('InternalName', 'AutoCheck'),
            StringStruct('LegalCopyright', 'Copyright (c) 2026 TaeHyeong.Kim. All Rights Reserved.'),
            StringStruct('OriginalFilename', 'AutoCheck.exe'),
            StringStruct('ProductName', 'AutoCheck'),
            StringStruct('ProductVersion', _version),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0412, 1200])]),
    ],
)

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoCheck',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,   # UPX 로 압축한 실행 파일은 사내 백신이 오탐하는 일이 잦다 — 크기보다 배포 안정성
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='web_ui/icons/app_icon.ico' if os.path.exists('web_ui/icons/app_icon.ico') else None,
    version=version_info,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AutoCheck',
)
