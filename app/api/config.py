"""Routes de configuration."""

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ..categories import CATEGORIES, DEFAULT_MAPPING, build_ext_map
from ..config import DEFAULT_EXCLUSIONS, ConfigError
from ..store import Store
from ..summary import build_summary
from .deps import Context, ctx_of, require_user

log = logging.getLogger("config")
router = APIRouter(prefix="/api", tags=["configuration"], dependencies=[Depends(require_user)])


def _payload(ctx: Context) -> dict:
    data = ctx.cfg.public_dict()
    data["roots"] = [{**r, "available": ctx.cfg.available(ctx.cfg.config.roots[i])} for i, r in enumerate(data["roots"])]
    data["detected_folders"] = ctx.cfg.detect_folders()
    data["category_labels"] = dict(CATEGORIES)
    data["defaults"] = {"exclusions": DEFAULT_EXCLUSIONS, "categories": DEFAULT_MAPPING}
    data["auth_disabled"] = ctx.settings.auth_disabled
    return data


@router.get("/config", summary="Configuration (sans les secrets)")
def get_config(request: Request) -> dict:
    ctx = ctx_of(request)
    ctx.cfg.sync_roots()
    return _payload(ctx)


def _recategorize(ctx: Context) -> None:
    """Applique un nouveau mapping de catégories sans rescanner (en arrière-plan)."""
    store = ctx.state.store
    if store is None:
        return
    try:
        store.apply_categories(build_ext_map(ctx.cfg.config.categories))
        summary = ctx.state.summary or {}
        new = build_summary(store, summary.get("scan") or {"status": "ok"})
        ctx.scans.summaries.save_current(new)  # l'historique n'est pas touché
        ctx.state.summary = new
    except Exception:
        log.exception("recalcul des catégories")


@router.put("/config", summary="Enregistrer la configuration")
def put_config(request: Request, body: dict = Body(...)) -> dict:
    ctx = ctx_of(request)
    before = ctx.cfg.config.categories
    try:
        ctx.cfg.update(body)
    except ConfigError as exc:
        return JSONResponse(  # type: ignore[return-value]
            status_code=422, content={"detail": "Configuration invalide.", "errors": exc.messages}
        )
    if ctx.cfg.config.categories != before:
        threading.Thread(target=_recategorize, args=(ctx,), daemon=True, name="recategorize").start()
    ctx.scheduler.wake()
    return _payload(ctx)


@router.post("/cache/clear", summary="Vider le cache puis relancer un scan")
def clear_cache(request: Request) -> dict:
    ctx = ctx_of(request)
    if ctx.scans.running:
        raise HTTPException(409, "Un scan est en cours : annulez-le d'abord.")
    ctx.scans.summaries.clear()
    ctx.state.summary = None
    ctx.state.swap(Store.empty(), None)
    ok, message = ctx.scans.start(None, "rescan")
    return {"started": ok, "message": "Cache vidé. " + message}
