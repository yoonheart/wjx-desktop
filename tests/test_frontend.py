"""前端冒烟：用 jsdom 真跑一遍面板脚本，防止 TDZ / 导入不生效这类回归。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_MODULES = (
    Path.home() / ".workbuddy" / "binaries" / "node" / "workspace" / "node_modules"
)


def _node() -> str:
    found = shutil.which("node")
    if found:
        return found
    managed = (
        Path.home()
        / ".workbuddy"
        / "binaries"
        / "node"
        / "versions"
        / "22.22.2-6"
        / "node.exe"
    )
    return str(managed) if managed.exists() else ""


def test_frontend_smoke() -> None:
    node = _node()
    if not node:
        pytest.skip("没有 node 运行时")
    if not WORKSPACE_MODULES.joinpath("jsdom").exists():
        pytest.skip("没有安装 jsdom")

    env = os.environ.copy()
    env["NODE_PATH"] = str(WORKSPACE_MODULES)
    result = subprocess.run(
        [node, str(ROOT / "tests" / "frontend_smoke.js")],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr


def test_frontend_load_all_scripts() -> None:
    """把 analysis.js 也真加载一遍：抓「调用了但没定义」这类 ReferenceError。"""
    node = _node()
    if not node:
        pytest.skip("没有 node 运行时")
    if not WORKSPACE_MODULES.joinpath("jsdom").exists():
        pytest.skip("没有安装 jsdom")

    env = os.environ.copy()
    env["NODE_PATH"] = str(WORKSPACE_MODULES)
    result = subprocess.run(
        [node, str(ROOT / "tests" / "frontend_load_all.js")],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
