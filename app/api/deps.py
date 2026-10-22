"""Contexte partagé et dépendances communes des routeurs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import Query, Request

from ..auth import COOKIE_NAME, LoginThrottle, SessionCodec, fingerprint
from ..config import ConfigManager
from ..i18n import ApiError
from ..pathsec import PathError, normalize_rel
from ..scan_manager import AppState, ScanManager
from ..scheduler import Scheduler
from ..settings import Settings
from ..store import FileFilter, Store
from ..timeutil import day_end, day_start


@dataclass
class Context:
    settings: Settings
    cfg: ConfigManager
    state: AppState
    scans: ScanManager
    scheduler: Scheduler
    throttle: LoginThrottle

    @property
    def codec(self) -> SessionCodec:
        return SessionCodec(self.cfg.config.auth.secret)

    def session_user(self, request: Request) -> str | None:
        auth = self.cfg.config.auth
        payload = self.codec.verify(request.cookies.get(COOKIE_NAME))
        if not payload or not auth.username:
            return None
        if payload.get("u") != auth.username or payload.get("f") != fingerprint(auth.password_hash):
            return None
        return auth.username

    def store_or_503(self) -> Store:
        store = self.state.store
        if store is None:
            raise ApiError(503, "index_loading")
        return store


def ctx_of(request: Request) -> Context:
    return request.app.state.ctx


def require_user(request: Request) -> str:
    ctx = ctx_of(request)
    if ctx.settings.auth_disabled:
        return "anonyme"
    user = ctx.session_user(request)
    if user is None:
        raise ApiError(401, "auth_required")
    return user


def _csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(sorted({v.strip().lower().lstrip(".") for v in value.split(",") if v.strip()}))


def _date(value: str | None, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ApiError(400, "bad_date", name=name) from exc


def file_filter(
    root: str | None = Query(None, description="Identifiant de la racine (toutes si absent)"),
    path: str = Query("", description="Dossier relatif à la racine"),
    recursive: bool = Query(True, description="Inclure les sous-dossiers"),
    ext: str | None = Query(None, description="Extensions séparées par des virgules (jpg,png)"),
    category: str | None = Query(None, description="Catégories séparées par des virgules"),
    from_: str | None = Query(None, alias="from", description="Date de début AAAA-MM-JJ (incluse)"),
    to: str | None = Query(None, description="Date de fin AAAA-MM-JJ (incluse)"),
    date_field: str = Query("mtime", pattern="^(mtime|ctime)$", description="mtime (modification) ou ctime"),
    min_size: int | None = Query(None, ge=0, description="Taille minimale en octets"),
    max_size: int | None = Query(None, ge=0, description="Taille maximale en octets"),
    q: str = Query("", max_length=256, description="Sous-chaîne du nom, ou glob avec * et ?"),
) -> FileFilter:
    try:
        rel = normalize_rel(path)
    except PathError as exc:
        raise ApiError(400, "path_refused") from exc
    d_from, d_to = _date(from_, "from"), _date(to, "to")
    return FileFilter(
        root=root or None,
        path=rel if root else "",
        recursive=recursive if root else True,
        exts=_csv(ext),
        cats=_csv(category),
        date_field=date_field,
        date_from=day_start(d_from) if d_from else None,
        date_to=day_end(d_to) if d_to else None,
        min_size=min_size,
        max_size=max_size,
        q=q.strip(),
    )
