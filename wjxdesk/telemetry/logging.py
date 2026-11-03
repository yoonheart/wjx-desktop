"""日志。

为什么要有：任务突然停下时，界面只显示「已完成 1 / 6」，看不出是验证、限流、
还是代理池没取到 IP。日志把每次结果和停机理由都落盘，用户可以整份发回来。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
_configured = False


def setup_logging(log_dir: Path, *, level: int = logging.INFO) -> Path:
    """配置根日志：控制台 + 滚动文件。重复调用只生效一次。"""
    global _configured
    log_dir.mkdir(parents=True, exist_ok=True)
    target = log_dir / "wjxdesk.log"

    root = logging.getLogger()
    if _configured:
        return target

    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    file_handler = RotatingFileHandler(
        target, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # 无控制台 exe（console=False）里 sys.stderr 是 None，
    # 加 StreamHandler 会在每次写日志时崩掉，这里要判断。
    import sys

    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    # 单独的出错日志：只记录失败与验证原因，用户反馈问题时发这一个文件即可
    error_target = log_dir / "errors.log"
    error_handler = RotatingFileHandler(
        error_target, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8"
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.WARNING)
    error_logger = logging.getLogger("wjxdesk.errors")
    error_logger.setLevel(logging.WARNING)
    error_logger.addHandler(error_handler)
    error_logger.propagate = True

    # 第三方库的噪音压掉，只留警告
    for noisy in ("httpx", "httpcore", "uvicorn.access", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True
    return target
