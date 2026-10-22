"""Routes de pilotage du scan, dont le flux SSE de progression."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..i18n import ApiError, lang_of, tr
from ..timeutil import iso
from .deps import Context, ctx_of, require_user

router = APIRouter(prefix="/api", tags=["scan"], dependencies=[Depends(require_user)])


class ScanRequest(BaseModel):
    root: str | None = None


def full_status(ctx: Context, lang: str) -> dict:
    st = ctx.scans.status(lang)
    nxt = ctx.scheduler.next_run
    st["next_run"] = iso(nxt) if nxt else None
    st["interval_minutes"] = ctx.cfg.config.scan.interval_minutes
    return st


@router.post("/scan", status_code=202, summary="Lancer un scan (global ou d'une racine)")
def start_scan(request: Request, body: ScanRequest | None = None) -> dict:
    ctx = ctx_of(request)
    root = body.root if body else None
    ok, key = ctx.scans.start([root] if root else None, "manuel")
    if not ok:
        raise ApiError(409, key)
    return {"started": True, "message": tr(key, lang_of(request)), "code": key}


@router.post("/scan/cancel", summary="Annuler le scan en cours")
def cancel_scan(request: Request) -> dict:
    ok = ctx_of(request).scans.cancel()
    key = "cancel_requested" if ok else "no_scan_running"
    return {"cancelled": ok, "message": tr(key, lang_of(request)), "code": key}


@router.get("/scan/status", summary="État du scan, dernier et prochain scan")
def scan_status(request: Request) -> dict:
    return full_status(ctx_of(request), lang_of(request))


@router.get("/scan/progress", summary="Progression en direct (Server-Sent Events)")
async def scan_progress(request: Request) -> StreamingResponse:
    ctx = ctx_of(request)
    lang = lang_of(request)

    async def events() -> AsyncIterator[str]:
        last_version = -1
        idle = 0.0
        yield "retry: 5000\n\n"
        while not await request.is_disconnected():
            running = ctx.scans.running
            version = ctx.scans.version
            if running or version != last_version:
                last_version = version
                idle = 0.0
                yield f"event: progress\ndata: {json.dumps(full_status(ctx, lang), ensure_ascii=False)}\n\n"
            elif idle >= 20:
                idle = 0.0
                yield ": ping\n\n"
            delay = 1.0 if running else 2.0
            idle += delay
            await asyncio.sleep(delay)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
