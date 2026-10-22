from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.settings import Settings  # noqa: E402

T2020 = 1577836800  # 2020-01-01T00:00:00Z
T2024 = 1704067200  # 2024-01-01T00:00:00Z


def make_file(path: Path, size: int = 0, mtime: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        if size:
            fh.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def sample_tree(tmp_path: Path) -> Path:
    """Arborescence de test sous tmp/data/photos (+ racine docs)."""
    data = tmp_path / "data"
    photos = data / "photos"
    make_file(photos / "a.jpg", 1000, T2020)
    make_file(photos / "b.JPG", 2000, T2024)
    make_file(photos / "notes.txt", 10, T2024)
    make_file(photos / "2023" / "IMG_001.jpg", 5000, T2024)
    make_file(photos / "2023" / "IMG_002.jpg", 5000, T2024)
    make_file(photos / "2023" / "film.mp4", 100_000, T2024)
    make_file(photos / "2023" / "deep" / "x" / "y.png", 300, T2020)
    make_file(photos / "copie" / "a.jpg", 1000, T2020)  # doublon de a.jpg
    make_file(photos / "copie" / "vide.dat", 0, T2020)
    (photos / "vide").mkdir()
    make_file(photos / "@eaDir" / "thumb.jpg", 50, T2020)  # exclu par défaut
    make_file(photos / "cache.tmp", 50, T2020)  # exclu par défaut
    make_file(data / "docs" / "rapport.pdf", 4000, T2024)
    make_file(data / "docs" / "tableau.xlsx", 3000, T2024)
    return data


@pytest.fixture
def settings(tmp_path: Path, sample_tree: Path) -> Settings:
    return Settings(
        config_dir=tmp_path / "config",
        cache_dir=tmp_path / "cache",
        data_dir=sample_tree,
        web_dir=Path(__file__).resolve().parent.parent / "web",
        host="127.0.0.1",
        port=8080,
        log_level="WARNING",
        auth_disabled=False,
        admin_user="admin",
        admin_password="motdepasse-test",
        scan_interval_minutes=15,
        initial_scan=False,
        forwarded_allow_ips="127.0.0.1",
    )


def wait_until(predicate, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("délai dépassé")
