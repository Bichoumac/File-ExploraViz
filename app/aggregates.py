"""Statistiques et arbre treemap, calculés en numpy sur une sélection du Store."""

from __future__ import annotations

import time
from collections import deque
from typing import Any

import numpy as np

from .categories import CATEGORY_IDS, CATEGORY_LABELS, NCAT
from .store import FLAG_ERROR, Selection, Store

KB, MB, GB, TB = 1024, 1024**2, 1024**3, 1024**4
SIZE_EDGES = [0, 1, KB, 10 * KB, 100 * KB, MB, 10 * MB, 100 * MB, GB, 10 * GB, 100 * GB]
SIZE_LABELS = [
    "0 o", "< 1 Ko", "1–10 Ko", "10–100 Ko", "100 Ko–1 Mo", "1–10 Mo", "10–100 Mo",
    "100 Mo–1 Go", "1–10 Go", "10–100 Go", "≥ 100 Go",
]
DAY = 86400
AGE_EDGES = [0, 7 * DAY, 30 * DAY, 365 * DAY, 2 * 365 * DAY, 5 * 365 * DAY, 10 * 365 * DAY]
AGE_LABELS = ["< 7 jours", "7–30 jours", "1–12 mois", "1–2 ans", "2–5 ans", "5–10 ans", "> 10 ans"]


def _i(x: Any) -> int:
    return int(round(float(x)))


def _top(values: np.ndarray, n: int) -> np.ndarray:
    """Indices des ``n`` plus grandes valeurs, triés par valeur décroissante."""
    if len(values) == 0:
        return np.zeros(0, dtype=np.int64)
    if len(values) > n:
        part = np.argpartition(-values, n - 1)[:n]
    else:
        part = np.arange(len(values))
    return part[np.argsort(-values[part], kind="stable")]


def _dir_ref(store: Store, d: int) -> dict[str, Any]:
    return {"root": store.roots[int(store.dir_root[d])].id, "path": store.dir_path[d]}


def compute_stats(store: Store, sel: Selection, *, now: float | None = None, detail: bool = True) -> dict[str, Any]:
    """Statistiques d'une sélection. ``detail=False`` : bandeau uniquement (totaux + catégories)."""
    now = time.time() if now is None else now
    sizes = sel.take(store.size)
    cats = sel.take(store.cat)
    n = int(len(sizes))
    total = int(sizes.sum()) if n else 0

    cat_count = np.bincount(cats, minlength=NCAT) if n else np.zeros(NCAT, np.int64)
    cat_bytes = np.bincount(cats, weights=sizes, minlength=NCAT) if n else np.zeros(NCAT)
    categories = [
        {"id": cid, "label": CATEGORY_LABELS[cid], "files": int(cat_count[i]), "bytes": _i(cat_bytes[i])}
        for i, cid in enumerate(CATEGORY_IDS)
    ]
    fdir = sel.take(store.file_dir)
    scope_ids = sel.dir_ids()
    scope_dirs = len(scope_ids)
    totals: dict[str, Any] = {
        "files": n,
        "bytes": total,
        "dirs": int(scope_dirs),
        "dirs_with_files": int(len(np.unique(fdir))) if n else 0,
    }
    result: dict[str, Any] = {"totals": totals, "categories": categories}
    if not detail:
        return result

    mt = sel.take(store.mtime)
    exts = sel.take(store.ext_id)
    rows = sel.rows()
    aggs = store.dir_aggs()

    # Totaux complémentaires
    totals.update(
        {
            "empty_files": int((sizes == 0).sum()),
            "hidden_files": int(sel.take(store.hidden()).sum()) if n else 0,
            "mean_size": _i(sizes.mean()) if n else 0,
            "median_size": _i(np.median(sizes)) if n else 0,
            "oldest_mtime": int(mt.min()) if n else None,
            "newest_mtime": int(mt.max()) if n else None,
            "future_mtime": int((mt > now + DAY).sum()),
            # Fichiers dont la « date de création » est la vraie (btime), et non st_ctime.
            "birth_times": int(sel.take(store.birth).sum()) if n else 0,
            "extensions": int(len(np.unique(exts))) if n else 0,
            "max_depth": int(store.dir_depth[scope_ids].max(initial=0) - store.dir_depth[sel.dir_lo])
            if scope_dirs
            else 0,
        }
    )

    # Extensions
    ne = len(store.exts)
    e_count = np.bincount(exts, minlength=ne) if n else np.zeros(ne, np.int64)
    e_bytes = np.bincount(exts, weights=sizes, minlength=ne) if n else np.zeros(ne)

    def ext_rows(order: np.ndarray) -> list[dict[str, Any]]:
        out = []
        for e in order.tolist():
            if e_count[e] == 0:
                continue
            ext = store.exts[e]
            out.append(
                {"ext": ext or "(sans)", "files": int(e_count[e]), "bytes": _i(e_bytes[e]),
                 "category": CATEGORY_IDS[int(store.ext_cat[e])]}
            )
        return out

    result["extensions_by_size"] = ext_rows(_top(e_bytes, 20))
    result["extensions_by_count"] = ext_rows(_top(e_count.astype(np.float64), 20))

    # Histogramme des tailles (tranches logarithmiques)
    bucket = np.searchsorted(np.array(SIZE_EDGES[1:]), sizes, side="right")
    b_count = np.bincount(bucket, minlength=len(SIZE_LABELS)) if n else np.zeros(len(SIZE_LABELS), np.int64)
    b_bytes = np.bincount(bucket, weights=sizes, minlength=len(SIZE_LABELS)) if n else np.zeros(len(SIZE_LABELS))
    result["size_histogram"] = [
        {"label": SIZE_LABELS[i], "files": int(b_count[i]), "bytes": _i(b_bytes[i])} for i in range(len(SIZE_LABELS))
    ]

    # Années de modification et ancienneté
    if n:
        # Bornées : une date aberrante (an 30000...) ne doit pas créer des milliers de tranches.
        years = np.clip(mt.astype("datetime64[s]").astype("datetime64[Y]").astype(np.int64) + 1970, 1900, 2200)
        y0 = int(years.min())
        y_count = np.bincount(years - y0)
        y_bytes = np.bincount(years - y0, weights=sizes)
        result["years"] = [
            {"year": y0 + i, "files": int(c), "bytes": _i(y_bytes[i])} for i, c in enumerate(y_count.tolist()) if c
        ]
        age = np.searchsorted(np.array(AGE_EDGES[1:]), np.maximum(now - mt, 0), side="right")
        a_count = np.bincount(age, minlength=len(AGE_LABELS))
        a_bytes = np.bincount(age, weights=sizes, minlength=len(AGE_LABELS))
    else:
        result["years"] = []
        a_count, a_bytes = np.zeros(len(AGE_LABELS), np.int64), np.zeros(len(AGE_LABELS))
    result["age"] = [
        {"label": AGE_LABELS[i], "files": int(a_count[i]), "bytes": _i(a_bytes[i])} for i in range(len(AGE_LABELS))
    ]

    # Plus gros fichiers
    result["largest_files"] = [
        {**store.row_dict(int(rows[i]))} for i in _top(sizes, 50).tolist()
    ]

    # Plus gros dossiers (contenu direct correspondant aux filtres) et dossiers les plus peuplés
    if n:
        local = fdir.astype(np.int64) - sel.dir_lo
        d_bytes = np.bincount(local, weights=sizes)
        d_count = np.bincount(local)
    else:
        d_bytes, d_count = np.zeros(0), np.zeros(0, np.int64)

    def dir_rows(order: np.ndarray) -> list[dict[str, Any]]:
        out = []
        for j in order.tolist():
            if d_count[j] == 0:
                continue
            d = sel.dir_lo + j
            out.append(
                {**_dir_ref(store, d), "bytes": _i(d_bytes[j]), "files": int(d_count[j]), "total_bytes": int(aggs.total_size[d])}
            )
        return out

    result["largest_dirs"] = dir_rows(_top(d_bytes, 20))
    result["busiest_dirs"] = dir_rows(_top(d_count.astype(np.float64), 10))

    # Doublons probables : même nom (sans la casse) et même taille, fichiers non vides
    result["duplicates"] = _duplicates(store, rows, sizes)

    # Dossiers vides et éléments inaccessibles : dépendent du périmètre, pas des filtres fichiers
    ids = scope_ids
    empty = ids[(aggs.direct_count[ids] == 0) & (store.dir_end[ids] == ids + 1)] if len(ids) else np.zeros(0, np.int64)
    in_scope = set(ids.tolist()) if sel.dir_mask is not None else None
    dl, dh = sel.dir_lo, sel.dir_hi

    def scoped(d: int) -> bool:
        return dl <= d < dh and (in_scope is None or d in in_scope)

    result["empty_dirs"] = {"count": int(len(empty)), "sample": [_dir_ref(store, int(d)) for d in empty[:50].tolist()]}
    bad_dirs = [d for d, _ in store.dir_error.items() if scoped(d)]
    bad_entries = [(d, nm, msg) for d, nm, msg in store.entry_errors if scoped(d)]
    sample = [{**_dir_ref(store, d), "error": store.dir_error[d], "kind": "dir"} for d in bad_dirs[:100]]
    for d, nm, msg in bad_entries[: max(0, 100 - len(sample))]:
        ref = _dir_ref(store, d)
        ref["path"] = f"{ref['path']}/{nm}" if ref["path"] else nm
        sample.append({**ref, "error": msg, "kind": "file"})
    result["inaccessible"] = {"count": len(bad_dirs) + len(bad_entries), "sample": sample}
    result["truncated_dirs"] = int((store.dir_flags[ids] & 2).astype(bool).sum())
    return result


def _duplicates(store: Store, rows: np.ndarray, sizes: np.ndarray, top: int = 20) -> dict[str, Any]:
    empty = {"groups": 0, "files": 0, "recoverable_bytes": 0, "top": []}
    keep = sizes > 0
    if not keep.any():
        return empty
    r, s = rows[keep], sizes[keep]
    h = store.name_hash()[r]
    order = np.lexsort((h, s))
    r, s, h = r[order], s[order], h[order]
    same = (s[1:] == s[:-1]) & (h[1:] == h[:-1])
    if not same.any():
        return empty
    # Début de chaque groupe (série de lignes identiques) de taille >= 2
    starts = np.flatnonzero(np.concatenate(([True], ~same)))
    lengths = np.diff(np.concatenate((starts, [len(r)])))
    multi = lengths >= 2
    g_start, g_len = starts[multi], lengths[multi]
    g_size = s[g_start]
    recoverable = g_size * (g_len - 1)
    best = _top(recoverable.astype(np.float64), top)
    top_groups = []
    for g in best.tolist():
        members = r[g_start[g] : g_start[g] + g_len[g]]
        top_groups.append(
            {
                "name": store.name(int(members[0])),
                "size": int(g_size[g]),
                "count": int(g_len[g]),
                "recoverable_bytes": int(recoverable[g]),
                "paths": [
                    {"root": store.roots[int(store.dir_root[store.file_dir[m]])].id, "path": store.file_path(int(m))}
                    for m in members[:10].tolist()
                ],
            }
        )
    return {
        "groups": int(len(g_start)),
        "files": int(g_len.sum()),
        "recoverable_bytes": int(recoverable.sum()),
        "top": top_groups,
    }


# --------------------------------------------------------------------------- treemap


def _cats_dict(row: np.ndarray) -> dict[str, int]:
    return {CATEGORY_IDS[i]: _i(v) for i, v in enumerate(row.tolist()) if v > 0}


def build_tree(
    store: Store,
    d: int,
    *,
    depth: int = 3,
    max_nodes: int = 4000,
    max_dirs: int = 150,
    max_files: int = 60,
) -> dict[str, Any]:
    """Arbre agrégé pour la treemap, construit en largeur avec un budget de nœuds.

    Les nœuds non développés ont ``children: null`` (le client les dessine par catégories et
    les charge au zoom). Les petits fichiers d'un dossier sont regroupés par catégorie.
    """
    aggs = store.dir_aggs()

    def dir_node(dd: int) -> dict[str, Any]:
        return {
            "type": "dir",
            "name": store.dir_name(dd),
            "path": store.dir_path[dd],
            "size": int(aggs.total_size[dd]),
            "files": int(aggs.total_count[dd]),
            "dirs": int(store.dir_end[dd]) - dd - 1,
            "mtime": int(aggs.max_mtime[dd]) or None,
            "cats": _cats_dict(aggs.total_cat[dd]),
            "error": bool(store.dir_flags[dd] & FLAG_ERROR),
            "children": None,
        }

    root = dir_node(d)
    root["root"] = store.roots[int(store.dir_root[d])].id
    budget = max_nodes
    queue: deque[tuple[dict[str, Any], int, int]] = deque([(root, d, 0)])
    while queue and budget > 0:
        node, dd, level = queue.popleft()
        if level >= depth:
            continue
        children: list[dict[str, Any]] = []
        kids = [k for k in store.children(dd) if aggs.total_size[k] > 0]
        kids.sort(key=lambda k: -int(aggs.total_size[k]))
        shown, rest = kids[:max_dirs], kids[max_dirs:]
        expand: list[tuple[dict[str, Any], int]] = []
        for k in shown:
            child = dir_node(k)
            children.append(child)
            if int(store.dir_end[k]) > k + 1 or aggs.direct_count[k] > 0:
                expand.append((child, k))
        if rest:
            children.append(
                {
                    "type": "group",
                    "name": f"{len(rest)} autres dossiers",
                    "size": int(sum(int(aggs.total_size[k]) for k in rest)),
                    "count": len(rest),
                    "cats": _cats_dict(aggs.total_cat[rest].sum(axis=0)),
                }
            )
        f0, f1 = int(store.dir_fstart[dd]), int(store.dir_fstart[dd + 1])
        if f1 > f0:
            sizes = store.size[f0:f1]
            best = [int(i) for i in _top(sizes, max_files).tolist() if sizes[i] > 0]
            for i in best:
                row = f0 + i
                children.append(
                    {
                        "type": "file",
                        "name": store.name(row),
                        "size": int(store.size[row]),
                        "cat": CATEGORY_IDS[int(store.cat[row])],
                        "mtime": int(store.mtime[row]),
                    }
                )
            others = np.ones(f1 - f0, dtype=bool)
            others[best] = False
            if others.any():
                oc = store.cat[f0:f1][others]
                cnt = np.bincount(oc, minlength=NCAT)
                byt = np.bincount(oc, weights=sizes[others], minlength=NCAT)
                for ci in np.flatnonzero(byt > 0).tolist():
                    children.append(
                        {
                            "type": "group",
                            "name": f"{int(cnt[ci])} autres fichiers",
                            "size": _i(byt[ci]),
                            "count": int(cnt[ci]),
                            "cat": CATEGORY_IDS[ci],
                        }
                    )
        children.sort(key=lambda c: -c["size"])
        node["children"] = children
        budget -= len(children)
        for child, k in expand:
            queue.append((child, k, level + 1))
    return root


def _allowed_below(store: Store, k: int, access: tuple[tuple[int, int], ...]) -> list[int]:
    """Dossiers autorisés de plus haut niveau situés sous ``k`` (exclu)."""
    end = int(store.dir_end[k])
    return [a for a, _ in access if k < a < end]


def subdirs(store: Store, d: int, access: tuple[tuple[int, int], ...] | None = None) -> list[dict[str, Any]]:
    """Sous-dossiers directs (arbre repliable de l'Explorateur), triés par nom.

    Compte restreint : seuls les sous-dossiers autorisés, ou menant à un dossier autorisé, sont
    listés ; pour ces derniers, taille et nombre de fichiers ne comptent que la partie autorisée.
    """
    aggs = store.dir_aggs()
    out = []
    for k in store.children(d):
        size, files = int(aggs.total_size[k]), int(aggs.total_count[k])
        if access is not None and not store.inside(k, access):
            below = _allowed_below(store, k, access)
            if not below:
                continue
            size = sum(int(aggs.total_size[a]) for a in below)
            files = sum(int(aggs.total_count[a]) for a in below)
        out.append(
            {
                "name": store.dir_name(k),
                "path": store.dir_path[k],
                "size": size,
                "files": files,
                "has_children": int(store.dir_end[k]) > k + 1,
                "error": bool(store.dir_flags[k] & FLAG_ERROR),
            }
        )
    out.sort(key=lambda x: x["name"].lower())
    return out


def build_tree_scoped(
    store: Store, d: int, access: tuple[tuple[int, int], ...] | None, *, depth: int = 3, max_nodes: int = 4000
) -> dict[str, Any] | None:
    """Treemap limitée au périmètre d'un compte ; None si ``d`` n'est pas visible.

    Pour un dossier parent de dossiers autorisés, on construit un nœud de synthèse dont les
    enfants sont ces dossiers autorisés (nommés par leur chemin relatif) : ni les fichiers propres
    du parent ni ses autres sous-dossiers n'y figurent.
    """
    if access is None or store.inside(d, access):
        return build_tree(store, d, depth=depth, max_nodes=max_nodes)
    below = _allowed_below(store, d, access)
    if not below:
        return None
    budget = max(200, max_nodes // len(below))
    prefix = store.dir_path[d]
    children = []
    for a in below:
        child = build_tree(store, a, depth=max(0, depth - 1), max_nodes=budget)
        child.pop("root", None)
        rel = store.dir_path[a][len(prefix):].lstrip("/") if prefix else store.dir_path[a]
        child["name"] = rel or child["name"]
        children.append(child)
    children.sort(key=lambda c: -c["size"])
    cats: dict[str, int] = {}
    for c in children:
        for k, v in c["cats"].items():
            cats[k] = cats.get(k, 0) + v
    return {
        "type": "dir",
        "name": store.dir_name(d),
        "path": prefix,
        "root": store.roots[int(store.dir_root[d])].id,
        "size": sum(c["size"] for c in children),
        "files": sum(c["files"] for c in children),
        "dirs": sum(c["dirs"] + 1 for c in children),
        "mtime": max((c["mtime"] or 0 for c in children), default=0) or None,
        "cats": cats,
        "error": False,
        "children": children,
    }


def find_in_tree(tree: dict[str, Any], path: str) -> dict[str, Any] | None:
    """Retrouve un nœud dans un arbre pré-calculé (summary.yaml) par son chemin relatif."""
    if not path:
        return tree
    node: dict[str, Any] | None = tree
    for i, _ in enumerate(parts := path.split("/")):
        target = "/".join(parts[: i + 1])
        nxt = None
        for child in (node or {}).get("children") or []:
            if child.get("type") == "dir" and child.get("path") == target:
                nxt = child
                break
        node = nxt
        if node is None:
            return None
    return node
