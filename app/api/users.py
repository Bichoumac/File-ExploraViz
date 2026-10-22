"""Gestion des comptes (administrateurs seulement).

Règles :
* un administrateur crée des comptes ``admin`` ou ``viewer`` ;
* un ``viewer`` voit soit tous les dossiers (``all_folders``), soit seulement ses ``scopes``
  (racine + sous-dossier, sous-arborescence comprise) ;
* un administrateur peut réinitialiser le mot de passe d'un compte ``viewer`` ; le mot de passe
  d'un administrateur ne change que par lui-même (``/api/auth/password``) ;
* il reste toujours au moins un administrateur, et on ne peut pas supprimer son propre compte.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..auth import hash_password
from ..config import USERNAME_RE, Scope, UserConfig
from ..i18n import ApiError, lang_of, tr
from ..pathsec import PathError, normalize_rel
from .auth import check_password_policy
from .deps import Context, Principal, ctx_of, require_admin

router = APIRouter(prefix="/api/users", tags=["comptes"], dependencies=[Depends(require_admin)])


class ScopeIn(BaseModel):
    root: str = Field(min_length=1, max_length=64)
    path: str = Field("", max_length=4096)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    role: Literal["admin", "viewer"] = "viewer"
    all_folders: bool = False
    scopes: list[ScopeIn] = Field(default_factory=list, max_length=200)


class UserUpdate(BaseModel):
    role: Literal["admin", "viewer"] | None = None
    all_folders: bool | None = None
    scopes: list[ScopeIn] | None = Field(None, max_length=200)


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


def _scopes(ctx: Context, items: list[ScopeIn], lang: str) -> list[Scope]:
    """Valide les dossiers autorisés : racine configurée, chemin relatif sans « .. »."""
    known = {r.id for r in ctx.cfg.config.roots}
    out: list[Scope] = []
    seen = set()
    for item in items:
        if item.root not in known:
            raise ApiError(400, "invalid_scope", detail=tr("unknown_root", lang, root=item.root))
        try:
            path = normalize_rel(item.path)
        except PathError as exc:
            raise ApiError(400, "invalid_scope", detail=tr("bad_scope_path", lang, path=item.path)) from exc
        if (item.root, path) not in seen:
            seen.add((item.root, path))
            out.append(Scope(root=item.root, path=path))
    return out


def _public(ctx: Context, user: UserConfig, me: Principal) -> dict:
    store = ctx.state.store
    return {
        "username": user.username,
        "role": user.role,
        "all_folders": user.all_folders,
        "scopes": [
            {
                "root": sc.root,
                "path": sc.path,
                # Le dossier existe-t-il dans l'index actuel ? (renommé ou supprimé sinon)
                "found": None if store is None else store.resolve_dir(sc.root, sc.path) is not None,
            }
            for sc in user.scopes
        ],
        "is_self": user.username.lower() == me.username.lower(),
    }


def _get(ctx: Context, username: str) -> UserConfig:
    user = ctx.cfg.config.auth.find(username)
    if user is None:
        raise ApiError(404, "user_not_found")
    return user


@router.get("", summary="Liste des comptes")
def list_users(request: Request, me: Principal = Depends(require_admin)) -> list[dict]:
    ctx = ctx_of(request)
    return [_public(ctx, u, me) for u in ctx.cfg.config.auth.users]


@router.post("", status_code=201, summary="Créer un compte")
def create_user(body: UserCreate, request: Request, me: Principal = Depends(require_admin)) -> dict:
    ctx = ctx_of(request)
    username = body.username.strip()
    if not re.fullmatch(USERNAME_RE, username):
        raise ApiError(400, "invalid_username")
    check_password_policy(body.password)
    scopes = _scopes(ctx, body.scopes, lang_of(request))
    with ctx.cfg.lock:
        auth = ctx.cfg.config.auth
        if auth.find(username) is not None:
            raise ApiError(409, "user_exists", name=username)
        user = UserConfig(
            username=username,
            password_hash=hash_password(body.password),
            role=body.role,
            all_folders=body.all_folders,
            scopes=scopes,
        )
        auth.users.append(user)
        ctx.cfg.save()
    return _public(ctx, user, me)


@router.put("/{username}", summary="Modifier le rôle ou les dossiers d'un compte")
def update_user(username: str, body: UserUpdate, request: Request, me: Principal = Depends(require_admin)) -> dict:
    ctx = ctx_of(request)
    scopes = _scopes(ctx, body.scopes, lang_of(request)) if body.scopes is not None else None
    with ctx.cfg.lock:
        user = _get(ctx, username)
        if body.role == "viewer" and user.role == "admin" and len(ctx.cfg.config.auth.admins) <= 1:
            raise ApiError(400, "last_admin")
        if body.role is not None:
            user.role = body.role
        if body.all_folders is not None:
            user.all_folders = body.all_folders
        if scopes is not None:
            user.scopes = scopes
        ctx.cfg.save()
    return _public(ctx, user, me)


@router.post("/{username}/password", summary="Réinitialiser le mot de passe d'un compte viewer")
def reset_password(username: str, body: PasswordReset, request: Request) -> dict:
    ctx = ctx_of(request)
    with ctx.cfg.lock:
        user = _get(ctx, username)
        if user.role == "admin":
            raise ApiError(403, "reset_admin_forbidden")
        check_password_policy(body.password)
        user.password_hash = hash_password(body.password)  # invalide aussitôt ses sessions
        ctx.cfg.save()
    return {"ok": True, "message": tr("password_reset", lang_of(request), name=user.username)}


@router.delete("/{username}", summary="Supprimer un compte")
def delete_user(username: str, request: Request, me: Principal = Depends(require_admin)) -> dict:
    ctx = ctx_of(request)
    with ctx.cfg.lock:
        auth = ctx.cfg.config.auth
        user = _get(ctx, username)
        if user.username.lower() == me.username.lower():
            raise ApiError(400, "cannot_delete_self")
        if user.role == "admin" and len(auth.admins) <= 1:
            raise ApiError(400, "last_admin")
        auth.users.remove(user)
        ctx.cfg.save()
    return {"ok": True}
