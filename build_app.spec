# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：one-folder，双击启动。

用法：
    .venv\\Scripts\\pyinstaller.exe build_app.spec --noconfirm
产物：
    dist\\问卷星助手\\问卷星助手.exe
"""

from pathlib import Path

ROOT = Path(SPECPATH)

block_cipher = None

a = Analysis(
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # 前端静态资源整体带上（页面、js、css、图片）
        (str(ROOT / "frontend"), "frontend"),
        # 协议清单随包，避免运行时找不到
        (str(ROOT / "wjxdesk" / "protocol" / "wjx" / "manifest.json"),
         "wjxdesk/protocol/wjx"),
        # 应用图标：窗口图标（branding.py 按 wjxdesk/assets 找）与 exe 图标共用
        (str(ROOT / "wjxdesk" / "assets"), "wjxdesk/assets"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 科学计算库一律不带：运行时代码完全没用到，带上会让包体涨 400MB
    excludes=[
        "numpy", "scipy", "pandas", "statsmodels",
        "matplotlib", "PIL", "PyQt5", "PySide6", "tkinter",
        "pytest", "setuptools", "pip",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="问卷星助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 双击不弹黑框
    disable_windowed_traceback=False,
    icon=str(ROOT / "wjxdesk" / "assets" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="问卷星助手",
)
