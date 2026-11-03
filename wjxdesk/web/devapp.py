"""开发模式入口。

与 `app/main.py` 的区别：这里在模块级构建 app，供 `uvicorn --reload` 使用。
命令行示例：

    .\\.venv\\Scripts\\python.exe -m uvicorn wjxdesk.web.devapp:app --reload \
        --reload-dir wjxdesk --port 8000

前端（HTML/CSS/JS）改动不需要重启：页面里注入了热更新脚本，会自动刷新。
"""

from __future__ import annotations

from pathlib import Path

from wjxdesk.storage.db import Store
from wjxdesk.storage.settings import db_path, load_profile, log_dir
from wjxdesk.telemetry.logging import setup_logging
from wjxdesk.web.app import create_app
from wjxdesk.web.state import AppContext

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"

setup_logging(log_dir())
_store = Store(db_path())
_context = AppContext(store=_store, profile=load_profile())

app = create_app(_context, FRONTEND, dev_reload=True)
