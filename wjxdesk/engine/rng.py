"""可复现随机源。

所有随机都从 (任务种子, 份号, 题号) 派生，绝不使用全局 random。
这样任意一份答卷都能被精确复现，也保证并发下不会互相污染。
"""

from __future__ import annotations

import hashlib
import random


def derive(seed: int, *keys: object) -> random.Random:
    """由种子与若干键派生独立随机源（跨进程稳定，与字典顺序无关）。"""
    digest = hashlib.blake2b(digest_size=16)
    digest.update(str(int(seed)).encode("utf-8"))
    for key in keys:
        digest.update(b"\x1f")
        digest.update(str(key).encode("utf-8"))
    return random.Random(int.from_bytes(digest.digest(), "big"))


def derive_float(seed: int, *keys: object) -> float:
    """派生一个 [0,1) 的稳定浮点数。"""
    return derive(seed, *keys).random()
