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
from .deps import Context, Principal, ctx_of, current_user, require_admin, require_user

router = APIRouter(prefix="/api", tags=["scan"], dependencies=[Depends(require_user)])


class ScanRequest(BaseModel):
    root: str | None = None


# Champs de progression masqués aux comptes restreints : ils révèlent des dossiers et des
# volumes hors de leur périmètre.
_REDACTED = ("files", "dirs", "bytes", "inaccessible", "expected_files", "files_per_s", "current_root", "current_dir", "roots")


def full_status(ctx: Context, lang: str, user: Principal) -> dict:
    st = ctx.scans.status(lang)
    nxt = ctx.scheduler.next_run
    st["next_run"] = iso(nxt) if nxt else None
    st["interval_minutes"] = ctx.cfg.config.scan.interval_minutes
    if not user.unrestricted:
        for key in _REDACTED:
            st[key] = None
        if st.get("last_scan"):
            st["last_scan"] = {k: v for k, v in st["last_scan"].items() if k != "roots"}
    return st


@router.post("/scan", status_code=202, summary="Lancer un scan (global ou d'une racine)", dependencies=[Depends(require_admin)])
def start_scan(request: Request, body: ScanRequest | None = None) -> dict:
    ctx = ctx_of(request)
    root = body.root if body else None
    ok, key = ctx.scans.start([root] if root else None, "manuel")
    if not ok:
        raise ApiError(409, key)
    return {"started": True, "message": tr(key, lang_of(request)), "code": key}


@router.post("/scan/cancel", summary="Annuler le scan en cours", dependencies=[Depends(require_admin)])
def cancel_scan(request: Request) -> dict:
    ok = ctx_of(request).scans.cancel()
    key = "cancel_requested" if ok else "no_scan_running"
    return {"cancelled": ok, "message": tr(key, lang_of(request)), "code": key}


@router.get("/scan/status", summary="État du scan, dernier et prochain scan")
def scan_status(request: Request, user: Principal = Depends(current_user)) -> dict:
    return full_status(ctx_of(request), lang_of(request), user)


@router.get("/scan/progress", summary="Progression en direct (Server-Sent Events)")
async def scan_progress(request: Request, user: Principal = Depends(current_user)) -> StreamingResponse:
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
                yield f"event: progress\ndata: {json.dumps(full_status(ctx, lang, user), ensure_ascii=False)}\n\n"
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
