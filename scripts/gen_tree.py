"""Génère une arborescence factice pour tester les performances du scan.

Exemple : ``python scripts/gen_tree.py /tmp/data/demo --files 200000``

Les fichiers sont creux (``truncate``) : sous Linux ils n'occupent presque pas de disque malgré
leur taille apparente. Sous Windows/NTFS l'espace est réservé : utilisez ``--max-size 0`` (taille
nulle) ou une petite valeur.
"""

from __future__ import annotations

import argparse
import os
import random
import time
from pathlib import Path

PROFILES = [  # (poids, extensions, taille typique en octets)
    (30, ["jpg", "jpeg", "png", "heic", "cr2", "dng"], 4_000_000),
    (6, ["mp4", "mov", "mkv", "avi"], 400_000_000),
    (8, ["mp3", "flac", "m4a", "wav"], 8_000_000),
    (10, ["docx", "odt", "txt", "md", "rtf"], 60_000),
    (4, ["xlsx", "ods", "csv"], 120_000),
    (2, ["pptx", "odp"], 3_000_000),
    (6, ["pdf"], 900_000),
    (3, ["zip", "7z", "tar", "gz", "rar"], 50_000_000),
    (20, ["py", "js", "ts", "json", "html", "css", "yaml", "c", "h", "go"], 8_000),
    (1, ["iso", "exe", "msi", "dmg", "img"], 700_000_000),
    (10, ["dat", "bak", "log", "", "xyz", "db"], 30_000),
]
WORDS = "photos vacances 2019 2020 2021 2022 2023 2024 projet clients archives musique films docs scans famille travail backup export brouillons".split()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", type=Path)
    ap.add_argument("--files", type=int, default=200_000)
    ap.add_argument("--per-dir", type=int, default=60, help="fichiers par dossier (moyenne)")
    ap.add_argument("--max-size", type=int, default=None, help="taille maximale (0 = fichiers vides)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rnd = random.Random(args.seed)
    weights = [p[0] for p in PROFILES]
    now = time.time()
    t0 = time.time()
    dirs = [args.target]
    args.target.mkdir(parents=True, exist_ok=True)
    created = 0
    last_report = 0
    while created < args.files:
        parent = rnd.choice(dirs[-200:]) if rnd.random() < 0.7 else rnd.choice(dirs)
        if len(parent.relative_to(args.target).parts) < 7:
            d = parent / f"{rnd.choice(WORDS)}_{len(dirs)}"
            d.mkdir(exist_ok=True)
            dirs.append(d)
        else:
            d = parent
        for _ in range(max(1, int(rnd.expovariate(1 / args.per_dir)))):
            if created >= args.files:
                break
            _, exts, typical = rnd.choices(PROFILES, weights)[0]
            ext = rnd.choice(exts)
            name = f"{rnd.choice(WORDS)}_{created:07d}" + (f".{ext}" if ext else "")
            size = int(rnd.lognormvariate(0, 1.2) * typical)
            if args.max_size is not None:
                size = min(size, args.max_size)
            path = d / name
            with open(path, "wb") as fh:
                if size:
                    fh.truncate(size)
            mtime = now - rnd.expovariate(1 / (3 * 365 * 86400))
            os.utime(path, (mtime, mtime))
            created += 1
        if created % 20_000 < args.per_dir:
            print(f"\r{created} fichiers, {len(dirs)} dossiers…", end="", flush=True)
    print(f"\r{created} fichiers, {len(dirs)} dossiers créés en {time.time() - t0:.1f} s sous {args.target}")


if __name__ == "__main__":
    main()
