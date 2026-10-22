from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.auth import LoginThrottle, SessionCodec, fingerprint, hash_password, verify_password
from app.pathsec import PathError, normalize_rel, safe_join


@pytest.mark.parametrize(
    "raw,expected",
    [("", ""), ("/", ""), ("a/b", "a/b"), ("/a//b/", "a/b"), ("./a/./b", "a/b"), ("a\\b", "a/b")],
)
def test_normalize_ok(raw, expected):
    assert normalize_rel(raw) == expected


@pytest.mark.parametrize("raw", ["..", "../etc", "a/../../b", "a/..", "a\x00b", "..\\..\\windows"])
def test_normalize_rejects(raw):
    with pytest.raises(PathError):
        normalize_rel(raw)


def test_safe_join(tmp_path: Path):
    base = tmp_path / "root"
    (base / "sub").mkdir(parents=True)
    assert safe_join(base, "sub") == os.path.realpath(base / "sub")
    with pytest.raises(PathError):
        safe_join(base, "../root2")


@pytest.mark.skipif(os.name != "posix", reason="liens symboliques POSIX")
def test_safe_join_symlinks(tmp_path: Path):
    base = tmp_path / "root"
    (base / "sub").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    os.symlink(tmp_path / "outside", base / "escape")
    os.symlink(base / "sub", base / "inner")
    with pytest.raises(PathError):
        safe_join(base, "escape")
    with pytest.raises(PathError):
        safe_join(base, "escape", allow_symlinks=True)  # sortant : jamais
    with pytest.raises(PathError):
        safe_join(base, "inner")
    assert safe_join(base, "inner", allow_symlinks=True) == os.path.realpath(base / "sub")


def test_password_hashing():
    h = hash_password("correct horse battery staple")
    assert h.startswith("$2")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("mauvais", h)
    assert not verify_password("x", None)
    long_pw = "é" * 200  # > 72 octets : pas de troncature silencieuse
    h2 = hash_password(long_pw)
    assert verify_password(long_pw, h2) and not verify_password("é" * 199, h2)


def test_session_codec():
    codec = SessionCodec("secret")
    token = codec.issue("admin", "fp", 60)
    assert codec.verify(token)["u"] == "admin"
    assert SessionCodec("autre").verify(token) is None
    body, sig = token.rsplit(".", 1)
    assert codec.verify(body + "x." + sig) is None
    assert codec.verify(codec.issue("admin", "fp", -1)) is None
    assert codec.verify(None) is None and codec.verify("n'importe quoi") is None
    assert fingerprint("a") != fingerprint("b")


def test_throttle_progressive_delay():
    t = LoginThrottle()
    assert t.failure("ip:1") == 0 and t.failure("ip:1") == 0
    assert t.failure("ip:1") == 1.0
    assert t.failure("ip:1") == 2.0
    assert t.retry_after("ip:1") > 0
    assert t.retry_after("ip:2") == 0
    t.success("ip:1")
    assert t.retry_after("ip:1") == 0
    for _ in range(30):
        d = t.failure("ip:3")
    assert d == LoginThrottle.MAX_DELAY
    assert time.time() + t.retry_after("ip:3") > time.time()
