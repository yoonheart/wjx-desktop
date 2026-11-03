"""冒烟检查：起服务、打几个接口、退出。

用途：改完代码后的 30 秒自检，不依赖界面也不依赖外网。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import uvicorn

from wjxdesk.storage.db import Store
from wjxdesk.storage.settings import load_profile
from wjxdesk.web.app import create_app
from wjxdesk.web.state import AppContext

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
PORT = 8765


def main() -> int:
    store = Store(ROOT / "build" / "smoke.db")
    ctx = AppContext(store=store, profile=load_profile())
    app = create_app(ctx, FRONTEND)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.05)

    failures = 0
    with httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=10) as client:
        checks = [
            ("GET", "/", None),
            ("GET", "/analysis", None),
            ("GET", "/static/js/request.js", None),
            ("GET", "/api/health", None),
            ("GET", "/api/profile", None),
            ("GET", "/api/history", None),
            ("GET", "/api/tasks/nope", None),
        ]
        for method, path, payload in checks:
            response = client.request(method, path, json=payload)
            flag = "ok " if response.status_code < 500 else "ERR"
            if response.status_code >= 500:
                failures += 1
            print(f"{flag} {response.status_code} {method} {path} ({len(response.content)} bytes)")

    server.should_exit = True
    thread.join(timeout=5)
    store.close()
    print("冒烟检查通过" if failures == 0 else f"{failures} 项失败")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
