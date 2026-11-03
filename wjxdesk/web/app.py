"""HTTP 边界。

两个页面路由 + 一组 JSON 接口 + 一条 SSE 流。
所有异常在这里被翻译成统一信封，不让前端看到堆栈。
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from wjxdesk.domain.enums import ItemKind
from wjxdesk.domain.errors import WjxDeskError
from wjxdesk.domain.strategy import Strategy, default_strategy
from wjxdesk.runtime.gate import estimate
from wjxdesk.storage.settings import export_dir
from wjxdesk.web.envelope import err, ok
from wjxdesk.web.legacy import legacy_questions, strategy_from_legacy
from wjxdesk.web.livereload import FrontendWatcher, inject
from wjxdesk.web.state import AppContext

_KIND_CN = {
    ItemKind.SINGLE: "单选题",
    ItemKind.MULTI: "多选题",
    ItemKind.BLANK: "填空题",
    ItemKind.SCALE: "量表题",
    ItemKind.MATRIX: "矩阵题",
    ItemKind.DROPDOWN: "下拉框",
    ItemKind.SLIDER: "滑块题",
    ItemKind.RANK: "排序题",
    ItemKind.UNKNOWN: "未知题型",
}


def _survey_payload(survey: Any) -> dict[str, Any]:
    return {
        "token": survey.token,
        "title": survey.title,
        "count": survey.size,
        "questions": [
            {
                "number": item.number,
                "type": _KIND_CN.get(item.kind, "未知题型"),
                "kind": item.kind.value,
                "title": item.title,
                "required": item.required,
                "options": [{"index": o.index, "label": o.label} for o in item.options],
                "rows": [{"index": r.index, "label": r.label} for r in item.rows],
                "blanks": item.effective_blanks,
                "gate": item.gate.item_number if item.gate else None,
            }
            for item in survey.items
        ],
    }


def _sse(event: Any) -> str:
    return f"event: progress\ndata: {json.dumps(event.to_wire(), ensure_ascii=False)}\n\n"


def _goal_report(handle: Any) -> dict[str, Any] | None:
    """数据目标的体检报告：α / KMO / 每个变量的方向与 p 值。"""
    runner = getattr(handle, "runner", None)
    report = getattr(runner, "_goal_report", None)
    if report is None:
        return None
    try:
        return report.to_dict()
    except Exception:
        return None


def create_app(ctx: AppContext, frontend_dir: Path, *, dev_reload: bool = False) -> FastAPI:
    app = FastAPI(title="wjx-desktop", docs_url=None, redoc_url=None)

    # 本地单用户桌面应用：接口不认用户，只认「这是不是我们自己开的那个页面」。
    # 启动时生成一次性令牌，首个页面响应里种成 HttpOnly cookie；别的进程/网页
    # 拿不到这个 cookie，就碰不到 /api/* 下的任务创建、读取、停止等操作。
    session_token = secrets.token_urlsafe(32)

    @app.middleware("http")
    async def require_session(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/api/") and request.cookies.get("wjx_session") != session_token:
            return JSONResponse(err("未授权"), status_code=401)
        return await call_next(request)

    @app.middleware("http")
    async def no_store_static(request: Request, call_next: Any) -> Any:
        """界面资源一律禁缓存。

        WebView2 会把 /static 下的 js/css 按启发式规则缓存进磁盘，改完样式后
        用户看到的可能还是旧版。这里统一加 no-store，改一次就生效。
        """
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static") or path in {"/", "/analysis"}:
            response.headers["Cache-Control"] = "no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        return response

    if frontend_dir.exists():
        app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    # -- 页面 ---------------------------------------------------------------

    def _page(name: str, *, inject_dev: bool = True) -> Any:
        page = frontend_dir / name
        if not page.exists():
            return JSONResponse(err("前端文件缺失"))
        if dev_reload and inject_dev:
            response = HTMLResponse(inject(page.read_text(encoding="utf-8")))
        else:
            response = FileResponse(page)
        response.set_cookie("wjx_session", session_token, httponly=True, samesite="strict")
        return response

    @app.get("/")
    async def index() -> Any:
        return _page("index.html")

    @app.get("/analysis")
    async def analysis() -> Any:
        return _page("analysis.html")

    if dev_reload:
        watcher = FrontendWatcher(frontend_dir)

        @app.get("/api/dev/events")
        async def dev_events() -> Any:
            return StreamingResponse(
                watcher.stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            )

    # -- 解析与体检 ---------------------------------------------------------

    @app.get("/api/surveys/parse")
    async def parse(url: str = Query(...)) -> Any:
        try:
            survey = await ctx.platform.fetch_survey(url)
            return ok(_survey_payload(survey))
        except WjxDeskError as exc:
            return JSONResponse(err(str(exc)))
        except Exception as exc:  # 网络层异常统一兜底
            return JSONResponse(err(f"解析失败：{exc}"))

    @app.get("/api/selfcheck")
    async def selfcheck(url: str = Query(...)) -> Any:
        try:
            report = await ctx.platform.preflight(url)
            return ok(report.to_dict())
        except Exception as exc:
            return JSONResponse(err(f"体检失败：{exc}"))

    # -- 任务 ---------------------------------------------------------------

    @app.post("/api/tasks")
    async def create_task(request: Request) -> Any:
        body = await request.json()
        url = str(body.get("url") or "").strip()
        if not url:
            return JSONResponse(err("问卷地址不能为空"))
        raw_strategy = body.get("strategy") or default_strategy().to_dict()
        try:
            strategy = Strategy.from_dict(raw_strategy)
            strategy.validate()
        except Exception as exc:
            return JSONResponse(err(f"配置不合法：{exc}"))
        if body.get("total"):
            strategy = Strategy.from_dict({**strategy.to_dict(), "total": int(body["total"])})
        try:
            task_id = await ctx.create_task(url, strategy, dry_run=bool(body.get("dryRun")))
            return ok({"taskId": task_id})
        except Exception as exc:
            return JSONResponse(err(f"任务启动失败：{exc}"))

    @app.get("/api/tasks/{task_id}/events")
    async def events(task_id: str) -> Any:
        handle = ctx.tasks.get(task_id)
        if handle is None:
            return JSONResponse(err("任务不存在"), status_code=404)

        async def stream():
            queue = handle.subscribe()
            try:
                if handle.last_event is not None:
                    yield _sse(handle.last_event)
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        if handle.finished:
                            break
                        continue
                    yield _sse(event)
                    if event.state.is_terminal:
                        break
            finally:
                handle.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/tasks/{task_id}/stop")
    async def stop(task_id: str) -> Any:
        return ok("已请求停止") if ctx.stop_task(task_id) else JSONResponse(err("任务不存在"))

    @app.get("/api/tasks/{task_id}")
    async def task_detail(task_id: str) -> Any:
        handle = ctx.tasks.get(task_id)
        if handle is None:
            return JSONResponse(err("任务不存在"), status_code=404)
        return ok(
            {
                "task": handle.last_event.to_wire() if handle.last_event else None,
                "reasons": ctx.store.reason_summary(task_id),
                "records": ctx.store.records(task_id)[-200:],
                "goal": _goal_report(handle),
            }
        )

    # -- 配置 ---------------------------------------------------------------

    @app.get("/api/profile")
    async def get_profile() -> Any:
        return ok(ctx.profile.to_dict())

    @app.post("/api/profile")
    async def put_profile(request: Request) -> Any:
        body = await request.json()
        try:
            strategy = Strategy.from_dict(body)
            strategy.validate()
        except Exception as exc:
            return JSONResponse(err(f"配置不合法：{exc}"))
        ctx.profile = strategy
        return ok(strategy.to_dict())

    @app.post("/api/config/export")
    async def export_config(request: Request) -> Any:
        body = await request.json()
        name = str(body.get("suggestedName") or "wjx-profile.json")
        payload = body.get("data")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            if ctx.save_dialog is not None:
                path = await ctx.save_dialog(name, text)
                return ok({"path": path, "saved": bool(path)})
            # 无窗口（浏览器模式）：把内容交回前端，由它触发下载
            return ok({"path": "", "saved": False, "filename": name, "content": text})
        except Exception as exc:
            return JSONResponse(err(f"导出失败：{exc}"))

    @app.post("/api/config/save-text")
    async def save_text(request: Request) -> Any:
        """把一段文本（如答题表 CSV）存到用户选定的位置。"""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(err("请求体不是合法 JSON"))
        name = str(body.get("name") or "导出.csv")
        content = str(body.get("content") or "")
        if not content:
            return JSONResponse(err("没有内容可保存"))
        if ctx.save_dialog is None:
            return ok({"saved": False, "filename": name, "content": content})
        try:
            path = await ctx.save_dialog(name, content)
        except Exception as exc:
            return JSONResponse(err(f"保存失败：{exc}"))
        return ok({"saved": bool(path), "path": path or ""})

    @app.post("/api/config/import")
    async def import_config(request: Request) -> Any:
        try:
            body: dict[str, Any] = {}
            if request.headers.get("content-type", "").startswith("application/json"):
                try:
                    body = await request.json()
                except Exception:
                    body = {}

            content = body.get("content")
            if isinstance(content, str) and content.strip():
                raw = json.loads(content)
            elif ctx.open_dialog is not None:
                source = await ctx.open_dialog()
                if not source:
                    return JSONResponse(err("已取消"))
                raw = json.loads(Path(source).read_text(encoding="utf-8"))
            else:
                return JSONResponse(err("没有收到配置文件内容"))

            strategy = Strategy.from_dict(raw if isinstance(raw, dict) else {})
            strategy.validate()
            ctx.profile = strategy
            return ok(strategy.to_dict())
        except Exception as exc:
            return JSONResponse(err(f"导入失败：{exc}"))

    # -- 本地状态（替代 localStorage） ---------------------------------------

    @app.get("/api/state")
    async def get_state() -> Any:
        return ok(ctx.store.all_state())

    @app.post("/api/state")
    async def put_state(request: Request) -> Any:
        body = await request.json()
        items = body.get("items") if isinstance(body.get("items"), dict) else body
        saved = 0
        for name, value in (items or {}).items():
            if not isinstance(name, str):
                continue
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            ctx.store.set_state(name, text)
            saved += 1
        return ok({"saved": saved})

    # -- 历史 ---------------------------------------------------------------

    @app.get("/api/history")
    async def history() -> Any:
        return ok(
            [
                {"url": e.url, "title": e.title, "lastUsed": e.last_used, "runs": e.runs}
                for e in ctx.store.recent(8)
            ]
        )

    @app.get("/api/health")
    async def health() -> Any:
        return ok({"exits": ctx.pool.snapshot(), "tasks": len(ctx.tasks)})

    # -- 兼容：驱动接口已废弃，保留空壳避免旧前端报错 -------------------------

    @app.get("/api/driver/status")
    async def driver_status() -> Any:
        return ok(
            {
                "ready": True,
                "skipped": True,
                "edgeVersion": "",
                "driverVersion": "",
                "downloading": False,
                "progress": 100,
            }
        )

    @app.post("/api/driver/download")
    async def driver_download() -> Any:
        return ok({"ready": True, "skipped": True})

    @app.get("/api/driver/progress")
    async def driver_progress() -> Any:
        return ok({"ready": True, "downloading": False, "progress": 100})

    # -- 预估与导出口径 -----------------------------------------------------

    @app.get("/api/pacing/estimate")
    async def pacing_estimate(
        url: str = Query(...), total: int = 0, concurrency: int = 0
    ) -> Any:
        try:
            survey = await ctx.platform.fetch_survey(url)
            strategy = replace(
                ctx.profile,
                total=total or ctx.profile.total,
                concurrency=concurrency or ctx.profile.concurrency,
            )
            return ok(estimate(survey, strategy))
        except Exception as exc:
            return JSONResponse(err(f"预估失败：{exc}"))

    @app.get("/api/tasks/{task_id}/export")
    async def export_task(task_id: str, fmt: str = "csv") -> Any:
        if task_id not in ctx.tasks:
            return JSONResponse(err("任务不存在"), status_code=404)
        target = export_dir() / f"{task_id}.{f'csv' if fmt == 'csv' else 'json'}"
        try:
            if fmt == "csv":
                path = ctx.store.export_records_csv(task_id, target)
            else:
                path = ctx.store.export_json(task_id, target)
            return ok({"path": str(path)})
        except Exception as exc:
            return JSONResponse(err(f"导出失败：{exc}"))

    # -- 旧界面兼容（前端暂未改名时也能工作） -------------------------------

    @app.get("/api/analysis")
    async def legacy_analysis(url: str = Query(...)) -> Any:
        try:
            survey = await ctx.platform.fetch_survey(url)
            return ok(legacy_questions(survey))
        except WjxDeskError as exc:
            return JSONResponse(err(str(exc)))
        except Exception as exc:
            return JSONResponse(err(f"解析失败：{exc}"))

    @app.post("/api/brush")
    async def legacy_brush(request: Request) -> Any:
        body = await request.json()
        url = str(body.get("url") or "").strip()
        if not url:
            return JSONResponse(err("问卷地址不能为空"))
        try:
            strategy = strategy_from_legacy(body)
            task_id = await ctx.create_task(url, strategy, dry_run=bool(body.get("dryRun")))
            return ok({"taskId": task_id})
        except Exception as exc:
            return JSONResponse(err(f"任务启动失败：{exc}"))

    @app.get("/api/brush/progress/{task_id}")
    async def legacy_progress(task_id: str) -> Any:
        return await events(task_id)

    @app.post("/api/brush/stop/{task_id}")
    async def legacy_stop(task_id: str) -> Any:
        return await stop(task_id)

    return app
