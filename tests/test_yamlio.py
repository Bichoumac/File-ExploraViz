from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.yamlio import (
    IndexWriter,
    atomic_writer,
    clean_text,
    iter_index_fast,
    iter_index_yaml,
    read_yaml,
    write_yaml,
)

WEIRD_NAMES = [
    "simple.jpg",
    "accentué é à ç.txt",
    "emoji 😀 🎉.png",
    'guillemets "doubles" et \'simples\'.doc',
    "anti\\slash.txt",
    "retour\nligne.txt",
    "tab\tulation.txt",
    "nel\x85char.txt",
    "sep ligne.txt",
    "bom﻿.txt",
    "ctrl\x7f\x9f.txt",
    "- [pas une liste].txt",
    "---",
    "dir: faux",
    "#commentaire.txt",
    "yes",
    "null",
    "123",
    clean_text("octets\udcff\udcfeinvalides.bin"),
]


def test_atomic_write_keeps_previous_file_on_error(tmp_path: Path):
    target = tmp_path / "f.yaml"
    write_yaml(target, {"v": 1})
    with pytest.raises(RuntimeError):
        with atomic_writer(target) as fh:
            fh.write("v: 2\n")
            raise RuntimeError("boum")
    assert read_yaml(target) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["f.yaml"]  # pas de fichier temporaire oublié


def test_atomic_write_replaces(tmp_path: Path):
    target = tmp_path / "sub" / "f.yaml"
    write_yaml(target, {"v": 1})
    write_yaml(target, {"v": 2}, comment="commentaire")
    assert read_yaml(target) == {"v": 2}


def _write_index(path: Path) -> list[list]:
    rows = [[n, "x", "other", i * 10, 1_600_000_000 + i, 1_600_000_000] for i, n in enumerate(WEIRD_NAMES)]
    with IndexWriter(path, {"root_id": "r", "root_name": "R", "root_path": "/data/r", "started_at": "now"}) as w:
        w.write_dir("", rows[:5])
        w.write_dir("sous/dossier é", rows[5:], errors=[("illisible", "Permission denied")])
        w.write_dir("vide", [])
        w.write_dir("interdit", [], error="Permission denied")
        w.write_dir("profond", [], truncated=True)
        w.close({"status": "ok", "files": len(rows)})
    return rows


def _events(gen):
    return [(k, v.__dict__ if hasattr(v, "__dict__") else v) for k, v in gen]


def test_index_roundtrip_fast_equals_yaml(tmp_path: Path):
    path = tmp_path / "index_r.yaml"
    rows = _write_index(path)
    fast = _events(iter_index_fast(path))
    slow = _events(iter_index_yaml(path))
    assert fast == slow
    kinds = [k for k, _ in fast]
    assert kinds == ["header", "dir", "dir", "dir", "dir", "dir", "footer"]
    files = [f for k, v in fast if k == "dir" for f in v["files"]]
    assert files == rows
    assert fast[2][1]["errors"] == [["illisible", "Permission denied"]]
    assert fast[4][1]["error"] == "Permission denied"
    assert fast[5][1]["truncated"] is True
    assert fast[-1][1]["files"] == len(rows)


def test_index_is_plain_yaml(tmp_path: Path):
    path = tmp_path / "index_r.yaml"
    _write_index(path)
    with open(path, encoding="utf-8") as fh:
        docs = list(yaml.load_all(fh, Loader=yaml.SafeLoader))  # parseur pur Python, strict
    assert docs[0]["format"] == "fev-index/1"
    assert docs[1]["dir"] == ""


def test_writer_abort_leaves_nothing(tmp_path: Path):
    path = tmp_path / "index_r.yaml"
    with pytest.raises(KeyboardInterrupt):
        with IndexWriter(path, {"root_id": "r"}) as w:
            w.write_dir("", [])
            raise KeyboardInterrupt
    assert list(tmp_path.iterdir()) == []
