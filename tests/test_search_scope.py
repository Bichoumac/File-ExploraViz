"""Recherche par nom : portée (fichier, dossier, chemin), glob avec classes, expressions régulières."""

from __future__ import annotations

from pathlib import Path

import pytest

from app import store as store_mod
from app.store import FileFilter, QueryError, SearchTimeout, Store, compile_query

from .conftest import make_file
from .test_api import client, login, scan_and_wait  # noqa: F401 (fixture)
from .test_scanner_store import build


@pytest.fixture
def s(sample_tree: Path, tmp_path: Path) -> Store:
    return build(sample_tree, tmp_path)


def found(s: Store, q: str, scope: str = "file", mode: str = "text", **kw) -> list[str]:
    f = FileFilter(q=q, q_scope=scope, q_mode=mode, **kw)
    return sorted((s.file_path(int(i)) for i in s.select(f).rows()), key=str.lower)


# --------------------------------------------------------------------------- portée « dossier »


def test_folder_scope_includes_whole_subtree(s: Store):
    assert found(s, "2023", "folder") == ["2023/deep/x/y.png", "2023/film.mp4", "2023/IMG_001.jpg", "2023/IMG_002.jpg"]
    assert found(s, "deep", "folder") == ["2023/deep/x/y.png"]
    assert found(s, "COPIE", "folder") == ["copie/a.jpg", "copie/vide.dat"]  # insensible à la casse


def test_folder_scope_globs_and_regex(s: Store):
    assert found(s, "*e", "folder") == ["copie/a.jpg", "copie/vide.dat"]  # copie, vide (vide est vide)
    assert found(s, "d??p", "folder") == ["2023/deep/x/y.png"]
    assert found(s, "^(copie|x)$", "folder", "regex") == ["2023/deep/x/y.png", "copie/a.jpg", "copie/vide.dat"]


def test_folder_scope_never_matches_a_root(s: Store):
    assert found(s, "docs", "folder") == []
    assert found(s, "photos", "folder") == []


def test_folder_scope_combines_with_other_filters(s: Store):
    assert found(s, "2023", "folder", exts=("jpg",)) == ["2023/IMG_001.jpg", "2023/IMG_002.jpg"]
    assert found(s, "deep", "folder", root="photos", path="2023") == ["2023/deep/x/y.png"]
    assert found(s, "deep", "folder", root="photos", path="copie") == []
    assert found(s, "2023", "folder", root="photos", path="2023", recursive=False) == [
        "2023/film.mp4", "2023/IMG_001.jpg", "2023/IMG_002.jpg",
    ]


# --------------------------------------------------------------------------- portée « chemin »


def test_path_scope(s: Store):
    assert found(s, "2023/img", "path") == ["2023/IMG_001.jpg", "2023/IMG_002.jpg"]
    assert found(s, "a.jpg", "path") == ["a.jpg", "copie/a.jpg"]
    assert found(s, "copie/*", "path") == ["copie/a.jpg", "copie/vide.dat"]
    assert found(s, "2023/*.jpg", "path") == ["2023/IMG_001.jpg", "2023/IMG_002.jpg"]
    assert found(s, "*deep*", "path") == ["2023/deep/x/y.png"]  # « * » traverse les « / »
    assert found(s, "^2023/[^/]+$", "path", "regex") == ["2023/film.mp4", "2023/IMG_001.jpg", "2023/IMG_002.jpg"]
    assert found(s, "^notes", "path", "regex") == ["notes.txt"]  # fichier à la racine : chemin = nom


def test_path_scope_glob_on_a_large_folder(tmp_path: Path):
    """Plusieurs correspondances dans un même dossier : chaque fichier est retrouvé exactement."""
    for i in range(50):
        make_file(tmp_path / "data" / "big" / "sub" / f"f{i:02d}.{'jpg' if i % 2 else 'png'}", 1)
    s = build(tmp_path / "data", tmp_path, roots=("big",))
    assert len(found(s, "sub/f*.jpg", "path")) == 25
    assert found(s, "sub/f0?.png", "path") == [f"sub/f0{i}.png" for i in range(0, 10, 2)]


# --------------------------------------------------------------------------- portée « fichier »


def test_file_scope_glob_classes(s: Store):
    assert found(s, "img_00[12]*") == ["2023/IMG_001.jpg", "2023/IMG_002.jpg"]
    assert found(s, "img_00[!1]*") == ["2023/IMG_002.jpg"]
    assert found(s, "img_00[0-1]*") == ["2023/IMG_001.jpg"]
    assert found(s, "[ab].jpg") == []  # sans * ni ? : sous-chaîne littérale « [ab].jpg »


def test_file_scope_regex(s: Store):
    assert found(s, r"^img_\d{3}\.jpg$", mode="regex") == ["2023/IMG_001.jpg", "2023/IMG_002.jpg"]
    # Le motif n'est pas mis en minuscules : \D reste « non-chiffre ».
    assert found(s, r"^\D+\.pdf$", mode="regex") == ["rapport.pdf"]
    assert found(s, r"\.(png|mp4)$", mode="regex") == ["2023/deep/x/y.png", "2023/film.mp4"]


def test_bad_patterns_are_rejected():
    with pytest.raises(QueryError):
        compile_query("img(", "regex")
    with pytest.raises(QueryError):
        compile_query("*[z-a]*", "text")
    compile_query("img(", "text")  # en mode texte, les caractères spéciaux sont littéraux


def test_slow_regex_is_interrupted(tmp_path: Path, monkeypatch):
    make_file(tmp_path / "data" / "r" / ("a" * 60 + "b"), 1)
    s = build(tmp_path / "data", tmp_path, roots=("r",))
    monkeypatch.setattr(store_mod, "REGEX_BUDGET_S", 0.3)
    with pytest.raises(SearchTimeout):
        s.select(FileFilter(q="(a|aa)+$", q_mode="regex"))


# --------------------------------------------------------------------------- API


def test_api_search_scope_and_errors(client, monkeypatch):
    login(client)
    scan_and_wait(client)
    r = client.get("/api/files", params={"q": "2023", "q_scope": "folder"}).json()
    assert r["total"] == 4
    r = client.get("/api/files", params={"q": r"^img_\d+", "q_mode": "regex"}).json()
    assert r["total"] == 2
    stats = client.get("/api/stats", params={"q": "copie/*", "q_scope": "path"}).json()
    assert stats["totals"]["files"] == 2

    bad = client.get("/api/files", params={"q": "img(", "q_mode": "regex"})
    assert bad.status_code == 400 and bad.json()["code"] == "bad_pattern"
    assert client.get("/api/files", params={"q": "x", "q_scope": "disk"}).status_code == 422

    monkeypatch.setattr(store_mod, "REGEX_BUDGET_S", 0.0)
    slow = client.get("/api/files", params={"q": "z+", "q_mode": "regex"})
    assert slow.status_code == 400 and slow.json()["code"] == "regex_timeout"


def test_csv_reports_creation_date_source(client):
    login(client)
    scan_and_wait(client)
    lines = client.get("/api/files/export.csv", params={"root": "docs"}).content.decode("utf-8-sig").splitlines()
    assert lines[0].endswith(";created;created_source")
    assert all(line.rsplit(";", 1)[1] in ("birth time", "inode change") for line in lines[1:])
