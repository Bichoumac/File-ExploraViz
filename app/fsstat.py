"""``stat`` d'un fichier avec sa date de création réelle (« birth time ») quand elle existe.

* Linux : appel système ``statx(2)`` (noyau ≥ 4.11, glibc ≥ 2.28) via ``ctypes``, avec
  ``STATX_BTIME``. Il remplace le ``stat`` habituel : toujours un seul appel système par fichier.
  ext4, XFS, Btrfs, ZFS (OpenZFS ≥ 2.2) et CIFS fournissent la date ; sinon elle vaut None.
* Ailleurs (macOS, BSD, Windows) : ``st_birthtime`` quand Python l'expose.
* ``st_ctime`` reste fourni : c'est le dernier changement d'inode sous Linux, pas la création.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from collections.abc import Callable

# (mode, taille, mtime, ctime, btime ou None) ; dates en secondes epoch.
StatResult = tuple[int, int, int, int, int | None]
StatFn = Callable[[os.DirEntry[str] | str, bool], StatResult]

_AT_FDCWD = -100
_AT_SYMLINK_NOFOLLOW = 0x100
_AT_NO_AUTOMOUNT = 0x800  # ne jamais déclencher un automontage en lisant
_STATX_BASIC_STATS = 0x7FF
_STATX_BTIME = 0x800
# struct statx : stx_mask (0), stx_mode (28), stx_size (40), tv_sec de stx_btime (80),
# stx_ctime (96) et stx_mtime (112). La structure fait 256 octets.
_STATX_LAYOUT = struct.Struct("=I24xH10xQ32xq8xq8xq")
_STATX_SIZE = 256


def _load_statx() -> Callable[..., int] | None:
    """``statx`` de la libc, ou None (autre système, glibc trop ancienne, appel refusé)."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        fn = ctypes.CDLL(None, use_errno=True).statx
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
    fn.restype = ctypes.c_int
    # Essai : un noyau < 4.11 répond ENOSYS, un ancien profil seccomp EPERM.
    probe = ctypes.create_string_buffer(_STATX_SIZE)
    if fn(_AT_FDCWD, b"/", _AT_NO_AUTOMOUNT, _STATX_BASIC_STATS, probe) != 0:
        return None
    return fn


_statx = _load_statx()

# Source de la date de création, pour les journaux et les tests.
BIRTH_SOURCE = "statx" if _statx is not None else ("st_birthtime" if sys.platform != "linux" else "none")


def make_stat() -> StatFn:
    """Renvoie ``stat(entrée ou chemin, suivre_liens) -> StatResult``.

    Une fonction par scan : le tampon de ``statx`` lui est propre (pas de partage entre threads).
    """
    if _statx is None:

        def portable(target: os.DirEntry[str] | str, follow: bool) -> StatResult:
            st = target.stat(follow_symlinks=follow) if isinstance(target, os.DirEntry) else os.stat(target, follow_symlinks=follow)
            birth = getattr(st, "st_birthtime", None)
            return st.st_mode, st.st_size, int(st.st_mtime), int(st.st_ctime), int(birth) if birth else None

        return portable

    statx = _statx
    buf = ctypes.create_string_buffer(_STATX_SIZE)
    unpack = _STATX_LAYOUT.unpack_from
    mask = _STATX_BASIC_STATS | _STATX_BTIME

    def linux(target: os.DirEntry[str] | str, follow: bool) -> StatResult:
        path = target.path if isinstance(target, os.DirEntry) else target
        flags = _AT_NO_AUTOMOUNT if follow else _AT_NO_AUTOMOUNT | _AT_SYMLINK_NOFOLLOW
        if statx(_AT_FDCWD, os.fsencode(path), flags, mask, buf) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), path)
        got, mode, size, btime, ctime, mtime = unpack(buf)
        # Certains systèmes de fichiers annoncent STATX_BTIME mais renvoient 0 : inconnu.
        return mode, size, mtime, ctime, btime if (got & _STATX_BTIME and btime) else None

    return linux
