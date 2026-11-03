"""单实例与端口守卫。

问题来源：桌面端固定端口启动，重复双击会导致第二个实例起不来，用户看到的是
「Failed to launch JVM」这类无关错误。这里用端口占用检测给出明确提示。
"""

from __future__ import annotations

import socket

DEFAULT_PORT = 8000


def port_in_use(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        # 能连上说明已有实例在监听
        return probe.connect_ex((host, port)) == 0


def port_available(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def wait_until_ready(port: int = DEFAULT_PORT, host: str = "127.0.0.1", timeout_s: float = 15.0) -> bool:
    """等服务端就绪，避免窗口先于服务打开出现白屏。"""
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if port_in_use(port, host):
            return True
        time.sleep(0.1)
    return False
