"""Lecture/écriture YAML : écriture atomique, index détaillés écrits et relus en flux.

Les lignes de fichiers des index sont émises en JSON, qui est un sous-ensemble du YAML :
n'importe quel parseur YAML les lit (``CSafeLoader`` sert de repli), mais l'application les
relit ligne à ligne avec ``json.loads``, environ 20 fois plus vite que libyaml.
Voir docs/ARCHITECTURE.md § 3.1.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import yaml

try:  # libyaml (C) si disponible, sinon implémentation Python.
    SafeLoader: type = yaml.CSafeLoader
    SafeDumper: type = yaml.CSafeDumper
except AttributeError:  # pragma: no cover - dépend de la compilation de PyYAML
    SafeLoader = yaml.SafeLoader
    SafeDumper = yaml.SafeDumper

INDEX_FORMAT = "fev-index/1"
# btime (date de création réelle, null si inconnue) : 7e colonne, absente des index antérieurs.
INDEX_COLUMNS = ["name", "ext", "category", "size", "mtime", "ctime", "btime"]

# Caractères valides en JSON mais interdits ou transformés par YAML dans une chaîne entre
# guillemets (contrôles C1, séparateurs de ligne Unicode, BOM, non-caractères).
_YAML_UNSAFE = re.compile("[\x7f-\x9f  ﻿￾￿]")


class IndexFormatError(ValueError):
    """Le fichier ne suit pas la mise en forme produite par IndexWriter."""


# --------------------------------------------------------------------------- écriture atomique


def _fsync_dir(directory: Path) -> None:
    if os.name != "posix":
        return
    with suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def atomic_writer(path: Path) -> Iterator[IO[str]]:
    """Écrit dans un fichier temporaire du même dossier puis le renomme sur ``path``.

    Un lecteur voit donc soit l'ancien fichier complet, soit le nouveau complet, jamais un
    fichier à moitié écrit. En cas d'exception, le fichier temporaire est supprimé.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            yield fh
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)
        raise
    _fsync_dir(path.parent)


def dump_yaml(data: Any, fh: IO[str]) -> None:
    yaml.dump(data, fh, Dumper=SafeDumper, allow_unicode=True, sort_keys=False, width=120)


def write_yaml(path: Path, data: Any, comment: str | None = None) -> None:
    with atomic_writer(path) as fh:
        if comment:
            fh.write(f"# {comment}\n")
        dump_yaml(data, fh)


def read_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.load(fh, Loader=SafeLoader)


# --------------------------------------------------------------------------- encodage des lignes


def clean_text(text: str) -> str:
    """Rend une chaîne encodable en UTF-8.

    Un nom de fichier Linux qui n'est pas de l'UTF-8 valide arrive de ``os.scandir`` avec des
    « surrogates » ; on remplace les octets fautifs par ``\\xNN``.
    """
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        try:
            return text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
        except UnicodeEncodeError:
            return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _escape_unsafe(match: re.Match[str]) -> str:
    return f"\\u{ord(match.group()):04x}"


def yaml_json(value: Any) -> str:
    """Sérialise ``value`` en JSON sur une ligne, garanti valide en YAML."""
    return _YAML_UNSAFE.sub(_escape_unsafe, json.dumps(value, ensure_ascii=False))


# --------------------------------------------------------------------------- index : écriture


class IndexWriter:
    """Écrit un ``index_<racine>.yaml`` dossier par dossier, sans tout garder en mémoire."""

    def __init__(self, path: Path, header: dict[str, Any]) -> None:
        self.path = path
        self._cm = atomic_writer(path)
        self._fh = self._cm.__enter__()
        self._fh.write("# File ExploraViz - index détaillé (généré automatiquement, ne pas modifier)\n---\n")
        dump_yaml({"format": INDEX_FORMAT, **header, "columns": INDEX_COLUMNS}, self._fh)
        self._closed = False

    def write_dir(
        self,
        rel: str,
        files: list[tuple[str, str, str, int, int, int, int | None]],
        *,
        error: str | None = None,
        errors: list[tuple[str, str]] | None = None,
        truncated: bool = False,
    ) -> None:
        out = ["---", "dir: " + yaml_json(rel)]
        if error:
            out.append("error: " + yaml_json(error))
        if truncated:
            out.append("truncated: true")
        if errors:
            out.append("errors:")
            out.extend("- " + yaml_json(list(e)) for e in errors)
        if files:
            out.append("files:")
            out.extend("- " + yaml_json(list(f)) for f in files)
        else:
            out.append("files: []")
        out.append("")
        self._fh.write("\n".join(out))

    def close(self, footer: dict[str, Any]) -> None:
        self._fh.write("---\nfooter: " + yaml_json(footer) + "\n")
        self._closed = True
        self._cm.__exit__(None, None, None)

    def abort(self) -> None:
        if not self._closed:
            self._closed = True
            with suppress(Exception):
                self._cm.__exit__(RuntimeError, RuntimeError("annulé"), None)

    def __enter__(self) -> IndexWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self._closed:
            self.abort()


# --------------------------------------------------------------------------- index : lecture


@dataclass
class DirRecord:
    path: str
    files: list[list[Any]] = field(default_factory=list)
    error: str | None = None
    errors: list[list[Any]] = field(default_factory=list)
    truncated: bool = False


IndexEvent = tuple[str, Any]  # ("header", dict) | ("dir", DirRecord) | ("footer", dict)


def iter_index_fast(path: Path) -> Iterator[IndexEvent]:
    """Lecteur ligne à ligne du format produit par IndexWriter.

    Lève IndexFormatError à la première ligne inattendue (fichier retouché à la main, par
    exemple) ; l'appelant se rabat alors sur iter_index_yaml().
    """
    doc_kind: str | None = None
    header_lines: list[str] = []
    rec: DirRecord | None = None
    target: list[list[Any]] | None = None
    seen_header = False

    def finish() -> IndexEvent | None:
        if doc_kind == "header":
            return ("header", yaml.load("\n".join(header_lines), Loader=SafeLoader) or {})
        if doc_kind == "dir":
            return ("dir", rec)
        return None

    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\r\n")
            try:
                if line.startswith("- ["):
                    if target is None:
                        raise IndexFormatError(f"ligne {lineno} : élément de liste hors liste")
                    target.append(json.loads(line[2:]))
                elif line == "---":
                    event = finish()
                    if event:
                        yield event
                    doc_kind = "header" if not seen_header else "pending"
                    seen_header = True
                    rec, target = None, None
                elif not line or line.startswith("#"):
                    continue
                elif doc_kind == "header":
                    header_lines.append(line)
                elif doc_kind == "pending" and line.startswith("dir: "):
                    rec = DirRecord(path=json.loads(line[5:]))
                    doc_kind = "dir"
                elif doc_kind == "pending" and line.startswith("footer: "):
                    yield ("footer", json.loads(line[8:]))
                    doc_kind = None
                elif doc_kind == "dir" and rec is not None:
                    if line == "files:":
                        target = rec.files
                    elif line == "files: []":
                        target = None
                    elif line == "errors:":
                        target = rec.errors
                    elif line.startswith("error: "):
                        rec.error = json.loads(line[7:])
                    elif line == "truncated: true":
                        rec.truncated = True
                    else:
                        raise IndexFormatError(f"ligne {lineno} : clé inattendue")
                else:
                    raise IndexFormatError(f"ligne {lineno} : contenu inattendu")
            except json.JSONDecodeError as exc:
                raise IndexFormatError(f"ligne {lineno} : {exc}") from exc
        event = finish()
        if event:
            yield event


def iter_index_yaml(path: Path) -> Iterator[IndexEvent]:
    """Lecteur YAML générique (plus lent) : accepte toute mise en forme YAML équivalente."""
    with open(path, encoding="utf-8") as fh:
        for doc in yaml.load_all(fh, Loader=SafeLoader):
            if not isinstance(doc, dict):
                continue
            if "format" in doc:
                yield ("header", doc)
            elif "dir" in doc:
                yield (
                    "dir",
                    DirRecord(
                        path=doc.get("dir") or "",
                        files=doc.get("files") or [],
                        error=doc.get("error"),
                        errors=doc.get("errors") or [],
                        truncated=bool(doc.get("truncated")),
                    ),
                )
            elif "footer" in doc:
                yield ("footer", doc["footer"] or {})
