"""Orchestration des scans : verrou, thread basse priorité, progression, annulation.

C'est aussi ici que vit l'état partagé de l'application (Store en mémoire, summary,
historique), car c'est le scan qui le remplace.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .categories import build_ext_map
from .config import ConfigManager, RootConfig
from .scanner import ScanCancelled, ScanOptions, ScanProgress, scan_root
from .settings import Settings
from .store import FileFilter, RootPart, Store, load_root_part
from .summary import SummaryStore, build_summary, history_point, prime_stats
from .i18n import DEFAULT_LANG, tr
from .timeutil import iso, parse_iso

log = logging.getLogger("scan")

# Numéros de l'appel système ioprio_set selon l'architecture.
_IOPRIO_SET = {"x86_64": 251, "amd64": 251, "aarch64": 30, "arm64": 30, "armv7l": 314, "i686": 289, "i386": 289}


def lower_priority() -> None:
    """nice 19 + classe d'E/S « idle » pour le thread courant (Linux : par thread)."""
    if hasattr(os, "nice"):
        try:
            os.nice(19)
        except OSError:
            pass
    nr = _IOPRIO_SET.get(platform.machine().lower())
    if nr is None or not platform.system() == "Linux":
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        # ioprio_set(IOPRIO_WHO_PROCESS=1, 0 = thread courant, IOPRIO_CLASS_IDLE=3 << 13)
        if libc.syscall(nr, 1, 0, 3 << 13) != 0:
            log.debug("ionice idle refusé", extra={"errno": ctypes.get_errno()})
    except (OSError, AttributeError):
        pass


class AppState:
    """État en lecture pour l'API : remplacé d'un bloc après chaque scan."""

    def __init__(self) -> None:
        self.store: Store | None = None
        self.summary: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.index_ready = threading.Event()
        self.generation = 0

    def swap(self, store: Store, summary: dict[str, Any] | None) -> None:
        self.store = store
        if summary is not None:
            self.summary = summary
        self.index_ready.set()


class ScanManager:
    def __init__(self, settings: Settings, cfg: ConfigManager, state: AppState):
        self.settings = settings
        self.cfg = cfg
        self.state = state
        self.summaries = SummaryStore(settings.cache_dir)
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self.progress = ScanProgress()
        self.listeners: list[Callable[[], None]] = []
        self.info: dict[str, Any] = {"status": "idle", "message_key": None}
        self.started_ts: float | None = None
        self.expected_files: int | None = None
        self.version = 0  # incrémenté à chaque changement d'état (pour le flux SSE)

    # ----------------------------------------------------------------- état

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def index_path(self, root_id: str) -> Path:
        return self.settings.cache_dir / f"index_{root_id}.yaml"

    def last_finished_ts(self) -> float | None:
        summary = self.state.summary or {}
        return parse_iso((summary.get("scan") or {}).get("finished_at"))

    def _set(self, **kw: Any) -> None:
        self.info.update(kw)
        self.version += 1

    def _say(self, key: str | None, **params: Any) -> None:
        """Message d'état désigné par une clé de app/i18n.py, traduit dans status()."""
        self._set(message_key=key, message_params=params)

    def status(self, lang: str = DEFAULT_LANG) -> dict[str, Any]:
        p = self.progress
        now = time.time()
        elapsed = (now - self.started_ts) if (self.started_ts and self.running) else self.info.get("duration_s")
        percent, basis = None, None
        if self.running:
            if self.expected_files:
                percent, basis = min(99.0, 100.0 * p.files / self.expected_files), "previous"
            elif p.dirs:
                percent, basis = min(99.0, 100.0 * p.dirs / (p.dirs + p.pending_dirs + 1)), "estimation"
        elif self.info.get("status") == "done":
            percent, basis = 100.0, "done"
        summary = self.state.summary or {}
        info = {k: v for k, v in self.info.items() if k not in ("message_key", "message_params")}
        key = self.info.get("message_key")
        return {
            **info,
            "message": tr(key, lang, **(self.info.get("message_params") or {})) if key else None,
            "running": self.running,
            "files": p.files,
            "dirs": p.dirs,
            "bytes": p.bytes,
            "inaccessible": p.inaccessible,
            "current_root": p.current_root,
            "current_dir": p.current_dir,
            "elapsed_s": round(elapsed, 1) if elapsed else None,
            "files_per_s": round(p.files / elapsed, 1) if (elapsed and self.running) else None,
            "percent": round(percent, 1) if percent is not None else None,
            "percent_basis": basis,
            "expected_files": self.expected_files,
            "last_scan": summary.get("scan"),
            "index_ready": self.state.index_ready.is_set(),
            "version": self.version,
        }

    # ----------------------------------------------------------------- commandes

    def start(self, root_ids: list[str] | None = None, trigger: str = "manuel") -> tuple[bool, str]:
        """Lance un scan. Renvoie (lancé ?, clé du message dans app/i18n.py)."""
        if not self.state.index_ready.is_set():
            return False, "index_loading_retry"
        if not self._lock.acquire(blocking=False):
            return False, "scan_already_running"
        try:
            self.cfg.sync_roots()
            roots = [
                r for r in self.cfg.config.roots
                if r.enabled and self.cfg.available(r) and (root_ids is None or r.id in root_ids)
            ]
            if not roots:
                self._lock.release()
                if root_ids:
                    return False, "root_unavailable"
                return False, "no_roots"
            prev = (self.state.summary or {}).get("roots") or {}
            expected = [prev.get(r.id, {}).get("files") for r in roots]
            self.expected_files = sum(expected) if all(e is not None for e in expected) and sum(expected) else None
            self.progress = ScanProgress()
            self._cancel.clear()
            self.started_ts = time.time()
            self._set(
                status="running", phase="scan", trigger=trigger, roots=[r.id for r in roots],
                started_at=iso(self.started_ts), finished_at=None, duration_s=None,
            )
            self._say(None)
            self._thread = threading.Thread(target=self._run, args=(roots, trigger), name="scan", daemon=True)
            self._thread.start()
            return True, "scan_started"
        except BaseException:
            self._lock.release()  # le thread n'a pas pu démarrer
            raise

    def cancel(self) -> bool:
        if not self.running:
            return False
        self._cancel.set()
        self._set(status="cancelling")
        self._say("cancelling")
        return True

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    # ----------------------------------------------------------------- chargement initial

    def load_existing(self) -> None:
        """Au démarrage : charge les index existants en mémoire (thread d'arrière-plan)."""
        try:
            parts = []
            for root in self.cfg.config.roots:
                path = self.index_path(root.id)
                if root.enabled and path.exists():
                    t0 = time.time()
                    parts.append(load_root_part(path, root.id, root.name))
                    log.info("index chargé", extra={"root": root.id, "files": parts[-1].nfiles, "s": round(time.time() - t0, 2)})
            store = self._build(parts)
            summary = self.state.summary
            if parts and summary is None:
                summary = build_summary(store, {"status": "ok", "trigger": "reconstruction", "finished_at": iso()})
                self.summaries.save(summary, self.cfg.config.scan.history_size)
            elif parts:
                # Statistiques sans filtre pré-calculées : la première visite reste instantanée.
                for f in [FileFilter()] + [FileFilter(root=r.id) for r in store.roots]:
                    prime_stats(store, f)
            self.state.history = self.summaries.load_history()
            self.state.swap(store, summary)
        except Exception:
            log.exception("échec du chargement des index existants")
            self.state.swap(Store.empty(), self.state.summary)

    def _build(self, parts: list[RootPart]) -> Store:
        self.state.generation += 1
        store = Store.build(parts, build_ext_map(self.cfg.config.categories), self.state.generation)
        store.warm_up()
        return store

    # ----------------------------------------------------------------- thread de scan

    def _run(self, roots: list[RootConfig], trigger: str) -> None:
        lower_priority()
        cfg = self.cfg.config
        opts = ScanOptions(
            exclusions=list(cfg.scan.exclusions),
            follow_symlinks=cfg.scan.follow_symlinks,
            max_depth=cfg.scan.max_depth,
            ext_map=build_ext_map(cfg.categories),
            allowed_bases=[os.path.realpath(self.cfg.root_path(r)) for r in cfg.roots if self.cfg.available(r)],
        )
        statuses: dict[str, str] = {}
        try:
            for root in roots:
                if self._cancel.is_set():
                    raise ScanCancelled()
                footer = scan_root(
                    root.id, root.name, self.cfg.root_path(root), self.index_path(root.id), opts, self.progress, self._cancel
                )
                statuses[root.id] = footer.get("status", "ok")

            self._set(phase="indexation")
            scanned = {r.id for r in roots}
            current = self.state.store
            parts: list[RootPart] = []
            for root in self.cfg.config.roots:
                if not root.enabled:
                    continue
                part = None
                if root.id not in scanned and current is not None:
                    part = current.extract(root.id)
                if part is None and self.index_path(root.id).exists():
                    part = load_root_part(self.index_path(root.id), root.id, root.name)
                if part is not None:
                    part.name = root.name
                    parts.append(part)
            store = self._build(parts)

            self._set(phase="aggregates")
            finished = time.time()
            scan_info = {
                "started_at": iso(self.started_ts),
                "finished_at": iso(finished),
                "duration_s": round(finished - (self.started_ts or finished), 1),
                "status": "ok",
                "trigger": trigger,
                "roots": sorted(scanned),
            }
            summary = build_summary(store, scan_info)
            self.summaries.save(summary, self.cfg.config.scan.history_size)
            self.state.history = (self.state.history + [history_point(summary)])[-max(1, self.cfg.config.scan.history_size):]
            self.state.swap(store, summary)
            self._set(status="done", phase=None, finished_at=scan_info["finished_at"], duration_s=scan_info["duration_s"])
            self._say("scan_done", n=store.nfiles)
            log.info("scan terminé", extra={"files": store.nfiles, "duration_s": scan_info["duration_s"], "trigger": trigger})
        except ScanCancelled:
            self._set(status="cancelled", phase=None, finished_at=iso(), duration_s=round(time.time() - (self.started_ts or 0), 1))
            self._say("scan_cancelled")
            log.info("scan annulé")
        except Exception as exc:
            log.exception("échec du scan")
            self._set(status="error", phase=None, finished_at=iso())
            self._say("scan_error", error=str(exc))
        finally:
            self._lock.release()
            self.version += 1
            for cb in list(self.listeners):
                try:
                    cb()
                except Exception:
                    log.exception("notification de fin de scan")
