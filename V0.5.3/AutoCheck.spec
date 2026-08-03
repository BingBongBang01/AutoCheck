# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Hidden imports for Netmiko, PyWebview, Pandas, Openpyxl, ReportLab, pptx and app modules
hidden_imports = [
    'pywebview',
    'clr',
    'pythonnet',
    'netmiko',
    'netmiko.cisco',
    'netmiko.arista',
    'netmiko.ssh_autodetect',
    'netmiko.base_connection',
    'pandas',
    'openpyxl',
    'pptx',
    'reportlab',
    'yaml',
    'jinja2',
]

# Include submodules for key packages
for pkg in ['api', 'engine', 'pipeline', 'parsers', 'plugins', 'core', 'alarm', 'report', 'rule_engine', 'ai_analysis']:
    try:
        hidden_imports += collect_submodules(pkg)
    except Exception:
        pass

# Data files to bundle into EXE
datas = [
    ('web_ui', 'web_ui'),
    ('config', 'config'),
    ('labs', 'labs'),
    ('VERSION', '.'),
]

for optional_file in ['ai_config.yaml', 'connection.yaml', 'ai_settings.yaml', 'requirements.txt', '실행방법.txt']:
    if os.path.exists(optional_file):
        datas.append((optional_file, '.'))

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
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='web_ui/icons/app_icon.ico' if os.path.exists('web_ui/icons/app_icon.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoCheck',
)
