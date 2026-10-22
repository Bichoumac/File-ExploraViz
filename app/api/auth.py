"""Routes d'authentification."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ..i18n import ApiError
from ..auth import COOKIE_NAME, MIN_PASSWORD_LENGTH, fingerprint, hash_password, verify_password
from .deps import Context, ctx_of, require_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class PasswordChange(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


def _set_cookie(ctx: Context, request: Request, response: Response) -> None:
    auth = ctx.cfg.config.auth
    ttl = auth.session_hours * 3600
    token = ctx.codec.issue(auth.username or "", fingerprint(auth.password_hash), ttl)
    response.set_cookie(
        COOKIE_NAME, token, max_age=ttl, httponly=True, samesite="strict",
        secure=request.url.scheme == "https", path="/",
    )


def _check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError(400, "password_too_short", n=MIN_PASSWORD_LENGTH)


@router.get("/state", summary="État de l'authentification")
def auth_state(request: Request) -> dict:
    ctx = ctx_of(request)
    auth = ctx.cfg.config.auth
    return {
        "auth_disabled": ctx.settings.auth_disabled,
        "setup_required": not ctx.settings.auth_disabled and not auth.password_hash,
        "user": "anonyme" if ctx.settings.auth_disabled else ctx.session_user(request),
    }


@router.post("/setup", summary="Création du compte initial (si aucun compte n'existe)")
def setup(body: Credentials, request: Request, response: Response) -> dict:
    ctx = ctx_of(request)
    auth = ctx.cfg.config.auth
    if auth.password_hash:
        raise ApiError(409, "account_exists")
    _check_password_policy(body.password)
    auth.username = body.username.strip()
    auth.password_hash = hash_password(body.password)
    ctx.cfg.save()
    _set_cookie(ctx, request, response)
    return {"user": auth.username}


@router.post("/login", summary="Connexion")
def login(body: Credentials, request: Request, response: Response) -> dict:
    ctx = ctx_of(request)
    ip = request.client.host if request.client else "?"
    keys = (f"ip:{ip}", f"user:{body.username.lower()}")
    wait = ctx.throttle.retry_after(*keys)
    if wait > 0:
        raise ApiError(429, "too_many_attempts", headers={"Retry-After": str(int(wait) + 1)}, s=int(wait) + 1)
    auth = ctx.cfg.config.auth
    ok = verify_password(body.password, auth.password_hash) and body.username == auth.username
    if not ok:
        ctx.throttle.failure(*keys)
        time.sleep(0.3)
        raise ApiError(401, "bad_credentials")
    ctx.throttle.success(*keys)
    _set_cookie(ctx, request, response)
    return {"user": auth.username}


@router.post("/logout", summary="Déconnexion")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return {"ok": True}


@router.post("/password", summary="Changement de mot de passe", dependencies=[Depends(require_user)])
def change_password(body: PasswordChange, request: Request, response: Response) -> dict:
    ctx = ctx_of(request)
    if ctx.settings.auth_disabled:
        raise ApiError(400, "auth_disabled")
    auth = ctx.cfg.config.auth
    if not verify_password(body.current, auth.password_hash):
        raise ApiError(400, "bad_current_password")
    _check_password_policy(body.new)
    auth.password_hash = hash_password(body.new)
    ctx.cfg.save()
    _set_cookie(ctx, request, response)  # les autres sessions sont invalidées
    return {"ok": True}
