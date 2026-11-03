"""开发模式启动脚本。

一条命令：后端热重载 + 前端自动刷新。

    .\\.venv\\Scripts\\python.exe scripts\\dev.py

可加参数透传给 uvicorn，例如换端口：

    .\\.venv\\Scripts\\python.exe scripts\\dev.py --port 8010
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    args = sys.argv[1:]
    port = "8000"
    for index, item in enumerate(args):
        if item in {"--port", "-p"} and index + 1 < len(args):
            port = args[index + 1]

    import uvicorn

    print(f"开发模式启动：http://127.0.0.1:{port}/")
    print("  后端：改 wjxdesk/ 下的 .py 会自动重载")
    print("  前端：改 frontend/ 下的文件会自动刷新页面")
    print("  退出：Ctrl+C")
    uvicorn.run(
        "wjxdesk.web.devapp:app",
        host="127.0.0.1",
        port=int(port),
        reload=True,
        reload_dirs=[str(ROOT / "wjxdesk")],
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
