"""Index en mémoire : colonnes numpy, filtres, tri, pagination, recherche par nom.

Un ``Store`` est immuable une fois construit (seuls des caches internes s'y ajoutent) : un
scan en construit un nouveau, puis l'application remplace la référence. Les requêtes en cours
continuent sur l'ancien sans verrou.

Invariants (voir docs/ARCHITECTURE.md § 4) :
* dossiers en ordre préfixe : le sous-arbre de ``d`` est ``[d, dir_end[d])`` ;
* fichiers rangés par dossier : ceux du dossier ``d`` sont ``[dir_fstart[d], dir_fstart[d+1])``,
  ceux de son sous-arbre ``[dir_fstart[d], dir_fstart[dir_end[d]])``.
"""

from __future__ import annotations

import logging
import re
import threading
from array import array
from collections import OrderedDict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .categories import CATEGORY_IDS, CATEGORY_INDEX, CATEGORY_LABELS, NCAT, OTHER
from .yamlio import DirRecord, IndexFormatError, iter_index_fast, iter_index_yaml

log = logging.getLogger("store")

FLAG_ERROR = 1
FLAG_TRUNCATED = 2

SORT_FIELDS = ("name", "path", "ext", "category", "size", "mtime", "ctime")


# --------------------------------------------------------------------------- données d'une racine


@dataclass
class RootPart:
    """Données d'une racine, numérotation locale (dossier 0 = la racine)."""

    root_id: str
    name: str
    header: dict[str, Any]
    footer: dict[str, Any]
    dir_path: list[str]
    dir_flags: np.ndarray  # uint8
    dir_error: dict[int, str]
    dir_fcount: np.ndarray  # int64, fichiers directs par dossier
    exts: list[str]
    ext_id: np.ndarray  # int32 -> exts
    size: np.ndarray  # int64
    mtime: np.ndarray  # int64
    ctime: np.ndarray  # int64
    names: str  # noms concaténés
    name_len: np.ndarray  # int64
    entry_errors: list[tuple[int, str, str]]  # (dossier local, nom, message)

    @property
    def nfiles(self) -> int:
        return len(self.size)


def _path_key(path: str) -> tuple[str, ...]:
    return tuple(path.split("/")) if path else ()


def load_root_part(path: Path, root_id: str, name: str) -> RootPart:
    """Lit un index ; lecteur rapide d'abord, YAML générique si le format a été retouché."""
    try:
        return _load_part(path, root_id, name, iter_index_fast)
    except IndexFormatError as exc:
        log.warning("index non standard, relecture YAML générique (plus lente)", extra={"file": str(path), "error": str(exc)})
        return _load_part(path, root_id, name, iter_index_yaml)


def _load_part(path: Path, root_id: str, name: str, reader: Callable[[Path], Iterator[tuple[str, Any]]]) -> RootPart:
    header: dict[str, Any] = {}
    footer: dict[str, Any] = {}
    records: list[tuple[str, int, int, str | None, int]] = []  # (path, file_start, nfiles, error, flags)
    names: list[str] = []
    ext_ids = array("i")
    sizes, mtimes, ctimes = array("q"), array("q"), array("q")
    ext_table: dict[str, int] = {}
    entry_errors: list[tuple[int, str, str]] = []

    for kind, payload in reader(path):
        if kind == "header":
            header = payload
        elif kind == "footer":
            footer = payload
        elif kind == "dir":
            rec: DirRecord = payload
            d = len(records)
            for err in rec.errors:
                entry_errors.append((d, str(err[0]), str(err[1]) if len(err) > 1 else ""))
            start = len(sizes)
            if rec.files:
                try:
                    cols = list(zip(*rec.files, strict=False))
                    names.extend(map(str, cols[0]))
                    for e in cols[1]:
                        eid = ext_table.get(e)
                        if eid is None:
                            eid = ext_table[e] = len(ext_table)
                        ext_ids.append(eid)
                    sizes.extend(map(int, cols[3]))
                    mtimes.extend(map(int, cols[4]))
                    ctimes.extend(map(int, cols[5]))
                except (IndexError, TypeError, ValueError) as exc:
                    raise IndexFormatError(f"ligne de fichier invalide dans « {rec.path} » : {exc}") from exc
            flags = (FLAG_ERROR if rec.error else 0) | (FLAG_TRUNCATED if rec.truncated else 0)
            records.append((str(rec.path or ""), start, len(sizes) - start, rec.error, flags))

    if not records or records[0][0] != "":
        records.insert(0, ("", len(sizes), 0, None, 0))  # la racine doit exister

    n = len(sizes)
    ext_arr = np.frombuffer(ext_ids, dtype=np.int32).copy() if n else np.zeros(0, np.int32)
    size_arr = np.frombuffer(sizes, dtype=np.int64).copy() if n else np.zeros(0, np.int64)
    mt_arr = np.frombuffer(mtimes, dtype=np.int64).copy() if n else np.zeros(0, np.int64)
    ct_arr = np.frombuffer(ctimes, dtype=np.int64).copy() if n else np.zeros(0, np.int64)
    name_len = np.fromiter(map(len, names), dtype=np.int64, count=n)

    keys = [_path_key(r[0]) for r in records]
    if any(keys[i] >= keys[i + 1] for i in range(len(keys) - 1)):
        # Index retouché : on remet les dossiers en ordre préfixe (et leurs fichiers avec).
        order = sorted(range(len(records)), key=keys.__getitem__)
        dedup: list[int] = []
        for i in order:
            if dedup and keys[dedup[-1]] == keys[i]:
                continue
            dedup.append(i)
        perm = np.concatenate([np.arange(records[i][1], records[i][1] + records[i][2]) for i in dedup] or [np.zeros(0, np.int64)])
        remap = {old: new for new, old in enumerate(dedup)}
        entry_errors = [(remap[d], nm, m) for d, nm, m in entry_errors if d in remap]
        records = [records[i] for i in dedup]
        ext_arr, size_arr, mt_arr, ct_arr = ext_arr[perm], size_arr[perm], mt_arr[perm], ct_arr[perm]
        names = [names[i] for i in perm.tolist()]
        name_len = name_len[perm]

    return RootPart(
        root_id=root_id,
        name=name,
        header=header,
        footer=footer,
        dir_path=[r[0] for r in records],
        dir_flags=np.array([r[4] for r in records], dtype=np.uint8),
        dir_error={i: r[3] for i, r in enumerate(records) if r[3]},
        dir_fcount=np.array([r[2] for r in records], dtype=np.int64),
        exts=list(ext_table),
        ext_id=ext_arr,
        size=size_arr,
        mtime=mt_arr,
        ctime=ct_arr,
        names="".join(names),
        name_len=name_len,
        entry_errors=entry_errors,
    )


# --------------------------------------------------------------------------- filtres


@dataclass(frozen=True)
class FileFilter:
    root: str | None = None
    path: str = ""
    recursive: bool = True
    exts: tuple[str, ...] = ()
    cats: tuple[str, ...] = ()
    date_field: str = "mtime"
    date_from: int | None = None
    date_to: int | None = None
    min_size: int | None = None
    max_size: int | None = None
    q: str = ""
    # Périmètre autorisé (comptes restreints) : intervalles de dossiers [début, fin) fusionnés,
    # en numérotation préfixe ; None = tout. Fait partie de la clé des caches.
    access: tuple[tuple[int, int], ...] | None = None

    @property
    def has_file_filters(self) -> bool:
        return bool(
            self.exts or self.cats or self.q or self.date_from is not None or self.date_to is not None
            or self.min_size is not None or self.max_size is not None
        )


@dataclass
class Selection:
    """Lignes sélectionnées : l'intervalle ``[lo, hi)`` filtré par ``idx`` (None = tout)."""

    lo: int
    hi: int
    idx: np.ndarray | None
    dir_lo: int
    dir_hi: int
    found: bool = True
    dir_mask: np.ndarray | None = None  # dossiers de [dir_lo, dir_hi) autorisés (None = tous)

    def dir_ids(self) -> np.ndarray:
        """Dossiers du périmètre de la sélection (restreints aux dossiers autorisés)."""
        ids = np.arange(self.dir_lo, self.dir_hi, dtype=np.int64)
        return ids if self.dir_mask is None else ids[self.dir_mask]

    @property
    def count(self) -> int:
        return self.hi - self.lo if self.idx is None else int(len(self.idx))

    def take(self, arr: np.ndarray) -> np.ndarray:
        return arr[self.lo : self.hi] if self.idx is None else arr[self.idx]

    def rows(self) -> np.ndarray:
        return np.arange(self.lo, self.hi, dtype=np.int64) if self.idx is None else self.idx


def glob_to_regex(pattern: str) -> str:
    """Glob simple (``*``, ``?``) vers une regex qui ne déborde pas d'un nom (séparateur NUL).

    Un ``*`` initial (ou final) n'impose rien : on retire l'ancrage correspondant, ce qui évite
    de tenter une correspondance à chaque début de nom (environ 10 fois plus rapide sur 1 M de noms).
    """
    head = not pattern.startswith("*")
    tail = not pattern.endswith("*")
    out = []
    for ch in pattern.strip("*"):
        if ch == "*":
            out.append("[^\x00]*")
        elif ch == "?":
            out.append("[^\x00]")
        else:
            out.append(re.escape(ch))
    return ("(?<=\x00)" if head else "") + "".join(out) + ("(?=\x00)" if tail else "")


class _LRU:
    def __init__(self, size: int):
        self.size = size
        self.data: OrderedDict[Any, Any] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        return None

    def put(self, key: Any, value: Any) -> Any:
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)
        return value


# --------------------------------------------------------------------------- Store


@dataclass
class RootInfo:
    id: str
    name: str
    header: dict[str, Any]
    footer: dict[str, Any]
    dir_start: int
    dir_stop: int
    file_start: int
    file_stop: int


@dataclass
class DirAggs:
    direct_size: np.ndarray
    direct_count: np.ndarray
    total_size: np.ndarray
    total_count: np.ndarray
    total_cat: np.ndarray  # (ndirs, NCAT) float64
    max_mtime: np.ndarray


@dataclass
class Store:
    generation: int
    roots: list[RootInfo]
    dir_path: list[str]
    dir_root: np.ndarray  # int16
    dir_parent: np.ndarray  # int32 (-1 pour une racine)
    dir_end: np.ndarray  # int32
    dir_depth: np.ndarray  # int16
    dir_flags: np.ndarray  # uint8
    dir_error: dict[int, str]
    dir_fstart: np.ndarray  # int64, ndirs + 1
    exts: list[str]
    ext_id: np.ndarray
    cat: np.ndarray  # uint8
    size: np.ndarray
    mtime: np.ndarray
    ctime: np.ndarray
    file_dir: np.ndarray  # int32
    names: str
    name_off: np.ndarray  # int64, nfiles + 1
    entry_errors: list[tuple[int, str, str]]  # (dossier global, nom, message)
    dir_lookup: dict[tuple[int, str], int] = field(default_factory=dict)
    ext_cat: np.ndarray = field(default_factory=lambda: np.zeros(1, np.uint8))  # catégorie de chaque extension
    ext_index: dict[str, int] = field(default_factory=dict)
    _lazy_lock: threading.Lock = field(default_factory=threading.Lock)
    _search: tuple[str, np.ndarray] | None = None
    _name_rank: np.ndarray | None = None
    _name_hash: np.ndarray | None = None
    _hidden: np.ndarray | None = None
    _aggs: DirAggs | None = None
    _sel_cache: _LRU = field(default_factory=lambda: _LRU(32))
    _sort_cache: _LRU = field(default_factory=lambda: _LRU(16))
    _q_cache: _LRU = field(default_factory=lambda: _LRU(16))
    stats_cache: _LRU = field(default_factory=lambda: _LRU(32))

    # ----------------------------------------------------------------- construction

    @classmethod
    def build(cls, parts: list[RootPart], ext_map: dict[str, int], generation: int = 0) -> Store:
        ext_index: dict[str, int] = {}
        roots: list[RootInfo] = []
        dir_path: list[str] = []
        a_root, a_parent, a_end, a_depth, a_flags, a_fcount = [], [], [], [], [], []
        a_ext, a_size, a_mt, a_ct, a_nlen = [], [], [], [], []
        names: list[str] = []
        dir_error: dict[int, str] = {}
        entry_errors: list[tuple[int, str, str]] = []
        dir_lookup: dict[tuple[int, str], int] = {}
        d_off = f_off = 0

        for ri, part in enumerate(parts):
            nd = len(part.dir_path)
            local: dict[str, int] = {}
            parent = np.full(nd, -1, dtype=np.int32)
            depth = np.zeros(nd, dtype=np.int16)
            for i, p in enumerate(part.dir_path):
                local[p] = i
                dir_lookup[(ri, p)] = d_off + i
                if i:
                    up = p.rpartition("/")[0]
                    while up not in local and up:  # ancêtre absent (index retouché)
                        up = up.rpartition("/")[0]
                    pid = local.get(up, 0)
                    parent[i] = pid
                    depth[i] = depth[pid] + 1
            end = np.arange(1, nd + 1, dtype=np.int32)
            par = parent.tolist()
            endl = end.tolist()
            for i in range(nd - 1, 0, -1):
                p = par[i]
                if endl[i] > endl[p]:
                    endl[p] = endl[i]
            end = np.array(endl, dtype=np.int32)

            remap = np.array([ext_index.setdefault(e, len(ext_index)) for e in part.exts], dtype=np.int32)

            a_root.append(np.full(nd, ri, dtype=np.int16))
            a_parent.append(np.where(parent >= 0, parent + d_off, -1).astype(np.int32))
            a_end.append(end + d_off)
            a_depth.append(depth)
            a_flags.append(part.dir_flags)
            a_fcount.append(part.dir_fcount)
            a_ext.append(remap[part.ext_id] if len(remap) else part.ext_id.astype(np.int32))
            a_size.append(part.size)
            a_mt.append(part.mtime)
            a_ct.append(part.ctime)
            a_nlen.append(part.name_len)
            names.append(part.names)
            dir_path.extend(part.dir_path)
            dir_error.update({d + d_off: m for d, m in part.dir_error.items()})
            entry_errors.extend((d + d_off, n, m) for d, n, m in part.entry_errors)
            nf = part.nfiles
            roots.append(RootInfo(part.root_id, part.name, part.header, part.footer, d_off, d_off + nd, f_off, f_off + nf))
            d_off += nd
            f_off += nf

        exts = list(ext_index)

        def cat(arrs: list[np.ndarray], dtype: Any) -> np.ndarray:
            return np.concatenate(arrs).astype(dtype, copy=False) if arrs else np.zeros(0, dtype)

        fcount = cat(a_fcount, np.int64)
        fstart = np.zeros(len(fcount) + 1, dtype=np.int64)
        np.cumsum(fcount, out=fstart[1:])
        nlen = cat(a_nlen, np.int64)
        name_off = np.zeros(len(nlen) + 1, dtype=np.int64)
        np.cumsum(nlen, out=name_off[1:])
        ext_id = cat(a_ext, np.int32)

        store = cls(
            generation=generation,
            roots=roots,
            dir_path=dir_path,
            dir_root=cat(a_root, np.int16),
            dir_parent=cat(a_parent, np.int32),
            dir_end=cat(a_end, np.int32),
            dir_depth=cat(a_depth, np.int16),
            dir_flags=cat(a_flags, np.uint8),
            dir_error=dir_error,
            dir_fstart=fstart,
            exts=exts,
            ext_id=ext_id,
            cat=np.zeros(len(ext_id), dtype=np.uint8),
            size=cat(a_size, np.int64),
            mtime=cat(a_mt, np.int64),
            ctime=cat(a_ct, np.int64),
            file_dir=np.repeat(np.arange(len(fcount), dtype=np.int32), fcount),
            names="".join(names),
            name_off=name_off,
            entry_errors=entry_errors,
            dir_lookup=dir_lookup,
            ext_index=ext_index,
        )
        store.apply_categories(ext_map)
        return store

    @classmethod
    def empty(cls) -> Store:
        return cls.build([], {})

    def apply_categories(self, ext_map: dict[str, int]) -> None:
        """(Re)calcule la catégorie de chaque fichier depuis son extension."""
        lut = np.array([ext_map.get(e, OTHER) for e in self.exts] or [OTHER], dtype=np.uint8)
        self.ext_cat = lut
        self.cat = lut[self.ext_id] if len(self.ext_id) else np.zeros(0, np.uint8)
        self._aggs = None
        self.stats_cache = _LRU(32)
        self._sel_cache = _LRU(32)
        self._sort_cache = _LRU(16)

    def extract(self, root_id: str) -> RootPart | None:
        """Reconstitue la RootPart d'une racine (pour ne rescanner qu'une seule racine)."""
        ri = self.root_index(root_id)
        if ri is None:
            return None
        r = self.roots[ri]
        d0, d1, f0, f1 = r.dir_start, r.dir_stop, r.file_start, r.file_stop
        return RootPart(
            root_id=r.id,
            name=r.name,
            header=r.header,
            footer=r.footer,
            dir_path=self.dir_path[d0:d1],
            dir_flags=self.dir_flags[d0:d1],
            dir_error={d - d0: m for d, m in self.dir_error.items() if d0 <= d < d1},
            dir_fcount=np.diff(self.dir_fstart[d0 : d1 + 1]),
            exts=self.exts,
            ext_id=self.ext_id[f0:f1],
            size=self.size[f0:f1],
            mtime=self.mtime[f0:f1],
            ctime=self.ctime[f0:f1],
            names=self.names[int(self.name_off[f0]) : int(self.name_off[f1])],
            name_len=np.diff(self.name_off[f0 : f1 + 1]),
            entry_errors=[(d - d0, n, m) for d, n, m in self.entry_errors if d0 <= d < d1],
        )

    # ----------------------------------------------------------------- accès simples

    @property
    def nfiles(self) -> int:
        return len(self.size)

    @property
    def ndirs(self) -> int:
        return len(self.dir_path)

    def root_index(self, root_id: str | None) -> int | None:
        for i, r in enumerate(self.roots):
            if r.id == root_id:
                return i
        return None

    def name(self, i: int) -> str:
        return self.names[int(self.name_off[i]) : int(self.name_off[i + 1])]

    def file_path(self, i: int) -> str:
        d = self.dir_path[int(self.file_dir[i])]
        return f"{d}/{self.name(i)}" if d else self.name(i)

    def children(self, d: int) -> Iterator[int]:
        c, end = d + 1, int(self.dir_end[d])
        while c < end:
            yield c
            c = int(self.dir_end[c])

    def dir_name(self, d: int) -> str:
        p = self.dir_path[d]
        return p.rpartition("/")[2] if p else self.roots[int(self.dir_root[d])].name

    # ----------------------------------------------------------------- caches paresseux

    def _lower_names(self) -> list[str]:
        off = self.name_off.tolist()
        names = self.names
        return [names[off[i] : off[i + 1]].lower() for i in range(self.nfiles)]

    def warm_up(self) -> None:
        """Pré-calcule les caches coûteux (appelé dans le thread de scan, pas dans une requête)."""
        with self._lazy_lock:
            if self._search is None:
                low = self._lower_names()
                lens = np.fromiter(map(len, low), dtype=np.int64, count=len(low))
                starts = np.zeros(len(low), dtype=np.int64)
                if len(low):
                    np.cumsum(lens[:-1] + 1, out=starts[1:])
                starts += 1
                self._search = ("\x00" + "\x00".join(low) + "\x00", starts)
                order = sorted(range(len(low)), key=low.__getitem__)
                rank = np.empty(len(low), dtype=np.int32)
                rank[np.array(order, dtype=np.int64)] = np.arange(len(low), dtype=np.int32)
                self._name_rank = rank
                self._name_hash = np.fromiter(map(hash, low), dtype=np.int64, count=len(low))
                self._hidden = np.fromiter((n.startswith(".") for n in low), dtype=bool, count=len(low))
        self.dir_aggs()

    def name_rank(self) -> np.ndarray:
        if self._name_rank is None:
            self.warm_up()
        assert self._name_rank is not None
        return self._name_rank

    def name_hash(self) -> np.ndarray:
        if self._name_hash is None:
            self.warm_up()
        assert self._name_hash is not None
        return self._name_hash

    def hidden(self) -> np.ndarray:
        if self._hidden is None:
            self.warm_up()
        assert self._hidden is not None
        return self._hidden

    def dir_aggs(self) -> DirAggs:
        if self._aggs is not None:
            return self._aggs
        nd = self.ndirs
        fstart, end = self.dir_fstart, self.dir_end.astype(np.int64)
        ids = np.arange(nd, dtype=np.int64)
        cs = np.zeros(self.nfiles + 1, dtype=np.int64)
        np.cumsum(self.size, out=cs[1:])
        direct_size = cs[fstart[1:]] - cs[fstart[:-1]]
        direct_count = np.diff(fstart)
        total_size = cs[fstart[end]] - cs[fstart[ids]]
        total_count = fstart[end] - fstart[ids]
        direct_cat = np.bincount(
            self.file_dir.astype(np.int64) * NCAT + self.cat, weights=self.size, minlength=nd * NCAT
        ).reshape(nd, NCAT)
        cs_cat = np.zeros((nd + 1, NCAT), dtype=np.float64)
        np.cumsum(direct_cat, axis=0, out=cs_cat[1:])
        total_cat = cs_cat[end] - cs_cat[ids]
        max_mtime = np.zeros(nd, dtype=np.int64)
        if self.nfiles:
            np.maximum.at(max_mtime, self.file_dir, self.mtime)
            depth = self.dir_depth
            for lvl in range(int(depth.max(initial=0)), 0, -1):
                sel = np.flatnonzero(depth == lvl)
                np.maximum.at(max_mtime, self.dir_parent[sel], max_mtime[sel])
        self._aggs = DirAggs(direct_size, direct_count, total_size, total_count, total_cat, max_mtime)
        return self._aggs

    # ----------------------------------------------------------------- sélection

    def resolve_dir(self, root: str | None, path: str) -> int | None:
        ri = self.root_index(root)
        if ri is None:
            return None
        return self.dir_lookup.get((ri, path))

    # ----------------------------------------------------------------- périmètre des comptes

    def access_ranges(self, scopes: tuple[tuple[str, str], ...]) -> tuple[tuple[int, int], ...]:
        """Dossiers autorisés (racine, chemin) → intervalles [d, dir_end[d]) triés et fusionnés.

        Les sous-arborescences sont emboîtées ou disjointes : un intervalle contenu dans le
        précédent est simplement ignoré. Un dossier absent de l'index n'ouvre aucun accès.
        """
        ranges = sorted(
            (d, int(self.dir_end[d]))
            for root, path in scopes
            if (d := self.resolve_dir(root, path)) is not None
        )
        merged: list[tuple[int, int]] = []
        for a, b in ranges:
            if merged and a < merged[-1][1]:
                continue
            merged.append((a, b))
        return tuple(merged)

    def inside(self, d: int, access: tuple[tuple[int, int], ...]) -> bool:
        """Le dossier ``d`` est-il dans une sous-arborescence autorisée ?"""
        return any(a <= d < b for a, b in access)

    def visible(self, d: int, access: tuple[tuple[int, int], ...]) -> bool:
        """Autorisé, ou ancêtre d'un dossier autorisé (navigable sans voir son contenu propre)."""
        end = int(self.dir_end[d])
        return any(a <= d < b or d <= a < end for a, b in access)

    def select(self, f: FileFilter) -> Selection:
        cached = self._sel_cache.get(f)
        if cached is not None:
            return cached
        if f.root is None:
            lo, hi, dlo, dhi, found = 0, self.nfiles, 0, self.ndirs, True
        else:
            d = self.resolve_dir(f.root, f.path)
            if d is None or (f.access is not None and not self.visible(d, f.access)):
                # Hors périmètre : même réponse qu'un dossier inexistant (aucune fuite d'existence).
                return Selection(0, 0, None, 0, 0, found=False)
            dend = int(self.dir_end[d]) if f.recursive else d + 1
            lo, hi, dlo, dhi, found = int(self.dir_fstart[d]), int(self.dir_fstart[dend]), d, dend, True

        mask: np.ndarray | None = None

        def both(m: np.ndarray) -> None:
            nonlocal mask
            mask = m if mask is None else (mask & m)

        if f.exts:
            lut = np.zeros(len(self.exts) + 1, dtype=bool)
            for e in f.exts:
                if e in self.ext_index:
                    lut[self.ext_index[e]] = True
            both(lut[self.ext_id[lo:hi]])
        if f.cats:
            lut = np.zeros(NCAT, dtype=bool)
            for c in f.cats:
                if c in CATEGORY_INDEX:
                    lut[CATEGORY_INDEX[c]] = True
            both(lut[self.cat[lo:hi]])
        dates = self.ctime if f.date_field == "ctime" else self.mtime
        if f.date_from is not None:
            both(dates[lo:hi] >= f.date_from)
        if f.date_to is not None:
            both(dates[lo:hi] <= f.date_to)
        if f.min_size is not None:
            both(self.size[lo:hi] >= f.min_size)
        if f.max_size is not None:
            both(self.size[lo:hi] <= f.max_size)
        if f.q:
            both(self.name_mask(f.q)[lo:hi])

        dir_mask = None
        if f.access is not None:
            fmask = np.zeros(hi - lo, dtype=bool)
            dir_mask = np.zeros(dhi - dlo, dtype=bool)
            for a, b in f.access:
                s, e = max(int(self.dir_fstart[a]), lo), min(int(self.dir_fstart[b]), hi)
                if s < e:
                    fmask[s - lo : e - lo] = True
                s, e = max(a, dlo), min(b, dhi)
                if s < e:
                    dir_mask[s - dlo : e - dlo] = True
            both(fmask)

        idx = None if mask is None else (lo + np.flatnonzero(mask)).astype(np.int64)
        return self._sel_cache.put(f, Selection(lo, hi, idx, dlo, dhi, found, dir_mask))

    def name_mask(self, q: str) -> np.ndarray:
        """Masque des fichiers dont le nom contient ``q`` (ou correspond au glob ``q``)."""
        q = q.replace("\x00", "").strip().lower()
        cached = self._q_cache.get(q)
        if cached is not None:
            return cached
        if self._search is None:
            self.warm_up()
        assert self._search is not None
        text, starts = self._search
        if not q.strip("*"):  # « * » seul : tous les noms
            return self._q_cache.put(q, np.ones(self.nfiles, dtype=bool))
        rx = glob_to_regex(q) if ("*" in q or "?" in q) else re.escape(q)
        positions = np.fromiter((m.start() for m in re.finditer(rx, text)), dtype=np.int64)
        mask = np.zeros(self.nfiles, dtype=bool)
        if len(positions):
            mask[np.searchsorted(starts, positions, side="right") - 1] = True
        return self._q_cache.put(q, mask)

    def sorted_rows(self, f: FileFilter, sort: str) -> np.ndarray:
        key = (f, sort)
        cached = self._sort_cache.get(key)
        if cached is not None:
            return cached
        rows = self.select(f).rows()
        desc = sort.startswith("-")
        field_ = sort.lstrip("-+") or "path"
        if field_ not in SORT_FIELDS:
            field_ = "path"
        if field_ == "path":
            ordered = rows[::-1] if desc else rows  # ordre de l'arborescence
        else:
            if field_ == "size":
                k = self.size[rows]
            elif field_ == "mtime":
                k = self.mtime[rows]
            elif field_ == "ctime":
                k = self.ctime[rows]
            elif field_ == "name":
                k = self.name_rank()[rows].astype(np.int64)
            elif field_ == "ext":
                ext_rank = np.argsort(np.argsort(np.array(self.exts or [""], dtype=object))).astype(np.int64)
                k = ext_rank[self.ext_id[rows]]
            else:  # category : ordre alphabétique des libellés
                labels = [CATEGORY_LABELS[c] for c in CATEGORY_IDS]
                cat_rank = np.argsort(np.argsort(np.array(labels, dtype=object))).astype(np.int64)
                k = cat_rank[self.cat[rows]]
            order = np.argsort(-k if desc else k, kind="stable")
            ordered = rows[order]
        return self._sort_cache.put(key, ordered)

    def row_dict(self, i: int) -> dict[str, Any]:
        d = int(self.file_dir[i])
        dpath = self.dir_path[d]
        name = self.name(i)
        return {
            "root": self.roots[int(self.dir_root[d])].id,
            "name": name,
            "dir": dpath,
            "path": f"{dpath}/{name}" if dpath else name,
            "ext": self.exts[int(self.ext_id[i])],
            "category": CATEGORY_IDS[int(self.cat[i])],
            "size": int(self.size[i]),
            "mtime": int(self.mtime[i]),
            "ctime": int(self.ctime[i]),
        }
