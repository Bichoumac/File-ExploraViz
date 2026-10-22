"""Routes d'authentification (connexion, déconnexion, compte courant)."""

from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..auth import COOKIE_NAME, MIN_PASSWORD_LENGTH, fingerprint, hash_password, verify_password
from ..config import USERNAME_RE, UserConfig
from ..i18n import ApiError
from .deps import Context, Principal, ctx_of, current_user, require_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


def set_session_cookie(ctx: Context, request: Request, response: Response, user: UserConfig) -> None:
    ttl = ctx.cfg.config.auth.session_hours * 3600
    token = ctx.codec.issue(user.username, fingerprint(user.password_hash), ttl)
    response.set_cookie(
        COOKIE_NAME, token, max_age=ttl, httponly=True, samesite="strict",
        secure=request.url.scheme == "https", path="/",
    )


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError(400, "password_too_short", n=MIN_PASSWORD_LENGTH)


@router.get("/state", summary="État de l'authentification et compte courant")
def auth_state(request: Request) -> dict:
    ctx = ctx_of(request)
    if ctx.settings.auth_disabled:
        return {"auth_disabled": True, "setup_required": False, "user": "anonymous", "role": "admin", "all_folders": True}
    user = ctx.session_user(request)
    return {
        "auth_disabled": False,
        "setup_required": not ctx.cfg.config.auth.users,
        "user": user.username if user else None,
        "role": user.role if user else None,
        "all_folders": user.unrestricted if user else None,
    }


@router.post("/setup", summary="Création du premier compte (administrateur), si aucun compte n'existe")
def setup(body: Credentials, request: Request, response: Response) -> dict:
    ctx = ctx_of(request)
    username = body.username.strip()
    if not re.fullmatch(USERNAME_RE, username):
        raise ApiError(400, "invalid_username")
    check_password_policy(body.password)
    with ctx.cfg.lock:
        auth = ctx.cfg.config.auth
        if auth.users:
            raise ApiError(409, "account_exists")
        user = UserConfig(username=username, password_hash=hash_password(body.password), role="admin")
        auth.users.append(user)
        ctx.cfg.save()
    set_session_cookie(ctx, request, response, user)
    return {"user": user.username, "role": user.role}


@router.post("/login", summary="Connexion")
def login(body: Credentials, request: Request, response: Response) -> dict:
    ctx = ctx_of(request)
    ip = request.client.host if request.client else "?"
    keys = (f"ip:{ip}", f"user:{body.username.lower()}")
    wait = ctx.throttle.retry_after(*keys)
    if wait > 0:
        raise ApiError(429, "too_many_attempts", headers={"Retry-After": str(int(wait) + 1)}, s=int(wait) + 1)
    user = ctx.cfg.config.auth.find(body.username.strip())
    # verify_password() hache même si le compte n'existe pas : même durée de réponse.
    ok = verify_password(body.password, user.password_hash if user else None) and user is not None
    if not ok:
        ctx.throttle.failure(*keys)
        time.sleep(0.3)
        raise ApiError(401, "bad_credentials")
    ctx.throttle.success(*keys)
    set_session_cookie(ctx, request, response, user)
    return {"user": user.username, "role": user.role}


@router.post("/logout", summary="Déconnexion")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return {"ok": True}


@router.post("/password", summary="Changer son propre mot de passe", dependencies=[Depends(require_user)])
def change_password(
    body: PasswordChange, request: Request, response: Response, me: Principal = Depends(current_user)
) -> dict:
    ctx = ctx_of(request)
    if ctx.settings.auth_disabled:
        raise ApiError(400, "auth_disabled")
    with ctx.cfg.lock:
        user = ctx.cfg.config.auth.find(me.username)
        if user is None:
            raise ApiError(401, "auth_required")
        if not verify_password(body.current, user.password_hash):
            raise ApiError(400, "bad_current_password")
        check_password_policy(body.new)
        user.password_hash = hash_password(body.new)
        ctx.cfg.save()
    set_session_cookie(ctx, request, response, user)  # les autres sessions de ce compte sont invalidées
    return {"ok": True}
