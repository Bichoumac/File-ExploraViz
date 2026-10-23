"""Vraie date de création (statx / st_birthtime) : lecture, index, Store, API."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from app import fsstat
from app.categories import DEFAULT_MAPPING, build_ext_map
from app.scanner import ScanOptions, ScanProgress, scan_root
from app.store import FileFilter, Store, load_root_part
from app.yamlio import IndexWriter, iter_index_fast

from .conftest import T2020, T2024, make_file

EXT_MAP = build_ext_map(DEFAULT_MAPPING)


def test_make_stat_reads_size_dates_and_birth_time(tmp_path: Path):
    before = int(time.time()) - 5
    f = make_file(tmp_path / "photo.jpg", 1234, T2020)
    stat_of = fsstat.make_stat()
    mode, size, mtime, ctime, btime = stat_of(str(f), False)
    assert size == 1234 and mtime == T2020
    assert ctime >= before  # st_ctime : changement d'inode (utime), donc récent
    if btime is not None:
        # Créé à l'instant puis antidaté : la date de création reste celle d'aujourd'hui.
        assert btime >= before and btime != mtime
    with os.scandir(tmp_path) as it:
        entry = next(it)
    assert stat_of(entry, False)[:3] == (mode, size, mtime)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="statx(2) : Linux uniquement")
def test_statx_is_used_on_linux():
    assert fsstat.BIRTH_SOURCE == "statx"


class _Timestamp(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_uint32), ("reserved", ctypes.c_int32)]


class _Statx(ctypes.Structure):
    """``struct statx`` recopiée de linux/stat.h, indépendamment du format utilisé par fsstat."""

    _fields_ = [
        ("stx_mask", ctypes.c_uint32), ("stx_blksize", ctypes.c_uint32), ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink", ctypes.c_uint32), ("stx_uid", ctypes.c_uint32), ("stx_gid", ctypes.c_uint32),
        ("stx_mode", ctypes.c_uint16), ("spare0", ctypes.c_uint16),
        ("stx_ino", ctypes.c_uint64), ("stx_size", ctypes.c_uint64), ("stx_blocks", ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime", _Timestamp), ("stx_btime", _Timestamp), ("stx_ctime", _Timestamp), ("stx_mtime", _Timestamp),
        ("spare", ctypes.c_uint8 * 128),
    ]


@pytest.mark.parametrize("with_birth", [True, False])
def test_statx_buffer_layout(monkeypatch, with_birth: bool):
    """Décodage du tampon statx, vérifié sur tout système grâce à un faux appel système."""
    assert ctypes.sizeof(_Statx) == 256
    calls = []

    def fake_statx(dirfd, path, flags, mask, buf):
        calls.append((dirfd, path, flags, mask))
        st = _Statx(stx_mask=0x7FF | (0x800 if with_birth else 0), stx_mode=0o100644, stx_size=123456789012)
        st.stx_atime.tv_sec, st.stx_btime.tv_sec, st.stx_ctime.tv_sec, st.stx_mtime.tv_sec = 1, T2020, T2024 + 9, T2024
        ctypes.memmove(buf, ctypes.byref(st), ctypes.sizeof(st))
        return 0

    monkeypatch.setattr(fsstat, "_statx", fake_statx)
    stat_of = fsstat.make_stat()
    expected_birth = T2020 if with_birth else None
    assert stat_of("/data/x/é.jpg", False) == (0o100644, 123456789012, T2024, T2024 + 9, expected_birth)
    dirfd, path, flags, mask = calls[0]
    assert dirfd == -100 and path == "/data/x/é.jpg".encode() and mask & 0x800
    assert flags & 0x100  # AT_SYMLINK_NOFOLLOW
    stat_of("/data/lien", True)
    assert not calls[1][2] & 0x100


def test_statx_failure_raises_oserror(monkeypatch):
    def failing(dirfd, path, flags, mask, buf):
        ctypes.set_errno(errno.EACCES)
        return -1

    monkeypatch.setattr(fsstat, "_statx", failing)
    with pytest.raises(PermissionError):
        fsstat.make_stat()("/data/prive.jpg", False)


def test_make_stat_errors_are_oserror(tmp_path: Path):
    with pytest.raises(OSError) as info:
        fsstat.make_stat()(str(tmp_path / "absent"), False)
    assert info.value.errno == errno.ENOENT


@pytest.mark.skipif(os.name != "posix", reason="liens symboliques")
def test_make_stat_follow_flag(tmp_path: Path):
    target = make_file(tmp_path / "cible.bin", 500)
    link = tmp_path / "lien"
    os.symlink(target, link)
    stat_of = fsstat.make_stat()
    assert stat_of(str(link), True)[1] == 500
    assert stat_of(str(link), False)[1] != 500  # le lien lui-même


def test_scan_writes_birth_column_and_store_uses_it(tmp_path: Path):
    root = tmp_path / "data" / "r"
    make_file(root / "a.txt", 10, T2020)
    out = tmp_path / "index_r.yaml"
    footer = scan_root("r", "R", root, out, ScanOptions(ext_map=EXT_MAP), ScanProgress(), threading.Event())
    header = next(payload for kind, payload in iter_index_fast(out) if kind == "header")
    assert header["columns"][-1] == "btime"
    store = Store.build([load_root_part(out, "r", "R")], EXT_MAP)
    row = store.row_dict(0)
    assert footer["birth_times"] == int(row["ctime_birth"])
    if row["ctime_birth"]:
        assert row["ctime"] > T2024  # vraie création (aujourd'hui), pas la date antidatée


def _index(path: Path, files: list[tuple]) -> Store:
    with IndexWriter(path, {"root_id": "r"}) as w:
        w.write_dir("", files)
        w.close({"status": "ok"})
    return Store.build([load_root_part(path, "r", "R")], EXT_MAP)


def test_birth_time_replaces_ctime_when_known(tmp_path: Path):
    s = _index(
        tmp_path / "i.yaml",
        [("a.jpg", "jpg", "photo", 1, T2024, T2024 + 50, T2020), ("b.jpg", "jpg", "photo", 1, T2024, T2024 + 50, None)],
    )
    assert s.ctime.tolist() == [T2020, T2024 + 50]
    assert s.birth.tolist() == [True, False]
    # Filtre et tri « création » : la vraie date quand elle est connue.
    assert s.select(FileFilter(date_field="ctime", date_to=T2024)).count == 1
    assert [s.name(int(i)) for i in s.sorted_rows(FileFilter(), "ctime")] == ["a.jpg", "b.jpg"]
    # Reconstitution d'une racine (rescan partiel) : la colonne suit.
    s2 = Store.build([s.extract("r")], EXT_MAP)
    assert s2.birth.tolist() == [True, False] and s2.ctime.tolist() == s.ctime.tolist()


def test_index_without_birth_column_still_loads(tmp_path: Path):
    s = _index(tmp_path / "old.yaml", [("a.jpg", "jpg", "photo", 1, T2024, T2020)])
    assert s.ctime.tolist() == [T2020] and s.birth.tolist() == [False]
    assert s.row_dict(0)["ctime_birth"] is False
