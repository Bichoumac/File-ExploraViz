"""Lancement : ``python -m app`` (uvicorn, un seul worker)."""

from __future__ import annotations

import logging

import uvicorn

from .logging_setup import setup_logging
from .main import create_app
from .settings import Settings


def main() -> None:
    settings = Settings.from_env()
    setup_logging(settings.log_level)
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        log_config=None,
        access_log=logging.getLogger().level <= logging.DEBUG,
        server_header=False,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    main()
