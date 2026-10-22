"""Paramètres issus des variables d'environnement (lus une seule fois au démarrage)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "oui"}


def _env_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise SystemExit(f"Variable d'environnement {name} invalide : {value!r} (entier attendu)") from exc


@dataclass(frozen=True)
class Settings:
    config_dir: Path
    cache_dir: Path
    data_dir: Path
    web_dir: Path
    host: str
    port: int
    log_level: str
    auth_disabled: bool
    admin_user: str | None
    admin_password: str | None
    scan_interval_minutes: int | None
    initial_scan: bool
    forwarded_allow_ips: str

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.yaml"

    @classmethod
    def from_env(cls) -> Settings:
        base = Path(__file__).resolve().parent.parent
        return cls(
            config_dir=Path(os.environ.get("FEV_CONFIG_DIR", "/config")),
            cache_dir=Path(os.environ.get("FEV_CACHE_DIR", "/cache")),
            data_dir=Path(os.environ.get("FEV_DATA_DIR", "/data")),
            web_dir=Path(os.environ.get("FEV_WEB_DIR", str(base / "web"))),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=_env_int("PORT", 8080) or 8080,
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            auth_disabled=_env_bool("AUTH_DISABLED", False),
            admin_user=os.environ.get("ADMIN_USER") or None,
            admin_password=os.environ.get("ADMIN_PASSWORD") or None,
            scan_interval_minutes=_env_int("SCAN_INTERVAL_MINUTES", None),
            initial_scan=_env_bool("INITIAL_SCAN", True),
            forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        )
