"""Chronomètre le scan d'une arborescence réelle (par ex. générée par gen_tree.py).

    python scripts/bench_scan.py /tmp/data/demo
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.categories import DEFAULT_MAPPING, build_ext_map  # noqa: E402
from app.config import DEFAULT_EXCLUSIONS  # noqa: E402
from app.scanner import ScanOptions, ScanProgress, scan_root  # noqa: E402
from app.store import Store, load_root_part  # noqa: E402


def main() -> None:
    target = Path(sys.argv[1])
    ext_map = build_ext_map(DEFAULT_MAPPING)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "index_bench.yaml"
        for run in ("à froid", "à chaud (cache disque)"):
            t = time.perf_counter()
            footer = scan_root("bench", "Bench", target, out, ScanOptions(DEFAULT_EXCLUSIONS, ext_map=ext_map),
                               ScanProgress(), threading.Event())
            dt = time.perf_counter() - t
            print(f"Scan {run} : {footer['files']:,} fichiers, {footer['dirs']:,} dossiers en {dt:.1f} s "
                  f"({footer['files'] / dt:,.0f} fichiers/s) ; index {out.stat().st_size / 1e6:.1f} Mo".replace(",", " "))
        t = time.perf_counter()
        store = Store.build([load_root_part(out, "bench", "Bench")], ext_map)
        store.warm_up()
        print(f"Relecture de l'index + pré-calculs : {time.perf_counter() - t:.1f} s")


if __name__ == "__main__":
    main()
