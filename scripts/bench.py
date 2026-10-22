"""Mesures de performance : chargement de l'index, mémoire, latence de l'API.

Génère un index synthétique (sans créer de fichiers sur le disque), démarre l'application
dessus puis chronomètre les routes principales. Exemples :

    python scripts/bench.py --files 1000000
    python scripts/bench.py --files 200000 --workdir /tmp/fev-bench
"""

from __future__ import annotations

import argparse
import ctypes
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.categories import DEFAULT_MAPPING, build_ext_map  # noqa: E402
from app.categories import CATEGORY_IDS, OTHER  # noqa: E402
from app.yamlio import IndexWriter  # noqa: E402

WORDS = "photos vacances projet clients archives musique films docs scans famille travail backup export".split()
EXTS = ["jpg", "jpeg", "png", "heic", "cr2", "mp4", "mov", "mp3", "flac", "docx", "txt", "pdf", "xlsx", "pptx",
        "zip", "7z", "py", "js", "json", "iso", "exe", "dat", "log", ""]


def rss_mb(peak: bool = False) -> float:
    """Mémoire résidente du processus, ou son pic (Linux : /proc ; Windows : psapi)."""
    if sys.platform.startswith("linux"):
        key = "VmHWM:" if peak else "VmRSS:"
        for line in open("/proc/self/status", encoding="ascii"):
            if line.startswith(key):
                return int(line.split()[1]) / 1024
    if os.name == "nt":
        class PMC(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), ctypes.c_ulong]
        k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return (pmc.PeakWorkingSetSize if peak else pmc.WorkingSetSize) / 1024 / 1024
    return float("nan")


def write_index(path: Path, n_files: int, per_dir: int, seed: int) -> int:
    """Index synthétique réaliste : arborescence de profondeur 1 à 6, ~per_dir fichiers par dossier."""
    rnd = random.Random(seed)
    ext_map = build_ext_map(DEFAULT_MAPPING)
    now = int(time.time())
    n_dirs = max(1, n_files // per_dir)
    # Arbre en ordre préfixe : chaque dossier est l'enfant d'un ancêtre de la pile courante.
    paths, stack = [""], [""]
    for i in range(1, n_dirs):
        while len(stack) > 1 and (len(stack) > 6 or rnd.random() < 0.35):
            stack.pop()
        parent = stack[-1]
        p = f"{parent}/{rnd.choice(WORDS)}_{i:06d}" if parent else f"{rnd.choice(WORDS)}_{i:06d}"
        paths.append(p)
        stack.append(p)
    written = 0
    with IndexWriter(path, {"root_id": "bench", "root_name": "Bench", "root_path": "/data/bench", "started_at": "bench"}) as w:
        for d, p in enumerate(paths):
            count = n_files - written if d == len(paths) - 1 else min(n_files - written, int(rnd.expovariate(1 / per_dir)))
            rows = []
            for _ in range(count):
                ext = rnd.choice(EXTS)
                name = f"{rnd.choice(WORDS)}_{written:08d}" + (f".{ext}" if ext else "")
                size = int(rnd.lognormvariate(11, 2.5))
                mt = now - int(rnd.expovariate(1 / (3 * 365 * 86400)))
                rows.append((name, ext, CATEGORY_IDS[ext_map.get(ext, OTHER)], size, mt, mt))
                written += 1
            rows.sort(key=lambda r: (r[0].lower(), r[0]))
            w.write_dir(p, rows)
        w.close({"status": "ok", "files": written, "dirs": len(paths), "bytes": 0, "duration_s": 0})
    return len(paths)


def timed(client, url: str, repeat: int = 5) -> tuple[float, float]:
    """(1er appel, médiane des appels suivants) en ms."""
    t = time.perf_counter()
    r = client.get(url)
    first = (time.perf_counter() - t) * 1000
    assert r.status_code == 200, (url, r.status_code, r.text[:200])
    again = []
    for _ in range(repeat):
        t = time.perf_counter()
        client.get(url)
        again.append((time.perf_counter() - t) * 1000)
    return first, statistics.median(again)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", type=int, default=1_000_000)
    ap.add_argument("--per-dir", type=int, default=25)
    ap.add_argument("--workdir", type=Path, default=None)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    work = args.workdir or Path(tempfile.mkdtemp(prefix="fev-bench-"))
    cache, config, data = work / "cache", work / "config", work / "data"
    for d in (cache, config, data / "bench"):
        d.mkdir(parents=True, exist_ok=True)
    index = cache / "index_bench.yaml"
    print(f"Plateforme : {sys.platform}, Python {sys.version.split()[0]}, dossier {work}")

    t = time.perf_counter()
    n_dirs = write_index(index, args.files, args.per_dir, args.seed)
    print(f"Index synthétique : {args.files:,} fichiers, {n_dirs:,} dossiers, "
          f"{index.stat().st_size / 1e6:.0f} Mo, écrit en {time.perf_counter() - t:.1f} s".replace(",", " "))

    os.environ.update({"FEV_CONFIG_DIR": str(config), "FEV_CACHE_DIR": str(cache), "FEV_DATA_DIR": str(data),
                       "AUTH_DISABLED": "true", "INITIAL_SCAN": "false", "LOG_LEVEL": "WARNING"})
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings

    rss0 = rss_mb()
    app = create_app(Settings.from_env())
    with TestClient(app) as client:
        t = time.perf_counter()
        while not app.state.ctx.state.index_ready.is_set():
            time.sleep(0.05)
        load_s = time.perf_counter() - t
        rss1 = rss_mb()
        print(f"Chargement index + pré-calculs : {load_s:.1f} s ; RSS {rss0:.0f} → {rss1:.0f} Mo")

        q = "/api/files?root=bench&page_size=100"
        cases = [
            ("Liste, tri arborescence, page 1", q),
            ("Liste, tri taille décroissante", q + "&sort=-size"),
            ("Liste, tri par nom", q + "&sort=name"),
            ("Liste, page 5000", q + "&sort=-size&page=5000"),
            ("Filtre extensions + date", q + "&ext=jpg,png&from=2024-01-01&sort=-mtime"),
            ("Filtre catégorie + taille", q + "&category=video,audio&min_size=1000000"),
            ("Recherche sous-chaîne", q + "&q=vacances_0001"),
            ("Recherche glob", q + "&q=*_00012*.jp*g"),
            ("Bandeau (stats légères)", "/api/stats?root=bench&detail=false"),
            ("Statistiques complètes", "/api/stats?root=bench"),
            ("Statistiques filtrées", "/api/stats?root=bench&ext=pdf,docx"),
            ("Treemap racine (profondeur 3)", "/api/treemap?root=bench&depth=3"),
            ("Arbre (sous-dossiers)", "/api/tree?root=bench"),
            ("Autocomplétion extensions", "/api/extensions?root=bench&prefix=j"),
        ]
        print(f"\n| Requête | 1er appel (ms) | appels suivants, médiane (ms) |\n|---|---:|---:|")
        for label, url in cases:
            first, again = timed(client, url)
            print(f"| {label} | {first:.0f} | {again:.1f} |")
        t = time.perf_counter()
        r = client.get("/api/files/export.csv?root=bench&ext=pdf")
        print(f"\nExport CSV (filtre pdf, {r.text.count(chr(10)) - 1:,} lignes) : {(time.perf_counter() - t) * 1000:.0f} ms".replace(",", " "))
        print(f"RSS final : {rss_mb():.0f} Mo ; pic : {rss_mb(peak=True):.0f} Mo")
    if args.workdir is None:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
