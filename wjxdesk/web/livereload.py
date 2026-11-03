"""前端热更新：监听 frontend 目录，改动后让页面自动刷新。

只在开发模式启用（`dev_reload=True`），打包后的正式程序不会注入这段逻辑。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

_WATCH_SUFFIXES = {".html", ".css", ".js", ".png", ".jpg", ".svg", ".ico"}

INJECT_SCRIPT = """
<script>
/* 开发模式：前端文件变更后自动刷新（正式构建不会注入） */
(function () {
    if (!window.EventSource) return;
    var source = new EventSource('/api/dev/events');
    source.addEventListener('reload', function (event) {
        var detail = {};
        try { detail = JSON.parse(event.data || '{}'); } catch (e) {}
        console.info('[dev] 前端已更新，正在刷新：', detail.file || '');
        location.reload();
    });
})();
</script>
"""


class FrontendWatcher:
    """按 mtime 指纹判断前端是否变化。"""

    def __init__(self, root: Path, interval_s: float = 0.8) -> None:
        self.root = root
        self.interval = interval_s

    def fingerprint(self) -> tuple[float, str]:
        newest = 0.0
        changed = ""
        if not self.root.exists():
            return newest, changed
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _WATCH_SUFFIXES:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
                changed = str(path.relative_to(self.root))
        return newest, changed

    async def stream(self):
        """SSE：一直比对指纹，变化时推一条 reload 事件。"""
        last, _ = self.fingerprint()
        while True:
            await asyncio.sleep(self.interval)
            current, changed = self.fingerprint()
            if current > last:
                last = current
                payload = json.dumps({"file": changed, "mtime": current}, ensure_ascii=False)
                yield f"event: reload\ndata: {payload}\n\n"
            else:
                yield ": ping\n\n"


def inject(html: str) -> str:
    """把热更新脚本插到 </body> 前；没有 body 就追加到末尾。"""
    index = html.lower().rfind("</body>")
    if index == -1:
        return html + INJECT_SCRIPT
    return html[:index] + INJECT_SCRIPT + html[index:]
