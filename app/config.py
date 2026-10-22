"""Configuration persistée dans /config/config.yaml, validée par Pydantic."""

from __future__ import annotations

import logging
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .categories import CATEGORY_IDS, DEFAULT_MAPPING, normalize_ext
from .settings import Settings
from .yamlio import read_yaml, write_yaml

log = logging.getLogger("config")

DEFAULT_EXCLUSIONS = [
    ".zfs", ".snapshot", "@eaDir", "#recycle", "#snapshot", ".Trash*", ".Trashes",
    "$RECYCLE.BIN", "System Volume Information", ".Spotlight-V100", ".fseventsd",
    "node_modules", "__pycache__", ".git", "*.tmp", "Thumbs.db", ".DS_Store",
]

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ROOT_ID_RE = r"^[A-Za-z0-9._-]{1,64}$"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RootConfig(Strict):
    id: str = Field(pattern=ROOT_ID_RE, description="Identifiant (nom du fichier d'index)")
    folder: str = Field(min_length=1, description="Nom du dossier sous /data")
    name: str = Field(min_length=1, max_length=100, description="Nom d'affichage")
    enabled: bool = True

    @field_validator("folder")
    @classmethod
    def _folder(cls, v: str) -> str:
        if "/" in v or "\\" in v or v in (".", "..") or "\x00" in v:
            raise ValueError("doit être un nom de dossier direct de /data")
        return v


class OffPeak(Strict):
    enabled: bool = False
    start: str = "01:00"
    end: str = "06:00"

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("format HH:MM attendu (ex. 01:30)")
        return v


class ScanConfig(Strict):
    interval_minutes: int = Field(15, ge=0, le=7 * 24 * 60)
    offpeak: OffPeak = Field(default_factory=OffPeak)
    exclusions: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUSIONS))
    follow_symlinks: bool = False
    max_depth: int = Field(0, ge=0, le=1000)
    history_size: int = Field(30, ge=0, le=1000)

    @field_validator("exclusions")
    @classmethod
    def _exclusions(cls, v: list[str]) -> list[str]:
        out = [p.strip() for p in v if p and p.strip()]
        for p in out:
            if "\x00" in p:
                raise ValueError("motif invalide")
        return out


class UIConfig(Strict):
    page_size: int = Field(100, ge=10, le=1000)


class AuthConfig(Strict):
    username: str | None = Field(None, min_length=1, max_length=64)
    password_hash: str | None = None
    session_hours: int = Field(168, ge=1, le=24 * 365)
    secret: str = ""


class AppConfig(Strict):
    version: int = 1
    roots: list[RootConfig] = Field(default_factory=list)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    categories: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_MAPPING.items()})
    auth: AuthConfig = Field(default_factory=AuthConfig)

    @field_validator("categories")
    @classmethod
    def _categories(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        seen: dict[str, str] = {}
        out: dict[str, list[str]] = {}
        for cid, exts in v.items():
            if cid not in CATEGORY_IDS or cid == "other":
                raise ValueError(f"catégorie inconnue « {cid} » (attendu : {', '.join(CATEGORY_IDS[:-1])})")
            clean: list[str] = []
            for ext in exts:
                e = normalize_ext(str(ext))
                if not e or not re.fullmatch(r"[^\s/\\]{1,32}", e):
                    raise ValueError(f"extension invalide « {ext} » dans {cid}")
                if e in seen and seen[e] != cid:
                    raise ValueError(f"l'extension « {e} » est à la fois dans {seen[e]} et {cid}")
                if e not in clean:
                    clean.append(e)
                seen[e] = cid
            out[cid] = clean
        return out

    @model_validator(mode="after")
    def _unique_roots(self) -> AppConfig:
        ids = [r.id for r in self.roots]
        if len(ids) != len(set(ids)):
            raise ValueError("identifiants de racines en double")
        return self


def format_validation_error(exc: ValidationError) -> list[str]:
    """Messages lisibles : ``scan.interval_minutes : doit être ≥ 0``."""
    messages = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "configuration"
        msg = err["msg"].removeprefix("Value error, ")
        ctx = err.get("ctx") or {}
        typ = err["type"]
        if typ == "greater_than_equal":
            msg = f"doit être ≥ {ctx.get('ge')}"
        elif typ == "less_than_equal":
            msg = f"doit être ≤ {ctx.get('le')}"
        elif typ == "extra_forbidden":
            msg = "clé inconnue"
        elif typ == "missing":
            msg = "valeur obligatoire"
        elif typ in ("int_parsing", "int_type"):
            msg = "nombre entier attendu"
        elif typ in ("bool_parsing", "bool_type"):
            msg = "booléen attendu (true/false)"
        elif typ == "string_pattern_mismatch":
            msg = "caractères autorisés : lettres, chiffres, . _ -"
        messages.append(f"{loc} : {msg}")
    return messages


class ConfigError(Exception):
    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")[:48]
    return s or "racine"


class ConfigManager:
    """Charge, valide et sauvegarde la configuration (thread-safe)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.path = settings.config_file
        self._lock = threading.RLock()
        self._config = AppConfig()

    @property
    def config(self) -> AppConfig:
        return self._config

    def load(self) -> AppConfig:
        with self._lock:
            if self.path.exists():
                raw = read_yaml(self.path) or {}
                try:
                    cfg = AppConfig.model_validate(raw)
                except ValidationError as exc:
                    raise ConfigError(format_validation_error(exc)) from exc
                created = False
            else:
                cfg = AppConfig()
                if self.settings.scan_interval_minutes is not None:
                    cfg.scan.interval_minutes = max(0, self.settings.scan_interval_minutes)
                created = True
            if not cfg.auth.secret:
                cfg.auth.secret = secrets.token_urlsafe(48)
                created = True
            self._config = cfg
            if self.sync_roots() or created:
                self.save()
            return cfg

    def save(self, cfg: AppConfig | None = None) -> None:
        with self._lock:
            if cfg is not None:
                self._config = cfg
            write_yaml(
                self.path,
                self._config.model_dump(mode="json"),
                comment="File ExploraViz - configuration (modifiable depuis l'interface)",
            )

    def update(self, data: dict[str, Any]) -> AppConfig:
        """Valide puis enregistre une nouvelle configuration complète (hors section auth)."""
        with self._lock:
            merged = self._config.model_dump(mode="json")
            for key, value in data.items():
                if key == "auth":
                    # Seule la durée de session est modifiable par ce biais.
                    if isinstance(value, dict) and "session_hours" in value:
                        merged["auth"]["session_hours"] = value["session_hours"]
                    continue
                merged[key] = value
            try:
                cfg = AppConfig.model_validate(merged)
            except ValidationError as exc:
                raise ConfigError(format_validation_error(exc)) from exc
            self._config = cfg
            self.sync_roots()
            self.save()
            return cfg

    def detect_folders(self) -> list[str]:
        data = self.settings.data_dir
        try:
            return sorted(
                e.name for e in data.iterdir() if not e.name.startswith(".") and e.is_dir()
            )
        except OSError:
            return []

    def sync_roots(self) -> bool:
        """Ajoute les dossiers nouvellement montés sous /data. Renvoie True si modifié."""
        with self._lock:
            known = {r.folder for r in self._config.roots}
            ids = {r.id for r in self._config.roots}
            changed = False
            for folder in self.detect_folders():
                if folder in known:
                    continue
                rid, n = _slug(folder), 2
                while rid in ids:
                    rid, n = f"{_slug(folder)}-{n}", n + 1
                self._config.roots.append(RootConfig(id=rid, folder=folder, name=folder))
                ids.add(rid)
                changed = True
                log.info("nouvelle racine détectée", extra={"root": rid, "folder": folder})
            return changed

    def root_path(self, root: RootConfig) -> Path:
        return self.settings.data_dir / root.folder

    def available(self, root: RootConfig) -> bool:
        return self.root_path(root).is_dir()

    def public_dict(self) -> dict[str, Any]:
        data = self._config.model_dump(mode="json")
        data["auth"] = {"username": data["auth"]["username"], "session_hours": data["auth"]["session_hours"]}
        return data
