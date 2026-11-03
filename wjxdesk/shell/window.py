"""桌面窗口。

用系统 WebView2 承载界面，不打包任何浏览器内核。
文件对话框由壳提供，Web 层通过回调使用，因此界面上的「导出/导入」行为不变。
"""

from __future__ import annotations

from pathlib import Path

from wjxdesk.shell.branding import apply_window_icon


class Window:
    def __init__(self, url: str, title: str, *, width: int = 1440, height: int = 900) -> None:
        self._url = url
        self._title = title
        self._size = (width, height)
        self._window = None

    # -- 生命周期 -----------------------------------------------------------

    def open(self, *, debug: bool = False) -> None:
        import webview

        # 任务栏身份由入口在创建窗口前设置一次，这里不再重复
        self._window = webview.create_window(
            self._title,
            self._url,
            width=self._size[0],
            height=self._size[1],
            min_size=(1024, 700),
            confirm_close=False,
        )
        # pywebview 没有图标参数，窗口出来后由后台线程补上
        apply_window_icon(self._title)

        # private_mode 默认是 True：关窗即清空 localStorage，
        # 于是网址历史、IP 代理链接、各项设置每次启动都"跟新的一样"。
        # 这里显式关掉，并把 WebView 的存储固定到用户数据目录。
        try:
            from wjxdesk.storage.settings import data_dir

            storage = data_dir() / "webview"
            storage.mkdir(parents=True, exist_ok=True)
            webview.start(debug=debug, private_mode=False, storage_path=str(storage))
        except Exception:
            # 老版本 pywebview 不支持这些参数时，退回默认启动
            webview.start(debug=debug)

    def close(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None

    # -- 原生对话框 ---------------------------------------------------------

    @staticmethod
    def choose_save_path(suggested: str) -> str:
        """弹出另存为对话框，返回路径；用户取消返回空串。"""
        try:
            import webview

            window = webview.windows[0] if webview.windows else None
            if window is None:
                return ""
            result = window.create_file_dialog(
                webview.SAVE_DIALOG, save_filename=suggested
            )
            if not result:
                return ""
            return result if isinstance(result, str) else str(result[0])
        except Exception:
            return ""

    @staticmethod
    def choose_open_path() -> str:
        try:
            import webview

            window = webview.windows[0] if webview.windows else None
            if window is None:
                return ""
            result = window.create_file_dialog(
                webview.OPEN_DIALOG, file_types=("JSON 配置 (*.json)",)
            )
            if not result:
                return ""
            return result if isinstance(result, str) else str(result[0])
        except Exception:
            return ""

    @staticmethod
    def write_text(path: str, text: str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target
