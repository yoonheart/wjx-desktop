"""唯一池。

用途：填空题、开放题、「其他」输入框——同一问卷的多份答卷之间不允许重复。
池按分组名隔离，领取失败时降级为普通随机，绝不因为池空而中断任务。
"""

from __future__ import annotations

import logging
import random
import threading

log = logging.getLogger("wjxdesk.unique")


class UniquePool:
    """线程安全的领取-归还池。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._remaining: dict[str, list[str]] = {}
        self._taken: dict[str, list[str]] = {}
        self._warned: set[str] = set()

    def load(self, group: str, values: list[str]) -> None:
        with self._lock:
            self._remaining[group] = list(values)
            self._taken.setdefault(group, [])

    def groups(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._remaining)

    def size(self, group: str) -> int:
        with self._lock:
            return len(self._remaining.get(group, ()))

    def take(self, group: str, rng: random.Random, fallback: str = "") -> tuple[str, bool]:
        """领取一个未用过的值。返回 (值, 是否为池内真实值)。"""
        with self._lock:
            pool = self._remaining.get(group) or []
            if not pool:
                if group not in self._warned:
                    self._warned.add(group)
                    log.warning("唯一池已空（%s）：后续答卷会重复取默认值", group)
                return fallback, False
            index = rng.randrange(len(pool))
            value = pool.pop(index)
            self._taken.setdefault(group, []).append(value)
            return value, True

    def release(self, group: str, value: str) -> None:
        """归还（例如该份提交失败，答案要重新可用）。"""
        with self._lock:
            taken = self._taken.get(group) or []
            if value in taken:
                taken.remove(value)
                self._remaining.setdefault(group, []).append(value)

    def reset(self) -> None:
        with self._lock:
            for group, values in self._taken.items():
                self._remaining.setdefault(group, []).extend(values)
            self._taken.clear()
