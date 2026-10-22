"""Création de l'application FastAPI."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import auth as auth_routes
from .api import config as config_routes
from .api import data as data_routes
from .api import scan as scan_routes
from .api import users as users_routes
from .api.deps import Context
from .auth import LoginThrottle, hash_password
from .config import ConfigError, ConfigManager, UserConfig
from .i18n import ApiError, lang_of, tr
from .scan_manager import AppState, ScanManager
from .scheduler import Scheduler
from .settings import Settings

log = logging.getLogger("app")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def _init_admin(cfg: ConfigManager, settings: Settings) -> None:
    """ADMIN_USER / ADMIN_PASSWORD créent un administrateur seulement s'il n'en existe aucun.

    Cas d'usage : premier démarrage, ou récupération quand le seul administrateur a été retiré de
    config.yaml (mot de passe oublié). Un compte existant n'est jamais modifié.
    """
    auth = cfg.config.auth
    if auth.admins or not settings.admin_password:
        return
    username = settings.admin_user or "admin"
    if auth.find(username) is not None:
        log.error("ADMIN_USER désigne un compte existant non administrateur : choisissez un autre identifiant",
                  extra={"user": username})
        return
    user = UserConfig(username=username, password_hash=hash_password(settings.admin_password), role="admin")
    auth.users.append(user)
    cfg.save()
    log.info("compte administrateur initialisé depuis l'environnement", extra={"user": user.username})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    cfg = ConfigManager(settings)
    try:
        cfg.load()
    except ConfigError as exc:
        for msg in exc.messages:
            log.error("config.yaml invalide", extra={"detail": msg})
        raise SystemExit(f"Configuration invalide ({settings.config_file}) : " + " ; ".join(exc.messages)) from exc
    _init_admin(cfg, settings)
    if settings.auth_disabled:
        log.warning("authentification désactivée (AUTH_DISABLED=true) : à n'utiliser que derrière un proxy authentifié")

    state = AppState()
    scans = ScanManager(settings, cfg, state)
    state.summary = scans.summaries.load()
    scheduler = Scheduler(cfg, scans, initial_scan=settings.initial_scan)
    ctx = Context(settings, cfg, state, scans, scheduler, LoginThrottle())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        threading.Thread(target=scans.load_existing, name="index-load", daemon=True).start()
        task = asyncio.create_task(scheduler.run())
        log.info("File ExploraViz démarré", extra={"version": __version__, "port": settings.port})
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if scans.cancel():
            scans.wait(10)

    app = FastAPI(
        title="File ExploraViz",
        version=__version__,
        description="Explorateur et visualiseur de fichiers auto-hébergé (lecture seule).",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.ctx = ctx
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        path = request.url.path
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "same-origin")
        h.setdefault("X-Frame-Options", "DENY")
        if path.startswith("/api/docs"):
            pass  # Swagger UI charge ses ressources depuis un CDN : pas de CSP stricte ici
        else:
            h.setdefault("Content-Security-Policy", CSP)
        if path.startswith("/vendor/"):
            h["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.startswith("/api/") or path == "/healthz":
            h.setdefault("Cache-Control", "no-store")
        else:
            h.setdefault("Cache-Control", "no-cache")  # revalidation par ETag / Last-Modified
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        """Traduit le message d'erreur dans la langue de l'utilisateur (cookie fev_lang)."""
        detail = tr(exc.key, lang_of(request), **exc.params)
        return JSONResponse({"detail": detail, "code": exc.key}, status_code=exc.status_code, headers=exc.headers)

    @app.get("/healthz", tags=["système"], summary="Healthcheck")
    def healthz() -> dict:
        return {"status": "ok", "version": __version__, "index_ready": state.index_ready.is_set(), "scanning": scans.running}

    app.include_router(auth_routes.router)
    app.include_router(data_routes.router)
    app.include_router(scan_routes.router)
    app.include_router(config_routes.router)
    app.include_router(users_routes.router)

    web = settings.web_dir
    if (web / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")
        app.mount("/vendor", StaticFiles(directory=web / "vendor"), name="vendor")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(web / "index.html")

        @app.get("/favicon.svg", include_in_schema=False)
        def favicon() -> FileResponse:
            return FileResponse(web / "favicon.svg", media_type="image/svg+xml")

    return app
