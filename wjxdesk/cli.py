"""命令行入口。

同一个核心既能开窗口，也能无人值守跑批——协议层的正确性验证必须靠批量运行，
图形界面不适合做这件事。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from wjxdesk.config_schema import schema
from wjxdesk.domain.answer import AnswerSheet, Stamp
from wjxdesk.domain.strategy import Strategy, default_strategy
from wjxdesk.engine.sampling import generate_sheet
from wjxdesk.protocol.proxy import ExitPool
from wjxdesk.protocol.registry import load as load_platform
from wjxdesk.protocol.transport import HttpTransport
from wjxdesk.shell.guard import DEFAULT_PORT
from wjxdesk.storage.db import Store
from wjxdesk.storage.settings import db_path, export_dir, load_profile
from wjxdesk.version import APP_NAME_CN, VERSION


def _bundle(exit_link: str = ""):
    manifest = load_platform("wjx", None).manifest
    pool = ExitPool(link=exit_link)
    transport = HttpTransport(pool, ua_profiles=manifest.device_profiles)
    return load_platform("wjx", transport)


def cmd_dev(args: argparse.Namespace) -> int:
    """开发模式：后端热重载 + 前端自动刷新。

    app 必须由字符串导入（`wjxdesk.web.devapp:app`），uvicorn 才能在子进程里
    重建它；这也是 devapp 存在的原因——启动时不依赖 `__main__`。
    """
    import uvicorn

    root = Path(__file__).resolve().parents[1]
    print(f"{APP_NAME_CN} 开发模式：http://127.0.0.1:{args.port}/", flush=True)
    print("  后端：改 wjxdesk/ 下的 .py 自动重载", flush=True)
    print("  前端：改 frontend/ 下的文件，页面自动刷新", flush=True)
    print("  退出：Ctrl+C", flush=True)
    uvicorn.run(
        "wjxdesk.web.devapp:app",
        host="127.0.0.1",
        port=args.port,
        reload=True,
        reload_dirs=[str(root / "wjxdesk")],
        log_level="info",
    )
    return 0


def cmd_schema(_: argparse.Namespace) -> int:
    print(json.dumps(schema(), ensure_ascii=False, indent=2))
    return 0


async def cmd_parse(args: argparse.Namespace) -> int:
    bundle = _bundle(args.exit_link)
    survey = await bundle.fetch_survey(args.url)
    payload = {
        "token": survey.token,
        "title": survey.title,
        "count": survey.size,
        "questions": [
            {
                "number": item.number,
                "kind": item.kind.value,
                "title": item.title,
                "required": item.required,
                "options": [o.label for o in item.options],
                "rows": [r.label for r in item.rows],
            }
            for item in survey.items
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


async def cmd_selfcheck(args: argparse.Namespace) -> int:
    bundle = _bundle(args.exit_link)
    report = await bundle.preflight(args.url)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0 if report.viable else 1


async def cmd_verify(args: argparse.Namespace) -> int:
    """预演：解析 → 生成 → 编码，只打印将要发送的请求，不发送。"""
    bundle = _bundle(args.exit_link)
    survey = await bundle.fetch_survey(args.url)
    strategy = args.profile or default_strategy()
    seed = strategy.resolved_seed()

    for unit in range(1, max(1, args.units) + 1):
        sheet = generate_sheet(survey, strategy, seed, unit)
        stamp = Stamp(started_at=0.0, elapsed_s=90.0, issued_at=90.0, nonce=f"verify{unit:04d}")
        request = bundle.encoder.encode(survey, sheet, stamp, strategy.channel)
        print(f"--- unit {unit} ---")
        print(json.dumps(request.summarize(), ensure_ascii=False, indent=2))
        print("blob:", _decode_blob(request.body))
    return 0


def _decode_blob(body: str) -> str:
    from urllib.parse import unquote_plus

    for pair in body.split("&"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            if key in ("submitdata",):
                return unquote_plus(value)
    return ""


def cmd_export(args: argparse.Namespace) -> int:
    store = Store(db_path())
    target = Path(args.out) if args.out else export_dir() / f"{args.task_id}.csv"
    path = store.export_records_csv(args.task_id, target)
    print(str(path))
    store.close()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """无人值守跑批，进度打印到标准输出。"""
    from wjxdesk.runtime.pacing import Pacer
    from wjxdesk.runtime.runner import TaskRunner

    bundle = _bundle(args.exit_link)
    survey = asyncio.run(bundle.fetch_survey(args.url))
    strategy = args.profile or default_strategy()
    if args.total:
        strategy = Strategy.from_dict({**strategy.to_dict(), "total": args.total})

    runner = TaskRunner(
        task_id="cli",
        bundle=bundle,
        survey=survey,
        strategy=strategy,
        pool=bundle.transport._pool if bundle.transport else ExitPool(),
        pacer=Pacer(strategy.pacing, seed=strategy.resolved_seed()),
    )
    runner.on_event = lambda event: print(json.dumps(event.to_wire(), ensure_ascii=False))
    result = asyncio.run(runner.run())
    print(json.dumps({"state": result.state.value, "counters": result.counters.as_dict()}, ensure_ascii=False))
    return 0 if result.counters.confirmed else 1


def cmd_calibrate(args: argparse.Namespace) -> int:
    """用一次真实抓包反推「请求校验串」的算法。

    抓包文件形如：{"url": "https://.../processjq.ashx?shortid=..&jqnonce=..&jqsign=..&ktimes=..",
                  "body": "submitdata=..."}
    """
    from urllib.parse import parse_qs, urlparse

    from wjxdesk.protocol.wjx import seal

    raw = json.loads(Path(args.capture).read_text(encoding="utf-8"))
    query = parse_qs(urlparse(str(raw.get("url", ""))).query)
    nonce = (query.get("jqnonce") or [""])[0]
    expected = (query.get("jqsign") or [""])[0]
    try:
        elapsed = int((query.get("ktimes") or ["0"])[0] or 0)
    except ValueError:
        elapsed = 0
    token = (query.get("shortid") or [""])[0]
    body = str(raw.get("body", ""))

    print("抓包字段：")
    print("  jqnonce =", nonce or "(缺失)")
    print("  jqsign  =", expected or "(缺失)")
    print("  ktimes  =", elapsed)
    print("  shortid =", token or "(缺失)")
    print("  body 长度 =", len(body))

    found = None
    for mode, value in seal.candidates(nonce, elapsed, token).items():
        mark = "命中" if expected and value == expected else "   "
        print(f"  {mark} {mode:12} {value or '(空)'}")
        if expected and value == expected:
            found = mode

    if not expected:
        print("\n抓包里没有 jqsign：说明该问卷不需要签名，保持 channel.seal_mode = none 即可。")
        return 0
    if found:
        print(f"\n命中的策略：{found}")
        print(f'把方案里的 channel.seal_mode 改成 "{found}" 即可。')
        return 0
    print("\n三种候选都没命中。需要补一条策略：在 protocol/wjx/seal.py 里加一个分支。")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wjxdesk", description=f"{APP_NAME_CN} {VERSION}")
    parser.add_argument("--profile", type=Path, help="方案文件路径，默认读取本地方案")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("schema", help="输出配置结构说明").set_defaults(func=cmd_schema)

    dev = sub.add_parser("dev", help="开发模式：热重载启动界面")
    dev.add_argument("--port", type=int, default=DEFAULT_PORT)
    dev.set_defaults(func=cmd_dev)

    for name, func, help_text in (
        ("parse", cmd_parse, "解析问卷结构"),
        ("selfcheck", cmd_selfcheck, "可行性体检"),
        ("verify", cmd_verify, "预演编码，不发送"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("url")
        p.add_argument("--exit-link", default="", help="出口提取链接，留空为直连")
        p.add_argument("--units", type=int, default=2)
        p.set_defaults(func=func)

    run = sub.add_parser("run", help="无人值守跑批")
    run.add_argument("url")
    run.add_argument("--total", type=int, default=0)
    run.add_argument("--exit-link", default="")
    run.set_defaults(func=cmd_run)

    export = sub.add_parser("export", help="导出任务记录")
    export.add_argument("task_id")
    export.add_argument("--out", default="")
    export.set_defaults(func=cmd_export)

    calibrate = sub.add_parser("calibrate", help="用抓包反推请求校验串算法")
    calibrate.add_argument("capture")
    calibrate.set_defaults(func=cmd_calibrate)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认可能是 GBK，统一成 UTF-8 并容忍不可编码字符，避免中文报错
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    profile_path = getattr(args, "profile", None)
    if profile_path:
        try:
            args.profile = Strategy.from_dict(json.loads(Path(profile_path).read_text("utf-8")))
        except Exception as exc:
            print(f"方案文件读取失败：{exc}", file=sys.stderr)
            return 2
    elif hasattr(args, "profile"):
        args.profile = load_profile()

    result = args.func(args)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return int(result or 0)


if __name__ == "__main__":
    sys.exit(main())
