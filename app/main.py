"""桌面入口。

顺序：端口守卫 → 起服务 → 等就绪 → 开窗口。
服务在后台线程跑，窗口关闭即退出。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import threading
from pathlib import Path

import uvicorn

from wjxdesk.domain.strategy import default_strategy
from wjxdesk.shell.branding import set_app_id
from wjxdesk.shell.guard import DEFAULT_PORT, port_in_use, wait_until_ready
from wjxdesk.shell.window import Window
from wjxdesk.storage.db import Store
from wjxdesk.storage.settings import data_dir, db_path, load_profile, log_dir
from wjxdesk.telemetry.logging import setup_logging
from wjxdesk.version import APP_NAME_CN
from wjxdesk.web.app import create_app
from wjxdesk.web.state import AppContext

def _bundle_root() -> Path:
    """源码运行时是项目根；PyInstaller 打包后是解包目录（_MEIPASS）。"""
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


ROOT = _bundle_root()
FRONTEND = ROOT / "frontend"


def _serve(port: int, ctx: AppContext) -> None:
    app = create_app(ctx, FRONTEND)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _ensure_std_streams() -> None:
    """打包成无控制台 exe 后 sys.stdout/stderr 是 None。

    logging.StreamHandler 和 uvicorn 都会往它们写，None 会让服务线程静默死掉，
    表现就是「双击 exe 没任何反应」。这里先补上占位流。
    """
    import os

    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    _ensure_std_streams()

    # 任务栏身份先定下来，窗口才会用应用自己的图标而不是 python.exe 的
    set_app_id()

    log_file = setup_logging(log_dir())

    parser = argparse.ArgumentParser(prog=APP_NAME_CN, description=f"{APP_NAME_CN} 桌面端")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--debug", action="store_true", help="打开开发者工具")
    parser.add_argument("--no-window", action="store_true", help="只起服务，不开窗口")
    args = parser.parse_args(argv)

    if port_in_use(args.port):
        # 已经被上一个实例占用时，换一个端口照常启动，不要静默退出
        picked = None
        for candidate in range(args.port + 1, args.port + 21):
            if not port_in_use(candidate):
                picked = candidate
                break
        if picked is None:
            print(f"端口 {args.port}~{args.port + 20} 都被占用，无法启动。")
            return 2
        print(f"端口 {args.port} 已被占用，改用 {picked}。")
        args.port = picked

    store = Store(db_path())
    profile = load_profile() or default_strategy()

    ctx = AppContext(store=store, profile=profile)
    ctx.save_dialog = _save_dialog
    ctx.open_dialog = lambda: asyncio.to_thread(Window.choose_open_path)

    threading.Thread(target=_serve, args=(args.port, ctx), daemon=True).start()

    if not wait_until_ready(args.port):
        print("服务启动超时，请检查日志：", data_dir() / "logs")
        return 3

    if args.no_window:
        print(f"已启动：http://127.0.0.1:{args.port}/")
        threading.Event().wait()
        return 0

    Window(f"http://127.0.0.1:{args.port}/", APP_NAME_CN).open(debug=args.debug)
    store.close()
    return 0


async def _save_dialog(name: str, text: str) -> str:
    path = await asyncio.to_thread(Window.choose_save_path, name)
    if not path:
        return ""
    await asyncio.to_thread(Window.write_text, path, text)
    return path


if __name__ == "__main__":
    sys.exit(main())
