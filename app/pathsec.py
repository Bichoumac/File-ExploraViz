"""Sécurité des chemins : normalisation et confinement dans les racines configurées."""

from __future__ import annotations

import os


class PathError(ValueError):
    """Chemin refusé (sortie de la racine, composant interdit, lien symbolique sortant...)."""


def normalize_rel(path: str | None) -> str:
    """Normalise un chemin relatif en ``a/b/c`` (sans / initial ni final).

    Refuse les octets NUL et les composants ``..`` : on ne « résout » pas un ``..``, on le
    rejette, ce qui ferme la porte à toute tentative de remonter au-dessus de la racine.
    """
    if not path:
        return ""
    if "\x00" in path:
        raise PathError("caractère NUL interdit dans un chemin")
    parts = [p for p in path.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathError("composant « .. » interdit")
    return "/".join(parts)


def is_within(path: str, base: str) -> bool:
    """Vrai si ``path`` est ``base`` ou se trouve dessous (chemins absolus normalisés)."""
    try:
        return os.path.commonpath([path, base]) == base
    except ValueError:  # lecteurs différents sous Windows, chemin vide...
        return False


def safe_join(base: str | os.PathLike[str], rel: str | None, *, allow_symlinks: bool = False) -> str:
    """Joint ``rel`` à ``base`` et vérifie que le résultat réel reste dans ``base``.

    Si ``allow_symlinks`` est faux, tout lien symbolique traversé est refusé, même s'il pointe
    à l'intérieur de la racine.
    """
    base_real = os.path.realpath(base)
    rel = normalize_rel(rel)
    target = os.path.join(base_real, *rel.split("/")) if rel else base_real
    real = os.path.realpath(target)
    if not is_within(real, base_real):
        raise PathError("le chemin sort de la racine autorisée")
    if not allow_symlinks and os.path.normcase(real) != os.path.normcase(os.path.normpath(target)):
        raise PathError("lien symbolique non autorisé")
    return real


def within_any(real_path: str, bases: list[str]) -> bool:
    return any(is_within(real_path, b) for b in bases)
