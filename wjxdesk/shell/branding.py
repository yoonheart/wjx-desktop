"""品牌外观：应用窗口图标与任务栏归属。

pywebview 的 `create_window` 没有图标参数，Windows 上只能等窗口真正创建出来，
再用 Win32 消息把图标补上——否则标题栏和任务栏挂的是 Python 的图标，和产品名对不上。

这里**只管应用自己的那个窗口**，不碰命令行/终端窗口：从命令行启动时，
终端长什么样是用户的环境，不该被程序改。

所有函数都是「尽力而为」：非 Windows、图标文件缺失、API 调用失败一律静默返回
False。图标只是外观，不该成为启动失败的原因。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path

from wjxdesk.version import ICON_FILE

# -- Win32 常量 -------------------------------------------------------------

WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
GCLP_HICON = -14
GCLP_HICONSM = -34

APP_USER_MODEL_ID = "wjxdesk.assistant"

_IS_WINDOWS = sys.platform == "win32"

# 控制台宿主窗口的类名：它们也属于本进程，但换图标是另一套逻辑
_CONSOLE_CLASSES = {
    "ConsoleWindowClass",
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "PseudoConsoleWindow",
}


# -- 图标文件定位 -----------------------------------------------------------


def icon_candidates() -> list[Path]:
    """按「打包后优先、源码次之」的顺序给出候选路径。

    Nuitka 打包后资源在 exe 同级；开发态在包内 `wjxdesk/assets/`。
    """
    roots: list[Path] = []
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        roots.append(Path(sys.executable).resolve().parent)
    here = Path(__file__).resolve()
    roots.append(here.parents[1])  # wjxdesk/
    roots.append(here.parents[2])  # 仓库根
    return [root / "assets" / ICON_FILE for root in roots] + [
        root / ICON_FILE for root in roots
    ]


def icon_path() -> Path | None:
    for candidate in icon_candidates():
        if candidate.is_file():
            return candidate
    return None


# -- Win32 封装 -------------------------------------------------------------


def _load_icon(path: Path, size: int = 0):
    """载入图标句柄；size 为 0 时取系统默认尺寸。"""
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = ctypes.c_void_p
    user32.LoadImageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    flags = LR_LOADFROMFILE | (LR_DEFAULTSIZE if size == 0 else 0)
    return user32.LoadImageW(None, str(path), IMAGE_ICON, size, size, flags)


def _send_icon(hwnd: int, which: int, handle: int) -> None:
    user32 = ctypes.windll.user32
    user32.SendMessageW.restype = ctypes.c_void_p
    user32.SendMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    user32.SendMessageW(ctypes.c_void_p(hwnd), WM_SETICON, which, ctypes.c_void_p(handle))


def _set_window_class_icon(hwnd: int, index: int, handle: int) -> None:
    """同时改窗口类图标：任务栏与新弹出的系统菜单都读这个值。"""
    user32 = ctypes.windll.user32
    setter = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW
    setter.restype = ctypes.c_void_p
    setter.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    setter(ctypes.c_void_p(hwnd), index, ctypes.c_void_p(handle))


def _apply_to(hwnd: int, path: Path) -> bool:
    """给指定窗口挂大/小两个尺寸的图标。"""
    if not hwnd:
        return False
    big = _load_icon(path, 0)
    small = _load_icon(path, 16)
    if not big and not small:
        return False
    if big:
        _send_icon(hwnd, ICON_BIG, big)
        _set_window_class_icon(hwnd, GCLP_HICON, big)
    if small:
        _send_icon(hwnd, ICON_SMALL, small)
        _set_window_class_icon(hwnd, GCLP_HICONSM, small)
    return True


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(ctypes.c_void_p(hwnd), buffer, 256)
    return buffer.value


def _find_window(title: str) -> int:
    """找本进程的界面窗口，拿到 HWND。

    先按标题精确匹配：网页自身的 `<title>` 一般与窗口标题一致，这是最稳的信号。
    再退回按 pid 兜底——但 pywebview 有可能先建一个可见的辅助顶层窗口，
    直接取「本进程第一个可见窗口」会挂错，所以只在标题没命中时才用。
    控制台窗口也属于本进程，按类名排除掉。
    """
    user32 = ctypes.windll.user32
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.restype = ctypes.c_int
    pid_of = user32.GetWindowThreadProcessId
    pid_of.restype = ctypes.c_ulong
    pid_of.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]

    own_pid = os.getpid()
    by_pid: list[int] = []
    by_title: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            return True
        length = user32.GetWindowTextLengthW(ctypes.c_void_p(hwnd))
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(ctypes.c_void_p(hwnd), buffer, length + 1)

        if buffer.value == title:
            by_title.append(hwnd)
            return True

        pid = ctypes.c_ulong()
        pid_of(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        if pid.value == own_pid and _class_name(hwnd) not in _CONSOLE_CLASSES:
            by_pid.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    if by_title:
        return by_title[0]
    return by_pid[0] if by_pid else 0


# -- 对外 -------------------------------------------------------------------


def set_app_id(app_id: str = APP_USER_MODEL_ID) -> bool:
    """给进程一个独立的任务栏身份，避免和 python.exe 归成一组。"""
    if not _IS_WINDOWS:
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:
        return False


def apply_window_icon(title: str, *, timeout_s: float = 6.0, interval_s: float = 0.2) -> bool:
    """在后台等窗口出现并挂上图标。

    pywebview 要等 `start()` 进入事件循环后才创建原生窗口，所以这里只能轮询。
    用线程而不是阻塞，是为了不拖慢窗口显示。
    """
    if not _IS_WINDOWS:
        return False
    path = icon_path()
    if path is None:
        return False

    def worker() -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                hwnd = _find_window(title)
                if hwnd and _apply_to(hwnd, path):
                    return
            except Exception:
                return
            time.sleep(interval_s)

    threading.Thread(target=worker, name="wjxdesk-icon", daemon=True).start()
    return True
