"""请求校验串。

线上客户端的真实算法（从页面脚本读出，属于协议事实）：

    key = ktimes % 10;  if (key == 0) key = 1;
    sign = 每个字符 charCode ^ key 后拼回字符串

也就是「用 ktimes 的个位做异或」，不是哈希也不是加密。
用 python 复刻：

    "".join(chr(ord(c) ^ key) for c in nonce)

之所以单独放一个文件：如果哪天平台换算法，只改这里。
"""

from __future__ import annotations

MODES = ("none", "xor_ktimes", "md5_concat", "md5_list")


def _xor_key(ktimes: int) -> int:
    key = int(ktimes) % 10
    return key or 1


def compute(mode: str, *, nonce: str, ktimes: int, token: str = "") -> str:
    """按策略计算校验串。未知策略抛 ValueError，避免静默出错。"""
    if mode in ("", "none"):
        return ""
    if mode == "xor_ktimes":
        key = _xor_key(ktimes)
        return "".join(chr(ord(ch) ^ key) for ch in nonce)
    if mode == "md5_concat":
        import hashlib

        return hashlib.md5(f"{nonce}{ktimes}".encode("utf-8")).hexdigest()
    if mode == "md5_list":
        import hashlib

        return hashlib.md5("|".join([nonce, str(ktimes), token]).encode("utf-8")).hexdigest()
    raise ValueError(f"未知的签名策略：{mode}，可选 {', '.join(MODES)}")


def candidates(nonce: str, ktimes: int, token: str = "") -> dict[str, str]:
    """给 calibrate 命令用：一次算出所有候选值。"""
    return {
        mode: compute(mode, nonce=nonce, ktimes=ktimes, token=token)
        for mode in MODES
    }
