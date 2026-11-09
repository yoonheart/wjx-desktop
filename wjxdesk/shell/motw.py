"""清除 Windows「来自网络」标记（Mark of the Web）。

从浏览器下载的 zip，经资源管理器「全部解压缩」后，每个文件都会带上
``文件名:Zone.Identifier`` 备用数据流。pythonnet 用 ``Assembly.LoadFrom``
加载 ``Python.Runtime.dll`` 时，.NET 会拒绝这类「远程」程序集
（HRESULT 0x80131515），表现为一启动就崩：

    Failed to resolve Python.Runtime.Loader.Initialize

对策是在 ``import webview`` 之前把这些标记删掉。本模块只依赖标准库，
不导入包内其他模块，保证可以被最早调用、不会引入循环依赖。
"""

from __future__ import annotations

import os
import sys

#: 要处理的扩展名。真正卡住启动的是 dll，exe/pyd 顺带一起清。
SUFFIXES = (".dll", ".exe", ".pyd")

_DELETE_FILE = None


def _delete_file():
    """取 DeleteFileW 并缓存；取不到时抛异常，由调用方兜住。"""
    global _DELETE_FILE
    if _DELETE_FILE is not None:
        return _DELETE_FILE

    import ctypes
    from ctypes import wintypes

    fn = ctypes.windll.kernel32.DeleteFileW
    fn.argtypes = [wintypes.LPCWSTR]
    fn.restype = wintypes.BOOL
    _DELETE_FILE = fn
    return fn


def _drop_stream(delete, path: str) -> bool:
    """删掉 path 的 :Zone.Identifier 流。

    DeleteFileW 对不存在的流只是返回 0，不抛异常；文件被占用时同样返回 0。
    """
    try:
        return bool(delete(path + ":Zone.Identifier"))
    except Exception:
        return False


def clear_network_marks(root: str | os.PathLike[str] | None = None) -> int:
    """递归清除 root 下可执行文件的网络标记，返回清除个数。

    root 默认取 PyInstaller 的解包目录（``sys._MEIPASS``）。源码运行时没有
    这个目录，直接返回 0，不会动到开发机上的文件。
    """
    if sys.platform != "win32":
        return 0

    if root is None:
        root = getattr(sys, "_MEIPASS", None)
        if not root:
            return 0

    base = os.fspath(root)
    if not os.path.isdir(base):
        return 0

    try:
        delete = _delete_file()
    except Exception:
        return 0

    removed = 0
    for dirpath, _dirnames, filenames in os.walk(base):
        for name in filenames:
            if not name.lower().endswith(SUFFIXES):
                continue
            if _drop_stream(delete, os.path.join(dirpath, name)):
                removed += 1
    return removed


def clear_executable_mark() -> bool:
    """清除当前 exe 自身的网络标记，避免以后每次启动都弹 SmartScreen。

    只对打包后的程序生效：源码运行时 ``sys.executable`` 是 python.exe，
    绝不能去动它。
    """
    if sys.platform != "win32":
        return False
    if not getattr(sys, "frozen", False):
        return False

    exe = sys.executable
    if not exe or not os.path.isfile(exe):
        return False

    try:
        delete = _delete_file()
    except Exception:
        return False
    return _drop_stream(delete, exe)
