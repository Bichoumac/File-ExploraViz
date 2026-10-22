"""Point d'entrée du conteneur : applique PUID/PGID puis lance l'application.

Démarré en root, le processus :
1. s'assure que /config et /cache appartiennent à PUID:PGID ;
2. abandonne définitivement les privilèges (groupes, GID puis UID) ;
3. vérifie que /data n'est pas inscriptible (montage ``:ro`` attendu) ;
4. lance uvicorn (``app.__main__.main``).

Démarré directement en non-root (``user:`` dans compose), il saute les étapes 1 et 2.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _log(level: str, msg: str) -> None:
    print(f"level={level} logger=entrypoint msg=\"{msg}\"", file=sys.stderr, flush=True)


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    st = root.stat()
    if (st.st_uid, st.st_gid) == (uid, gid):
        return
    _log("INFO", f"attribution de {root} à {uid}:{gid}")
    for dirpath, dirnames, filenames in os.walk(root):
        for name in [dirpath, *(os.path.join(dirpath, n) for n in dirnames + filenames)]:
            try:
                os.lchown(name, uid, gid)
            except OSError as exc:
                _log("WARNING", f"chown impossible sur {name} : {exc}")


def drop_privileges() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        return
    uid = int(os.environ.get("PUID", "1000"))
    gid = int(os.environ.get("PGID", "1000"))
    extra = [int(g) for g in os.environ.get("SUPPLEMENTARY_GIDS", "").replace(" ", "").split(",") if g]
    for var in ("FEV_CONFIG_DIR", "FEV_CACHE_DIR"):
        path = Path(os.environ.get(var, "/" + var.split("_")[1].lower()))
        path.mkdir(parents=True, exist_ok=True)
        try:
            _chown_tree(path, uid, gid)
        except OSError as exc:
            _log("WARNING", f"{path} : {exc}")
    if uid == 0:
        _log("WARNING", "PUID=0 : l'application tourne en root, ce qui est déconseillé")
        return
    os.setgroups(sorted({gid, *extra}))
    os.setgid(gid)
    os.setuid(uid)
    if os.geteuid() == 0:  # garde-fou : ne jamais continuer en root par erreur
        raise SystemExit("Impossible d'abandonner les privilèges root")
    os.environ["HOME"] = "/tmp"
    _log("INFO", f"exécution en tant que {uid}:{gid}" + (f" (groupes supplémentaires {extra})" if extra else ""))


def check_data_readonly() -> None:
    data = Path(os.environ.get("FEV_DATA_DIR", "/data"))
    if not data.is_dir():
        _log("WARNING", f"{data} n'existe pas : montez vos répertoires sous {data}/<nom>:ro")
        return
    for entry in data.iterdir():
        if entry.is_dir() and os.access(entry, os.W_OK):
            _log("WARNING", f"{entry} est accessible en écriture : montez-le en lecture seule (:ro)")


def main() -> None:
    drop_privileges()
    check_data_readonly()
    from .__main__ import main as run

    run()


if __name__ == "__main__":
    main()
