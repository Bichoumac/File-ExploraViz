"""Contexte partagé et dépendances communes des routeurs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from fastapi import Depends, Query, Request

from ..auth import COOKIE_NAME, LoginThrottle, SessionCodec, fingerprint
from ..config import ConfigManager
from ..i18n import ApiError
from ..pathsec import PathError, normalize_rel
from ..scan_manager import AppState, ScanManager
from ..scheduler import Scheduler
from ..settings import Settings
from ..store import FileFilter, Store
from ..timeutil import day_end, day_start


@dataclass(frozen=True)
class Principal:
    """Compte à l'origine d'une requête : rôle et périmètre de dossiers."""

    username: str
    role: str  # "admin" | "viewer"
    all_folders: bool
    scopes: tuple[tuple[str, str], ...]  # (racine, chemin) autorisés, sous-arborescences comprises

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def unrestricted(self) -> bool:
        return self.is_admin or self.all_folders


# AUTH_DISABLED=true : l'authentification est déléguée au reverse proxy, accès complet.
ANONYMOUS = Principal("anonymous", "admin", True, ())


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

    def session_user(self, request: Request) -> Principal | None:
        """Compte de la session ; None si le cookie est absent, invalide, expiré ou périmé.

        Le jeton contient l'empreinte du haché du mot de passe : une réinitialisation du mot de
        passe ou la suppression du compte invalide aussitôt ses sessions. Rôle et dossiers sont
        relus à chaque requête, donc toute modification s'applique immédiatement.
        """
        payload = self.codec.verify(request.cookies.get(COOKIE_NAME))
        if not payload:
            return None
        user = self.cfg.config.auth.find(payload.get("u"))
        if user is None or payload.get("f") != fingerprint(user.password_hash):
            return None
        return Principal(
            user.username, user.role, user.all_folders, tuple((sc.root, sc.path) for sc in user.scopes)
        )

    def store_or_503(self) -> Store:
        store = self.state.store
        if store is None:
            raise ApiError(503, "index_loading")
        return store

    def restrict(self, f: FileFilter, user: Principal, store: Store) -> FileFilter:
        """Ajoute au filtre le périmètre autorisé d'un compte restreint."""
        if user.unrestricted:
            return f
        return replace(f, access=store.access_ranges(user.scopes))


def ctx_of(request: Request) -> Context:
    return request.app.state.ctx


def current_user(request: Request) -> Principal:
    ctx = ctx_of(request)
    if ctx.settings.auth_disabled:
        return ANONYMOUS
    user = ctx.session_user(request)
    if user is None:
        raise ApiError(401, "auth_required")
    return user


# Nom historique, utilisé comme dépendance de routeur.
require_user = current_user


def require_admin(user: Principal = Depends(current_user)) -> Principal:
    if not user.is_admin:
        raise ApiError(403, "admin_only")
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
