from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
import pytest

from app.aggregates import build_tree, compute_stats, subdirs
from app.categories import DEFAULT_MAPPING, build_ext_map
from app.config import DEFAULT_EXCLUSIONS
from app.scanner import ScanCancelled, ScanOptions, ScanProgress, scan_root
from app.store import FileFilter, Store, load_root_part

from .conftest import T2020, T2024, make_file

EXT_MAP = build_ext_map(DEFAULT_MAPPING)


def scan(data: Path, tmp_path: Path, root: str = "photos", **kw) -> tuple[dict, Path, ScanProgress]:
    out = tmp_path / "cache" / f"index_{root}.yaml"
    opts = ScanOptions(exclusions=kw.pop("exclusions", DEFAULT_EXCLUSIONS), ext_map=EXT_MAP, **kw)
    progress = ScanProgress()
    footer = scan_root(root, root.title(), data / root, out, opts, progress, threading.Event())
    return footer, out, progress


def build(data: Path, tmp_path: Path, roots=("photos", "docs"), **kw) -> Store:
    parts = []
    for r in roots:
        _, out, _ = scan(data, tmp_path, r, **kw)
        parts.append(load_root_part(out, r, r.title()))
    return Store.build(parts, EXT_MAP)


# --------------------------------------------------------------------------- scanner


def test_scan_counts_and_exclusions(sample_tree: Path, tmp_path: Path):
    footer, out, progress = scan(sample_tree, tmp_path)
    # 9 fichiers (thumb.jpg dans @eaDir et cache.tmp exclus)
    assert footer["files"] == 9
    assert footer["dirs"] == 6  # racine, 2023, 2023/deep, 2023/deep/x, copie, vide
    assert footer["bytes"] == 1000 + 2000 + 10 + 5000 + 5000 + 100_000 + 300 + 1000 + 0
    assert progress.files == 9
    text = out.read_text(encoding="utf-8")
    assert "@eaDir" not in text and "cache.tmp" not in text


def test_scan_is_preorder_and_sorted(sample_tree: Path, tmp_path: Path):
    _, out, _ = scan(sample_tree, tmp_path)
    part = load_root_part(out, "photos", "Photos")
    assert part.dir_path == ["", "2023", "2023/deep", "2023/deep/x", "copie", "vide"]


def test_scan_max_depth(sample_tree: Path, tmp_path: Path):
    footer, out, _ = scan(sample_tree, tmp_path, max_depth=1)
    part = load_root_part(out, "photos", "Photos")
    assert "2023/deep" not in part.dir_path
    assert part.dir_flags[part.dir_path.index("2023")] & 2  # marqué tronqué


def test_scan_path_exclusion(sample_tree: Path, tmp_path: Path):
    footer, _, _ = scan(sample_tree, tmp_path, exclusions=["2023/deep", "*.tmp", "@eaDir"])
    assert footer["files"] == 8


def test_scan_cancel(sample_tree: Path, tmp_path: Path):
    cancel = threading.Event()
    cancel.set()
    out = tmp_path / "cache" / "index_photos.yaml"
    with pytest.raises(ScanCancelled):
        scan_root("photos", "P", sample_tree / "photos", out, ScanOptions(ext_map=EXT_MAP), ScanProgress(), cancel)
    assert not out.exists()
    assert not any(out.parent.iterdir())


@pytest.mark.skipif(os.name != "posix", reason="liens symboliques et droits POSIX")
def test_scan_symlinks_not_followed_by_default(sample_tree: Path, tmp_path: Path):
    outside = make_file(tmp_path / "dehors" / "secret.txt", 10)
    os.symlink(outside.parent, sample_tree / "photos" / "lien_dehors")
    os.symlink(sample_tree / "photos" / "2023", sample_tree / "photos" / "lien_dedans")
    footer, _, _ = scan(sample_tree, tmp_path)
    assert footer["files"] == 9 and footer["symlinks_skipped"] == 2
    # Suivi activé : le lien interne est suivi, le lien sortant jamais.
    footer, out, _ = scan(sample_tree, tmp_path, follow_symlinks=True, allowed_bases=[os.path.realpath(sample_tree)])
    assert footer["files"] == 9 + 4
    assert "secret.txt" not in out.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="droits POSIX, non root")
def test_scan_permission_error_is_logged_and_skipped(sample_tree: Path, tmp_path: Path):
    locked = sample_tree / "photos" / "prive"
    make_file(locked / "cache.jpg", 10)
    locked.chmod(0)
    try:
        footer, out, _ = scan(sample_tree, tmp_path)
    finally:
        locked.chmod(0o755)
    assert footer["inaccessible"] == 1
    store = Store.build([load_root_part(out, "photos", "Photos")], EXT_MAP)
    stats = compute_stats(store, store.select(FileFilter(root="photos")))
    assert stats["inaccessible"]["count"] == 1


# --------------------------------------------------------------------------- store / filtres


def names(store: Store, f: FileFilter, sort: str = "path") -> list[str]:
    return [store.name(int(i)) for i in store.sorted_rows(f, sort)]


def test_filters(sample_tree: Path, tmp_path: Path):
    s = build(sample_tree, tmp_path)
    assert s.nfiles == 11
    assert s.select(FileFilter(root="photos")).count == 9
    assert names(s, FileFilter(root="photos", path="2023", recursive=False)) == ["film.mp4", "IMG_001.jpg", "IMG_002.jpg"]
    assert s.select(FileFilter(root="photos", path="2023")).count == 4
    assert s.select(FileFilter(root="photos", path="inexistant")).found is False
    assert s.select(FileFilter(exts=("jpg",))).count == 5  # b.JPG compris (extension normalisée)
    assert s.select(FileFilter(cats=("pdf", "spreadsheet"))).count == 2
    assert s.select(FileFilter(min_size=4000, max_size=5000)).count == 3
    assert s.select(FileFilter(date_from=T2024)).count == 7
    assert s.select(FileFilter(date_to=T2024 - 1)).count == 4
    assert sorted(names(s, FileFilter(q="img"))) == ["IMG_001.jpg", "IMG_002.jpg"]
    assert sorted(names(s, FileFilter(q="*.JPG")), key=str.lower) == ["a.jpg", "a.jpg", "b.JPG", "IMG_001.jpg", "IMG_002.jpg"]
    assert names(s, FileFilter(q="a?jpg")) == ["a.jpg", "a.jpg"]
    assert s.select(FileFilter(q="introuvable")).count == 0
    combo = FileFilter(root="photos", exts=("jpg",), date_from=T2024, q="img")
    assert s.select(combo).count == 2


def test_sorting(sample_tree: Path, tmp_path: Path):
    s = build(sample_tree, tmp_path)
    f = FileFilter(root="photos")
    sizes = [int(s.size[i]) for i in s.sorted_rows(f, "-size")]
    assert sizes == sorted(sizes, reverse=True)
    by_name = names(s, f, "name")
    assert by_name == sorted(by_name, key=str.lower)
    assert names(s, f, "-name") == list(reversed(by_name))


def test_extract_and_rebuild_is_identical(sample_tree: Path, tmp_path: Path):
    s = build(sample_tree, tmp_path)
    parts = [s.extract("docs"), s.extract("photos")]
    s2 = Store.build(parts, EXT_MAP)
    assert sorted(names(s2, FileFilter())) == sorted(names(s, FileFilter()))
    assert s2.select(FileFilter(root="photos", path="2023")).count == 4
    assert int(s2.size.sum()) == int(s.size.sum())


def test_recategorize_without_rescan(sample_tree: Path, tmp_path: Path):
    s = build(sample_tree, tmp_path)
    assert s.select(FileFilter(cats=("document",))).count == 1  # notes.txt
    mapping = {k: list(v) for k, v in DEFAULT_MAPPING.items()}
    mapping["document"].remove("txt")
    mapping["code"].append("txt")
    s.apply_categories(build_ext_map(mapping))
    assert s.select(FileFilter(cats=("document",))).count == 0
    assert s.select(FileFilter(cats=("code",))).count == 1


# --------------------------------------------------------------------------- agrégats


def test_stats(sample_tree: Path, tmp_path: Path):
    s = build(sample_tree, tmp_path)
    st = compute_stats(s, s.select(FileFilter(root="photos")))
    assert st["totals"]["files"] == 9
    assert st["totals"]["empty_files"] == 1
    cats = {c["id"]: c for c in st["categories"]}
    assert cats["photo"]["files"] == 6 and cats["video"]["bytes"] == 100_000
    assert sum(c["files"] for c in st["categories"]) == 9
    assert st["largest_files"][0]["name"] == "film.mp4"
    assert st["duplicates"]["groups"] == 1  # a.jpg x2 ; IMG_001/IMG_002 ont des noms différents
    assert st["duplicates"]["recoverable_bytes"] == 1000
    dup_names = {g["name"] for g in st["duplicates"]["top"]}
    assert "a.jpg" in dup_names
    assert st["empty_dirs"]["count"] == 1 and st["empty_dirs"]["sample"][0]["path"] == "vide"
    years = {y["year"]: y["files"] for y in st["years"]}
    assert years[2020] + years[2024] == 9
    assert sum(b["files"] for b in st["size_histogram"]) == 9
    assert st["largest_dirs"][0]["path"] == "2023"


def test_tree_and_subdirs(sample_tree: Path, tmp_path: Path):
    s = build(sample_tree, tmp_path)
    d = s.resolve_dir("photos", "")
    tree = build_tree(s, d, depth=2)
    assert tree["size"] == int(s.size[: s.roots[0].file_stop].sum()) or tree["size"] > 0
    kinds = {c["type"] for c in tree["children"]}
    assert "dir" in kinds and "file" in kinds
    d2023 = next(c for c in tree["children"] if c["type"] == "dir" and c["path"] == "2023")
    assert d2023["children"] is not None
    assert sum(c["size"] for c in tree["children"]) == tree["size"]
    sub = subdirs(s, d)
    assert [x["name"] for x in sub] == ["2023", "copie", "vide"]
    assert sub[0]["has_children"] is True


def test_tree_budget_groups_small_files(tmp_path: Path):
    root = tmp_path / "data" / "big"
    for i in range(80):
        make_file(root / f"f{i:03d}.jpg", i + 1, T2020)
    _, out, _ = scan(tmp_path / "data", tmp_path, "big")
    s = Store.build([load_root_part(out, "big", "Big")], EXT_MAP)
    tree = build_tree(s, 0, depth=1, max_files=10)
    files = [c for c in tree["children"] if c["type"] == "file"]
    groups = [c for c in tree["children"] if c["type"] == "group"]
    assert len(files) == 10 and len(groups) == 1 and groups[0]["count"] == 70
    assert sum(c["size"] for c in tree["children"]) == sum(range(1, 81))


def test_empty_store():
    s = Store.empty()
    assert s.nfiles == 0
    st = compute_stats(s, s.select(FileFilter()))
    assert st["totals"]["files"] == 0 and np.isscalar(st["totals"]["bytes"])
