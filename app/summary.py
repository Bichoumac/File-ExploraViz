"""summary.yaml (agrégats pré-calculés) et historique des scans."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .aggregates import build_tree, compute_stats
from .store import FileFilter, Store
from .timeutil import iso, parse_iso
from .yamlio import read_yaml, write_yaml

log = logging.getLogger("summary")

SUMMARY_FORMAT = "fev-summary/1"
_HIST_RE = re.compile(r"^summary_(\d{8}T\d{6})\.yaml$")


def prime_stats(store: Store, f: FileFilter) -> dict[str, Any]:
    """Calcule les statistiques d'un périmètre et les place dans le cache utilisé par /api/stats."""
    sel = store.select(f)
    stats = compute_stats(store, sel)
    store.stats_cache.put((f, True), {**stats, "found": sel.found})
    return stats


def build_summary(store: Store, scan_info: dict[str, Any]) -> dict[str, Any]:
    roots: dict[str, Any] = {}
    for r in store.roots:
        f = r.footer
        roots[r.id] = {
            "name": r.name,
            "files": r.file_stop - r.file_start,
            "dirs": r.dir_stop - r.dir_start,
            "bytes": int(store.size[r.file_start : r.file_stop].sum()),
            "inaccessible": int(f.get("inaccessible", 0)),
            "symlinks_skipped": int(f.get("symlinks_skipped", 0)),
            "scanned_at": f.get("finished_at"),
            "duration_s": f.get("duration_s"),
            "status": f.get("status", "inconnu"),
        }
    stats = prime_stats(store, FileFilter())
    return {
        "format": SUMMARY_FORMAT,
        "generated_at": iso(),
        "scan": scan_info,
        "totals": {
            "files": store.nfiles,
            "dirs": store.ndirs,
            "bytes": int(store.size.sum()),
            "inaccessible": sum(v["inaccessible"] for v in roots.values()),
        },
        "roots": roots,
        "stats": stats,
        "stats_by_root": {r.id: prime_stats(store, FileFilter(root=r.id)) for r in store.roots},
        "trees": {
            r.id: build_tree(store, r.dir_start, depth=3, max_nodes=3000) for r in store.roots
        },
    }


def history_point(summary: dict[str, Any]) -> dict[str, Any]:
    """Point compact de l'historique (évolution dans le temps)."""
    scan = summary.get("scan") or {}
    stats = summary.get("stats") or {}
    return {
        "date": scan.get("finished_at") or summary.get("generated_at"),
        "files": (summary.get("totals") or {}).get("files", 0),
        "bytes": (summary.get("totals") or {}).get("bytes", 0),
        "dirs": (summary.get("totals") or {}).get("dirs", 0),
        "roots": {k: {"files": v.get("files", 0), "bytes": v.get("bytes", 0)} for k, v in (summary.get("roots") or {}).items()},
        "categories": {c["id"]: c["bytes"] for c in stats.get("categories", [])},
    }


class SummaryStore:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.path = cache_dir / "summary.yaml"
        self.history_dir = cache_dir / "history"

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            data = read_yaml(self.path)
        except Exception as exc:  # fichier corrompu : on le signale et on repart d'un scan
            log.error("summary.yaml illisible", extra={"error": str(exc)})
            return None
        if not isinstance(data, dict) or data.get("format") != SUMMARY_FORMAT:
            log.warning("summary.yaml ignoré (format inconnu)")
            return None
        return data

    def save_current(self, summary: dict[str, Any]) -> None:
        write_yaml(self.path, summary, comment="File ExploraViz - agrégats du dernier scan (généré automatiquement)")

    def save(self, summary: dict[str, Any], history_size: int) -> None:
        """Écrit summary.yaml et en ajoute une copie allégée à l'historique."""
        self.save_current(summary)
        if history_size <= 0:
            self.prune(0)
            return
        ts = parse_iso((summary.get("scan") or {}).get("finished_at")) or parse_iso(summary.get("generated_at"))
        stamp = iso(ts)[:19].replace("-", "").replace(":", "")  # AAAAMMJJTHHMMSS
        light = {k: v for k, v in summary.items() if k not in ("trees", "stats_by_root")}
        write_yaml(self.history_dir / f"summary_{stamp}.yaml", light)
        self.prune(history_size)

    def _history_files(self) -> list[Path]:
        if not self.history_dir.is_dir():
            return []
        return sorted(p for p in self.history_dir.iterdir() if _HIST_RE.match(p.name))

    def prune(self, keep: int) -> None:
        files = self._history_files()
        for p in files[: max(0, len(files) - keep)]:
            try:
                p.unlink()
            except OSError as exc:
                log.warning("suppression d'historique impossible", extra={"file": p.name, "error": str(exc)})

    def load_history(self) -> list[dict[str, Any]]:
        points = []
        for p in self._history_files():
            try:
                data = read_yaml(p)
                if isinstance(data, dict):
                    points.append(history_point(data))
            except Exception as exc:
                log.warning("historique illisible", extra={"file": p.name, "error": str(exc)})
        return points

    def clear(self) -> None:
        """Vide le cache (index + summary) ; l'historique est conservé."""
        for p in self.cache_dir.glob("index_*.yaml"):
            p.unlink(missing_ok=True)
        self.path.unlink(missing_ok=True)
