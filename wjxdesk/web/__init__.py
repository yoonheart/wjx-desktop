"""Web 层公开接口。"""

from wjxdesk.web.app import create_app
from wjxdesk.web.envelope import err, ok
from wjxdesk.web.state import AppContext, TaskHandle

__all__ = ["AppContext", "TaskHandle", "create_app", "err", "ok"]
