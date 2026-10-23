"""Parcours d'une racine avec ``os.scandir`` et écriture en flux de ``index_<racine>.yaml``.

* Itératif (pile explicite) : aucune récursion, profondeur arbitraire.
* Ordre préfixe, enfants triés par nom : le sous-arbre d'un dossier est contigu dans l'index.
* Les informations de ``DirEntry`` (type d'entrée sans appel système, ``stat`` mis en cache)
  sont réutilisées : un seul ``stat`` par fichier, aucun par dossier.
* Lecture seule stricte : on n'ouvre jamais un fichier, on ne fait que lister et ``stat``.
* Le ``stat`` passe par ``fsstat`` (``statx`` sous Linux) pour obtenir la vraie date de création.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import stat as stat_mod
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .categories import CATEGORY_IDS, OTHER, ext_of
from .fsstat import BIRTH_SOURCE, make_stat
from .pathsec import is_within, within_any
from .timeutil import iso
from .yamlio import IndexWriter, clean_text

log = logging.getLogger("scanner")


class ScanCancelled(Exception):
    pass


@dataclass
class ScanProgress:
    """Compteurs mis à jour par le thread de scan, lus (sans verrou) par l'API."""

    files: int = 0
    dirs: int = 0
    bytes: int = 0
    inaccessible: int = 0
    symlinks_skipped: int = 0
    pending_dirs: int = 0
    current_root: str = ""
    current_dir: str = ""


@dataclass
class ScanOptions:
    exclusions: list[str] = field(default_factory=list)
    follow_symlinks: bool = False
    max_depth: int = 0  # 0 = illimité
    ext_map: dict[str, int] = field(default_factory=dict)
    allowed_bases: list[str] = field(default_factory=list)  # racines réelles (liens symboliques)


def compile_exclusions(patterns: list[str]) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """Motifs sans « / » : comparés au nom ; avec « / » : au chemin relatif à la racine."""
    by_name = [fnmatch.translate(p) for p in patterns if "/" not in p]
    by_path = [fnmatch.translate(p.strip("/")) for p in patterns if "/" in p]
    name_rx = re.compile("|".join(by_name)) if by_name else None
    path_rx = re.compile("|".join(by_path)) if by_path else None
    return name_rx, path_rx


def _errmsg(exc: OSError) -> str:
    return exc.strerror or str(exc) or exc.__class__.__name__


def scan_root(
    root_id: str,
    root_name: str,
    root_path: str | os.PathLike[str],
    out_path: Path,
    opts: ScanOptions,
    progress: ScanProgress,
    cancel: threading.Event,
) -> dict:
    """Scanne une racine et écrit son index de façon atomique. Renvoie le pied de l'index."""
    started = time.time()
    base = os.path.realpath(root_path)
    name_rx, path_rx = compile_exclusions(opts.exclusions)
    ext_map = opts.ext_map
    cat_names = CATEGORY_IDS
    follow = opts.follow_symlinks
    max_depth = opts.max_depth
    bases = opts.allowed_bases or [base]
    stat_of = make_stat()

    n_files = n_dirs = n_bytes = n_inacc = n_links = n_birth = 0
    visited_links: set[str] = set()
    progress.current_root = root_id

    header = {
        "root_id": root_id,
        "root_name": root_name,
        "root_path": str(root_path),
        "started_at": iso(started),
    }
    stack: list[tuple[str, str, int]] = [(base, "", 0)]

    with IndexWriter(out_path, header) as writer:
        while stack:
            if cancel.is_set():
                raise ScanCancelled()
            abspath, rel, depth = stack.pop()
            progress.current_dir = rel or "/"
            progress.pending_dirs = len(stack)

            files: list[tuple[str, str, str, int, int, int, int | None]] = []
            errors: list[tuple[str, str]] = []
            subdirs: list[tuple[str, str]] = []  # (nom, chemin absolu à parcourir)
            dir_error: str | None = None

            try:
                with os.scandir(abspath) as it:
                    for entry in it:
                        name = entry.name
                        if name_rx is not None and name_rx.match(name):
                            continue
                        if path_rx is not None and path_rx.match(f"{rel}/{name}" if rel else name):
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                subdirs.append((name, entry.path))
                                continue
                            if entry.is_file(follow_symlinks=False):
                                st = stat_of(entry, False)
                            elif entry.is_symlink():
                                if not follow:
                                    n_links += 1
                                    continue
                                target = os.path.realpath(entry.path)
                                # Jamais de lien « sortant » : la cible doit rester dans une racine.
                                if not within_any(target, bases):
                                    n_links += 1
                                    continue
                                st = stat_of(target, True)
                                if stat_mod.S_ISDIR(st[0]):
                                    # Pas de boucle : ni cible déjà visitée, ni ancêtre du dossier courant.
                                    if target in visited_links or is_within(os.path.realpath(abspath), target):
                                        n_links += 1
                                        continue
                                    visited_links.add(target)
                                    subdirs.append((name, target))
                                    continue
                                if not stat_mod.S_ISREG(st[0]):
                                    continue
                            else:
                                continue  # socket, FIFO, périphérique : ignorés
                        except OSError as exc:
                            errors.append((clean_text(name), _errmsg(exc)))
                            continue
                        clean = clean_text(name)
                        ext = ext_of(clean)
                        _, size, mtime, ctime, btime = st
                        files.append((clean, ext, cat_names[ext_map.get(ext, OTHER)], size, mtime, ctime, btime))
                        n_bytes += size
                        if btime is not None:
                            n_birth += 1
            except OSError as exc:
                dir_error = _errmsg(exc)
                n_inacc += 1
                log.debug("dossier inaccessible", extra={"root": root_id, "path": rel, "error": dir_error})

            truncated = False
            if max_depth and depth >= max_depth and subdirs:
                truncated, subdirs = True, []

            files.sort(key=lambda f: (f[0].lower(), f[0]))
            writer.write_dir(clean_text(rel), files, error=dir_error, errors=errors, truncated=truncated)

            n_dirs += 1
            n_files += len(files)
            n_inacc += len(errors)
            progress.dirs += 1
            progress.files += len(files)
            progress.bytes += sum(f[3] for f in files)
            progress.inaccessible += len(errors) + (1 if dir_error else 0)
            progress.symlinks_skipped = n_links

            # Empilés en ordre inverse pour être dépilés dans l'ordre alphabétique.
            subdirs.sort(key=lambda s: s[0], reverse=True)
            for name, path in subdirs:
                stack.append((path, f"{rel}/{name}" if rel else name, depth + 1))

        finished = time.time()
        footer = {
            "finished_at": iso(finished),
            "status": "ok",
            "files": n_files,
            "dirs": n_dirs,
            "bytes": n_bytes,
            "inaccessible": n_inacc,
            "symlinks_skipped": n_links,
            "birth_times": n_birth,
            "duration_s": round(finished - started, 3),
        }
        writer.close(footer)
    log.info(
        "racine scannée",
        extra={
            "root": root_id, "files": n_files, "dirs": n_dirs, "bytes": n_bytes, "duration_s": footer["duration_s"],
            "birth_times": n_birth, "birth_source": BIRTH_SOURCE,
        },
    )
    return footer
