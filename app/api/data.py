"""Routes de consultation : racines, arbre, fichiers, statistiques, treemap.

Aucune de ces routes ne touche au disque : tout vient du Store en mémoire (ou, pendant le
chargement initial, des agrégats de summary.yaml).
"""

from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterator
from datetime import datetime

import numpy as np
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ..aggregates import build_tree, compute_stats, find_in_tree, subdirs
from ..categories import CATEGORY_LABELS, CATEGORY_LABELS_EN
from ..i18n import ApiError, lang_of, tr
from ..pathsec import PathError, normalize_rel
from ..store import FileFilter
from .deps import ctx_of, file_filter, require_user

router = APIRouter(prefix="/api", tags=["données"], dependencies=[Depends(require_user)])


@router.get("/roots", summary="Racines configurées et détectées")
def roots(request: Request) -> list[dict]:
    ctx = ctx_of(request)
    summary_roots = (ctx.state.summary or {}).get("roots") or {}
    store = ctx.state.store
    out = []
    for r in ctx.cfg.config.roots:
        info = summary_roots.get(r.id, {})
        out.append(
            {
                "id": r.id,
                "name": r.name,
                "folder": r.folder,
                "enabled": r.enabled,
                "available": ctx.cfg.available(r),
                "indexed": store is not None and store.root_index(r.id) is not None,
                "files": info.get("files"),
                "dirs": info.get("dirs"),
                "bytes": info.get("bytes"),
                "scanned_at": info.get("scanned_at"),
                "inaccessible": info.get("inaccessible"),
            }
        )
    return out


def _rel(path: str) -> str:
    try:
        return normalize_rel(path)
    except PathError as exc:
        raise ApiError(400, "path_refused") from exc


@router.get("/tree", summary="Sous-dossiers directs d'un dossier")
def tree(request: Request, root: str = Query(...), path: str = Query("")) -> dict:
    ctx = ctx_of(request)
    store = ctx.store_or_503()
    rel = _rel(path)
    d = store.resolve_dir(root, rel)
    if d is None:
        raise ApiError(404, "dir_not_found")
    return {"root": root, "path": rel, "dirs": subdirs(store, d)}


@router.get("/files", summary="Liste filtrée, triée et paginée")
def files(
    request: Request,
    f: FileFilter = Depends(file_filter),
    sort: str = Query("path", description="name|path|ext|category|size|mtime|ctime, préfixe - pour décroissant"),
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1, le=1000),
) -> dict:
    ctx = ctx_of(request)
    store = ctx.store_or_503()
    sel = store.select(f)
    rows = store.sorted_rows(f, sort)
    total = int(len(rows))
    size = page_size or ctx.cfg.config.ui.page_size
    pages = max(1, math.ceil(total / size))
    page = min(page, pages)
    chunk = rows[(page - 1) * size : page * size]
    return {
        "total": total,
        "page": page,
        "page_size": size,
        "pages": pages,
        "sort": sort,
        "found": sel.found,
        "rows": [store.row_dict(i) for i in chunk.tolist()],
    }


def _fmt_ts(ts: int) -> str:
    try:
        return datetime.fromtimestamp(ts).isoformat(sep=" ", timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


@router.get("/files/export.csv", summary="Export CSV du résultat filtré (séparateur ;)")
def export_csv(
    request: Request,
    f: FileFilter = Depends(file_filter),
    sort: str = Query("path"),
) -> StreamingResponse:
    ctx = ctx_of(request)
    store = ctx.store_or_503()
    rows = store.sorted_rows(f, sort)
    lang = lang_of(request)
    labels = CATEGORY_LABELS if lang == "fr" else CATEGORY_LABELS_EN

    def generate() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        buf.write("﻿")  # BOM : Excel détecte l'UTF-8
        writer.writerow(tr("csv_header", lang).split(";"))
        for start in range(0, len(rows), 5000):
            for i in rows[start : start + 5000].tolist():
                r = store.row_dict(i)
                writer.writerow(
                    [r["root"], r["path"], r["name"], r["ext"], labels[r["category"]], r["size"],
                     _fmt_ts(r["mtime"]), _fmt_ts(r["ctime"])]
                )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
        if buf.tell():
            yield buf.getvalue()

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="exploraviz-{stamp}.csv"'},
    )


@router.get("/extensions", summary="Extensions présentes (autocomplétion)")
def extensions(request: Request, root: str | None = None, prefix: str = "", limit: int = Query(30, ge=1, le=200)) -> list[dict]:
    ctx = ctx_of(request)
    store = ctx.store_or_503()
    sel = store.select(FileFilter(root=root or None))
    ids = sel.take(store.ext_id)
    counts = np.bincount(ids, minlength=len(store.exts)) if len(ids) else np.zeros(len(store.exts), np.int64)
    p = prefix.strip().lower().lstrip(".")
    found = [(store.exts[i], int(c)) for i, c in enumerate(counts.tolist()) if c and store.exts[i] and store.exts[i].startswith(p)]
    found.sort(key=lambda x: -x[1])
    return [{"ext": e, "files": c} for e, c in found[:limit]]


@router.get("/stats", summary="Statistiques (mêmes filtres que /api/files)")
def stats(request: Request, f: FileFilter = Depends(file_filter), detail: bool = Query(True)) -> dict:
    ctx = ctx_of(request)
    store = ctx.state.store
    summary = ctx.state.summary or {}
    if store is None:
        # Chargement initial : on sert les agrégats pré-calculés quand le filtre le permet.
        if not f.has_file_filters and not f.path:
            pre = summary.get("stats") if f.root is None else (summary.get("stats_by_root") or {}).get(f.root)
            if pre:
                return {**pre, "source": "summary", "found": True}
        raise ApiError(503, "index_loading")
    key = (f, detail)
    cached = store.stats_cache.get(key)
    if cached is None:
        sel = store.select(f)
        cached = store.stats_cache.put(key, {**compute_stats(store, sel, detail=detail), "found": sel.found})
    return {**cached, "source": "index"}


@router.get("/stats/history", summary="Évolution (historique des scans)")
def history(request: Request) -> list[dict]:
    return ctx_of(request).state.history


@router.get("/treemap", summary="Arbre agrégé pour la treemap")
def treemap(
    request: Request,
    root: str = Query(...),
    path: str = Query(""),
    depth: int = Query(3, ge=1, le=6),
) -> dict:
    ctx = ctx_of(request)
    rel = _rel(path)
    store = ctx.state.store
    if store is None:
        tree_ = ((ctx.state.summary or {}).get("trees") or {}).get(root)
        node = find_in_tree(tree_, rel) if tree_ else None
        if node is None:
            raise ApiError(503, "index_loading")
        return {**node, "root": root}
    d = store.resolve_dir(root, rel)
    if d is None:
        raise ApiError(404, "dir_not_found")
    key = ("treemap", d, depth)
    tree_ = store.stats_cache.get(key)
    if tree_ is None:
        tree_ = store.stats_cache.put(key, build_tree(store, d, depth=depth))
    return tree_
