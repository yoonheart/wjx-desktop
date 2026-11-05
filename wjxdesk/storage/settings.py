"""路径与方案文件。

程序目录可能只读（装在 Program Files），所以数据一律放用户目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from wjxdesk.domain.strategy import Strategy, default_strategy

APP_DIR_NAME = "wjx-desktop"


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    path = root / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "wjxdesk.db"


def app_dir() -> Path:
    """程序所在目录：打包后是 exe 同级，源码运行时是项目根。"""
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def log_dir() -> Path:
    """日志放在程序目录下的 logs（方便用户直接找到）。

    如果程序装在只读位置（例如 Program Files），写不进去就退回用户数据目录，
    保证不会因为权限问题丢日志。
    """
    candidate = app_dir() / "logs"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".writable"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return candidate
    except Exception:
        fallback = data_dir() / "logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def profile_path() -> Path:
    return data_dir() / "profile.json"


def export_dir() -> Path:
    path = data_dir() / "export"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_profile() -> Strategy:
    """读取方案；文件不存在或损坏时返回默认方案，绝不因此启动失败。"""
    path = profile_path()
    if not path.exists():
        return default_strategy()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Strategy.from_dict(raw)
    except Exception:
        return default_strategy()


def save_profile(strategy: Strategy) -> Path:
    path = profile_path()
    path.write_text(
        json.dumps(strategy.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def export_profile(strategy: Strategy, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(strategy.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


def import_profile(source: Path) -> Strategy:
    raw = json.loads(source.read_text(encoding="utf-8"))
    return Strategy.from_dict(raw)
